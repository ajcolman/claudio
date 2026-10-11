import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from accounts import sessions
from sandbox import Sandbox, oauth_account, oauth_credentials, use_macos

ACCOUNT = oauth_account('ana@example.com', 'uuid-1')


def dead_pid():
    process = subprocess.Popen([sys.executable, '-c', 'pass'])
    process.wait()
    return process.pid


class ProfileTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.profile = sessions.profile_dir(self.home / 'store', '2', 'ana@example.com')

    def running(self, pid):
        (self.profile / 'sessions').mkdir(parents=True, exist_ok=True)
        (self.profile / 'sessions' / f'{pid}.json').write_text(json.dumps({'pid': pid, 'sessionId': 'x'}))

    def test_profile_folder_is_named_after_the_account(self):
        self.assertEqual(self.profile, self.home / 'store' / 'sessions' / '2-ana_example.com')
        self.assertEqual(sessions.profile_dir(Path('/s'), '7', 'a+b/ñ@x.io').name, '7-a_b___x.io')

    def test_new_profile_is_logged_in_as_the_account(self):
        (self.home / '.claude.json').write_text(json.dumps({'theme': 'light', 'mcpServers': {'docs': {'command': 'docs-mcp'}}, 'oauthAccount': oauth_account('otra@example.com')}))
        sessions.prepare(self.profile, oauth_credentials('r2'), ACCOUNT)
        self.assertEqual((self.profile / '.credentials.json').read_text(), oauth_credentials('r2'))
        self.assertEqual(json.loads((self.profile / '.claude.json').read_text()),
                         {'oauthAccount': ACCOUNT, 'hasCompletedOnboarding': True, 'theme': 'light', 'mcpServers': {'docs': {'command': 'docs-mcp'}}})

    @unittest.skipIf(os.name == 'nt', 'Windows no usa permisos POSIX.')
    def test_profile_and_its_login_are_private(self):
        sessions.prepare(self.profile, oauth_credentials(), ACCOUNT)
        self.assertEqual(stat.S_IMODE(self.profile.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.profile / '.credentials.json').stat().st_mode), 0o600)

    def test_profile_keeps_its_own_settings_between_launches(self):
        sessions.prepare(self.profile, oauth_credentials('r2'), ACCOUNT)
        config = json.loads((self.profile / '.claude.json').read_text())
        (self.profile / '.claude.json').write_text(json.dumps(dict(config, theme='daltonized', numStartups=4)))
        sessions.prepare(self.profile, oauth_credentials('r3'), ACCOUNT)
        config = json.loads((self.profile / '.claude.json').read_text())
        self.assertEqual((config['theme'], config['numStartups']), ('daltonized', 4))
        self.assertEqual((self.profile / '.credentials.json').read_text(), oauth_credentials('r3'))

    @unittest.skipIf(os.name == 'nt', 'Los enlaces simbólicos requieren permisos especiales en Windows.')
    def test_settings_and_history_are_shared_with_the_main_profile(self):
        (self.claude / 'settings.json').write_text('{"model": "opus"}')
        (self.claude / 'projects').mkdir()
        (self.claude / 'history.jsonl').write_text('{}\n')
        sessions.prepare(self.profile, oauth_credentials(), ACCOUNT)
        for name in ('settings.json', 'projects', 'history.jsonl'):
            with self.subTest(name=name):
                self.assertTrue((self.profile / name).is_symlink())
                self.assertEqual((self.profile / name).resolve(), self.claude / name)
        self.assertFalse((self.profile / 'skills').exists())
        (self.profile / 'projects' / 'conversation.jsonl').write_text('shared')
        self.assertEqual((self.claude / 'projects' / 'conversation.jsonl').read_text(), 'shared')

    @unittest.skipIf(os.name == 'nt', 'Los enlaces simbólicos requieren permisos especiales en Windows.')
    def test_data_already_in_the_profile_is_never_replaced(self):
        (self.claude / 'projects').mkdir()
        (self.profile / 'projects').mkdir(parents=True)
        (self.profile / 'projects' / 'own.jsonl').write_text('mine')
        (self.claude / 'settings.json').write_text('{"model": "opus"}')
        (self.profile / 'settings.json').write_text('{"model": "own"}')
        sessions.prepare(self.profile, oauth_credentials(), ACCOUNT)
        self.assertFalse((self.profile / 'projects').is_symlink())
        self.assertEqual((self.profile / 'projects' / 'own.jsonl').read_text(), 'mine')
        self.assertEqual((self.profile / 'settings.json').read_text(), '{"model": "own"}')

    def test_without_symlinks_settings_are_copied_and_refreshed(self):
        (self.claude / 'settings.json').write_text('{"model": "opus"}')
        (self.claude / 'skills').mkdir()
        (self.claude / 'skills' / 'a.md').write_text('skill')
        (self.claude / 'projects').mkdir()
        with patch.object(Path, 'symlink_to', side_effect=OSError('privilege not held')):
            sessions.prepare(self.profile, oauth_credentials(), ACCOUNT)
            (self.claude / 'settings.json').write_text('{"model": "sonnet"}')
            sessions.prepare(self.profile, oauth_credentials(), ACCOUNT)
        self.assertEqual((self.profile / 'settings.json').read_text(), '{"model": "sonnet"}')
        self.assertEqual((self.profile / 'skills' / 'a.md').read_text(), 'skill')
        # A copied history would fork the conversations instead of sharing them.
        self.assertFalse((self.profile / 'projects').exists())

    def test_profile_in_use_is_left_alone(self):
        sessions.prepare(self.profile, oauth_credentials('in-use'), ACCOUNT)
        self.running(os.getpid())
        before = (self.profile / '.claude.json').read_text()
        sessions.prepare(self.profile, oauth_credentials('newer'), oauth_account('otra@example.com'))
        self.assertEqual((self.profile / '.credentials.json').read_text(), oauth_credentials('in-use'))
        self.assertEqual((self.profile / '.claude.json').read_text(), before)

    def test_only_a_running_claude_makes_the_profile_live(self):
        self.assertFalse(sessions.is_live(self.profile))
        self.running(dead_pid())
        self.assertFalse(sessions.is_live(self.profile))
        self.running(os.getpid())
        self.assertTrue(sessions.is_live(self.profile))

    def test_unreadable_session_file_counts_as_live(self):
        (self.profile / 'sessions').mkdir(parents=True)
        (self.profile / 'sessions' / 'x.json').write_text('{"pid":')
        self.assertTrue(sessions.is_live(self.profile))

    def test_removed_profile_leaves_shared_data_intact(self):
        (self.claude / 'projects').mkdir()
        (self.claude / 'projects' / 'keep.jsonl').write_text('keep')
        sessions.prepare(self.profile, oauth_credentials(), ACCOUNT)
        sessions.remove(self.profile)
        sessions.remove(self.profile)
        self.assertFalse(self.profile.exists())
        self.assertEqual((self.claude / 'projects' / 'keep.jsonl').read_text(), 'keep')

    def test_launch_points_claude_at_the_profile(self):
        self.assertEqual(sessions.environment(self.profile), {'CLAUDE_CONFIG_DIR': str(self.profile)})


class MacosProfileTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.keychain = use_macos(self)
        self.profile = sessions.profile_dir(self.home / 'store', '2', 'ana@example.com')
        from accounts import credentials
        self.item = (credentials.profile_service(self.profile), 'tester')

    def test_login_rotated_by_claude_is_read_from_the_profile_keychain_item(self):
        sessions.prepare(self.profile, oauth_credentials('seeded'), ACCOUNT)
        self.keychain.items[self.item] = oauth_credentials('rotated-by-claude')
        self.assertEqual(sessions.stored_credentials(self.profile), oauth_credentials('rotated-by-claude'))

    def test_reseeding_removes_the_stale_keychain_item(self):
        self.keychain.items[self.item] = oauth_credentials('stale')
        sessions.prepare(self.profile, oauth_credentials('fresh'), ACCOUNT)
        self.assertNotIn(self.item, self.keychain.items)
        self.assertEqual(sessions.stored_credentials(self.profile), oauth_credentials('fresh'))

    def test_removing_the_profile_removes_its_keychain_item(self):
        self.keychain.items[self.item] = oauth_credentials('stale')
        self.profile.mkdir(parents=True)
        sessions.remove(self.profile)
        self.assertEqual(self.keychain.items, {})


if __name__ == '__main__':
    unittest.main()
