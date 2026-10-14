"""Wi-Fi inventory failures must not be mistaken for missing adapters."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jobs import Job
import network
import wifi
import network_runtime as runtime
from tests.test_network_runtime import CHECKPOINT, FakeManager, NEW, OLD


class ScanTests(unittest.TestCase):
    def scan(self, devices='', device_code=0, points='', scan_code=0,
             blocks=(False, False), kernel='', kernel_interfaces=(), recovery=None):
        job = Job('scan', None)
        job.cancel_event = Mock()
        job.cancel_event.is_set.return_value = False

        def command(args, **kwargs):
            if args[-1] == 'device':
                return device_code, devices
            if args == ['iw', 'dev']:
                return 0, kernel
            if 'BSSID,SSID,SIGNAL,SECURITY,DEVICE' in args:
                return scan_code, points
            return 0, ''

        with patch.object(job, 'command', side_effect=command) as commands, \
                patch('wifi.radio_blocks', return_value=blocks), \
                patch('wifi.wireless_interfaces', return_value=kernel_interfaces), \
                patch('wifi.recover_missing_interfaces', recovery or Mock(return_value={
                    'attempted': False, 'loaded': [], 'devices': [], 'errors': []})):
            wifi.scan_job(job)
        return job, [call.args[0] for call in commands.call_args_list]

    def test_unmanaged_adapter_is_detected_and_never_claimed_or_scanned(self):
        job, commands = self.scan('wlan0:wifi:unmanaged\n')
        self.assertTrue(job.incomplete)
        self.assertFalse(job.failed)
        self.assertEqual(job.interfaces, [])
        self.assertIn('обнаружен, но не управляется', job.result)
        self.assertNotIn('не найден', job.result)
        self.assertFalse(any('BSSID,SSID,SIGNAL,SECURITY,DEVICE' in args for args in commands))
        self.assertFalse(any('managed' in args for args in commands))
        self.assertIn('wlan0: unmanaged', str(job.sections))

    def test_kernel_interface_missing_from_nm_is_not_diagnosed_as_missing_driver(self):
        job, _ = self.scan(kernel='phy#0\n\tInterface wlan0\n\t\ttype managed\n')
        self.assertIn('Драйвер создал Wi-Fi интерфейс', job.result)
        self.assertIn('wifi-pci.txt', job.attachments)
        self.assertIn('wifi-usb.txt', job.attachments)
        self.assertIn('wifi-kernel.txt', job.attachments)

    def test_no_kernel_interface_explains_driver_or_firmware_and_preserves_evidence(self):
        job, commands = self.scan()
        self.assertIn('Ядро не зарегистрировало', job.result)
        self.assertIn('прошивка', job.result)
        self.assertIn(['lspci', '-nnk'], commands)
        self.assertIn(['lsusb'], commands)
        self.assertTrue(any(args[0] == 'journalctl' for args in commands))

    def test_nm_failure_keeps_service_error_instead_of_claiming_hardware_absent(self):
        job, commands = self.scan('Error: NetworkManager is not running.', device_code=10)
        self.assertTrue(job.failed)
        self.assertIn('NetworkManager', job.result)
        self.assertIn('NetworkManager is not running', str(job.sections))
        self.assertEqual(job.interfaces, [])
        self.assertFalse(any('BSSID,SSID,SIGNAL,SECURITY,DEVICE' in args for args in commands))

    def test_hardware_or_software_block_explains_scan_failure(self):
        for blocks, expected in (((True, False), 'отключён аппаратно'),
                                 ((False, True), 'заблокирован программно')):
            with self.subTest(blocks=blocks):
                job, _ = self.scan('wlan0:wifi:unavailable\n', scan_code=1, blocks=blocks)
                self.assertTrue(job.incomplete)
                self.assertIn(expected, job.result)
                self.assertNotIn('адаптер не найден', job.result)

    def test_empty_successful_scan_preserves_hidden_network_flow(self):
        job, commands = self.scan('wlan0:wifi:disconnected\n')
        self.assertEqual(job.interfaces, ['wlan0'])
        self.assertFalse(job.failed or job.incomplete)
        self.assertIn('введите скрытую сеть', job.result)
        self.assertEqual(sum('BSSID,SSID,SIGNAL,SECURITY,DEVICE' in args for args in commands), 3)

    def test_first_scan_eventually_returns_network_without_using_unmanaged_radio(self):
        job = Job('scan', None)
        job.cancel_event = Mock()
        job.cancel_event.is_set.return_value = False
        scans = []

        def command(args, **kwargs):
            if args[-1] == 'device':
                return 0, 'wlan0:wifi:unmanaged\nwlan1:wifi:disconnected\n'
            if 'BSSID,SSID,SIGNAL,SECURITY,DEVICE' in args:
                scans.append(args)
                return 0, '' if len(scans) == 1 else r'AA\:BB\:CC\:DD\:EE\:FF:Test:80:WPA2:wlan1'
            return 0, ''

        with patch.object(job, 'command', side_effect=command):
            wifi.scan_job(job)
        self.assertEqual(job.interfaces, ['wlan1'])
        self.assertEqual(len(job.payload), 1)
        self.assertFalse(job.failed or job.incomplete)
        self.assertTrue(all('wlan1' in args and 'wlan0' not in args for args in scans))
        job.cancel_event.wait.assert_called_once_with(2)

    def test_hotplug_inventory_and_unavailable_scan_can_recover_without_false_failure(self):
        job = Job('scan', None)
        job.cancel_event = Mock()
        job.cancel_event.is_set.return_value = False
        inventories, scans = [], []

        def command(args, **kwargs):
            if args[-1] == 'device':
                inventories.append(args)
                return (0, '') if len(inventories) == 1 else (0, 'wlan0:wifi:unavailable\n')
            if 'BSSID,SSID,SIGNAL,SECURITY,DEVICE' in args:
                scans.append(args)
                return (1, 'Device unavailable') if len(scans) == 1 else (
                    0, r'AA\:BB\:CC\:DD\:EE\:FF:Test:80:WPA2:wlan0')
            return 0, ''

        with patch.object(job, 'command', side_effect=command):
            wifi.scan_job(job)
        self.assertFalse(job.failed or job.incomplete)
        self.assertEqual(job.interfaces, ['wlan0'])
        self.assertEqual(len(job.payload), 1)
        self.assertEqual((len(inventories), len(scans)), (2, 2))

    def test_diagnostic_commands_being_absent_does_not_hide_original_nm_state(self):
        job = Job('scan', None)

        def command(args, **kwargs):
            if args[-1] == 'device':
                return 0, 'wlan0:wifi:unmanaged\n'
            if args[0] != 'nmcli':
                raise OSError('tool absent')
            return 0, ''

        with patch.object(job, 'command', side_effect=command), \
                patch('wifi.radio_blocks', return_value=(False, False)):
            wifi.scan_job(job)
        self.assertIn('не управляется NetworkManager', job.result)
        self.assertIn('wifi-kernel.txt', job.attachments)
        self.assertIn('wifi-udev.txt', job.attachments)

    def test_unavailable_kernel_inventory_does_not_assert_absent_interfaces(self):
        job = Job('scan', None)
        with patch.object(job, 'command', side_effect=OSError('tool absent')), \
                patch('wifi.wireless_interfaces', side_effect=OSError('sysfs absent')), \
                patch('wifi.radio_blocks', return_value=(False, False)):
            result = wifi.scan_diagnostics(job, [])
        self.assertIn('Не удалось проверить', result)
        self.assertNotIn('не зарегистрировало', result)

    def test_kernel_report_keeps_early_probe_and_info_records_beyond_recent_warnings(self):
        job = Job('diagnosis', None)
        records = [(6, 'iwlwifi: detected Intel AX211'), (3, 'iwlwifi: probe failed with error -110')]
        records += [(4, 'unrelated warning ' + str(index)) for index in range(200)]
        records += [(4, 'user-program: private-profile-token')]

        def command(args, **kwargs):
            if args[0] == 'journalctl' and '-k' in args:
                selected = [text for level, text in records if '-p' not in args or level <= 4]
                limit = int(args[args.index('-n') + 1])
                return 0, '\n'.join(selected[-limit:])
            return 0, ''

        with patch.object(job, 'command', side_effect=command), \
                patch('wifi.wireless_interfaces', return_value=[]), \
                patch('wifi.radio_blocks', return_value=(False, False)):
            wifi.scan_diagnostics(job, [])
        self.assertIn('detected Intel AX211', job.attachments['wifi-kernel.txt'])
        self.assertIn('probe failed with error -110', job.attachments['wifi-kernel.txt'])
        self.assertNotIn('private-profile-token', str(job.attachments) + str(job.sections))
        self.assertIn('wifi-nm-devices.txt', job.attachments)
        self.assertIn('wifi-udev.txt', job.attachments)

    def test_access_point_interface_names_cannot_become_command_options(self):
        self.assertEqual(wifi.access_points(r'AA\:BB\:CC\:DD\:EE\:FF:Test:80:WPA2:--help'), [])

    def test_missing_interface_recovery_is_followed_by_new_nm_inventory_and_scan(self):
        job = Job('scan', None)
        job.cancel_event = Mock()
        job.cancel_event.is_set.return_value = False
        statuses = iter(['', '', 'wlan0:wifi:disconnected\n'])
        outcome = {'attempted': True, 'loaded': ['iwlwifi'], 'devices': [], 'errors': []}

        def command(args, **kwargs):
            if args[-1] == 'device':
                return 0, next(statuses)
            if 'BSSID,SSID,SIGNAL,SECURITY,DEVICE' in args:
                return 0, r'AA\:BB\:CC\:DD\:EE\:FF:Test:80:WPA2:wlan0'
            return 0, ''

        with patch.object(job, 'command', side_effect=command), \
                patch('wifi.wireless_interfaces', return_value=[]), \
                patch('wifi.recover_missing_interfaces', return_value=outcome) as recover:
            wifi.scan_job(job)
        recover.assert_called_once_with(job)
        self.assertEqual(job.interfaces, ['wlan0'])
        self.assertEqual(len(job.payload), 1)
        self.assertFalse(job.failed or job.incomplete)
        self.assertEqual(json.loads(job.attachments['wifi-recovery.json']), outcome)

    def test_known_nm_adapters_and_kernel_interfaces_never_trigger_recovery(self):
        for devices, kernel in (('wlan0:wifi:disconnected\n', ()),
                                ('wlan0:wifi:unmanaged\n', ()), ('', ('wlan0',))):
            with self.subTest(devices=devices, kernel=kernel):
                recover = Mock()
                self.scan(devices, kernel_interfaces=kernel, recovery=recover)
                recover.assert_not_called()

    def test_bound_driver_without_interface_is_diagnosed_without_claiming_driver_absence(self):
        outcome = {'attempted': False, 'loaded': [], 'devices': [{
            'bus': 'pci', 'address': '0000:00:14.3', 'module': 'iwlwifi', 'alias': 'fixture-alias',
            'driver': 'iwlwifi', 'already_loaded': True, 'status': 'driver_already_bound'}], 'errors': []}
        job, _ = self.scan(recovery=Mock(return_value=outcome))
        self.assertTrue(job.incomplete)
        self.assertIn('Сопоставленный драйвер присутствует', job.result)
        self.assertIn('драйвер уже привязан', str(job.sections))
        self.assertEqual(json.loads(job.attachments['wifi-recovery.json']), outcome)

    def test_nm_errors_never_trigger_module_recovery(self):
        recover = Mock()
        job, _ = self.scan('NetworkManager is not running', device_code=10, recovery=recover)
        self.assertTrue(job.failed)
        recover.assert_not_called()

    def test_unknown_sysfs_inventory_does_not_trigger_recovery(self):
        job = Job('scan', None)
        job.cancel_event = Mock()
        job.cancel_event.is_set.return_value = False
        with patch.object(job, 'command', return_value=(0, '')), \
                patch('wifi.wireless_interfaces', side_effect=OSError('sysfs unavailable')), \
                patch('wifi.radio_blocks', return_value=(False, False)), \
                patch('wifi.recover_missing_interfaces') as recover:
            wifi.scan_job(job)
        recover.assert_not_called()
        self.assertNotIn('wifi-recovery.json', job.attachments)


class KernelInventoryTests(unittest.TestCase):
    def test_rfkill_ignores_bluetooth_and_reads_both_wireless_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, kind, hard, soft in (
                    ('rfkill0', 'bluetooth', '1', '1'),
                    ('rfkill1', 'wlan', '1', '0'),
                    ('rfkill2', 'wlan', '0', '1')):
                entry = root / 'class/rfkill' / name
                entry.mkdir(parents=True)
                for field, value in (('type', kind), ('hard', hard), ('soft', soft)):
                    (entry / field).write_text(value)
            self.assertEqual(wifi.radio_blocks(root), (True, True))
            (root / 'class/rfkill/rfkill1/hard').write_text('0')
            (root / 'class/rfkill/rfkill2/soft').write_text('0')
            self.assertEqual(wifi.radio_blocks(root), (False, False))

    def test_wireless_phy_without_legacy_wireless_directory_is_never_ethernet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('eth0', 'wlan0', 'wlan1', 'lo'):
                entry = root / name
                entry.mkdir()
                if name != 'lo':
                    (entry / 'device').mkdir()
            (root / 'wlan0/phy80211').mkdir()
            (root / 'wlan1/wireless').mkdir()
            with patch('network.Path', return_value=root):
                self.assertEqual(network.wired_interfaces(), ['eth0'])
                self.assertEqual(network.wireless_interfaces(), ['wlan0', 'wlan1'])

    @unittest.skipIf(os.name == 'nt', 'Linux sysfs driver symlinks')
    def test_bound_interface_driver_and_modalias_are_in_the_saved_report_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / 'wlan0/device'
            device.mkdir(parents=True)
            alias = 'pci:v00008086d00007E40sv00008086sd00000094bc02sc80i00'
            (device / 'modalias').write_text(alias)
            driver = root / 'drivers/iwlwifi'
            driver.mkdir(parents=True)
            (device / 'driver').symlink_to(driver, target_is_directory=True)
            job = Job('diagnosis', None)
            with patch('wifi.Path', return_value=root), patch.object(job, 'command', return_value=(0, '')), \
                    patch('wifi.wireless_interfaces', return_value=['wlan0']), \
                    patch('wifi.radio_blocks', return_value=(False, False)):
                wifi.scan_diagnostics(job, [('wlan0', 'unmanaged')])
            self.assertIn('wlan0: драйвер iwlwifi', str(job.sections))
            self.assertIn(alias, str(job.sections))


class WifiReportPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from interface import UI
        except ImportError as error:
            raise unittest.SkipTest('Live interface dependencies unavailable: ' + str(error))
        cls.UI = UI

    def assert_persisted_diagnostic(self, entrypoint, wireless_interface=None):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'flashos').mkdir()
            ui = self.UI(Mock(graphical=True), Mock(),
                         SimpleNamespace(data={'progress_seconds': 30}), root)
            ui.draw = Mock()
            ui.choose = Mock(return_value=None)
            ui.screen.getch.return_value = -1
            real_scan = wifi.scan_job

            def scan(job):
                # Keep kernel/NM discovery and real report writing, without delays.
                job.cancel_event.wait = Mock(return_value=False)
                real_scan(job)

            def command(args, **kwargs):
                if args[-1] == 'device':
                    if wireless_interface:
                        return 0, wireless_interface + ':wifi:disconnected\n'
                    return 0, 'eth0:ethernet:connected\n'
                if 'BSSID,SSID,SIGNAL,SECURITY,DEVICE' in args:
                    return 0, r'AA\:BB\:CC\:DD\:EE\:FF:Test:80:WPA2:' + wireless_interface
                if args[0] == 'lspci':
                    return 0, 'Network controller [0280]: Intel AX211 [8086:7e40]\nKernel modules: iwlwifi\n'
                if args[0] == 'journalctl':
                    return 0, 'iwlwifi 0000:00:14.3: probe failed with error -110\n'
                return 0, ''

            with patch('wifi.scan_job', side_effect=scan), patch('jobs.Job.command', side_effect=command), \
                    patch('wifi.wireless_interfaces', return_value=[]), \
                    patch('wifi.radio_blocks', return_value=(False, False)), \
                    patch('wifi.recover_missing_interfaces', return_value={
                        'attempted': False, 'loaded': [], 'devices': [], 'errors': []}):
                entrypoint(ui)
            reports = root / 'flashos/reports'
            if wireless_interface:
                scans = list(reports.glob('*-wifi-' + wireless_interface + '.txt'))
                self.assertEqual(len(scans), 1)
                self.assertIn('Test:80:WPA2:', scans[0].read_text(encoding='utf-8'))
                self.assertEqual(ui.choose.call_args.args[0], 'Доступные сети Wi-Fi')
                return
            kernel = list(reports.glob('*-wifi-kernel.txt'))
            devices = list(reports.glob('*-wifi-devices.txt'))
            self.assertEqual(len(kernel), 1)
            self.assertEqual(len(devices), 1)
            self.assertIn('probe failed with error -110', kernel[0].read_text(encoding='utf-8'))
            self.assertIn('eth0:ethernet:connected', devices[0].read_text(encoding='utf-8'))
            main = kernel[0].with_name(kernel[0].name.removesuffix('-wifi-kernel.txt') + '.txt')
            self.assertIn('Ядро не зарегистрировало Wi-Fi интерфейс', main.read_text(encoding='utf-8'))

    def test_quick_wifi_failure_persists_hardware_evidence_for_next_boot(self):
        self.assert_persisted_diagnostic(wifi.wifi_menu)

    def test_legal_adapter_name_with_underscore_does_not_interrupt_wifi_scan(self):
        self.assert_persisted_diagnostic(wifi.wifi_menu, wireless_interface='wlan_test0')

    def test_profile_wifi_scan_persists_evidence_if_adapter_disappears_after_selection(self):
        from network_settings import NetworkSettings

        def create(ui):
            settings = NetworkSettings(ui)
            ui.text_input = Mock(return_value='Дом')
            with patch.object(settings, 'choose_device', return_value='wlan0'), \
                    patch('network_settings.machine_id', return_value='a' * 64), \
                    patch('network_settings.device_id', return_value='02:34:56:78:9a:bc'):
                settings.create('wifi')
            self.assertEqual(settings.store.load(), ())

        self.assert_persisted_diagnostic(create)


class QuickConnectionTests(unittest.TestCase):
    point = {'ssid': 'Test', 'device': 'wlan0', 'security': 'WPA2'}

    def connect(self, manager, *, activation_failure=False, cancel_after_activation=False):
        job = Job('wifi', None)
        create = tempfile.NamedTemporaryFile
        with tempfile.TemporaryDirectory() as directory:
            def private_file(*args, **kwargs):
                self.assertEqual(kwargs.pop('dir'), '/run')
                # Windows denies reopening a delete-on-close NamedTemporaryFile.
                # The containing test directory still removes every secret file.
                return create(*args, dir=directory, delete=False, **kwargs)

            def command(args, **kwargs):
                result = manager.command(args, **kwargs)
                if 'passwd-file' in args:
                    # NM has replaced the old connection before activation fails.
                    if activation_failure:
                        return 4, 'Activation failed'
                    if cancel_after_activation:
                        job.cancel()
                return result

            with patch('wifi.NetworkManager', return_value=manager), \
                    patch('wifi.tempfile.NamedTemporaryFile', side_effect=private_file), \
                    patch.object(job, 'command', side_effect=command):
                job._run(wifi.connect_job(self.point, 'private-password'))
        return job

    def manager(self, *, address=True, previous=OLD):
        raw = json.dumps([{'ifname': 'wlan0', 'addr_info': [
            {'local': '192.168.1.2', 'scope': 'global', 'family': 'inet'}] if address else []}])
        return FakeManager(previous=previous, output=raw)

    def test_failed_activation_restores_previous_wifi_and_deletes_only_candidate(self):
        manager = self.manager()
        job = self.connect(manager, activation_failure=True)
        self.assertTrue(job.failed)
        self.assertEqual(manager.active, OLD)
        self.assertEqual(manager.rolled_back, [CHECKPOINT])
        self.assertEqual(manager.deleted, [manager.created])
        self.assertNotIn(OLD, manager.deleted)
        self.assertNotIn('private-password', str(job.lines) + str(job.attachments))

    def test_missing_address_restores_old_wifi_instead_of_leaving_it_disconnected(self):
        manager = self.manager(address=False)
        job = self.connect(manager)
        self.assertTrue(job.failed)
        self.assertEqual(manager.active, OLD)
        self.assertEqual(manager.deleted, [manager.created])

    def test_cancellation_after_activation_rolls_back_before_job_finishes(self):
        manager = self.manager()
        job = self.connect(manager, cancel_after_activation=True)
        self.assertTrue(job.cancelled)
        self.assertEqual(manager.active, OLD)
        self.assertEqual(manager.deleted, [manager.created])

    def test_success_commits_checkpoint_and_keeps_new_ram_connection(self):
        manager = self.manager()
        job = self.connect(manager)
        self.assertFalse(job.failed or job.cancelled)
        self.assertEqual(manager.active, manager.created)
        self.assertEqual(manager.rolled_back, [])
        self.assertEqual(manager.deleted, [])
        self.assertEqual(manager.destroyed, [(CHECKPOINT, True)])

    def test_native_rollback_failure_reactivates_previous_uuid(self):
        manager = self.manager()
        manager.rollback_success = False
        job = self.connect(manager, activation_failure=True)
        self.assertTrue(job.failed)
        self.assertEqual(manager.active, OLD)
        reconnect = [args for args, _ in manager.commands if 'up' in args and OLD in args]
        self.assertEqual(len(reconnect), 1)

    def test_failed_first_connection_restores_disconnected_state(self):
        manager = self.manager(previous=None)
        manager.rollback_success = False
        job = self.connect(manager, activation_failure=True)
        self.assertTrue(job.failed)
        self.assertIsNone(manager.active)
        self.assertEqual(manager.deleted, [manager.created])

    def test_unavailable_checkpoint_stops_before_connection_is_created(self):
        manager = self.manager()
        manager.checkpoint = Mock(side_effect=RuntimeError('Checkpoint unavailable'))
        job = self.connect(manager)
        self.assertTrue(job.failed)
        self.assertEqual(manager.active, OLD)
        self.assertIsNone(manager.created)
        self.assertEqual(manager.commands, [])

    def test_tentative_ipv6_is_not_committed_before_uniqueness_check_finishes(self):
        manager = self.manager()
        address = {'local': '2001:db8::1', 'scope': 'global', 'family': 'inet6'}
        responses = [json.dumps([{'ifname': 'wlan0', 'addr_info': [{**address, 'flags': ['tentative']}]}]),
                     json.dumps([{'ifname': 'wlan0', 'addr_info': [address]}])]
        original = manager.command

        def command(args, **kwargs):
            if args[0] == 'ip':
                manager.commands.append((args, kwargs))
                return 0, responses.pop(0)
            return original(args, **kwargs)

        manager.command = command
        with patch('network_runtime.time.sleep'):
            job = self.connect(manager)
        self.assertFalse(job.failed)
        self.assertEqual(responses, [])
        self.assertEqual(manager.destroyed, [(CHECKPOINT, True)])

    def test_duplicate_ipv6_address_rolls_back_the_previous_connection(self):
        manager = self.manager()
        manager.addresses = json.dumps([{'ifname': 'wlan0', 'addr_info': [
            {'local': '2001:db8::1', 'scope': 'global', 'family': 'inet6', 'flags': ['dadfailed']}]}])
        job = self.connect(manager)
        self.assertTrue(job.failed)
        self.assertEqual(manager.active, OLD)
        self.assertEqual(manager.deleted, [manager.created])

    def test_rollback_error_still_removes_candidate_and_keeps_timeout_backstop(self):
        manager = self.manager()
        manager.wait_active_uuid = Mock(side_effect=RuntimeError('Adapter did not recover'))
        self.assertFalse(runtime.rollback_temporary_connection(manager, 'wlan0', NEW, CHECKPOINT, OLD))
        self.assertEqual(manager.deleted, [NEW])
        self.assertEqual(manager.destroyed, [])

    def test_fallback_does_not_replace_an_unrelated_connection(self):
        manager = self.manager()
        other = '019badfb-695c-7567-b6db-9d96f7a4e41'
        manager.active = other
        manager.rollback_success = False
        self.assertFalse(runtime.rollback_temporary_connection(manager, 'wlan0', NEW, CHECKPOINT, OLD))
        self.assertEqual(manager.active, other)
        self.assertEqual(manager.commands, [])
        self.assertEqual(manager.deleted, [NEW])


if __name__ == '__main__':
    unittest.main()
