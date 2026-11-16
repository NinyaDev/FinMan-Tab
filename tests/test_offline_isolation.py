import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import test_offline


class OfflineIsolationTests(unittest.TestCase):
    def test_private_json_env_and_symlinks_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            viewer = root / 'viewer'
            viewer.mkdir()
            names = ['app.py', 'demo.json', 'readonly-credentials.json',
                     'sheet.local.json', '.env', 'connection-secret.json']
            for name in names:
                (viewer / name).write_text('synthetic sentinel')
            (viewer / 'borrowed.py').symlink_to(viewer / 'readonly-credentials.json')
            names.append('borrowed.py')
            with patch.object(test_offline, 'ROOT', root):
                excluded = test_offline.ignore_private_files(str(viewer), names)
            self.assertEqual(set(names) - set(excluded), {'app.py', 'demo.json'})
