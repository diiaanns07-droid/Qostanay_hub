#!/usr/bin/env python3
"""Check that a branch touches only the classroom paths of one T-role (owner: T01). Stdlib only.

    python proctoring/classroom/coordination/verify_ownership.py --agent T03 --base <T01_BASELINE_SHA> [--head HEAD]
    python proctoring/classroom/coordination/verify_ownership.py --self-test

Ownership = longest matching path prefix across all T-roles (T01 owns proctoring/classroom/ except the listed
feature carve-outs). Exit 0 = PASS.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

MAP = Path(__file__).resolve().parent / "OWNERSHIP.json"


def load() -> dict:
    return json.loads(MAP.read_text(encoding="utf-8"))


def prefixes(data: dict) -> list[tuple[str, str]]:
    return [(p, agent) for agent, spec in data["agents"].items() for p in spec["paths"]]


def owner_of(path: str, data: dict) -> str | None:
    best: tuple[int, str] | None = None
    for prefix, agent in prefixes(data):
        if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), agent)
    return best[1] if best else None


def self_test(data: dict) -> int:
    problems: list[str] = []
    flat = prefixes(data)
    seen: dict[str, str] = {}
    for prefix, agent in flat:
        if prefix in seen:
            problems.append(f"duplicate prefix {prefix}: {seen[prefix]} and {agent}")
        seen[prefix] = agent
    for prefix, agent in flat:
        for other, other_agent in flat:
            if other != prefix and other.endswith("/") and prefix.startswith(other) and other_agent != "T01":
                problems.append(f"{agent} {prefix} is nested inside {other_agent} {other}; only carve-outs inside T01's tree are allowed")
    for agent, spec in data["agents"].items():
        if agent != "T01" and not any(p.startswith(f"proctoring/handoffs/{agent}/") for p in spec["paths"]):
            problems.append(f"{agent} has no handoff path")
    for line in problems:
        print(line)
    print("CLASSROOM OWNERSHIP self-test:", "FAIL" if problems else f"PASS ({len(flat)} prefixes, {len(data['agents'])} roles)")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--agent")
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    data = load()
    if args.self_test:
        return self_test(data)
    if not args.agent or not args.base or args.agent not in data["agents"]:
        ap.error("--agent Txx and --base <sha> are required")
    changed = subprocess.run(["git", "diff", "--name-only", f"{args.base}...{args.head}"], capture_output=True, text=True, check=True).stdout.split()
    bad = [(p, owner_of(p, data) or "<not classroom>") for p in changed if owner_of(p, data) != args.agent]
    for path, owner in bad:
        print(f"NOT OWNED by {args.agent}: {path} (owner: {owner})")
    print(f"{len(changed)} changed file(s), {len(bad)} outside {args.agent}'s classroom paths: {'FAIL' if bad else 'PASS'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
