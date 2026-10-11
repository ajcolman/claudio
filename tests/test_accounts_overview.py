import json
import os
import unittest
import urllib.error

from accounts import sessions
from accounts.manager import AccountError
from sandbox import NOW, ManagerCase, oauth_credentials, quota

API_KEY = 'sk-ant-api03-' + 'k' * 40 + 'ENDOFKEY0123456789ab'
EARLY, LATE = '2099-01-02T00:00:00+00:00', '2099-01-06T00:00:00+00:00'


class OverviewTests(ManagerCase):
    def rows(self):
        return {row['number']: row for row in self.accounts.overview()['list']['accounts']}

    def test_nothing_linked_and_logged_out(self):
        self.assertEqual(self.accounts.overview(), {'status': {'schemaVersion': 1, 'active': None},
                                                    'list': {'schemaVersion': 1, 'activeAccountNumber': None, 'accounts': []}})

    def test_login_that_is_not_linked_is_shown_as_unmanaged(self):
        self.login('ana@example.com', 'r1')
        self.assertEqual(self.accounts.overview()['status'], {'schemaVersion': 1, 'active': {'email': 'ana@example.com', 'managed': False}})

    def test_every_account_is_described_with_its_quota(self):
        self.link('ana@example.com', 'r1', alias='Personal')
        self.link('ana@empresa.com', 'r2', organization='org-2', name='Empresa')
        self.accounts.set_disabled(2, True)
        self.network.usage['access-r1'] = quota(42, 68, reset=EARLY)
        self.network.usage['access-r2'] = quota(five_hour=91.5)
        overview = self.accounts.overview()
        first = {'number': 1, 'email': 'ana@example.com', 'organizationName': '', 'organizationUuid': '', 'isOrganization': False, 'active': False, 'usageStatus': 'ok',
                 'usage': {'fiveHour': {'pct': 42, 'resetsAt': EARLY}, 'sevenDay': {'pct': 68, 'resetsAt': EARLY}}, 'alias': 'Personal'}
        second = {'number': 2, 'email': 'ana@empresa.com', 'organizationName': 'Empresa', 'organizationUuid': 'org-2', 'isOrganization': True, 'active': True, 'usageStatus': 'ok',
                  'usage': {'fiveHour': {'pct': 91.5, 'resetsAt': '2099-01-01T00:00:00+00:00'}}, 'disabled': True}
        self.assertEqual(overview['list'], {'schemaVersion': 1, 'activeAccountNumber': 2, 'accounts': [first, second]})
        self.assertEqual(overview['status'], {'schemaVersion': 1, 'active': {'number': 2, 'email': 'ana@empresa.com', 'organizationName': 'Empresa', 'organizationUuid': 'org-2',
                                                                               'isOrganization': True, 'managed': True, 'usageStatus': 'ok', 'usage': second['usage']}})

    def test_quota_is_asked_at_most_once_a_minute(self):
        self.link('ana@example.com', 'r1')
        self.network.usage['access-r1'] = quota(10, 20)
        self.accounts.overview()
        self.now += 59
        self.network.usage['access-r1'] = quota(11, 21)
        self.assertEqual(self.rows()[1]['usage']['fiveHour']['pct'], 10)
        self.assertEqual(len(self.network.calls('/oauth/usage')), 1)
        self.now += 2
        self.assertEqual(self.rows()[1]['usage']['fiveHour']['pct'], 11)

    def test_expired_login_of_an_idle_account_is_renewed_once_and_saved(self):
        self.link('ana@example.com', 'r1')
        self.link('luis@example.com', 'r2', expires_in=90000)
        self.now += 4000
        self.network.tokens['r1'] = {'access_token': 'access-r1b', 'refresh_token': 'r1b', 'expires_in': 3600}
        self.network.usage['access-r1b'] = quota(5, 6)
        self.assertEqual(self.rows()[1]['usage']['sevenDay']['pct'], 6)
        self.assertEqual(self.refresh_token(self.stored(1)), 'r1b')
        self.now += 120
        self.rows()
        self.assertEqual(len(self.network.calls('/oauth/token')), 1)

    def test_expired_login_of_the_active_account_is_left_to_claude_code(self):
        self.link('ana@example.com', 'r1')
        self.now += 4000
        self.assertEqual((self.rows()[1]['usageStatus'], self.rows()[1]['usage']), ('token_expired', None))
        self.assertEqual(self.network.calls('/oauth/token'), [])

    def test_login_used_by_an_open_parallel_session_is_not_renewed(self):
        self.link('ana@example.com', 'r1')
        self.link('luis@example.com', 'r2', expires_in=90000)
        profile = sessions.profile_dir(self.accounts.registry.root, '1', 'ana@example.com')
        (profile / 'sessions').mkdir(parents=True)
        (profile / 'sessions' / 'x.json').write_text(json.dumps({'pid': os.getpid()}))
        self.now += 4000
        self.assertEqual(self.rows()[1]['usageStatus'], 'token_expired')
        self.assertEqual(self.network.calls('/oauth/token'), [])

    def test_dead_login_is_flagged_and_never_retried(self):
        self.link('ana@example.com', 'r1')
        self.link('luis@example.com', 'r2', expires_in=90000)
        self.now += 4000
        self.assertEqual(self.rows()[1]['usageStatus'], 'relogin_required')
        self.now += 4000
        self.assertEqual(self.rows()[1]['usageStatus'], 'relogin_required')
        self.assertEqual(len(self.network.calls('/oauth/token')), 1)

    def test_failed_query_keeps_the_last_known_figures(self):
        self.link('ana@example.com', 'r1')
        self.network.usage['access-r1'] = quota(10, 20)
        self.accounts.overview()
        self.now += 61
        self.network.usage['access-r1'] = urllib.error.URLError('offline')
        row = self.rows()[1]
        self.assertEqual((row['usageStatus'], row['usage']['fiveHour']['pct']), ('unavailable', 10))

    def test_failing_service_is_asked_less_and_less_often(self):
        self.link('ana@example.com', 'r1')
        self.network.usage['access-r1'] = (429, '{}')
        asked = []
        for _ in range(40):
            self.rows()
            asked.append(len(self.network.calls('/oauth/usage')))
            self.now += 30
        self.assertEqual(asked[:4], [1, 2, 2, 3])
        self.assertLess(asked[-1], 12)

    def test_accounts_without_a_quota_say_why(self):
        self.link('ana@example.com', 'r1')
        self.link('luis@example.com', 'r2')
        self.accounts.link_token(API_KEY)
        self.accounts.registry.delete_credentials('1')
        rows = self.rows()
        self.assertEqual((rows[1]['usageStatus'], rows[3]['usageStatus']), ('no_credentials', 'api_key'))
        self.assertEqual((rows[1]['usage'], rows[3]['usage']), (None, None))

    def test_login_renewed_by_claude_code_is_saved_while_listing(self):
        self.link('ana@example.com', 'r1')
        self.login('ana@example.com', 'r1-rotated')
        self.network.profiles['access-r1-rotated'] = {'account': {'uuid': 'uuid-ana@example.com', 'email': 'ana@example.com'}}
        self.accounts.overview()
        self.assertEqual(self.refresh_token(self.stored(1)), 'r1-rotated')

    def test_renewed_login_goes_to_the_right_account_of_the_same_person(self):
        self.link('ana@example.com', 'r1')
        self.link('ana@example.com', 'r2', organization='org-9', name='Empresa')
        self.login('ana@example.com', 'r2-rotated', organization='org-9', name='Empresa')
        self.network.profiles['access-r2-rotated'] = {'account': {'uuid': 'uuid-ana@example.com', 'email': 'ana@example.com'}, 'organization': {'uuid': 'org-9'}}
        self.accounts.overview()
        self.assertEqual(self.refresh_token(self.stored(2)), 'r2-rotated')
        self.assertEqual(self.refresh_token(self.stored(1)), 'r1')

    def test_login_that_cannot_be_verified_is_not_saved_while_listing(self):
        self.link('ana@example.com', 'r1')
        self.login('ana@example.com', 'r1-rotated')
        self.accounts.overview()
        self.assertEqual(self.refresh_token(self.stored(1)), 'r1')

    def test_login_of_an_unlinked_account_is_not_shown_as_the_linked_one(self):
        # /login as someone else is half done: new credential, config still naming Ana.
        self.link('ana@example.com', 'r1')
        self.login('ana@example.com', 'r9')
        self.network.profiles['access-r9'] = {'account': {'uuid': 'uuid-otra', 'email': 'otra@example.com'}}
        self.network.usage['access-r9'] = quota(99, 99)
        row = self.rows()[1]
        self.assertEqual((row['usageStatus'], row['usage']), ('foreign_credential', None))
        self.assertEqual(self.refresh_token(self.stored(1)), 'r1')

    def test_account_made_active_outside_claudio_is_recognized(self):
        self.link('ana@example.com', 'r1')
        self.link('luis@example.com', 'r2')
        self.login('ana@example.com', 'r1')
        self.assertEqual(self.accounts.overview()['list']['activeAccountNumber'], 1)
        self.assertEqual(self.accounts.registry.load()['active'], 1)


