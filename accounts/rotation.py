"""Rotación automática: revisa las cuotas y cambia de cuenta antes de agotar la activa."""
import threading


class Rotation:
    def __init__(self, accounts, interval=60.0):
        self._accounts = accounts
        self._interval = interval
        self._thread = None
        self._stop = threading.Event()
        self.config = {'threshold': 90, 'strategy': 'best'}
        self.last_error = ''

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, threshold, strategy):
        self.stop()
        self.config = {'threshold': threshold, 'strategy': strategy}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop, threshold, strategy), daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=30)
            self._thread = None

    def _run(self, stop, threshold, strategy):
        while True:
            try:
                self._accounts.rotate(threshold, strategy)
                self.last_error = ''
            except Exception as error:
                # One failed pass (no network, a busy lock) must not end the rotation.
                self.last_error = str(error)
            if stop.wait(self._interval):
                return
