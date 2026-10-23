"""Exercise publication boundaries on temporary files, never real USB media."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import public_payload as payload

spec = importlib.util.spec_from_file_location('package_docs', TOOLS / 'package-public-docs.py')
docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs)


class PublicPayload(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.put('tools/public-files.json', json.dumps(['docs/ru/guide.md']))
        for name in payload.ROOT_DOCS:
            self.put(name, 'public ' + name)
        self.put('docs/ru/guide.md', 'public guide')
        docs.package(self.root)
        for name in payload.USB_FILES:
            self.put('dist/usb/' + name, 'system')
        for name in payload.BOOT_FILES:
            self.put('dist/boot/' + name, 'boot')

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf-8')
        return path

    def test_unlisted_payloads_are_refused_and_iso_is_omitted(self):
        self.put('dist/usb/iso/user.iso', 'user image')
        self.assertFalse(any('iso/' in name for name, _ in payload.payload_files(self.root, 'usb')))
        for section, name in [('usb', 'notes.txt'), ('usb', 'flashos/private.json'), ('boot', 'audit.txt')]:
            path = self.put('dist/' + section + '/' + name, 'private')
            with self.subTest(name=name), self.assertRaises(ValueError):
                payload.payload_files(self.root, section)
            path.unlink()

    def test_stale_document_is_refused(self):
        self.put('dist/usb/docs/ru/guide.md', 'old private report')
        with self.assertRaises(ValueError):
            payload.payload_files(self.root, 'usb')

    def system_proof(self):
        source = self.put('src/menu.py', 'current source')
        image = self.root / 'dist/usb/flashos/filesystem.squashfs'
        boot_names = set(payload.BOOT_STAGED_CONFIGS) | set(payload.BOOT_ROOT_CONFIGS) | {'oneshot.cfg'}
        for name in boot_names:
            self.put('boot/' + name, 'reviewed ' + name + '\n')
        for name, targets in payload.BOOT_STAGED_CONFIGS.items():
            for target in targets:
                self.put(target, (self.root / 'boot' / name).read_text(encoding='utf-8'))
        self.put('dist/ventoy.disk.img.xz', 'prepared Ventoy image')
        self.put('dist/ventoy-efi.img', 'matching raw Ventoy image')
        for section in ('usb', 'boot'):
            self.put('dist/' + section + '/flashos/flashos.marker', 'FlashOS test-version\n')
        for name in ('vmlinuz', 'initrd'):
            (self.root / 'dist/boot/flashos' / name).write_bytes((self.root / 'dist/usb/flashos' / name).read_bytes())
        report = {'app_store': {'verified': True, 'filesystem_sha256': hashlib.sha256((self.root / 'dist/usb/flashos/filesystem.squashfs').read_bytes()).hexdigest()}, 'sources_match': True, 'no_test_fixture': True, 'system_version': 'test-version',
                  'sources_sha256': {'menu.py': hashlib.sha256(source.read_bytes()).hexdigest()},
                  'squashfs_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                  'boot_sources_match': True,
                  'boot_sources_sha256': {name: hashlib.sha256((self.root / 'boot' / name).read_bytes()).hexdigest()
                                         for name in boot_names},
                  'boot_artifact_sha256': {name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
                                           for name in payload.BOOT_ARTIFACTS},
                  'hardware': {'verified': True, 'artifact_sha256': {
                      name: hashlib.sha256((self.root / 'dist/usb/flashos' / name).read_bytes()).hexdigest()
                      for name in ('filesystem.squashfs', 'vmlinuz', 'initrd')}}}
        report['terminal'] = {'verified': True, 'filesystem_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
        self.put('dist/toolkit-build-verification.json', json.dumps(report))
        return source, image

    def test_packaging_requires_the_verified_image_and_matching_sources(self):
        source, image = self.system_proof()
        payload.verified_system(self.root, 'test-version')
        for target in (source, image):
            original = target.read_bytes()
            target.write_bytes(b'changed after verification')
            with self.subTest(target=target), self.assertRaises(ValueError):
                payload.verified_system(self.root, 'test-version')
            target.write_bytes(original)

    def test_python_source_lf_and_crlf_variants_match_the_same_certified_code(self):
        lf = b"VALUE = 'same code'\nprint(VALUE)\n"
        for certified in (lf, lf.replace(b'\n', b'\r\n')):
            for checkout in (lf, lf.replace(b'\n', b'\r\n'), lf.replace(b'\n', b'\r\n', 1)):
                with self.subTest(certified=certified, checkout=checkout):
                    source, _ = self.system_proof()
                    report_path = self.root / 'dist/toolkit-build-verification.json'
                    report = json.loads(report_path.read_text(encoding='utf-8'))
                    report['sources_sha256']['menu.py'] = hashlib.sha256(certified).hexdigest()
                    report_path.write_text(json.dumps(report), encoding='utf-8')
                    source.write_bytes(checkout)
                    payload.verified_system(self.root, 'test-version')

    def test_all_boot_source_edits_require_new_certification(self):
        self.system_proof()
        for source in (self.root / 'boot').iterdir():
            original = source.read_bytes()
            source.write_bytes(original + b'changed after verification\n')
            with self.subTest(source=source.name), self.assertRaisesRegex(ValueError, 'Boot sources'):
                payload.verified_system(self.root, 'test-version')
            source.write_bytes(original)
        added = self.put('boot/new-config.cfg', 'new input')
        with self.assertRaisesRegex(ValueError, 'Boot sources'):
            payload.verified_system(self.root, 'test-version')
        added.unlink()
        source.unlink()
        with self.assertRaisesRegex(ValueError, 'Boot sources'):
            payload.verified_system(self.root, 'test-version')

    def test_boot_source_line_endings_allow_transport_but_keep_other_bytes(self):
        self.system_proof()
        for source in (self.root / 'boot').iterdir():
            original = source.read_bytes().replace(b'\r\n', b'\n')
            source.write_bytes(original.replace(b'\n', b'\r\n'))
        payload.verified_system(self.root, 'test-version')
        source.write_bytes(original.replace(b'\n', b'\r\r\n'))
        with self.assertRaisesRegex(ValueError, 'Boot sources'):
            payload.verified_system(self.root, 'test-version')

    def test_boot_artifacts_cannot_change_after_certification(self):
        self.system_proof()
        for name in payload.BOOT_ARTIFACTS:
            target = self.root / name
            original = target.read_bytes()
            target.write_bytes(original + b'changed')
            with self.subTest(name=name), self.assertRaises(ValueError):
                payload.verified_system(self.root, 'test-version')
            target.write_bytes(original)

    def test_staged_boot_text_allows_line_ending_transport(self):
        self.system_proof()
        for targets in payload.BOOT_STAGED_CONFIGS.values():
            for name in targets:
                target = self.root / name
                content = target.read_bytes().replace(b'\r\n', b'\n')
                target.write_bytes(content.replace(b'\n', b'\r\n'))
        payload.verified_system(self.root, 'test-version')

    def test_certification_requires_current_staged_config_in_both_efi_locations(self):
        self.system_proof()
        report_path = self.root / 'dist/toolkit-build-verification.json'
        original_report = json.loads(report_path.read_text(encoding='utf-8'))
        for targets in payload.BOOT_STAGED_CONFIGS.values():
            for name in targets:
                target = self.root / name
                original = target.read_bytes()
                target.write_bytes(b'old boot configuration')
                report = {**original_report, 'boot_artifact_sha256': {
                    **original_report['boot_artifact_sha256'], name: hashlib.sha256(target.read_bytes()).hexdigest()}}
                report_path.write_text(json.dumps(report), encoding='utf-8')
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Stale boot configuration'):
                    payload.verified_system(self.root, 'test-version')
                target.write_bytes(original)
                report_path.write_text(json.dumps(original_report), encoding='utf-8')

    def test_old_reports_without_boot_certification_are_rejected(self):
        self.system_proof()
        report_path = self.root / 'dist/toolkit-build-verification.json'
        original = json.loads(report_path.read_text(encoding='utf-8'))
        for key in ('boot_sources_match', 'boot_sources_sha256', 'boot_artifact_sha256'):
            report = dict(original)
            report.pop(key)
            report_path.write_text(json.dumps(report), encoding='utf-8')
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Boot'):
                payload.verified_system(self.root, 'test-version')

    def test_terminal_runtime_requires_certification_for_the_same_image(self):
        self.system_proof()
        report_path = self.root / 'dist/toolkit-build-verification.json'
        original = json.loads(report_path.read_text(encoding='utf-8'))
        for record in (None, {}, {'verified': False}, {'verified': True, 'filesystem_sha256': 'old'}):
            report = dict(original)
            if record is None:
                report.pop('terminal')
            else:
                report['terminal'] = record
            report_path.write_text(json.dumps(report), encoding='utf-8')
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, 'Terminal runtime'):
                payload.verified_system(self.root, 'test-version')

    def test_python_source_other_bytes_cannot_be_hidden_by_line_ending_equivalence(self):
        certified = b"VALUE = 'same code'\nprint(VALUE)\n"
        for changed in (certified.replace(b'same code', b'new code'), b'\xef\xbb\xbf' + certified,
                        certified + b' ', certified.replace(b'\n', b'\r\r\n')):
            with self.subTest(changed=changed):
                source, _ = self.system_proof()
                report_path = self.root / 'dist/toolkit-build-verification.json'
                report = json.loads(report_path.read_text(encoding='utf-8'))
                report['sources_sha256']['menu.py'] = hashlib.sha256(certified).hexdigest()
                report_path.write_text(json.dumps(report), encoding='utf-8')
                source.write_bytes(changed)
                with self.assertRaisesRegex(ValueError, 'stale or unverified'):
                    payload.verified_system(self.root, 'test-version')

    def test_python_source_filename_set_must_match_without_missing_extra_or_duplicate_names(self):
        for change in ('missing', 'extra', 'nested-duplicate'):
            with self.subTest(change=change):
                source, _ = self.system_proof()
                original = source.read_bytes()
                extra = None
                if change == 'missing':
                    source.unlink()
                else:
                    extra = self.put('src/' + ('new.py' if change == 'extra' else 'nested/menu.py'), '')
                    extra.write_bytes(original)
                with self.assertRaisesRegex(ValueError, 'stale or unverified'):
                    payload.verified_system(self.root, 'test-version')
                source.write_bytes(original)
                if extra:
                    extra.unlink()

    def test_line_ending_equivalence_never_relaxes_image_or_boot_binary_hashes(self):
        _, image = self.system_proof()
        binary = b'unchanged binary content\nsecond line\n'
        image.write_bytes(binary)
        for name in ('vmlinuz', 'initrd'):
            for section in ('usb', 'boot'):
                (self.root / 'dist' / section / 'flashos' / name).write_bytes(binary)
        report_path = self.root / 'dist/toolkit-build-verification.json'
        report = json.loads(report_path.read_text(encoding='utf-8'))
        digest = hashlib.sha256(binary).hexdigest()
        report['squashfs_sha256'] = report['app_store']['filesystem_sha256'] = digest
        report['terminal']['filesystem_sha256'] = digest
        report['hardware']['artifact_sha256'] = {name: digest for name in ('filesystem.squashfs', 'vmlinuz', 'initrd')}
        for name in ('vmlinuz', 'initrd'):
            report['boot_artifact_sha256']['dist/boot/flashos/' + name] = digest
        report_path.write_text(json.dumps(report), encoding='utf-8')
        payload.verified_system(self.root, 'test-version')
        for target in (image, self.root / 'dist/usb/flashos/vmlinuz', self.root / 'dist/usb/flashos/initrd'):
            with self.subTest(target=target):
                target.write_bytes(binary.replace(b'\n', b'\r\n'))
                with self.assertRaises(ValueError):
                    payload.verified_system(self.root, 'test-version')
                target.write_bytes(binary)

    def test_hardware_proof_is_required_and_boot_files_cannot_be_swapped(self):
        self.system_proof()
        report_path = self.root / 'dist/toolkit-build-verification.json'
        report = json.loads(report_path.read_text())
        report.pop('hardware')
        report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'Hardware'):
            payload.verified_system(self.root, 'test-version')
        self.system_proof()
        for name in ('usb/flashos/vmlinuz', 'usb/flashos/initrd', 'boot/flashos/vmlinuz', 'boot/flashos/initrd'):
            path = self.root / 'dist' / name
            original = path.read_bytes()
            path.write_bytes(b'different boot file')
            with self.subTest(name=name), self.assertRaises(ValueError):
                payload.verified_system(self.root, 'test-version')
            path.write_bytes(original)

    def test_app_runtime_proof_is_required_and_cannot_certify_another_image(self):
        for change in ('missing', 'stale'):
            self.system_proof()
            report_path = self.root / 'dist/toolkit-build-verification.json'
            report = json.loads(report_path.read_text())
            if change == 'missing':
                report.pop('app_store')
            else:
                report['app_store']['filesystem_sha256'] = '0' * 64
            report_path.write_text(json.dumps(report))
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'Application runtime'):
                payload.verified_system(self.root, 'test-version')

    def test_packaging_rejects_an_old_system_version(self):
        self.system_proof()
        with self.assertRaises(ValueError):
            payload.verified_system(self.root, 'new-version')

    def test_marker_edits_do_not_relabel_the_verified_image(self):
        self.system_proof()
        for section in ('usb', 'boot'):
            self.put('dist/' + section + '/flashos/flashos.marker', 'FlashOS new-version\n')
        with self.assertRaises(ValueError):
            payload.verified_system(self.root, 'new-version')

    def test_stale_export_does_not_replace_an_existing_archive(self):
        source, _ = self.system_proof()
        source.write_text('source changed after verification')
        archive = self.put('release/FlashOS-test-version-system.zip', 'existing archive')
        export_spec = importlib.util.spec_from_file_location('export_system', TOOLS / 'export-system.py')
        export = importlib.util.module_from_spec(export_spec)
        export_spec.loader.exec_module(export)
        with mock.patch.object(export, 'ROOT', self.root), mock.patch.object(export, 'SYSTEM_VERSION', 'test-version'):
            with self.assertRaises(ValueError):
                export.main()
        self.assertEqual(archive.read_text(), 'existing archive')

    def test_managed_directories_are_replaced_without_residue(self):
        for directory in ('docs', 'licenses', 'metadata'):
            self.put('dist/usb/' + directory + '/private.json', 'private')
        docs.package(self.root)
        for directory in ('docs', 'licenses', 'metadata'):
            self.assertFalse((self.root / 'dist/usb' / directory / 'private.json').exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink fixture')
    def test_payload_directory_links_are_refused_even_under_iso(self):
        outside = self.root / 'outside'
        outside.mkdir()
        for name in ('dist/usb/iso', 'dist/boot/linked'):
            link = self.root / name
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                payload.payload_files(self.root, 'usb' if '/usb/' in name else 'boot')
            link.unlink()

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink fixture')
    def test_document_links_are_refused_before_writes(self):
        outside = self.put('outside.txt', 'must remain')
        for name in ('README.md', 'dist/usb/README.md'):
            path = self.root / name
            old = path.read_bytes()
            path.unlink()
            path.symlink_to(outside)
            with self.assertRaises(ValueError):
                docs.package(self.root)
            self.assertEqual(outside.read_text(), 'must remain')
            path.unlink()
            path.write_bytes(old)

    def make_archive(self, members):
        path = self.root / 'downloads/input.zip'
        path.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(path, 'w') as archive:
            for name in members:
                archive.writestr(name, 'binary')
        data = path.read_bytes()
        self.put('metadata/build-inputs.json', json.dumps([{
            'file': 'input.zip', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]))
        return path

    def test_input_pin_refuses_replaced_archive(self):
        path = self.make_archive(['root/program.exe'])
        with payload.pinned_zip(self.root, 'input.zip') as archive:
            self.assertEqual(archive.read('root/program.exe'), b'binary')
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaises(ValueError):
            payload.pinned_zip(self.root, 'input.zip')

    def test_input_zip_refuses_case_collisions_and_traversal(self):
        for members in (['root/A.exe', 'root/a.exe'], ['root/../private'], ['C:/private']):
            self.make_archive(members)
            with self.subTest(members=members), self.assertRaises(ValueError):
                payload.pinned_zip(self.root, 'input.zip')
