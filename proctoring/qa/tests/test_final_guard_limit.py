"""No real hook: prove the QA adapter caps enforce without changing the product."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("arguments", [
    ["--parent-pid", "123", "--mode", "enforce", "--max-minutes", "240"],
    ["--parent-pid", "123", "--mode", "dry-run"],
    ["--self-check"],
])
def test_qa_adapter_preserves_arguments_and_caps_duration(tmp_path, arguments):
    fake = tmp_path / "guard.py"
    fake.write_text("import json,sys; print(json.dumps(sys.argv[1:]))", encoding="utf-8")
    adapter = Path(__file__).resolve().parents[1] / "tools/final_guard_limit.py"
    result = subprocess.run([sys.executable, str(adapter), *arguments],
        env={**os.environ, "QORGAU_QA_REAL_HELPER": str(fake)},
        capture_output=True, text=True, timeout=5, check=True)
    forwarded = json.loads(result.stdout)
    assert forwarded[:len(arguments)] == arguments
    assert forwarded == arguments if "--self-check" in arguments else forwarded[-2:] == ["--max-minutes", "2"]
