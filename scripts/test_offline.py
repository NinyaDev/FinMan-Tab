"""Run public tests from a disposable copy with synthetic config and no sockets.

Never imports code from the real working directory or copies credentials.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='finance-offline-') as directory:
    target = Path(directory)
    for name in ('clients', 'tests', 'viewer', 'onboarding'):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, target / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('pipeline.py', 'config.py', 'prompts.yaml'):
        shutil.copy2(ROOT / name, target / name)
    shutil.copy2(ROOT / 'config.example.yaml', target / 'config.yaml')
    environment = {k: v for k, v in os.environ.items()
                   if k in ('PATH', 'SYSTEMROOT', 'LANG', 'TMPDIR')}
    environment.update(GEMINI_API_KEY='offline-test-key', HOME=directory,
                       PYTHON_DOTENV_DISABLED='1', PYTHONDONTWRITEBYTECODE='1')
    code = '''
import socket, unittest

def blocked(*args, **kwargs):
    raise AssertionError("Network disabled in offline tests")
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
suite = unittest.defaultTestLoader.discover("tests")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=directory, env=environment)
    raise SystemExit(result.returncode)
