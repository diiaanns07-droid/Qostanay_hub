"""Unit tests for the pure parts of proctor.phone.detector (owner: A03).

NO REAL MODEL HERE. Every inference result in this file is a crafted tensor returned by a fake
onnxruntime session, so these tests check geometry, decoding, NMS, class selection and error
handling only. They say NOTHING about how well YOLO11n finds phones (that needs the real model and
measured clips). A phone box is a detector output, never evidence that anything was photographed.

The only test that touches the real onnxruntime is ``test_load_garbage_file_raises_model_invalid``
(it feeds a non-ONNX file to ORT and expects a clean DetectorLoadError).
"""

from __future__ import annotations

import hashlib
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from proctor.phone.config import PhoneConfig
from proctor.phone.detector import (
    MAX_NMS_CANDIDATES,
    PAD_VALUE,
    STRIDE,
    DetectorInfo,
    DetectorLoadError,
    Letterbox,
    RawDetection,
    YoloOnnxDetector,
    decode,
    iou_matrix,
    letterbox_params,
    nms,
    parse_names,
    preprocess,
)
from proctor.phone.manifest import load_manifest
from proctor.phone.tests.helpers import make_image
from proctor_contracts.v1 import BBox, ModelManifest, PhoneDetection

cv2 = pytest.importorskip("cv2")

MANIFEST = load_manifest()
COCO = {int(k): v for k, v in (MANIFEST.class_names or {}).items()}
PHONE_IDX = 67  # index of "cell phone" in the committed COCO manifest (the detector looks it up by NAME)
PAD = np.float32(PAD_VALUE) / np.float32(255.0)


# --------------------------------------------------------------------------------------------
# helpers: crafted raw YOLO head outputs + fake ORT session
# --------------------------------------------------------------------------------------------
Row = tuple[float, float, float, float, dict[int, float]]  # x1, y1, x2, y2 (INPUT pixels), {class: score}


def raw_output(rows: list[Row], nc: int = 80, layout: str = "CN", background: int = 25) -> np.ndarray:
    """YOLOv8/11 raw head: (1, 4+nc, N) for layout "CN" or (1, N, 4+nc) for "NC".

    ``background`` extra anchors with all-zero scores are appended (as in a real head, most anchors
    score ~0); they must never produce detections.
    """
    out = np.zeros((len(rows) + background, 4 + nc), dtype=np.float32)
    for k, (x1, y1, x2, y2, scores) in enumerate(rows):
        out[k, :4] = [(x1 + x2) / 2.0, (y1 + y2) / 2.0, x2 - x1, y2 - y1]
        for cls, score in scores.items():
            out[k, 4 + cls] = score
    if background:
        out[len(rows) :, :4] = [320.0, 240.0, 50.0, 50.0]
    return out.T[None].copy() if layout == "CN" else out[None].copy()


class FakeSession:
    """Stands in for onnxruntime.InferenceSession.run; ``make_output(tensor) -> raw head array``."""

    def __init__(self, make_output):
        self.make_output = make_output
        self.feeds: list[dict[str, np.ndarray]] = []

    def run(self, output_names, feed):
        assert output_names is None
        self.feeds.append(feed)
        (tensor,) = feed.values()
        return [self.make_output(tensor)]


def make_detector(
    make_output,
    *,
    config: PhoneConfig | None = None,
    rect: bool = True,
    static: tuple[int, int] | None = None,
    input_size: int = 640,
    names: dict[int, str] | None = None,
    phone: dict[int, str] | None = None,
) -> tuple[YoloOnnxDetector, FakeSession]:
    """YoloOnnxDetector with an injected fake session (no ONNX file, no onnxruntime)."""
    cfg = config or PhoneConfig()
    det = YoloOnnxDetector(Path("unused-fake.onnx"), MANIFEST, cfg)
    names = COCO if names is None else names
    if phone is None:  # explicit, by NAME (mirrors the documented rule; load() itself is tested below)
        phone = {i: n for i, n in sorted(names.items()) if n in cfg.phone_class_names}
    session = FakeSession(make_output)
    det._session = session
    det._cv2 = cv2
    det.info = DetectorInfo(
        input_name="images",
        class_names=names,
        phone_classes=phone,
        provider="FakeSession(test)",
        intra_op_threads=1,
        static_input=static,
        input_size=input_size,
        rect=False if static else rect,
    )
    return det, session


def const_output(rows: list[Row], nc: int = 80, layout: str = "CN"):
    arr = raw_output(rows, nc=nc, layout=layout)
    return lambda tensor: arr


def assert_contract_ok(det: RawDetection) -> None:
    """A RawDetection must be convertible to the wire PhoneDetection (normalized bbox, unit confidence)."""
    PhoneDetection.model_validate(
        {
            "bbox": BBox(x_min=det.x_min, y_min=det.y_min, x_max=det.x_max, y_max=det.y_max).model_dump(mode="json"),
            "confidence": det.confidence,
            "class_name": det.class_name,
            "class_index": det.class_index,
        }
    )


# --------------------------------------------------------------------------------------------
# letterbox_params
# --------------------------------------------------------------------------------------------
def test_letterbox_640x480_rect_is_identity():
    lb = letterbox_params(480, 640, 640, rect=True)
    assert isinstance(lb, Letterbox)
    assert lb == Letterbox(scale=1.0, left=0, top=0, new_w=640, new_h=480, in_w=640, in_h=480)


