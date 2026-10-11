"""Escritura atómica de archivos privados y bloqueos compartidos con Claude Code."""
import json
import os
import random
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import paths

if sys.platform == 'win32':
    import msvcrt
else:
    import fcntl


class LockTimeout(RuntimeError):
    pass


def read_text(path):
    try:
        return Path(path).read_text(encoding='utf-8')
    except (OSError, ValueError):
        return ''


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_private(path, text):
    """Replace the file in one step so a reader never sees half a credential."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        if sys.platform != 'win32':
            os.chmod(temporary, 0o600)
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows refuses while another process has the target open.
                if attempt == 4:
                    raise
                time.sleep(0.1)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_json(path, data):
    write_private(path, json.dumps(data, indent=2))


@contextmanager
def file_lock(path, timeout=10.0):
    """Cross-process lock for Claudio's own store."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with open(path, 'w') as handle:
        while True:
            try:
                if sys.platform == 'win32':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise LockTimeout('Otra operación de Claudio sigue en curso. Intenta nuevamente.')
                time.sleep(0.05)
        try:
            yield
        finally:
            if sys.platform == 'win32':
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def directory_lock(lock_dir, timeout=9.0, stale=10.0):
    """Lock compatible with the proper-lockfile package Claude Code uses.

    The lock is a directory: mkdir is the mutex, its mtime is refreshed while
    held, and one older than `stale` seconds belongs to a dead holder.
    """
    lock_dir = Path(lock_dir)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.mkdir(lock_dir)
            break
        except FileExistsError:
            pass
        if time.monotonic() > deadline:
            raise LockTimeout('Claude Code está actualizando sus credenciales. Intenta de nuevo en unos segundos.')
        try:
            abandoned = time.time() - os.stat(lock_dir).st_mtime > stale
        except FileNotFoundError:
            continue
        if abandoned:
            try:
                os.rmdir(lock_dir)
            except OSError:
                time.sleep(0.05)
            continue
        time.sleep(0.1 + random.random() * 0.1)
    released = threading.Event()

    def keep_alive():
        while not released.wait(3.0):
            try:
                os.utime(lock_dir)
            except OSError:
                return

    toucher = threading.Thread(target=keep_alive, daemon=True)
    toucher.start()
    try:
        yield
    finally:
        released.set()
        toucher.join(timeout=1.0)
        try:
            os.rmdir(lock_dir)
        except OSError:
            pass


@contextmanager
def claude_locks():
    """Hold Claude Code's credential and config locks, in the order it takes them.

    Its token refresh reads, refreshes and saves under the first two; without
    them a swap landing in that window would be overwritten by the old account.
    """
    home, config = paths.claude_home(), paths.claude_config()
    with directory_lock(home / '.oauth_refresh.lock', stale=60.0), \
            directory_lock(home.parent / (home.name + '.lock'), stale=60.0), \
            directory_lock(config.parent / (config.name + '.lock'), stale=10.0):
        yield
