"""Recovery of disposable boot requests without weakening action-time checks."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catalog import Image
from selection import HEADER, SIZE, arm, environment, initialize


class BootSelectionRecoveryTests(unittest.TestCase):
    def test_startup_recovers_missing_truncated_and_oversized_state(self):
        for previous in (None, b'', b'bad', HEADER + b'x' * 2048):
            with self.subTest(previous=repr(previous)[:80]), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'ventoy').mkdir()
                target = root / 'ventoy/flashos.env'
                if previous is not None:
                    target.write_bytes(previous)
                initialize(root)
                self.assertEqual(target.read_bytes(), environment())
                iso = root / 'iso/test.iso'
                iso.parent.mkdir()
                iso.write_bytes(b'test media')
                image = Image(iso, '/iso/test.iso', 'Test', iso.stat().st_size, True)
                arm(root, image)
                self.assertEqual(target.read_bytes(), environment('/iso/test.iso'))

    def test_startup_creates_missing_state_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            initialize(root)
            self.assertEqual((root / 'ventoy/flashos.env').read_bytes(), environment())

    def test_startup_clears_pending_request_without_replacing_healthy_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'ventoy').mkdir()
            target = root / 'ventoy/flashos.env'
            target.write_bytes(environment('/iso/test.iso'))
            inode = target.stat().st_ino
            initialize(root)
            self.assertEqual(target.stat().st_ino, inode)
            self.assertEqual(target.stat().st_size, SIZE)
            self.assertEqual(target.read_bytes(), environment())

    def test_startup_does_not_follow_links_or_overwrite_directories(self):
        for linked in ('ventoy', 'file', 'directory'):
            with self.subTest(linked=linked), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                outside = root / 'unrelated'
                outside.mkdir()
                keep = outside / 'flashos.env'
                keep.write_bytes(b'unrelated data')
                try:
                    if linked == 'ventoy':
                        (root / 'ventoy').symlink_to(outside, target_is_directory=True)
                    else:
                        (root / 'ventoy').mkdir()
                        target = root / 'ventoy/flashos.env'
                        if linked == 'file':
                            target.symlink_to(keep)
                        else:
                            target.mkdir()
                except OSError:
                    self.skipTest('Host cannot create symlinks')
                with self.assertRaises(ValueError):
                    initialize(root)
                self.assertEqual(keep.read_bytes(), b'unrelated data')
