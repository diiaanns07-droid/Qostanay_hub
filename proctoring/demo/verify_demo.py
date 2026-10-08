"""Validate A10 content against the frozen public contract, without starting a camera."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "contracts" / "python"))
from proctor_contracts.v1 import ExamDefinition


def verify(path: Path) -> dict:
    exam = ExamDefinition.model_validate_json(path.read_text(encoding="utf-8"))
    if not exam.is_demo:
        raise ValueError("Demo content must explicitly set is_demo=true")
    ids = [q.question_id for q in exam.questions]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate question ids")
    for q in exam.questions:
        option_ids = [o.option_id for o in q.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError(f"Duplicate option ids: {q.question_id}")
        if q.kind.value.endswith("choice") and len(q.options) < 2:
            raise ValueError(f"Choice question needs at least two options: {q.question_id}")
        if q.kind.value == "short_text" and (q.options or q.max_length is None):
            raise ValueError(f"Short text question needs a limit and no options: {q.question_id}")
    return {"status": "PASS", "contract": "qorgau.v1", "exam_id": exam.exam_id,
            "questions": len(ids), "scope": "content validation only"}


if __name__ == "__main__":
    print(json.dumps(verify(ROOT / "demo" / "exams" / "demo_exam.json"), ensure_ascii=False))
