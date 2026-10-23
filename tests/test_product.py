import curses
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import time
from unittest.mock import Mock, patch
import contextlib
import io
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jobs import Job
from interface import UI, OpenPower
from inventory import memory_sections, cpu_sections, disk_sections, report_job
from file_worker import checked_path, listing, plan
from files import mount_options, worker, copy_job, Volume
from wifi import access_points, connect_job, scan_job
from network import target_job
from help_content import TOPICS


class InventoryTests(unittest.TestCase):
    def test_real_smbios_memory_population_and_empty_slots(self):
        fixture = '''Handle 0x0010, DMI type 16, 23 bytes
Physical Memory Array
    Use: System Memory
    Maximum Capacity: 32 GB
    Error Correction Type: None
Handle 0x0011, DMI type 17, 40 bytes
Memory Device
    Array Handle: 0x0010
    Size: 4 GB
    Locator: DIMM A
    Type: DDR3
    Manufacturer: Micron
    Configured Memory Speed: 1600 MT/s
Handle 0x0012, DMI type 17, 40 bytes
Memory Device
    Array Handle: 0x0010
    Size: No Module Installed
    Locator: DIMM B
'''
        sections = memory_sections(fixture, 'MemTotal: 6291456 kB\nMemAvailable: 4000000 kB\nSwapTotal: 0 kB\n')
        content = str(sections)
        self.assertIn('С модулем: 1. Пустых: 1', content)
        self.assertIn('6.00 ГиБ', content)
        self.assertIn('Настроенная скорость: 1600 MT/s', content)
        self.assertNotIn('No Module Installed', content)
        self.assertIn('Коррекция ошибок по данным BIOS: не предусмотрена', content)

    def test_nested_lscpu_and_null_disk_fields(self):
        sections = cpu_sections({'lscpu': [{'field': 'CPU(s):', 'data': '22', 'children': [
            {'field': 'Model name:', 'data': 'Intel Ultra'}, {'field': 'L3 cache:', 'data': '24 MiB'}]}]})
        self.assertIn('Модель: Intel Ultra', str(sections))
        data = {'blockdevices': [{'path': '/dev/sda', 'size': 1000000000, 'type': 'disk', 'mountpoints': [None],
            'fsavail': None, 'ro': False, 'rota': False, 'children': [{'path': '/dev/sda1', 'size': 900000000,
             'type': 'part', 'fstype': 'ntfs', 'mountpoints': [None]}]}]}
        self.assertIn('Родительское устройство: /dev/sda', str(disk_sections(data)))
        self.assertIn('файловая система не смонтирована', str(disk_sections(data)))

    def test_inventory_all_unavailable_and_partial_not_success(self):
        job = Job('inventory', None)
        with patch.object(job, 'command', side_effect=OSError('unavailable')):
            report_job('summary')(job)
        self.assertTrue(job.failed)
        job = Job('inventory', None)
        def command(args, **kwargs):
            return (0, 'MemTotal: 10000 kB') if args[0] == 'cat' else (1, '')
        with patch.object(job, 'command', side_effect=command):
            report_job('summary')(job)
        self.assertFalse(job.failed)
        self.assertTrue(job.incomplete)


