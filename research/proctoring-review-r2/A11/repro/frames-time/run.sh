#!/bin/sh
# usage: run.sh <repro.py> [args...]   -> Python repro on the isolated combo tree (build_combo.sh first)
#        run.sh r3                     -> A07 renderer geometry (vite build + preview on 127.0.0.1:4173, Playwright)
W=/tmp/claude-0/-home-user-Qostanay-hub/45deca49-8d87-5e54-89f8-dc4697612908/scratchpad/a11/find-frames-time
if [ "$1" = "r3" ]; then
  cd "$W/ui/proctoring/desktop" && ./node_modules/.bin/vite build >/dev/null 2>&1
  ./node_modules/.bin/vite preview >/dev/null 2>&1 &
  PREVIEW_PID=$!
  sleep 3
  cd "$W" && NODE_PATH=$(npm root -g) PLAYWRIGHT_MODULE=$(npm root -g)/playwright node r3_calibration_stage_geometry.mjs "$W"
  kill $PREVIEW_PID 2>/dev/null
  exit 0
fi
cd "$W" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$W/combo/backend:$W/combo/contracts/python" \
  /home/user/Qostanay_hub/proctoring/.venv/bin/python -W ignore "$@" 2>&1 | grep -v -e "^INFO" -e "^DEBUG" -e "exam file"
