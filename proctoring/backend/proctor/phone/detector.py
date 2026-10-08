"""YOLO (v8/11 detect head) ONNX inference on CPU with onnxruntime (owner: A03).

* Input: uint8 HxWx3 BGR frame (read-only, never modified) -> letterbox (gray 114 padding) -> RGB
  float32 NCHW in [0, 1]. With ``rect`` the padding goes only up to a stride multiple, so a 640x480 frame
  is inferred at 640x480 instead of 640x640 (the exported model has dynamic H/W; a static-shape model
  is detected and inferred at its fixed size).
* Output: (1, 4+nc, anchors) -> boxes cx,cy,w,h in input pixels + per-class scores (already sigmoid).
  Only the classes whose NAME is in ``config.phone_class_names`` are kept (the index comes from the
  model's own ``names`` metadata, cross-checked with the manifest; 67 is not assumed).
* Greedy NMS, then boxes are mapped back to normalized coordinates of the ORIGINAL unmirrored frame.

cv2/onnxruntime are imported lazily so the package imports (and reports health) without the CV extra.
"""

from __future__ import annotations

import ast
import math
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from proctor_contracts.v1 import ModelManifest

from .config import PhoneConfig

PAD_VALUE = 114
STRIDE = 32
MAX_NMS_CANDIDATES = 300