def test_letterbox_491_high_pads_top_to_stride_multiple():
    lb = letterbox_params(491, 640, 640, rect=True)
    assert (lb.scale, lb.new_w, lb.new_h) == (1.0, 640, 491)
    assert (lb.in_w, lb.in_h) == (640, 512)
    assert lb.left == 0 and lb.top == (512 - 491) // 2 == 10
    assert lb.in_h % STRIDE == 0 and lb.in_w % STRIDE == 0


def test_letterbox_portrait_pads_left():
    lb = letterbox_params(640, 491, 640, rect=True)
    assert (lb.in_w, lb.in_h, lb.left, lb.top) == (512, 640, 10, 0)


def test_letterbox_square_mode_is_640x640_centered():
    lb = letterbox_params(480, 640, 640, rect=False)
    assert (lb.in_w, lb.in_h) == (640, 640)
    assert (lb.scale, lb.left, lb.top, lb.new_w, lb.new_h) == (1.0, 0, 80, 640, 480)


def test_letterbox_static_input_shape_wins_over_size_and_rect():
    lb = letterbox_params(480, 640, 320, rect=True, static=(640, 640))
    assert (lb.in_w, lb.in_h, lb.scale, lb.top) == (640, 640, 1.0, 80)
    lb2 = letterbox_params(480, 640, 640, rect=True, static=(320, 640))  # non-square static input
    assert (lb2.in_w, lb2.in_h) == (640, 320)
    assert lb2.scale == pytest.approx(320 / 480)
    assert (lb2.new_w, lb2.new_h) == (427, 320)
    assert lb2.left == (640 - 427) // 2 and lb2.top == 0


@pytest.mark.parametrize(
    "h, w, rect, expected",
    [
        (240, 320, True, Letterbox(2.0, 0, 0, 640, 480, 640, 480)),  # upscaling
        (10, 10, True, Letterbox(64.0, 0, 0, 640, 640, 640, 640)),  # tiny frame
        (720, 1280, True, Letterbox(0.5, 0, 12, 640, 360, 640, 384)),
        (720, 1280, False, Letterbox(0.5, 0, 140, 640, 360, 640, 640)),
        (1080, 1920, True, Letterbox(1 / 3, 0, 12, 640, 360, 640, 384)),
    ],
)
def test_letterbox_scaling_cases(h, w, rect, expected):
    lb = letterbox_params(h, w, 640, rect=rect)
    assert lb.scale == pytest.approx(expected.scale)
    assert lb[1:] == expected[1:]


@pytest.mark.parametrize("h, w", [(1, 1), (1, 1000), (1000, 1), (4000, 6000), (3, 5000), (2160, 3840)])
def test_letterbox_invariants_for_extreme_sizes(h, w):
    for rect in (True, False):
        lb = letterbox_params(h, w, 640, rect=rect)
        assert lb.new_w >= 1 and lb.new_h >= 1
        assert max(lb.in_w, lb.in_h) == 640
        assert lb.in_w % STRIDE == 0 and lb.in_h % STRIDE == 0
        assert 0 <= lb.left and lb.left + lb.new_w <= lb.in_w
        assert 0 <= lb.top and lb.top + lb.new_h <= lb.in_h
        if not rect:
            assert (lb.in_w, lb.in_h) == (640, 640)


def test_letterbox_smaller_input_size():
    lb = letterbox_params(480, 640, 320, rect=True)
    assert (lb.scale, lb.new_w, lb.new_h, lb.in_w, lb.in_h, lb.top) == (0.5, 320, 240, 320, 256, 8)


@pytest.mark.parametrize("h, w", [(0, 640), (480, 0), (0, 0), (-1, 640)])
def test_letterbox_rejects_empty_image(h, w):
    with pytest.raises(ValueError):
        letterbox_params(h, w, 640, rect=True)


# --------------------------------------------------------------------------------------------
# preprocess
# --------------------------------------------------------------------------------------------
def test_preprocess_shape_dtype_range_and_contiguity():
    image = make_image(640, 480)
    lb = letterbox_params(480, 640, 640, rect=True)
    t = preprocess(image, lb, cv2)
    assert t.shape == (1, 3, 480, 640)
    assert t.dtype == np.float32
    assert t.flags.c_contiguous
    assert float(t.min()) >= 0.0 and float(t.max()) <= 1.0


def test_preprocess_padding_is_114_gray_and_content_is_exact_without_resize():
    image = make_image(640, 480, seed=3)
    lb = letterbox_params(480, 640, 640, rect=False)  # top=80, bottom pad 80
    t = preprocess(image, lb, cv2)
    assert t.shape == (1, 3, 640, 640)
    assert np.all(t[0, :, :80, :] == PAD)
    assert np.all(t[0, :, 560:, :] == PAD)
    content = np.rint(t[0, :, 80:560, :] * 255.0).astype(np.uint8)  # CHW, RGB
    np.testing.assert_array_equal(content, image[:, :, ::-1].transpose(2, 0, 1))


def test_preprocess_left_right_padding_for_portrait_frame():
    image = make_image(491, 640, seed=4)  # width 491 -> in_w 512, left=10, right=11
    lb = letterbox_params(640, 491, 640, rect=True)
    t = preprocess(image, lb, cv2)
    assert t.shape == (1, 3, 640, 512)
    assert np.all(t[0, :, :, :10] == PAD)
    assert np.all(t[0, :, :, 10 + 491 :] == PAD)