class FileTests(unittest.TestCase):
    def test_missing_or_writable_mount_stops_before_copy(self):
        volume = Volume(SimpleNamespace(path='/dev/test'), '/dev/usb')
        job = Job('copy', None)
        with patch.object(job, 'command', return_value=(0, json.dumps({'filesystems': [
            {'source': '/dev/test', 'options': 'rw'}]}))):
            with self.assertRaises(RuntimeError):
                volume.verify(job)
        with patch('files.check_device'), patch.object(volume, 'verify', side_effect=RuntimeError('gone')), \
                patch('files.destination') as destination:
            job._run(copy_job(volume, Path('.'), Path('/usb')))
        self.assertTrue(job.failed)
        destination.assert_not_called()

    def test_progress_emitted_once_at_each_new_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for i in range(500):
                (root / str(i)).write_text('x')
            for i in range(800):
                (root / ('s' + str(i))).symlink_to('/does-not-exist')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = plan(root, '.')
            self.assertEqual(result['files'], 500)
            self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_link_escape_special_file_and_paging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'escape').symlink_to('/etc')
            os.mkfifo(root / 'fifo')
            for i in range(260):
                (root / f'{i:03}.txt').write_text('hello')
            with self.assertRaises(ValueError):
                checked_path(root, 'escape/passwd')
            with self.assertRaises(ValueError):
                checked_path(root, '../passwd')
            result = listing(root, '.', 250)
            self.assertEqual(result['total'], 260)
            self.assertEqual(len(result['items']), 10)
            result = plan(root, '.')
            self.assertEqual(result['files'], 260)
            self.assertEqual(result['skipped'], 2)

    def test_exfat_collisions_rejected_before_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Test').write_text('one')
            (root / 'test').write_text('two')
            with self.assertRaises(ValueError):
                plan(root, '.', windows=True)

    def test_all_supported_windows_destinations_reject_name_collisions_before_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Test').write_text('one')
            (root / 'test').write_text('two')
            volume = SimpleNamespace(device=Mock(), live_disk='/dev/usb', root=root, verify=Mock())
            for fstype in ('exfat', 'vfat', 'ntfs', 'ntfs3', 'fuseblk'):
                with self.subTest(fstype=fstype), patch('files.check_device'), \
                        patch('files.medium_info', return_value=('/dev/usb1', fstype)), \
                        patch('files.destination') as destination:
                    with self.assertRaisesRegex(RuntimeError, 'Имена нельзя без потерь'):
                        copy_job(volume, Path('.'), Path('/usb'))(Job('copy', None))
                    destination.assert_not_called()

    def test_skipped_links_and_special_files_cannot_block_windows_name_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'test').write_text('one')
            (root / 'Test').symlink_to('/not-copied')
            (root / 'NUL').symlink_to('/not-copied')
            os.mkfifo(root / 'CON')
            result = plan(root, '.', windows=True)
            self.assertEqual(result, {'files': 1, 'bytes': 3, 'skipped': 3, 'directory': True})

    def test_fat32_size_rejected_without_reading_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / 'big').open('wb') as stream:
                stream.truncate(4 * 1024**3)
            with self.assertRaises(ValueError):
                plan(root, 'big', fat32=True)

    def test_journal_replay_never_enabled(self):
        for fstype, flag in [('ext4', 'noload'), ('xfs', 'norecovery'), ('btrfs', 'nologreplay'), ('ntfs', 'norecover')]:
            driver, options = mount_options(fstype)
            self.assertIn('ro', options.split(','))
            self.assertIn(flag, options.split(','))
        with self.assertRaises(ValueError):
            mount_options('crypto_LUKS')

    def test_real_worker_text_and_size_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'русский.txt').write_bytes('Проверка'.encode('utf-16'))
            job = Job('worker', None)
            self.assertEqual(worker(job, 'text', root, 'русский.txt')['lines'], ['Проверка'])
            (root / 'legacy.txt').write_bytes('Проверка старого файла'.encode('cp1251'))
            self.assertEqual(worker(job, 'text', root, 'legacy.txt')['lines'], ['Проверка старого файла'])
            self.assertGreater(worker(job, 'space', root)['free'], 0)

    @unittest.skipUnless(os.name == 'posix', 'Linux subprocess pipe selectors')
    def test_real_worker_preserves_large_directory_and_text_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(250):
                (root / f'{index:03}-document-with-a-long-name.txt').touch()
            job = Job('worker', None)
            expected = listing(root, '.')
            self.assertGreater(len(json.dumps({'result': expected})), 8192)
            self.assertEqual(worker(job, 'list', root), expected)
            text = ('Длинный текст для просмотра.\n' * 500)
            (root / 'preview.txt').write_text(text, encoding='utf-8')
            self.assertEqual(worker(job, 'text', root, 'preview.txt')['lines'], text.splitlines())

    @unittest.skipUnless(os.name == 'posix', 'Linux subprocess pipe selectors')
    def test_truncated_unicode_preview_keeps_its_encoding(self):
        for encoding, text, expected in (
                ('utf-8', 'A' + 'я' * 40000, 'A' + 'я' * 32767),
                ('utf-16', '𐍈' * 20000, '𐍈' * 16383)):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'preview.txt').write_bytes(text.encode(encoding))
                result = worker(Job('worker', None), 'text', root, 'preview.txt')
                self.assertTrue(result['truncated'])
                self.assertEqual(result['lines'], [expected])


