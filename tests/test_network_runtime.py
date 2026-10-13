"""Network changes are explicit, device-bound and provisional until confirmed."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import network_runtime as runtime
from network_profiles import IPSettings, NetworkProfile, ProfileScope, Route, WifiSettings

MACHINE = 'a' * 64
MAC = '52:54:00:12:34:56'
OLD = 'aee472e5-2118-46b9-a447-998510f91f1d'
NEW = 'd5b627f8-8ef8-4335-87de-59ce7b2bb3d7'
CHECKPOINT = '/org/freedesktop/NetworkManager/Checkpoint/1'
DEVICE = '/org/freedesktop/NetworkManager/Devices/2'


def profile(**changes):
    value = NetworkProfile('7536f29d-6f69-4997-9a3d-bc1e9c39a541', 'Рабочая сеть', 'wired',
                           ProfileScope(MACHINE, MAC))
    return replace(value, **changes)


def addresses(*values, **properties):
    return json.dumps([{'ifname': 'eth0', 'addr_info': [
        {'local': value, 'family': 'inet6' if ':' in value else 'inet', 'scope': 'global',
         **properties} for value in values]}])


class FakeManager:
    def __init__(self, previous=OLD, output=None):
        self.active = previous
        self.previous = previous
        self.commands = []
        self.addresses = output or addresses('10.0.2.15')
        self.created = None
        self.rollback_success = True
        self.activation_state = 100
        self.destroy_failure = False
        self.activation_failure = False
        self.seen_secret = None
        self.seen_permissions = None
        self.checkpoint_calls = []
        self.deleted = []
        self.adjusted = []
        self.destroyed = []
        self.rolled_back = []

    def command(self, argv, **kwargs):
        self.commands.append((argv, kwargs))
        if argv[:3] == ['nmcli', 'connection', 'add']:
            self.created = argv[argv.index('connection.uuid') + 1]
        elif argv[0] == 'nmcli' and 'up' in argv:
            if 'passwd-file' in argv:
                path = Path(argv[argv.index('passwd-file') + 1])
                self.seen_secret = path.read_text(encoding='utf-8')
                self.seen_permissions = path.stat().st_mode & 0o777
            if self.activation_failure:
                raise RuntimeError('Не подключено.')
            self.active = argv[argv.index('uuid') + 1]
        elif argv[:3] == ['nmcli', 'connection', 'down']:
            self.active = None
        elif argv[0] == 'ip':
            return 0, self.addresses
        return 0, ''

    def active_uuid(self, interface, cleanup=False, activated=False):
        return self.active if not activated or self.activation_state == 100 else None

    def connection_status(self, interface, cleanup=False):
        return self.active, self.activation_state

    def device_path(self, interface):
        return DEVICE

    def checkpoint(self, path, seconds):
        self.checkpoint_calls.append((path, seconds))
        return CHECKPOINT

    def adjust(self, path, seconds):
        self.adjusted.append((path, seconds))

    def destroy(self, path, required=True):
        self.destroyed.append((path, required))
        if self.destroy_failure and required:
            raise RuntimeError('Checkpoint destroy failed')
        return not self.destroy_failure

    def rollback(self, path):
        self.rolled_back.append(path)
        if self.rollback_success:
            self.active = self.previous
        return self.rollback_success

    def wait_active_uuid(self, interface, identifier, seconds=15):
        return self.active

    def delete(self, identifier):
        self.deleted.append(identifier)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name) / 'active.json'
        self.manager = FakeManager()
        self.job = Mock()
        self.job.lines = []
        self.job.cancel_event = threading.Event()
        self.patchers = [patch('network_runtime.NetworkManager', return_value=self.manager),
                         patch('network_runtime.available_devices', return_value=[
                             {'interface': 'eth0', 'kind': 'wired', 'state': 'connected'}]),
                         patch('network_runtime.machine_id', return_value=MACHINE),
                         patch('network_runtime.device_id', return_value=MAC),
                         patch('network_runtime.threading.Timer'),
                         patch('network_runtime.threading.Thread')]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def connect(self, value=None, **kwargs):
        runtime.connect_job(value or profile(), 'eth0', state_path=self.state, **kwargs)(self.job)
        return self.job.payload

    def transaction(self, **kwargs):
        value = runtime.ConnectionTransaction(self.manager, profile(), 'eth0', NEW, CHECKPOINT,
                                             OLD, MAC, MACHINE, self.state, **kwargs)
        self.manager.active = NEW
        value.deadline = time.monotonic() + 60
        return value

    def test_connect_is_pending_and_does_not_write_or_destroy_checkpoint(self):
        transaction = self.connect()
        self.assertTrue(transaction.pending)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.manager.destroyed, [])
        self.assertEqual(self.manager.checkpoint_calls, [(DEVICE, 130)])
        self.assertEqual(self.manager.adjusted, [(CHECKPOINT, 65)])

    def test_confirmation_records_only_validated_profile(self):
        transaction = self.connect()
        transaction.confirm()
        value = json.loads(self.state.read_text(encoding='utf-8'))
        self.assertEqual(value['profile'], profile().to_dict())
        self.assertEqual(value['machine'], MACHINE)
        self.assertEqual(value['device'], MAC)
        self.assertEqual(value['connection_uuid'], self.manager.created)
        self.assertEqual(self.manager.destroyed, [(CHECKPOINT, True)])
        self.assertFalse(transaction.pending)
        transaction.confirm()
        self.assertEqual(len(self.manager.destroyed), 1)

    def test_rejection_rolls_back_exact_previous_connection_and_deletes_candidate(self):
        transaction = self.connect()
        transaction.rollback()
        self.assertEqual(self.manager.active, OLD)
        self.assertEqual(self.manager.rolled_back, [CHECKPOINT])
        self.assertEqual(self.manager.deleted, [self.manager.created])
        self.assertFalse(transaction.pending)
        transaction.rollback()
        self.assertEqual(len(self.manager.rolled_back), 1)

    def test_expired_confirmation_never_persists(self):
        transaction = self.transaction()
        transaction.deadline = time.monotonic() - 1
        with self.assertRaisesRegex(RuntimeError, 'истекло'):
            transaction.confirm()
        self.assertEqual(self.manager.active, OLD)
        self.assertFalse(self.state.exists())

    def test_changed_active_uuid_is_not_confirmed(self):
        transaction = self.transaction()
        self.manager.active = str(uuid.uuid4())
        with self.assertRaisesRegex(RuntimeError, 'Соединение изменилось'):
            transaction.confirm()
        self.assertFalse(self.state.exists())

    def test_connecting_candidate_uuid_is_not_confirmed(self):
        transaction = self.transaction()
        self.manager.activation_state = 70
        with self.assertRaisesRegex(RuntimeError, 'Соединение изменилось'):
            transaction.confirm()
        self.assertFalse(self.state.exists())

    def test_changed_permanent_mac_or_machine_is_not_confirmed(self):
        for function, result in [('device_id', '52:54:00:99:34:56'), ('machine_id', 'b' * 64)]:
            with self.subTest(function=function):
                transaction = self.transaction()
                with patch('network_runtime.' + function, return_value=result), \
                        self.assertRaisesRegex(RuntimeError, 'адаптер изменился'):
                    transaction.confirm()
                self.assertFalse(self.state.exists())

    def test_checkpoint_destroy_failure_restores_previous_state(self):
        old = {'format': 1, 'interface': 'eth0', 'connection_uuid': OLD, 'machine': MACHINE,
               'device': MAC, 'profile': profile().to_dict()}
        runtime._write_state(self.state, old)
        transaction = self.transaction()
        self.manager.destroy_failure = True
        with self.assertRaisesRegex(RuntimeError, 'Checkpoint destroy'):
            transaction.confirm()
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8')), old)
        self.assertEqual(self.manager.active, OLD)

    def test_failed_confirmation_on_other_nic_restores_previous_proxy_context(self):
        other_uuid = 'b119fd8c-8084-450f-8b1a-12ac2ff4ae72'
        old = {'format': 1, 'interface': 'eth1', 'connection_uuid': other_uuid,
               'machine': MACHINE, 'device': '52:54:00:12:34:99', 'profile': profile().to_dict()}
        runtime._write_state(self.state, old)
        transaction = self.transaction()
        self.manager.destroy_failure = True
        with self.assertRaisesRegex(RuntimeError, 'Checkpoint destroy'):
            transaction.confirm()
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8')), old)
        self.assertEqual(self.manager.active, OLD)
        self.assertNotIn(other_uuid, self.manager.deleted)

    def test_rollback_fallback_activates_previous_uuid_after_native_timeout(self):
        transaction = self.transaction()
        self.manager.rollback_success = False
        transaction.rollback()
        commands = [argv for argv, _ in self.manager.commands]
        self.assertIn(['nmcli', 'connection', 'down', 'uuid', NEW], commands)
        self.assertIn(['nmcli', '--wait', '15', 'connection', 'up', 'uuid', OLD,
                       'ifname', 'eth0'], commands)
        self.assertEqual(self.manager.active, OLD)

    def test_rollback_wait_failure_cleans_candidate_and_keeps_confirmed_context(self):
        old = {'format': 1, 'interface': 'eth0', 'connection_uuid': OLD, 'machine': MACHINE,
               'device': MAC, 'profile': profile().to_dict()}
        runtime._write_state(self.state, old)
        transaction = self.transaction()
        transaction._timer = Mock()
        runtime._write_state(self.state, {'format': 1, 'interface': 'eth0', 'connection_uuid': NEW})
        self.manager.wait_active_uuid = Mock(side_effect=RuntimeError('activation still pending'))
        with self.assertRaisesRegex(RuntimeError, 'Не удалось подтвердить'):
            transaction.rollback()
        self.assertIn(NEW, self.manager.deleted)
        self.assertFalse(transaction.pending)
        transaction._timer.cancel.assert_called_once()
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8')), old)
        self.assertEqual(self.manager.destroyed, [])

    def test_rollback_without_previous_profile_disconnects_failed_candidate(self):
        self.manager.previous = None
        self.manager.rollback_success = False
        transaction = runtime.ConnectionTransaction(self.manager, profile(), 'eth0', NEW,
                                                    CHECKPOINT, None, MAC, MACHINE, self.state)
        self.manager.active = NEW
        transaction.rollback()
        self.assertIsNone(self.manager.active)
        self.assertFalse(transaction.pending)

    def test_old_profile_on_another_interface_is_not_deleted_by_confirmation(self):
        runtime._write_state(self.state, {'format': 1, 'interface': 'eth1', 'connection_uuid': OLD})
        transaction = self.transaction()
        transaction.confirm()
        self.assertNotIn(OLD, self.manager.deleted)

    def test_activation_failure_restores_network_and_has_no_pending_payload(self):
        self.manager.activation_failure = True
        with self.assertRaisesRegex(RuntimeError, 'Не подключено'):
            self.connect()
        self.assertEqual(self.manager.active, OLD)
        self.assertEqual(self.manager.rolled_back, [CHECKPOINT])
        self.assertIn(self.manager.created, self.manager.deleted)
        self.assertFalse(self.state.exists())

    def test_missing_global_address_rolls_back(self):
        self.manager.addresses = addresses()
        with self.assertRaisesRegex(RuntimeError, 'не получен'):
            self.connect()
        self.assertEqual(self.manager.active, OLD)

    def test_wrong_pc_and_adapter_are_rejected_before_any_mutation(self):
        for change in [('b' * 64, MAC), (MACHINE, '52:54:00:12:34:99')]:
            value = profile(scope=ProfileScope(*change))
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'другому компьютеру'):
                self.connect(value)
        self.assertEqual(self.manager.commands, [])
        self.assertEqual(self.manager.checkpoint_calls, [])

    def test_unmanaged_or_wrong_kind_is_rejected_before_mutation(self):
        with patch('network_runtime.available_devices', return_value=[]), \
                self.assertRaisesRegex(ValueError, 'не управляется'):
            self.connect()
        self.assertEqual(self.manager.commands, [])

    def test_interface_input_cannot_be_an_option_or_path(self):
        for name in ('--help', '../eth0', 'eth0;whoami', 'e' * 16):
            with self.subTest(name=name), self.assertRaises(ValueError):
                runtime.connect_job(profile(), name)(self.job)
        self.assertEqual(self.manager.commands, [])

    def test_password_rejects_empty_multiline_and_overlong_before_mutation(self):
        value = profile(kind='wifi', wifi=WifiSettings('Сеть', 'wpa2'))
        with patch('network_runtime.available_devices', return_value=[
                {'interface': 'eth0', 'kind': 'wifi', 'state': 'disconnected'}]):
            for password in (None, '', 'a\nb', 'a\rb', 'a\0b', 'a' * 129):
                with self.subTest(password=password), self.assertRaisesRegex(ValueError, 'одной строкой'):
                    self.connect(value, password=password)
        self.assertEqual(self.manager.commands, [])

    def test_wifi_secret_is_private_temporary_file_and_never_argv_or_state(self):
        value = profile(kind='wifi', wifi=WifiSettings('Сеть', 'wpa3'))
        factory = tempfile.NamedTemporaryFile
        def create(**kwargs):
            if os.name == 'nt':
                kwargs['delete_on_close'] = False
            return factory(**{**kwargs, 'dir': self.directory.name})
        with patch('network_runtime.available_devices', return_value=[
                {'interface': 'eth0', 'kind': 'wifi', 'state': 'disconnected'}]), \
                patch('network_runtime.tempfile.NamedTemporaryFile', side_effect=create):
            transaction = self.connect(value, password='unique-secret-123')
        self.assertEqual(self.manager.seen_secret, '802-11-wireless-security.psk:unique-secret-123\n')
        if os.name != 'nt':
            self.assertEqual(self.manager.seen_permissions, 0o600)
        commands = [argv for argv, _ in self.manager.commands]
        self.assertNotIn('unique-secret-123', repr(commands))
        secret_path = next(Path(argv[argv.index('passwd-file') + 1]) for argv in commands if 'passwd-file' in argv)
        self.assertFalse(secret_path.exists())
        transaction.confirm()
        self.assertNotIn('unique-secret-123', self.state.read_text())

    def test_auto_timeout_callback_rolls_back(self):
        transaction = self.transaction()
        transaction._automatic_rollback()
        self.assertFalse(transaction.pending)
        self.assertEqual(self.manager.active, OLD)

    def test_pending_cancel_before_handoff_rolls_back(self):
        self.job.check.side_effect = RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.connect()
        self.assertEqual(self.manager.active, OLD)

    def test_active_profile_requires_uuid_machine_and_permanent_mac(self):
        transaction = self.connect()
        transaction.confirm()
        self.assertEqual(runtime.active_profile(state_path=self.state), profile())
        self.manager.active = OLD
        self.assertIsNone(runtime.active_profile(state_path=self.state))
        self.assertFalse(self.state.exists())

    def test_stale_profile_with_mismatched_machine_is_cleared(self):
        transaction = self.connect()
        transaction.confirm()
        with patch('network_runtime.machine_id', return_value='b' * 64):
            self.assertIsNone(runtime.active_profile(state_path=self.state))
        self.assertFalse(self.state.exists())

    def test_connecting_uuid_temporarily_pauses_but_preserves_active_proxy_context(self):
        transaction = self.connect()
        transaction.confirm()
        self.manager.activation_state = 70
        self.assertIsNone(runtime.active_profile(state_path=self.state))
        self.assertTrue(self.state.exists())
        self.manager.activation_state = 100
        self.assertEqual(runtime.active_profile(state_path=self.state), profile())

    def test_malformed_state_does_not_break_network_ui(self):
        for data in ({'format': 1, 'interface': None, 'connection_uuid': NEW},
                     {'format': 1, 'interface': 'eth0', 'connection_uuid': NEW, 'profile': {}},
                     {'format': True}, 'wrong', ['wrong']):
            with self.subTest(data=data):
                self.state.write_text(json.dumps(data))
                self.assertIsNone(runtime.active_profile(state_path=self.state))
        self.state.write_bytes(b'\xff')
        self.assertIsNone(runtime.active_profile(state_path=self.state))

    def test_state_over_64k_is_not_read(self):
        self.state.write_text(' ' * 65537)
        self.assertIsNone(runtime._read_state(self.state))

    @unittest.skipIf(os.name == 'nt', 'POSIX FIFO and symlink protections')
    def test_state_rejects_symlink_and_fifo_without_blocking(self):
        destination = Path(self.directory.name) / 'target'
        destination.write_text('{"format":1}')
        self.state.symlink_to(destination)
        self.assertIsNone(runtime._read_state(self.state))
        self.state.unlink()
        os.mkfifo(self.state)
        self.assertIsNone(runtime._read_state(self.state))

    def test_startup_only_connects_exact_pc_nic_once_with_short_bound(self):
        value = profile(autoconnect=True)
        transaction = Mock()
        def connect(*args, **kwargs):
            def run(job):
                job.payload = transaction
            return run
        with patch('network_runtime.connect_job', side_effect=connect) as function:
            runtime.startup_job([value], state_path=self.state)(self.job)
        function.assert_called_once_with(value, 'eth0', None, confirmation_seconds=10,
                                         activation_seconds=10, state_path=self.state)
        transaction.confirm.assert_called_once()

    def test_startup_skips_foreign_pc_and_missing_identity(self):
        value = profile(autoconnect=True)
        for identity in ('b' * 64, None):
            with self.subTest(identity=identity), patch('network_runtime.machine_id', return_value=identity), \
                    patch('network_runtime.connect_job') as function:
                runtime.startup_job([value])(self.job)
                function.assert_not_called()

    def test_portable_wifi_is_never_autoconnected_on_foreign_pc(self):
        value = profile(kind='wifi', wifi=WifiSettings('Сеть'), autoconnect=True,
                        scope=ProfileScope(MACHINE, MAC, portable=True))
        with patch('network_runtime.available_devices', return_value=[
                {'interface': 'eth0', 'kind': 'wifi', 'state': 'disconnected'}]), \
                patch('network_runtime.machine_id', return_value='b' * 64), \
                patch('network_runtime.connect_job') as function:
            runtime.startup_job([value])(self.job)
            function.assert_not_called()

    def test_duplicate_autoconnect_profiles_do_not_apply_arbitrary_static(self):
        value = profile(autoconnect=True)
        other = replace(value, id=str(uuid.uuid4()), name='Другая сеть')
        with patch('network_runtime.connect_job') as function:
            runtime.startup_job([value, other])(self.job)
            function.assert_not_called()
        self.assertIn('Несколько профилей', self.job.result)

    def test_locked_wifi_secrets_skip_autoconnect_without_prompt(self):
        value = profile(kind='wifi', wifi=WifiSettings('Сеть', 'wpa2'), autoconnect=True,
                        secret_ref=profile().id)
        with patch('network_runtime.available_devices', return_value=[
                {'interface': 'eth0', 'kind': 'wifi', 'state': 'disconnected'}]), \
                patch('network_runtime.connect_job') as function:
            runtime.startup_job([value], secret_resolver=lambda ref: None)(self.job)
            function.assert_not_called()

    def test_startup_failure_preserves_manual_network_access(self):
        with patch('network_runtime.connect_job', side_effect=RuntimeError('not connected')):
            runtime.startup_job([profile(autoconnect=True)])(self.job)
        self.assertIn('ручное подключение', self.job.result)

    def test_startup_skips_unavailable_cable_and_can_use_wifi(self):
        value = profile(autoconnect=True)
        with patch('network_runtime.available_devices', return_value=[
                {'interface': 'eth0', 'kind': 'wired', 'state': 'unavailable'}]), \
                patch('network_runtime.connect_job') as function:
            runtime.startup_job([value])(self.job)
            function.assert_not_called()

    def test_failed_ethernet_startup_can_fall_back_to_saved_wifi(self):
        wired = profile(autoconnect=True)
        wireless = profile(kind='wifi', wifi=WifiSettings('Открытая сеть'), autoconnect=True)
        transaction = Mock()
        def second(job):
            job.payload = transaction
        with patch('network_runtime.available_devices', return_value=[
                {'interface': 'eth0', 'kind': 'wired', 'state': 'disconnected'},
                {'interface': 'wl0', 'kind': 'wifi', 'state': 'disconnected'}]), \
                patch('network_runtime.connect_job', side_effect=[RuntimeError('bad DHCP'), second]) as function:
            runtime.startup_job([wired, wireless])(self.job)
        self.assertEqual(function.call_count, 2)
        transaction.confirm.assert_called_once()

    def test_temporary_dhcp_explicitly_creates_new_ram_only_profile(self):
        with patch('network_runtime.ACTIVE_STATE', self.state):
            runtime.temporary_dhcp_job('eth0')(self.job)
        argv = self.manager.commands[0][0]
        self.assertEqual(argv[:6], ['nmcli', 'connection', 'add', 'save', 'no', 'type'])
        self.assertIn('flashos-dhcp-', repr(argv))
        self.assertIn('connection.autoconnect', argv)
        self.assertEqual(argv[argv.index('ipv4.addresses') + 1], '')
        self.assertEqual(argv[argv.index('ipv4.gateway') + 1], '')
        self.assertEqual(argv[argv.index('ipv4.routes') + 1], '')
        self.assertEqual(argv[argv.index('ipv4.dns') + 1], '')
        self.assertEqual(argv[argv.index('ipv4.ignore-auto-dns') + 1], 'no')
        self.assertNotIn(['nmcli', '--wait', '35', 'device', 'connect', 'eth0'],
                         [argv for argv, _ in self.manager.commands])
        self.assertIn('DHCP', self.job.result)
        self.assertEqual(self.manager.destroyed, [(CHECKPOINT, True)])

    def test_dhcp_failure_keeps_previous_connection(self):
        self.manager.activation_failure = True
        with self.assertRaises(RuntimeError):
            runtime.temporary_dhcp_job('eth0')(self.job)
        self.assertEqual(self.manager.active, OLD)
        self.assertEqual(self.manager.rolled_back, [CHECKPOINT])


class ArgumentAndAddressTests(unittest.TestCase):
    def test_wifi_secret_is_cached_by_nm_only_in_ram_for_checkpoint_reactivation(self):
        value = profile(kind='wifi', wifi=WifiSettings('Сеть', 'wpa3'))
        args = runtime.candidate_arguments(value, 'wlan0', NEW, MAC)
        self.assertEqual(args[args.index('save') + 1], 'no')
        self.assertEqual(args[args.index('wifi-sec.psk-flags') + 1], '0')
        self.assertEqual(args[args.index('connection.autoconnect') + 1], 'no')
        self.assertNotIn('wifi-sec.psk', args)

    def test_static_arguments_include_all_dns_routes_metrics_and_gateway(self):
        value = profile(ipv4=IPSettings(method='manual', addresses=('192.168.7.20/24',),
                        gateway='192.168.7.1', dns_method='manual', dns=('9.9.9.9',),
                        dns_search=('office.example',), routes=(Route('10.0.0.0/8', '192.168.7.2', 70),),
                        route_metric=40, never_default=True))
        args = runtime.candidate_arguments(value, 'renamed0', NEW, MAC)
        for key, expected in [('ipv4.addresses', '192.168.7.20/24'), ('ipv4.gateway', '192.168.7.1'),
                              ('ipv4.dns', '9.9.9.9'), ('ipv4.ignore-auto-dns', 'yes'),
                              ('ipv4.routes', '10.0.0.0/8 192.168.7.2 70'),
                              ('ipv4.route-metric', '40'), ('ipv4.never-default', 'yes'),
                              ('ipv4.dad-timeout', '3000'), ('802-3-ethernet.mac-address', MAC)]:
            self.assertEqual(args[args.index(key) + 1], expected)
        self.assertIn('save', args)
        self.assertEqual(args[args.index('save') + 1], 'no')

    def test_ipv6_routes_without_gateway_use_unspecified_next_hop(self):
        settings = IPSettings(routes=(Route('2001:db8::/32', metric=90),))
        args = runtime._ip_arguments(settings, 6)
        self.assertEqual(args[args.index('ipv6.routes') + 1], '2001:db8::/32 :: 90')
        self.assertNotIn('ipv6.dad-timeout', args)

    def test_ipv6_only_address_is_valid_but_not_sufficient_for_quick_ipv4_dhcp(self):
        manager = FakeManager(output=addresses('2001:db8::1'))
        self.assertEqual(runtime._verify_addresses(manager, profile(), 'eth0'), ['2001:db8::1'])

    def test_wrong_static_address_is_rejected(self):
        value = profile(ipv4=IPSettings('manual', ('10.0.2.50/24',)))
        with self.assertRaisesRegex(RuntimeError, 'не применены'):
            runtime._verify_addresses(FakeManager(), value, 'eth0')

    def test_tentative_ipv6_address_is_waited_for_before_acceptance(self):
        manager = Mock()
        manager.command.side_effect = [(0, addresses('2001:db8::1', tentative=True)),
                                       (0, addresses('2001:db8::1'))]
        with patch('network_runtime.time.sleep'):
            self.assertEqual(runtime._verify_addresses(manager, profile(), 'eth0'), ['2001:db8::1'])
        self.assertEqual(manager.command.call_count, 2)

    def test_tentative_timeout_and_duplicate_addresses_are_rejected(self):
        for properties, expected in [({'tentative': True}, 'уникальности'),
                                      ({'dadfailed': True}, 'конфликт'),
                                      ({'flags': ['dadfailed']}, 'конфликт')]:
            with self.subTest(properties=properties), self.assertRaisesRegex(RuntimeError, expected):
                runtime._verify_addresses(FakeManager(output=addresses('2001:db8::1', **properties)),
                                          profile(), 'eth0', dad_seconds=0)

    def test_link_local_only_does_not_report_full_connection(self):
        with self.assertRaisesRegex(RuntimeError, 'не получен'):
            runtime._verify_addresses(FakeManager(output=addresses('fe80::1')), profile(), 'eth0')

    def test_explicit_manual_link_local_ipv4_and_ipv6_are_valid(self):
        for version, cidr in ((4, '169.254.7.20/16'), (6, 'fe80::20/64')):
            value = profile(**{'ipv' + str(version): IPSettings('manual', (cidr,))})
            address = cidr.split('/')[0]
            with self.subTest(version=version):
                self.assertEqual(runtime._verify_addresses(
                    FakeManager(output=addresses(address, scope='link')), value, 'eth0'), [address])

    def test_automatic_or_unrequested_link_local_is_not_accepted(self):
        for address in ('169.254.7.20', 'fe80::20'):
            with self.subTest(address=address), self.assertRaisesRegex(RuntimeError, 'не получен'):
                runtime._verify_addresses(FakeManager(output=addresses(address, scope='link')),
                                          profile(), 'eth0')

    def test_invalid_ip_json_does_not_escape_as_raw_output(self):
        for output in ('not-json', '{}', '[null]', '[{"ifname":"eth0","addr_info":[{}]}]'):
            with self.subTest(output=output), self.assertRaisesRegex(RuntimeError, 'проверить'):
                runtime._verify_addresses(FakeManager(output=output), profile(), 'eth0')


class ManagerProtocolTests(unittest.TestCase):
    def test_rollback_waits_for_activated_state_after_uuid_appears(self):
        job = Mock()
        job.command.side_effect = [(0, OLD + '\n70 (connecting (getting IP configuration))\n'),
                                   (0, OLD + '\n100 (connected)\n')]
        with patch('network_runtime.time.sleep') as sleep:
            self.assertEqual(runtime.NetworkManager(job).wait_active_uuid('eth0', OLD), OLD)
        self.assertEqual(job.command.call_count, 2)
        sleep.assert_called_once_with(.25)
        self.assertIn('GENERAL.CON-UUID,GENERAL.STATE', job.command.call_args.args[0])

    def test_disconnected_rollback_does_not_accept_empty_uuid_while_connecting(self):
        job = Mock()
        job.command.side_effect = [(0, '--\n40 (connecting (prepare))\n'),
                                   (0, '--\n30 (disconnected)\n')]
        with patch('network_runtime.time.sleep') as sleep:
            self.assertIsNone(runtime.NetworkManager(job).wait_active_uuid('eth0', None))
        self.assertEqual(job.command.call_count, 2)
        sleep.assert_called_once_with(.25)

    def test_unfinished_matching_uuid_at_timeout_is_not_reported_restored(self):
        for value in (OLD, None):
            with self.subTest(value=value):
                job = Mock()
                job.command.return_value = (0, (value or '--') + '\n70 (connecting)\n')
                with self.assertRaisesRegex(RuntimeError, 'не восстановлено'):
                    runtime.NetworkManager(job).wait_active_uuid('eth0', value, seconds=0)

    def test_checkpoint_targets_one_device_without_global_profile_deletion(self):
        job = Mock()
        job.command.return_value = (0, 'o "' + CHECKPOINT + '"')
        self.assertEqual(runtime.NetworkManager(job).checkpoint(DEVICE, 60), CHECKPOINT)
        args, kwargs = job.command.call_args
        self.assertEqual(args[0][-6:], ['CheckpointCreate', 'aouu', '1', DEVICE, '60', '0'])
        self.assertFalse(kwargs['log_output'])
        self.assertEqual(kwargs['resource'], 'network')

    def test_rollback_reply_must_confirm_each_selected_device(self):
        job = Mock()
        manager = runtime.NetworkManager(job)
        for text, expected in [('a{su} 1 "' + DEVICE + '" 0', True),
                               ('a{su} 1 "' + DEVICE + '" 1', False),
                               ('a{su} 0', False), ('unexpected', False)]:
            with self.subTest(text=text):
                job.command.return_value = (0, text)
                self.assertEqual(manager.rollback(CHECKPOINT), expected)
        self.assertTrue(job.command.call_args.kwargs['cleanup'])

    def test_managed_ethernet_wifi_only_and_invalid_interfaces_filtered(self):
        job = Mock()
        job.command.return_value = (0, 'eth0:ethernet:connected\nwl0:wifi:disconnected\n'
            'lo:loopback:connected\neth1:ethernet:unmanaged\n--help:ethernet:connected\n')
        self.assertEqual(runtime.available_devices(job), [
            {'interface': 'eth0', 'kind': 'wired', 'state': 'connected'},
            {'interface': 'wl0', 'kind': 'wifi', 'state': 'disconnected'}])

    def test_command_failure_hides_arbitrary_secret_output(self):
        job = Mock()
        job.command.return_value = (1, 'secret password must not leak')
        with self.assertRaisesRegex(RuntimeError, 'NetworkManager') as caught:
            runtime.NetworkManager(job).command(['nmcli', 'test'])
        self.assertNotIn('secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
