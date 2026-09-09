import lzma
from pathlib import Path
import struct
import sys
import unittest
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from installer.boot_verify import partitions, fat16_files


class BootVerification(unittest.TestCase):
    def make_gpt(self, hidden=True):
        size = 4 * 1024**3
        sectors = size // 512
        mbr = bytearray(512)
        mbr[450] = 0xEE
        struct.pack_into('<II', mbr, 454, 1, sectors - 1)
        mbr[510:] = b'\x55\xaa'
        table = bytearray(128 * 128)
        second = (size - 544 * 1024**2) // 512
        third = second + 65536
        bounds = [(2048, second - 1), (second, third - 1), (third, sectors - 34)]
        for i, (first, last) in enumerate(bounds):
            offset = i * 128
            table[offset:offset + 16] = uuid.UUID('ebd0a0a2-b9e5-4433-87c0-68b6b72699c7').bytes_le
            table[offset + 16:offset + 32] = uuid.uuid4().bytes_le
            struct.pack_into('<QQQ', table, offset + 32, first, last, 0x8000000000000000 if i == 1 and hidden else 0)
        guid = uuid.uuid4().bytes_le
        chunks = {0: bytes(mbr), 2 * 512: bytes(table), (sectors - 33) * 512: bytes(table)}
        for lba, alternate, entries in [(1, sectors - 1, 2), (sectors - 1, 1, sectors - 33)]:
            header = bytearray(512)
            header[:8] = b'EFI PART'
            struct.pack_into('<III', header, 8, 0x10000, 92, 0)
            struct.pack_into('<QQQQ', header, 24, lba, alternate, 34, sectors - 34)
            header[56:72] = guid
            struct.pack_into('<QIII', header, 72, entries, 128, 128, zlib.crc32(table))
            struct.pack_into('<I', header, 16, zlib.crc32(header[:92]))
            chunks[lba * 512] = bytes(header)
        return chunks, size

    def test_gpt_primary_and_backup_with_ventoy_attribute(self):
        chunks, size = self.make_gpt()
        _, gpt, parts = partitions(lambda offset, length: chunks[offset][:length], size)
        self.assertTrue(gpt)
        self.assertEqual(len(parts), 3)

    def test_gpt_corrupt_backup_is_rejected(self):
        chunks, size = self.make_gpt()
        key = size - 512
        damaged = bytearray(chunks[key])
        damaged[56] ^= 1
        chunks[key] = bytes(damaged)
        with self.assertRaisesRegex(ValueError, 'контрольная сумма'):
            partitions(lambda offset, length: chunks[offset][:length], size)

    def test_gpt_missing_hidden_attribute_is_rejected(self):
        chunks, size = self.make_gpt(hidden=False)
        with self.assertRaisesRegex(ValueError, 'защитный атрибут'):
            partitions(lambda offset, length: chunks[offset][:length], size)

    def make_mbr(self, gap=0):
        raw = bytearray(512)
        size = 4 * 1024**3
        entries = [(0x80, 7, 2048, (size - 544 * 1024**2) // 512 - 2048),
                   (0, 0xEF, (size - 544 * 1024**2) // 512, 65536),
                   (0, 0xC, (size - 512 * 1024**2 + gap) // 512, (512 * 1024**2 - gap) // 512)]
        for i, (active, kind, start, count) in enumerate(entries):
            struct.pack_into('<B3sB3sII', raw, 446 + i * 16, active, b'\0'*3, kind, b'\0'*3, start, count)
        raw[510:] = b'\x55\xaa'
        return bytes(raw), size

    def test_expected_three_partition_mbr(self):
        raw, size = self.make_mbr()
        _, gpt, found = partitions(lambda offset, length: raw, size)
        self.assertFalse(gpt)
        self.assertEqual(3, len(found))

    def test_windows_alignment_gap_is_supported(self):
        raw, size = self.make_mbr(gap=512)
        partitions(lambda offset, length: raw, size)

    def test_destroyed_efi_partition_is_rejected(self):
        raw, size = self.make_mbr()
        raw = bytearray(raw)
        raw[466] = 0
        with self.assertRaises(ValueError):
            partitions(lambda offset, length: raw, size)

    def test_wrong_mbr_types_are_rejected(self):
        for position in (450, 482):
            raw, size = self.make_mbr()
            raw = bytearray(raw)
            raw[position] = 0x83
            with self.assertRaises(ValueError):
                partitions(lambda offset, length: raw, size)

    def test_gpt_missing_protective_length_is_rejected(self):
        chunks, size = self.make_gpt()
        damaged = bytearray(chunks[0])
        damaged[458:462] = b'\0' * 4
        chunks[0] = bytes(damaged)
        with self.assertRaisesRegex(ValueError, 'защитная запись'):
            partitions(lambda offset, length: chunks[offset][:length], size)

    @unittest.skipUnless((ROOT / 'dist/ventoy.disk.img.xz').exists(), 'Official verified Ventoy fixture')
    def test_all_efi_files_are_read_and_changed_executable_is_detected(self):
        data = lzma.decompress((ROOT / 'dist/ventoy.disk.img.xz').read_bytes())
        original = fat16_files(data)
        self.assertIn('efi/boot/bootx64.efi', original)
        self.assertGreater(len(original), 500)
        corrupt = bytearray(data)
        reserved = struct.unpack_from('<H', data, 14)[0]
        fat_sectors = struct.unpack_from('<H', data, 22)[0]
        roots = struct.unpack_from('<H', data, 17)[0]
        first_data = (reserved + data[16] * fat_sectors) * 512 + ((roots * 32 + 511) // 512) * 512
        offset = data.index(b'MZ', first_data)
        corrupt[offset + 80] ^= 1
        changed = fat16_files(corrupt)
        self.assertNotEqual(original, changed)


if __name__ == '__main__':
    unittest.main()
