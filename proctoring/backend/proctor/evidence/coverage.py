"""Observed monitoring time and coverage gaps (owner: A08).

Coverage is tracked per camera analyzer ("phone", "attention") as segments of consecutive
observations on the session timeline (t_session_ms):
  * cls "determined"   - status ok/degraded (the analyzer produced a value);
  * cls "undetermined" - status unknown/error (it ran but could not tell).
A hole longer than coverage_gap_ms between observations ends a segment. Time without determined
observations is "unknown", never "no violation".
"""

from __future__ import annotations

from dataclasses import dataclass

Interval = tuple[float, float]

COVERED_COMPONENTS = ("phone", "attention")
DETERMINED = "determined"
UNDETERMINED = "undetermined"


@dataclass
class Segment:
    component: str
    seg_no: int
    cls: str
    t_start_ms: float
    t_end_ms: float
    n: int = 1
    dirty: bool = True


class CoverageTracker:
    """Per-session, in-memory current segments; closed/dirty ones are flushed by the store."""

    def __init__(self, gap_ms: float, max_segments: int, next_seg_no: dict[str, int] | None = None):
        self.gap_ms = gap_ms
        self.max_segments = max_segments
        self.current: dict[str, Segment] = {}
        self.pending: list[Segment] = []  # closed, not yet flushed
        self.next_seg_no: dict[str, int] = dict(next_seg_no or {})
        self.overflow = False
        self.last_flush_t: float | None = None

    def observe(self, component: str, determined: bool, t_ms: float) -> None:
        cls = DETERMINED if determined else UNDETERMINED
        seg = self.current.get(component)
        if seg is not None and seg.cls == cls and t_ms - seg.t_end_ms <= self.gap_ms:
            if t_ms > seg.t_end_ms:
                seg.t_end_ms = t_ms
            seg.n += 1
            seg.dirty = True
            return
        if seg is not None:
            if seg.cls != cls and t_ms - seg.t_end_ms <= self.gap_ms:
                seg.t_end_ms = max(seg.t_end_ms, t_ms)  # class switch: no hole between the two
            self.pending.append(seg)
        seg_no = self.next_seg_no.get(component, 0)
        if seg_no >= self.max_segments:
            self.overflow = True
            self.current.pop(component, None)
            return
        self.next_seg_no[component] = seg_no + 1
        self.current[component] = Segment(component, seg_no, cls, t_ms, t_ms)

    def take_dirty(self) -> list[Segment]:
        out = self.pending + [s for s in self.current.values() if s.dirty]
        self.pending = []
        for s in out:
            s.dirty = False
        return out

    def close_all(self) -> None:
        self.pending.extend(self.current.values())
        for s in self.current.values():
            s.dirty = True
        self.current = {}


# ---------------------------------------------------------------------------
# Interval arithmetic on sorted, merged lists of [start, end)
# ---------------------------------------------------------------------------


def merge(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for start, end in sorted((a, b) for a, b in intervals if b > a):
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        end = min(a[i][1], b[j][1])
        if end > start:
            out.append((start, end))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for start, end in a:
        cur = start
        for bs, be in b:
            if be <= cur or bs >= end:
                continue
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
            if cur >= end:
                break
        if cur < end:
            out.append((cur, end))
    return out


def total(a: list[Interval]) -> float:
    return sum(end - start for start, end in a)
