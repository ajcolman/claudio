#!/usr/bin/env python3
"""Ventana nativa de Claudio y arranque compartido para el panel web."""
import hashlib
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 5252
SERVER_URL = f'http://localhost:{PORT}'
DIR = os.path.dirname(os.path.abspath(__file__))
API_VERSION = 2
STARTUP_ERROR = ''
EXPECTED_REVISION = hashlib.sha256(Path(DIR, 'server.py').read_bytes()).hexdigest()[:16]

try:
    from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QStandardPaths, QUrl
    from PySide6.QtGui import QAction, QIcon, QKeySequence
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


def is_server_running():
    try:
        # Startup must not wait for remote account usage.
        with urllib.request.urlopen(f'{SERVER_URL}/api/health', timeout=1) as response:
            data = json.load(response)
            return (data.get('app') == 'claudio' and data.get('api_version') == API_VERSION
                    and data.get('revision') == EXPECTED_REVISION)
    except (OSError, ValueError, AttributeError):
        return False


def port_is_open():
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=0.3):
            return True
    except OSError:
        return False


def find_owned_server():
    """Identify only this user's Python server.py that owns the listening socket.

    Linux migration of installations that predate /api/health. Never terminate
    another application merely because it uses our port.
    """
    if platform.system() != 'Linux':
        return None
    try:
        inodes = set()
        for table in ('/proc/net/tcp', '/proc/net/tcp6'):
            for line in Path(table).read_text().splitlines()[1:]:
                fields = line.split()
                if int(fields[1].split(':')[1], 16) == PORT and fields[3] == '0A':
                    inodes.add(f'socket:[{fields[9]}]')
        expected = Path(DIR, 'server.py').resolve()
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat().st_uid != os.getuid():
                    continue
                argv = (entry / 'cmdline').read_bytes().decode().strip('\0').split('\0')
                if len(argv) < 2 or not Path(argv[0]).name.startswith('python'):
                    continue
                script = Path(argv[1])
                if not script.is_absolute():
                    script = (entry / 'cwd').resolve() / script
                if script.resolve() != expected:
                    continue
                if any(os.readlink(fd) in inodes for fd in (entry / 'fd').iterdir()):
                    return int(entry.name)
            except (OSError, ValueError, UnicodeError):
                continue
    except OSError:
        pass
    return None


def migrate_existing_server():
    """Stop an identified stale server, preserving its rotation configuration."""
    if is_server_running():
        return None
    pid = find_owned_server()
    if pid is None:
        raise RuntimeError(f'El puerto {PORT} está ocupado por otro proceso. Claudio no lo ha cerrado.')
    with urllib.request.urlopen(f'{SERVER_URL}/api/status', timeout=35) as response:
        status = json.load(response)
    if type(status.get('auto_running')) is not bool:
        raise RuntimeError('No se pudo comprobar la rotación del servidor anterior; se ha conservado el proceso.')
    config = status.get('auto_config', {}) if status['auto_running'] else None
    if config is not None:
        if type(config.get('threshold')) is not int or not 70 <= config['threshold'] <= 98 or config.get('strategy') not in ('best', 'consume-first'):
            raise RuntimeError('La configuración de rotación anterior no es compatible; se ha conservado el proceso.')
        set_rotation(False, config)
    # Recheck ownership immediately before sending a signal.
    if find_owned_server() != pid:
        raise RuntimeError('El servidor cambió durante la actualización. Vuelve a abrir Claudio.')
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not port_is_open():
            return config
        time.sleep(0.1)
    raise RuntimeError('El servidor anterior aún se está cerrando. Vuelve a abrir Claudio en unos segundos.')


