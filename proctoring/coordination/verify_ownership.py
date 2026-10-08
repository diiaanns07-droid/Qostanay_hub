#!/usr/bin/env python3
"""Check that a branch only touches the paths owned by one agent (owner: A01).

    python proctoring/coordination/verify_ownership.py --agent A03 --base <BOOTSTRAP_SHA> [--head HEAD]
    python proctoring/coordination/verify_ownership.py --self-test

Exit 0 = every changed path belongs to the agent; 1 = violations listed. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

OWNERSHIP = Path(__file__).resolve().parent / "OWNERSHIP.json"


def load() -> dict:
    return json.loads(OWNERSHIP.read_text(encoding="utf-8"))


def owner_of(path: str, agents: dict) -> list[str]:
    owners = []
    for agent, spec in agents.items():
        for prefix in spec["paths"]:
            if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
                owners.append(agent)
    return owners


def self_test(data: dict) -> int:
    """Every prefix must be owned by exactly one agent (no overlaps)."""
    agents = data["agents"]
    problems = []
    flat = [(a, p) for a, spec in agents.items() for p in spec["paths"]]
    for a1, p1 in flat:
        for a2, p2 in flat:
            if a1 >= a2:
                continue
            if p1 == p2 or (p1.endswith("/") and p2.startswith(p1)) or (p2.endswith("/") and p1.startswith(p2)):
                problems.append(f"overlap: {a1} {p1} <-> {a2} {p2}")
    for line in problems:
        print(line)
    print("OWNERSHIP self-test:", "FAIL" if problems else f"PASS ({len(flat)} path prefixes, {len(agents)} agents)")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agent")
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    data = load()
    if args.self_test:
        return self_test(data)
    if not args.agent or not args.base or args.agent not in data["agents"]:
        ap.error("--agent Axx and --base <sha> are required")
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{args.base}...{args.head}"], capture_output=True, text=True, check=True
    ).stdout.split()
    bad = []
    for path in out:
        owners = owner_of(path, data["agents"])
        if args.agent not in owners:
            bad.append((path, owners or ["<unassigned>"]))
    for path, owners in bad:
        print(f"NOT OWNED by {args.agent}: {path} (owner: {', '.join(owners)})")
    print(f"{len(out)} changed file(s), {len(bad)} outside {args.agent}'s paths: {'FAIL' if bad else 'PASS'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
