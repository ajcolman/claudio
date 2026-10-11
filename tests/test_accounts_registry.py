import base64
import json
import unittest

from accounts import registry
from accounts.registry import Registry
from sandbox import Sandbox, oauth_account, oauth_credentials, use_macos


def claude_swap_store(root, accounts, active=None, credentials=None, configs=None):
    """A claude-swap data folder laid out like the real one."""
    (root / 'credentials').mkdir(parents=True)
    (root / 'configs').mkdir()
    (root / 'sequence.json').write_text(json.dumps({'activeAccountNumber': active, 'lastUpdated': '2026-01-01T00:00:00Z',
                                                    'sequence': sorted(int(n) for n in accounts if str(n).isdigit()), 'accounts': accounts}))
    for number, text in (credentials or {}).items():
        (root / 'credentials' / f'.creds-{number}-{accounts[number]["email"]}.enc').write_text(base64.b64encode(text.encode()).decode())
    for number, config in (configs or {}).items():
        (root / 'configs' / f'.claude-config-{number}-{accounts[number]["email"]}.json').write_text(json.dumps(config))


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob('*')) if path.is_file()}


class RegistryTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.registry = Registry()

    def test_new_registry_has_no_accounts(self):
        self.assertEqual(self.registry.load(), {'version': 1, 'active': None, 'accounts': {}})

    def test_saved_accounts_are_read_back(self):
        data = self.registry.load()
        data['accounts']['3'] = {'email': 'ana@example.com'}
        data['active'] = 3
        self.registry.save(data)
        self.assertEqual(Registry().load(), {'version': 1, 'active': 3, 'accounts': {'3': {'email': 'ana@example.com'}}})

    def test_damaged_registry_is_reported_instead_of_read_as_empty(self):
        self.registry.file.parent.mkdir(parents=True)
        self.registry.file.write_text('{"accounts": {"1"')
        with self.assertRaises(registry.RegistryError):
            self.registry.load()

    def test_replacing_a_credential_keeps_the_previous_one(self):
        self.registry.save_credentials('2', oauth_credentials('first'))
        self.assertEqual(self.registry.previous_credentials('2'), '')
        self.registry.save_credentials('2', oauth_credentials('second'))
        self.registry.save_credentials('2', oauth_credentials('second'))
        self.assertEqual(self.registry.credentials('2'), oauth_credentials('second'))
        self.assertEqual(self.registry.previous_credentials('2'), oauth_credentials('first'))

    def test_deleted_credentials_leave_nothing_behind(self):
        self.registry.save_credentials('2', oauth_credentials('first'))
        self.registry.save_credentials('2', oauth_credentials('second'))
        self.registry.delete_credentials('2')
        self.registry.delete_credentials('2')
        self.assertEqual(list((self.registry.root / 'credentials').iterdir()), [])
        self.assertEqual(self.registry.credentials('2'), '')

    def test_same_email_in_an_organization_is_a_different_account(self):
        data = {'accounts': {'1': {'email': 'ana@example.com', 'organizationUuid': ''}, '2': {'email': 'ana@example.com', 'organizationUuid': 'org-1'}}}
        self.assertEqual(registry.find(data, 'ana@example.com', None), '1')
        self.assertEqual(registry.find(data, 'ana@example.com', 'org-1'), '2')
        self.assertIsNone(registry.find(data, 'ana@example.com', 'org-2'))
        self.assertIsNone(registry.find(data, 'otra@example.com', ''))

    def test_new_accounts_get_the_number_after_the_highest(self):
        self.assertEqual(registry.next_number({'accounts': {}}), 1)
        self.assertEqual(registry.next_number({'accounts': {'1': {}, '4': {}}}), 5)


class ImportTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.source = self.home / '.local' / 'share' / 'claude-swap'
        self.accounts = {'1': {'email': 'ana@example.com', 'uuid': 'uuid-1', 'organizationUuid': '', 'organizationName': '', 'added': '2026-01-01T00:00:00Z', 'alias': 'personal'},
                         '3': {'email': 'ana@empresa.com', 'uuid': 'uuid-3', 'organizationUuid': 'org-3', 'organizationName': 'Empresa', 'added': '2026-02-01T00:00:00Z', 'disabled': True}}

    def test_linked_accounts_arrive_with_their_login_alias_and_state(self):
        claude_swap_store(self.source, self.accounts, active=3, credentials={'1': oauth_credentials('r1'), '3': oauth_credentials('r3')},
                          configs={'1': {'oauthAccount': oauth_account('ana@example.com', 'uuid-1'), 'numStartups': 9},
                                   '3': {'oauthAccount': oauth_account('ana@empresa.com', 'uuid-3', 'org-3', 'Empresa')}})
        store = Registry()
        self.assertEqual(store.import_claude_swap(), 2)
        data = store.load()
        self.assertEqual(data['active'], 3)
        self.assertEqual(data['accounts']['1'], {'email': 'ana@example.com', 'uuid': 'uuid-1', 'organizationUuid': '', 'organizationName': '', 'added': '2026-01-01T00:00:00Z',
                                                 'alias': 'personal', 'oauthAccount': oauth_account('ana@example.com', 'uuid-1')})
        self.assertTrue(data['accounts']['3']['disabled'])
        self.assertEqual(data['accounts']['3']['oauthAccount']['organizationName'], 'Empresa')
        self.assertEqual(store.credentials('1'), oauth_credentials('r1'))
        self.assertEqual(store.credentials('3'), oauth_credentials('r3'))

    def test_claude_swap_data_is_left_exactly_as_it_was(self):
        claude_swap_store(self.source, self.accounts, active=1, credentials={'1': oauth_credentials('r1')}, configs={'1': {'oauthAccount': oauth_account('ana@example.com')}})
        before = snapshot(self.source)
        Registry().import_claude_swap()
        self.assertEqual(snapshot(self.source), before)

    def test_import_happens_only_once(self):
        claude_swap_store(self.source, {'1': self.accounts['1']}, credentials={'1': oauth_credentials('r1')})
        store = Registry()
        self.assertEqual(store.import_claude_swap(), 1)
        data = store.load()
        del data['accounts']['1']
        store.save(data)
        self.assertEqual(Registry().import_claude_swap(), 0)
        self.assertEqual(Registry().load()['accounts'], {})

    def test_nothing_to_import_creates_nothing(self):
        self.assertEqual(Registry().import_claude_swap(), 0)
        self.assertFalse(Registry().file.exists())

    def test_account_without_readable_login_is_listed_without_credentials(self):
        claude_swap_store(self.source, self.accounts, active=1, credentials={'1': oauth_credentials('r1')})
        (self.source / 'credentials' / '.creds-3-ana@empresa.com.enc').write_text('%%% not base64 %%%')
        store = Registry()
        self.assertEqual(store.import_claude_swap(), 2)
        self.assertEqual(store.credentials('3'), '')
        self.assertEqual(store.load()['accounts']['3']['oauthAccount'], oauth_account('ana@empresa.com', 'uuid-3', 'org-3', 'Empresa'))

    def test_malformed_entries_are_skipped(self):
        claude_swap_store(self.source, {'1': self.accounts['1'], 'x': {'email': 'raro@example.com'}, '5': {'uuid': 'sin-correo'}, '6': 'texto'})
        store = Registry()
        self.assertEqual(store.import_claude_swap(), 1)
        self.assertEqual(list(store.load()['accounts']), ['1'])

    def test_api_key_accounts_keep_their_kind(self):
        claude_swap_store(self.source, {'2': {'email': 'api-key-2@token.local', 'uuid': '', 'organizationUuid': '', 'organizationName': '', 'added': 'x', 'kind': 'api_key'}},
                          credentials={'2': 'sk-ant-api03-fictitious'})
        store = Registry()
        store.import_claude_swap()
        self.assertEqual(store.load()['accounts']['2']['kind'], 'api_key')
        self.assertEqual(store.credentials('2'), 'sk-ant-api03-fictitious')


class MacosImportTests(Sandbox):
    def test_logins_kept_in_the_keychain_are_imported(self):
        fake = use_macos(self)
        fake.items[('claude-swap', 'account-1-ana@example.com')] = oauth_credentials('from-keychain')
        claude_swap_store(self.home / '.claude-swap-backup', {'1': {'email': 'ana@example.com', 'uuid': 'uuid-1'}})
        store = Registry()
        self.assertEqual(store.import_claude_swap(), 1)
        self.assertEqual(store.credentials('1'), oauth_credentials('from-keychain'))
        self.assertEqual(store.root, self.home / 'Library' / 'Application Support' / 'Claudio' / 'accounts')


if __name__ == '__main__':
    unittest.main()
