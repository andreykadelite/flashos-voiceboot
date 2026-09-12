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
        for section in ('usb', 'boot'):
            self.put('dist/' + section + '/flashos/flashos.marker', 'FlashOS test-version\n')
        report = {'sources_match': True, 'no_test_fixture': True, 'system_version': 'test-version',
                  'sources_sha256': {'menu.py': hashlib.sha256(source.read_bytes()).hexdigest()},
                  'squashfs_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
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
