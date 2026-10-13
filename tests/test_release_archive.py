import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    'join_system_archive', Path(__file__).resolve().parents[1] / 'tools/join-system-archive.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleaseArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = b'first part\x00second part'
        self.output = self.root / 'system.zip'
        self.manifest = self.root / 'system-archive-parts.json'
        def entry(name, content):
            return dict(file=name, bytes=len(content),
                        sha256=hashlib.sha256(content).hexdigest())
        self.value = dict(format=1, archive=entry('system.zip', self.data), parts=[])
        for index, content in enumerate([self.data[:10], self.data[10:]], 1):
            name = f'system.zip.{index:03d}'
            (self.root / name).write_bytes(content)
            self.value['parts'].append(entry(name, content))
        self.save()

    def save(self):
        self.manifest.write_text(json.dumps(self.value), encoding='utf-8')

    def test_reassembles_exact_bytes_and_checks_all_hashes(self):
        self.assertEqual(MODULE.join(self.manifest), self.output)
        self.assertEqual(self.output.read_bytes(), self.data)

    def test_existing_archive_is_preserved(self):
        self.output.write_bytes(b'existing archive')
        with self.assertRaises(FileExistsError):
            MODULE.join(self.manifest)
        self.assertEqual(self.output.read_bytes(), b'existing archive')

    def test_corrupt_or_missing_part_leaves_no_partial_archive(self):
        part = self.root / 'system.zip.002'
        for action in ('corrupt', 'missing'):
            if action == 'corrupt':
                part.write_bytes(b'corrupt')
            else:
                part.unlink()
            with self.assertRaises((ValueError, FileNotFoundError)):
                MODULE.join(self.manifest)
            self.assertFalse(self.output.exists())

    def test_checks_complete_hash_even_when_parts_are_valid(self):
        self.value['archive']['sha256'] = '0' * 64
        self.save()
        with self.assertRaises(ValueError):
            MODULE.join(self.manifest)
        self.assertFalse(self.output.exists())

    def test_rejects_traversal_reordering_and_invalid_sizes_before_writing(self):
        for mutation in ('traversal', 'order', 'size'):
            with self.subTest(mutation=mutation):
                value = json.loads(self.manifest.read_text())
                if mutation == 'traversal':
                    self.value['archive']['file'] = '../system.zip'
                elif mutation == 'order':
                    self.value['parts'].reverse()
                else:
                    self.value['parts'][0]['bytes'] = -1
                self.save()
                with self.assertRaises(ValueError):
                    MODULE.join(self.manifest)
                self.assertFalse(self.output.exists())
                self.value = value
                self.save()
