# A10 delivery status

Branch: `codex/proctor-A10`. Contract/baseline: A01
`35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`, `qorgau.v1`.
Previous published A10 checkpoint: `de71342df7ac8758e355009004593ef71ca38690`.
Stage: selection submission draft delivered: PDF presentation, description and video/pitch scripts.
A01 candidate de72905 has now passed the A10 Windows SYNTHETIC rehearsal. Actual MP4 remains pending.

## 8 October submission continuation

The captain explicitly authorized `proctoring/docs/submission/` as an additional A10 path and ordinary
pushes to `codex/proctor-A10`. A01's existing OWNERSHIP.json does not list this new path yet. It is left
unchanged; dependency request below asks A01 to add the prefix. All other modified files belong to A10.

Fetch before work: A10 and all module SHAs matched the captain's list. Only the prompt package advanced
to `b61e346724542767c2fb2186e9f71a71366833b2`; this was not merged or treated as a new contract baseline.
The already published A10 checkpoint was verified on origin. During final fetch A01 advanced to
`de7290509bf558d6488be84d2e0730b2b9ab104a` (integrated r2 candidate), and A06 to
`d4f23f604443cfbab6e7ab03441f19a3e60e9e83`. Read both handoffs; no foreign module was merged into A10.

Created a detached scratch checkout at exact de72905. With the pinned A09 Windows Python environment:
`demo/verify_demo.py` PASS; `demo/rehearse.py --mode synthetic --expected-sha de7290509bf558d6488be84d2e0730b2b9ab104a`
PASS in 9.08 s, 14 PASS / 3 NOT_RUN. This includes successful HTML/JSON endpoint responses, not a
visual/content audit of those reports. Evidence: `demo/results/20261008_candidate_de72905_windows.json`.
Product tree clean, backend shut down, token absent from logs. No camera or OS hooks activated.
Latest A01 replay reported a false phone detection on no-phone images; it is not used as phone quality evidence.
A06 explicitly reports no delivered native helper/OS blocking for Alt+Tab/Win/PrtScn; deck limitations updated.

New delivery:
- `docs/pitch/PRESENTATION.md` and `Qorgau_Exam_Presentation.pdf`: 10 Russian slides, all six criteria.
- `docs/submission/ОПИСАНИЕ.md` and `Qorgau_Exam_Description.pdf`: two pages with architecture, versions,
  model provenance/licenses, exact zone-rule-1, limitations and Claude Code/OpenAI Codex disclosure.
- `docs/submission/VIDEO_SHOT_LIST.md`: 170-second shot list, narration, Windows recording instructions,
  visible source labels and fallback at 20:00 when the candidate is absent/failing.
- `docs/submission/CHECKLIST.md`: upload files, integration branch/SHA, local 23:00 submission target.
- `docs/submission/MANUAL_RESULTS_TEMPLATE.md`, `SOURCES.md`, `EVIDENCE.json`, PDF builder.
- Updated three-minute pitch, alternative 90-second speech and jury FAQ including false red-zone review.

Status rules: slide 3 uses only A09's published backend report (tested b275c70a, 352 PASS/16 XFAIL/1 SKIP).
All case rows remain partial. No manual captain results were supplied. Zones are a visibly labelled mockup
with fictitious sessions, not a working screen. No accuracy, full offline, LIVE or Windows-enforcement claim.
The first overview is limited to one computer; JSON import is conditional and network aggregation is future work.

Validation: isolated document tools, `python docs/submission/build_submission.py --preview-dir <outside-Git>`
produced exactly 10+2 PDF pages. Cyrillic text extraction and text bounds PASS; all 12 pages rendered and
visually inspected, table wrapping corrected. Evidence hashes and versions are in EVIDENCE.json.
No product runtime changed, so the historical API results below remain scoped to their recorded SHAs.

NOT_RUN: actual MP4, spoken timing, LIVE/REPLAY of an integrated candidate, overview/zones, Windows key
blocking and full offline behaviour. The video is a script, not fabricated footage. The submission form
was not sent. Captain must provide final team name if different from the neutral "Qorgau Exam, 5 participants".

## После 9 октября

Сетевая сводка класса, импорт JSON после проверки формата, расширенные CV-измерения, длительные прогоны,
установка без среды разработчика и повторная репетиция на версии для 16 октября. Сегодня эти задачи
не задерживают публикацию материалов для отбора.

## Delivered

- `demo/exams/demo_exam.json`: four real demo questions; existing A01 settings load this path.
- `demo/verify_demo.py`: strict shared-contract validation and question/option integrity checks.
- `demo/rehearse.py`: real backend process, token via stdin, HTTP flow, exact-SHA guard,
  synthetic/replay distinction, cleanup, redacted failures, measured duration and explicit NOT_RUN rows.
- `demo/replay/README.md`: commands matching A02's published CLI and recording format.
- Requirement-to-demo matrix, operator/recovery/submission checklist, source provenance hashes.
- Three-minute demonstration script, separate 90-second speech and jury FAQ in Russian.

Owned paths: `proctoring/demo/`, `proctoring/docs/pitch/`, `proctoring/handoffs/A10/`, plus the captain's
explicit addition `proctoring/docs/submission/` for this continuation.
No new UI, camera owner, model, common contract or product module was introduced.

## Verification

Environment: Windows 11 build 26200, Python 3.12.14 from the A09 pinned environment.
Backend product tree remained unmodified; A10 rehearsal/content hashes are in the result.

| Check | Result |
|---|---|
| `python demo/verify_demo.py` | PASS, ExamDefinition qorgau.v1, four questions |
| `python demo/rehearse.py --mode synthetic --expected-sha 35bea4c7b28d2c622cf7ba26ff354273cc7b6c49 --out demo/results/20261008_bootstrap_windows.json` | PASS for automated backend orchestration; 6.30 s |
| Backend READY, exact demo exam, preflight, answer, scripted phone episode, finish, human review, summary, capture stop, backend shutdown, no token in logs | 12 PASS checks |
| Calibration, HTML export, JSON export, LIVE camera/gaze, Windows shortcuts | 5 NOT_RUN checks |
| Final demo question wording + repeat API rehearsal | PASS, 5.75 s, `demo/results/20261008_content_final_windows.json` |
| Incorrect `--expected-sha` | Rejected before starting backend or writing a result |

This is an API rehearsal using SYNTHETIC signals. It is not a live presentation, real CV measurement,
Electron test or claim that all three case requirements work together. `product_release_verified=false`.
The final repetition used backend at commit `466b611` and the content hash recorded in its result;
only the demo question wording was edited. Initial 6.30 s evidence is retained.

## Next on A01 candidate

1. Integrate A10 files with A09 and the product modules; retain the full candidate SHA.
2. Repeat content validation and synthetic API rehearsal using `--expected-sha`.
3. Prepare consented replay outside Git; run replay-check and the same API rehearsal in REPLAY.
4. Execute the three-minute script in the existing Electron UI with real volunteers and camera;
   record actual elapsed time, candidate SHA, per-requirement outcomes and any failure.
5. Finalize claims/metrics only from A09's report on that same candidate.

Push status and new commit SHA are reported separately after the commit; this file does not claim
an unknown push succeeded. No deployment, main merge or contact with other team members performed.
