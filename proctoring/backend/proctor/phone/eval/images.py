"""Image-level check of the phone DETECTOR on labelled still images (YOLO txt labels).

    python -m proctor.phone.eval.images --images <dir> --labels <dir> --out <report.json> [options]

* Uses only the verified local model (manifest + sha256); reads image FILES with cv2.imread — never a
  camera or a video (frames/clips belong to A02's replay path).
* Labels: one "<class> <cx> <cy> <w> <h>" line per object, normalized. The phone label class defaults to
  the model's own index of "cell phone", i.e. the labels must use the same class order (COCO-80).
* Reports instance-level TP/FP/FN, precision/recall per confidence threshold, image-level hit rates,
  false-positive rates on negative and "hard negative" images (remote/book/laptop/... but no phone),
  recall by object size and AP50 — always with denominators.

These numbers describe generic photos (e.g. COCO val2017). They are NOT the exam-webcam accuracy and
say nothing about phone_raised / possible_screen_capture (see EVAL_PLAN.md for clip-level evaluation).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from proctor.settings import Settings

from ..config import PhoneConfig
from ..detector import YoloOnnxDetector
from ..manifest import load_manifest, verify_model_file
from .metrics import Box, average_precision, match_boxes, ratio

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}
SIZE_BUCKETS = (("small", 0.0, 0.002), ("medium", 0.002, 0.02), ("large", 0.02, 1.01))
DEFAULT_HARD_NEGATIVES = "remote,book,laptop,mouse,keyboard,tv,clock,scissors,toothbrush"


def read_labels(path: Path) -> list[tuple[int, Box]]:
    items: list[tuple[int, Box]] = []
    if not path.is_file():
        return items
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        cls = int(float(parts[0]))
        cx, cy, w, h = (float(v) for v in parts[1:5])
        items.append((cls, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)))
    return items


def evaluate(args: argparse.Namespace) -> dict:
    import cv2  # noqa: PLC0415

    models_dir = args.models_dir or Settings.from_env().models_dir
    manifest = load_manifest()
    check = verify_model_file(models_dir, manifest)
    if not check.ok:
        raise SystemExit(f"[{check.code}] {check.message}")
    cfg = replace(PhoneConfig(), conf_threshold=args.floor_conf, input_size=args.input_size, rect=not args.square)
    detector = YoloOnnxDetector(check.path, manifest, cfg)  # type: ignore[arg-type]
    info = detector.load()
    names_to_idx = {n: i for i, n in info.class_names.items()}
    phone_cls = args.label_phone_class if args.label_phone_class is not None else next(iter(info.phone_classes))
    hard_cls = {names_to_idx[n] for n in args.hard_negative_names.split(",") if n in names_to_idx}
    thresholds = sorted({float(t) for t in args.thresholds.split(",")} | {PhoneConfig().conf_threshold})

    images = sorted(p for p in Path(args.images).iterdir() if p.suffix.lower() in IMAGE_EXT)
    records = []  # per image: kind, gts, preds
    infer_ms: list[float] = []
    unreadable = 0
    for path in images:
        labels = read_labels(Path(args.labels) / f"{path.stem}.txt")
        gts = [b for c, b in labels if c == phone_cls]
        classes = {c for c, _ in labels}
        kind = "phone" if gts else ("hard_negative" if classes & hard_cls else "negative")
        if args.skip_plain_negatives and kind == "negative":
            continue
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            unreadable += 1
            continue
        image.flags.writeable = False
        dets, timing = detector.detect(image)
        infer_ms.append(timing["infer_ms"])
        preds = [((d.x_min, d.y_min, d.x_max, d.y_max), d.confidence) for d in dets]
        hard_present = sorted(info.class_names[c] for c in classes & hard_cls)
        records.append({"kind": kind, "gts": gts, "preds": preds, "hard": hard_present})
        if args.limit and len(records) >= args.limit:
            break

    n_gt = sum(len(r["gts"]) for r in records)
    by_kind = {k: sum(1 for r in records if r["kind"] == k) for k in ("phone", "hard_negative", "negative")}
    scored: list[tuple[float, bool]] = []
    for r in records:
        tp, _ = match_boxes(r["preds"], r["gts"], args.iou)
        scored.extend((p[1], t) for p, t in zip(r["preds"], tp))

    rows = []
    default_conf = PhoneConfig().conf_threshold
    size_recall = None
    for thr in thresholds:
        tp = fp = fn = img_hit = neg_fp = hard_fp = 0
        hard_fp_by_class: dict[str, int] = {}
        size = {name: [0, 0] for name, _, _ in SIZE_BUCKETS}
        for r in records:
            preds = [p for p in r["preds"] if p[1] >= thr]
            pred_tp, gt_hit = match_boxes(preds, r["gts"], args.iou)
            tp += sum(pred_tp)
            fp += len(preds) - sum(pred_tp)
            fn += len(r["gts"]) - sum(gt_hit)
            if r["kind"] == "phone" and any(gt_hit):
                img_hit += 1
            if r["kind"] != "phone" and preds:
                neg_fp += 1
                if r["kind"] == "hard_negative":
                    hard_fp += 1
                    for name in r["hard"]:
                        hard_fp_by_class[name] = hard_fp_by_class.get(name, 0) + 1
            for gt, hit in zip(r["gts"], gt_hit):
                area = (gt[2] - gt[0]) * (gt[3] - gt[1])
                for name, lo, hi in SIZE_BUCKETS:
                    if lo <= area < hi:
                        size[name][0] += 1
                        size[name][1] += int(hit)
        negatives = by_kind["hard_negative"] + by_kind["negative"]
        rows.append(
            {
                "conf": thr,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "precision": ratio(tp, tp + fp),
                "recall": ratio(tp, tp + fn),
                "phone_images_with_a_hit": f"{img_hit}/{by_kind['phone']}",
                "negative_images_with_fp": f"{neg_fp}/{negatives}",
                "hard_negative_images_with_fp": f"{hard_fp}/{by_kind['hard_negative']}",
                "hard_negative_fp_images_by_present_class": dict(sorted(hard_fp_by_class.items())),
                "recall_by_size": {k: f"{v[1]}/{v[0]}" for k, v in size.items()},
            }
        )
        if thr == default_conf:
            size_recall = rows[-1]["recall_by_size"]

    return {
        "tool": "proctor.phone.eval.images",
        "dataset": args.dataset_name,
        "images_considered": len(records),
        "images_unreadable": unreadable,
        "images_by_kind": by_kind,
        "phone_instances": n_gt,
        "label_phone_class": phone_cls,
        "hard_negative_classes": sorted(info.class_names[c] for c in hard_cls),
        "iou_threshold": args.iou,
        "floor_conf": args.floor_conf,
        "default_conf_threshold": default_conf,
        "ap50": average_precision(scored, n_gt),
        "thresholds": rows,
        "recall_by_size_at_default_conf": size_recall,
        "size_buckets_normalized_area": {name: [lo, hi] for name, lo, hi in SIZE_BUCKETS},
        "model": {
            "model_id": manifest.model_id,
            "sha256": check.actual_sha256,
            "provider": info.provider,
            "intra_op_threads": info.intra_op_threads,
            "input_size": info.input_size,
            "rect": info.rect,
        },
        "infer_ms_p50": round(float(np.percentile(infer_ms, 50)), 1) if infer_ms else None,
        "infer_ms_p95": round(float(np.percentile(infer_ms, 95)), 1) if infer_ms else None,
        "caveats": [
            "Generic still photos, not the exam webcam view; not a measurement of exam accuracy.",
            "Detector only: tracking, phone_raised and possible_screen_capture are not evaluated here.",
            "A matched box means 'a phone-shaped object was found', never 'something was photographed'.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m proctor.phone.eval.images", description=__doc__.splitlines()[0])
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dataset-name", default="unnamed")
    ap.add_argument("--models-dir", type=Path, default=None)
    ap.add_argument("--label-phone-class", type=int, default=None)
    ap.add_argument("--hard-negative-names", default=DEFAULT_HARD_NEGATIVES)
    ap.add_argument("--floor-conf", type=float, default=0.05)
    ap.add_argument("--thresholds", default="0.1,0.15,0.2,0.25,0.3,0.4,0.5")
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--input-size", type=int, default=640)
    ap.add_argument("--square", action="store_true", help="square letterbox instead of rect")
    ap.add_argument("--skip-plain-negatives", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    started = time.perf_counter()
    report = evaluate(args)
    report["wall_s"] = round(time.perf_counter() - started, 1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("dataset", "images_by_kind", "phone_instances", "ap50", "infer_ms_p50")}, ensure_ascii=False))
    for row in report["thresholds"]:
        print(
            f"conf>={row['conf']:.2f}  P={row['precision']}  R={row['recall']}  (tp={row['tp']} fp={row['fp']} fn={row['fn']})  "
            f"phone imgs hit {row['phone_images_with_a_hit']}  neg imgs w/ FP {row['negative_images_with_fp']}  "
            f"hard-neg imgs w/ FP {row['hard_negative_images_with_fp']}  size {row['recall_by_size']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
