"""Launcher fallback must wait for systemd to clean every X process group."""
import configparser
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import launch


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.ready = self.directory / 'gui-ready'
        self.failed = self.directory / 'graphics-failed'
        self.cmdline = self.directory / 'cmdline'
        self.cmdline.write_text('boot=live')
        self.process = mock.Mock()
        self.process.poll.return_value = None
        self.process.wait.return_value = 0
        def mapped_path(value):
            return {'/run/flashos-gui-ready': self.ready,
                    '/run/flashos-graphics-failed': self.failed,
                    '/proc/cmdline': self.cmdline}.get(str(value), Path(value))
        fixtures = [mock.patch.object(launch, 'Path', side_effect=mapped_path),
                    mock.patch.object(launch, 'previous_service_processes', return_value=[]),
                    mock.patch.object(launch, 'open', mock.mock_open(), create=True),
                    mock.patch.object(launch.subprocess, 'Popen', return_value=self.process),
                    mock.patch.object(launch.os, 'execv'),
                    mock.patch.object(launch.os, 'killpg', create=True),
                    mock.patch.object(launch.time, 'monotonic', side_effect=[0, 36]),
                    mock.patch.object(launch.time, 'sleep'),
                    mock.patch.object(launch.sys, 'argv', ['launch.py', '--root', '/live']),
                    mock.patch.dict(os.environ, {}, clear=True)]
        for fixture in fixtures:
            fixture.start()
            self.addCleanup(fixture.stop)

    def assert_service_restart(self):
        with self.assertRaises(SystemExit) as result:
            launch.main()
        self.assertEqual(result.exception.code, 1)
        self.assertTrue(self.failed.is_file())
        launch.os.execv.assert_not_called()
        launch.os.killpg.assert_not_called()

    def test_startup_timeout_never_runs_console_with_live_x_children(self):
        self.assert_service_restart()
        self.assertIn('35 секунд', self.failed.read_text())
        self.process.wait.assert_not_called()
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()

    def test_early_x_exit_also_requires_cleanup_before_console(self):
        self.process.poll.return_value = 1
        self.process.returncode = 1
        self.assert_service_restart()
        self.assertIn('до готовности. Код: 1', self.failed.read_text())

    def test_graphical_session_exit_keeps_the_existing_cleanup_contract(self):
        def spawn(*args, **kwargs):
            self.ready.write_text('ready\n')
            return self.process
        launch.subprocess.Popen.side_effect = spawn
        self.assert_service_restart()
        self.process.wait.assert_called_once_with()
        self.assertIn('завершилось. Код: 0', self.failed.read_text())

    def test_missing_xinit_falls_back_after_restart_instead_of_repeating_x(self):
        launch.subprocess.Popen.side_effect = FileNotFoundError('xinit unavailable')
        self.assert_service_restart()
        self.assertIn('xinit unavailable', self.failed.read_text())

    def test_clean_restart_after_failure_starts_only_console(self):
        self.failed.write_text('previous X failure')
        launch.main()
        launch.subprocess.Popen.assert_not_called()
        launch.os.execv.assert_called_once_with(sys.executable,
            [sys.executable, str(Path(launch.__file__).with_name('menu.py')), '--root', '/live'])
        self.assertEqual(os.environ['FLASHOS_GRAPHICS_FAILED'], '1')

    def test_explicit_text_boot_does_not_attempt_x_or_mark_a_failure(self):
        self.cmdline.write_text('boot=live flashos.text=1')
        launch.main()
        launch.subprocess.Popen.assert_not_called()
        launch.os.execv.assert_called_once()
        self.assertFalse(self.failed.exists())

    def test_surviving_cgroup_member_blocks_new_console_and_graphics(self):
        launch.previous_service_processes.return_value = [77]
        with self.assertRaises(SystemExit) as result:
            launch.main()
        self.assertEqual(result.exception.code, 1)
        launch.subprocess.Popen.assert_not_called()
        launch.os.execv.assert_not_called()
        self.assertFalse(self.failed.exists())


class ServiceCleanupContract(unittest.TestCase):
    def test_service_cleans_all_groups_and_resets_tty_before_fallback_restart(self):
        service = configparser.ConfigParser(interpolation=None, strict=False)
        service.read(Path(__file__).resolve().parents[1] / 'boot/flashos.service')
        self.assertEqual(service.get('Service', 'KillMode'), 'control-group')
        self.assertEqual(service.get('Service', 'Restart'), 'always')
        self.assertTrue(service.getboolean('Service', 'SendSIGKILL'))
        self.assertTrue(service.getboolean('Service', 'TTYReset'))
        self.assertEqual(service.get('Service', 'TTYPath'), '/dev/tty1')


if __name__ == '__main__':
    unittest.main()
