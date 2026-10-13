"""Profile validation, PC/NIC isolation and crash-safe USB persistence."""
from dataclasses import FrozenInstanceError, replace
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid


spec = importlib.util.spec_from_file_location(
    'network_profiles_model', Path(__file__).resolve().parents[1] / 'src/network_profiles.py')
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)

PC = 'a' * 64
OTHER_PC = 'b' * 64
MAC = '02:34:56:78:9a:bc'
OTHER_MAC = '02:34:56:78:9a:bd'


def profile(**changes):
    properties = {'id': str(uuid.uuid4()), 'name': 'Дом', 'kind': 'wired',
                  'scope': profiles.ProfileScope(PC, MAC)}
    properties.update(changes)
    return profiles.NetworkProfile(**properties)


class NetworkProfileTests(unittest.TestCase):
    def test_nested_validated_model_roundtrips_unicode_and_dual_stack(self):
        value = profile(
            kind='wifi', wifi=profiles.WifiSettings('Дом', 'wpa3', True),
            secret_ref=str(uuid.uuid4()),
            ipv4=profiles.IPSettings('manual', ('192.0.2.5/24',), '192.0.2.1', 'manual',
                                    ('1.1.1.1',), ('пример.рф',),
                                    (profiles.Route('198.51.100.7/24', '192.0.2.2', 100),), 200, True),
            ipv6=profiles.IPSettings('manual', ('2001:db8::5/64',), '2001:db8::1', 'manual',
                                    ('2606:4700:4700::1111',), (), (), 100),
            proxy=profiles.ProxySettings('manual', 'http://127.0.0.1:3128/', 'socks5h://proxy.example:1080',
                                         ('localhost', '.example.org', '192.0.2.0/24'), 'user'))
        self.assertEqual(profiles.profile_from_dict(value.to_dict()), value)
        self.assertEqual(value.ipv4.routes[0].destination, '198.51.100.0/24')
        self.assertEqual(value.ipv4.dns_search, ('xn--e1afmkfd.xn--p1ai',))
        self.assertEqual(value.proxy.http_url, 'http://127.0.0.1:3128')
        self.assertIsInstance(value.to_dict()['ipv4']['routes'], list)
        self.assertTrue(value.static)
        self.assertTrue(value.ipv4.ignore_auto_dns)
        with self.assertRaises(FrozenInstanceError):
            value.autoconnect = True

    def test_manual_ip_requires_cidr_and_matching_family(self):
        for changes in (
            {'ipv4': {'method': 'manual'}},
            {'ipv4': {'method': 'manual', 'addresses': ['192.0.2.2']}},
            {'ipv4': {'method': 'manual', 'addresses': ['2001:db8::2/64']}},
            {'ipv6': {'method': 'manual', 'addresses': ['192.0.2.2/24']}},
            {'ipv4': {'method': 'dhcp'}},
            {'ipv4': {'method': 'auto', 'addresses': ['192.0.2.2/24']}},
            {'ipv4': {'gateway': '192.0.2.1'}},
            {'ipv4': {'dns_method': 'manual', 'dns': ['::1']}},
        ):
            data = profile().to_dict() | changes
            with self.subTest(changes=changes), self.assertRaises(profiles.ProfileError):
                profiles.profile_from_dict(data)
        profile(ipv6=profiles.IPSettings(method='dhcp'))

    def test_address_and_gateway_validation_rejects_scope_ids_and_nonhost_addresses(self):
        for address in ('0.0.0.0/24', '127.0.0.1/8', '224.0.0.1/24', '255.255.255.255/32',
                        '::/64', 'fe80::1%eth0/64', '192.0.2.1/33', '192.0.2.0/24', '192.0.2.255/24'):
            with self.subTest(address=address), self.assertRaises(profiles.ProfileError):
                profiles.IPSettings('manual', (address,))
        with self.assertRaises(profiles.ProfileError):
            profile(ipv4=profiles.IPSettings('manual', ('192.0.2.1/24',), '2001:db8::1'))

    def test_manual_gateway_must_be_reachable_and_cannot_be_own_or_broadcast_address(self):
        for gateway in ('192.0.2.5', '192.0.2.0', '192.0.2.255', '198.51.100.1'):
            with self.subTest(gateway=gateway), self.assertRaises(profiles.ProfileError):
                profile(ipv4=profiles.IPSettings('manual', ('192.0.2.5/24',), gateway))
        with self.assertRaises(profiles.ProfileError):
            profile(ipv4=profiles.IPSettings('manual', ('192.0.2.5/24',), routes=(
                profiles.Route('203.0.113.0/24', '198.51.100.1'),)))
        value = profile(ipv4=profiles.IPSettings('manual', ('192.0.2.5/32',), '198.51.100.1', routes=(
            profiles.Route('198.51.100.1/32'),)))
        self.assertEqual(value.ipv4.gateway, '198.51.100.1')
        profile(ipv6=profiles.IPSettings('manual', ('2001:db8::5/64',), 'fe80::1'))
        profile(ipv4=profiles.IPSettings('manual', ('192.0.2.0/31',), '192.0.2.1'))
        with self.assertRaises(profiles.ProfileError):
            profile(ipv4=profiles.IPSettings('manual', ('192.0.2.5/24', '192.0.2.5/32')))

    def test_disabled_ip_cannot_retain_hidden_dns_routes_or_gateway(self):
        for field in ({'dns_method': 'none'}, {'route_metric': 100}, {'never_default': True},
                      {'routes': (profiles.Route('192.0.2.0/24'),)}, {'dns_search': ('example.org',)}):
            with self.subTest(field=field), self.assertRaises(profiles.ProfileError):
                profiles.IPSettings(method='disabled', **field)
        profile(ipv4=profiles.IPSettings(method='disabled'))

    def test_dns_auto_manual_and_disabled_are_distinct_and_domains_validated(self):
        automatic = profiles.IPSettings()
        self.assertFalse(automatic.ignore_auto_dns)
        self.assertTrue(profiles.IPSettings(dns_method='none').ignore_auto_dns)
        self.assertEqual(profiles.IPSettings(dns_method='manual', dns=('127.0.0.53',)).dns, ('127.0.0.53',))
        for fields in ({'dns_method': 'manual'}, {'dns': ('1.1.1.1',)},
                       {'dns_method': 'none', 'dns': ('1.1.1.1',)},
                       {'dns_method': 'none', 'dns_search': ('example.org',)},
                       {'dns_search': ('bad..domain',)}, {'dns_search': ('-bad.example',)},
                       {'dns_search': ('domain\nother',)}):
            with self.subTest(fields=fields), self.assertRaises(profiles.ProfileError):
                profiles.IPSettings(**fields)

    def test_route_gateway_family_and_metric_bounds_are_checked(self):
        profiles.Route('0.0.0.0/0', '192.0.2.1', 0)
        profiles.Route('::/0', '2001:db8::1', 2**32 - 1)
        for route in (('192.0.2.0/24', '2001:db8::1', None), ('::/0', '192.0.2.1', None),
                      ('192.0.2.0', '', None), ('192.0.2.0/24', '', -1),
                      ('192.0.2.0/24', '', True), ('192.0.2.0/24', '', 2**32)):
            with self.subTest(route=route), self.assertRaises(profiles.ProfileError):
                profiles.Route(*route)
        with self.assertRaises(profiles.ProfileError):
            profile(ipv4=profiles.IPSettings(routes=(profiles.Route('2001:db8::/64'),)))

    def test_static_profiles_stay_manual_until_explicit_same_pc_opt_in(self):
        static = profile(ipv4=profiles.IPSettings('manual', ('192.0.2.2/24',)))
        self.assertFalse(static.autoconnect)
        self.assertTrue(profiles.matches(static, PC, MAC))
        self.assertFalse(profiles.matches(static, PC, MAC, autoconnect=True))
        opted_in = replace(static, autoconnect=True)
        self.assertTrue(profiles.matches(opted_in, PC, MAC, autoconnect=True))
        for machine, device in ((OTHER_PC, MAC), (PC, OTHER_MAC), (None, MAC), (PC, None)):
            self.assertFalse(profiles.matches(opted_in, machine, device, autoconnect=True))
            self.assertFalse(profiles.matches(opted_in, machine, device))

    def test_portable_wifi_is_explicit_dhcp_only_and_never_automatic_on_a_new_pc(self):
        portable = profile(kind='wifi', wifi=profiles.WifiSettings('Office', 'wpa2'),
                           scope=profiles.ProfileScope(PC, MAC, True), autoconnect=True)
        self.assertTrue(profiles.matches(portable, OTHER_PC, OTHER_MAC))
        self.assertFalse(profiles.matches(portable, OTHER_PC, OTHER_MAC, autoconnect=True))
        self.assertTrue(profiles.matches(portable, PC, MAC, autoconnect=True))
        for change in ({'kind': 'wired', 'wifi': None},
                       {'ipv4': profiles.IPSettings('manual', ('192.0.2.2/24',))},
                       {'ipv4': profiles.IPSettings(routes=(profiles.Route('192.0.2.0/24'),))},
                       {'ipv4': profiles.IPSettings(method='disabled'),
                        'ipv6': profiles.IPSettings(method='disabled')}):
            with self.subTest(change=change), self.assertRaises(profiles.ProfileError):
                replace(portable, **change)

    def test_manual_rebinding_keeps_original_profile_and_secret_but_resets_auto(self):
        original = profile(autoconnect=True, secret_ref=str(uuid.uuid4()))
        rebound = profiles.rebind(original, OTHER_PC, OTHER_MAC)
        self.assertNotEqual(rebound.id, original.id)
        self.assertEqual(original.scope.machine, PC)
        self.assertEqual(rebound.secret_ref, original.secret_ref)
        self.assertFalse(rebound.autoconnect)
        self.assertTrue(profiles.matches(rebound, OTHER_PC, OTHER_MAC))

    def test_wifi_ssid_security_and_octet_limits_are_validated(self):
        profiles.WifiSettings('я' * 16, 'wpa3', True)
        for values in (('', 'open', False), ('я' * 17, 'wpa2', False), ('x' * 33, 'open', False),
                       ('Office', 'wep', False), ('Office', 'eap', False), ('bad\nname', 'open', False),
                       ('Office', 'open', 1)):
            with self.subTest(values=values), self.assertRaises(profiles.ProfileError):
                profiles.WifiSettings(*values)
        with self.assertRaises(profiles.ProfileError):
            profile(kind='wifi')
        with self.assertRaises(profiles.ProfileError):
            profile(wifi=profiles.WifiSettings('Office'))

    def test_proxy_urls_credentials_and_bypass_are_validated(self):
        proxy = profiles.ProxySettings('manual', 'http://proxy.example:8080', 'https://[2001:db8::1]:443',
                                       ('*.example.org', '192.0.2.5', '2001:db8::/64'), 'username')
        self.assertEqual(proxy.bypass[0], '*.example.org')
        for url in ('http://user:password@proxy.example:8080', 'http://user@proxy.example:8080',
                    'ftp://proxy.example', 'http://proxy.example/path', 'http://proxy.example?a=1',
                    'http://proxy.example#fragment', 'http://proxy.example:0', 'http://proxy.example:65536',
                    'http://proxy.example\nheader', 'http://[fe80::1%eth0]:8080'):
            with self.subTest(url=url), self.assertRaises(profiles.ProfileError):
                profiles.ProxySettings('manual', http_url=url)
        for fields in ({'mode': 'manual'}, {'http_url': 'http://proxy.example'},
                       {'mode': 'manual', 'http_url': 'http://proxy.example', 'bypass': ('x;command',)},
                       {'mode': 'manual', 'http_url': 'http://proxy.example', 'username': 'user\nname'}):
            with self.subTest(fields=fields), self.assertRaises(profiles.ProfileError):
                profiles.ProxySettings(**fields)

    def test_password_fields_and_unknown_protocol_fields_are_never_saved(self):
        for change in ({'password': 'secret'}, {'wifi_password': 'secret'},
                       {'scope': {'machine': PC, 'device': MAC, 'interface': 'eth0'}},
                       {'proxy': {'mode': 'manual', 'http_url': 'http://proxy.example', 'password': 'secret'}},
                       {'kind': 'wifi', 'wifi': {'ssid': 'Office', 'password': 'secret'}},
                       {'ipv4': {'routes': [{'gateway': '192.0.2.1'}]}}):
            with self.subTest(change=change), self.assertRaises(profiles.ProfileError):
                profiles.profile_from_dict(profile().to_dict() | change)
        for changes in ({'id': 'a' * 32}, {'secret_ref': '/tmp/password'}, {'autoconnect': 1},
                        {'scope': profiles.ProfileScope(PC, MAC)} | {'name': 'bad\nname'}):
            with self.subTest(changes=changes), self.assertRaises(profiles.ProfileError):
                profile(**changes)

    def test_mac_scope_uses_permanent_identity_and_rejects_interface_names(self):
        self.assertEqual(profiles.ProfileScope(PC, '02-34-56-78-9A-BC').device, MAC)
        for device in ('eth0', '00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff', '01:23:45:67:89:ab', 'bad'):
            with self.subTest(device=device), self.assertRaises(profiles.ProfileError):
                profiles.ProfileScope(PC, device)
        for machine in ('host', 'a' * 63, 'A' * 64, None):
            with self.subTest(machine=machine), self.assertRaises(profiles.ProfileError):
                profiles.ProfileScope(machine, MAC)


