"""Registro de las cuentas vinculadas y copia de la credencial de cada una."""
import base64
import sys
from pathlib import Path

from . import keychain, paths, storage

RECORD_FIELDS = ('email', 'uuid', 'organizationUuid', 'organizationName', 'added')


class RegistryError(RuntimeError):
    pass


def find(data, email, organization=''):
    """Number of the account with this identity. The same email inside an organization is another account."""
    for number, record in data['accounts'].items():
        if record.get('email') == email and (record.get('organizationUuid') or '') == (organization or ''):
            return number
    return None


def next_number(data):
    return max((int(number) for number in data['accounts']), default=0) + 1


class Registry:
    def __init__(self, root=None):
        self.root = Path(root) if root else paths.data_dir()
        self.file = self.root / 'accounts.json'

    def lock(self):
        return storage.file_lock(self.root / '.lock')

    def load(self):
        data = storage.read_json(self.file)
        if data is None:
            # Reading a damaged registry as empty would let the next write erase every account.
            if self.file.exists():
                raise RegistryError(f'El registro de cuentas {self.file} está dañado. Repáralo o muévelo antes de continuar.')
            return {'version': 1, 'active': None, 'accounts': {}}
        if not isinstance(data.get('accounts'), dict):
            raise RegistryError(f'El registro de cuentas {self.file} está dañado. Repáralo o muévelo antes de continuar.')
        return data

    def save(self, data):
        storage.write_json(self.file, data)

    def _credentials_file(self, number):
        return self.root / 'credentials' / f'{number}.json'

    def credentials(self, number):
        return storage.read_text(self._credentials_file(number))

    def previous_credentials(self, number):
        return storage.read_text(self._credentials_file(number).with_suffix('.prev'))

    def save_credentials(self, number, text):
        target = self._credentials_file(number)
        current = storage.read_text(target)
        if current and current != text:
            # One earlier generation survives as a way back if this write turns out wrong.
            storage.write_private(target.with_suffix('.prev'), current)
        storage.write_private(target, text)

    def delete_credentials(self, number):
        for path in (self._credentials_file(number), self._credentials_file(number).with_suffix('.prev')):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def import_claude_swap(self):
        """Copy the accounts linked in claude-swap, once, leaving its data untouched."""
        source = paths.claude_swap_dir()
        theirs = storage.read_json(source / 'sequence.json') or {}
        if self.file.exists() or not isinstance(theirs.get('accounts'), dict):
            return 0
        data = {'version': 1, 'active': None, 'accounts': {}}
        for number, record in theirs['accounts'].items():
            if not str(number).isdigit() or not isinstance(record, dict) or not record.get('email'):
                continue
            email = record['email']
            imported = {field: record.get(field) or '' for field in RECORD_FIELDS}
            for field in ('alias', 'disabled', 'kind'):
                if record.get(field):
                    imported[field] = record[field]
            config = storage.read_json(source / 'configs' / f'.claude-config-{number}-{email}.json') or {}
            account = config.get('oauthAccount')
            imported['oauthAccount'] = account if isinstance(account, dict) and account else {
                'emailAddress': email, 'accountUuid': imported['uuid'],
                'organizationUuid': imported['organizationUuid'] or None, 'organizationName': imported['organizationName'] or None}
            stored = _claude_swap_credentials(source, number, email)
            if stored:
                self.save_credentials(number, stored)
            data['accounts'][str(number)] = imported
        if str(theirs.get('activeAccountNumber')) in data['accounts']:
            data['active'] = int(theirs['activeAccountNumber'])
        self.save(data)
        return len(data['accounts'])


def _claude_swap_credentials(source, number, email):
    encoded = storage.read_text(source / 'credentials' / f'.creds-{number}-{email}.enc').strip()
    if encoded:
        try:
            return base64.b64decode(encoded, validate=True).decode('utf-8')
        except ValueError:
            pass
    if sys.platform == 'darwin':
        try:
            return keychain.read('claude-swap', f'account-{number}-{email}') or ''
        except keychain.KeychainError:
            pass
    return ''