class FourAccounts(ManagerCase):
    def setUp(self):
        super().setUp()
        self.link('ana@example.com', 'r1')
        self.link('luis@example.com', 'r2')
        self.link('eva@example.com', 'r3')
        self.link('activa@example.com', 'r4')
        self.usage(r1=quota(30, 80, LATE), r2=quota(50, 40, EARLY), r3=quota(10, 60, LATE), r4=quota(70, 70))

    def usage(self, **by_refresh):
        for refresh, answer in by_refresh.items():
            self.network.usage[f'access-{refresh}'] = answer

    def active(self):
        return self.live_config()['oauthAccount']['emailAddress']


class StrategyTests(FourAccounts):
    def test_best_goes_to_the_account_with_most_room_in_its_tightest_window(self):
        self.accounts.switch(strategy='best')
        self.assertEqual(self.active(), 'luis@example.com')

    def test_best_stays_when_the_active_account_already_has_most_room(self):
        self.usage(r4=quota(5, 5))
        self.accounts.switch(strategy='best')
        self.assertEqual(self.active(), 'activa@example.com')

    def test_best_ignores_paused_accounts_and_figures_that_could_not_be_updated(self):
        self.accounts.set_disabled(2, True)
        self.accounts.overview()
        # Eva's last known figures are the best ones, but they can no longer be confirmed.
        self.usage(r3=urllib.error.URLError('offline'))
        self.now += 61
        self.accounts.switch(strategy='best')
        self.assertEqual(self.active(), 'activa@example.com')
        self.usage(r4=quota(95, 95))
        self.now += 61
        self.accounts.switch(strategy='best')
        self.assertEqual(self.active(), 'ana@example.com')

    def test_next_moves_to_the_following_account_and_wraps_around(self):
        self.accounts.switch(strategy='next-available')
        self.assertEqual(self.active(), 'ana@example.com')
        self.accounts.switch(strategy='next-available')
        self.assertEqual(self.active(), 'luis@example.com')

    def test_next_skips_paused_exhausted_and_unusable_accounts(self):
        self.accounts.set_disabled(1, True)
        self.usage(r2=quota(100, 40))
        self.accounts.registry.delete_credentials('3')
        self.accounts.link_token('sk-ant-oat01-setup')
        self.accounts.switch(strategy='next-available')
        self.assertEqual(self.active(), 'setup-token-5@token.local')

    def test_consume_first_spends_the_week_that_resets_soonest(self):
        self.accounts.switch(strategy='consume-first')
        self.assertEqual(self.active(), 'luis@example.com')
        self.usage(r2=quota(100, 40, EARLY))
        self.now += 61
        self.accounts.switch(4)
        self.accounts.switch(strategy='consume-first')
        self.assertIn(self.active(), ('ana@example.com', 'eva@example.com'))

    def test_no_other_usable_account_is_reported(self):
        for number in (1, 2, 3):
            self.accounts.set_disabled(number, True)
        for strategy in ('best', 'next-available', 'consume-first'):
            with self.subTest(strategy=strategy), self.assertRaises(AccountError):
                self.accounts.switch(strategy=strategy)
        with self.assertRaises(AccountError):
            self.accounts.switch(strategy='random')


