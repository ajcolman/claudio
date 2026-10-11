import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which('bash')


@unittest.skipUnless(BASH and os.name == 'posix', 'El lanzador claudio requiere bash.')
class LauncherTests(unittest.TestCase):
    def launch(self, system, **environment):
        """Run the launcher with stand-ins for uname and python3; return the script it started."""
        with tempfile.TemporaryDirectory() as stubs:
            for name, body in [('uname', f'echo {system}'), ('python3', 'echo "$1"')]:
                stub = Path(stubs, name)
                stub.write_text(f'#!/bin/sh\n{body}\n')
                stub.chmod(0o755)
            result = subprocess.run([BASH, str(ROOT / 'claudio')], env={'PATH': f'{stubs}:/usr/bin:/bin', **environment},
                                    capture_output=True, text=True, timeout=10)
        return result.stdout.strip()

    def test_macos_opens_the_window_without_x11_or_wayland(self):
        self.assertEqual(self.launch('Darwin'), str(ROOT / 'desktop.py'))

    def test_macos_ssh_session_starts_only_the_server(self):
        self.assertEqual(self.launch('Darwin', SSH_CONNECTION='10.0.0.2 50000 10.0.0.1 22'), str(ROOT / 'server.py'))

    def test_linux_without_a_display_starts_only_the_server(self):
        self.assertEqual(self.launch('Linux'), str(ROOT / 'server.py'))

    def test_linux_with_a_display_opens_the_window(self):
        self.assertEqual(self.launch('Linux', WAYLAND_DISPLAY='wayland-0'), str(ROOT / 'desktop.py'))


if __name__ == '__main__':
    unittest.main()
