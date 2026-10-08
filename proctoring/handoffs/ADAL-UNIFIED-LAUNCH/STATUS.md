# ADAL-UNIFIED-LAUNCH

Branch `codex/adal-unified-launch`, base `8302d14`; assigned origin checked: `https://github.com/diiaanns07-droid/Qostanay_hub.git`. Only assigned launcher/packaging/README/LIVE_TONIGHT/test/handoff paths changed. Coordinator integrates and pushes; no push from this agent.

## Checkpoint 1 — implementation and stub regression

- Added `proctoring/Start-Adal.ps1 -Role Teacher|Student|Standalone` as the documented entry point. Teacher delegates to existing C1 launcher; Student/Standalone use the existing shared Windows kill-on-close Job owner. Standalone packaging launcher now delegates to Start-Student instead of spawning an unowned Electron tree.
- `-Enforce` explicitly sets the real Electron configuration environment to `1`; omission explicitly sets `0` even if inherited environment says `1`. BackendOnly+Enforce and incompatible role options fail locally. Native helper path uses the selected checkout; presence is checked without executing it. Startup self-test remains enabled; emergency combination is fixed/printed as Ctrl+Alt+Shift+F12.
- Selected Python is explicit or current `.venv`; selected-checkout PYTHONPATH is retained, including Standalone. Class connection environment is cleared for Standalone. Existing stdin payload/token handling, code masking, no persistent secret logs, and Job child ownership remain. Actual Electron PID is printed by its owning worker for precise recovery.
- Current Git SHA/dirty status is printed; optional ExpectedSha requires exact SHA and clean proctoring tree. Historical pinned de729 LIVE launcher is a compatibility delegate. Retired no-selftest bypass is rejected. Local build freshness checks remain; Standalone retains the strict model/dependency preflight without device activation.
- README now describes actual C1/C2/Electron/LIVE startup for PC1+PC2+PC3 on the same LAN/Wi-Fi, offline preparation, loopback-only teacher panel, explicit Enforce, and blocked/detected_only/unverified distinctions. Restored readable UTF-8 LIVE_TONIGHT checklist for simultaneous CV+Enforce and emergency recovery; no physical PASS claim. PowerShell files have UTF-8 BOM for PS5.1, with UTF-8 console output.

Validation: `test_unified_launcher.py` **11 PASS** on Windows PowerShell 5.1. Temporary copied scripts execute actual environment/root/build validation; only process/probe/wait functions are stubbed. Covers explicit Enforce/default-off, standalone delegation/current-source selection, no app in CheckOnly, bad option rejection, missing helper/models, stale build rejection, optional SHA failure, compatibility flags, BOM and parser. No Electron/device/network/hook launched by these tests. Initial sandbox temp-directory restriction required bounded approved test escalation.

Checkpoint commit: `a5ff87044096ee14d0bc35673652cb6079b6c3b1`.

## Final validation

- New process-stub suite: **11/11 PASS Windows PowerShell 5.1**, **11/11 PASS PowerShell 7**. Actual production functions choose root/Python/environment/build paths; no production testing bypass was added.
- Existing `test_launchers.py` selected parser/bad server-code-Python/whitespace-label checks: **3/3 PASS**. Server/backend runtime tests were intentionally not executed in this assignment.
- Actual canonical `Start-Adal -Role Teacher -ExpectedSha <full checkpoint SHA> -CheckOnly -Python <explicit shared test venv>`: **PASS**, prints the full actual SHA, `dirty=False`, selected checkout source and readable Russian; imports only, no C1 listener.
- Actual canonical `Start-Adal -Role Student -BackendOnly -CheckOnly` with synthetic join code: **PASS**; prints selected source, masks code, reports no camera/microphone/hooks/window/network. Backend service was not started.
- Short ExpectedSha is rejected by the 40-character parameter contract; missing Git metadata with explicit ExpectedSha fails closed. Stale source/build and missing model checks remain covered by negative fixture tests.
- `git diff --check` clean; four PS entrypoints have UTF-8 BOM, and redirected console output remains valid UTF-8.

No real Electron, camera, microphone, native hook, service deployment, firewall change or physical Enforce test was executed. Enforce remains a request; actual native blocking and simultaneous LIVE CV require the restored manual target-machine checklist. Standalone's strict assets preflight is tested with the copied process fixture here; this isolated checkout has no installed desktop/model assets, so no full standalone launch is claimed.

Next: coordinator integrates this branch (checkpoint plus final handoff commit), rebuilds the integrated desktop, publishes the integration branch and uses the single Start-Adal entrypoint for explicitly authorized manual LIVE+Enforce acceptance. No push by this agent.
