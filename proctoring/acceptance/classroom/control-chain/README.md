# Actual classroom screen-control chain

Runs the built production Electron main, preload and React renderer, its real proctor backend/C2, a second independent real proctor backend/C2, a real C1 server, and the public teacher panel in Chrome. Teacher commands are clicked through the registered control module. Neither the harness nor the Python helper sends a lock receipt or fabricates a device status/ACK.

Both backend sessions are explicitly SYNTHETIC and remain CREATED. The runner does not preflight, start an exam, use native guards, camera or microphone. The native helper path points to a nonexistent file; native enforcement and shell self-test are disabled. The Electron window is shown, hidden and restarted during visibility/recovery checks, without kiosk or fullscreen.

Prerequisites: installed desktop dependencies, Python backend dependencies, Chrome, and Playwright. Build `proctoring/desktop` with `npm run build`. From repository root in PowerShell:

```powershell
$env:QORGAU_PYTHON = 'C:/absolute/runtime/Scripts/python.exe'
$env:PLAYWRIGHT_MODULE = 'C:/absolute/node_modules/playwright'
node proctoring/acceptance/classroom/control-chain/run.mjs C:/absolute/external-output
```

The output directory must be outside the checkout. Runtime databases/profiles contain ephemeral test credentials; do not commit or share them. Only `results.json` and the cropped `actual-lock.png` are intended as sanitized evidence. PIN/join code pass through a private parent/child pipe and are never saved to the report. The helper reuses the reviewed C1 `ServerProcess` harness but sets status stale=6s and command ACK window=8s to accommodate the production C2's 2s heartbeat and 5s lock receipt deadline.

Scenarios: custom teacher reason; pending then genuine lock/unlock receipt; two identities with isolated targeting; actual teacher WebSocket loss/reconnect and invalidation of displayed confirmation; no-renderer timeout; hidden-window refusal; renderer reload; full Electron/backend restart with persisted identity/lock; disconnected peer invalidation. WebSocket traffic is forwarded unchanged; the loss check closes the actual connection without injecting messages. Audio is only an optional asset-mount check, without opening a media device. A failure stops the run and saves bounded sanitized DOM/status observations; later scenarios are not claimed as passed. The optional `--skip-reload` switch is solely for independent restart diagnosis and explicitly marks the skipped scenario in the report; it is not a full acceptance run.

This proves application-overlay control on a local synthetic process chain. It does not prove native OS enforcement, real media capture, CV accuracy, or remote-LAN performance.
