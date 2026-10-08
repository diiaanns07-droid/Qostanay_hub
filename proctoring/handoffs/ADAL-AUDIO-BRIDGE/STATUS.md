# ADAL-AUDIO-BRIDGE — shutdown checkpoint

Branch: codex/adal-audio-bridge. Baseline 308b8ac; imported sibling lock537ab55 (and rootfc8a3a8) via merge24f110c, resolving preload to retain both helper imports. No push by this agent; parent owns final GitHub save.

## Implemented
- C1 default factory classroom.server.audio_feature:create_classroom_feature wraps T05 AudioHub, authenticated /api/teacher/audio/ws plus /api/teacher/audio/assets/{teacher|shared}/{file}. Existing loopback/Host/Origin gate retained; cookie checked every0.5s while idle; logout/disconnect/session replacement stops audio.
- Explicit audio_protocol=qorgau.class.audio.v1 negotiation in student hello and every extension message; bounded whitelist relay binds session and current student connection. Old REST audio_start/audio_stop refuse audio_extension_required, preventing ACK-only active claims.
- C2 proctor/uplink/audio.py: ephemeral scoped renderer relay, active audio_session_id and owned command ACK checks; heartbeat readiness, truthful mic_live, terminal/source-session/disconnect cleanup. Sibling537ab55 supplies actual client.py/app.py hook integration.
- Desktop main/src/class-audio.ts: fixed token-private IPC, exact trusted main frame, audio-only permission; teacher listen request + 5-second post-banner capture lease. Camera, subframes and foreign WebContents denied. Own preload/renderer main mount done.
- Renderer banner paints before getUserMedia/ACK; talk-only never captures student mic; actual StudentAudioEndpoint transports audio. Generation cancellation prevents delayed mic acquisition after stop. TeacherAudio listening also requires successful playback.

## ROOT STILL MUST WIRE main.ts
import createClassAudio from ./class-audio; after client construction: const classAudio=createClassAudio(client,trustedSender,()=>mainWindow?.webContents??null,devOrigin);
stream onEnvelope: classAudio.observe(env); stream onClose: ()=>classAudio.reset(); supervisor onLost: classAudio.reset(); registerIpc(): classAudio.register(ipcMain);
hardenSession: replace ONLY request/check deny handlers with classAudio.installPermissions(ses); KEEP device/display denies. Exam external Session stays all deny. Exam owner confirmed min native view y112 so72px banner visible.
Merge classreview factory into ServerConfig features comma-list rather than replace either factory.

## Observed verification
- Desktop contracts/main/renderer typecheck PASS before final small race hardening.
- class-audio strict JS typecheck PASS before final small race hardening.
- Existing AudioHub tests16 PASS.
- New real C1 websocket tests2 PASS: ACK remains accepted, forged other-student signal rejected, teacher logout sends audio_stop, legacy command refused.
- New Electron permission unit test PASS: exact main frame +audio+active lease only; no camera/subframe/foreign URL/window.
- Real C1→real C2 Uplink→real Electron fixed IPC→actual WebRTC fake-tone fixture PASS (hidden Electron, headless Chrome, generated WAV, both outputs muted, no real mic/camera, no guards).
  receiver bytes2778/packets34/audioLevel0.0267; recorded playable received synthetic Opus10922bytes.
  talk-only received teacher RTP+playback with unchanged student capture count.
  injected NotAllowedError→mic_denied and NotFoundError→mic_not_found, no live tracks.
  exam finish and classroom disconnect ended all student tracks/peers; zero false listening states.
  Evidence local ignored desktop/out/audio-electron-1791462430152/results.json and received-synthetic-tone.webm.
  Test fixture uses production helpers+IPC/permission policy with synthetic backend snapshot, not production main/OS guards.

## Latest unverified small edits after full fake-tone pass
Student update/offer generation guards, teacher delayed-microphone generation cancellation + playback invariant, C1 reconnect old-session termination, visible Adal rename. Rerun typechecks + fixture after root integration. This shutdown checkpoint deliberately saves these without claiming a rerun.

## Reproduction
Python: shared Qorgau-run/proctoring/.venv/Scripts/python.exe; PYTHONPATH=proctoring;proctoring/backend;proctoring/contracts/python;proctoring/class-audio/server.
pytest --basetemp=proctoring/desktop/out/<fresh-name> proctoring/classroom/server/tests/test_audio_extension.py proctoring/class-audio/server/tests/test_hub.py
From desktop: node scripts/typecheck.mjs; node node_modules/typescript/bin/tsc -p ../class-audio/tsconfig.json; node main/tests/run.mjs class-audio.
PLAYWRIGHT_MODULE=C:/Users/LEGION/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright and QORGAU_PYTHON=<shared python>; node renderer/tests/audio-electron/run.mjs.
Esbuild/test subprocesses needed sandbox escalation here; all commands authorized safe fixture only.

## Remaining
Root main.ts hooks + merged full build; final race tests/re-run; panel integration by teacher wave; real two-computer LAN/firewall/mDNS/TURN and physical device quality unverified. No STUN/TURN configured (LAN host candidates only).
All owned test processes exited normally; fixture finally closed hidden Electron, headless Chrome, C2 and C1. No production app, real capture or OS restrictions launched.
