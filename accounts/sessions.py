"""Perfiles aislados para usar otra cuenta en una sola terminal."""
import json
import os
import shutil
import sys
import unicodedata

from . import credentials, keychain, paths, storage

# Customizations that follow the user into every profile.
SHARED = ('settings.json', 'keybindings.json', 'CLAUDE.md', 'skills', 'commands', 'agents')
# Conversations and prompt history, so `claude --resume` lists the same sessions for every account.
HISTORY = ('projects', 'history.jsonl')
COPIED = '.claudio-copied.json'
# Variables that would override the selected account inside Claude Code.
AUTH_OVERRIDES = ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN',
                  'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR', 'CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR')


def profile_dir(root, number, email):
    normalized = unicodedata.normalize('NFC', email)
    slug = ''.join(char if char.isascii() and (char.isalnum() or char in '._-') else '_' for char in normalized)
    return root / 'sessions' / f'{number}-{slug}'


def environment(profile):
    return {'CLAUDE_CONFIG_DIR': str(profile)}


def _alive(pid):
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        raise ValueError(pid)
    if sys.platform == 'win32':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        return bool(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    return True


def is_live(profile):
    """Whether a Claude Code process is using the profile.

    A session file that cannot be read counts as live: a running Claude Code
    renews its own tokens, and renewing them here as well would log it out.
    """
    folder = profile / 'sessions'
    if not folder.is_dir():
        return False
    for path in folder.glob('*.json'):
        try:
            if _alive(json.loads(path.read_text(encoding='utf-8'))['pid']):
                return True
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return True
    return False


def stored_credentials(profile):
    """The profile's login. On macOS Claude Code moves it to a Keychain item of the profile."""
    if sys.platform == 'darwin':
        try:
            value = keychain.read(credentials.profile_service(profile))
        except keychain.KeychainError:
            value = None
        if value:
            return value
    return storage.read_text(profile / '.credentials.json')


def save_credentials(profile, text):
    storage.write_private(profile / '.credentials.json', text)
    _forget_keychain_item(profile)


def _forget_keychain_item(profile):
    # Claude Code reads the Keychain first; a stale item would shadow the file.
    if sys.platform == 'darwin':
        try:
            keychain.delete(credentials.profile_service(profile))
        except keychain.KeychainError:
            pass


def prepare(profile, stored, account):
    """Leave the profile ready to start Claude Code as the account."""
    profile.mkdir(parents=True, exist_ok=True)
    if sys.platform != 'win32':
        os.chmod(profile, 0o700)
    if not is_live(profile):
        save_credentials(profile, stored)
        main = storage.read_json(paths.claude_config()) or {}
        config = storage.read_json(profile / '.claude.json') or {}
        config['oauthAccount'] = account
        config['hasCompletedOnboarding'] = True
        config.setdefault('theme', main.get('theme') or 'dark')
        if isinstance(main.get('mcpServers'), dict):
            config['mcpServers'] = main['mcpServers']
        storage.write_json(profile / '.claude.json', config)
    _share(profile)


def _share(profile):
    copied = (storage.read_json(profile / COPIED) or {}).get('items', [])
    still_copied = []
    for name in SHARED + HISTORY:
        source, target = paths.claude_home() / name, profile / name
        if name in copied and not target.is_symlink():
            # Our own copy from an earlier launch: replace it with the current version.
            shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
        if not source.exists() or target.is_symlink() or target.exists():
            # The profile's own data is never replaced.
            continue
        try:
            target.symlink_to(source, target_is_directory=source.is_dir())
        except OSError:
            # Windows without the symlink privilege. Copied history would fork instead of sharing.
            if name in HISTORY:
                continue
            shutil.copytree(source, target) if source.is_dir() else shutil.copy2(source, target)
            still_copied.append(name)
    if still_copied or copied:
        storage.write_json(profile / COPIED, {'items': still_copied})


def remove(profile):
    _forget_keychain_item(profile)
    # rmtree removes the links themselves and never follows them into the shared data.
    shutil.rmtree(profile, ignore_errors=True)