def set_rotation(enable, config):
    request = urllib.request.Request(f'{SERVER_URL}/api/toggle-auto',
                                     data=json.dumps(dict(config, enable=enable)).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
    if not result.get('success'):
        raise RuntimeError(result.get('error') or 'No se pudo restaurar la rotación automática.')


def start_server_in_background():
    global STARTUP_ERROR
    STARTUP_ERROR = ''
    if is_server_running():
        return True
    rotation = None
    try:
        if port_is_open():
            rotation = migrate_existing_server()
        if not is_server_running():
            kwargs = {'cwd': DIR, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
            if platform.system() == 'Windows':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            else:
                kwargs['start_new_session'] = True
            subprocess.Popen([sys.executable, os.path.join(DIR, 'server.py')], **kwargs)
        # Another simultaneous launcher may have won the bind. Wait for the
        # shared service rather than reporting failure for our own child.
        for _ in range(40):
            if is_server_running():
                if rotation:
                    set_rotation(True, rotation)
                return True
            time.sleep(0.15)
    except (OSError, ValueError, RuntimeError) as error:
        STARTUP_ERROR = str(error)
        return False
    STARTUP_ERROR = f'No se pudo iniciar el servidor local en el puerto {PORT}.'
    if rotation:
        STARTUP_ERROR += ' La rotación quedó detenida; actívala cuando el servidor vuelva a estar disponible.'
    return False


if QT_AVAILABLE:
    class ClaudioWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle('Claudio · Tu espacio de trabajo')
            self.setWindowIcon(QIcon(os.path.join(DIR, 'assets', 'claudio.svg')))
            self.resize(1240, 900)
            self.setMinimumSize(420, 540)
            self.settings = QSettings('Claudio', 'Desktop')
            geometry = self.settings.value('geometry')
            if geometry:
                self.restoreGeometry(geometry)
            else:
                screen = QApplication.primaryScreen().availableGeometry()
                self.resize(min(self.width(), screen.width()), min(self.height(), screen.height()))
                self.move(screen.center() - self.rect().center())

            # A named profile persists favorites and theme between desktop sessions.
            self.profile = QWebEngineProfile('Claudio', self)
            storage = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
            self.profile.setPersistentStoragePath(os.path.join(storage, 'web-profile'))
            self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
            self.browser = QWebEngineView(self)
            self.browser.setPage(QWebEnginePage(self.profile, self.browser))
            self.setCentralWidget(self.browser)
            self.setStyleSheet('QMainWindow, QWebEngineView { background-color: #101211; } '
                               'QStatusBar { background-color: #101211; color: #a0aaa0; }')
            self.browser.setZoomFactor(float(self.settings.value('zoom', 1.0)))
            self.browser.loadFinished.connect(self.on_load_finished)
            self.browser.setUrl(QUrl(SERVER_URL))
            self.statusBar().showMessage('Conectando con tu espacio de trabajo…')

            view_menu = self.menuBar().addMenu('Ver')
            for title, shortcuts, callback in [
                ('Actualizar', ['F5', 'Ctrl+R'], self.browser.reload),
                ('Acercar', ['Ctrl++', 'Ctrl+='], lambda: self.set_zoom(0.1)),
                ('Alejar', ['Ctrl+-'], lambda: self.set_zoom(-0.1)),
                ('Tamaño original', ['Ctrl+0'], lambda: self.browser.setZoomFactor(1.0)),
            ]:
                action = QAction(title, self)
                action.setShortcuts([QKeySequence(shortcut) for shortcut in shortcuts])
                action.triggered.connect(callback)
                view_menu.addAction(action)

        def set_zoom(self, delta):
            self.browser.setZoomFactor(max(0.7, min(1.6, self.browser.zoomFactor() + delta)))

        def on_load_finished(self, ok):
            self.statusBar().showMessage('Claudio · Servicio local' if ok else 'No se pudo cargar el panel. Pulsa F5 para reintentar.', 5000 if ok else 0)

        def closeEvent(self, event):
            self.settings.setValue('geometry', self.saveGeometry())
            self.settings.setValue('zoom', self.browser.zoomFactor())
            super().closeEvent(event)


def run_web_fallback():
    import webbrowser
    if start_server_in_background():
        webbrowser.open(SERVER_URL)
        return 0
    print(f'ERROR: {STARTUP_ERROR}', file=sys.stderr)
    return 1


def main():
    if '--web' in sys.argv or not QT_AVAILABLE:
        return run_web_fallback()
    app = QApplication(sys.argv)
    app.setApplicationName('Claudio')
    app.setOrganizationName('Claudio')
    app.setDesktopFileName('claudio')
    app.setWindowIcon(QIcon(os.path.join(DIR, 'assets', 'claudio.svg')))
    if not start_server_in_background():
        QMessageBox.critical(None, 'No se pudo abrir Claudio', STARTUP_ERROR)
        return 1
    window = ClaudioWindow()
    window.show()
    exit_code = app.exec()
    # Flush and destroy the page before its persistent profile.
    window.browser.page().deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
