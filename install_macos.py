#!/usr/bin/env python3
"""Instala o actualiza la aplicación Claudio.app de macOS para el usuario actual."""
from pathlib import Path
import plistlib
import shlex
import shutil
import struct
import sys

ROOT = Path(__file__).resolve().parent


def install():
    app = Path.home() / 'Applications' / 'Claudio.app'
    contents = app / 'Contents'
    for folder in ('MacOS', 'Resources'):
        (contents / folder).mkdir(parents=True, exist_ok=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleName': 'Claudio',
        'CFBundleIdentifier': 'io.github.ajcolman.claudio',
        'CFBundleExecutable': 'Claudio',
        'CFBundleIconFile': 'claudio',
        'CFBundlePackageType': 'APPL',
        'NSHighResolutionCapable': True,
        'NSAppleEventsUsageDescription': 'Claudio abre Terminal para iniciar y reanudar sesiones de Claude Code.',
    }))
    # ICNS stores PNG data as is; ic09 is the entry type for the 512x512 logo.
    png = (ROOT / 'assets' / 'claudio.png').read_bytes()
    (contents / 'Resources' / 'claudio.icns').write_bytes(
        b'icns' + struct.pack('>I', len(png) + 16) + b'ic09' + struct.pack('>I', len(png) + 8) + png)
    # Finder starts applications with a minimal PATH, where cswap is usually missing.
    cswap = shutil.which('cswap')
    path = ':'.join(([shlex.quote(str(Path(cswap).parent))] if cswap else [])
                    + ['"$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"'])
    launcher = contents / 'MacOS' / 'Claudio'
    launcher.write_text(f'#!/bin/sh\nexport PATH={path}\n'
                        f'exec {shlex.quote(sys.executable)} {shlex.quote(str(ROOT / "desktop.py"))}\n', encoding='utf-8')
    launcher.chmod(0o755)
    print(f'Aplicación e icono actualizados: {app}')


if __name__ == '__main__':
    if sys.platform != 'darwin':
        sys.exit('Este instalador es solo para macOS. En Linux usa install_linux.py.')
    install()
