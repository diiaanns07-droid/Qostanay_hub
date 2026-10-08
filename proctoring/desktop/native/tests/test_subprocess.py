"""Actual pipe shutdown, never an actual hook. Detect daemon/stdio shutdown deadlocks."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

@pytest.mark.parametrize('case,command,reason', [
    ('input','stop\n','stop_requested'), ('input','','stdin_eof'),
    ('heartbeat',None,'heartbeat_lost'), ('parent',None,'parent_exited'), ('limit',None,'max_minutes'),
])
def test_lifecycle_with_real_pipes_and_fake_hook(case, command, reason):
    process = subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('runtime_driver.py')), case],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    try:
        if command is not None:
            process.stdin.write(command)
            process.stdin.flush()
            if command == '':
                process.stdin.close()
        # Keep stdin OPEN for watchdog cases. The child must exit anyway.
        assert process.wait(timeout=4) == 0
        events = [json.loads(line) for line in process.stdout.read().splitlines()]
        assert events[0]['type'] == 'ready'
        assert events[-1] == {'type':'bye', 'reason':reason}
        assert process.stderr.read() == ''
    finally:
        if process.poll() is None:
            process.kill()  # our fake-hook test child only
            process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
