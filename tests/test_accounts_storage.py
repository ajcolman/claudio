import os
import stat
import sys
import time
import unittest

from accounts import paths, storage
from sandbox import Sandbox


class PathTests(Sandbox):
    def test_default_profile_lives_in_the_home_folder(self):
        self.assertEqual(paths.credentials_file(), self.home / '.claude' / '.credentials.json')
        self.assertEqual(paths.claude_config(), self.home / '.claude.json')

    def test_relocated_profile_keeps_its_config_inside(self):
        os.environ['CLAUDE_CONFIG_DIR'] = str(self.home / 'work')
        self.assertEqual(paths.credentials_file(), self.home / 'work' / '.credentials.json')
        self.assertEqual(paths.claude_config(), self.home / 'work' / '.claude.json')

    def test_legacy_config_file_wins_when_present(self):
        (self.claude / '.config.json').write_text('{}')
        self.assertEqual(paths.claude_config(), self.claude / '.config.json')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Ubicación XDG, solo en Linux.')
    def test_linux_store_follows_xdg_and_stays_apart_from_claude_swap(self):
        self.assertEqual(paths.data_dir(), self.home / '.local' / 'share' / 'claudio')
        os.environ['XDG_DATA_HOME'] = str(self.home / 'data')
        self.assertEqual(paths.data_dir(), self.home / 'data' / 'claudio')
        self.assertEqual(paths.claude_swap_dir(), self.home / 'data' / 'claude-swap')


class StorageTests(Sandbox):
    def test_missing_or_corrupt_json_reads_as_nothing(self):
        broken = self.home / 'broken.json'
        broken.write_text('{"half":')
        self.assertIsNone(storage.read_json(self.home / 'absent.json'))
        self.assertIsNone(storage.read_json(broken))
        (self.home / 'list.json').write_text('[1, 2]')
        self.assertIsNone(storage.read_json(self.home / 'list.json'))

    def test_private_write_replaces_content_and_leaves_no_temporary_file(self):
        target = self.home / 'store' / 'secret.json'
        storage.write_private(target, 'first')
        storage.write_private(target, 'second')
        self.assertEqual(target.read_text(), 'second')
        self.assertEqual([item.name for item in target.parent.iterdir()], ['secret.json'])

    @unittest.skipIf(os.name == 'nt', 'Windows no usa permisos POSIX.')
    def test_private_write_is_readable_only_by_the_owner(self):
        target = self.home / 'secret.json'
        storage.write_private(target, 'token')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_file_lock_excludes_a_second_holder_until_released(self):
        lock = self.home / 'store' / '.lock'
        with storage.file_lock(lock):
            with self.assertRaises(storage.LockTimeout):
                with storage.file_lock(lock, timeout=0.2):
                    pass
        with storage.file_lock(lock, timeout=0.2):
            pass

    def test_directory_lock_waits_for_a_live_holder(self):
        lock = self.home / '.claude.lock'
        with storage.directory_lock(lock, stale=60):
            self.assertTrue(lock.is_dir())
            with self.assertRaises(storage.LockTimeout):
                with storage.directory_lock(lock, timeout=0.3, stale=60):
                    pass
        self.assertFalse(lock.exists())

    def test_directory_lock_takes_over_an_abandoned_lock(self):
        lock = self.home / '.claude.lock'
        lock.mkdir()
        old = time.time() - 120
        os.utime(lock, (old, old))
        with storage.directory_lock(lock, timeout=1, stale=60):
            self.assertTrue(lock.is_dir())
        self.assertFalse(lock.exists())

    def test_claude_locks_hold_the_three_locks_claude_code_uses(self):
        expected = [self.claude / '.oauth_refresh.lock', self.home / '.claude.lock', self.home / '.claude.json.lock']
        with storage.claude_locks():
            self.assertTrue(all(lock.is_dir() for lock in expected))
        self.assertFalse(any(lock.exists() for lock in expected))


if __name__ == '__main__':
    unittest.main()