def test_preprocess_converts_bgr_to_rgb_with_pure_blue_image():
    blue = np.zeros((480, 640, 3), dtype=np.uint8)
    blue[:, :, 0] = 255  # BGR: pure blue
    blue.flags.writeable = False
    lb = letterbox_params(480, 640, 640, rect=False)
    t = preprocess(blue, lb, cv2)[0]
    region = t[:, 80:560, :]
    assert np.all(region[0] == 0.0)  # R
    assert np.all(region[1] == 0.0)  # G
    assert np.all(region[2] == 1.0)  # B
    assert np.all(t[:, :80, :] == PAD)  # padding is neutral gray in every channel


@pytest.mark.parametrize("w, h, rect", [(640, 480, True), (1280, 720, True), (320, 240, False), (491, 640, True)])
def test_preprocess_never_writes_into_read_only_input(w, h, rect):
    image = make_image(w, h, seed=7)
    assert not image.flags.writeable
    before = image.copy()
    lb = letterbox_params(h, w, 640, rect=rect)
    t = preprocess(image, lb, cv2)
    np.testing.assert_array_equal(image, before)
    assert not image.flags.writeable
    assert t.shape == (1, 3, lb.in_h, lb.in_w)
    t[...] = 0.0  # the tensor is a separate buffer
    np.testing.assert_array_equal(image, before)


def test_preprocess_accepts_non_contiguous_read_only_view():
    big = make_image(1280, 960, seed=8)
    view = big[::2, ::2]
    assert not view.flags.c_contiguous
    lb = letterbox_params(480, 640, 640, rect=True)
    t = preprocess(view, lb, cv2)
    assert t.shape == (1, 3, 480, 640)
    np.testing.assert_array_equal(np.rint(t[0] * 255.0).astype(np.uint8), view[:, :, ::-1].transpose(2, 0, 1))


# --------------------------------------------------------------------------------------------
# decode
# --------------------------------------------------------------------------------------------
ROWS: list[Row] = [
    (100, 50, 300, 250, {PHONE_IDX: 0.9}),
    (400, 100, 450, 200, {PHONE_IDX: 0.3, 0: 0.95}),
    (10, 10, 20, 20, {PHONE_IDX: 0.1}),  # below threshold
    (500, 300, 600, 460, {0: 0.99}),  # person only, no phone score
]


@pytest.mark.parametrize("layout", ["CN", "NC"])
def test_decode_accepts_both_layouts_and_returns_xyxy(layout):
    boxes, scores, classes = decode(raw_output(ROWS, layout=layout), 80, [PHONE_IDX], 0.25)
    assert boxes.shape == (2, 4) and boxes.dtype == np.float32
    assert scores.dtype == np.float32
    np.testing.assert_allclose(boxes[0], [100, 50, 300, 250], atol=1e-4)
    np.testing.assert_allclose(boxes[1], [400, 100, 450, 200], atol=1e-4)
    np.testing.assert_allclose(scores, [0.9, 0.3], atol=1e-6)
    assert classes.tolist() == [PHONE_IDX, PHONE_IDX]  # the REAL model index, not 0


def test_decode_layouts_give_identical_results():
    a = decode(raw_output(ROWS, layout="CN"), 80, [PHONE_IDX], 0.05)
    b = decode(raw_output(ROWS, layout="NC"), 80, [PHONE_IDX], 0.05)
    for x, y in zip(a, b):
        np.testing.assert_array_equal(x, y)


def test_decode_threshold_is_inclusive_and_filters_below():
    rows: list[Row] = [(0, 0, 10, 10, {PHONE_IDX: 0.5}), (20, 20, 30, 30, {PHONE_IDX: 0.25})]
    _, scores, _ = decode(raw_output(rows), 80, [PHONE_IDX], 0.5)
    assert scores.tolist() == [0.5]  # score == threshold is kept
    _, scores, _ = decode(raw_output(rows), 80, [PHONE_IDX], 0.5000001)
    assert scores.size == 0


def test_decode_ignores_non_phone_class_scores():
    rows: list[Row] = [(0, 0, 100, 100, {0: 0.99, 65: 0.97, 63: 0.9})]  # person/remote/laptop, no phone
    boxes, scores, classes = decode(raw_output(rows), 80, [PHONE_IDX], 0.2)
    assert boxes.shape == (0, 4) and scores.shape == (0,) and classes.shape == (0,)
    assert boxes.dtype == np.float32 and scores.dtype == np.float32


def test_decode_multiple_selected_classes_returns_best_real_index():
    rows: list[Row] = [
        (0, 0, 50, 50, {65: 0.7, PHONE_IDX: 0.4}),
        (100, 0, 150, 50, {65: 0.3, PHONE_IDX: 0.8}),
    ]
    _, scores, classes = decode(raw_output(rows), 80, [PHONE_IDX, 65], 0.2)
    assert classes.tolist() == [65, PHONE_IDX]
    np.testing.assert_allclose(scores, [0.7, 0.8], atol=1e-6)


def test_decode_custom_class_count():
    rows: list[Row] = [(5, 5, 25, 45, {2: 0.6, 1: 0.9})]
    boxes, scores, classes = decode(raw_output(rows, nc=3), 3, [2], 0.2)
    assert classes.tolist() == [2] and scores.tolist() == pytest.approx([0.6])
    np.testing.assert_allclose(boxes[0], [5, 5, 25, 45], atol=1e-5)


