"""Windows launcher checks with a stub Electron CLI: no window, camera or keyboard hook."""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PROCTORING = Path(__file__).resolve().parents[3]
LAUNCHER = PROCTORING / 'demo/Start-AdalDemo.ps1'
if not LAUNCHER.exists():
    LAUNCHER = PROCTORING / 'handoffs/A07/Start-AdalDemo.ps1'


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell launcher')
class LauncherTest(unittest.TestCase):
    def run_case(self, enforce=False, check_only=False, build_fail=False):
        with tempfile.TemporaryDirectory(prefix='adal-launcher-') as temp:
            root = Path(temp)
            candidate = root / 'candidate with spaces'
            desktop = candidate / 'proctoring/desktop'
            local = root / 'local'
            for file in [candidate / 'proctoring/.venv/Scripts/python.exe',
                         desktop / 'node_modules/electron/dist/electron.exe',
                         desktop / 'native/qorgau_guard.py',
                         local / 'QorgauExam/models/attention/face_landmarker.task',
                         local / 'QorgauExam/models/phone/yolo11n.onnx']:
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('TEST STUB', encoding='utf-8')
            (desktop / 'package.json').write_text(json.dumps({'scripts': {'build':
                'node -e "process.exit(' + ('9' if build_fail else '0') + ')"'}}), encoding='utf-8')
            keys = ['ELECTRON_RUN_AS_NODE', 'NODE_OPTIONS', 'QORGAU_MODELS_DIR', 'QORGAU_REPLAY_DIR',
                    'QORGAU_SHELL_DEMO_OPERATOR', 'QORGAU_SHELL_NATIVE_ENFORCE', 'QORGAU_PYTHON',
                    'QORGAU_SHELL_DEV_RENDERER_URL', 'QORGAU_SHELL_NATIVE_HELPER',
                    'QORGAU_SHELL_EMERGENCY_ACCELERATOR']
            (desktop / 'node_modules/electron/cli.js').write_text(
                'console.log("CHILD="+JSON.stringify({cwd:process.cwd(), env:Object.fromEntries('
                + json.dumps(keys) + '.map(k=>[k,process.env[k]??null]))}));', encoding='utf-8')
            env = {**os.environ, 'LOCALAPPDATA': str(local), 'ELECTRON_RUN_AS_NODE': '1',
                   'QORGAU_SHELL_NATIVE_ENFORCE': 'inherited', 'QORGAU_SHELL_NATIVE_HELPER': 'untrusted-stale-path',
                   'QORGAU_SHELL_DEV_RENDERER_URL': 'http://localhost:9999', 'NODE_OPTIONS': '--trace-warnings'}
            quote = lambda s: "'" + str(s).replace("'", "''") + "'"
            flags = (' -Enforce' if enforce else '') + (' -CheckOnly' if check_only else '')
            command = (f"$before=(Get-Location).Path; try {{ & {quote(LAUNCHER)} -CandidateRoot {quote(candidate)}{flags} }} "
                       "catch { Write-Output ('ERROR=' + $_.Exception.Message) }; "
                       "Write-Output ('RESTORED=' + (@{node=$env:ELECTRON_RUN_AS_NODE; enforce=$env:QORGAU_SHELL_NATIVE_ENFORCE; "
                       "cwd=((Get-Location).Path -eq $before)} | ConvertTo-Json -Compress))")
            encoded = base64.b64encode(command.encode('utf-16le')).decode('ascii')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded],
                                    env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            restored = json.loads(next(line[9:] for line in lines if line.startswith('RESTORED=')))
            self.assertEqual(restored, {'node': '1', 'enforce': 'inherited', 'cwd': True})
            child = [json.loads(line[6:]) for line in lines if line.startswith('CHILD=')]
            if check_only or build_fail:
                self.assertEqual(child, [], result.stdout)
                if build_fail:
                    self.assertIn('Desktop build failed', result.stdout)
                return
            self.assertEqual(len(child), 1, result.stdout)
            state = child[0]
            self.assertEqual(Path(state['cwd']), desktop)
            actual = state['env']
            for key in ['ELECTRON_RUN_AS_NODE', 'NODE_OPTIONS', 'QORGAU_SHELL_DEV_RENDERER_URL', 'QORGAU_SHELL_NATIVE_HELPER']:
                self.assertIsNone(actual[key], key)
            self.assertEqual(actual['QORGAU_SHELL_DEMO_OPERATOR'], '1')
            self.assertEqual(actual['QORGAU_SHELL_NATIVE_ENFORCE'], '1' if enforce else '0')
            self.assertEqual(Path(actual['QORGAU_MODELS_DIR']), local / 'QorgauExam/models')
            self.assertEqual(Path(actual['QORGAU_REPLAY_DIR']), local / 'QorgauExam/replay')
            self.assertEqual(Path(actual['QORGAU_PYTHON']), candidate / 'proctoring/.venv/Scripts/python.exe')
            self.assertEqual(actual['QORGAU_SHELL_EMERGENCY_ACCELERATOR'], 'CommandOrControl+Alt+Shift+F12')

    def test_default_clears_stale_enforce_and_node_mode(self):
        self.run_case()

    def test_enforce_is_explicit(self):
        self.run_case(enforce=True)

    def test_check_only_never_launches(self):
        self.run_case(check_only=True, enforce=True)

    def test_build_failure_prevents_launch_and_restores_environment(self):
        self.run_case(build_fail=True)


if __name__ == '__main__':
    unittest.main()
