#!/usr/bin/env python3
"""Claudio: API local para cuentas, cuotas y contexto de Claude Code."""
import hashlib
import http.server
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from accounts.manager import Accounts
from accounts.rotation import Rotation
from accounts.sessions import AUTH_OVERRIDES

PORT = 5252
API_VERSION = 2
SERVER_REVISION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
CLAUDE_JSON_PATH = os.path.expanduser('~/.claude.json')
command_lock = threading.Lock()
services_lock = threading.Lock()
manager = None
rotation = None


def services():
    """The account manager and its rotation, created on first use."""
    global manager, rotation
    with services_lock:
        if manager is None:
            manager = Accounts()
        if rotation is None:
            rotation = Rotation(manager)
        return manager, rotation


def launch_terminal(argv, cwd=None, environment=None):
    """Only receive validated argument lists; quote once at the terminal boundary.

    `environment` starts the command as another account: its variables are set and
    the ones that would override the account inside Claude Code are removed.
    """
    cwd = os.path.abspath(os.path.expanduser(cwd or '~'))
    if not os.path.isdir(cwd):
        return False, 'La carpeta del proyecto ya no está disponible.'
    system = platform.system()
    if environment and system != 'Windows':
        argv = ['env'] + [part for name in AUTH_OVERRIDES for part in ('-u', name)] + [f'{name}={value}' for name, value in environment.items()] + argv
    try:
        if system == 'Windows':
            def ps_quote(value):
                return "'" + value.replace("'", "''") + "'"
            command = '& ' + ' '.join(ps_quote(arg) for arg in argv)
            if environment:
                command = ''.join(f'Remove-Item Env:{name} -ErrorAction SilentlyContinue; ' for name in AUTH_OVERRIDES) \
                    + ''.join(f'$env:{name} = {ps_quote(value)}; ' for name, value in environment.items()) + command
            powershell = shutil.which('pwsh') or shutil.which('powershell.exe') or shutil.which('powershell')
            if not powershell:
                return False, 'No se encontró PowerShell para abrir la sesión.'
            wt = shutil.which('wt.exe') or shutil.which('wt')
            args = [powershell, '-NoExit', '-Command', command]
            subprocess.Popen([wt, '-d', cwd] + args if wt else args, cwd=cwd,
                             creationflags=0 if wt else subprocess.CREATE_NEW_CONSOLE)
        elif system == 'Darwin':
            command = f'cd -- {shlex.quote(cwd)} && {shlex.join(argv)}'
            script = 'tell application "Terminal" to do script ' + json.dumps(command, ensure_ascii=False)
            subprocess.Popen(['osascript', '-e', script])
        else:
            wrapped = shlex.join(argv) + '; exec bash'
            for terminal in ['konsole', 'gnome-terminal', 'ptyxis', 'alacritty', 'kitty', 'xfce4-terminal', 'xterm']:
                binary = shutil.which(terminal)
                if not binary:
                    continue
                if terminal == 'konsole':
                    args = [binary, '--workdir', cwd, '-e', 'bash', '-c', wrapped]
                elif terminal in ['alacritty', 'kitty']:
                    args = [binary, '--working-directory', cwd, '-e', 'bash', '-c', wrapped]
                elif terminal == 'xfce4-terminal':
                    args = [binary, '--working-directory', cwd, '--command', shlex.join(['bash', '-c', wrapped])]
                elif terminal == 'xterm':
                    args = [binary, '-e', 'bash', '-c', wrapped]
                else:
                    args = [binary, '--', 'bash', '-c', wrapped]
                subprocess.Popen(args, cwd=cwd)
                break
            else:
                return False, 'No se encontró un emulador de terminal compatible.'
        return True, None
    except OSError as error:
        return False, str(error)


def project_timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value / 1000 if value > 100_000_000_000 else value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError:
            pass
    return 0


def get_recent_projects():
    try:
        with open(CLAUDE_JSON_PATH, encoding='utf-8') as file:
            data = json.load(file)
        projects = data.get('projects', {})
        if not isinstance(projects, dict):
            return []
        result = [{'path': path, 'name': os.path.basename(path.rstrip('/\\')) or path,
                   'exists': os.path.isdir(path),
                   'lastAccessed': project_timestamp(details.get('lastAccessed')) if isinstance(details, dict) else 0}
                  for path, details in projects.items() if isinstance(path, str)]
        return sorted(result, key=lambda item: (-item['lastAccessed'], item['name'].lower()))
    except (OSError, ValueError, AttributeError):
        return []


def get_system_status():
    accounts, automatic = services()
    # Serialize with account writes so status and list describe the same operation.
    with command_lock:
        try:
            data = accounts.overview()
        except (RuntimeError, OSError) as error:
            return {'success': False, 'error': str(error) or 'No se pudo consultar las cuentas.'}
    data['auto_running'] = automatic.running
    data['auto_config'] = dict(automatic.config)
    data['projects'] = get_recent_projects()
    return data


def string_field(payload, key, required=False, max_length=1000):
    value = payload.get(key, '')
    if not isinstance(value, str) or len(value) > max_length or '\x00' in value:
        raise ValueError(f'El campo {key} no es válido.')
    value = value.strip()
    if required and not value:
        raise ValueError(f'El campo {key} es requerido.')
    return value


def account_field(payload, key='account'):
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise ValueError('Selecciona una cuenta válida.')
    return str(value)


def bool_field(payload, key, default=False):
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f'El campo {key} debe ser verdadero o falso.')
    return value