@pytest.mark.parametrize(
    "shape",
    [(1, 85, 30), (1, 30, 83), (1, 84 + 1, 84 + 1), (2, 84, 30), (1, 1, 84, 30), (84,)],
)
def test_decode_rejects_wrong_output_shape(shape):
    with pytest.raises(ValueError):
        decode(np.zeros(shape, dtype=np.float32), 80, [PHONE_IDX], 0.2)


def test_decode_caps_candidates_at_max_nms_candidates_keeping_best():
    n = MAX_NMS_CANDIDATES + 700
    rng = np.random.default_rng(11)
    raw_scores = rng.permutation(np.linspace(0.30, 0.95, n).astype(np.float32))
    rows: list[Row] = [(float(i), 0.0, float(i) + 5.0, 5.0, {PHONE_IDX: float(s)}) for i, s in enumerate(raw_scores)]
    boxes, scores, classes = decode(raw_output(rows, background=0), 80, [PHONE_IDX], 0.2)
    assert boxes.shape == (MAX_NMS_CANDIDATES, 4) and scores.shape == (MAX_NMS_CANDIDATES,)
    expected_top = np.sort(raw_scores)[::-1][:MAX_NMS_CANDIDATES]
    np.testing.assert_array_equal(np.sort(scores)[::-1], expected_top)
    # boxes stay paired with their own scores
    for b, s in zip(boxes[:20], scores[:20]):
        i = int(round(float(b[0])))
        assert raw_scores[i] == s
    assert set(classes.tolist()) == {PHONE_IDX}


def test_decode_exactly_max_candidates_is_not_truncated():
    rows: list[Row] = [(float(i), 0.0, float(i) + 5.0, 5.0, {PHONE_IDX: 0.5}) for i in range(MAX_NMS_CANDIDATES)]
    boxes, _, _ = decode(raw_output(rows, background=0), 80, [PHONE_IDX], 0.2)
    assert boxes.shape[0] == MAX_NMS_CANDIDATES


# --------------------------------------------------------------------------------------------
# iou_matrix / nms
# --------------------------------------------------------------------------------------------
def f32(*rows) -> np.ndarray:
    return np.asarray(rows, dtype=np.float32).reshape(-1, 4)


def test_iou_matrix_known_values():
    a = f32([0, 0, 2, 2], [0, 0, 4, 4])
    b = f32([0, 0, 2, 2], [1, 0, 3, 2], [2, 0, 4, 2], [5, 5, 6, 6], [1, 1, 3, 3])
    m = iou_matrix(a, b)
    assert m.shape == (2, 5) and m.dtype == np.float32
    np.testing.assert_allclose(m[0], [1.0, 1 / 3, 0.0, 0.0, 1 / 7], atol=1e-6)  # touching edges -> 0
    np.testing.assert_allclose(m[1, 4], 4 / 16, atol=1e-6)  # contained box
    np.testing.assert_allclose(iou_matrix(b, a), m.T, atol=1e-7)  # symmetric


def test_iou_matrix_empty_and_degenerate_boxes():
    assert iou_matrix(f32(), f32([0, 0, 1, 1])).shape == (0, 1)
    assert iou_matrix(f32([0, 0, 1, 1]), f32()).shape == (1, 0)
    zero = f32([1, 1, 1, 1])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        m = iou_matrix(zero, np.concatenate([zero, f32([0, 0, 2, 2])]))
    assert np.all(np.isfinite(m)) and np.all(m == 0.0)


def test_nms_suppresses_overlap_and_keeps_disjoint_in_score_order():
    boxes = f32([0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60], [100, 0, 110, 10])
    scores = np.asarray([0.6, 0.9, 0.7, 0.3], dtype=np.float32)
    keep = nms(boxes, scores, 0.45)
    assert keep == [1, 2, 3]  # box 0 (IoU ~0.68 with box 1, lower score) suppressed
    assert all(type(i) is int for i in keep)


def test_nms_equal_scores_is_deterministic_lowest_index_wins():
    boxes = f32([0, 0, 10, 10], [0, 0, 10, 10], [20, 0, 30, 10], [40, 0, 50, 10])
    scores = np.full(4, 0.5, dtype=np.float32)
    results = {tuple(nms(boxes, scores, 0.45)) for _ in range(5)}
    assert results == {(0, 2, 3)}


def test_nms_threshold_is_strict_greater_for_suppression():
    boxes = f32([0, 0, 3, 1], [1, 0, 4, 1])  # IoU exactly 0.5
    scores = np.asarray([0.9, 0.8], dtype=np.float32)
    assert float(iou_matrix(boxes[:1], boxes[1:])[0, 0]) == 0.5
    assert nms(boxes, scores, 0.5) == [0, 1]  # IoU == threshold -> kept
    assert nms(boxes, scores, 0.49) == [0]  # IoU > threshold -> suppressed


def test_nms_greedy_chain_and_empty():
    # A overlaps B, B overlaps C, A and C disjoint: greedy keeps A and C.
    boxes = f32([0, 0, 10, 10], [5, 0, 15, 10], [10, 0, 20, 10])
    scores = np.asarray([0.9, 0.8, 0.7], dtype=np.float32)
    assert nms(boxes, scores, 0.3) == [0, 2]
    assert nms(f32(), np.zeros((0,), dtype=np.float32), 0.45) == []


