import io
import json
import tempfile
import threading
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server
from accounts.rotation import Rotation
from sandbox import ManagerCase, quota

API_KEY = 'sk-ant-api03-' + 'k' * 40 + 'ENDOFKEY0123456789ab'


class ServerTests(ManagerCase):
    """The API against the real account manager, inside a temporary home."""

    def setUp(self):
        super().setUp()
        self.rotation = Rotation(self.accounts, interval=30)
        self.addCleanup(self.rotation.stop)
        # A fresh lock per test: a request left blocked by one test must not hang the others.
        for name, value in (('manager', self.accounts), ('rotation', self.rotation), ('command_lock', threading.Lock())):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.link('ana@example.com', 'r1')
        self.link('luis@example.com', 'r2')

    def test_invalid_account_never_reaches_the_accounts(self):
        before = self.accounts.registry.load()
        for account in [None, True, 0, '-1', '--help', 'a; touch /tmp/no', []]:
            with self.subTest(account=account), self.assertRaises(ValueError):
                server.perform_action('/api/alias', {'account': account, 'alias': 'test'})
        self.assertEqual(self.accounts.registry.load(), before)

    def test_alias_is_saved_and_a_duplicate_is_explained(self):
        self.assertTrue(server.perform_action('/api/alias', {'account': 1, 'alias': 'Personal'})['success'])
        result = server.perform_action('/api/alias', {'account': 2, 'alias': 'personal'})
        self.assertFalse(result['success'])
        self.assertIn('alias', result['error'])
        self.assertEqual(self.record(1)['alias'], 'Personal')

    def test_remove_unlinks_the_account(self):
        self.assertTrue(server.perform_action('/api/remove', {'account': 1})['success'])
        self.assertEqual(list(self.accounts.registry.load()['accounts']), ['2'])

    def test_pause_requires_boolean(self):
        with self.assertRaises(ValueError):
            server.perform_action('/api/toggle-account', {'account': 1, 'disable': 'false'})
        self.assertTrue(server.perform_action('/api/toggle-account', {'account': 1, 'disable': True})['success'])
        self.assertTrue(self.record(1)['disabled'])

    def test_switch_changes_the_account_claude_code_uses(self):
        self.assertTrue(server.perform_action('/api/switch', {'target': 1})['success'])
        self.assertEqual(self.live_config()['oauthAccount']['emailAddress'], 'ana@example.com')
        self.assertTrue(server.perform_action('/api/switch', {'strategy': 'next-available'})['success'])
        self.assertEqual(self.live_config()['oauthAccount']['emailAddress'], 'luis@example.com')
        with self.assertRaises(ValueError):
            server.perform_action('/api/switch', {'strategy': 'random'})

    def test_current_session_is_linked_with_its_alias(self):
        self.login('eva@example.com', 'r3')
        self.assertTrue(server.perform_action('/api/add-current', {'alias': 'Eva'})['success'])
        self.assertEqual((self.record(3)['email'], self.record(3)['alias']), ('eva@example.com', 'Eva'))

    def test_token_is_linked_with_its_alias_and_never_echoed(self):
        result = server.perform_action('/api/add-token', {'token': API_KEY, 'email': 'equipo@example.com', 'alias': 'Equipo'})
        self.assertTrue(result['success'])
        self.assertNotIn(API_KEY, json.dumps(result))
        self.assertEqual((self.record(3)['alias'], self.record(3)['kind']), ('Equipo', 'api_key'))

    def test_rejected_token_is_explained_without_echoing_it(self):
        result = server.perform_action('/api/add-token', {'token': API_KEY, 'email': 'ana@example.com'})
        self.assertFalse(result['success'])
        self.assertNotIn(API_KEY, json.dumps(result))
        for payload in ({'token': 'una\ndos'}, {'token': 'x', 'alias': 'sin correo'}, {'token': ''}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                server.perform_action('/api/add-token', payload)

    def test_status_describes_accounts_rotation_and_projects(self):
        self.network.usage['access-r2'] = quota(42, 68)
        with patch.object(server, 'get_recent_projects', return_value=[]):
            result = server.get_system_status()
        self.assertEqual(result['status']['active']['email'], 'luis@example.com')
        self.assertEqual([row['number'] for row in result['list']['accounts']], [1, 2])
        self.assertEqual(result['list']['accounts'][1]['usage']['fiveHour']['pct'], 42)
        self.assertEqual((result['auto_running'], result['auto_config'], result['projects']), (False, {'threshold': 90, 'strategy': 'best'}, []))

    def test_unknown_quota_remains_unknown(self):
        self.network.usage['access-r2'] = urllib.error.URLError('offline')
        result = server.get_system_status()
        self.assertIsNone(result['list']['accounts'][1]['usage'])

    def test_status_failure_not_reported_as_empty_accounts(self):
        self.accounts.registry.file.write_text('{"accounts": {"1"')
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

    def test_parallel_session_starts_claude_inside_the_account_profile(self):
        with patch.object(server, 'launch_terminal', return_value=(True, None)) as launch:
            result = server.perform_action('/api/open-terminal', {'mode': 'parallel', 'account': 1, 'cwd': "/tmp/John's project"})
        self.assertTrue(result['success'])
        profile = self.accounts.registry.root / 'sessions' / '1-ana_example.com'
        launch.assert_called_once_with(['claude', '--resume'], "/tmp/John's project", {'CLAUDE_CONFIG_DIR': str(profile)})
        self.assertTrue((profile / '.credentials.json').exists())

    def test_parallel_session_of_the_active_account_uses_the_main_profile(self):
        with patch.object(server, 'launch_terminal', return_value=(True, None)) as launch:
            server.perform_action('/api/open-terminal', {'mode': 'parallel', 'account': 2})
        launch.assert_called_once_with(['claude', '--resume'], None, None)

    def test_parallel_session_that_cannot_start_opens_no_terminal(self):
        server.perform_action('/api/add-token', {'token': API_KEY})
        with patch.object(server, 'launch_terminal') as launch:
            result = server.perform_action('/api/open-terminal', {'mode': 'parallel', 'account': 3})
        self.assertFalse(result['success'])
        self.assertIn('API key', result['error'])
        launch.assert_not_called()

    def test_plain_sessions_start_claude_in_the_main_profile(self):
        with patch.object(server, 'launch_terminal', return_value=(True, None)) as launch:
            server.perform_action('/api/open-terminal', {'mode': 'new', 'cwd': '/work'})
            server.perform_action('/api/open-terminal', {'mode': 'resume'})
        self.assertEqual([call.args for call in launch.call_args_list], [(['claude'], '/work', None), (['claude', '--resume'], None, None)])

    def started(self, system, **which):
        """What launch_terminal hands to the operating system when starting as another account."""
        environment = {'CLAUDE_CONFIG_DIR': "/data/it's here/1-ana"}
        with tempfile.TemporaryDirectory() as directory, patch.object(server.platform, 'system', return_value=system), \
                patch.object(server.shutil, 'which', side_effect=lambda name: which.get(name)), patch.object(server.subprocess, 'Popen') as launch:
            self.assertEqual(server.launch_terminal(['claude', '--resume'], directory, environment), (True, None))
        return launch.call_args.args[0]

    def test_linux_terminal_sets_the_account_only_for_claude(self):
        arguments = self.started('Linux', konsole='/bin/konsole')
        self.assertEqual(arguments[-1], "env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u CLAUDE_CODE_OAUTH_TOKEN -u CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR "
                                        "-u CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR 'CLAUDE_CONFIG_DIR=/data/it'\"'\"'s here/1-ana' claude --resume; exec bash")

    def test_macos_terminal_sets_the_account(self):
        script = self.started('Darwin')[-1]
        self.assertIn("'CLAUDE_CONFIG_DIR=/data/it'\\\"'\\\"'s here/1-ana' claude --resume", script)
        self.assertIn('-u ANTHROPIC_API_KEY', script)

    def test_windows_terminal_sets_the_account(self):
        command = self.started('Windows', pwsh='pwsh', wt='wt')[-1]
        self.assertTrue(command.endswith("$env:CLAUDE_CONFIG_DIR = '/data/it''s here/1-ana'; & 'claude' '--resume'"))
        self.assertIn('Remove-Item Env:ANTHROPIC_API_KEY', command)

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
        for threshold in [True, '90', 99, 69, 90.5]:
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                server.toggle_auto({'enable': True, 'threshold': threshold})
        with self.assertRaises(ValueError):
            server.toggle_auto({'enable': True, 'threshold': 80, 'strategy': 'random'})
        self.assertFalse(self.rotation.running)

    def test_auto_starts_and_stops_the_rotation(self):
        result = server.toggle_auto({'enable': True, 'threshold': 80, 'strategy': 'consume-first'})
        self.assertEqual(result, {'success': True, 'auto_running': True, 'auto_config': {'threshold': 80, 'strategy': 'consume-first'}})
        self.assertTrue(server.get_system_status()['auto_running'])
        result = server.toggle_auto({'enable': False})
        self.assertEqual((result['auto_running'], result['auto_config']['threshold']), (False, 80))

    def test_auto_settings_cannot_change_while_running(self):
        server.toggle_auto({'enable': True, 'threshold': 80})
        with self.assertRaisesRegex(ValueError, 'Detén'):
            server.toggle_auto({'enable': True, 'threshold': 85})
        self.assertTrue(server.toggle_auto({'enable': True, 'threshold': 80})['auto_running'])
        self.assertEqual(self.rotation.config['threshold'], 80)

    def test_every_action_answers_through_the_api(self):
        # Requests are serialized by one lock; an action that takes it again would never answer.
        requests = [('/api/toggle-auto', b'{"enable":true,"threshold":85}'), ('/api/toggle-auto', b'{"enable":false}'),
                    ('/api/alias', b'{"account":1,"alias":"Personal"}'), ('/api/toggle-account', b'{"account":1,"disable":true}'),
                    ('/api/switch', b'{"target":1}'), ('/api/add-current', b'{}'), ('/api/remove', b'{"account":1}')]
        for path, payload in requests:
            handler = self.handler(payload, path=path)
            worker = threading.Thread(target=handler.do_POST, daemon=True)
            worker.start()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), f'{path} {payload} quedó bloqueada')
            self.assertEqual(handler.send_json.call_args.args[1], 200, (path, payload))

    def handler(self, payload, origin=None, path='/api/alias'):
        handler = server.ClaudioHandler.__new__(server.ClaudioHandler)
        handler.headers = Message()
        handler.headers['Host'] = 'localhost:5252'
        handler.headers['Content-Type'] = 'application/json'
        handler.headers['Content-Length'] = str(len(payload))
        if origin:
            handler.headers['Origin'] = origin
        handler.server = SimpleNamespace(server_port=5252)
        handler.path = path
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
        with patch.object(self.accounts.registry, 'save', side_effect=OSError('disco lleno')):
            handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[1], 502)

    def test_problem_the_user_can_fix_returns_its_message(self):
        handler = self.handler(b'{"account":9,"alias":"test"}')
        handler.do_POST()
        body, status = handler.send_json.call_args.args
        self.assertEqual((status, body['success']), (502, False))
        self.assertIn('vinculada', body['error'])


if __name__ == '__main__':
    unittest.main()
