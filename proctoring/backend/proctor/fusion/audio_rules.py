"""Audio and headphones episodes (owner: A05) — on plain values until a contract exists.

There is no contract observation/rule for audio or headphones in qorgau.v1 1.0.0, so these rules
take simple samples and return ``AuxEpisode`` objects with string rule ids. They are pure (no clock,
no I/O) and feed ``proctor.fusion.zones.assess_session_zone`` directly (same field names as Incident).
When A01 publishes contract types they are wired into the engine.

* ``background_speech``: ``voice_like`` holds for >= 4 s within a 6 s window -> low; when it is the
  3rd (or later) such episode within 5 minutes -> medium. «Возможная речь или фоновый разговор — 12:05, 6 с».
* ``headphones_visible``: visible continuously for >= 3 s -> medium. «Видны наушники — 03:10».
* A sample with value ``None`` (no device, degraded, unknown) is neither evidence nor "all clear":
  it is returned as uncovered time (-> coverage / grey zone), never as "no episode".

Samples: ``(t_ms, value)`` sorted by time on the session timeline; one sample stands for at most
``sample_hold_ms`` (so a silent hole in the stream is uncovered time, not evidence).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .zones import mmss

AUX_RULE_VERSION = "a05-aux-1.0.0"


@dataclass(frozen=True)
class AuxConfig:
    speech_window_ms: float = 6000.0
    speech_min_ms: float = 4000.0
    speech_repeat_count: int = 3
    speech_repeat_window_ms: float = 300000.0
    headphones_min_ms: float = 3000.0
    sample_hold_ms: float = 600.0


@dataclass(frozen=True)
class AuxEpisode:
    rule_id: str
    priority: str
    t_start_ms: float
    t_end_ms: float
    summary_ru: str
    facts: dict[str, float] = field(default_factory=dict)
    rule_version: str = AUX_RULE_VERSION


def _held(samples: Sequence[tuple[float, bool | None]], hold: float) -> list[tuple[float, float, bool | None]]:
    """[(t0, t1, value)]: each sample lasts until the next one, at most ``hold``; the rest is None."""
    out: list[tuple[float, float, bool | None]] = []
    for i, (t, v) in enumerate(samples):
        nxt = samples[i + 1][0] if i + 1 < len(samples) else t + hold
        end = min(nxt, t + hold)
        if end > t:
            out.append((t, end, v))
        if nxt > end:
            out.append((end, nxt, None))
    return out


def uncovered_ms(samples: Iterable[tuple[float, bool | None]], cfg: AuxConfig | None = None) -> float:
    """Time without a determined value (device missing/degraded/unknown, or stream holes)."""
    cfg = cfg or AuxConfig()
    seq = sorted(samples)
    return sum(t1 - t0 for t0, t1, v in _held(seq, cfg.sample_hold_ms) if v is None)


def background_speech(samples: Iterable[tuple[float, bool | None]], cfg: AuxConfig | None = None, exam_start_ms: float = 0.0) -> list[AuxEpisode]:
    cfg = cfg or AuxConfig()
    seq = sorted(samples)
    spans = [(t0, t1) for t0, t1, v in _held(seq, cfg.sample_hold_ms) if v is True]
    # voice time inside the window (t - W, t] for every span end; an episode covers the union of
    # windows where voice >= speech_min_ms
    episodes: list[tuple[float, float]] = []
    for _, end in spans:
        w0 = end - cfg.speech_window_ms
        voice = sum(max(0.0, min(b, end) - max(a, w0)) for a, b in spans if b > w0 and a < end)
        if voice + 1e-6 >= cfg.speech_min_ms:
            first = min(a for a, b in spans if b > w0 and a < end)
            start = max(first, w0)
            if episodes and start <= episodes[-1][1]:
                episodes[-1] = (episodes[-1][0], end)
            else:
                episodes.append((start, end))
    out: list[AuxEpisode] = []
    for i, (a, b) in enumerate(episodes):
        recent = sum(1 for a2, _ in episodes[: i + 1] if a - a2 <= cfg.speech_repeat_window_ms)
        prio = "medium" if recent >= cfg.speech_repeat_count else "low"
        dur = int(round((b - a) / 1000.0))
        out.append(AuxEpisode(
            "background_speech", prio, a, b,
            f"Возможная речь или фоновый разговор — {mmss(a - exam_start_ms)}, {dur} с",
            {"duration_ms": b - a, "episodes_in_window": float(recent)},
        ))
    return out


def headphones_visible(samples: Iterable[tuple[float, bool | None]], cfg: AuxConfig | None = None, exam_start_ms: float = 0.0) -> list[AuxEpisode]:
    cfg = cfg or AuxConfig()
    seq = sorted(samples)
    out: list[AuxEpisode] = []
    run: list[float] | None = None  # [start, end]
    for t0, t1, v in _held(seq, cfg.sample_hold_ms) + [(float("inf"), float("inf"), False)]:
        if v is True:
            if run is not None and t0 <= run[1]:
                run[1] = t1
            else:
                run = [t0, t1]
            continue
        if run is not None and run[1] - run[0] >= cfg.headphones_min_ms:
            out.append(AuxEpisode(
                "headphones_visible", "medium", run[0], run[1],
                f"Видны наушники — {mmss(run[0] - exam_start_ms)}", {"duration_ms": run[1] - run[0]},
            ))
        run = None  # None (unknown) also breaks the run: never extended through missing data
    return out
