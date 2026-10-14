"""Coldplug recovery never resets or takes over an existing wireless driver."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jobs import Cancelled
import wifi_hardware as hardware


PCI_ALIAS = 'pci:v00008086d00007E40sv00008086sd00000094bc02sc80i00'
USB_ALIAS = 'usb:v0BDApB812d0200dc00dsc00dp00icFFiscFFipFFin00'


class ColdplugTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sys = self.root / 'sys'
        (self.sys / 'module').mkdir(parents=True)
        self.device = self.sys / 'bus/pci/devices/card0'
        self.device.mkdir(parents=True)
        (self.device / 'class').write_text('0x028000\n')
        (self.device / 'modalias').write_text(PCI_ALIAS + '\n')
        self.modules = self.root / 'modules'
        self.module = self.modules / 'fixture/kernel/drivers/net/wireless/testcard.ko.xz'
        self.module.parent.mkdir(parents=True)
        self.module.write_bytes(b'mocked kernel module')
        self.dependency = self.modules / 'fixture/kernel/net/wireless/cfg80211.ko.xz'
        self.dependency.parent.mkdir(parents=True)
        self.dependency.write_bytes(b'mocked dependency')
        self.job = Mock()
        self.job.cancel_event.is_set.return_value = False
        self.job.command.side_effect = self.command
        self.interfaces = []
        self.plan = f'insmod {self.dependency}\ninsmod {self.module}\n'
        self.load_code, self.load_output = 0, ''
        self.loads = []

    def command(self, arguments, **kwargs):
        if '--resolve-alias' in arguments:
            return 0, 'testcard\n'
        if arguments[0] == 'modinfo':
            return 0, str(self.module) + '\n'
        if '--show-depends' in arguments:
            return 0, self.plan
        self.loads.append(arguments)
        if not self.load_code:
            (self.sys / 'module/testcard').mkdir(exist_ok=True)
            self.interfaces.append('wlan0')
        return self.load_code, self.load_output

    def recover(self):
        with patch('wifi_hardware.wireless_interfaces', side_effect=lambda: list(self.interfaces)):
            return hardware.recover_missing_interfaces(self.job, self.sys, self.modules, 'fixture')

    def test_matching_unloaded_pci_driver_loads_once_with_blacklist_policy(self):
        report = self.recover()
        self.assertTrue(report['attempted'])
        self.assertEqual(report['loaded'], ['testcard'])
        self.assertEqual(self.loads, [['modprobe', '-S', 'fixture', '--use-blacklist', 'testcard']])
        for call in self.job.command.call_args_list:
            self.assertFalse(call.kwargs['log_output'])
            self.assertEqual(call.kwargs['resource'], 'network')
            self.assertLessEqual(call.kwargs['timeout'], 4)
        self.assertEqual(report['devices'][0]['status'], 'module_loaded')

    def test_existing_wireless_interface_prevents_even_discovery_commands(self):
        self.interfaces.append('wlan0')
        report = self.recover()
        self.assertEqual(report['skipped'], 'wireless_interface_exists')
        self.job.command.assert_not_called()

    def test_unknown_wireless_inventory_fails_closed(self):
        with patch('wifi_hardware.wireless_interfaces', side_effect=OSError('unavailable')):
            report = hardware.recover_missing_interfaces(self.job, self.sys, self.modules, 'fixture')
        self.assertEqual(report['skipped'], 'wireless_inventory_unavailable')
        self.job.command.assert_not_called()

    def test_bound_driver_is_reported_without_reloading_it(self):
        with patch('wifi_hardware._driver', return_value='iwlwifi'):
            report = self.recover()
        self.assertFalse(report['attempted'])
        self.assertEqual(report['devices'][0]['driver'], 'iwlwifi')
        self.assertEqual(report['devices'][0]['status'], 'driver_already_bound')
        self.assertEqual(self.loads, [])

    def test_loaded_module_with_failed_probe_is_never_reset_or_reloaded(self):
        (self.sys / 'module/testcard').mkdir()
        report = self.recover()
        self.assertFalse(report['attempted'])
        self.assertTrue(report['devices'][0]['already_loaded'])
        self.assertEqual(report['devices'][0]['status'], 'module_already_loaded')
        self.assertEqual(self.loads, [])

    def test_blacklisted_module_cannot_be_bypassed_by_explicit_module_name(self):
        self.plan = ''
        report = self.recover()
        self.assertFalse(report['attempted'])
        self.assertEqual(report['devices'][0]['status'], 'blacklisted_or_unavailable')
        self.assertEqual(self.loads, [])

    def test_custom_install_handler_or_missing_dependency_prevents_loading(self):
        for plan in ('install /bin/true\n', f'insmod {self.dependency}\ninstall /bin/false\n',
                     f'insmod {self.root / "missing.ko"}\ninsmod {self.module}\n'):
            with self.subTest(plan=plan):
                self.plan = plan
                report = self.recover()
                self.assertFalse(report['attempted'])
                self.assertEqual(report['devices'][0]['status'], 'unexpected_load_plan')
        self.assertEqual(self.loads, [])

    def test_non_wireless_modinfo_path_cannot_be_loaded(self):
        self.module = self.modules / 'fixture/kernel/drivers/usb/testcard.ko.xz'
        self.module.parent.mkdir(parents=True)
        self.module.write_bytes(b'not wireless')
        report = self.recover()
        self.assertEqual(report['devices'], [])
        self.assertEqual(self.loads, [])

    def test_pci_ethernet_device_is_never_a_wireless_recovery_candidate(self):
        (self.device / 'class').write_text('0x020000\n')
        report = self.recover()
        self.assertEqual(report['devices'], [])
        self.job.command.assert_not_called()

    def test_unsafe_alias_never_reaches_a_command(self):
        (self.device / 'modalias').write_text(PCI_ALIAS + '\n--install=/bin/false')
        report = self.recover()
        self.assertTrue(report['errors'])
        self.job.command.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'Linux sysfs USB interface names contain a colon')
    def test_usb_interface_alias_can_recover_without_touching_parent_device(self):
        (self.device / 'class').write_text('0x020000\n')
        parent = self.sys / 'bus/usb/devices/1-2'
        parent.mkdir(parents=True)
        (parent / 'modalias').write_text(USB_ALIAS)
        interface = self.sys / 'bus/usb/devices/1-2:1.0'
        interface.mkdir()
        (interface / 'modalias').write_text(USB_ALIAS)
        report = self.recover()
        self.assertEqual(report['loaded'], ['testcard'])
        self.assertEqual([row['address'] for row in report['devices']], ['1-2:1.0'])

    def test_hotplug_or_binding_after_preflight_prevents_action(self):
        original = self.command
        def command(arguments, **kwargs):
            result = original(arguments, **kwargs)
            if '--show-depends' in arguments:
                self.interfaces.append('wlan1')
            return result
        self.job.command.side_effect = command
        report = self.recover()
        self.assertFalse(report['attempted'])
        self.assertEqual(report['devices'][0]['status'], 'device_became_active')
        self.assertEqual(self.loads, [])

    def test_device_alias_replaced_after_preflight_prevents_loading(self):
        original = self.command
        def command(arguments, **kwargs):
            result = original(arguments, **kwargs)
            if '--show-depends' in arguments:
                (self.device / 'modalias').write_text(PCI_ALIAS.replace('7E40', '7E41'))
            return result
        self.job.command.side_effect = command
        report = self.recover()
        self.assertFalse(report['attempted'])
        self.assertEqual(self.loads, [])

    def test_modprobe_failure_preserves_diagnostic_error_without_other_mutations(self):
        self.load_code, self.load_output = 1, 'modprobe: ERROR: could not insert testcard: Required key not available'
        report = self.recover()
        self.assertTrue(report['attempted'])
        self.assertEqual(report['loaded'], [])
        self.assertEqual(report['devices'][0]['status'], 'load_failed')
        self.assertIn('Required key not available', report['devices'][0]['load_error'])
        self.assertEqual(len(self.loads), 1)

    def test_cancelled_job_never_loads_a_module(self):
        self.job.check.side_effect = Cancelled()
        with self.assertRaises(Cancelled):
            self.recover()
        self.job.command.assert_not_called()

    def test_expired_budget_stops_before_commands(self):
        with patch('wifi_hardware.time.monotonic', side_effect=[0, 20, 20]):
            report = self.recover()
        self.assertFalse(report['attempted'])
        self.assertTrue(report['errors'])
        self.job.command.assert_not_called()

    def test_unreadable_driver_binding_fails_closed(self):
        with patch('wifi_hardware._driver', side_effect=PermissionError('driver state unavailable')):
            report = self.recover()
        self.assertFalse(report['attempted'])
        self.assertTrue(report['errors'])
        self.assertEqual(self.loads, [])


if __name__ == '__main__':
    unittest.main()
