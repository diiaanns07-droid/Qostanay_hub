"""Review-only objects (COCO book / laptop / tv) from the same YOLO inference: detector + analyzer.

Objects are plain detections with class_name; the phone tracker and phone signals never see them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from proctor.phone.analyzer import PhoneAnalyzer
from proctor.phone.config import PhoneConfig
from proctor.phone.tests.helpers import FakeDetector, box, make_frame
from proctor.phone.tests.test_analyzer_contract import IMG, loaded, run_one, signal
from proctor.phone.tests.test_detector_unit import COCO, const_output, make_detector
from proctor_contracts.v1 import PhoneSignalName, SignalState

OBJECTS = {i: n for i, n in COCO.items() if n in ("book", "laptop", "tv")}


def test_config_defaults_and_validation():
    cfg = PhoneConfig()
    assert cfg.object_class_names == ("book", "laptop", "tv") and cfg.object_conf_threshold == 0.5
    with pytest.raises(ValueError):
        PhoneConfig(object_class_names=("cell phone",))
    with pytest.raises(ValueError):
        PhoneConfig(object_conf_threshold=0.0)
    assert PhoneConfig(object_conf_threshold=0.6).config_version != cfg.config_version


def test_detect_with_objects_threshold_per_class_nms_and_phones_unchanged():
    assert set(OBJECTS.values()) == {"book", "laptop", "tv"}
    book, laptop, tv = (next(i for i, n in COCO.items() if n == x) for x in ("book", "laptop", "tv"))
    rows = [
        (100, 100, 200, 300, {67: 0.30}),  # phone (phone floor 0.20)
        (90, 90, 210, 310, {book: 0.60}),  # book under the phone: per-class NMS keeps both
        (400, 100, 600, 250, {laptop: 0.45}),  # below the object threshold 0.5
        (300, 300, 500, 450, {tv: 0.70}),
    ]
    det, _ = make_detector(const_output(rows))
    det.info = replace(det.info, object_classes=OBJECTS)
    phones, objects, timings = det.detect_with_objects(IMG)
    assert [(d.class_name, round(d.confidence, 2)) for d in phones] == [("cell phone", 0.3)]
    assert sorted((d.class_name, round(d.confidence, 2)) for d in objects) == [("book", 0.6), ("tv", 0.7)]
    assert "infer_ms" in timings
    only_phones, _ = det.detect(IMG)  # the old API keeps returning phones only
    assert [d.class_name for d in only_phones] == ["cell phone"]


def test_objects_off_when_not_configured():
    det, _ = make_detector(const_output([(90, 90, 210, 310, {next(i for i, n in COCO.items() if n == "book"): 0.9})]))
    phones, objects, _ = det.detect_with_objects(IMG)  # make_detector leaves object_classes empty
    assert phones == [] and objects == []


@dataclass
class ObjectsDetector(FakeDetector):
    objects: list | None = None

    def detect_with_objects(self, image_bgr):
        phones, timings = self.detect(image_bgr)
        return phones, list(self.objects or []), timings


def test_analyzer_reports_objects_as_plain_detections_never_as_phone(settings):
    book = box(0.5, 0.5, conf=0.8, cls=73, name="book")
    analyzer = loaded(settings, ObjectsDetector(objects=[book]))
    for i in range(6):
        obs = run_one(analyzer, make_frame(i, image=IMG))
    names = [(d.class_name, d.track_id) for d in obs.detections]
    assert names == [("book", None)]  # no track, no phone
    assert signal(obs, PhoneSignalName.PHONE_VISIBLE).state == SignalState.ABSENT
    assert signal(obs, PhoneSignalName.PHONE_RAISED).state != SignalState.PRESENT


def test_analyzer_phone_and_object_together_and_limit(settings):
    phone = box(0.4, 0.4, conf=0.7)
    objs = [box(0.1 + 0.05 * k, 0.8, conf=0.9, cls=63, name="laptop") for k in range(20)]
    analyzer = loaded(settings, ObjectsDetector(script=lambda i: [phone], objects=objs))
    obs = run_one(analyzer, make_frame(0, image=IMG))
    assert obs.detections[0].class_name == "cell phone" and obs.detections[0].track_id is not None
    assert len(obs.detections) == 16 and all(d.class_name == "laptop" for d in obs.detections[1:])
    assert signal(obs, PhoneSignalName.PHONE_VISIBLE).facts["detections"] == 1  # objects are not phones