# --------------------------------------------------------------------------------------------
# parse_names
# --------------------------------------------------------------------------------------------
def test_parse_names_ultralytics_dict_literal():
    raw = str(COCO)  # Ultralytics writes str(dict): "{0: 'person', 1: 'bicycle', ...}"
    assert raw.startswith("{0: 'person'")
    names = parse_names(raw)
    assert names == COCO
    assert names[PHONE_IDX] == "cell phone"


def test_parse_names_list_literal_and_empty():
    assert parse_names("['person', 'remote', 'cell phone']") == {0: "person", 1: "remote", 2: "cell phone"}
    assert parse_names(None) is None
    assert parse_names("") is None


@pytest.mark.parametrize(
    "raw",
    [
        "{0: 'person'",  # truncated
        "not python at all",
        "{'0': 'person'}",  # string keys
        "{0: 1}",  # non-string names
        "[1, 2]",
        "42",
        "{0: 'a', 1.0: 'b'}",
        "('person', 'cell phone')",  # tuple is not an accepted container
        "{0: 'a'}.keys()",
    ],
)
def test_parse_names_rejects_non_names(raw):
    assert parse_names(raw) is None


def test_parse_names_malicious_strings_return_none_without_side_effects(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(os, "system", lambda cmd: calls.append(cmd) or 0)
    marker = tmp_path / "pwned.txt"
    payloads = [
        "__import__('os').system('echo x')",
        "{0: __import__('os').system('echo x')}",
        f"open({str(marker)!r}, 'w').write('x')",
        "(lambda: __import__('os').system('echo x'))()",
        "[c for c in ().__class__.__base__.__subclasses__()]",
        "exec('import os; os.system(\"echo x\")')",
    ]
    for raw in payloads:
        assert parse_names(raw) is None, raw
    assert calls == []
    assert not marker.exists()


@pytest.mark.parametrize("raw", ["{[1]: 'a'}", "{{}: 'a'}"])
def test_parse_names_unhashable_key_literal_returns_none(raw):
    assert parse_names(raw) is None


# --------------------------------------------------------------------------------------------
# YoloOnnxDetector.detect with a FAKE session (geometry / class / filtering logic only)
# --------------------------------------------------------------------------------------------
def test_detect_before_load_raises():
    det = YoloOnnxDetector(Path("unused.onnx"), MANIFEST, PhoneConfig())
    with pytest.raises(RuntimeError):
        det.detect(make_image())


# (frame w, h), rect, static, ORIGINAL box px, expected INPUT box px, expected tensor (H, W)
MAPPING_CASES = [
    pytest.param((640, 480), True, None, (100, 50, 300, 250), (100, 50, 300, 250), (480, 640), id="640x480-rect"),
    pytest.param((640, 480), False, None, (100, 50, 300, 250), (100, 130, 300, 330), (640, 640), id="640x480-square"),
    pytest.param((640, 491), True, None, (100, 50, 300, 250), (100, 60, 300, 260), (512, 640), id="640x491-rect"),
    pytest.param((491, 640), True, None, (100, 50, 300, 250), (110, 50, 310, 250), (640, 512), id="491x640-portrait"),
    pytest.param((1280, 720), True, None, (100, 50, 300, 250), (50, 37, 150, 137), (384, 640), id="1280x720-rect"),
    pytest.param((1280, 720), False, None, (100, 50, 300, 250), (50, 165, 150, 265), (640, 640), id="1280x720-square"),
    pytest.param((1280, 720), True, (640, 640), (100, 50, 300, 250), (50, 165, 150, 265), (640, 640), id="1280x720-static"),
    pytest.param((320, 240), True, None, (40, 20, 140, 120), (80, 40, 280, 240), (480, 640), id="320x240-upscale"),
    pytest.param((640, 480), True, (320, 640), (90, 60, 300, 240), (166, 40, 306, 160), (320, 640), id="640x480-static-320x640"),
]


@pytest.mark.parametrize("layout", ["CN", "NC"])
@pytest.mark.parametrize("frame_wh, rect, static, orig_px, input_px, tensor_hw", MAPPING_CASES)
def test_detect_maps_input_pixels_to_normalized_original_frame(frame_wh, rect, static, orig_px, input_px, tensor_hw, layout):
    w, h = frame_wh
    det, session = make_detector(const_output([(*input_px, {PHONE_IDX: 0.8})], layout=layout), rect=rect, static=static)
    image = make_image(w, h)
    detections, timings = det.detect(image)

    (feed,) = session.feeds
    assert list(feed) == ["images"]
    assert feed["images"].shape == (1, 3, *tensor_hw) and feed["images"].dtype == np.float32
    assert len(detections) == 1
    d = detections[0]
    x1, y1, x2, y2 = orig_px
    assert d.x_min == pytest.approx(x1 / w, abs=1e-4)
    assert d.y_min == pytest.approx(y1 / h, abs=1e-4)
    assert d.x_max == pytest.approx(x2 / w, abs=1e-4)
    assert d.y_max == pytest.approx(y2 / h, abs=1e-4)
    assert d.confidence == pytest.approx(0.8, abs=1e-6)
    assert (d.class_index, d.class_name) == (PHONE_IDX, "cell phone")
    assert set(timings) == {"pre_ms", "infer_ms", "post_ms"} and all(v >= 0 for v in timings.values())
    assert_contract_ok(d)


ROUNDTRIP_CASES = [
    pytest.param(640, 480, True, None, id="640x480-rect"),
    pytest.param(640, 480, False, None, id="640x480-square"),
    pytest.param(640, 491, True, None, id="640x491-rect"),
    pytest.param(1280, 720, True, None, id="1280x720-rect"),
    pytest.param(1280, 720, False, None, id="1280x720-square"),
    pytest.param(720, 1280, True, None, id="720x1280-portrait"),
    pytest.param(320, 240, True, None, id="320x240-upscale"),
    pytest.param(1280, 720, True, (640, 640), id="1280x720-static"),
]


@pytest.mark.parametrize("w, h, rect, static", ROUNDTRIP_CASES)
def test_detect_roundtrip_marker_through_real_preprocess(w, h, rect, static):
    """A pure-red rectangle drawn in the ORIGINAL frame is located in the preprocessed tensor by the fake
    session and reported as a box there; detect() must map it back onto the drawn rectangle.
    This checks letterbox + preprocess + inverse mapping consistency, NOT detection quality."""
    ox1, oy1, ox2, oy2 = int(0.30 * w), int(0.20 * h), int(0.55 * w), int(0.65 * h)
    image = make_image(w, h, seed=5).copy()
    image[oy1:oy2, ox1:ox2] = (0, 0, 255)  # BGR red
    image.flags.writeable = False
    before = image.copy()

    def find_red(tensor: np.ndarray) -> np.ndarray:
        r, g, b = tensor[0]
        ys, xs = np.nonzero((r > 0.9) & (g < 0.2) & (b < 0.2))
        assert xs.size, "marker not found in the network input"
        row: Row = (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1), {PHONE_IDX: 0.9})
        return raw_output([row])

    det, _ = make_detector(find_red, rect=rect, static=static)
    detections, _ = det.detect(image)
    np.testing.assert_array_equal(image, before)
    assert len(detections) == 1
    d = detections[0]
    tol_x, tol_y = 3.0 / w, 3.0 / h  # resize interpolation blurs the edge by ~1 input pixel
    assert d.x_min == pytest.approx(ox1 / w, abs=tol_x)
    assert d.y_min == pytest.approx(oy1 / h, abs=tol_y)
    assert d.x_max == pytest.approx(ox2 / w, abs=tol_x)
    assert d.y_max == pytest.approx(oy2 / h, abs=tol_y)


