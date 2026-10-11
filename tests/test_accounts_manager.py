import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from accounts import credentials, sessions, storage
from accounts.manager import AccountError
from sandbox import NOW, ManagerCase, oauth_account, oauth_credentials

API_KEY = 'sk-ant-api03-' + 'k' * 40 + 'ENDOFKEY0123456789ab'


class LinkTests(ManagerCase):
    def test_linking_registers_the_logged_in_account(self):
        self.link('ana@example.com', 'r1')
        data = self.accounts.registry.load()
        self.assertEqual(data['active'], 1)
        record = data['accounts']['1']
        self.assertEqual({key: record[key] for key in ('email', 'uuid', 'organizationUuid', 'organizationName')},
                         {'email': 'ana@example.com', 'uuid': 'uuid-ana@example.com', 'organizationUuid': '', 'organizationName': ''})
        self.assertEqual(record['oauthAccount'], oauth_account('ana@example.com', 'uuid-ana@example.com'))
        self.assertEqual(self.stored(1), self.live_credentials())

    def test_linking_the_same_account_again_updates_it(self):
        self.link('ana@example.com', 'r1', alias='Personal')
        added = self.record(1)['added']
        self.now += 500
        self.link('ana@example.com', 'r1-rotated')
        self.assertEqual(list(self.accounts.registry.load()['accounts']), ['1'])
        self.assertEqual(self.refresh_token(self.stored(1)), 'r1-rotated')
        self.assertEqual((self.record(1)['alias'], self.record(1)['added']), ('Personal', added))

    def test_organization_login_with_the_same_email_is_a_second_account(self):
        self.link('ana@example.com', 'r1')
        self.link('ana@example.com', 'r2', organization='org-9', name='Empresa')
        self.assertEqual(self.record(2)['organizationName'], 'Empresa')
        self.assertEqual(self.refresh_token(self.stored(1)), 'r1')

    def test_nothing_to_link_when_logged_out(self):
        with self.assertRaisesRegex(AccountError, 'sesión'):
            self.accounts.link_current()
        self.write_login(json.dumps({'claudeAiOauth': {}}), oauth_account('ana@example.com'))
        with self.assertRaisesRegex(AccountError, 'sesión'):
            self.accounts.link_current()
        self.assertEqual(self.accounts.registry.load()['accounts'], {})

    def test_login_that_belongs_to_someone_else_is_not_linked(self):
        # Claude Code is in the middle of /login: new credential, config still naming the old account.
        self.login('ana@example.com', 'r1')
        self.network.profiles['access-r1'] = {'account': {'uuid': 'uuid-otra', 'email': 'otra@example.com'}}
        with self.assertRaises(AccountError):
            self.accounts.link_current()
        self.assertEqual(self.accounts.registry.load()['accounts'], {})

    def test_api_key_session_is_not_linked_as_a_login(self):
        self.write_login('', oauth_account('ana@example.com'), primaryApiKey=API_KEY)
        with self.assertRaisesRegex(AccountError, 'API key'):
            self.accounts.link_current()

    def test_alias_is_free_text_but_unique(self):
        self.link('ana@example.com', 'r1', alias='  Cuenta personal  ')
        self.link('luis@example.com', 'r2')
        self.assertEqual(self.record(1)['alias'], 'Cuenta personal')
        with self.assertRaisesRegex(AccountError, 'alias'):
            self.accounts.set_alias(2, 'cuenta PERSONAL')
        for invalid in ('a' * 81, 'dos\nlíneas', 'nul\x00o'):
            with self.subTest(alias=invalid), self.assertRaises(AccountError):
                self.accounts.set_alias(2, invalid)
        self.accounts.set_alias(1, 'Cuenta personal')
        self.accounts.set_alias(2, 'Trabajo')
        self.accounts.set_alias(1, '')
        self.assertNotIn('alias', self.record(1))
        self.assertEqual(self.record(2)['alias'], 'Trabajo')

    def test_unknown_account_is_reported(self):
        for action in (lambda: self.accounts.set_alias(9, 'x'), lambda: self.accounts.set_disabled(9, True),
                       lambda: self.accounts.remove(9), lambda: self.accounts.switch(9)):
            with self.assertRaises(AccountError):
                action()

    def test_paused_account_is_remembered(self):
        self.link('ana@example.com', 'r1')
        self.accounts.set_disabled(1, True)
        self.assertTrue(self.record(1)['disabled'])
        self.accounts.set_disabled(1, False)
        self.assertNotIn('disabled', self.record(1))

    def test_unlinking_forgets_the_account_but_leaves_claude_logged_in(self):
        self.link('ana@example.com', 'r1')
        self.link('luis@example.com', 'r2')
        live = self.live_credentials()
        self.accounts.remove(2)
        data = self.accounts.registry.load()
        self.assertEqual((list(data['accounts']), data['active']), (['1'], None))
        self.assertEqual(self.stored(2), '')
        self.assertEqual(self.live_credentials(), live)

    def test_unlinking_removes_the_parallel_session_profile(self):
        self.link('ana@example.com', 'r1')
        profile = sessions.profile_dir(self.accounts.registry.root, '1', 'ana@example.com')
        sessions.prepare(profile, self.stored(1), self.record(1)['oauthAccount'])
        self.accounts.remove(1)
        self.assertFalse(profile.exists())

    def test_account_with_an_open_parallel_session_cannot_be_unlinked(self):
        self.link('ana@example.com', 'r1')
        profile = sessions.profile_dir(self.accounts.registry.root, '1', 'ana@example.com')
        (profile / 'sessions').mkdir(parents=True)
        (profile / 'sessions' / 'x.json').write_text(json.dumps({'pid': os.getpid()}))
        with self.assertRaisesRegex(AccountError, 'sesión paralela'):
            self.accounts.remove(1)
        self.assertIn('1', self.accounts.registry.load()['accounts'])


