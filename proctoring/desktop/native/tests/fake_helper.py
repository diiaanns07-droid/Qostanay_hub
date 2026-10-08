"""Protocol-only subprocess for native.ts. Never imports Win32 or installs hooks."""
import json
import sys

def send(value):
    print(json.dumps(value), flush=True)

if '--self-check' in sys.argv:
    send({'type': 'selfcheck', 'version': 'protocol-fake', 'os': 'FAKE', 'elevated': False})
else:
    mode = sys.argv[sys.argv.index('--mode') + 1]
    send({'type': 'ready', 'version': 'protocol-fake', 'mode': 'enforce' if mode == 'enforce' else 'dry_run',
          'hook': False, 'foreground_watch': False})
    for line in sys.stdin:
        if line.strip() == 'stop':
            send({'type': 'bye', 'reason': 'stop_requested'})
            break
