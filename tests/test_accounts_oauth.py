import json
import unittest
import urllib.error

from accounts import oauth
from sandbox import TEST_ROOT, oauth_credentials, quota, use_macos, use_network

NOW = 1_700_000_000


class ExpiryTests(unittest.TestCase):
    def test_token_counts_as_expired_five_minutes_early(self):
        self.assertFalse(oauth.expired(oauth_credentials(expires_in=301, now=NOW), now=NOW))
        self.assertTrue(oauth.expired(oauth_credentials(expires_in=299, now=NOW), now=NOW))

    def test_token_without_expiry_never_expires(self):
        self.assertFalse(oauth.expired(json.dumps({'claudeAiOauth': {'accessToken': 'sk-ant-oat01-setup'}}), now=NOW))
        self.assertFalse(oauth.expired('', now=NOW))


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.network = use_network(self)

    def test_refresh_rotates_the_tokens_and_keeps_everything_else(self):
        self.network.tokens['refresh-1'] = {'access_token': 'access-2', 'refresh_token': 'refresh-2', 'expires_in': 28800, 'scope': 'user:inference user:profile user:sessions'}
        renewed, error = oauth.refresh(oauth_credentials(mcpOAuth={'github': 'kept'}), now=NOW)
        self.assertEqual(error, '')
        data = json.loads(renewed)
        self.assertEqual(data['claudeAiOauth'], {'accessToken': 'access-2', 'refreshToken': 'refresh-2', 'expiresAt': (NOW + 28800) * 1000,
                                                 'scopes': ['user:inference', 'user:profile', 'user:sessions'], 'subscriptionType': 'max'})
        self.assertEqual(data['mcpOAuth'], {'github': 'kept'})
        self.assertEqual(self.network.requests[0]['body'], {'grant_type': 'refresh_token', 'refresh_token': 'refresh-1', 'client_id': '9d1c250a-e61b-44d9-88ed-5944d1962f5e'})

    def test_refresh_token_is_kept_when_the_server_does_not_rotate_it(self):
        self.network.tokens['refresh-1'] = {'access_token': 'access-2', 'expires_in': 60}
        renewed, _ = oauth.refresh(oauth_credentials(), now=NOW)
        self.assertEqual(json.loads(renewed)['claudeAiOauth']['refreshToken'], 'refresh-1')

    def test_only_an_explicit_verdict_marks_the_login_as_dead(self):
        cases = {(400, '{"error": "invalid_grant"}'): 'invalid_grant', (401, '{"error": "invalid_client"}'): 'invalid_client',
                 (400, '{"error": {"message": "invalid_grant mentioned in passing"}}'): 'transient', (400, 'not json'): 'transient',
                 (500, '{"error": "invalid_grant"}'): 'transient', (429, '{}'): 'transient'}
        for answer, expected in cases.items():
            with self.subTest(answer=answer):
                self.network.tokens['refresh-1'] = answer
                self.assertEqual(oauth.refresh(oauth_credentials(), now=NOW), (None, expected))

    def test_network_failure_can_be_retried(self):
        self.network.tokens['refresh-1'] = urllib.error.URLError('offline')
        self.assertEqual(oauth.refresh(oauth_credentials(), now=NOW), (None, 'transient'))
        self.network.tokens['refresh-1'] = {'unexpected': 'shape'}
        self.assertEqual(oauth.refresh(oauth_credentials(), now=NOW), (None, 'transient'))

    def test_login_without_refresh_token_is_not_sent_anywhere(self):
        setup_token = json.dumps({'claudeAiOauth': {'accessToken': 'sk-ant-oat01-setup', 'scopes': ['user:inference']}})
        self.assertEqual(oauth.refresh(setup_token, now=NOW), (None, 'no_refresh_token'))
        self.assertEqual(oauth.refresh('{"half":', now=NOW), (None, 'transient'))
        self.assertEqual(self.network.requests, [])


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.network = use_network(self)

    def test_usage_is_requested_with_the_account_token(self):
        self.network.usage['access-1'] = quota(42, 68)
        self.assertEqual(oauth.usage('access-1'), quota(42, 68))
        request = self.network.requests[0]
        self.assertEqual((request['token'], request['headers']['anthropic-beta']), ('access-1', 'oauth-2025-04-20'))

    def test_windows_keep_percentage_and_reset_for_the_interface(self):
        self.assertEqual(oauth.windows(quota(42.5, 68, reset='2099-09-17T20:00:00+00:00')),
                         {'fiveHour': {'pct': 42.5, 'resetsAt': '2099-09-17T20:00:00+00:00'}, 'sevenDay': {'pct': 68, 'resetsAt': '2099-09-17T20:00:00+00:00'}})
        self.assertEqual(oauth.windows({'five_hour': {'utilization': 0, 'resets_at': None}}), {'fiveHour': {'pct': 0}})

    def test_answer_without_usable_windows_is_no_data(self):
        for answer in ({}, None, [], {'five_hour': None, 'seven_day': {'utilization': 'high'}}, {'five_hour': {'utilization': True}}):
            with self.subTest(answer=answer):
                self.assertIsNone(oauth.windows(answer))

    def test_profile_names_the_owner_of_a_token(self):
        self.network.profiles['access-1'] = {'account': {'uuid': ' uuid-1 ', 'email': 'ana@example.com'}, 'organization': {'uuid': 'org-1'}}
        self.assertEqual(oauth.profile('access-1'), {'uuid': 'uuid-1', 'email': 'ana@example.com', 'organizationUuid': 'org-1'})

    def test_profile_without_identity_is_unknown(self):
        self.network.profiles['access-1'] = {'account': {'email': 'ana@example.com'}}
        self.assertIsNone(oauth.profile('access-1'))
        self.assertIsNone(oauth.profile('rejected-token'))


class CertificateTests(unittest.TestCase):
    """python.org builds for macOS ship without root certificates; the system's own are used."""

    def setUp(self):
        self.network = use_network(self)
        self.network.usage['access-1'] = quota(1, 2)

    def trusted(self):
        return [dict(pair[0] for pair in certificate['subject'])['commonName'] for certificate in self.network.contexts[-1].get_ca_certs()]

    def test_macos_requests_trust_the_roots_of_the_system(self):
        use_macos(self).roots = TEST_ROOT
        oauth.usage('access-1')
        self.assertIn('Claudio Test Root', self.trusted())

    def test_every_kind_of_request_uses_the_same_trust(self):
        keychain = use_macos(self)
        keychain.roots = TEST_ROOT
        oauth.usage('access-1')
        oauth.profile('access-1')
        oauth.refresh(oauth_credentials())
        self.assertEqual(len(self.network.contexts), 3)
        self.assertTrue(all(context is self.network.contexts[0] for context in self.network.contexts))
        self.assertEqual(keychain.calls.count('find-certificate'), 1)

    def test_unreadable_system_roots_do_not_stop_the_request(self):
        for roots in ('', 'not a certificate', '-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n'):
            with self.subTest(roots=roots):
                oauth._context = None
                use_macos(self).roots = roots
                self.assertEqual(oauth.usage('access-1'), quota(1, 2))
                self.assertNotIn('Claudio Test Root', self.trusted())

    def test_other_systems_keep_the_certificates_of_python(self):
        from unittest.mock import patch
        with patch.object(oauth.keychain, 'system_roots', side_effect=AssertionError('solo en macOS')):
            self.assertEqual(oauth.usage('access-1'), quota(1, 2))
        self.assertIsNotNone(self.network.contexts[-1])


if __name__ == '__main__':
    unittest.main()