class TokenTests(ManagerCase):
    def test_setup_token_becomes_an_account_with_a_generated_name(self):
        self.accounts.link_token('sk-ant-oat01-setup')
        self.assertEqual(self.record(1)['email'], 'setup-token-1@token.local')
        self.assertEqual(json.loads(self.stored(1)), {'claudeAiOauth': {'accessToken': 'sk-ant-oat01-setup', 'scopes': ['user:inference']}})
        self.assertEqual(self.record(1)['oauthAccount']['emailAddress'], 'setup-token-1@token.local')
        self.assertNotIn('kind', self.record(1))

    def test_api_key_becomes_an_api_key_account(self):
        self.accounts.link_token(f'  {API_KEY}  ', email='equipo@example.com')
        self.assertEqual((self.record(1)['email'], self.record(1)['kind']), ('equipo@example.com', 'api_key'))
        self.assertEqual(self.stored(1), API_KEY)

    def test_new_token_for_the_same_name_replaces_the_old_one(self):
        self.accounts.link_token('sk-ant-oat01-first', email='equipo@example.com')
        self.accounts.link_token('sk-ant-oat01-second', email='equipo@example.com')
        self.assertEqual(list(self.accounts.registry.load()['accounts']), ['1'])
        self.assertEqual(json.loads(self.stored(1))['claudeAiOauth']['accessToken'], 'sk-ant-oat01-second')

    def test_invalid_token_requests_are_rejected(self):
        self.link('ana@example.com', 'r1')
        self.accounts.link_token('sk-ant-oat01-setup', email='equipo@example.com')
        cases = [dict(token='   '), dict(token='sk-ant-oat01-x', email='sin-arroba'), dict(token=API_KEY, email='equipo@example.com'),
                 dict(token='sk-ant-oat01-x', email='nuevo@example.com', slot=1), dict(token='sk-ant-oat01-x', slot=0)]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(AccountError):
                self.accounts.link_token(**case)
        self.assertEqual(list(self.accounts.registry.load()['accounts']), ['1', '2'])

    def test_token_can_be_placed_in_a_free_slot(self):
        self.accounts.link_token('sk-ant-oat01-setup', slot=5)
        self.assertEqual(self.record(5)['email'], 'setup-token-5@token.local')


