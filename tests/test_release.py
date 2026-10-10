import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from dex_fixture import make_dex
from scripts.build_release import build


class ReleaseTests(unittest.TestCase):
    def test_invalid_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for version in ('0.1.0', '../v0.1.0', 'v01.0.0', 'v0.1.0-rc.0'):
                with self.subTest(version=version), self.assertRaises(ValueError):
                    build(version, Path(directory))

    def test_archive_is_reproducible_and_runs_outside_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = build('v0.1.0-rc.1', root)
            original = archive.read_bytes()
            self.assertEqual(build('v0.1.0-rc.1', root).read_bytes(), original)
            self.assertEqual((root / 'SHA256SUMS').read_text().split()[0],
                             hashlib.sha256(original).hexdigest())
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
                prefix = 'ASC-v0.1.0-rc.1/'
                required = (
                    'LICENSE', 'requirements.txt',
                    'requirements-gui.txt', 'requirements-gui-lock.txt',
                    'requirements-mcp.txt', 'requirements-mcp-lock.txt',
                    'README.md', 'README-GUI.md', 'README-MCP.md', 'VALIDATION.md',
                    'mcp-config.example.json',
                    'asc_gui/__main__.py', 'asc_gui/app.py', 'asc_gui/assets/close.svg',
                    'asc_mcp/__main__.py', 'asc_mcp/server.py', 'asc_mcp/worker.py',
                )
                for path in required:
                    self.assertIn(prefix + path, names)
                for path in ('install-gui.sh', 'run-gui.sh', 'install-mcp.sh', 'run-mcp.sh'):
                    info = package.getinfo(prefix + path)
                    self.assertEqual(info.create_system, 3)
                    self.assertEqual(info.external_attr >> 16, 0o100755)
                self.assertEqual(package.getinfo(prefix + 'asc_mcp/server.py').external_attr >> 16,
                                 0o100644)
                self.assertFalse(any(name.endswith(('.dex', '.apk', '/test.py', '/test_findrefs.py'))
                                     or any(part in name for part in
                                            ('/tests/', '/.git/', '/screenshots/', '/.venv/',
                                             '/.venv-mcp/', '/__pycache__/')) for name in names))
                package.extractall(root)
            app = root / 'ASC-v0.1.0-rc.1'
            # Analysis imports the shared GUI parsers without importing Qt or MCP.
            result = subprocess.run([
                sys.executable, '-I', '-c',
                'import sys; sys.path.insert(0, sys.argv[1]); '
                'from asc_mcp.analysis import AnalysisSession; '
                'assert not AnalysisSession().status()["loaded"]; '
                'assert "PySide6" not in sys.modules and "mcp" not in sys.modules',
                str(app),
            ], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            apk = root / 'fixture.apk'
            with zipfile.ZipFile(apk, 'w', compression=zipfile.ZIP_DEFLATED) as package:
                package.writestr('classes.dex', make_dex())
            for args, expected in ((['findrefs', str(apk), 'string', 'token'], 'token'),
                                   (['getclass', str(apk), 'example.Test'], 'class Test')):
                result = subprocess.run([sys.executable, str(app / 'main.py'), *args],
                                        cwd=root, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