def test_detect_clips_boxes_to_unit_square():
    # 640x480 square letterbox: top padding 80. Box spills over the left/top padding and the right edge.
    det, _ = make_detector(const_output([(-20, 40, 700, 200, {PHONE_IDX: 0.9})]), rect=False)
    (d,), _ = det.detect(make_image())
    assert (d.x_min, d.y_min, d.x_max) == (0.0, 0.0, 1.0)
    assert d.y_max == pytest.approx((200 - 80) / 480, abs=1e-5)
    for v in (d.x_min, d.y_min, d.x_max, d.y_max):
        assert 0.0 <= v <= 1.0
    assert_contract_ok(d)


def test_detect_drops_box_entirely_inside_padding():
    det, _ = make_detector(const_output([(100, 10, 300, 70, {PHONE_IDX: 0.95})]), rect=False)  # y in top pad
    detections, _ = det.detect(make_image())
    assert detections == []


def test_detect_drops_boxes_below_min_box_area():
    rows: list[Row] = [
        (10, 10, 14, 14, {PHONE_IDX: 0.95}),  # 4x4 px of 640x480 -> 5.2e-5 < 1e-4
        (200, 200, 220, 220, {PHONE_IDX: 0.6}),  # 20x20 px -> 1.3e-3
    ]
    det, _ = make_detector(const_output(rows))
    detections, _ = det.detect(make_image())
    assert len(detections) == 1 and detections[0].confidence == pytest.approx(0.6)
    assert detections[0].area >= det.config.min_box_area

    det0, _ = make_detector(const_output(rows), config=PhoneConfig(min_box_area=0.0))
    assert len(det0.detect(make_image())[0]) == 2


def test_detect_respects_max_detections_in_score_order():
    scores = [0.4, 0.9, 0.5, 0.8, 0.6, 0.7]
    rows: list[Row] = [(i * 100.0, 100.0, i * 100.0 + 60.0, 200.0, {PHONE_IDX: s}) for i, s in enumerate(scores)]
    det, _ = make_detector(const_output(rows), config=PhoneConfig(max_detections=3))
    detections, _ = det.detect(make_image())
    assert [round(d.confidence, 4) for d in detections] == [0.9, 0.8, 0.7]


def test_detect_dropped_small_boxes_do_not_consume_max_detections():
    rows: list[Row] = [
        (10, 10, 13, 13, {PHONE_IDX: 0.99}),  # too small, dropped
        (100, 100, 160, 200, {PHONE_IDX: 0.9}),
        (300, 100, 360, 200, {PHONE_IDX: 0.8}),
        (500, 100, 560, 200, {PHONE_IDX: 0.7}),
    ]
    det, _ = make_detector(const_output(rows), config=PhoneConfig(max_detections=2))
    detections, _ = det.detect(make_image())
    assert [round(d.confidence, 4) for d in detections] == [0.9, 0.8]


