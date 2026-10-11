import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server


class ServerTests(unittest.TestCase):
    def test_bad_account_never_runs_command(self):
        with patch.object(server, 'run_cswap') as run:
            for account in [None, True, 0, '-1', '--help', 'a; touch /tmp/no', []]:
                with self.subTest(account=account), self.assertRaises(ValueError):
                    server.perform_action('/api/alias', {'account': account, 'alias': 'test'})
            run.assert_not_called()

    def test_alias_failure_is_reported(self):
        with patch.object(server, 'run_cswap', return_value=(1, '', 'Alias duplicado')):
            result = server.perform_action('/api/alias', {'account': 1, 'alias': 'test'})
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'Alias duplicado')

    def test_remove_confirms_the_action_already_confirmed_in_dialog(self):
        with patch.object(server, 'run_cswap', return_value=(0, 'removed', '')) as run:
            result = server.perform_action('/api/remove', {'account': 2})
        self.assertTrue(result['success'])
        run.assert_called_once_with(['remove', '2'], stdin_input='y\n')

    def test_pause_requires_boolean(self):
        with self.assertRaises(ValueError):
            server.perform_action('/api/toggle-account', {'account': 1, 'disable': 'false'})

    def test_token_uses_stdin_and_redacts_errors(self):
        with patch.object(server, 'run_cswap', return_value=(1, '', 'invalid secret-token')) as run:
            result = server.perform_action('/api/add-token', {'token': 'secret-token'})
        run.assert_called_once_with(['add-token', '-'], stdin_input='secret-token\n')
        self.assertNotIn('secret-token', json.dumps(result))

    def test_partial_registration_does_not_invite_duplicate_registration(self):
        with patch.object(server, 'run_cswap', side_effect=[(0, 'ok', ''), (1, '', 'alias error')]):
            result = server.perform_action('/api/add-token', {'token': 'token', 'email': 'test@example.com', 'alias': 'Test'})
        self.assertTrue(result['success'])
        self.assertIn('warning', result)

    def test_unknown_quota_remains_unknown(self):
        with patch.object(server, 'run_cswap', side_effect=[(0, '{"active":{}}', ''), (0, '{"accounts":[{"number":1,"usage":null}]}', '')]), patch.object(server, 'get_recent_projects', return_value=[]):
            result = server.get_system_status()
        self.assertIsNone(result['list']['accounts'][0]['usage'])

    def test_status_failure_not_reported_as_empty_accounts(self):
        for response in [(1, '', 'cswap no disponible'), (0, 'no json', ''), (0, '[]', '')]:
            with self.subTest(response=response), patch.object(server, 'run_cswap', return_value=response):
                result = server.get_system_status()
                self.assertFalse(result['success'])
                self.assertNotIn('list', result)

    def test_mixed_project_dates_and_missing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'claude.json'
            config.write_text(json.dumps({'projects': {
                '/missing': {'lastAccessed': 'bad'}, directory: {'lastAccessed': '2026-09-17T10:00:00Z'},
                '/older': {'lastAccessed': 1000000000000}, '/unknown': None}}))
            with patch.object(server, 'CLAUDE_JSON_PATH', str(config)):
                projects = server.get_recent_projects()
            self.assertEqual(projects[0]['path'], directory)
            self.assertTrue(projects[0]['exists'])
            self.assertFalse(next(p for p in projects if p['path'] == '/missing')['exists'])

    def test_parallel_uses_argument_list(self):
        with patch.object(server, 'launch_terminal', return_value=(True, None)) as launch:
            result = server.perform_action('/api/open-terminal', {'mode': 'parallel', 'account': 2, 'cwd': "/tmp/John's project"})
        self.assertTrue(result['success'])
        launch.assert_called_once_with([server.CSWAP_BIN, 'run', '2', '--share-history', '--', '--resume'], "/tmp/John's project")

    def test_arbitrary_terminal_command_rejected(self):
        with patch.object(server, 'launch_terminal') as launch, self.assertRaises(ValueError):
            server.perform_action('/api/open-terminal', {'command': 'touch /tmp/no'})
        launch.assert_not_called()

    def test_missing_directory_does_not_launch(self):
        with patch.object(server.os.path, 'isdir', return_value=False), patch.object(server.subprocess, 'Popen') as launch:
            self.assertFalse(server.launch_terminal(['claude'], '/missing')[0])
        launch.assert_not_called()

    def test_terminal_path_with_quotes_kept_separate(self):
        with tempfile.TemporaryDirectory(prefix="claudio's ") as directory:
            with patch.object(server.platform, 'system', return_value='Linux'), patch.object(server.shutil, 'which', return_value='/bin/konsole'), patch.object(server.subprocess, 'Popen') as launch:
                self.assertTrue(server.launch_terminal(['claude', '--resume'], directory)[0])
            self.assertEqual(launch.call_args.args[0][2], directory)
            self.assertEqual(launch.call_args.kwargs['cwd'], directory)

    def test_auto_rejects_invalid_config_before_start(self):
        with patch.object(server.subprocess, 'Popen') as launch:
            for threshold in [True, '90', 99, 69, 90.5]:
                with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                    server.toggle_auto({'enable': True, 'threshold': threshold})
            launch.assert_not_called()

    def test_auto_reports_immediate_failure_without_changing_config(self):
        process = Mock()
        process.wait.return_value = 1
        with patch.object(server, 'auto_process', None), patch.object(server.subprocess, 'Popen', return_value=process):
            result = server.toggle_auto({'enable': True, 'threshold': 80})
            self.assertFalse(result['success'])
            self.assertIsNone(server.auto_process)
            self.assertEqual(server.auto_config['threshold'], 90)

    def handler(self, payload, origin=None):
        handler = server.ClaudioHandler.__new__(server.ClaudioHandler)
        handler.headers = Message()
        handler.headers['Host'] = 'localhost:5252'
        handler.headers['Content-Type'] = 'application/json'
        handler.headers['Content-Length'] = str(len(payload))
        if origin:
            handler.headers['Origin'] = origin
        handler.server = SimpleNamespace(server_port=5252)
        handler.path = '/api/alias'
        handler.rfile = io.BytesIO(payload)
        handler.send_json = Mock()
        return handler

    def test_malformed_json_returns_400(self):
        for body in [b'{', b'[]', b'null', b'"abc"']:
            handler = self.handler(body)
            handler.do_POST()
            self.assertEqual(handler.send_json.call_args.args[1], 400)

    def test_external_origin_rejected_without_mutation(self):
        handler = self.handler(b'{}', 'https://external.example')
        with patch.object(server, 'perform_action') as action:
            handler.do_POST()
            self.assertEqual(handler.send_json.call_args.args[1], 403)
            action.assert_not_called()

    def test_backend_operation_failure_returns_502(self):
        handler = self.handler(b'{"account":1,"alias":"test"}')
        with patch.object(server, 'run_cswap', return_value=(1, '', 'fallo')):
            handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[1], 502)


if __name__ == '__main__':
    unittest.main()
