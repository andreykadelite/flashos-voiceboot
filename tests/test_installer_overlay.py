"""Regression checks for a large system ZIP outside the mapped Windows image."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "append_installer_payload", ROOT / "tools/append-installer-payload.py")
overlay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(overlay)


def fake_pe(machine=0x8664, image_bytes=8192):
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HH", data, 132, machine, 1)
    struct.pack_into("<HH", data, 148, 240, 0x22)
    optional = 152
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<II", data, optional + 32, 4096, 512)
    struct.pack_into("<II", data, optional + 56, image_bytes, 512)
    section = optional + 240
    data[section:section + 8] = b".text\0\0\0"
    struct.pack_into("<IIII", data, section + 8, 512, 4096, 512, 512)
    data[512:] = b"\x90" * 512
    return bytes(data)


class InstallerOverlay(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.exe = self.directory / "FlashOS-Setup.exe"
        self.exe.write_bytes(fake_pe())
        self.original = self.exe.read_bytes()
        self.payload = self.directory / "payload.zip"

    def make_payload(self, *, corrupt=False, extra=False, duplicate=False, zip64=False):
        data = b"persistent FlashOS system object"
        digest = hashlib.sha256(data).hexdigest()
        manifest = {
            "schema": 1, "system_version": "test",
            "objects": {digest: len(data)},
            "files": [{"section": "usb", "path": "flashos/test",
                       "bytes": len(data), "sha256": digest}],
        }
        raw = json.dumps(manifest, sort_keys=True).encode()
        with zipfile.ZipFile(self.payload, "w", allowZip64=True) as archive:
            archive.writestr("manifest.json", raw)
            archive.writestr("objects/" + digest, b"x" * len(data) if corrupt else data)
            if extra:
                archive.writestr("unexpected.txt", b"not in the passport")
            if duplicate:
                with self.assertWarns(UserWarning):
                    archive.writestr("manifest.json", raw)
        if zip64:
            # Force real ZIP64 end records while keeping this fixture small.
            # Offsets in those records remain relative to the source ZIP.
            contents = self.payload.read_bytes()
            old_end = contents[-22:]
            fields = struct.unpack("<4sHHHHIIH", old_end)
            self.assertEqual(b"PK\x05\x06", fields[0])
            count, central_bytes, central_offset = fields[4:7]
            body = contents[:-22]
            zip64_end = struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45,
                                   0, 0, count, count, central_bytes, central_offset)
            locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, len(body), 1)
            end = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0,
                              0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
            self.payload.write_bytes(body + zip64_end + locator + end)
        return hashlib.sha256(raw).hexdigest(), data

    def assert_original_preserved(self):
        self.assertEqual(self.original, self.exe.read_bytes())
        self.assertEqual([], list(self.directory.glob("*.overlay-*.tmp")))

    def test_archive_is_readable_with_correct_offsets_without_changing_pe_prefix(self):
        pin, data = self.make_payload()
        original_payload = self.payload.read_bytes()
        report = overlay.append_payload(self.exe, self.payload, pin)
        self.assertTrue(report["ok"])
        self.assertEqual(len(self.original), report["overlay_offset"])
        self.assertEqual(8192, report["mapped_image_bytes"])
        self.assertEqual(self.original + original_payload, self.exe.read_bytes())
        self.assertEqual(hashlib.sha256(self.exe.read_bytes()).hexdigest(), report["exe_sha256"])
        with zipfile.ZipFile(self.exe) as archive:
            info = archive.getinfo("manifest.json")
            self.assertGreaterEqual(info.header_offset, len(self.original))
            digest = hashlib.sha256(data).hexdigest()
            self.assertEqual(data, archive.read("objects/" + digest))
        self.assertEqual(original_payload, self.payload.read_bytes())

    def test_zip64_end_records_work_after_the_executable_prefix(self):
        pin, data = self.make_payload(zip64=True)
        report = overlay.append_payload(self.exe, self.payload, pin)
        self.assertEqual(1, report["objects_verified"])
        with zipfile.ZipFile(self.exe) as archive:
            digest = hashlib.sha256(data).hexdigest()
            self.assertEqual(data, archive.read("objects/" + digest))
            self.assertGreaterEqual(archive.getinfo("objects/" + digest).header_offset,
                                    len(self.original))

    def test_wrong_manifest_pin_is_refused_without_replacing_stub(self):
        self.make_payload()
        with self.assertRaisesRegex(ValueError, "compiled pin"):
            overlay.append_payload(self.exe, self.payload, "0" * 64)
        self.assert_original_preserved()

    def test_modified_object_with_valid_zip_crc_is_refused_before_publication(self):
        pin, _ = self.make_payload(corrupt=True)
        with self.assertRaisesRegex(ValueError, "SHA256"):
            overlay.append_payload(self.exe, self.payload, pin)
        self.assert_original_preserved()

    def test_unlisted_and_duplicate_entries_are_refused(self):
        for kwargs in ({"extra": True}, {"duplicate": True}):
            with self.subTest(kwargs=kwargs):
                pin, _ = self.make_payload(**kwargs)
                with self.assertRaises(ValueError):
                    overlay.append_payload(self.exe, self.payload, pin)
                self.assert_original_preserved()

    def test_already_appended_archive_cannot_be_appended_again(self):
        pin, _ = self.make_payload()
        overlay.append_payload(self.exe, self.payload, pin)
        final = self.exe.read_bytes()
        with self.assertRaisesRegex(ValueError, "already contains"):
            overlay.append_payload(self.exe, self.payload, pin)
        self.assertEqual(final, self.exe.read_bytes())
        self.assertEqual([], list(self.directory.glob("*.overlay-*.tmp")))

    def test_invalid_or_giant_mapped_pe_image_is_refused(self):
        pin, _ = self.make_payload()
        for data in (b"not a PE" * 100, fake_pe(machine=0x14C),
                     fake_pe(image_bytes=overlay.MAX_STUB_BYTES + 4096),
                     fake_pe(image_bytes=4096)):
            with self.subTest(header=data[:2]):
                self.exe.write_bytes(data)
                with self.assertRaises(ValueError):
                    overlay.append_payload(self.exe, self.payload, pin)
                self.assertEqual(data, self.exe.read_bytes())

    def test_failed_atomic_replacement_keeps_original_and_cleans_temporary(self):
        pin, _ = self.make_payload()
        with mock.patch.object(overlay.os, "replace", side_effect=OSError("locked executable")):
            with self.assertRaisesRegex(OSError, "locked executable"):
                overlay.append_payload(self.exe, self.payload, pin)
        self.assert_original_preserved()

    def test_failed_fsync_keeps_original_and_cleans_temporary(self):
        pin, _ = self.make_payload()
        with mock.patch.object(overlay.os, "fsync", side_effect=OSError("I/O failure")):
            with self.assertRaisesRegex(OSError, "I/O failure"):
                overlay.append_payload(self.exe, self.payload, pin)
        self.assert_original_preserved()

    def test_payload_changed_during_copy_is_detected_before_replacement(self):
        pin, _ = self.make_payload()
        original_copy = overlay._copy_stream
        calls = 0

        def change_input(source, target, total):
            nonlocal calls
            calls += 1
            result = original_copy(source, target, total)
            if calls == 1:
                with self.payload.open("ab") as changed:
                    changed.write(b"late modification")
            return result

        with mock.patch.object(overlay, "_copy_stream", side_effect=change_input):
            with self.assertRaisesRegex(ValueError, "changed"):
                overlay.append_payload(self.exe, self.payload, pin)
        self.assert_original_preserved()


if __name__ == "__main__":
    unittest.main()
