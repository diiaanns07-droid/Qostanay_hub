# ADAL-AUDIO-BRIDGE checkpoint

Branch codex/adal-audio-bridge, baseline 308b8ac. Parent coordinates integration; no push requested for this wave.

Implemented first source slice (verification pending): default C1 T05 AudioHub adapter with explicit qorgau.class.audio.v1 negotiation, authenticated teacher /api/teacher/audio/ws, bounded relay, student extension dispatch, legacy audio command refusal. C2 helper proctor/uplink/audio.py queues no media and accepts only active scope/session/owned command messages. Awaiting owner integration hooks in uplink/client.py + backend app.py.

Student endpoint now presents micRequested before getUserMedia and cancels pending capture after disconnect. Further lifecycle tests, desktop IPC/permission helper, renderer banner and real fake-tone WebRTC verification still pending.

No real microphone/camera capture or OS guard was started. No remote push performed. Next: wire Electron renderer/main and prove the bounded vertical slice with fake devices.
