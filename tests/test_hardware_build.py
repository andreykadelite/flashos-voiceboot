"""Regression checks for offline artifact checks and PCI/firmware selection."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    'hardware_build', Path(__file__).resolve().parents[1] / 'tools/verify-hardware-build.py')
hardware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hardware)


class OfflineHardwareTests(unittest.TestCase):
    def test_alias_uses_full_subsystem_and_class_without_prefix_false_matches(self):
        aliases = ('alias pci:v00008086d00007E40sv00008086sd00000094bc02sc80i* iwlwifi\n'
                   'alias pci:v00008086d00007E40sv00008086sd00000095bc02sc80i* wrong_subsystem\n'
                   'alias pci:v00008086d00007E40sv*sd*bc02sc00i* ethernet_only\n'
                   'alias pci:v00008086d00007E4?sv*sd*bc*sc*i* other-driver\n')
        modalias = 'pci:v00008086d00007E40sv00008086sd00000094bc02sc80i00'
        self.assertEqual(hardware.resolve_aliases(aliases, modalias), ['iwlwifi', 'other_driver'])
        self.assertNotIn('iwlwifi', hardware.resolve_aliases(aliases, modalias.replace('00000094', '00000093')))

    def test_shipped_kernel_filter_handles_module_compression_and_builtins(self):
        paths = hardware.listing_paths(
            'squashfs-root/lib/modules/6.12/kernel/net/test-net.ko.xz\n'
            'squashfs-root/lib/modules/7.2/kernel/net/current.ko.zst\n'
            'squashfs-root/lib/modules/6.12/kernel/sound/test_audio.ko\n')
        self.assertEqual(hardware.module_inventory(paths, '6.12', 'kernel/drivers/builtin-test.ko\n'),
                         {'builtin_test': 'kernel/drivers/builtin-test.ko',
                          'test_net': 'lib/modules/6.12/kernel/net/test-net.ko.xz',
                          'test_audio': 'lib/modules/6.12/kernel/sound/test_audio.ko'})
        self.assertEqual(hardware.kernel_versions(paths), {'6.12', '7.2'})

    def test_packages_require_configured_installed_state(self):
        status = ('Package: present\nStatus: install ok installed\nVersion: 2\n\n'
                  'Package: broken\nStatus: install ok unpacked\nVersion: 3\n\n'
                  'Package: removed\nStatus: deinstall ok config-files\nVersion: 1\n')
        self.assertEqual(hardware.installed_packages(status), {'present': '2'})

    def check_fixture(self, initrd=None, status=None, aliases=None):
        return hardware.check_inventory(
            {'usr/lib/modules/fixture/kernel/net/card.ko.zst'}, 'fixture',
            {'usr/lib/modules/fixture/kernel/net/card.ko', *hardware.MICROCODE} if initrd is None else initrd,
            aliases or 'alias pci:v00001234d00005678sv*sd*bc*sc*i* card',
            status or 'Package: fixture-fw\nStatus: install ok installed\nVersion: 1\n',
            required_modules={'network': ['card']}, required_packages=['fixture-fw'],
            devices=[{'name': 'Fixture card', 'driver': 'card', 'pci_id': '1234:5678',
                      'modalias': 'pci:v00001234d00005678sv00001111sd00002222bc02sc00i00'}])

    def test_microcode_omission_and_mismatched_initrd_are_not_certified(self):
        paths = {'lib/modules/fixture/kernel/net/card.ko', hardware.MICROCODE[0]}
        with self.assertRaisesRegex(RuntimeError, 'early microcode'):
            self.check_fixture(initrd=paths)
        paths = {'lib/modules/other/kernel/net/card.ko', *hardware.MICROCODE}
        with self.assertRaisesRegex(RuntimeError, 'do not match'):
            self.check_fixture(initrd=paths)

    def test_unconfigured_firmware_and_wrong_device_alias_fail(self):
        with self.assertRaisesRegex(RuntimeError, 'fixture-fw'):
            self.check_fixture(status='Package: fixture-fw\nStatus: install ok unpacked\n')
        with self.assertRaisesRegex(RuntimeError, '1234:5678'):
            self.check_fixture(aliases='alias pci:v00001234d00009999sv*sd*bc*sc*i* card')
        result = self.check_fixture()
        self.assertEqual(result['notebook_devices'][0]['matched_modules'], ['card'])

    def test_api89_firmware_can_satisfy_declarative_max100_and_pnvm_is_required(self):
        family = 'iwlwifi-ma-b0-gf-a0'
        paths = {'lib/firmware/' + family + '-89.ucode.xz',
                 'lib/firmware/' + family + '.pnvm.zst'}
        config = 'CONFIG_FW_LOADER_COMPRESS_XZ=y\nCONFIG_FW_LOADER_COMPRESS_ZSTD=y\n'
        result = hardware.notebook_firmware([family + '-100.ucode', family + '.pnvm'], paths, config)
        self.assertEqual(result['runtime_api'], 89)
        self.assertTrue(result['files'][family + '-89.ucode'].endswith('.xz'))
        for broken_paths, broken_config in ((paths - {'lib/firmware/' + family + '.pnvm.zst'}, config),
                                           (paths, 'CONFIG_FW_LOADER_COMPRESS_XZ=y\n'),
                                           ({'lib/firmware/' + family + '-100.ucode'}, config)):
            with self.subTest(paths=broken_paths, config=broken_config):
                with self.assertRaisesRegex(RuntimeError, 'API 89'):
                    hardware.notebook_firmware([family + '-100.ucode'], broken_paths, broken_config)

    def test_image_symlink_is_resolved_and_loop_rejected(self):
        def fake_command(args, binary=False):
            if args[1] == '-ll':
                return 'lrwx squashfs-root/lib/firmware/fw -> intel/fw\n'
            if args[-1] == 'lib/firmware/intel/fw':
                return b'firmware'
            raise RuntimeError('not regular')
        with mock.patch.object(hardware, 'command', side_effect=fake_command):
            self.assertEqual(hardware.image_bytes(Path('image'), 'lib/firmware/fw'), b'firmware')
        with mock.patch.object(hardware, 'command', side_effect=lambda args, **kwargs:
                               'lrwx squashfs-root/fw -> fw\n' if args[1] == '-ll'
                               else (_ for _ in ()).throw(RuntimeError('not regular'))):
            with self.assertRaisesRegex(RuntimeError, 'symlink chain'):
                hardware.image_bytes(Path('image'), 'fw')


    def test_firmware_symlink_cannot_escape_image(self):
        def fake_command(args, binary=False):
            if args[1] == '-ll':
                return 'lrwx squashfs-root/fw -> ../outside\\n'
            raise RuntimeError('not regular')
        with mock.patch.object(hardware, 'command', side_effect=fake_command):
            with self.assertRaisesRegex(RuntimeError, 'Escaping image symlink'):
                hardware.image_bytes(Path('image'), 'fw')

    def test_exported_kernel_content_mismatch_prevents_success(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            image, initrd, kernel = (base / name for name in ('image', 'initrd', 'kernel'))
            for path in (image, initrd, kernel):
                path.write_bytes(b'exported')
            paths = ('squashfs-root/lib/modules/fixture/modules.alias\n'
                     'squashfs-root/lib/modules/fixture/kernel/net/iwlwifi.ko.xz\n')
            with mock.patch.object(hardware, 'command', return_value=paths), \
                 mock.patch.object(hardware, 'check_inventory', return_value={}), \
                 mock.patch.object(hardware, 'image_bytes', return_value=b'different'):
                with self.assertRaisesRegex(RuntimeError, 'Exported vmlinuz differs'):
                    hardware.verify_build(image, initrd, kernel, 'fixture')

    def test_exported_initrd_content_mismatch_prevents_success(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            image, initrd, kernel = (base / name for name in ('image', 'initrd', 'kernel'))
            for path in (image, initrd, kernel):
                path.write_bytes(b'exported')
            paths = ('squashfs-root/lib/modules/fixture/modules.alias\n'
                     'squashfs-root/lib/modules/fixture/kernel/net/iwlwifi.ko.xz\n')
            def image_bytes(_image, path):
                return b'exported' if path == 'boot/vmlinuz-fixture' else b'different'
            with mock.patch.object(hardware, 'command', return_value=paths), \
                 mock.patch.object(hardware, 'check_inventory', return_value={}), \
                 mock.patch.object(hardware, 'image_bytes', side_effect=image_bytes):
                with self.assertRaisesRegex(RuntimeError, 'Exported initrd differs'):
                    hardware.verify_build(image, initrd, kernel, 'fixture')

    def test_load_plan_rejects_missing_dependencies_and_driver_blacklists(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dependency, driver = (root / name for name in ('cfg80211.ko.xz', 'iwlwifi.ko.zst'))
            dependency.write_bytes(b'fixture')
            driver.write_bytes(b'fixture')
            plan = f'insmod {dependency}\ninsmod {driver}\n'
            self.assertEqual(hardware.check_load_plan(plan, root, 'iwlwifi'), ['cfg80211', 'iwlwifi'])
            dependency.unlink()
            with self.assertRaisesRegex(RuntimeError, 'dependency absent'):
                hardware.check_load_plan(plan, root, 'iwlwifi')
            with self.assertRaisesRegex(RuntimeError, 'blocked or absent'):
                hardware.check_load_plan('', root, 'iwlwifi')
            with self.assertRaisesRegex(RuntimeError, 'Unexpected module load plan'):
                hardware.check_load_plan('install /bin/false\n', root, 'iwlwifi')

    def test_text_alias_without_runtime_binary_indexes_is_not_certified(self):
        paths = {'lib/modules/fixture/modules.alias', 'lib/modules/fixture/modules.dep.bin'}
        with mock.patch.object(hardware, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'modules.alias.bin'):
                hardware.verify_module_resolution(Path('image'), paths, 'lib/modules/fixture', 'fixture')
        command.assert_not_called()

    def test_failed_check_replaces_stale_passing_report(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'verification.json'
            report.write_text(json.dumps({'verified': True}))
            with mock.patch.object(hardware, 'verify_build', side_effect=RuntimeError('missing driver')):
                with self.assertRaises(SystemExit) as failure:
                    hardware.main(['--kernel-version', 'fixture', '--output', str(report)])
            self.assertEqual(failure.exception.code, 1)
            self.assertEqual(json.loads(report.read_text())['verified'], False)
            self.assertEqual(json.loads(report.read_text())['error'], 'missing driver')
