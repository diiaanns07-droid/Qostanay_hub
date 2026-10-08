"""A09 independent QA harness for Qorgau Exam (owner: A09).

Black-box by design: talks to a REAL `python -m proctor serve --token-stdin` process over the
public HTTP/WS API v1 and validates every payload against the frozen contract (JSON Schema
`$defs` + Pydantic models from proctor_contracts.v1). It never imports backend internals.

Nothing produced by this harness on synthetic data is a CV-quality or Windows-security result.
"""

PROCTORING_ROOT_MARKER = "coordination/OWNERSHIP.json"
