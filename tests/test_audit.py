import curses
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from interface import UI
from jobs import Job, Cancelled
import hardware
import network
import smart
import toolkit
from speech import Speech


class NavigationTests(unittest.TestCase):
    def ui(self, keys):
        screen, speech, settings = Mock(), Mock(), Mock()
        screen.getch.side_effect = keys
        with patch('interface.curses.curs_set'):
            ui = UI(screen, speech, settings, '/tmp')
        ui.draw = Mock()
        return ui, speech

    def test_navigation_speaks_only_item_and_context_on_request(self):
        ui, speech = self.ui([curses.KEY_DOWN, ord(' '), curses.KEY_F3, 27])
        ui.choose('Главное меню', [('Запустить систему', 1), ('Инструменты', 2)])
        self.assertEqual([call.args[0] for call in speech.say.call_args_list],
                         ['Запустить систему', 'Инструменты', 'Инструменты', 'Главное меню. 2 из 2. Инструменты'])

    def test_confirmation_keeps_question_without_repeating_on_arrow(self):
        ui, speech = self.ui([curses.KEY_DOWN, 13])
        self.assertTrue(ui.confirm('Скопировать диск?'))
        self.assertEqual([call.args[0] for call in speech.say.call_args_list], ['Скопировать диск?. Отмена', 'Продолжить'])

    def test_long_report_line_not_discarded(self):
        ui, speech = self.ui([])
        ui.choose = Mock(return_value=None)
        line = ' '.join(str(i) for i in range(1000))
        ui.view('Отчёт', [line])
        rows = ui.choose.call_args.args[1]
        self.assertEqual(' '.join(item[0] for item in rows), line)
        self.assertTrue(all(len(item[0]) <= 350 for item in rows))

    def test_diagnostic_tone_ignores_notification_setting_and_reports_failure(self):
        speech = Speech.__new__(Speech)
        speech.lock, speech.closed = threading.RLock(), threading.Event()
        speech.signals, speech.volume = False, 75
        speech._current = Mock(return_value=Mock())
        speech.audio = Mock()
        with patch('speech.subprocess.run', return_value=types.SimpleNamespace(returncode=0)) as run:
            self.assertFalse(speech.signal())
            run.assert_not_called()
            self.assertTrue(speech.signal('success', 'left', force=True, wait=True))
            self.assertTrue(run.call_args.kwargs['input'].startswith(b'RIFF'))
        with patch('speech.subprocess.run', return_value=types.SimpleNamespace(returncode=1)):
            self.assertFalse(speech.signal('success', force=True, wait=True))


class DeviceTests(unittest.TestCase):
    def test_identical_models_have_distinct_spoken_identity(self):
        first = toolkit.Device('/dev/sda', 'disk', 1024, 'Same', 'AAA123456', '', False, '8:0', '/dev/sda')
        second = toolkit.Device('/dev/sdb', 'disk', 1024, 'Same', 'BBB654321', '', False, '8:16', '/dev/sdb')
        self.assertIn('123456', first.title)
        self.assertIn('654321', second.title)
        self.assertIn('AAA123456', first.details)

    def test_excludes_live_descendants_even_shared_mapper(self):
        shared = {'path': '/dev/mapper/shared', 'type': 'lvm', 'size': 100, 'fstype': 'ext4'}
        listing = {'blockdevices': [
            {'path': '/dev/sda', 'type': 'disk', 'size': 1000, 'children': [
                {'path': '/dev/sda1', 'type': 'part', 'size': 900, 'children': [shared]}]},
            {'path': '/dev/sdb', 'type': 'disk', 'size': 2000, 'model': 'Test disk', 'serial': 'different',
             'children': [{'path': '/dev/sdb1', 'type': 'part', 'size': 1000, 'fstype': 'ext4'}, shared]}]}
        with patch('toolkit.subprocess.check_output', return_value=json.dumps(listing)) as call:
            choices = toolkit.devices('/dev/sda')
        self.assertIn('--tree', call.call_args.args[0])
        self.assertEqual([item.path for item in choices], ['/dev/sdb', '/dev/sdb1'])
        self.assertEqual(choices[-1].serial, 'different')

    def test_mounted_child_blocks_whole_disk(self):
        listing = {'blockdevices': [{'path': '/dev/fake', 'children': [
            {'path': '/dev/fake1', 'mountpoints': ['/mnt/in-use']}]}]}
        with patch('toolkit.subprocess.check_output', return_value=json.dumps(listing)):
            with self.assertRaisesRegex(ValueError, 'используются'):
                toolkit.assert_unused('/dev/fake')

    def test_new_disk_generation_rejected(self):
        old = toolkit.Device('/dev/fake', 'disk', 1024, '', '', '', False, '8:0', '/dev/fake', ('', '', '', '10'))
        new = toolkit.Device('/dev/fake', 'disk', 1024, '', '', '', False, '8:0', '/dev/fake', ('', '', '', '11'))
        with patch('toolkit.devices', return_value=[new]):
            with self.assertRaisesRegex(ValueError, 'изменилось'):
                toolkit.check_device(old, '/dev/usb')

    def test_exfat_read_only(self):
        device = toolkit.Device('/dev/fake1', 'part', 100, '', '', 'exfat', False, '8:1', '/dev/fake')
        job = Job('exfat', None)
        with patch('toolkit.check_device') as check, patch.object(job, 'command', return_value=(0, '')) as command:
            toolkit.filesystem_job(device, '/dev/usb')(job)
        self.assertTrue(check.call_args.kwargs['unmounted'])
        self.assertEqual(command.call_args.args[0], ['fsck.exfat', '-n', '/dev/fake1'])


