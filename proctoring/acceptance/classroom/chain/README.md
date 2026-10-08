# Two real student backends, synthetic sources

Run from any working directory with a Python 3.12 environment containing the
project's full backend dependencies:

```powershell
python proctoring/acceptance/classroom/check_chain.py --python C:/path/to/.venv/Scripts/python.exe --output C:/path/to/windows-result.json
```

The selected interpreter runs both the harness and subprocesses. Explicit
`PYTHONPATH` forces this checkout, even when the environment's editable install
points elsewhere. No package installation, user configuration, secrets, webcam,
microphone, browser or native enforcement is involved. Only generated synthetic
frames and scripted observations are used; student labels and result JSON say
SYNTHETIC. C1's machine-readable origin is independently checked, not assumed.

The harness starts a loopback C1 server and **two separate full `python -m proctor
serve` processes**, using the real C2 uplink inside each process. It creates
synthetic sessions through authenticated APIs, skips synthetic calibration,
starts and finishes each student through addressed C1 commands, compares the
local evidence stores with C1 events and totals, and receives both teacher and
student WebSocket streams. It then kills and restarts C1 on the same port/data,
verifies durable events and commands, and restarts one backend to verify saved
uplink identity. On Windows, venv launcher and child interpreter PIDs differ;
the harness verifies process ancestry and stops only its own processes.

Every operation has a timeout. Temporary data lives in one fresh system temp
directory and is removed after owned processes stop. Credentials stay in memory
or that temporary application data, never on the command line or in the JSON.
Only sanitized evidence is retained. Exit code 0 means every check passed; 1
means a failed check or prerequisite. Skipped checks are explicit.

This proves loopback API/uplink wiring and persistence on the recorded operating
system. It does not prove CV accuracy, real cameras, LAN/Wi-Fi capacity, Electron
UI, native lockdown, or optional T03/T04/T05 feature integration.

`windows-result.json` is an observed run, not a fixture or promise of success on
another revision. Its `source_commit` identifies the product source under test.
