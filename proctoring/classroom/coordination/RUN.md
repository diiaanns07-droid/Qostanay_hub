# Qorgau Classroom — how to run (owner: T01)

All commands run from `proctoring/` with the project venv (Python 3.12). There are no new dependencies: the server uses
`fastapi`, `uvicorn`, `websockets` and `pydantic` from `requirements/full.txt`.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements/full.txt     # once (or: uv sync)
```

## Class server

```bash
PYTHONPATH=. .venv/bin/python -m classroom.server --port 8765 --data-dir ./data/classroom
```

* Listens on `0.0.0.0:8765`, so student PCs on the LAN can reach `/ws/student`.
* The teacher panel and the teacher API answer **only on this computer**: `http://127.0.0.1:8765/`.
* The console (stderr) prints the teacher PIN, the panel URL and the LAN addresses for students.
* A machine-readable line goes to stdout: `QORGAU_CLASS_READY {"port":…, "data_dir":…}`.
* Options:
  * `--host`, `--port` (`0` = free port), `--data-dir`;
  * `--features "pkg.mod:factory,…"` (feature plug-ins, see INTERFACES.md §3);
  * `--exit-on-stdin-eof` (for supervisors).
  * The environment equivalents are `QORGAU_CLASS_<FIELD>`, e.g. `QORGAU_CLASS_TEACHER_PIN`, `QORGAU_CLASS_UI_DIR`.
* The data (SQLite in WAL mode) survives restarts and `kill -9`: sessions, join code, students and tokens, statuses,
  events, incidents and commands. The default data dir is `%LOCALAPPDATA%\QorgauClassroom` on Windows and
  `~/.local/share/qorgau-classroom` elsewhere.
* Windows: allow inbound TCP 8765 on the **private** network profile only, e.g.
  `New-NetFirewallRule -DisplayName "Qorgau class" -Direction Inbound -Protocol TCP -LocalPort 8765 -Profile Private -Action Allow`.

### Teacher panel

Open `http://127.0.0.1:8765/` on the teacher PC and enter the PIN from the console.
* Today the page is the T02 class panel (`proctoring/class-panel/`) in REAL mode, served by this server; there is no
  separate `serve.mjs`.
* `--ui teacher-ui` selects the React shell once it is built; `--ui none` serves only the API.
* After login, create a session (curl below, or later the T04 exams module) and give students the join code.

### Student PC (C2 uplink, branch `codex/class-C2`)

```powershell
$env:QORGAU_CLASS_SERVER = "192.168.1.10:8765"   # teacher PC address from the server console
$env:QORGAU_CLASS_CODE   = "123456"              # join code of the session
# then start Qorgau Exam / the student backend as usual (C2 STATUS.md)
```

### First minutes with curl (on the teacher PC)

```bash
PIN=…   # from the console
curl -s -c jar -H 'Content-Type: application/json' -d "{\"pin\":\"$PIN\"}" http://127.0.0.1:8765/api/teacher/login
curl -s -b jar -H 'Content-Type: application/json' -d '{"title":"10А, физика","mode":"url","allowed_urls":["https://exam.example.kz/*"]}' http://127.0.0.1:8765/api/teacher/session   # -> join_code
curl -s -b jar http://127.0.0.1:8765/api/teacher/students
```

## Simulated students (test data, always labelled)

```bash
PYTHONPATH=. .venv/bin/python -m classroom.simulator --server 127.0.0.1:8765 --code <join_code> --students 30 --duration 120 [--chaos]
```

* Students are named `SIM-NN (симуляция)`, send `hello.simulated=true`, and their previews are watermarked `SIMULATED`.
* The server marks everything they send `origin: "simulated"`.
* **A simulation of N clients proves the server's protocol handling, not that N real cameras or a real Wi-Fi work.**

## Checks

```bash
PYTHONPATH=. .venv/bin/python -m classroom.contracts.generate --check     # generated schema/TS/fixtures in sync
.venv/bin/python -m pytest -q -p no:cacheprovider classroom/                # contracts + real-process server tests
python3 classroom/coordination/verify_ownership.py --self-test
# minimal C1 chain in a real browser: real C2 uplink code (synthetic data source) -> server -> T02 REAL panel
git worktree add --detach /tmp/c2 origin/codex/class-C2
C2_BACKEND=/tmp/c2/proctoring/backend node classroom/server/tests/chain/chain.e2e.mjs /tmp/c1-chain   # global Playwright 1.56.1
python3 classroom/coordination/verify_ownership.py --agent T0N --base <T01 baseline SHA>   # before every push of a T-role
```
