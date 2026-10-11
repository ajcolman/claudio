#!/usr/bin/env python3
"""Instala o actualiza el acceso e icono de Claudio para el usuario actual."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def desktop_quote(value):
    # Desktop Entry Exec quoting (including literal field-code percent signs).
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def install():
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    applications = data / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    for folder, source in [('scalable', 'claudio.svg'), ('512x512', 'claudio.png')]:
        icons = data / 'icons' / 'hicolor' / folder / 'apps'
        icons.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'assets' / source, icons / source)
    target = applications / 'claudio.desktop'
    target.write_text(
        '[Desktop Entry]\nName=Claudio\n'
        'Comment=Tus cuentas, cuotas y proyectos de Claude Code\n'
        f'Exec={desktop_quote(sys.executable)} {desktop_quote(ROOT / "desktop.py")}\n'
        f'Icon={ROOT / "assets/claudio.svg"}\n'
        'Terminal=false\nType=Application\nCategories=Development;Utility;\n'
        'Keywords=claude;code;cuentas;ai;terminal;\nStartupNotify=true\n', encoding='utf-8')
    for command in [('update-desktop-database', str(applications)), ('kbuildsycoca6', '--noincremental')]:
        if shutil.which(command[0]):
            subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    print(f'Acceso de escritorio e icono actualizados: {target}')


if __name__ == '__main__':
    install()
