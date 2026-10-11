import io
import os
import plistlib
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import install_macos

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(os.name == 'posix', 'La aplicación de macOS se inicia con /bin/sh.')
class InstallMacosTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.python = self.stand_in("it's python/python3")

    def stand_in(self, name, body='printf \'%s\\n\' "$0" "$@"'):
        """An executable that reports how it was started instead of opening a window."""
        target = self.home / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'#!/bin/sh\n{body}\n')
        target.chmod(0o755)
        return target

    def install(self):
        with patch.dict(os.environ, {'HOME': str(self.home)}), patch.object(sys, 'executable', str(self.python)), redirect_stdout(io.StringIO()):
            install_macos.install()
        return self.home / 'Applications' / 'Claudio.app' / 'Contents'

    def start_app(self, contents):
        """Start the bundle as Finder does: the executable named in Info.plist, with a minimal PATH."""
        info = plistlib.loads((contents / 'Info.plist').read_bytes())
        result = subprocess.run([str(contents / 'MacOS' / info['CFBundleExecutable'])], env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
                                capture_output=True, text=True, timeout=10)
        python, *arguments = result.stdout.splitlines()
        return python, arguments

    def test_app_opens_the_desktop_window_with_the_installing_python(self):
        python, arguments = self.start_app(self.install())
        self.assertEqual(python, str(self.python))
        self.assertEqual(arguments, [str(ROOT / 'desktop.py')])

    def test_bundle_icon_is_an_icns_wrapping_the_512px_logo(self):
        contents = self.install()
        info = plistlib.loads((contents / 'Info.plist').read_bytes())
        icns = (contents / 'Resources' / f'{info["CFBundleIconFile"]}.icns').read_bytes()
        png = (ROOT / 'assets' / 'claudio.png').read_bytes()
        # ICNS: magic and total length, then one entry (type, length including its header, PNG data).
        self.assertEqual(struct.unpack('>4sI', icns[:8]), (b'icns', len(icns)))
        self.assertEqual(struct.unpack('>4sI', icns[8:16]), (b'ic09', len(png) + 8))
        self.assertEqual(icns[16:], png)

    def test_bundle_is_declared_as_an_application(self):
        info = plistlib.loads((self.install() / 'Info.plist').read_bytes())
        self.assertEqual(info['CFBundlePackageType'], 'APPL')
        self.assertEqual(info['CFBundleName'], 'Claudio')
        self.assertRegex(info['CFBundleIdentifier'], r'^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$')

    def test_reinstalling_switches_the_app_to_the_new_python(self):
        self.install()
        self.python = self.stand_in('venv/bin/python3')
        python, _ = self.start_app(self.install())
        self.assertEqual(python, str(self.python))

    @unittest.skipIf(sys.platform == 'darwin', 'En macOS el instalador sí debe ejecutarse.')
    def test_script_refuses_to_install_outside_macos(self):
        result = subprocess.run([sys.executable, str(ROOT / 'install_macos.py')], env={'HOME': str(self.home), 'PATH': '/usr/bin:/bin'},
                                capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('macOS', result.stderr)
        self.assertFalse((self.home / 'Applications').exists())


if __name__ == '__main__':
    unittest.main()