class SwitchTests(ManagerCase):
    def setUp(self):
        super().setUp()
        self.link('ana@example.com', 'r1')
        self.link('luis@example.com', 'r2', mcpOAuth={'github': 'current'})

    def test_switching_logs_claude_code_in_as_the_target(self):
        self.accounts.switch(1)
        live = json.loads(self.live_credentials())
        self.assertEqual(live['claudeAiOauth']['refreshToken'], 'r1')
        self.assertEqual(live['mcpOAuth'], {'github': 'current'})
        self.assertEqual(self.live_config(), {'oauthAccount': oauth_account('ana@example.com', 'uuid-ana@example.com'), 'numStartups': 7, 'projects': {'/work': {}}})
        self.assertEqual(self.accounts.registry.load()['active'], 1)

    def test_login_renewed_by_claude_code_is_saved_before_leaving_the_account(self):
        self.login('luis@example.com', 'r2-rotated')
        self.accounts.switch(1)
        self.assertEqual(self.refresh_token(self.stored(2)), 'r2-rotated')
        self.assertEqual(self.refresh_token(self.accounts.registry.previous_credentials('2')), 'r2')

    def test_profile_details_updated_by_claude_code_come_back_with_the_account(self):
        config = self.live_config()
        config['oauthAccount']['displayName'] = 'Luis'
        (self.home / '.claude.json').write_text(json.dumps(config))
        self.accounts.switch(1)
        self.accounts.switch(2)
        self.assertEqual(self.live_config()['oauthAccount'], dict(oauth_account('luis@example.com', 'uuid-luis@example.com'), displayName='Luis'))

    def test_profile_details_of_a_foreign_login_are_not_saved_under_the_wrong_account(self):
        # The config names Luis, but the login is Ana's: Luis keeps his own details.
        self.login('luis@example.com', 'r1-new')
        self.network.profiles['access-r1-new'] = {'account': {'uuid': 'uuid-ana@example.com', 'email': 'ana@example.com'}}
        self.accounts.link_token('sk-ant-oat01-setup')
        self.accounts.switch(3)
        self.assertEqual(self.record(1)['oauthAccount'], oauth_account('ana@example.com', 'uuid-ana@example.com'))

    def test_login_of_another_linked_account_is_saved_under_its_real_owner(self):
        # The user ran /login as Ana outside Claudio; the config still names Luis.
        self.login('luis@example.com', 'r1-new')
        self.network.profiles['access-r1-new'] = {'account': {'uuid': 'uuid-ana@example.com', 'email': 'ana@example.com'}}
        self.accounts.link_token('sk-ant-oat01-setup')
        self.accounts.switch(3)
        self.assertEqual(self.refresh_token(self.stored(1)), 'r1-new')
        self.assertEqual(self.refresh_token(self.stored(2)), 'r2')

    def test_unlinked_login_is_never_overwritten(self):
        self.login('nueva@example.com', 'r9')
        before = (self.live_credentials(), self.live_config())
        with self.assertRaisesRegex(AccountError, 'vinc'):
            self.accounts.switch(1)
        self.assertEqual((self.live_credentials(), self.live_config()), before)
        self.assertEqual(self.refresh_token(self.stored(2)), 'r2')

    def test_switching_works_when_claude_code_is_logged_out(self):
        (self.claude / '.credentials.json').unlink()
        self.accounts.switch(1)
        self.assertEqual(self.refresh_token(self.live_credentials()), 'r1')

    def test_wiped_login_does_not_replace_the_saved_one(self):
        (self.claude / '.credentials.json').write_text(json.dumps({'claudeAiOauth': {}, 'mcpOAuth': {'github': 'current'}}))
        self.accounts.switch(1)
        self.assertEqual(self.refresh_token(self.stored(2)), 'r2')
        self.assertEqual(json.loads(self.live_credentials())['mcpOAuth'], {'github': 'current'})

    def test_expired_target_is_renewed_before_it_becomes_active(self):
        self.now += 4000
        self.login('luis@example.com', 'r2-live', expires_in=9000)
        self.network.tokens['r1'] = {'access_token': 'access-r1b', 'refresh_token': 'r1b', 'expires_in': 3600}
        self.accounts.switch(1)
        self.assertEqual(self.refresh_token(self.live_credentials()), 'r1b')
        self.assertEqual(self.refresh_token(self.stored(1)), 'r1b')

    def test_target_whose_login_died_is_not_activated(self):
        self.now += 4000
        self.login('luis@example.com', 'r2-live', expires_in=9000)
        before = self.live_credentials()
        with self.assertRaisesRegex(AccountError, 'iniciar sesión'):
            self.accounts.switch(1)
        self.assertEqual(self.live_credentials(), before)
        self.assertTrue(self.record(1)['needsLogin'])
        requests = len(self.network.requests)
        with self.assertRaises(AccountError):
            self.accounts.switch(1)
        self.assertEqual(len(self.network.requests), requests)

    def test_target_is_activated_when_renewal_cannot_be_reached(self):
        import urllib.error
        self.now += 4000
        self.login('luis@example.com', 'r2-live', expires_in=9000)
        self.network.tokens['r1'] = urllib.error.URLError('offline')
        self.accounts.switch(1)
        self.assertEqual(self.refresh_token(self.live_credentials()), 'r1')

    def test_relinking_revives_an_account_that_needed_login(self):
        self.now += 4000
        self.login('luis@example.com', 'r2-live', expires_in=9000)
        with self.assertRaises(AccountError):
            self.accounts.switch(1)
        self.link('ana@example.com', 'r1-fresh', expires_in=9000)
        self.assertNotIn('needsLogin', self.record(1))

    def test_account_without_saved_login_cannot_be_activated(self):
        self.accounts.registry.delete_credentials('1')
        with self.assertRaisesRegex(AccountError, 'credenciales'):
            self.accounts.switch(1)
        self.assertEqual(self.refresh_token(self.live_credentials()), 'r2')

    def test_failure_halfway_restores_the_previous_login(self):
        before = (self.live_credentials(), self.live_config())
        with patch.object(credentials, 'update_config', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.accounts.switch(1)
        self.assertEqual((self.live_credentials(), self.live_config()), before)
        self.assertEqual(self.accounts.registry.load()['active'], 2)

    def test_failure_after_the_config_changed_restores_it_too(self):
        before = (self.live_credentials(), self.live_config())
        with patch.object(self.accounts.registry, 'save', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.accounts.switch(1)
        self.assertEqual((self.live_credentials(), self.live_config()), before)

    def test_login_that_changes_during_the_switch_aborts_it(self):
        real = storage.claude_locks

        @contextmanager
        def claude_refreshes_first():
            self.login('luis@example.com', 'r2-rotated-meanwhile')
            with real():
                yield

        with patch.object(storage, 'claude_locks', claude_refreshes_first), self.assertRaisesRegex(AccountError, 'cambió'):
            self.accounts.switch(1)
        self.assertEqual(self.refresh_token(self.live_credentials()), 'r2-rotated-meanwhile')
        self.assertEqual(self.refresh_token(self.stored(2)), 'r2')

    def test_switching_to_the_active_account_changes_nothing(self):
        before = (self.live_credentials(), self.live_config())
        self.accounts.switch(2)
        self.assertEqual((self.live_credentials(), self.live_config()), before)

    def test_account_with_an_open_parallel_session_cannot_become_the_main_one(self):
        profile = sessions.profile_dir(self.accounts.registry.root, '1', 'ana@example.com')
        (profile / 'sessions').mkdir(parents=True)
        (profile / 'sessions' / 'x.json').write_text(json.dumps({'pid': os.getpid()}))
        with self.assertRaisesRegex(AccountError, 'sesión paralela'):
            self.accounts.switch(1)

    def test_login_renewed_inside_a_parallel_session_is_the_one_activated(self):
        profile = sessions.profile_dir(self.accounts.registry.root, '1', 'ana@example.com')
        sessions.prepare(profile, self.stored(1), self.record(1)['oauthAccount'])
        (profile / '.credentials.json').write_text(oauth_credentials('r1-from-session', 'access-s', 7200, NOW))
        self.accounts.switch(1)
        self.assertEqual(self.refresh_token(self.live_credentials()), 'r1-from-session')

    def test_api_key_account_replaces_the_login_and_back(self):
        self.accounts.link_token(API_KEY, email='equipo@example.com')
        self.accounts.switch(3)
        self.assertEqual(credentials.read_active(), API_KEY)
        self.assertEqual(self.live_config()['oauthAccount']['emailAddress'], 'equipo@example.com')
        self.assertEqual(self.refresh_token(self.stored(2)), 'r2')
        self.accounts.switch(1)
        self.assertEqual(self.refresh_token(credentials.read_active()), 'r1')
        self.assertNotIn('primaryApiKey', self.live_config())
        self.assertEqual(self.stored(3), API_KEY)


if __name__ == '__main__':
    unittest.main()