class WifiTests(unittest.TestCase):
    @contextlib.contextmanager
    def private_files(self):
        # The live service runs as root; unit tests must also run unprivileged.
        create = tempfile.NamedTemporaryFile
        with tempfile.TemporaryDirectory() as directory:
            def temporary_file(*args, **kwargs):
                self.assertEqual(kwargs.pop('dir'), '/run')
                return create(*args, dir=directory, **kwargs)
            def manager(job):
                return Mock(command=lambda args, **kwargs: job.command(
                    args, accepted=None, log_output=False, resource='network', **kwargs))
            with patch('wifi.tempfile.NamedTemporaryFile', side_effect=temporary_file), \
                    patch('wifi.NetworkManager', side_effect=manager):
                yield

    def test_scan_ignores_unmanaged_radio_and_waits_for_first_results(self):
        job = Job('scan', None)
        job.cancel_event = Mock()
        scans = []
        def command(args, **kwargs):
            if args[-1] == 'device':
                return 0, 'wlan0:wifi:unmanaged\nwlan1:wifi:disconnected\n'
            if 'BSSID,SSID,SIGNAL,SECURITY,DEVICE' in args:
                scans.append(args)
                return 0, '' if len(scans) == 1 else r'AA\:BB\:CC\:DD\:EE\:FF:Test:80:WPA2:wlan1'
            return 0, ''
        job.cancel_event.is_set.return_value = False
        with patch.object(job, 'command', side_effect=command):
            scan_job(job)
        self.assertEqual(job.interfaces, ['wlan1'])
        self.assertEqual(len(job.payload), 1)
        self.assertTrue(all('wlan1' in args and 'wlan0' not in args for args in scans))
        job.cancel_event.wait.assert_called_once_with(2)

    def test_colons_and_backslashes_in_ssid_are_not_split(self):
        points = access_points(r'AA\:BB\:CC\:DD\:EE\:FF:Home\: a\\b:80:WPA2:wlan0')
        self.assertEqual(points[0]['ssid'], 'Home: a\\b')
        self.assertEqual(points[0]['bssid'], 'AA:BB:CC:DD:EE:FF')

    def test_password_only_in_private_ram_file_and_removed_after_use(self):
        point = {'ssid': 'Test: Network', 'bssid': 'AA:BB:CC:DD:EE:FF', 'device': 'wlan0', 'security': 'WPA2'}
        commands, paths = [], []
        secret = 'DontLogThis-42'
        def command(args, **kwargs):
            commands.append(args)
            self.assertNotIn(secret, str(args))
            self.assertFalse(kwargs.get('log_output', True))
            if 'passwd-file' in args:
                path = Path(args[args.index('passwd-file') + 1])
                paths.append(path)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertIn(secret, path.read_text())
            if args[0] == 'ip':
                return 0, json.dumps([{'ifname': 'wlan0', 'addr_info': [{'family': 'inet', 'scope': 'global', 'local': '192.168.1.2'}]}])
            return 0, ''
        job = Job('wifi', None)
        with self.private_files(), patch.object(job, 'command', side_effect=command):
            connect_job(point, secret)(job)
        self.assertTrue(paths)
        self.assertFalse(paths[0].exists())
        self.assertNotIn(secret, str(job.lines) + str(job.attachments))
        self.assertIn('save', commands[0])
        self.assertIn('no', commands[0])

    def test_wrong_password_removes_connection_and_does_not_succeed(self):
        point = {'ssid': 'Test', 'device': 'wlan0', 'security': 'WPA2'}
        job = Job('wifi', None)
        def command(args, **kwargs):
            return (4, 'failed') if 'passwd-file' in args else (0, '')
        with self.private_files(), patch.object(job, 'command', side_effect=command), \
                patch('wifi.rollback_temporary_connection', return_value=True) as rollback:
            job._run(connect_job(point, 'wrong-password'))
        self.assertTrue(job.failed)
        rollback.assert_called_once()

    def test_quick_wpa2_and_wpa3_keep_psk_only_in_ram_for_later_rollback(self):
        address = json.dumps([{'ifname': 'wlan0', 'addr_info': [
            {'family': 'inet', 'scope': 'global', 'local': '192.168.1.2'}]}])
        for security, key_management in (('WPA2', 'wpa-psk'), ('WPA3', 'sae')):
            point = {'ssid': 'Test', 'device': 'wlan0', 'security': security}
            job = Job('wifi', None)
            with self.subTest(security=security), self.private_files(), patch.object(
                    job, 'command', side_effect=[(0, ''), (0, ''), (0, address)]) as run:
                connect_job(point, 'private-wifi-password')(job)
            created = run.call_args_list[0].args[0]
            self.assertEqual(created[created.index('save') + 1], 'no')
            self.assertEqual(created[created.index('connection.autoconnect') + 1], 'no')
            self.assertEqual(created[created.index('wifi-sec.psk-flags') + 1], '0')
            self.assertEqual(created[created.index('wifi-sec.key-mgmt') + 1], key_management)
            self.assertNotIn('private-wifi-password', str(run.call_args_list) + str(job.lines))


