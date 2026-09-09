import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catalog import discover, inspect_iso
from selection import arm, cancel, environment


def make_iso(path, boot=True):
    data = bytearray(40 * 2048)
    pvd = 16 * 2048
    data[pvd:pvd + 7] = b'\x01CD001\x01'
    data[pvd + 40:pvd + 72] = b'DEBIAN TEST'.ljust(32)
    if boot:
        record = 17 * 2048
        data[record:record + 7] = b'\x00CD001\x01'
        data[record + 7:record + 30] = b'EL TORITO SPECIFICATION\0'
        struct.pack_into('<I', data, record + 71, 20)
        catalog = 20 * 2048
        data[catalog] = 1
        data[catalog + 30:catalog + 32] = b'\x55\xaa'
        checksum = (-sum(struct.unpack('<16H', data[catalog:catalog + 32]))) & 0xffff
        struct.pack_into('<H', data, catalog + 28, checksum)
        data[catalog + 32] = 0x88
    data[18 * 2048:18 * 2048 + 7] = b'\xffCD001\x01'
    path.write_bytes(data)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'iso').mkdir()
        (self.root / 'ventoy').mkdir()
        (self.root / 'ventoy/flashos.env').write_bytes(environment())

    def tearDown(self):
        self.temp.cleanup()

    def test_only_bootable_iso_can_be_armed(self):
        make_iso(self.root / 'iso/debian.iso')
        make_iso(self.root / 'iso/data.iso', boot=False)
        (self.root / 'iso/truncated.iso').write_bytes(b'not an ISO')
        images = discover(self.root)
        self.assertEqual(sum(i.bootable for i in images), 1)
        image = next(i for i in images if i.bootable)
        arm(self.root, image)
        state = (self.root / 'ventoy/flashos.env').read_bytes()
        self.assertEqual(len(state), 1024)
        self.assertIn(b'flashos_next=/iso/debian.iso\n', state)
        with self.assertRaises(ValueError):
            arm(self.root, images[-1])
        cancel(self.root)
        self.assertNotIn(b'flashos_next', (self.root / 'ventoy/flashos.env').read_bytes())

    def test_environment_cannot_inject_another_variable(self):
        for path in ['/iso/x.iso\ncheck_signatures=no', '/other/a.iso', '/iso/../a.iso', '/iso/a.cfg', '/iso/' + 'x' * 1100 + '.iso']:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    environment(path)

    def test_nested_case_and_unicode_names_preserved(self):
        (self.root / 'iso/Linux').mkdir()
        make_iso(self.root / 'iso/Linux/Убунту 26.ISO')
        image, = discover(self.root)
        self.assertTrue(image.bootable)
        self.assertEqual(image.relative, '/iso/Linux/Убунту 26.ISO')
        arm(self.root, image)

    def test_changed_image_is_not_armed(self):
        path = self.root / 'iso/test.iso'
        make_iso(path)
        image, = discover(self.root)
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            arm(self.root, image)

    def test_fresh_scan_reflects_copied_and_deleted_images(self):
        self.assertEqual(discover(self.root), [])
        first = self.root / 'iso/debian.iso'
        second = self.root / 'iso/windows.iso'
        make_iso(first)
        self.assertEqual([i.relative for i in discover(self.root)], ['/iso/debian.iso'])
        make_iso(second)
        self.assertEqual([i.relative for i in discover(self.root)], ['/iso/debian.iso', '/iso/windows.iso'])
        first.unlink()
        self.assertEqual([i.relative for i in discover(self.root)], ['/iso/windows.iso'])
        second.unlink()
        self.assertEqual(discover(self.root), [])

    def test_symlinks_outside_iso_are_ignored(self):
        external = self.root / 'external.iso'
        make_iso(external)
        try:
            (self.root / 'iso/link.iso').symlink_to(external)
        except OSError:
            self.skipTest('Host cannot create symlinks')
        self.assertEqual(discover(self.root), [])

    def test_broken_catalog_checksum_is_rejected(self):
        path = self.root / 'iso/broken.iso'
        make_iso(path)
        data = bytearray(path.read_bytes())
        data[20 * 2048 + 28] ^= 1
        path.write_bytes(data)
        self.assertFalse(inspect_iso(path)[1])

    def test_bios_only_image_is_blocked_in_uefi(self):
        make_iso(self.root / 'iso/bios.iso')
        self.assertTrue(discover(self.root, 'bios')[0].bootable)
        self.assertFalse(discover(self.root, 'uefi')[0].bootable)

    def test_efi_section_is_recognized(self):
        path = self.root / 'iso/hybrid.iso'
        make_iso(path)
        data = bytearray(path.read_bytes())
        header = 20 * 2048 + 64
        data[header:header + 4] = b'\x91\xef\x01\x00'
        data[header + 32] = 0x88
        path.write_bytes(data)
        self.assertTrue(discover(self.root, 'uefi')[0].bootable)
        self.assertTrue(discover(self.root, 'bios')[0].bootable)

    def test_corrupted_state_is_not_overwritten(self):
        path = self.root / 'iso/test.iso'
        make_iso(path)
        image, = discover(self.root)
        (self.root / 'ventoy/flashos.env').write_bytes(b'bad')
        with self.assertRaises(ValueError):
            arm(self.root, image)


if __name__ == '__main__':
    unittest.main()
