"""A strict XPASS must remain a failed gate, but not be misreported as a product FAIL."""
import importlib.util
from pathlib import Path


def test_junit_distinguishes_strict_xpass_from_failure(tmp_path):
    spec = importlib.util.spec_from_file_location("qa_runner", Path(__file__).parents[1] / "run_qa.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "junit.xml"
    source.write_text('''<testsuite>
      <testcase name="fixed"><failure message="[XPASS(strict)] QA-BUG-001"/></testcase>
      <testcase name="broken"><failure message="actual assertion failure"/></testcase>
      <testcase name="known"><skipped type="pytest.xfail" message="known bug"/></testcase>
      <testcase name="ok"/>
    </testsuite>''', encoding="utf-8")
    assert [r["status"] for r in module._junit_rows(source)] == ["XPASS", "FAIL", "XFAIL", "PASS"]
