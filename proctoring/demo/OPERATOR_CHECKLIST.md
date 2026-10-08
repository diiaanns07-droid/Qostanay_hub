# Operator checklist

## Before the clock starts

1. Use A01's exact integration SHA. Record the SHA in A09 results and the rehearsal record.
2. Prepare dependencies and models with the network available, then verify their checksums.
3. Run A09 checks. Keep FAIL, XFAIL and NOT_RUN visible. Do not treat test counts as CV accuracy.
4. Set the A06 teacher PIN using its existing tool. Keep the PIN out of slides, commits and logs.
5. Test camera permission, lighting, glasses and the actual demo laptop. Position the camera at
   the top of the screen; use the same position during calibration and demonstration.
6. Ask the two on-camera volunteers for permission. Leave `retain_media=false` unless retaining
   evidence is explicitly agreed. Keep real recordings outside the repository.
7. Open the existing Electron UI. Complete the real five-point calibration before timed pitching.
   Failed calibration requires retry or an explicitly explained limitation; do not fake completion.
8. Confirm LIVE/REPLAY/SYNTHETIC is visible. Start the local demo exam with `is_demo=true`.
9. Check the A06 emergency shortcut `Ctrl+Alt+Shift+F12` in a controlled test before enabling
   restrictions. Verify clean keyboard/focus recovery. Stop if that fails.
10. Prepare a previously recorded video of the working candidate and a labelled REPLAY fallback.
    Neither may silently replace a claimed live result.

## Roles during the 180-second demonstration

- Speaker: introduces the task, narrates signals and opens the teacher report after the exam ends.
- Student volunteer: normal reading, phone raise, sustained down/side glance, short exit from frame.
- Second volunteer: enters frame briefly, then leaves.
- Operator: handles the existing UI, stopwatch and emergency release. One person may combine roles
  if the sequence was rehearsed; do not have five people operating the same application.

Use the phone and gaze together for one interval. Hold each intended signal beyond the actual
configured threshold, measured during rehearsal. A05 currently uses a 3-second engineering default
for sustained gaze; this is a configurable rule, not a scientific threshold for dishonesty.

## On failure

| Failure | Action |
|---|---|
| Camera unavailable | End/abort the session; inspect permissions and camera ownership. If unrecoverable, explicitly switch to recorded demonstration |
| Backend disconnected | Wait for A06's visible release/restart state. Verify input restored before retrying |
| App frozen with restrictions | Use the tested emergency shortcut; do not keep exercising keys if release is uncertain |
| Weights missing or invalid | Stop. Run preparation/checksum verification before restarting; never substitute fixture mode silently |
| Internet missing | Runtime should use prepared assets; record any attempted network dependency as a defect |
| A detection is missed | Say it was missed, log conditions, continue. Do not claim 100% detection |

Never terminate arbitrary user processes, change registry/group policy, or disable the computer's
network for a demonstration. A09's isolation protocol is scoped to the test process/VM.

## Submission before 23:59 on 8 October

- PDF presentation: problem, existing architecture, demonstration, measured results, limitations,
  expected pilot effect. The source regulation explicitly requests PDF.
- Working prototype/demo or video of the actual prototype. Show source mode and avoid editing
  a failed detection into a claimed success.
- Repository link or source archive as required by the case/submission form.
- Technical description: models/data, dependencies, local processing, conditions, limitations,
  AI development tools used, and what the team can explain about the code.
- Verify uploaded links and access before the deadline; retain the submission confirmation.

The regulation has inconsistent registration/development dates in some paragraphs. Its submission
clause 5.3 and key-dates table both say 8 October 23:59; this package plans against that deadline.
