# Case requirement to demonstration

Source: supplied two-page Russian case, page 2. Regulation: supplied six-page Russian regulation,
pages 4–5. Rows below are acceptance targets, not claims that the candidate has passed them.
Current baseline has no integrated camera/CV/Windows shell; final LIVE status is NOT_RUN.

| Case requirement | Visible action in the existing app | Evidence to retain | Owner / current status |
|---|---|---|---|
| Phone in hand or before screen | Show a phone in the camera view for several seconds | Real bounding box, phone_visible episode, time and source mode | A03 / NOT_RUN in integrated candidate |
| Phone raised | Raise it from below the frame and hold it briefly | Distinct phone_raised signal, recorded timing | A03 / NOT_RUN |
| Possible camera pointed at screen | Demonstrate the observed raising/position cue, if supported | Explanation and uncertainty; never say a photo was proven | A03 / NOT_RUN; may be insufficient_evidence |
| Gaze and head direction | Calibrate, then look down and to the side for a sustained interval | Separate head/gaze values and episode duration | A04/A05 / NOT_RUN |
| Normal brief glance | Read the bottom question; glance briefly at keyboard | No immediate accusation; log false alerts if any | A04/A05 / NOT_RUN |
| Presence | Leave the frame briefly after other actions | face_missing with valid camera frames | A04/A05 / NOT_RUN |
| Second face | Volunteer enters the frame while the student remains visible | multiple_faces episode | A04/A05 / NOT_RUN |
| Alt+Tab, Ctrl+C/V, Win, PrtScn | Controlled A06/A09 test, one key at a time | Per-key result on the target Windows machine | A06/A09 / NOT_RUN |
| Tabs and foreign windows | Controlled navigation/focus attempt | What was blocked versus merely detected | A06/A09 / NOT_RUN |
| Three modules at the same time locally | Phone + gaze observation while exam protection is engaged | Continuous recording and A09 candidate SHA | A01/A09 / NOT_RUN |
| Working local prototype | Start, exam, incident, teacher review, report, clean exit | Successful candidate run, prepared local assets | A01 / awaiting candidate |

The journal and teacher review strengthen the demonstration but do not replace a missing mandatory
case function. Show the actual capability matrix when a Windows action is only detected or unverified.
Do not credit synthetic events as successful real object detection or enforcement.

## Jury criteria from the regulation

| Criterion | Maximum | Demonstration focus |
|---|---:|---|
| Correspondence to case and understanding | 15 | Show each of the three mandatory blocks |
| Technical implementation | 20 | One working chain; explain model, rules and failure states |
| Deployment potential and expected effect | 20 | Local operation, hardware measurement, pilot conditions |
| Innovation | 15 | Explainable combination of observations and teacher review |
| Working prototype and demonstration | 15 | Real run, clear mode labels, prepared backup |
| Pitch and answers | 15 | Three-minute timing; limitations explained directly |

Expected effect must be presented as a hypothesis until measured. No claimed reduction in cheating,
staff workload or false alerts is included without an actual experiment.
