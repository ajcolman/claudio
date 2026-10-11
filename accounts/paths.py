"""Ubicación de los archivos de Claude Code y del almacén de cuentas de Claudio."""
import os
import sys
from pathlib import Path


def claude_home():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')


def claude_config():
    # Claude Code keeps its global config beside the profile unless a legacy file exists inside it.
    legacy = claude_home() / '.config.json'
    if legacy.exists():
        return legacy
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home()) / '.claude.json'


def credentials_file():
    return claude_home() / '.credentials.json'


def _xdg_data_home():
    # The XDG specification ignores the variable when it is empty or relative.
    value = os.environ.get('XDG_DATA_HOME', '')
    return Path(value) if os.path.isabs(value) else Path.home() / '.local' / 'share'


def data_dir():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Claudio' / 'accounts'
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming') / 'Claudio' / 'accounts'
    return _xdg_data_home() / 'claudio'


def claude_swap_dir():
    """Almacén de claude-swap, del que se importan una vez las cuentas ya vinculadas."""
    if sys.platform.startswith('linux'):
        return _xdg_data_home() / 'claude-swap'
    return Path.home() / '.claude-swap-backup'
