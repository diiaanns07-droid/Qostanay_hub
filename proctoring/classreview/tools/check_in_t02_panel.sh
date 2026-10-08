#!/usr/bin/env bash
# Mount the T03 module inside the REAL T02 class panel (any T02 commit) through T02's own serve.mjs proxy.
# Builds two scratch copies of the panel: "patched" (CSP media-src 'self' + register.js) and "csp_unpatched"
# (register.js only) to show what T02 has to change. Uses the DEV harness + SYNTHETIC fake student.
#   proctoring/classreview/tools/check_in_t02_panel.sh <T02_SHA> [workdir]
# Needs: git, node + global playwright (NODE_PATH=$(npm root -g)), the proctoring venv with OpenCV.
set -euo pipefail
T02_SHA=${1:?T02 commit}; WORK=${2:-$(mktemp -d)}
ROOT=$(cd "$(dirname "$0")/../.." && pwd); PY=${PY:-$ROOT/.venv/bin/python}; PIN=135790
TOP=$(git -C "$ROOT" rev-parse --show-toplevel)
git -C "$TOP" archive "$T02_SHA" proctoring/class-panel | tar -x -C "$WORK"
for v in patched csp_unpatched; do
  d=$WORK/$v; mkdir -p "$d/src/t03"; cp -r "$WORK/proctoring/class-panel/." "$d/"
  cp "$ROOT"/classreview/ui/{review-module.js,register.js,review.css} "$d/src/t03/"
  if [ "$v" = patched ]; then
    sed -i "s|img-src 'self' data: blob:;|img-src 'self' data: blob:; media-src 'self';|" "$d/index.html"
  fi
  sed -i 's|<script type="module" src="./src/app.js"></script>|&\n    <script type="module" src="./src/t03/register.js"></script>|' "$d/index.html"
done
cd "$ROOT"
"$PY" -m classreview.devserver --port 8781 --pin $PIN --data-dir "$WORK/data" > "$WORK/dev.log" 2>&1 & DEV=$!
node "$WORK/patched/serve.mjs" --port 8782 --upstream http://127.0.0.1:8781 > "$WORK/p.log" 2>&1 & P1=$!
node "$WORK/csp_unpatched/serve.mjs" --port 8783 --upstream http://127.0.0.1:8781 > "$WORK/u.log" 2>&1 & P2=$!
trap 'kill $DEV $P1 $P2 ${FS:-} 2>/dev/null || true' EXIT
sleep 2.5
CODE=$("$PY" - <<PYEOF
import httpx
c = httpx.Client(base_url="http://127.0.0.1:8781"); c.post("/api/teacher/login", json={"pin": "$PIN"})
print(c.post("/api/teacher/session", json={"title": "t02check"}).json()["join_code"])
PYEOF
)
"$PY" -m classreview.examples.fake_student --server http://127.0.0.1:8781 --code "$CODE" --run-seconds 90 > "$WORK/fs.log" 2>&1 & FS=$!
sleep 3
for v in patched:8782 csp_unpatched:8783; do
  echo "== ${v%%:*}"; NODE_PATH=$(npm root -g) node "$ROOT/classreview/tools/check_in_t02_panel.cjs" "http://127.0.0.1:${v#*:}" $PIN "$WORK/${v%%:*}.png"
done
