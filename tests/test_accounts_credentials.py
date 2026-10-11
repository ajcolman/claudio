import json
import os
import stat
import unittest

from accounts import credentials, keychain
from sandbox import Sandbox, oauth_account, oauth_credentials, use_macos

API_KEY = 'sk-ant-api03-' + 'k' * 40 + 'ENDOFKEY0123456789ab'


class CredentialShapeTests(unittest.TestCase):
    def test_only_a_bare_api_key_counts_as_api_key(self):
        self.assertTrue(credentials.is_api_key(API_KEY + '\n'))
        self.assertFalse(credentials.is_api_key(oauth_credentials()))
        self.assertFalse(credentials.is_api_key('sk-ant-oat01-setup-token'))
        self.assertFalse(credentials.is_api_key(''))

    def test_wiped_login_is_not_a_login(self):
        self.assertTrue(credentials.has_login(oauth_credentials()))
        self.assertTrue(credentials.has_login(API_KEY))
        self.assertFalse(credentials.has_login(json.dumps({'claudeAiOauth': {'scopes': []}, 'mcpOAuth': {'x': 1}})))
        self.assertFalse(credentials.has_login('{"half":'))
        self.assertFalse(credentials.has_login(''))

    def test_switching_keeps_this_machines_integrations_and_the_targets_login(self):
        live = oauth_credentials('live-refresh', mcpOAuth={'github': 'live'})
        target = oauth_credentials('target-refresh', mcpOAuth={'github': 'stale'}, pluginSecrets={'old': 1}, trustedDeviceToken='device-of-target')
        merged = json.loads(credentials.with_shared_fields(target, live))
        self.assertEqual(merged['claudeAiOauth']['refreshToken'], 'target-refresh')
        self.assertEqual(merged['mcpOAuth'], {'github': 'live'})
        self.assertNotIn('pluginSecrets', merged)
        self.assertEqual(merged['trustedDeviceToken'], 'device-of-target')

    def test_unreadable_live_credential_leaves_the_target_untouched(self):
        target = oauth_credentials(mcpOAuth={'github': 'kept'})
        self.assertEqual(credentials.with_shared_fields(target, ''), target)
        self.assertEqual(credentials.with_shared_fields(API_KEY, oauth_credentials(mcpOAuth={'x': 1})), API_KEY)


class FileBackendTests(Sandbox):
    def test_reads_the_login_claude_code_left_on_disk(self):
        self.assertEqual(credentials.read_active(), '')
        self.write_login(oauth_credentials(), oauth_account('ana@example.com'))
        self.assertEqual(credentials.read_active(), oauth_credentials())

    @unittest.skipIf(os.name == 'nt', 'Windows no usa permisos POSIX.')
    def test_written_login_is_private(self):
        credentials.write_active(oauth_credentials())
        self.assertEqual(stat.S_IMODE((self.claude / '.credentials.json').stat().st_mode), 0o600)

    def test_activating_a_login_removes_a_leftover_api_key_and_nothing_else(self):
        self.write_login('', oauth_account('ana@example.com'), primaryApiKey=API_KEY, projects={'/work': {}})
        credentials.write_active(oauth_credentials('new'))
        self.assertEqual(json.loads(self.live_credentials())['claudeAiOauth']['refreshToken'], 'new')
        self.assertEqual(self.live_config(), {'oauthAccount': oauth_account('ana@example.com'), 'numStartups': 7, 'projects': {'/work': {}}})

    def test_activating_an_api_key_approves_it_and_removes_the_login(self):
        self.write_login(oauth_credentials(), oauth_account('ana@example.com'), customApiKeyResponses={'approved': ['older-key-tail'], 'rejected': ['no']})
        credentials.write_active(API_KEY)
        self.assertFalse((self.claude / '.credentials.json').exists())
        config = self.live_config()
        self.assertEqual(config['primaryApiKey'], API_KEY)
        self.assertEqual(config['customApiKeyResponses'], {'approved': ['older-key-tail', 'ENDOFKEY0123456789ab'], 'rejected': ['no']})
        self.assertEqual(config['numStartups'], 7)
        self.assertEqual(credentials.read_active(), API_KEY)

    def test_unreadable_config_is_never_overwritten(self):
        (self.home / '.claude.json').write_text('{"projects": {"/work"')
        with self.assertRaises(credentials.CredentialError):
            credentials.write_active(API_KEY)
        self.assertEqual((self.home / '.claude.json').read_text(), '{"projects": {"/work"')


class KeychainBackendTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.keychain = use_macos(self)

    def test_keychain_login_wins_over_a_stale_file(self):
        self.write_login(oauth_credentials('from-file'), oauth_account('ana@example.com'))
        self.keychain.items[('Claude Code-credentials', 'tester')] = oauth_credentials('from-keychain')
        self.assertEqual(credentials.read_active(), oauth_credentials('from-keychain'))

    def test_locked_keychain_falls_back_to_the_file(self):
        self.write_login(oauth_credentials('from-file'), oauth_account('ana@example.com'))
        self.keychain.locked = True
        self.assertEqual(credentials.read_active(), oauth_credentials('from-file'))

    def test_login_goes_to_the_keychain_without_creating_a_plaintext_file(self):
        credentials.write_active(oauth_credentials('new'))
        self.assertEqual(self.keychain.items[('Claude Code-credentials', 'tester')], oauth_credentials('new'))
        self.assertFalse((self.claude / '.credentials.json').exists())

    def test_existing_file_is_refreshed_so_a_running_claude_reloads(self):
        self.write_login(oauth_credentials('old'), oauth_account('ana@example.com'))
        credentials.write_active(oauth_credentials('new'))
        self.assertEqual(self.live_credentials(), oauth_credentials('new'))

    def test_locked_keychain_writes_the_file_instead(self):
        self.keychain.locked = True
        credentials.write_active(oauth_credentials('new'))
        self.assertEqual(self.live_credentials(), oauth_credentials('new'))

    def test_large_credential_survives_the_round_trip(self):
        large = oauth_credentials(mcpOAuth={f'plugin-{n}': 'x' * 200 for n in range(40)})
        credentials.write_active(large)
        self.assertEqual(credentials.read_active(), large)

    def test_api_key_lives_in_its_own_keychain_item(self):
        self.write_login('', oauth_account('ana@example.com'))
        self.keychain.items[('Claude Code-credentials', 'tester')] = oauth_credentials()
        credentials.write_active(API_KEY)
        self.assertEqual(self.keychain.items, {('Claude Code', 'tester'): API_KEY})
        self.assertNotIn('primaryApiKey', self.live_config())
        self.assertEqual(credentials.read_active(), API_KEY)

    def test_relocated_profile_uses_its_own_keychain_item(self):
        os.environ['CLAUDE_CONFIG_DIR'] = '/claudio-test-profile'
        self.keychain.items[('Claude Code-credentials', 'tester')] = oauth_credentials('default-profile')
        self.keychain.items[('Claude Code-credentials-6da239d1', 'tester')] = oauth_credentials('relocated')
        self.assertEqual(credentials.read_active(), oauth_credentials('relocated'))

    def test_keychain_account_falls_back_to_the_system_user(self):
        del os.environ['USER']
        self.assertTrue(keychain.account_name())


if __name__ == '__main__':
    unittest.main()