def test_detect_default_max_detections_fits_contract_limit():
    rows: list[Row] = [((i % 8) * 80.0, (i // 8) * 80.0, (i % 8) * 80.0 + 40.0, (i // 8) * 80.0 + 40.0, {PHONE_IDX: 0.5 + i / 100}) for i in range(40)]
    det, _ = make_detector(const_output(rows))
    detections, _ = det.detect(make_image())
    assert len(detections) == 16  # PhoneConfig.max_detections default == PhoneObservation.detections max_length
    for d in detections:
        assert_contract_ok(d)


def test_detect_applies_nms_and_threshold():
    rows: list[Row] = [
        (100, 100, 200, 220, {PHONE_IDX: 0.6}),
        (104, 102, 204, 222, {PHONE_IDX: 0.9}),  # same object, higher score
        (400, 100, 480, 220, {PHONE_IDX: 0.19}),  # below default conf_threshold 0.20
    ]
    det, _ = make_detector(const_output(rows))
    detections, _ = det.detect(make_image())
    assert len(detections) == 1
    assert detections[0].confidence == pytest.approx(0.9)
    assert detections[0].x_min == pytest.approx(104 / 640, abs=1e-5)


def test_detect_ignores_classes_not_named_in_config():
    rows: list[Row] = [
        (100, 100, 200, 220, {0: 0.99, PHONE_IDX: 0.05}),  # person
        (300, 100, 360, 220, {65: 0.95}),  # remote
        (450, 100, 520, 220, {63: 0.95, 73: 0.9}),  # laptop / book
    ]
    det, _ = make_detector(const_output(rows))
    assert det.info.phone_classes == {PHONE_IDX: "cell phone"}
    detections, _ = det.detect(make_image())
    assert detections == []


def test_detect_class_name_comes_from_model_names():
    names = {0: "person", 1: "mobile phone", 2: "remote"}
    cfg = PhoneConfig(phone_class_names=("mobile phone",))
    rows: list[Row] = [(100, 100, 200, 220, {1: 0.7}), (300, 100, 360, 220, {2: 0.9})]
    det, _ = make_detector(const_output(rows, nc=3), config=cfg, names=names)
    detections, _ = det.detect(make_image())
    assert [(d.class_index, d.class_name) for d in detections] == [(1, "mobile phone")]


def test_detect_confidence_is_clamped_to_unit_interval():
    det, _ = make_detector(const_output([(100, 100, 200, 220, {PHONE_IDX: 1.5})]))
    (d,), _ = det.detect(make_image())
    assert d.confidence == 1.0
    assert_contract_ok(d)


def test_detect_does_not_modify_read_only_frame_and_is_repeatable():
    det, session = make_detector(const_output([(100, 50, 300, 250, {PHONE_IDX: 0.8})]))
    image = make_image(1280, 720, seed=9)
    before = image.copy()
    first, _ = det.detect(image)
    second, _ = det.detect(image)
    np.testing.assert_array_equal(image, before)
    assert not image.flags.writeable
    assert first == second
    np.testing.assert_array_equal(session.feeds[0]["images"], session.feeds[1]["images"])


# --------------------------------------------------------------------------------------------
# YoloOnnxDetector.load
# --------------------------------------------------------------------------------------------
def _manifest_for(path: Path, models_dir: Path, class_names: dict[str, str] | None = None) -> ModelManifest:
    data = path.read_bytes()
    return ModelManifest(
        model_id="unit-test-model",
        module="phone",
        task="object_detection",
        file=path.relative_to(models_dir).as_posix(),
        format="onnx",
        version="test",
        source_url="https://example.invalid/unit-test.onnx",
        license="test-only",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        class_names=class_names,
        prepared_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
    )


def test_load_garbage_file_raises_model_invalid(tmp_path):
    pytest.importorskip("onnxruntime")
    models_dir = tmp_path / "models"
    path = models_dir / "phone" / "garbage.onnx"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not an onnx model\x00\x01\x02" * 64)
    det = YoloOnnxDetector(path, _manifest_for(path, models_dir), PhoneConfig())
    with pytest.raises(DetectorLoadError) as err:
        det.load()
    assert err.value.code == "model_invalid"
    assert err.value.message
    assert det._session is None and det.info is None
    with pytest.raises(RuntimeError):
        det.detect(make_image())


# --- load() with a FAKE onnxruntime module: class selection by NAME, static input, layout check ----
class _FakeInput:
    def __init__(self, name: str, shape: list):
        self.name = name
        self.shape = shape


class _FakeOrtSession(FakeSession):
    def __init__(self, shape: list, meta: dict[str, str], make_output, providers: list[str]):
        super().__init__(make_output)
        self._inputs = [_FakeInput("images", shape)]
        self._meta = meta
        self._providers = providers

    def get_inputs(self):
        return self._inputs

    def get_modelmeta(self):
        return SimpleNamespace(custom_metadata_map=dict(self._meta))

    def get_providers(self):
        return list(self._providers)


def _fake_ort(monkeypatch, *, shape, meta, make_output) -> SimpleNamespace:
    created: list[_FakeOrtSession] = []

    def inference_session(path, sess_options=None, providers=None):
        s = _FakeOrtSession(shape, meta, make_output, providers)
        created.append(s)
        return s

    ort = SimpleNamespace(
        SessionOptions=lambda: SimpleNamespace(),
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="seq"),
        GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL="all"),
        get_available_providers=lambda: ["CPUExecutionProvider"],
        InferenceSession=inference_session,
        created=created,
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    return ort


def _zeros_output(nc: int):
    return lambda tensor: np.zeros((1, 4 + nc, 21), dtype=np.float32)


def _dummy_model(tmp_path: Path, class_names: dict[str, str] | None) -> tuple[Path, ModelManifest]:
    models_dir = tmp_path / "models"
    path = models_dir / "phone" / "fake.onnx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake model bytes (never parsed: onnxruntime is faked)")
    return path, _manifest_for(path, models_dir, class_names)


def test_load_selects_phone_class_by_name_not_by_index(monkeypatch, tmp_path):
    names = {0: "person", 1: "remote", 2: "cell phone"}
    ort = _fake_ort(
        monkeypatch,
        shape=["batch", 3, "height", "width"],
        meta={"task": "detect", "names": str(names), "version": "8.3.0", "license": "AGPL-3.0"},
        make_output=_zeros_output(3),
    )
    path, manifest = _dummy_model(tmp_path, {str(k): v for k, v in names.items()})
    det = YoloOnnxDetector(path, manifest, PhoneConfig())
    info = det.load()

    session = ort.created[0]
    assert session._providers == ["CPUExecutionProvider"]  # default execution_provider="cpu"
    assert info.phone_classes == {2: "cell phone"}
    assert info.class_names == names
    assert info.static_input is None and info.rect is True and info.input_size == 640
    assert info.warmup_ms is not None and info.warmup_ms >= 0
    assert session.feeds[0]["images"].shape == (1, 3, 480, 640)  # warm-up on a 640x480 gray frame

    session.make_output = const_output(
        [(100, 100, 200, 220, {1: 0.95}), (300, 100, 360, 220, {2: 0.6})], nc=3
    )
    detections, _ = det.detect(make_image())
    assert [(d.class_index, d.class_name) for d in detections] == [(2, "cell phone")]

    det2 = YoloOnnxDetector(path, manifest, PhoneConfig(phone_class_names=("cell phone", "remote")))
    det2.load()
    assert det2.info.phone_classes == {1: "remote", 2: "cell phone"}
    ort.created[-1].make_output = session.make_output
    detections, _ = det2.detect(make_image())
    assert [(d.class_index, d.class_name) for d in detections] == [(1, "remote"), (2, "cell phone")]


def test_load_detects_static_input_shape(monkeypatch, tmp_path):
    ort = _fake_ort(
        monkeypatch,
        shape=[1, 3, 640, 640],
        meta={"task": "detect", "names": str(COCO)},
        make_output=_zeros_output(80),
    )
    path, manifest = _dummy_model(tmp_path, None)
    info = YoloOnnxDetector(path, manifest, PhoneConfig(rect=True, input_size=320)).load()
    assert info.static_input == (640, 640)
    assert info.rect is False and info.input_size == 640
    assert info.phone_classes == {PHONE_IDX: "cell phone"}
    assert ort.created[0].feeds[0]["images"].shape == (1, 3, 640, 640)


@pytest.mark.parametrize(
    "shape, meta, manifest_names, nc_out",
    [
        pytest.param(["b", 3, "h", "w"], {"task": "detect", "names": str(COCO)}, None, 81, id="output-channels-mismatch"),
        pytest.param(["b", 3, "h", "w"], {"task": "segment", "names": str(COCO)}, None, 80, id="wrong-task"),
        pytest.param(["b", 1, "h", "w"], {"task": "detect", "names": str(COCO)}, None, 80, id="one-channel-input"),
        pytest.param(["b", 3, "h"], {"task": "detect", "names": str(COCO)}, None, 80, id="rank-3-input"),
        pytest.param(["b", 3, "h", "w"], {"task": "detect", "names": "{0: 'person', 1: 'car'}"}, None, 2, id="no-phone-class"),
        pytest.param(["b", 3, "h", "w"], {"task": "detect"}, None, 80, id="no-names-anywhere"),
        pytest.param(
            ["b", 3, "h", "w"], {"task": "detect", "names": "{0: 'person', 1: 'cell phone'}"}, {"0": "person", "1": "remote"}, 2,
            id="names-differ-from-manifest",
        ),
    ],
)
def test_load_rejects_invalid_models_with_model_invalid(monkeypatch, tmp_path, shape, meta, manifest_names, nc_out):
    _fake_ort(monkeypatch, shape=shape, meta=meta, make_output=_zeros_output(nc_out))
    path, manifest = _dummy_model(tmp_path, manifest_names)
    det = YoloOnnxDetector(path, manifest, PhoneConfig())
    with pytest.raises(DetectorLoadError) as err:
        det.load()
    assert err.value.code == "model_invalid"
    assert det._session is None


def test_load_uses_manifest_names_when_model_has_none(monkeypatch, tmp_path):
    _fake_ort(monkeypatch, shape=["b", 3, "h", "w"], meta={"task": "detect"}, make_output=_zeros_output(80))
    path, manifest = _dummy_model(tmp_path, MANIFEST.class_names)
    info = YoloOnnxDetector(path, manifest, PhoneConfig()).load()
    assert info.phone_classes == {PHONE_IDX: "cell phone"}
