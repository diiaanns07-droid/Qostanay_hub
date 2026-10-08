# A10 delivery status

Branch: `codex/proctor-A10`. Contract/baseline: A01
`35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`, `qorgau.v1`.
Previous A10 checkpoint: `466b611e495b6228fa755f0ed6772362615359da` (local first delivery).
No published A10 delivery was found before starting.
Stage: demo content, executable API rehearsal and pitch package delivered; full candidate pending.

## Delivered

- `demo/exams/demo_exam.json`: four real demo questions; existing A01 settings load this path.
- `demo/verify_demo.py`: strict shared-contract validation and question/option integrity checks.
- `demo/rehearse.py`: real backend process, token via stdin, HTTP flow, exact-SHA guard,
  synthetic/replay distinction, cleanup, redacted failures, measured duration and explicit NOT_RUN rows.
- `demo/replay/README.md`: commands matching A02's published CLI and recording format.
- Requirement-to-demo matrix, operator/recovery/submission checklist, source provenance hashes.
- Three-minute demonstration script, separate 90-second speech and jury FAQ in Russian.

Only `proctoring/demo/`, `proctoring/docs/pitch/`, `proctoring/handoffs/A10/` were changed.
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
