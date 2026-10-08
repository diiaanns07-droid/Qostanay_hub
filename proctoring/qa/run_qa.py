#!/usr/bin/env python3
"""Run the A09 QA suite and write a result record bound to the exact tested SHA (owner: A09).

    python qa/run_qa.py                      # A09 suite only
    python qa/run_qa.py --with-baseline      # + A01 checks (pytest, generate --check, smoke)
    python qa/run_qa.py --label candidate    # results land in qa/results/<date>_<label>_<sha12>/

Writes summary.json + summary.md + pytest.txt (+ junit.xml). Statuses are PASS / FAIL / XFAIL
(known, tracked bug or observation) / SKIP / NOT_RUN. Nothing is converted into PASS.
Logs contain no token (tests redact it) and no camera frames.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

QA = Path(__file__).resolve().parent
ROOT = QA.parent  # proctoring/


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout.strip()


def _versions() -> dict[str, str]:
    out = {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine()}
    for mod in ("fastapi", "uvicorn", "websockets", "pydantic", "numpy", "cv2", "mediapipe", "onnxruntime", "httpx", "pytest"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "?")
        except Exception as exc:
            out[mod] = f"not importable ({type(exc).__name__})"
    return out


def _junit_rows(path: Path) -> list[dict[str, str]]:
    rows = []
    for case in ET.parse(path).getroot().iter("testcase"):
        name = f"{case.get('classname', '').rsplit('.', 1)[-1]}::{case.get('name')}"
        status, detail = "PASS", ""
        for child in case:
            if child.tag in ("failure", "error"):
                status, detail = "FAIL", (child.get("message") or "")[:300]
            elif child.tag == "skipped":
                kind = child.get("type", "")
                msg = child.get("message") or ""
                status = "XFAIL" if "xfail" in kind or msg.startswith("QA-") or "xfail" in msg.lower() else "SKIP"
                detail = msg[:300]
        rows.append({"test": name, "status": status, "detail": detail})
    return rows


def _run(cmd: list[str], log: Path, timeout: int) -> int:
    with log.open("w", encoding="utf-8") as fh:
        fh.write("$ " + " ".join(cmd) + "\n")
        fh.flush()
        try:
            return subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            fh.write(f"\nTIMEOUT after {timeout}s\n")
            return 124


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="bootstrap")
    ap.add_argument("--with-baseline", action="store_true")
    ap.add_argument("-k", default=None, help="pytest -k expression (partial runs are labelled partial)")
    args = ap.parse_args()

    sha = _git("rev-parse", "HEAD")
    # product = everything except the A09 harness paths; the harness version is recorded separately
    dirty = bool(_git("status", "--porcelain", "--", ".", ":!qa", ":!packaging", ":!handoffs/A09"))
    harness_dirty = bool(_git("status", "--porcelain", "--", "qa", "packaging"))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    out = QA / "results" / f"{stamp}_{args.label}_{sha[:12]}"
    out.mkdir(parents=True, exist_ok=True)

    summary: dict = {
        "tested_sha": sha,
        "product_tree_dirty": dirty,
        "harness_uncommitted": harness_dirty,
        "label": args.label,
        "partial": bool(args.k),
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "environment": _versions(),
        "scope_note": "SYNTHETIC/bootstrap pipeline unless stated; not CV accuracy, not Windows environment protection",
        "suites": {},
    }

    if args.with_baseline:
        for name, cmd, to in (
            ("a01_pytest", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], 900),
            ("a01_generate_check", [sys.executable, "contracts/tools/generate.py", "--check"], 120),
            ("a01_smoke", [sys.executable, "-m", "proctor", "smoke"], 300),
            ("ownership_self_test", [sys.executable, "coordination/verify_ownership.py", "--self-test"], 60),
        ):
            rc = _run(cmd, out / f"{name}.txt", to)
            summary["suites"][name] = {"status": "PASS" if rc == 0 else "FAIL", "exit_code": rc, "log": f"{name}.txt"}

    cmd = [sys.executable, "-m", "pytest", "qa/tests", "-q", "-rA", "-p", "no:cacheprovider", f"--junitxml={out / 'junit.xml'}"]
    if args.k:
        cmd += ["-k", args.k]
    rc = _run(cmd, out / "pytest.txt", 3600)
    rows = _junit_rows(out / "junit.xml") if (out / "junit.xml").exists() else []
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary["suites"]["a09_qa"] = {"status": "PASS" if rc == 0 else "FAIL", "exit_code": rc, "counts": counts, "log": "pytest.txt"}
    summary["known_issues"] = sorted({m.group(0) for r in rows if r["status"] == "XFAIL" for m in [re.match(r"QA-(BUG|OBS)-\d+", r["detail"])] if m})
    summary["failures"] = [r for r in rows if r["status"] == "FAIL"]
    # tokens are 64 hex chars: any such string in the written logs is treated as a leak
    summary["hex64_strings_in_logs"] = [
        p.name for p in sorted(out.iterdir()) if p.suffix in (".txt", ".xml") and re.search(r"\b[0-9a-f]{64}\b", p.read_text(encoding="utf-8", errors="replace"))
    ]
    summary["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [f"# QA run {args.label} @ `{sha}`{' (DIRTY product tree)' if dirty else ''}", "", f"Scope: {summary['scope_note']}.", ""]
    md += ["| Suite | Status | Details |", "|---|---|---|"]
    for name, s in summary["suites"].items():
        md.append(f"| {name} | {s['status']} | {json.dumps(s.get('counts', {'exit_code': s['exit_code']}))} |")
    md += ["", "Known issues (XFAIL, tracked in qa/BUGS.md): " + (", ".join(summary["known_issues"]) or "none")]
    md += [f"Token-like strings in logs: {summary['hex64_strings_in_logs'] or 'none'}", ""]
    if summary["failures"]:
        md += ["## FAIL", *[f"- `{f['test']}` — {f['detail']}" for f in summary["failures"]], ""]
    md += ["## Environment", *[f"- {k}: {v}" for k, v in summary["environment"].items()]]
    (out / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"results: {out.relative_to(ROOT)}  a09_qa={summary['suites']['a09_qa']['status']} {counts}")
    ok = all(s["status"] == "PASS" for s in summary["suites"].values()) and not summary["hex64_strings_in_logs"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
