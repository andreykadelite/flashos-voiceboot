import contextlib
import threading
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import medium


class Job:
    def __init__(self):
        self.commands = []
        self.cancelled = False

    def command(self, args, **kwargs):
        self.commands.append((args, kwargs))
        if self.cancelled and not kwargs.get('cleanup'):
            raise RuntimeError('cancelled')
        return 0, 'ro' if args[0] == 'findmnt' else ''


class MediumLeases(unittest.TestCase):
    def setUp(self):
        medium._writers.clear()
        self.job = Job()
        self.info = patch('medium.medium_info', return_value=('/dev/sda1', 'exfat'))
        self.info.start()
        self.addCleanup(self.info.stop)

    def test_nested_writers_do_not_remount_ro_while_store_is_open(self):
        with medium.writable(self.job, '/usb', '/dev/sda'):
            with medium.writable(self.job, '/usb', '/dev/sda'):
                self.assertEqual(medium.writer_count('/usb'), 2)
            self.assertEqual(medium.writer_count('/usb'), 1)
            self.assertFalse(any('remount,ro' in args for args, _ in self.job.commands))
        self.assertEqual(medium.writer_count('/usb'), 0)
        self.assertEqual(sum('remount,rw' in args for args, _ in self.job.commands), 1)
        self.assertEqual(sum('remount,ro' in args for args, _ in self.job.commands), 1)

    def test_cancelled_writer_still_restores_readonly_using_cleanup(self):
        with medium.writable(self.job, '/usb', '/dev/sda'):
            self.job.cancelled = True
        args, kwargs = self.job.commands[-1]
        self.assertIn('remount,ro', args)
        self.assertTrue(kwargs['cleanup'])

    def test_cancel_after_successful_remount_still_unwinds_acquisition(self):
        original = self.job.command
        def command(args, **kwargs):
            result = original(args, **kwargs)
            if 'remount,rw' in args:
                self.job.cancelled = True
                raise RuntimeError('cancelled after mount')
            return result
        self.job.command = command
        with self.assertRaisesRegex(RuntimeError, 'cancelled after'):
            with medium.writable(self.job, '/usb', '/dev/sda'):
                self.fail('cancelled acquisition must not yield')
        self.assertEqual(medium.writer_count('/usb'), 0)
        self.assertIn('remount,ro', self.job.commands[-1][0])
        self.assertTrue(self.job.commands[-1][1]['cleanup'])

    def test_restore_refuses_open_store_without_any_remount(self):
        with medium.writable(self.job, '/usb', '/dev/sda'):
            before = len(self.job.commands)
            with self.assertRaisesRegex(RuntimeError, 'хранилище'):
                with medium.readonly(self.job, '/usb', '/dev/sda'):
                    self.fail('restore should not start')
            self.assertEqual(len(self.job.commands), before)

    def test_failed_write_releases_lease_and_returns_to_readonly(self):
        with self.assertRaises(ValueError):
            with medium.writable(self.job, '/usb', '/dev/sda'):
                raise ValueError('copy failed')
        self.assertEqual(medium.writer_count('/usb'), 0)
        self.assertIn('remount,ro', self.job.commands[-1][0])

    def test_restore_holds_out_new_writer_until_whole_operation_finishes(self):
        started, entered = threading.Event(), threading.Event()
        def writer():
            started.set()
            with medium.writable(self.job, '/usb', '/dev/sda'):
                entered.set()
        with medium.readonly(self.job, '/usb', '/dev/sda'):
            thread = threading.Thread(target=writer)
            thread.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(entered.wait(0.05))
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(entered.is_set())

    def test_original_readwrite_mount_is_preserved_after_last_writer(self):
        original = self.job.command
        def command(args, **kwargs):
            code, raw = original(args, **kwargs)
            return (code, 'rw') if args[0] == 'findmnt' else (code, raw)
        self.job.command = command
        with medium.writable(self.job, '/usb', '/dev/sda'):
            pass
        self.assertFalse(any('remount,ro' in args for args, _ in self.job.commands))

    def test_changed_mount_is_not_mutated_on_cleanup(self):
        with patch('medium.medium_info', side_effect=[('/dev/sda1', 'exfat'), ('/dev/sdb1', 'exfat')]):
            with self.assertRaises(ValueError):
                with medium.writable(self.job, '/usb', '/dev/sda'):
                    pass
        self.assertFalse(any('remount,ro' in args for args, _ in self.job.commands))


if __name__ == '__main__':
    unittest.main()