class MachineIdentityTests(unittest.TestCase):
    def test_dmi_uuid_hash_stays_stable_and_never_exposes_raw_values(self):
        with tempfile.TemporaryDirectory() as directory:
            dmi = Path(directory) / 'class/dmi/id'
            dmi.mkdir(parents=True)
            identifier = str(uuid.uuid4())
            (dmi / 'product_uuid').write_text(identifier.upper())
            first = profiles.machine_id(directory)
            self.assertEqual(len(first), 64)
            self.assertNotIn(identifier, first)
            (dmi / 'product_uuid').write_text(identifier.lower())
            self.assertEqual(first, profiles.machine_id(directory))
            (dmi / 'product_uuid').write_text(str(uuid.uuid4()))
            self.assertNotEqual(first, profiles.machine_id(directory))

    def test_invalid_dmi_fallback_never_makes_placeholder_machines_match(self):
        with tempfile.TemporaryDirectory() as directory:
            dmi = Path(directory) / 'class/dmi/id'
            dmi.mkdir(parents=True)
            (dmi / 'product_uuid').write_text(str(uuid.UUID(int=0)))
            for serial in ('To Be Filled By O.E.M.', 'Default string', 'unknown', '0000000', ''):
                (dmi / 'board_serial').write_text(serial)
                self.assertIsNone(profiles.machine_id(directory))
            (dmi / 'board_serial').write_text('board-unique-test-123')
            self.assertEqual(len(profiles.machine_id(directory)), 64)

    def test_device_identity_prefers_ethtool_permanent_mac_over_randomized_address(self):
        job = SimpleNamespace(command=mock.Mock(return_value=(0, 'Permanent address: ' + MAC)))
        self.assertEqual(profiles.device_id('wlan0', job=job), MAC)
        self.assertFalse(job.command.call_args.kwargs['log_output'])
        self.assertIsNone(profiles.device_id('wlan0;command', job=job))
        self.assertEqual(job.command.call_count, 1)

    def test_device_fallback_never_uses_random_current_mac(self):
        with tempfile.TemporaryDirectory() as directory:
            nic = Path(directory) / 'class/net/wlan0'
            nic.mkdir(parents=True)
            (nic / 'address').write_text(OTHER_MAC)
            (nic / 'addr_assign_type').write_text('3')
            job = SimpleNamespace(command=mock.Mock(return_value=(1, 'not supported')))
            self.assertIsNone(profiles.device_id('wlan0', job=job, sys_root=directory))
            (nic / 'addr_assign_type').write_text('0')
            self.assertEqual(profiles.device_id('wlan0', job=job, sys_root=directory), OTHER_MAC)
            (nic / 'perm_address').write_text(MAC)
            self.assertEqual(profiles.device_id('wlan0', job=job, sys_root=directory), MAC)


class ProfileStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = profiles.ProfileStore(self.root)
        # Windows cannot fsync a directory through POSIX open; exercise that error separately.
        if os.name == 'nt':
            patch = mock.patch.object(profiles, 'fsync_directory')
            patch.start()
            self.addCleanup(patch.stop)

    def test_atomic_roundtrip_upsert_delete_and_secret_reference_only(self):
        first = profile(secret_ref=str(uuid.uuid4()))
        second = profile(name='Работа')
        self.assertEqual(self.store.load(), ())
        self.store.upsert(first)
        self.store.upsert(second)
        self.store.upsert(replace(first, name='Дом обновлён'))
        saved = self.store.load()
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0].name, 'Дом обновлён')
        self.assertEqual(saved[0].secret_ref, first.secret_ref)
        self.assertNotIn('password', self.store.path.read_text(encoding='utf-8'))
        self.assertTrue(self.store.delete(first.id))
        self.assertFalse(self.store.delete(first.id))
        self.assertEqual(self.store.load(), (second,))
        self.assertEqual(list(self.store.path.parent.glob('*.tmp')), [])

    def test_corrupt_unknown_and_oversized_database_are_not_silently_overwritten(self):
        self.store.path.parent.mkdir(parents=True)
        for raw in ('not-json', json.dumps({'format': 2, 'profiles': []}),
                    '{"format": 1, "profiles": [], "profiles": []}',
                    json.dumps({'format': True, 'profiles': []}),
                    json.dumps({'format': 1, 'profiles': [], 'password': 'plaintext'}),
                    ' ' * (profiles.MAX_BYTES + 1)):
            self.store.path.write_text(raw, encoding='utf-8')
            with self.subTest(raw_length=len(raw)), self.assertRaises(profiles.ProfileError):
                self.store.upsert(profile())
            self.assertEqual(self.store.path.read_text(encoding='utf-8'), raw)

    def test_duplicate_ids_profile_count_and_serialized_size_are_bounded(self):
        value = profile()
        for saved in ((value, value), tuple(profile() for _ in range(profiles.MAX_PROFILES + 1))):
            with self.assertRaises(profiles.ProfileError):
                self.store.save(saved)
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text(json.dumps({'format': 1, 'profiles': [value.to_dict(), value.to_dict()]}))
        with self.assertRaises(profiles.ProfileError):
            self.store.load()
        with mock.patch.object(profiles, 'MAX_BYTES', 10), self.assertRaises(profiles.ProfileError):
            self.store.save((value,))

    def test_replace_failure_preserves_previous_database_and_removes_temp(self):
        first = profile()
        self.store.save((first,))
        before = self.store.path.read_bytes()
        with mock.patch.object(profiles.os, 'replace', side_effect=OSError('simulated failure')):
            with self.assertRaises(OSError):
                self.store.save((profile(),))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.store.path.parent.glob('*.tmp')), [])

    def test_file_flush_failure_preserves_previous_database(self):
        first = profile()
        self.store.save((first,))
        with mock.patch.object(profiles.os, 'fsync', side_effect=OSError('flush failed')):
            with self.assertRaises(OSError):
                self.store.save((profile(),))
        self.assertEqual(self.store.load(), (first,))

    def test_directory_flush_failure_reports_data_written_but_not_certified(self):
        first = profile()
        with mock.patch.object(profiles, 'fsync_directory', side_effect=OSError('directory flush failed')):
            with self.assertRaises(profiles.DurabilityError):
                self.store.save((first,))
        self.assertEqual(self.store.load(), (first,))

    def test_load_and_save_reject_symlinks_in_root_parent_and_leaf(self):
        external = self.root / 'external'
        external.mkdir()
        for relative in ('flashos', 'flashos/network', 'flashos/network/profiles.json'):
            link_root = self.root / uuid.uuid4().hex
            link_root.mkdir()
            target = link_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.symlink_to(external, target_is_directory=True)
            except OSError:
                self.skipTest('Host does not support creating symlinks')
            store = profiles.ProfileStore(link_root)
            for operation in (store.load, lambda: store.save((profile(),))):
                with self.subTest(relative=relative), self.assertRaises(profiles.ProfileError):
                    operation()
        outer_link = self.root / 'root-link'
        outer_link.symlink_to(external, target_is_directory=True)
        nested = external / 'nested'
        nested.mkdir()
        with self.assertRaises(profiles.ProfileError):
            profiles.ProfileStore(outer_link / 'nested').load()
        self.assertEqual(list(external.iterdir()), [nested])

    def test_nonregular_target_and_traversal_paths_are_rejected(self):
        self.store.path.mkdir(parents=True)
        with self.assertRaises(profiles.ProfileError):
            self.store.load()
        with self.assertRaises(profiles.ProfileError):
            self.store.save((profile(),))
        for relative in ('../outside', str(self.root / 'outside')):
            with self.subTest(relative=relative), self.assertRaises(profiles.ProfileError):
                profiles.secure_path(self.root, relative)


if __name__ == '__main__':
    unittest.main()
