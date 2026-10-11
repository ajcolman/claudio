"""Headless Qt integration with fictitious data; never runs cswap or writes user settings."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
fixture = {'status': {'active': {'email': 'demo@example.com'}},
           'list': {'accounts': [{'number': 1, 'email': 'demo@example.com', 'alias': 'Demo', 'active': True, 'usage': None}], 'activeAccountNumber': 1},
           'projects': [], 'auto_running': False, 'auto_config': {'threshold': 90, 'strategy': 'best'}}

with tempfile.TemporaryDirectory(prefix='claudio-qt-') as directory:
    os.environ.update(QT_QPA_PLATFORM='offscreen', QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu',
                      QT_QUICK_BACKEND='software', XDG_CONFIG_HOME=directory,
                      XDG_DATA_HOME=directory, XDG_CACHE_HOME=directory)
    import desktop
    import server
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    server.get_system_status = lambda: fixture
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.ClaudioHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    desktop.SERVER_URL = f'http://127.0.0.1:{httpd.server_port}'
    app = QApplication([])
    app.setApplicationName('ClaudioSmoke')
    window = desktop.ClaudioWindow()
    window.show()
    result = {'ok': False}
    def inspect():
        window.browser.page().runJavaScript("JSON.stringify({count:document.querySelector('#stat-accounts')?.textContent, theme:document.documentElement.dataset.theme, width:document.documentElement.scrollWidth, viewport:innerWidth, logo:document.querySelector('.brand-mark')?.naturalWidth})", checked)
    def checked(value):
        if not value:
            return
        data = json.loads(value)
        if data.get('count') != '1':
            return
        result['ok'] = (data['theme'] == 'dark' and data['width'] <= data['viewport']
                        and data.get('logo', 0) > 0 and not window.windowIcon().isNull())
        poll.stop()
        QTimer.singleShot(500, finish)
    def finish():
        result['ok'] = result['ok'] and not window.profile.isOffTheRecord()
        window.grab().save('/tmp/claudio-native.png')
        window.set_zoom(0.1)
        result['ok'] = result['ok'] and abs(window.browser.zoomFactor() - 1.1) < 0.01
        window.close()
        app.quit()
    poll = QTimer()
    poll.timeout.connect(inspect)
    poll.start(300)
    QTimer.singleShot(20000, app.quit)
    app.exec()
    poll.stop()
    # Dispose pages before the persistent profile.
    import shiboken6
    shiboken6.delete(window.browser)
    shiboken6.delete(window)
    httpd.shutdown()
    httpd.server_close()
    if not result['ok']:
        raise SystemExit('Native smoke test failed')
    print('Qt OK: panel cargado, datos renderizados, perfil persistente y zoom operativo.')