class RotationTests(FourAccounts):
    def test_account_below_the_threshold_keeps_working(self):
        self.assertIsNone(self.accounts.rotate(90, 'best'))
        self.assertEqual(self.active(), 'activa@example.com')

    def test_account_at_the_threshold_hands_over_to_the_best_one(self):
        self.usage(r4=quota(90, 20))
        self.assertIsNotNone(self.accounts.rotate(90, 'best'))
        self.assertEqual(self.active(), 'luis@example.com')

    def test_strategy_decides_who_takes_over(self):
        self.usage(r4=quota(90, 20), r2=quota(50, 40, LATE), r3=quota(10, 60, EARLY))
        self.accounts.rotate(90, 'consume-first')
        self.assertEqual(self.active(), 'eva@example.com')

    def test_accounts_close_to_the_threshold_do_not_take_over(self):
        self.usage(r4=quota(92, 20), r1=quota(86, 10), r2=quota(88, 10), r3=quota(99, 10))
        self.assertIsNone(self.accounts.rotate(90, 'best'))
        self.assertEqual(self.active(), 'activa@example.com')

    def test_paused_accounts_never_take_over(self):
        self.usage(r4=quota(95, 20))
        for number in (1, 2, 3):
            self.accounts.set_disabled(number, True)
        self.assertIsNone(self.accounts.rotate(90, 'best'))

    def test_rotation_waits_before_switching_again(self):
        self.usage(r4=quota(95, 20), r2=quota(50, 40))
        self.accounts.rotate(90, 'best')
        self.usage(r2=quota(95, 40))
        self.now += 120
        self.assertIsNone(self.accounts.rotate(90, 'best'))
        self.assertEqual(self.active(), 'luis@example.com')
        self.now += 300
        self.assertIsNotNone(self.accounts.rotate(90, 'best'))
        self.assertEqual(self.active(), 'eva@example.com')

    def test_exhausted_account_does_not_wait(self):
        self.usage(r4=quota(95, 20), r2=quota(50, 40))
        self.accounts.rotate(90, 'best')
        self.usage(r2=quota(100, 40))
        self.now += 61
        self.assertIsNotNone(self.accounts.rotate(90, 'best'))
        self.assertEqual(self.active(), 'eva@example.com')

    def test_unknown_usage_of_the_active_account_changes_nothing(self):
        self.usage(r4=urllib.error.URLError('offline'))
        self.assertIsNone(self.accounts.rotate(90, 'best'))
        self.assertEqual(self.active(), 'activa@example.com')