class DetectorLoadError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RawDetection:
    """One phone box in normalized coordinates of the original (unmirrored) frame."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float
    confidence: float  # detector score for the phone class (not calibrated, not a cheating probability)
    class_index: int
    class_name: str

    @property
    def area(self) -> float:
        return max(0.0, self.x_max - self.x_min) * max(0.0, self.y_max - self.y_min)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x_min + self.x_max) / 2.0, (self.y_min + self.y_max) / 2.0


@dataclass(frozen=True)
class DetectorInfo:
    input_name: str
    class_names: dict[int, str]
    phone_classes: dict[int, str]
    provider: str
    intra_op_threads: int
    static_input: tuple[int, int] | None  # (h, w) when the model has a fixed input size
    input_size: int
    rect: bool
    model_meta: dict[str, str] = field(default_factory=dict)  # selected ONNX metadata (version, license, date)
    warmup_ms: float | None = None


class Letterbox(NamedTuple):
    scale: float
    left: int
    top: int
    new_w: int
    new_h: int
    in_w: int
    in_h: int


def letterbox_params(h: int, w: int, size: int, rect: bool, static: tuple[int, int] | None = None) -> Letterbox:
    if h <= 0 or w <= 0:
        raise ValueError("empty image")
    if static is not None:
        in_h, in_w = static
        scale = min(in_h / h, in_w / w)
    else:
        scale = min(size / h, size / w)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    if static is None:
        if rect:
            in_w, in_h = math.ceil(new_w / STRIDE) * STRIDE, math.ceil(new_h / STRIDE) * STRIDE
        else:
            in_w = in_h = size
    return Letterbox(scale, (in_w - new_w) // 2, (in_h - new_h) // 2, new_w, new_h, in_w, in_h)


def preprocess(image_bgr: np.ndarray, lb: Letterbox, cv2: Any) -> np.ndarray:
    """Never writes into image_bgr (frames are shared read-only between consumers)."""
    h, w = image_bgr.shape[:2]
    resized = image_bgr if (w, h) == (lb.new_w, lb.new_h) else cv2.resize(image_bgr, (lb.new_w, lb.new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((lb.in_h, lb.in_w, 3), PAD_VALUE, dtype=np.uint8)
    canvas[lb.top : lb.top + lb.new_h, lb.left : lb.left + lb.new_w] = resized
    chw = canvas[:, :, ::-1].transpose(2, 0, 1)  # BGR->RGB, HWC->CHW
    return np.ascontiguousarray(chw, dtype=np.float32)[None] / np.float32(255.0)


def decode(
    output: np.ndarray, num_classes: int, class_indices: list[int], conf_threshold: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """YOLOv8/11 raw head -> (xyxy in input pixels, scores, class index). Accepts (1,C,N) or (1,N,C)."""
    channels = 4 + num_classes
    out = np.asarray(output)
    if out.ndim == 3 and out.shape[0] == 1:
        out = out[0]
    if out.ndim != 2:
        raise ValueError(f"unexpected output rank {np.asarray(output).shape}")
    if out.shape[0] == channels:
        out = out.T
    elif out.shape[1] != channels:
        raise ValueError(f"output shape {np.asarray(output).shape} does not match 4+{num_classes} channels")
    cls_scores = out[:, [4 + i for i in class_indices]]
    best = cls_scores.argmax(axis=1)
    scores = cls_scores[np.arange(cls_scores.shape[0]), best]
    keep = np.flatnonzero(scores >= conf_threshold)
    if keep.size == 0:
        empty = np.zeros((0,), dtype=np.float32)
        return np.zeros((0, 4), dtype=np.float32), empty, np.zeros((0,), dtype=np.int64)
    if keep.size > MAX_NMS_CANDIDATES:
        keep = keep[np.argsort(-scores[keep], kind="stable")[:MAX_NMS_CANDIDATES]]
    cx, cy, bw, bh = out[keep, 0], out[keep, 1], out[keep, 2], out[keep, 3]
    xyxy = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1).astype(np.float32)
    return xyxy, scores[keep].astype(np.float32), np.asarray(class_indices)[best[keep]]


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU of xyxy boxes a (N,4) and b (M,4)."""
    if a.size == 0 or b.size == 0:
        return np.zeros((a.shape[0], b.shape[0]), dtype=np.float32)
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = np.clip(a[:, 2] - a[:, 0], 0, None) * np.clip(a[:, 3] - a[:, 1], 0, None)
    area_b = np.clip(b[:, 2] - b[:, 0], 0, None) * np.clip(b[:, 3] - b[:, 1], 0, None)
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0, inter / np.maximum(union, 1e-12), 0.0).astype(np.float32)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Greedy NMS (deterministic: stable sort by score)."""
    order = list(np.argsort(-scores, kind="stable"))
    keep: list[int] = []
    while order:
        i = order.pop(0)
        keep.append(int(i))
        if not order:
            break
        ious = iou_matrix(boxes[i : i + 1], boxes[order])[0]
        order = [j for j, iou in zip(order, ious) if iou <= iou_threshold]
    return keep


def parse_names(raw: str | None) -> dict[int, str] | None:
    """Ultralytics stores names as a Python dict literal; parsed with literal_eval (no code execution)."""
    if not raw:
        return None
    if len(raw) > 100_000:
        return None
    try:
        value = ast.literal_eval(raw)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if isinstance(value, dict) and all(isinstance(k, int) and isinstance(v, str) for k, v in value.items()):
        return dict(value)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return dict(enumerate(value))
    return None


class YoloOnnxDetector:
    def __init__(self, model_path: Path, manifest: ModelManifest, config: PhoneConfig):
        self.model_path = model_path
        self.manifest = manifest
        self.config = config
        self.info: DetectorInfo | None = None
        self._session: Any = None
        self._cv2: Any = None

    # ------------------------------------------------------------------ load
    def load(self) -> DetectorInfo:
        try:
            import cv2  # noqa: PLC0415
            import onnxruntime as ort  # noqa: PLC0415
        except Exception as exc:  # environment without the "cv" extra
            raise DetectorLoadError("runtime_missing", f"onnxruntime/opencv not importable: {type(exc).__name__}") from exc
        self._cv2 = cv2
        cfg = self.config
        options = ort.SessionOptions()
        options.intra_op_num_threads = cfg.intra_op_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3
        if cfg.execution_provider == "cpu":
            providers = ["CPUExecutionProvider"]
        else:
            providers = [p for p in ort.get_available_providers() if p != "AzureExecutionProvider"] or ["CPUExecutionProvider"]
            if "CPUExecutionProvider" not in providers:
                providers.append("CPUExecutionProvider")
        try:
            session = ort.InferenceSession(str(self.model_path), sess_options=options, providers=providers)
        except Exception as exc:
            raise DetectorLoadError("model_invalid", f"onnxruntime cannot load the model: {type(exc).__name__}") from exc

        inputs = session.get_inputs()
        if len(inputs) != 1 or len(inputs[0].shape) != 4:
            raise DetectorLoadError("model_invalid", "expected exactly one NCHW image input")
        shape = inputs[0].shape
        if isinstance(shape[1], int) and shape[1] != 3:
            raise DetectorLoadError("model_invalid", f"expected 3 input channels, got {shape[1]}")
        static = (int(shape[2]), int(shape[3])) if isinstance(shape[2], int) and isinstance(shape[3], int) else None

        meta = session.get_modelmeta().custom_metadata_map or {}
        if meta.get("task") not in (None, "detect"):
            raise DetectorLoadError("model_invalid", f"model task is {meta.get('task')!r}, expected 'detect'")
        model_names = parse_names(meta.get("names"))
        manifest_names = {int(k): v for k, v in (self.manifest.class_names or {}).items()} or None
        if model_names is not None and manifest_names is not None and model_names != manifest_names:
            raise DetectorLoadError("model_invalid", "class names in the model differ from the manifest")
        names = model_names or manifest_names
        if not names:
            raise DetectorLoadError("model_invalid", "no class names in the model metadata or manifest")
        phone = {i: n for i, n in sorted(names.items()) if n in cfg.phone_class_names}
        if not phone:
            raise DetectorLoadError("model_invalid", f"model has no class named {list(cfg.phone_class_names)}")

        self._session = session
        self.info = DetectorInfo(
            input_name=inputs[0].name,
            class_names=names,
            phone_classes=phone,
            provider=session.get_providers()[0],
            intra_op_threads=cfg.intra_op_threads,
            static_input=static,
            input_size=static[1] if static else cfg.input_size,
            rect=False if static else cfg.rect,
            model_meta={k: str(meta[k])[:64] for k in ("version", "date", "license", "imgsz", "author") if k in meta},
        )
        # Warm-up also validates the output layout against the number of classes.
        warm = np.full((480, 640, 3), PAD_VALUE, dtype=np.uint8)
        warm.flags.writeable = False
        warmup_ms = None
        try:
            for i in range(max(1, cfg.warmup_runs)):
                t0 = time.perf_counter()
                self.detect(warm)
                if i == 0:
                    warmup_ms = (time.perf_counter() - t0) * 1000.0
        except Exception as exc:
            self._session = None
            raise DetectorLoadError("model_invalid", f"warm-up inference failed: {type(exc).__name__}: {exc}"[:300]) from exc
        self.info = replace(self.info, warmup_ms=warmup_ms)
        return self.info

    # ---------------------------------------------------------------- detect
    def detect(self, image_bgr: np.ndarray) -> tuple[list[RawDetection], dict[str, float]]:
        if self._session is None or self.info is None:
            raise RuntimeError("detector not loaded")
        info, cfg = self.info, self.config
        h, w = image_bgr.shape[:2]
        t0 = time.perf_counter()
        lb = letterbox_params(h, w, info.input_size, info.rect, info.static_input)
        tensor = preprocess(image_bgr, lb, self._cv2)
        t1 = time.perf_counter()
        output = self._session.run(None, {info.input_name: tensor})[0]
        t2 = time.perf_counter()
        boxes, scores, classes = decode(output, len(info.class_names), list(info.phone_classes), cfg.conf_threshold)
        detections: list[RawDetection] = []
        if scores.size:
            for i in nms(boxes, scores, cfg.nms_iou):
                x1, y1, x2, y2 = boxes[i]
                nx1 = float(np.clip((x1 - lb.left) / lb.scale / w, 0.0, 1.0))
                ny1 = float(np.clip((y1 - lb.top) / lb.scale / h, 0.0, 1.0))
                nx2 = float(np.clip((x2 - lb.left) / lb.scale / w, 0.0, 1.0))
                ny2 = float(np.clip((y2 - lb.top) / lb.scale / h, 0.0, 1.0))
                det = RawDetection(nx1, ny1, nx2, ny2, float(min(1.0, max(0.0, scores[i]))), int(classes[i]), info.class_names[int(classes[i])])
                if det.area < cfg.min_box_area:
                    continue
                detections.append(det)
                if len(detections) >= cfg.max_detections:
                    break
        t3 = time.perf_counter()
        return detections, {"pre_ms": (t1 - t0) * 1e3, "infer_ms": (t2 - t1) * 1e3, "post_ms": (t3 - t2) * 1e3}

    def close(self) -> None:
        self._session = None
