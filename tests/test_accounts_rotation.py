import threading
import unittest

from accounts.rotation import Rotation


class Passes:
    """Stands in for the account manager and records each rotation pass."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.happened = threading.Semaphore(0)

    def rotate(self, threshold, strategy):
        self.calls.append((threshold, strategy))
        self.happened.release()
        if self.fail:
            raise OSError('sin red')


class RotationTests(unittest.TestCase):
    def start(self, passes, interval=0.01):
        rotation = Rotation(passes, interval=interval)
        self.addCleanup(rotation.stop)
        rotation.start(85, 'consume-first')
        return rotation

    def test_running_rotation_checks_the_quota_with_its_settings(self):
        passes = Passes()
        rotation = self.start(passes)
        self.assertTrue(passes.happened.acquire(timeout=2))
        self.assertTrue(passes.happened.acquire(timeout=2))
        self.assertTrue(rotation.running)
        self.assertEqual(passes.calls[0], (85, 'consume-first'))
        self.assertEqual(rotation.config, {'threshold': 85, 'strategy': 'consume-first'})

    def test_stopped_rotation_checks_nothing_more(self):
        passes = Passes()
        rotation = self.start(passes, interval=30)
        self.assertTrue(passes.happened.acquire(timeout=2))
        rotation.stop()
        self.assertFalse(rotation.running)
        self.assertEqual(len(passes.calls), 1)

    def test_failed_pass_does_not_end_the_rotation(self):
        passes = Passes(fail=True)
        rotation = self.start(passes)
        self.assertTrue(passes.happened.acquire(timeout=2))
        self.assertTrue(passes.happened.acquire(timeout=2))
        self.assertTrue(rotation.running)
        self.assertEqual(rotation.last_error, 'sin red')

    def test_rotation_is_idle_until_started(self):
        rotation = Rotation(Passes())
        self.assertFalse(rotation.running)
        self.assertEqual(rotation.config, {'threshold': 90, 'strategy': 'best'})
        rotation.stop()


if __name__ == '__main__':
    unittest.main()
