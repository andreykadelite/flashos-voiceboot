import curses
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jobs import Job
from recovery import image_path, read_manifest, restore_job, repair_job, MANIFEST, map_extent, image_extent, catalog_job
from restore_verify import ranges
from toolkit import Device
from launch import previous_service_processes


class RecoveryTests(unittest.TestCase):
    @staticmethod
    def manifest():
        return {'format': 'flashos-partclone-1', 'file': 'partition.pcl', 'source_bytes': 4096,
                'image_bytes': 500, 'fstype': 'ext4', 'sha256': '0' * 64,
                'filesystem_bytes': 4096, 'map_bytes': 100, 'map_sha256': '1' * 64,
                'used_sha256': '2' * 64, 'created': '2026-10-22 12:00:00', 'model': 'Test disk'}

    def test_malformed_manifests_are_rejected_as_validation_errors(self):
        valid = self.manifest()
        malformed = [None, [], 42, 'image', {**valid, 'fstype': []},
                     {**valid, 'created': None}, {**valid, 'model': {}}]
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / 'partition.pcl'
            for value in malformed:
                with self.subTest(value=value):
                    image.with_name(MANIFEST).write_text(json.dumps(value), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        read_manifest(image)
            image.with_name(MANIFEST).write_text('[' * 1500 + '0' + ']' * 1500, encoding='utf-8')
            with self.assertRaises(ValueError):
                read_manifest(image)

    def test_corrupt_manifest_does_not_hide_healthy_backups(self):
        with tempfile.TemporaryDirectory() as temporary:
            medium = Path(temporary)
            malformed = [[], {**self.manifest(), 'created': None},
                         {**self.manifest(), 'fstype': []}]
            for index, value in enumerate([*malformed, self.manifest()]):
                folder = medium / 'backups' / ('files-' + str(index) * 12)
                folder.mkdir(parents=True)
                (folder / MANIFEST).write_text(json.dumps(value), encoding='utf-8')
            function = catalog_job(medium, '/dev/test')
            job = Job('catalog', function)
            with patch('recovery.medium_info'):
                job._run(function)
            self.assertFalse(job.failed, job.result)
            self.assertEqual(len(job.payload), 1)
            self.assertEqual(len(job.lines), 3)
            self.assertIn('files-' + '3' * 12, job.payload[0][1])

    def test_restart_refuses_to_overlap_lingering_service_process(self):
        with patch('launch.Path.read_text', side_effect=['0::/system.slice/flashos.service\n', '42\n99\n']), patch('launch.os.getpid', return_value=42):
            self.assertEqual(previous_service_processes(), [99])
        with patch('launch.Path.read_text', return_value='0::/unrelated.scope\n'):
            self.assertEqual(previous_service_processes(), [])
    def test_smaller_filesystem_has_separate_exact_extent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'blocks.map'
            path.write_text('# Domain\n0x2000 ?\n0x0 0x1000 +\n0x1000 0x1000 ?\n')
            self.assertEqual(map_extent(path, 0x4000), 0x2000)
            with self.assertRaises(ValueError):
                map_extent(path, 0x1000)
        job = Job('info', lambda job: None)
        job.command = lambda *args, **kwargs: (0, 'Device size: 33.6 MB = 32768 Blocks\nBlock size: 1024 Byte\nimage format: 0002\n')
        self.assertEqual(image_extent(job, Path('/image')), 33554432)

    def test_long_progress_stream_is_bounded_and_error_tail_is_kept(self):
        job = Job('stream', lambda job: None)
        received = []
        code, text = job.command([sys.executable, '-c', "for i in range(10000): print('x'*100)"],
            output_limit=1000, log_output=False, capture_output=False, on_line=received.append)
        self.assertEqual(code, 0)
        self.assertEqual(len(received), 10000)
        self.assertLessEqual(len(text), 1000)
        with self.assertRaises(RuntimeError):
            job.command([sys.executable, '-c', "import sys; print('specific device error'); sys.exit(9)"],
                        log_output=False, capture_output=False)
        self.assertIn('specific device error', list(job.lines))

    def test_image_rejects_escape_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for value in ('../x', '/backups/files-' + 'a'*12, 'recovery/files-' + 'a'*12, 'backups/../../x'):
                with self.assertRaises(ValueError):
                    image_path(root, value)
            (root / 'backups').symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                image_path(root, 'backups/files-' + 'a'*12)

    def test_partial_overlapping_or_out_of_bounds_maps_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'map'
            for text in ('0x0 0x1000 +\n', '0x0 0x1000 +\n0x800 0x1800 ?\n',
                         '0x0 0x3000 +\n', '0x0 0x1000 +\n0x1000 0x1000 X\n'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    list(ranges(path, 0x2000))
            path.write_text('# map\n0x2000 ?\n0x0 0x1000 +\n0x1000 0x1000 ?\n')
            self.assertEqual(list(ranges(path, 0x2000)), [(0, 4096)])

    def test_missing_map_integrity_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / 'partition.pcl'
            data = {'format': 'flashos-partclone-1', 'file': image.name, 'source_bytes': 4096,
                'image_bytes': 500, 'fstype': 'ext4', 'sha256': '0'*64}
            image.with_name(MANIFEST).write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                read_manifest(image)

    def test_restore_never_writes_when_verify_fails_or_target_too_small(self):
        device = Device('/dev/vdb1', 'part', 4096, 'test', 's', '', False, '253:1', '/dev/vdb')
        for failure in (ValueError('SHA256 mismatch'), None):
            with patch('recovery.check_device'), patch('recovery.medium_info'), patch('recovery.verify_image') as verify:
                if failure:
                    verify.side_effect = failure
                else:
                    verify.return_value = (Path('/image'), {'source_bytes': 8192})
                commands = []
                function = restore_job('backups/files-' + 'a'*12, device, '/dev/sda', Path('/usb'))
                job = Job('test', function)
                job.command = lambda command, **kwargs: commands.append(command) or (0, '')
                job._run(function)
                self.assertTrue(job.failed)
                self.assertFalse(any(c[0] == 'partclone.restore' for c in commands))

    def test_restore_rejects_whole_disk(self):
        device = Device('/dev/vdb', 'disk', 4096, 'test', 's', '', False, '253:1', '/dev/vdb')
        function = restore_job('backups/files-' + 'a'*12, device, '/dev/sda', Path('/usb'))
        job = Job('test', function)
        job._run(function)
        self.assertTrue(job.failed)

    def test_fat_usage_error_is_failure(self):
        device = Device('/dev/vdb1', 'part', 4096, 'test', 's', 'vfat', False, '253:1', '/dev/vdb')
        function = repair_job(device, '/dev/sda')
        with patch('recovery.check_device'):
            job = Job('test', function)
            job.command = lambda command, **kwargs: (2 if command[0] == 'fsck.fat' else 0, 'Usage error')
            job._run(function)
        self.assertTrue(job.failed)

    def test_repair_requires_clean_read_only_recheck(self):
        device = Device('/dev/vdb1', 'part', 4096, 'test', 's', 'ext4', False, '253:1', '/dev/vdb')
        function = repair_job(device, '/dev/sda')
        with patch('recovery.check_device'):
            job = Job('test', function)
            commands = []
            def command(args, **kwargs):
                commands.append(args)
                return (4 if '-n' in args else 0, 'result')
            job.command = command
            job._run(function)
        self.assertTrue(job.failed)
        self.assertIn(['e2fsck', '-f', '-n', '/dev/vdb1'], commands)


if __name__ == '__main__':
    unittest.main()