class DiagnosticTests(unittest.TestCase):
    def test_composite_report_all_failures(self):
        job = Job('report', None)
        with patch.object(job, 'command', side_effect=OSError('not available')):
            toolkit.report_job('services')(job)
        self.assertTrue(job.failed)
        self.assertEqual(len(job.sections), 3)

    def test_composite_report_partial_is_not_green(self):
        job = Job('report', None)
        with patch.object(job, 'command', side_effect=[(0, 'ok'), TimeoutError('timeout'), (0, 'ok')]):
            toolkit.report_job('services')(job)
        self.assertFalse(job.failed)
        self.assertTrue(job.incomplete)

    def test_no_network_is_failure_and_skips_external_probes(self):
        job = Job('network', None)
        with patch.object(job, 'command', return_value=(1, 'missing')) as command, patch('network.wired_interfaces', return_value=[]):
            network.network_job(job)
        self.assertTrue(job.failed)
        self.assertNotIn('curl', [call.args[0][0] for call in command.call_args_list])

    def test_ping_blocked_does_not_hide_working_https(self):
        def execute(command, **kwargs):
            if command[:3] == ['ip', '-j', 'address']:
                return 0, json.dumps([{'ifname': 'eth0', 'flags': ['LOWER_UP'], 'addr_info': [{'local': '10.0.2.15', 'scope': 'global', 'family': 'inet'}]}])
            if 'route' in command:
                return 0, '[{"dst":"default","gateway":"10.0.2.2","dev":"eth0"}]'
            return (1, '') if command[0] == 'ping' else (0, 'ok')
        job = Job('network', None)
        with patch.object(job, 'command', side_effect=execute), patch('network.wired_interfaces', return_value=[]):
            network.network_job(job)
        self.assertFalse(job.failed or job.incomplete)

    def test_storage_linger_does_not_block_other_jobs(self):
        with patch.object(Job, 'lingering', [(Mock(), 'storage')]):
            job = Job('independent', lambda j: j.command([sys.executable, '-c', 'print("ok")'])).start()
            self.assertTrue(job.done.wait(3))
            self.assertFalse(job.failed)
            blocked = Job('blocked', lambda j: j.command(['smartctl', '--scan-open'])).start()
            self.assertTrue(blocked.done.wait(3))
            self.assertTrue(blocked.failed)

    def test_hash_worker_matches_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.iso'
            path.write_bytes(b'example content' * 100000)
            image = types.SimpleNamespace(path=path, size=path.stat().st_size)
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            path.with_name('test.iso.sha256').write_text(expected)
            job = Job('hash', toolkit.checksum_job(image)).start()
            self.assertTrue(job.done.wait(5))
            self.assertFalse(job.failed)
            self.assertIn('совпадает', job.result)

    def test_charge_based_battery_health(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            for name, value in {'type': 'Battery', 'charge_full': '3000000', 'charge_full_design': '4000000', 'status': 'Discharging'}.items():
                (path / name).write_text(value)
            rows = hardware.battery_lines(path)
            self.assertTrue(any('75.0 процентов' in row for row in rows))
            self.assertIn('Состояние: разряжается', rows)

    def test_hash_without_valid_reference_is_incomplete(self):
        for reference in (None, 'invalid'):
            with self.subTest(reference=reference), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'example.iso'
                path.write_bytes(b'test')
                if reference is not None:
                    path.with_name('example.iso.sha256').write_text(reference)
                job = Job('hash', toolkit.checksum_job(types.SimpleNamespace(path=path, size=4))).start()
                self.assertTrue(job.done.wait(5))
                self.assertFalse(job.failed)
                self.assertTrue(job.incomplete)

    def test_link_local_address_is_not_dhcp_success(self):
        self.assertEqual(network.addresses([{'ifname': 'eth0', 'addr_info': [
            {'scope': 'global', 'local': '169.254.1.2', 'family': 'inet'}]}]), [])

    def test_partial_rescue_is_incomplete_and_requires_unused_source(self):
        device = toolkit.Device('/dev/fake', 'disk', 1024, '', '', '', False, '8:0', '/dev/fake')
        with tempfile.TemporaryDirectory() as folder:
            medium = Path(folder)
            (medium / 'flashos').mkdir()
            (medium / 'flashos/flashos.marker').touch()
            job = Job('rescue', None)
            def command(args, **kwargs):
                return (1, '') if '--done-status' in args else (0, '')
            with patch('toolkit.check_device') as check, patch('toolkit.subprocess.check_output', side_effect=[
                '{"filesystems":[{"source":"/dev/usb1","fstype":"exfat"}]}', 'usb\n', '/dev/usb1\n']), patch.object(job, 'command', side_effect=command):
                toolkit.rescue_job(device, '/dev/usb', medium)(job)
            self.assertTrue(job.incomplete)
            self.assertTrue(check.call_args.kwargs['unmounted'])

    def test_rescue_rejects_oversize_fat32_before_remount(self):
        device = toolkit.Device('/dev/fake', 'disk', 4 * 1024**3, '', '', '', False, '8:0', '/dev/fake')
        job = Job('rescue', None)
        with patch('toolkit.check_device'), patch('toolkit.subprocess.check_output', return_value=
            '{"filesystems":[{"source":"/dev/usb1","fstype":"vfat"}]}'), patch.object(job, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'FAT32'):
                toolkit.rescue_job(device, '/dev/usb', '/fake')(job)
        command.assert_not_called()


class SmartTests(unittest.TestCase):
    def run_report(self, code, data):
        job = Job('smart', None)
        device = toolkit.Device('/dev/fake', 'disk', 100, '', '', '', False, '8:0', '/dev/fake')
        with patch('toolkit.check_device'), patch.object(job, 'command', return_value=(code, json.dumps(data))) as call:
            smart.report_job(device, '/dev/usb')(job)
        self.assertIn('-x', call.call_args.args[0])
        self.assertIn('--json=ov', call.call_args.args[0])
        return job

    def test_actual_wd_unsupported_self_test_retains_all_metrics(self):
        data = {'smart_status': {'passed': True}, 'smartctl': {'messages': [{'string': 'Read Self-test Log failed: Invalid Field in Command (0x4002)'}]},
                'nvme_smart_health_information_log': {'temperature': 59, 'percentage_used': 3, 'data_units_read': 29610398,
                    'unsafe_shutdowns': 25, 'power_on_hours': 1964, 'media_errors': 0, 'temperature_sensors': [74, 59]}}
        job = self.run_report(4, data)
        self.assertFalse(job.failed)
        self.assertTrue(job.incomplete)
        self.assertTrue(any('отклонил запрос' in row for row in job.lines))
        allrows = '\n'.join(row for _, rows in job.sections for row in rows)
        self.assertIn('1964', allrows)
        self.assertIn('29610398', allrows)
        self.assertIn('74', allrows)
        self.assertIn('smart.json', job.attachments)

    def test_exit_bit3_is_critical_even_without_health_object(self):
        self.assertTrue(self.run_report(8, {}).failed)

    def test_history_warning_is_not_silent_success(self):
        job = self.run_report(128, {'smart_status': {'passed': True}})
        self.assertTrue(job.warning)
        self.assertFalse(job.failed)

    def test_pending_ata_sectors_warn_even_when_health_passes(self):
        job = self.run_report(0, {'smart_status': {'passed': True}, 'ata_smart_attributes': {'table': [
            {'name': 'Current_Pending_Sector', 'raw': {'value': 8}}]}})
        self.assertTrue(job.warning)

    def test_usb_scan_preserves_database_protocol(self):
        job = Job('scan', None)
        disk = toolkit.Device('/dev/sdb', 'disk', 100, 'USB SSD', '', '', False, '8:16', '/dev/sdb')
        with patch('toolkit.devices', return_value=[disk]), patch('smart.node_identity', return_value=(1, 2, 3)), patch.object(job, 'command', return_value=(0, '{"devices":[{"name":"/dev/sdb","type":"sntrealtek"},{"name":"/dev/sda","type":"sat"}]}')):
            smart.discovery(job, '/dev/sda')
        self.assertEqual(len(job.payload), 1)
        self.assertEqual(job.payload[0].driver, 'sntrealtek')

    def test_ata_vendor_attribute_preserved(self):
        job = self.run_report(0, {'smart_status': {'passed': True}, 'ata_smart_attributes': {'table': [
            {'id': 250, 'name': 'Vendor_Specific', 'value': 100, 'worst': 95, 'thresh': 10, 'raw': {'string': '12345678901234567890'}}]}})
        rows = '\n'.join(row for _, rows in job.sections for row in rows)
        self.assertIn('12345678901234567890', rows)
        self.assertIn('Vendor Specific', rows)

    def test_nvme_page_does_not_repeat_section_name_on_every_line(self):
        rows = dict(smart.sections({'nvme_smart_health_information_log': {'temperature': 50, 'power_on_hours': 100}}))['Показатели NVMe']
        self.assertEqual(rows, ['Температура, градусов Цельсия: 50', 'Часы работы: 100'])

    def test_single_scsi_metric_keeps_its_name(self):
        rows = dict(smart.sections({'scsi_grown_defect_list': 5}))['Статистика SCSI и SAS']
        self.assertEqual(rows, ['Добавленные дефекты: 5'])
