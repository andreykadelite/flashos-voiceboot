import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('image_gate', ROOT / 'tools/check-installer-image.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def fixture(machine=0x8664, image_size=8192, raw_size=512):
    data = bytearray(1024)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', data, 132, machine, 1, 0, 0, 0, 240, 0x22)
    struct.pack_into('<H', data, 152, 0x20b)
    struct.pack_into('<II', data, 184, 4096, 512)
    struct.pack_into('<II', data, 208, image_size, 512)
    data[392:397] = b'.text'
    struct.pack_into('<IIII', data, 400, 512, 4096, raw_size, 512)
    return data


class InstallerImageTests(unittest.TestCase):
    def inspect(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'installer.exe'
            path.write_bytes(data)
            return gate.image_layout(path)

    def test_appended_archive_does_not_expand_mapped_image(self):
        result = self.inspect(fixture() + b'outside executable sections' * 1000)
        self.assertEqual(result['image_bytes'], 8192)
        self.assertEqual(result['pe_file_end'], 1024)

    def test_rejects_wrong_architecture(self):
        with self.assertRaisesRegex(ValueError, 'x64'):
            self.inspect(fixture(machine=0x14c))

    def test_rejects_giant_executable_image(self):
        with self.assertRaisesRegex(ValueError, 'oversized'):
            self.inspect(fixture(image_size=2038190080))

    def test_rejects_sections_past_end_of_file(self):
        with self.assertRaisesRegex(ValueError, 'extent'):
            self.inspect(fixture(raw_size=1024))

    def test_rejects_truncated_pe_header(self):
        with self.assertRaises(ValueError):
            self.inspect(fixture()[:140])
