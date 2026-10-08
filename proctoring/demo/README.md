# Demonstration package

Owner A10. Baseline: A01 `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`, contract `qorgau.v1`.
Content goes into the existing A01/A07 application. There is no second interface.

The competition regulation gives **3 minutes total for the presentation and demonstration**, then
3 minutes for questions. The primary script is `docs/pitch/DEMO_180_SECONDS.md`; the separate
90-second speech is an alternative format, not an extra 90 seconds to add to the three-minute slot.
Submission is due 8 October 2026 at 23:59 according to the supplied regulation; Demo Day is 16 October.

## Contents

| File | Purpose |
|---|---|
| `exams/demo_exam.json` | Four Russian demo questions, validated against ExamDefinition; A01 settings load this exact path |
| `verify_demo.py` | Contract and content checks without opening the camera |
| `rehearse.py` | Timed synthetic/replay backend rehearsal, exact SHA, labelled results, no native restrictions |
| `replay/README.md` | A02-compatible recording, verification and replay commands; recordings stay outside Git |
| `REQUIREMENT_TO_DEMO.md` | Case requirement to visible demonstration and evidence |
| `OPERATOR_CHECKLIST.md` | Setup, live actions, emergency recovery and submission checklist |
| `results/` | Measured API rehearsal results; each states what was not tested |
| `docs/pitch/` | 180-second script, 90-second speech, jury questions and claim rules |

## Submission on 8 October

The captain extended A10's scope to `proctoring/docs/submission/` explicitly for this delivery.
See `docs/submission/CHECKLIST.md`: 10-slide PDF + editable Markdown, two-page description PDF +
Markdown, 170-second video shot list, recording guidance, a 20:00 fallback and submission by 23:00
Asia/Qyzylorda (official cutoff 23:59). `docs/pitch/PRESENTATION.md` is the slide source.
Zones are a labelled mockup awaiting A01 1.1/A05/A08/A07/A09; no second runtime UI was added.
Actual MP4 recording and full LIVE acceptance remain NOT_RUN. The old v1 API rehearsal does not
validate the new session overview or zone fields.

From `proctoring/`, with the pinned Python environment prepared:

```powershell
.\.venv\Scripts\python.exe demo\verify_demo.py
.\.venv\Scripts\python.exe demo\rehearse.py --mode synthetic --expected-sha FULL_SHA_FROM_A01 --out demo\results\synthetic_rehearsal.json
```

The `--expected-sha` value must be the full candidate SHA actually supplied by A01. A mismatch
stops before spawning the backend. Synthetic rehearsal checks the real backend API using scripted
signals. A PASS does not prove gaze accuracy, phone detection, Windows protection or a rehearsed
human presentation. HTTP 501 exports remain NOT_RUN; they are not counted as an implemented report.

For LIVE, follow the operator checklist in the existing Electron application. For REPLAY, use the
commands under `replay/README.md`. Do not start a new implementation of A02 or merge other branches:
the integration candidate belongs to A01.
