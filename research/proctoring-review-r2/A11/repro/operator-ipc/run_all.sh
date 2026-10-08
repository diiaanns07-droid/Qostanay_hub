#!/usr/bin/env bash
# A11 find-operator-ipc: ISOLATED REPRO (not integration, no real Electron, no camera, no models).
# Combo tree (built once by A11, see comments): proctoring/backend + contracts <- A01r2 29cadde;
# desktop/{main,preload,scripts,tsconfig*,package.json} <- A06 62a7fb1; desktop/renderer <- A07 3fef6fb;
# demo/exams/demo_exam.json <- A10 de71342.  Build: node scripts/build-electron.mjs && vite build.
set -u
S=$(cd "$(dirname "$0")" && pwd)
export PYTHONDONTWRITEBYTECODE=1 XDG_DATA_HOME="$S/data"
export QORGAU_PYTHON=/home/user/Qostanay_hub/proctoring/.venv/bin/python
export PYTHONPATH="$S/proctoring/backend:$S/proctoring/contracts/python"
cd "$S"
(cd proctoring/desktop && node scripts/build-electron.mjs >/dev/null && node node_modules/vite/bin/vite.js build --logLevel error)
echo "##### repro_ipc_lifecycle.mjs";   timeout 240 node repro_ipc_lifecycle.mjs proctoring/desktop
echo "##### repro_ui_teacher_console.mjs"; timeout 240 node repro_ui_teacher_console.mjs proctoring/desktop shots
echo "##### repro_ui_reload_unlock.mjs";  timeout 240 node repro_ui_reload_unlock.mjs proctoring/desktop shots
echo "##### repro_pin_maxlength.mjs";     timeout 120 node repro_pin_maxlength.mjs proctoring/desktop
