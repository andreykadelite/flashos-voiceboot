import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from installer.model import identity, unavailable_reason, validate_request
from installer.bundle import Bundle, safe_relative


def usb(**overrides):
    disk = dict(number=9, model='Test USB', serial='', size=16 * 1024**3, sector=512,
                path='device-path', pnp='USBSTOR\\device-id', bus='USB', boot=False, system=False,
                readonly=False, offline=False, protected=False, volumes=[])
    disk.update(overrides)
    return disk


class DiskRules(unittest.TestCase):
    def test_usb_without_serial_is_supported_when_device_paths_exist(self):
        self.assertEqual('', unavailable_reason(usb()))

    def test_system_and_non_usb_devices_are_refused(self):
        for changes in ({'boot': True}, {'system': True}, {'protected': True}, {'bus': 'NVMe'},
                        {'readonly': True}, {'offline': True}, {'path': ''}, {'pnp': ''}, {'sector': 4096}):
            with self.subTest(changes=changes):
                self.assertTrue(unavailable_reason(usb(**changes)))

    def test_size_and_filesystem_limits(self):
        self.assertTrue(unavailable_reason(usb(size=1_000_000_000)))
        self.assertTrue(unavailable_reason(usb(size=3 * 1024**4)))
        self.assertFalse(unavailable_reason(usb(size=3 * 1024**4), style='GPT'))
        self.assertTrue(unavailable_reason(usb(size=64_000_000_000), 'format', filesystem='FAT32'))

    def test_target_replacement_is_detected_even_at_same_number(self):
        request = dict(target=identity(usb()), action='install', style='MBR', filesystem='exFAT', erase_confirmed=True)
        for key, value in dict(serial='other', path='replacement', pnp='other', size=8 * 1024**3, number=10).items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_request(request, usb(**{key: value}))

    def test_explicit_confirmation_required_for_destructive_operations(self):
        for action in ('install', 'format'):
            request = dict(target=identity(usb()), action=action, style='MBR', filesystem='exFAT', erase_confirmed=False)
            with self.assertRaises(ValueError):
                validate_request(request, usb())
        request['action'] = 'verify'
        validate_request(request, usb())


class BundleRules(unittest.TestCase):
    def test_paths_cannot_escape_archive_destination(self):
        for value in ('../x', '/x', 'C:/x', 'a/../../b', 'a\\b', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_relative(value)

    def make_bundle(self, directory, corrupt=False):
        data = b'verified file'
        digest = hashlib.sha256(data).hexdigest()
        meta = dict(schema=1, objects={digest: len(data)}, files=[dict(section='usb', path='flashos/test', sha256=digest, bytes=len(data))])
        raw = json.dumps(meta).encode()
        path = Path(directory) / 'bundle.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('manifest.json', raw)
            archive.writestr('objects/' + digest, b'corrupt! file' if corrupt else data)
        return Bundle(path, hashlib.sha256(raw).hexdigest())

    def test_modified_payload_fails_before_disk_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.make_bundle(directory, corrupt=True)
            with self.assertRaises(ValueError):
                bundle.extract(Path(directory) / 'work', lambda e: None, lambda: False)

    def test_extraction_cancellation_does_not_start_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.make_bundle(directory)
            with self.assertRaises(InterruptedError):
                bundle.extract(Path(directory) / 'work', lambda e: None, lambda: True)

    def test_modified_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.make_bundle(directory)
            with self.assertRaises(ValueError):
                Bundle(bundle.path, '0' * 64)

    def test_selective_verification_does_not_unpack_system_image(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.make_bundle(directory)
            work = Path(directory) / 'work'
            events = []
            bundle.extract(work, events.append, lambda: False, required=set())
            self.assertEqual([], list((work / 'objects').iterdir()))
            self.assertTrue((work / 'manifest.json').is_file())
            self.assertEqual(0, events[-1]['total_bytes'])

    def test_extraction_reports_complete_byte_total(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.make_bundle(directory)
            events = []
            bundle.extract(Path(directory) / 'work', events.append, lambda: False)
            self.assertEqual(100, events[-1]['percent'])
            self.assertEqual(events[-1]['bytes'], events[-1]['total_bytes'])


if __name__ == '__main__':
    unittest.main()
