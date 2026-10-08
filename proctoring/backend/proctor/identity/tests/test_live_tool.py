"""A13 LIVE tool summary (pure function; the tool itself needs a camera or a replay clip)."""

from __future__ import annotations

from proctor.identity.live import summarize


def _r(t, state, sim=None, enrolled=True):
    return {"t_s": t, "same_person": state, "similarity": sim, "enrolled": enrolled, "face_score": 0.9, "reasons": []}


def test_summary_splits_at_swap_and_measures_absent_run():
    rows = [_r(0.0, "unknown", enrolled=False), _r(2.5, "present", 0.9), _r(3.5, "present", 0.8),
            _r(20.1, "absent", 0.1), _r(21.1, "absent", 0.05), _r(22.1, "unknown"), _r(23.1, "absent", 0.2),
            _r(24.1, "absent", 0.0), _r(25.1, "absent", -0.1)]
    s = summarize(rows, swap_at_s=20.0)
    assert s["enrolled_at_s"] == 2.5
    assert s["longest_absent_run_s"] == 3.0  # 23.1..25.1 + one interval; "unknown" breaks the run
    assert s["before_swap"]["present"] == 2 and s["before_swap"]["similarity_max"] == 0.9
    assert s["after_swap"]["absent"] == 5 and s["after_swap"]["unknown"] == 1
    assert s["after_swap"]["similarity_min"] == -0.1
    assert "records" not in s  # summary holds numbers only
