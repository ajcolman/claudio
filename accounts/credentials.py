"""Credencial activa de Claude Code: un archivo en Linux y Windows, el llavero en macOS."""
import hashlib
import json
import os
import sys
import unicodedata

from . import keychain, paths, storage

OAUTH_SERVICE = 'Claude Code-credentials'
API_KEY_SERVICE = 'Claude Code'
# Integrations of this machine stored beside the login. They rotate on their own, so on a
# switch the live copy is the valid one; every other field belongs to the account.
SHARED_KEYS = frozenset({'mcpOAuth', 'mcpOAuthClientConfig', 'mcpXaaIdp', 'mcpXaaIdpConfig', 'pluginSecrets'})


class CredentialError(RuntimeError):
    pass


def is_api_key(credentials):
    """A managed key is a bare sk-ant-api… string; every login is a JSON object."""
    return (credentials or '').strip().startswith('sk-ant-api')


def parse(credentials):
    if not credentials or is_api_key(credentials):
        return None
    try:
        data = json.loads(credentials)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def login(credentials):
    section = (parse(credentials) or {}).get('claudeAiOauth')
    return section if isinstance(section, dict) else {}


def has_login(credentials):
    # Claude Code wipes the tokens when a refresh is rejected, leaving an empty shell behind.
    tokens = login(credentials)
    return is_api_key(credentials) or bool(tokens.get('accessToken') or tokens.get('refreshToken'))


def with_shared_fields(target, live):
    """The target account's login combined with this machine's current integrations."""
    data, current = parse(target), parse(live)
    if data is None or current is None or 'claudeAiOauth' not in data:
        return target
    merged = {key: value for key, value in data.items() if key not in SHARED_KEYS}
    merged.update({key: value for key, value in current.items() if key in SHARED_KEYS})
    return json.dumps(merged)


def profile_service(config_dir):
    """Keychain item of a relocated profile: Claude Code hashes the folder into the name."""
    normalized = unicodedata.normalize('NFC', str(config_dir))
    return f'{OAUTH_SERVICE}-{hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]}'


def _oauth_service():
    relocated = os.environ.get('CLAUDE_CONFIG_DIR')
    return profile_service(relocated) if relocated else OAUTH_SERVICE


def _uses_keychain():
    return sys.platform == 'darwin'


def _forget(service):
    try:
        keychain.delete(service)
    except keychain.KeychainError:
        pass


def read_active():
    """Claude Code's current credential: a login, an API key, or '' when logged out."""
    if _uses_keychain():
        try:
            value = keychain.read(_oauth_service())
        except keychain.KeychainError:
            value = None
        if value:
            return value
    text = storage.read_text(paths.credentials_file())
    if text.strip():
        return text
    if _uses_keychain() and not os.environ.get('CLAUDE_CONFIG_DIR'):
        try:
            value = keychain.read(API_KEY_SERVICE)
        except keychain.KeychainError:
            value = None
        if value:
            return value
    key = (storage.read_json(paths.claude_config()) or {}).get('primaryApiKey')
    return key if isinstance(key, str) else ''


def update_config(change):
    """Apply a change to Claude Code's global config, keeping every other key."""
    path = paths.claude_config()
    data = storage.read_json(path)
    if data is None and path.exists():
        raise CredentialError(f'{path} existe pero no se pudo leer; no se ha modificado.')
    data = data or {}
    change(data)
    storage.write_json(path, data)


def write_active(credentials):
    """Make this credential the one Claude Code uses, clearing the other kind."""
    if is_api_key(credentials):
        _write_api_key(credentials.strip())
    else:
        _write_login(credentials)
        _clear_api_key()


def _write_login(credentials):
    if _uses_keychain():
        try:
            keychain.write(_oauth_service(), credentials)
        except keychain.KeychainError:
            pass
        else:
            # Rewriting an existing file bumps its date, which makes a running Claude Code reload
            # the account. Never create one: Keychain-only users keep no plaintext on disk.
            if paths.credentials_file().exists():
                storage.write_private(paths.credentials_file(), credentials)
            return
    storage.write_private(paths.credentials_file(), credentials)
    if _uses_keychain():
        # Claude Code reads the Keychain first; a stale item would shadow the file.
        _forget(_oauth_service())


def _write_api_key(key):
    in_keychain = False
    if _uses_keychain():
        try:
            keychain.write(API_KEY_SERVICE, key)
            in_keychain = True
        except keychain.KeychainError:
            pass

    def change(config):
        responses = config.get('customApiKeyResponses')
        responses = responses if isinstance(responses, dict) else {}
        approved = responses.get('approved')
        approved = approved if isinstance(approved, list) else []
        # Claude Code recognizes an approved key by its last 20 characters.
        if key[-20:] not in approved:
            approved.append(key[-20:])
        responses['approved'] = approved
        responses.setdefault('rejected', [])
        config['customApiKeyResponses'] = responses
        if in_keychain:
            config.pop('primaryApiKey', None)
        else:
            config['primaryApiKey'] = key

    update_config(change)
    if _uses_keychain():
        _forget(_oauth_service())
    try:
        paths.credentials_file().unlink()
    except FileNotFoundError:
        pass


def _clear_api_key():
    if _uses_keychain():
        _forget(API_KEY_SERVICE)
    if 'primaryApiKey' in (storage.read_json(paths.claude_config()) or {}):
        update_config(lambda config: config.pop('primaryApiKey', None))
