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

PORT = 5252
API_VERSION = 2
SERVER_REVISION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
CSWAP_BIN = shutil.which('cswap') or shutil.which('cswap.exe')
if not CSWAP_BIN:
    local_bin = os.path.expanduser('~/.local/bin/cswap')
    CSWAP_BIN = local_bin if os.path.exists(local_bin) else 'cswap'
CLAUDE_JSON_PATH = os.path.expanduser('~/.claude.json')
auto_process = None
auto_lock = threading.Lock()
command_lock = threading.Lock()
auto_config = {'threshold': 90, 'strategy': 'best'}


def run_cswap(args, stdin_input='n\n', timeout=15):
    try:
        result = subprocess.run([CSWAP_BIN] + args, capture_output=True, text=True,
                                input=stdin_input, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, '', 'La operación tardó demasiado. Intenta nuevamente.'
    except FileNotFoundError:
        return 1, '', 'No se encontró cswap. Instala claude-swap para gestionar tus cuentas.'
    except OSError as error:
        return 1, '', str(error)


def launch_terminal(argv, cwd=None):
    """Only receive validated argument lists; quote once at the terminal boundary."""
    cwd = os.path.abspath(os.path.expanduser(cwd or '~'))
    if not os.path.isdir(cwd):
        return False, 'La carpeta del proyecto ya no está disponible.'
    system = platform.system()
    try:
        if system == 'Windows':
            def ps_quote(value):
                return "'" + value.replace("'", "''") + "'"
            command = '& ' + ' '.join(ps_quote(arg) for arg in argv)
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
    data = {}
    # Serialize with account writes so status and list describe the same operation.
    with command_lock:
        for key, command in [('status', 'status'), ('list', 'list')]:
            code, output, error = run_cswap([command, '--json'])
            if code:
                return {'success': False, 'error': error or f'No se pudo consultar {command}.'}
            try:
                parsed = json.loads(output)
                if not isinstance(parsed, dict) or parsed.get('success') is False:
                    raise ValueError()
                if key == 'list' and not isinstance(parsed.get('accounts'), list):
                    raise ValueError()
                data[key] = parsed
            except (ValueError, TypeError):
                return {'success': False, 'error': f'cswap devolvió datos inválidos para {command}.'}
    with auto_lock:
        data['auto_running'] = auto_process is not None and auto_process.poll() is None
        data['auto_config'] = auto_config.copy()
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


def cswap_result(args, stdin_input='n\n'):
    code, output, error = run_cswap(args, stdin_input=stdin_input)
    return {'success': code == 0, 'output': output,
            'error': (error or output or 'No se pudo completar la operación.') if code else None}


def toggle_auto(payload):
    global auto_process
    enable = bool_field(payload, 'enable', True)
    threshold = payload.get('threshold', 90)
    strategy = payload.get('strategy', 'best')
    if type(threshold) is not int or not 70 <= threshold <= 98:
        raise ValueError('El umbral debe ser un entero entre 70 y 98 %.')
    if strategy not in ('best', 'consume-first'):
        raise ValueError('Estrategia de rotación no válida.')
    with auto_lock:
        running = auto_process is not None and auto_process.poll() is None
        if enable:
            if running and (threshold != auto_config['threshold'] or strategy != auto_config['strategy']):
                raise ValueError('Detén la rotación antes de cambiar su configuración.')
            if not running:
                process = subprocess.Popen([CSWAP_BIN, 'auto', '--threshold', str(threshold), '--strategy', strategy],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass
                else:
                    return {'success': False, 'error': 'La rotación no pudo iniciarse. Revisa las cuentas y la instalación de cswap.'}
                auto_process = process
                auto_config.update(threshold=threshold, strategy=strategy)
        elif running:
            auto_process.terminate()
            try:
                auto_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                auto_process.kill()
                auto_process.wait(timeout=2)
            auto_process = None
        return {'success': True, 'auto_running': auto_process is not None and auto_process.poll() is None,
                'auto_config': auto_config.copy()}


def perform_action(path, payload):
    if path == '/api/switch':
        strategy = payload.get('strategy')
        if strategy is not None:
            if strategy not in ('best', 'next-available', 'consume-first'):
                raise ValueError('Estrategia no válida.')
            args = ['--strategy', strategy]
        else:
            args = [account_field(payload, 'target')]
        return cswap_result(['switch'] + args + ['--json'])
    if path == '/api/add-current':
        alias = string_field(payload, 'alias', max_length=80)
        return cswap_result(['add'] + (['--alias', alias] if alias else []), stdin_input='y\n')
    if path == '/api/add-token':
        token = string_field(payload, 'token', required=True, max_length=16384)
        if '\n' in token or '\r' in token:
            raise ValueError('El token debe ocupar una sola línea.')
        email = string_field(payload, 'email')
        alias = string_field(payload, 'alias', max_length=80)
        if alias and not email:
            raise ValueError('Indica el correo o identificador para asignar el alias.')
        args = ['add-token', '-'] + (['--email', email] if email else [])
        if payload.get('slot') is not None:
            args += ['--slot', account_field(payload, 'slot')]
        result = cswap_result(args, stdin_input=token + '\n')
        # Never return a secret even if a CLI error includes the input.
        for key in ('output', 'error'):
            if result.get(key):
                result[key] = result[key].replace(token, '[token oculto]')
        if result['success'] and alias:
            alias_result = cswap_result(['alias', email, alias])
            if not alias_result['success']:
                result['warning'] = 'Cuenta vinculada, pero no se pudo guardar el alias. Puedes editarlo desde su tarjeta.'
        return result
    if path == '/api/remove':
        return cswap_result(['remove', account_field(payload)], stdin_input='y\n')
    if path == '/api/toggle-account':
        return cswap_result(['disable' if bool_field(payload, 'disable') else 'enable', account_field(payload)])
    if path == '/api/alias':
        account = account_field(payload)
        alias = string_field(payload, 'alias', max_length=80)
        return cswap_result(['alias', account, alias] if alias else ['alias', account, '--unset'])
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
        argv = ['claude'] + (['--resume'] if mode == 'resume' else [])
        if mode == 'parallel':
            argv = [CSWAP_BIN, 'run', account_field(payload), '--share-history', '--', '--resume']
        ok, error = launch_terminal(argv, cwd)
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
        with auto_lock:
            if auto_process and auto_process.poll() is None:
                auto_process.terminate()
        server.server_close()


if __name__ == '__main__':
    run()