class SessionTests(ManagerCase):
    def setUp(self):
        super().setUp()
        self.link('ana@example.com', 'r1')
        self.link('luis@example.com', 'r2', expires_in=90000)

    def test_parallel_session_gets_a_profile_logged_in_as_the_account(self):
        environment = self.accounts.session(1)
        profile = sessions.profile_dir(self.accounts.registry.root, '1', 'ana@example.com')
        self.assertEqual(environment, {'CLAUDE_CONFIG_DIR': str(profile)})
        self.assertEqual(self.refresh_token((profile / '.credentials.json').read_text()), 'r1')
        self.assertEqual(json.loads((profile / '.claude.json').read_text())['oauthAccount']['emailAddress'], 'ana@example.com')

    def test_active_account_runs_on_the_main_profile(self):
        self.assertIsNone(self.accounts.session(2))
        self.assertFalse((self.accounts.registry.root / 'sessions').exists())

    def test_expired_login_is_renewed_before_the_session_starts(self):
        self.now += 4000
        self.network.tokens['r1'] = {'access_token': 'access-r1b', 'refresh_token': 'r1b', 'expires_in': 3600}
        self.accounts.session(1)
        profile = sessions.profile_dir(self.accounts.registry.root, '1', 'ana@example.com')
        self.assertEqual(self.refresh_token((profile / '.credentials.json').read_text()), 'r1b')
        self.assertEqual(self.refresh_token(self.stored(1)), 'r1b')

    def test_login_renewed_by_an_earlier_session_is_kept(self):
        self.accounts.session(1)
        profile = sessions.profile_dir(self.accounts.registry.root, '1', 'ana@example.com')
        (profile / '.credentials.json').write_text(oauth_credentials('r1-from-session', 'access-s', 7200, NOW))
        self.accounts.session(1)
        self.assertEqual(self.refresh_token((profile / '.credentials.json').read_text()), 'r1-from-session')

    def test_api_key_accounts_have_no_parallel_session(self):
        self.accounts.link_token(API_KEY)
        with self.assertRaisesRegex(AccountError, 'API key'):
            self.accounts.session(3)

    def test_account_that_needs_login_cannot_start_a_session(self):
        self.now += 4000
        with self.assertRaises(AccountError):
            self.accounts.session(1)


if __name__ == '__main__':
    unittest.main()