class AccessTests(unittest.TestCase):
    def test_f10_waits_for_job_stop_before_opening_power(self):
        ui = self.ui([])
        ui.settings.data = {'progress_seconds': 30}
        ui.screen.getch.side_effect = lambda: (time.sleep(0.01) or curses.KEY_F10)
        stopped = []
        def function(job):
            try:
                while True:
                    job.check()
                    time.sleep(0.005)
            finally:
                stopped.append(True)
        with self.assertRaises(OpenPower):
            ui.run_job('test', function, save=False)
        self.assertEqual(stopped, [True])

    def ui(self, keys):
        screen, speech, settings = Mock(), Mock(), Mock()
        screen.getch.side_effect = keys
        with patch('interface.curses.curs_set'):
            ui = UI(screen, speech, settings, '/tmp')
        ui.draw = Mock()
        return ui

    def test_paging_clamps_without_wrapping(self):
        ui = self.ui([curses.KEY_PPAGE, 13])
        self.assertEqual(ui.choose('test', [(str(i), i) for i in range(15)]), 0)
        ui = self.ui([curses.KEY_END, curses.KEY_NPAGE, 13])
        self.assertEqual(ui.choose('test', [(str(i), i) for i in range(15)]), 14)

    def test_secret_keyboard_never_speaks_characters(self):
        ui = self.ui([])
        ui.screen.get_wch.side_effect = ['a', '+', 'б', curses.KEY_F3, '\n']
        self.assertEqual(ui.text_input('Пароль', secret=True), 'a+б')
        spoken = [call.args[0] for call in ui.speech.say.call_args_list]
        self.assertEqual(spoken[1:], ['Символ', 'Символ', 'Символ', 'Символов: 3'])

    def test_help_has_contacts_and_attribution(self):
        content = str(TOPICS)
        for value in ('Зотов Андрей Анатольевич', 'исходного автора', 'andreykadelite@ya.ru', 'zotovsmol'):
            self.assertIn(value, content)

    def test_target_rejects_cli_and_url_and_accepts_idn(self):
        for target in ('-f', 'https://example.com', 'abc;reboot', ''):
            with self.assertRaises(ValueError):
                target_job(target)
        self.assertTrue(callable(target_job('пример.рф')))