def account_result(action, *arguments, **options):
    """Run an account operation; problems the user can act on become its error message."""
    try:
        return {'success': True, 'output': action(*arguments, **options), 'error': None}
    except RuntimeError as error:
        return {'success': False, 'output': '', 'error': str(error) or 'No se pudo completar la operación.'}


def toggle_auto(payload):
    automatic = services()[1]
    enable = bool_field(payload, 'enable', True)
    threshold = payload.get('threshold', 90)
    strategy = payload.get('strategy', 'best')
    if type(threshold) is not int or not 70 <= threshold <= 98:
        raise ValueError('El umbral debe ser un entero entre 70 y 98 %.')
    if strategy not in ('best', 'consume-first'):
        raise ValueError('Estrategia de rotación no válida.')
    if enable:
        if automatic.running and automatic.config != {'threshold': threshold, 'strategy': strategy}:
            raise ValueError('Detén la rotación antes de cambiar su configuración.')
        if not automatic.running:
            automatic.start(threshold, strategy)
    else:
        automatic.stop()
    return {'success': True, 'auto_running': automatic.running, 'auto_config': dict(automatic.config)}


def perform_action(path, payload):
    accounts = services()[0]
    if path == '/api/switch':
        strategy = payload.get('strategy')
        if strategy is not None:
            if strategy not in ('best', 'next-available', 'consume-first'):
                raise ValueError('Estrategia no válida.')
            return account_result(accounts.switch, strategy=strategy)
        return account_result(accounts.switch, account_field(payload, 'target'))
    if path == '/api/add-current':
        return account_result(accounts.link_current, string_field(payload, 'alias', max_length=80))
    if path == '/api/add-token':
        token = string_field(payload, 'token', required=True, max_length=16384)
        if '\n' in token or '\r' in token:
            raise ValueError('El token debe ocupar una sola línea.')
        email = string_field(payload, 'email')
        alias = string_field(payload, 'alias', max_length=80)
        if alias and not email:
            raise ValueError('Indica el correo o identificador para asignar el alias.')
        slot = account_field(payload, 'slot') if payload.get('slot') is not None else None
        return account_result(accounts.link_token, token, email, slot, alias)
    if path == '/api/remove':
        return account_result(accounts.remove, account_field(payload))
    if path == '/api/toggle-account':
        return account_result(accounts.set_disabled, account_field(payload), bool_field(payload, 'disable'))
    if path == '/api/alias':
        return account_result(accounts.set_alias, account_field(payload), string_field(payload, 'alias', max_length=80))
    if path == '/api/toggle-auto':
        return toggle_auto(payload)
    if path == '/api/open-terminal':
        mode = payload.get('mode')
        # Keep the two existing fixed commands compatible with older clients.
        if mode is None:
            mode = {'claude': 'new', 'claude --resume': 'resume'}.get(payload.get('command', 'claude'))
        if mode not in ('new', 'resume', 'parallel'):
            raise ValueError('Selecciona un modo de terminal válido.')
        cwd = string_field(payload, 'cwd') or None
        environment = None
        if mode == 'parallel':
            try:
                environment = accounts.session(account_field(payload))
            except RuntimeError as error:
                return {'success': False, 'error': str(error)}
        ok, error = launch_terminal(['claude'] + ([] if mode == 'new' else ['--resume']), cwd, environment)
        return {'success': ok, 'error': error}
    return None


class ClaudioHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_content(self, content, content_type, status=200):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, data, status_code=200):
        self.send_content(json.dumps(data).encode('utf-8'), 'application/json; charset=utf-8', status_code)

    def local_request(self):
        host = self.headers.get('Host', '')
        allowed = {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}
        origin = self.headers.get('Origin')
        if host not in allowed or (origin and origin not in {f'http://{item}' for item in allowed}):
            self.send_json({'success': False, 'error': 'Esta API solo admite solicitudes desde Claudio local.'}, 403)
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        path = urlparse(self.path).path
        if path == '/api/health':
            self.send_json({'app': 'claudio', 'api_version': API_VERSION, 'revision': SERVER_REVISION})
        elif path == '/api/status':
            data = get_system_status()
            self.send_json(data, 503 if data.get('success') is False else 200)
        else:
            assets = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
                      '/app.js': ('app.js', 'text/javascript'), '/styles.css': ('styles.css', 'text/css'),
                      '/assets/claudio.svg': ('assets/claudio.svg', 'image/svg+xml'),
                      '/favicon.ico': ('assets/claudio.ico', 'image/x-icon')}
            if path not in assets:
                self.send_json({'success': False, 'error': 'Recurso no encontrado.'}, 404)
                return
            filename, mime = assets[path]
            with open(os.path.join(os.path.dirname(__file__), filename), 'rb') as file:
                self.send_content(file.read(), mime + '; charset=utf-8')

    def do_POST(self):
        if not self.local_request():
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 32768:
                raise ValueError('El cuerpo de la solicitud está vacío o es demasiado grande.')
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('La solicitud debe contener JSON.')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Se esperaba un objeto JSON.')
            with command_lock:
                result = perform_action(urlparse(self.path).path, payload)
            if result is None:
                self.send_json({'success': False, 'error': 'Acción no encontrada.'}, 404)
            else:
                self.send_json(result, 200 if result['success'] else 502)
        except (ValueError, TypeError) as error:
            self.send_json({'success': False, 'error': str(error)}, 400)
        except (OSError, subprocess.SubprocessError) as error:
            self.send_json({'success': False, 'error': f'No se pudo ejecutar la operación: {error}'}, 502)


def run():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), ClaudioHandler)
    print(f'Claudio · Panel local: http://localhost:{PORT}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if rotation is not None:
            rotation.stop()
        server.server_close()


if __name__ == '__main__':
    run()
