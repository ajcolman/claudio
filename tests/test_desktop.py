import builtins
import io
import json
import runpy
import unittest
from unittest.mock import Mock, patch

import desktop


class DesktopTests(unittest.TestCase):
    def test_ready_check_uses_health_without_reading_accounts(self):
        response = io.BytesIO(json.dumps({'app': 'claudio', 'api_version': 2, 'revision': desktop.EXPECTED_REVISION}).encode())
        with patch.object(desktop.urllib.request, 'urlopen', return_value=response) as open_url:
            self.assertTrue(desktop.is_server_running())
        self.assertEqual(open_url.call_args.args[0], 'http://localhost:5252/api/health')

    def test_incompatible_server_is_not_ready(self):
        response = io.BytesIO(b'{"app":"claudio","api_version":1}')
        with patch.object(desktop.urllib.request, 'urlopen', return_value=response):
            self.assertFalse(desktop.is_server_running())

    def test_reuse_running_server(self):
        with patch.object(desktop, 'is_server_running', return_value=True), patch.object(desktop.subprocess, 'Popen') as spawn:
            self.assertTrue(desktop.start_server_in_background())
            spawn.assert_not_called()

    def test_process_failure_reports_actionable_error(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(desktop, 'is_server_running', return_value=False), patch.object(desktop, 'port_is_open', return_value=False), patch.object(desktop.time, 'sleep'), patch.object(desktop.subprocess, 'Popen', return_value=process):
            self.assertFalse(desktop.start_server_in_background())
            self.assertIn('5252', desktop.STARTUP_ERROR)

    def test_old_build_is_not_reused(self):
        response = io.BytesIO(b'{"app":"claudio","api_version":2,"revision":"old"}')
        with patch.object(desktop.urllib.request, 'urlopen', return_value=response):
            self.assertFalse(desktop.is_server_running())

    def test_unrelated_process_is_never_terminated(self):
        with patch.object(desktop, 'is_server_running', return_value=False), patch.object(desktop, 'find_owned_server', return_value=None), patch.object(desktop.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'otro proceso'):
                desktop.migrate_existing_server()
            kill.assert_not_called()

    def test_stale_server_is_stopped_only_after_ownership_recheck(self):
        response = io.BytesIO(b'{"auto_running":false}')
        with patch.object(desktop, 'is_server_running', return_value=False), patch.object(desktop, 'find_owned_server', return_value=1234) as identify, patch.object(desktop.urllib.request, 'urlopen', return_value=response), patch.object(desktop.os, 'kill') as kill, patch.object(desktop, 'port_is_open', return_value=False):
            self.assertIsNone(desktop.migrate_existing_server())
            self.assertEqual(identify.call_count, 2)
            kill.assert_called_once_with(1234, desktop.signal.SIGTERM)

    def test_changed_process_is_never_terminated(self):
        response = io.BytesIO(b'{"auto_running":false}')
        with patch.object(desktop, 'is_server_running', return_value=False), patch.object(desktop, 'find_owned_server', side_effect=[1234, 5678]), patch.object(desktop.urllib.request, 'urlopen', return_value=response), patch.object(desktop.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'cambió'):
                desktop.migrate_existing_server()
            kill.assert_not_called()

    def test_rotation_is_paused_before_migration(self):
        config = {'threshold': 85, 'strategy': 'consume-first'}
        response = io.BytesIO(json.dumps({'auto_running': True, 'auto_config': config}).encode())
        with patch.object(desktop, 'is_server_running', return_value=False), patch.object(desktop, 'find_owned_server', return_value=1234), patch.object(desktop.urllib.request, 'urlopen', return_value=response), patch.object(desktop.os, 'kill'), patch.object(desktop, 'port_is_open', return_value=False), patch.object(desktop, 'set_rotation') as rotation:
            self.assertEqual(desktop.migrate_existing_server(), config)
            rotation.assert_called_once_with(False, config)

    def test_migration_starts_new_server_and_restores_rotation(self):
        config = {'threshold': 85, 'strategy': 'best'}
        with patch.object(desktop, 'is_server_running', side_effect=[False, False, True]), patch.object(desktop, 'port_is_open', return_value=True), patch.object(desktop, 'migrate_existing_server', return_value=config), patch.object(desktop, 'set_rotation') as rotation, patch.object(desktop.subprocess, 'Popen') as spawn:
            self.assertTrue(desktop.start_server_in_background())
            spawn.assert_called_once()
            rotation.assert_called_once_with(True, config)

    def test_import_without_pyside_supports_browser_fallback(self):
        original_import = builtins.__import__
        def without_qt(name, *args, **kwargs):
            if name.startswith('PySide6'):
                raise ImportError('PySide6 unavailable')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=without_qt):
            module = runpy.run_path(desktop.__file__, run_name='desktop_no_qt')
        self.assertFalse(module['QT_AVAILABLE'])
        self.assertNotIn('ClaudioWindow', module)
        self.assertTrue(callable(module['run_web_fallback']))


if __name__ == '__main__':
    unittest.main()
