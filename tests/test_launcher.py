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
    def run_launcher(self, system, *arguments, **environment):
        """Run the launcher with stand-ins for uname and python3, which prints the script it was given."""
        with tempfile.TemporaryDirectory() as stubs:
            for name, body in [('uname', f'echo {system}'), ('python3', 'echo "$@"')]:
                stub = Path(stubs, name)
                stub.write_text(f'#!/bin/sh\n{body}\n')
                stub.chmod(0o755)
            return subprocess.run([BASH, str(ROOT / 'claudio'), *arguments], env={'PATH': f'{stubs}:/usr/bin:/bin', **environment},
                                  capture_output=True, text=True, timeout=10)

    def launch(self, system, **environment):
        return self.run_launcher(system, **environment).stdout.strip()

    def test_named_modes_open_the_window_or_the_browser(self):
        self.assertEqual(self.run_launcher('Linux', 'desktop').stdout.strip(), str(ROOT / 'desktop.py'))
        self.assertEqual(self.run_launcher('Linux', 'web').stdout.strip(), f'{ROOT / "desktop.py"} --web')

    def test_unknown_argument_explains_the_usage_and_starts_nothing(self):
        result = self.run_launcher('Linux', 'list', WAYLAND_DISPLAY='wayland-0')
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertIn('claudio [desktop|web]', result.stderr)

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
