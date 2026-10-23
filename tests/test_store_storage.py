import errno
import io
import os
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import store_storage as storage


class Job:
    def __init__(self, responses=None):
        self.commands = []
        self.responses = responses or {}

    def command(self, args, **kwargs):
        self.commands.append(args)
        return self.responses.get(args[0], (0, ''))

    def update(self, text):
        self.status = text

    def check(self):
        pass


class StoreStorageSafety(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.medium = Path(self.temporary.name) / 'usb'
        (self.medium / 'flashos/applications').mkdir(parents=True)
        (self.medium / 'flashos/flashos.marker').write_text('FlashOS')
        self.env = storage.StoreEnvironment(self.medium, '/dev/usb')
        self.env.state = Path(self.temporary.name) / 'state.json'
        self.info = patch('store_storage.medium_info', return_value=('/dev/usb1', 'exfat'))
        self.info.start()
        self.addCleanup(self.info.stop)

    def test_shutdown_service_allows_storage_flush_and_cleanup_budget(self):
        import configparser
        service = configparser.ConfigParser(interpolation=None)
        service.read(Path(__file__).resolve().parents[1] / 'boot/flashos-apps.service')
        self.assertGreaterEqual(service.getint('Service', 'TimeoutStopSec'), storage.WRITE_TIMEOUT + 600)
        self.assertIn('store_storage.py --shutdown', service.get('Service', 'ExecStop'))
        self.assertIn('flashos.service', service.get('Unit', 'Before'))

    def test_offline_resolver_does_not_block_apps_and_later_connection_refreshes_dns(self):
        self.env.root = Path(self.temporary.name) / 'app-root'
        (self.env.root / 'etc').mkdir(parents=True)
        source = Path(self.temporary.name) / 'host-resolver'
        self.env.refresh_resolver(source)
        resolver = self.env.root / 'etc/resolv.conf'
        self.assertIn('Offline applications remain available', resolver.read_text())
        source.write_text('nameserver 10.0.2.3\n')
        self.env.refresh_resolver(source)
        self.assertEqual(resolver.read_text(), source.read_text())

    def test_existing_store_is_preserved_without_seed_or_commands(self):
        self.env.image.write_bytes(b'existing data')
        job = Job()
        with self.assertRaises(ValueError):
            self.env.initialize(job)
        self.assertEqual(self.env.image.read_bytes(), b'existing data')
        self.assertEqual(job.commands, [])

    def test_fat32_four_gib_and_insufficient_outer_space_refused(self):
        with patch('store_storage.medium_info', return_value=('/dev/usb1', 'vfat')):
            with self.assertRaisesRegex(ValueError, 'FAT32'):
                self.env._validate(Job(), 4)
        with patch('store_storage.shutil.disk_usage') as usage:
            usage.return_value.free = storage.RESERVE
            with self.assertRaisesRegex(ValueError, 'свободного'):
                self.env._validate(Job(), 2, 2 * 1024**3)

    def test_seed_digest_failure_preserves_existing_files(self):
        archive = Path(self.temporary.name) / 'seed'
        archive.write_bytes(b'wrong')
        passport = archive.with_suffix('.json')
        passport.write_text(json.dumps({'format': 1, 'sha256': '0'*64, 'unpacked_bytes': 1}))
        with patch.object(storage, 'SEED', archive), patch.object(storage, 'SEED_METADATA', passport):
            with self.assertRaisesRegex(ValueError, 'сумма'):
                self.env.initialize(Job())
        self.assertFalse(self.env.image.exists())

    def test_tar_traversal_refused_and_absolute_container_link_rewritten(self):
        archive = Path(self.temporary.name) / 'seed.tar.xz'
        destination = Path(self.temporary.name) / 'root'
        destination.mkdir()
        with tarfile.open(archive, 'w:xz') as output:
            item = tarfile.TarInfo('../outside')
            item.size = 1
            output.addfile(item, io.BytesIO(b'x'))
        with patch.object(storage, 'SEED', archive):
            with self.assertRaises(ValueError):
                storage.extract_seed(destination)
        self.assertFalse((destination.parent / 'outside').exists())
        if sys.platform == 'win32':
            return
        with tarfile.open(archive, 'w:xz') as output:
            directory = tarfile.TarInfo('var')
            directory.type = tarfile.DIRTYPE
            output.addfile(directory)
            item = tarfile.TarInfo('var/run')
            item.type = tarfile.SYMTYPE
            item.linkname = '/run'
            output.addfile(item)
        with patch.object(storage, 'SEED', archive):
            storage.extract_seed(destination)
        self.assertEqual((destination / 'var/run').readlink(), Path('../run'))

    @unittest.skipIf(sys.platform == 'win32', 'Linux symlink semantics')
    def test_symlinked_store_parent_is_rejected(self):
        outside = Path(self.temporary.name) / 'outside'
        outside.mkdir()
        (self.medium / 'flashos/applications').rmdir()
        (self.medium / 'flashos/applications').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.env._validate(Job())
        self.assertFalse(list(outside.iterdir()))

    def test_fsck_uncorrected_errors_do_not_mount_store(self):
        self.env.image.write_bytes(b'fake')
        self.env.mount = Path(self.temporary.name) / 'mount'
        job = Job({'findmnt': (1, ''), 'losetup': (0, '/dev/loop7'),
                   'blkid': (0, 'ext4'), 'e2fsck': (4, 'errors')})
        def command(args, **kwargs):
            job.commands.append(args)
            if args[0] == 'losetup' and '--list' in args:
                return 0, str(self.env.image)
            return job.responses.get(args[0], (0, ''))
        job.command = command
        with self.assertRaisesRegex(RuntimeError, 'чистую'):
            self.env._attach(job, self.env.image)
        self.assertFalse(any(args[0] == 'mount' for args in job.commands))
        self.assertEqual(self.env.image.read_bytes(), b'fake')

    def test_grow_never_shrinks_or_overwrites_image(self):
        self.env.image.write_bytes(b'existing')
        with self.assertRaises(ValueError):
            self.env.grow(Job(), 0)
        self.assertEqual(self.env.image.read_bytes(), b'existing')

    def test_failed_offline_repair_preserves_image_and_does_not_resize(self):
        self.env.image.write_bytes(b'important user data')
        job = Job({'blkid': (0, 'ext4'), 'e2fsck': (4, 'uncorrected errors')})
        from contextlib import nullcontext
        with patch('store_storage.writable', return_value=nullcontext()), patch.object(self.env, 'close'):
            with self.assertRaisesRegex(RuntimeError, 'Файл сохранён'):
                self.env.repair_image(job)
        self.assertEqual(self.env.image.read_bytes(), b'important user data')
        self.assertFalse(any(args[0] in ('resize2fs', 'mkfs.ext4', 'mount') or
                             (args[0] == 'losetup' and '--associated' not in args)
                             for args in job.commands))

    def test_offline_operations_refuse_a_store_owned_by_another_live_process(self):
        self.env.image.write_bytes(b'important user data')
        self.env.state.write_text('{}')
        for operation in (lambda job: self.env.repair_image(job),
                          lambda job: self.env.grow(job, 2)):
            job = Job()
            with patch.object(storage, 'runtime_state', return_value={
                    'owner_pid': 77, 'owner_starttime': '123'}), \
                 patch.object(storage, 'process_starttime', return_value='123'), \
                 patch.object(self.env, 'close'), \
                 patch.object(storage, 'allocate'), \
                 patch.object(storage, 'shutdown') as shutdown, \
                 patch.object(storage, 'writable') as writable:
                with self.assertRaisesRegex(RuntimeError, 'другим процессом'):
                    operation(job)
                shutdown.assert_not_called()
                writable.assert_not_called()
            self.assertEqual(job.commands, [])
            self.assertEqual(self.env.image.read_bytes(), b'important user data')

    def test_offline_operations_refuse_an_associated_image_without_running_fsck(self):
        self.env.image.write_bytes(b'important user data')
        for operation in (lambda job: self.env.repair_image(job),
                          lambda job: self.env.grow(job, 2)):
            job = Job({'losetup': (0, '/dev/loop9')})
            with patch.object(self.env, 'close'), \
                 patch.object(storage, 'allocate'), \
                 patch.object(storage, 'writable') as writable:
                with self.assertRaisesRegex(RuntimeError, 'остаётся подключённым'):
                    operation(job)
                writable.assert_not_called()
            self.assertEqual(len(job.commands), 1)
            self.assertIn('--associated', job.commands[0])
            self.assertEqual(self.env.image.read_bytes(), b'important user data')

    def test_attach_refuses_an_unknown_mount_state_without_creating_a_loop(self):
        job = Job({'findmnt': (32, 'mount table unreadable')})
        with self.assertRaisesRegex(RuntimeError, 'Не удалось проверить'):
            self.env._attach(job, self.env.image)
        self.assertEqual(len(job.commands), 1)
        self.assertEqual(job.commands[0][0], 'findmnt')

    @unittest.skipIf(sys.platform == 'win32', 'Linux root ownership semantics')
    def test_shutdown_rejects_changed_loop_without_unmount_or_detach(self):
        self.env.image.write_bytes(b'preserved')
        self.env.state.write_text(json.dumps({'format': 1, 'medium': str(self.medium.resolve()),
            'live_disk': '/dev/usb', 'image': str(self.env.image),
            'mount': str(storage.MOUNT), 'loop': '/dev/loop7', 'restore_ro': True}))
        self.env.state.chmod(0o600)
        job = Job({'losetup': (0, '/another/valuable/image.ext4')})
        with self.assertRaisesRegex(ValueError, 'изменилось'):
            storage.shutdown(job, self.medium, self.env.state)
        self.assertEqual(self.env.image.read_bytes(), b'preserved')
        self.assertTrue(self.env.state.exists())
        self.assertFalse(any(args[0] in ('umount', 'mount') or '-d' in args for args in job.commands))

    @unittest.skipIf(sys.platform == 'win32', 'Linux root ownership semantics')
    def test_shutdown_rejects_untrusted_state_file_permissions(self):
        self.env.state.write_text('{}')
        self.env.state.chmod(0o644)
        job = Job()
        with self.assertRaisesRegex(ValueError, 'Небезопасный'):
            storage.shutdown(job, self.medium, self.env.state)
        self.assertEqual(job.commands, [])

    def test_shutdown_preserves_loop_and_state_when_mount_inspection_fails(self):
        self.env.image.write_bytes(b'preserved')
        self.env.state.write_text('{}')
        state = {'format': 1, 'medium': str(self.medium.resolve()),
                 'live_disk': '/dev/usb', 'source': '/dev/usb1',
                 'image': str(self.env.image), 'mount': str(storage.MOUNT),
                 'loop': '/dev/loop7', 'restore_ro': True}
        job = Job({'losetup': (0, str(self.env.image)), 'findmnt': (32, '')})
        with patch.object(storage, 'runtime_state', return_value=state):
            with self.assertRaisesRegex(RuntimeError, 'паспорт сохранён'):
                storage.shutdown(job, self.medium, self.env.state)
        self.assertTrue(self.env.state.exists())
        self.assertEqual(self.env.image.read_bytes(), b'preserved')
        self.assertFalse(any(args[0] in ('umount', 'mount') or '-d' in args
                             for args in job.commands))

    @unittest.skipIf(sys.platform == 'win32', 'Linux root ownership semantics')
    def test_open_refuses_live_owner_without_closing_other_mount(self):
        with self.env.image.open('wb') as stream:
            stream.truncate(64 * 1024**2)
        self.env.state.write_text(json.dumps({'owner_pid': 77, 'owner_starttime': '123'}))
        self.env.state.chmod(0o600)
        with patch('store_storage.process_starttime', return_value='123'), patch('store_storage.shutdown') as shutdown:
            with self.assertRaisesRegex(RuntimeError, 'другим процессом'):
                self.env.open(Job())
            shutdown.assert_not_called()

    @unittest.skipIf(sys.platform == 'win32', 'Linux root ownership semantics')
    def test_shutdown_detached_loop_retry_never_detaches_a_device(self):
        self.env.image.write_bytes(b'preserved store')
        self.env.state.write_text(json.dumps({'format': 1, 'medium': str(self.medium.resolve()),
            'live_disk': '/dev/usb', 'source': '/dev/usb1', 'image': str(self.env.image),
            'mount': str(storage.MOUNT), 'loop': '/dev/loop7', 'restore_ro': True}))
        self.env.state.chmod(0o600)
        job = Job({'losetup': (0, ''), 'findmnt': (1, '')})
        storage.shutdown(job, self.medium, self.env.state)
        self.assertFalse(self.env.state.exists())
        self.assertEqual(self.env.image.read_bytes(), b'preserved store')
        self.assertFalse(any(args[0] == 'umount' or '-d' in args for args in job.commands))
        self.assertTrue(any('remount,ro' in args for args in job.commands))

    @unittest.skipIf(sys.platform == 'win32', 'Linux root ownership semantics')
    def test_detached_loop_retry_refuses_a_mount_or_associated_image(self):
        self.env.image.write_bytes(b'preserved store')
        state = {'format': 1, 'medium': str(self.medium.resolve()), 'source': '/dev/usb1',
            'live_disk': '/dev/usb', 'image': str(self.env.image),
            'mount': str(storage.MOUNT), 'loop': '/dev/loop7', 'restore_ro': True}
        for mounted, associated in ((True, ''), (False, '/dev/loop9')):
            self.env.state.write_text(json.dumps(state)); self.env.state.chmod(0o600)
            job = Job()
            def command(args, **kwargs):
                job.commands.append(args)
                if args[0] == 'findmnt':
                    return (0, '{}') if mounted else (1, '')
                if args[0] == 'losetup':
                    return 0, associated if '--associated' in args else ''
                return 0, ''
            job.command = command
            with self.assertRaises(ValueError):
                storage.shutdown(job, self.medium, self.env.state)
            self.assertTrue(self.env.state.exists())
            self.assertFalse(any(args[0] in ('umount', 'mount') or '-d' in args for args in job.commands))

    def test_partial_preparation_archive_preserves_all_bytes_without_formatting(self):
        from contextlib import nullcontext
        creating = self.env.image.with_suffix('.ext4.creating')
        content = b'partial initialization and user bytes' * 300
        creating.write_bytes(content)
        with patch('store_storage.writable', return_value=nullcontext()), \
             patch.object(storage.os, 'fsync') if sys.platform == 'win32' else nullcontext(), \
             patch.object(storage, 'fsync_directory') if sys.platform == 'win32' else nullcontext():
            archived = self.env.archive_preparation(Job())
        self.assertFalse(creating.exists())
        self.assertFalse(self.env.image.exists())
        self.assertEqual(archived.read_bytes(), content)
        self.assertEqual(archived.parent.name, 'incomplete')

    def test_preparation_recovery_does_not_replace_existing_store(self):
        self.env.image.write_bytes(b'existing portable apps')
        creating = self.env.image.with_suffix('.ext4.creating')
        creating.write_bytes(b'partial seed')
        job = Job()
        with self.assertRaises(ValueError):
            self.env.recover_preparation(job)
        self.assertEqual(self.env.image.read_bytes(), b'existing portable apps')
        self.assertEqual(creating.read_bytes(), b'partial seed')
        self.assertEqual(job.commands, [])

    def test_truncated_preparation_has_clear_recovery_guidance_without_commands(self):
        creating = self.env.image.with_suffix('.ext4.creating')
        creating.write_bytes(b'incomplete mkfs' + bytes(4081))
        original = creating.read_bytes()
        job = Job()
        with patch.object(storage, 'seed_info', return_value={'unpacked_bytes': 1024**3}), \
             self.assertRaisesRegex(ValueError, 'в архив и создайте новый'):
            self.env.recover_preparation(job)
        self.assertEqual(creating.read_bytes(), original)
        self.assertEqual(job.commands, [])
        self.assertFalse(self.env.image.exists())

    def test_atomic_finish_never_replaces_competing_destination(self):
        source = Path(self.temporary.name) / 'creating'
        destination = Path(self.temporary.name) / 'existing'
        source.write_bytes(b'prepared store'); destination.write_bytes(b'existing store')
        with self.assertRaises(FileExistsError):
            storage.rename_preserving(source, destination)
        self.assertEqual(source.read_bytes(), b'prepared store')
        self.assertEqual(destination.read_bytes(), b'existing store')

    def test_busy_runtime_keeps_store_and_loop_attached(self):
        self.env._mounted = True
        self.env.loop = '/dev/loop7'
        self.env._bindings = [Path('/busy')]
        job = Job()
        def busy(args, **kwargs):
            job.commands.append(args)
            raise RuntimeError('busy')
        job.command = busy
        with self.assertRaises(RuntimeError):
            self.env.close(job)
        self.assertTrue(self.env._mounted)
        self.assertEqual(self.env.loop, '/dev/loop7')
        self.assertFalse(any(args[0] == 'losetup' for args in job.commands))


class StoreAllocationRegression(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'store.ext4.creating'

    def test_unsupported_allocation_falls_back_and_keeps_existing_growth_bytes(self):
        self.path.write_bytes(b'original')
        with patch.object(storage.os, 'posix_fallocate', side_effect=OSError(errno.EOPNOTSUPP, 'unsupported'), create=True), \
             patch.object(storage, 'ALLOCATION_CHUNK', 3):
            storage.allocate(self.path, 19, 8, job=Job())
        self.assertEqual(self.path.read_bytes(), b'original' + bytes(11))

    def test_fat_sequential_reservation_bypasses_native_fallocate(self):
        with patch.object(storage.os, 'posix_fallocate', create=True) as native, \
             patch.object(storage, 'ALLOCATION_CHUNK', 7):
            storage.allocate(self.path, 23, sequential=True)
        native.assert_not_called()
        self.assertEqual(self.path.read_bytes(), bytes(23))

    def test_glibc_write_only_emulation_error_uses_sequential_writes(self):
        with patch.object(storage.os, 'posix_fallocate', side_effect=OSError(errno.EBADF, 'emulation needs read access'), create=True):
            storage.allocate(self.path, 23)
        self.assertEqual(self.path.read_bytes(), bytes(23))

    def test_device_and_no_space_errors_are_not_hidden_by_zero_write_fallback(self):
        for error_code in (errno.ENOSPC, errno.EIO, errno.EROFS, errno.EFBIG):
            with self.subTest(error=error_code):
                path = self.path.with_name(str(error_code))
                with patch.object(storage.os, 'posix_fallocate', side_effect=OSError(error_code, 'failure'), create=True), \
                     patch.object(storage.os, 'write') as write, \
                     self.assertRaises(OSError) as failure:
                    storage.allocate(path, 1024)
                self.assertEqual(failure.exception.errno, error_code)
                write.assert_not_called()
                self.assertTrue(path.is_file())

    def test_partial_writes_are_completed_without_missing_bytes(self):
        original_write = os.write
        with patch.object(storage.os, 'posix_fallocate', side_effect=OSError(errno.ENOSYS, 'unsupported'), create=True), \
             patch.object(storage.os, 'write', side_effect=lambda fd, view: original_write(fd, view[:2])), \
             patch.object(storage, 'ALLOCATION_CHUNK', 7):
            storage.allocate(self.path, 23)
        self.assertEqual(self.path.read_bytes(), bytes(23))

    def test_cancelled_allocation_keeps_partial_file_and_never_extends_past_chunk(self):
        job = Job()
        def check():
            if self.path.exists() and self.path.stat().st_size >= 7:
                raise storage.Cancelled()
        job.check = check
        with patch.object(storage.os, 'posix_fallocate', side_effect=OSError(errno.EOPNOTSUPP, 'unsupported'), create=True), \
             patch.object(storage, 'ALLOCATION_CHUNK', 7), \
             self.assertRaises(storage.Cancelled):
            storage.allocate(self.path, 21, job=job)
        self.assertEqual(self.path.read_bytes(), bytes(7))
        self.assertIn('0%', job.status)

    def test_invalid_growth_offset_never_overwrites_existing_bytes(self):
        self.path.write_bytes(b'valuable existing image')
        for start in (1, 100):
            with self.assertRaises(ValueError):
                storage.allocate(self.path, 200, start)
        self.assertEqual(self.path.read_bytes(), b'valuable existing image')

    def test_invalid_sizes_do_not_create_partial_images(self):
        for size, start in ((0, 0), (True, 0), (2, -1), (2, 2), (2.5, 0)):
            with self.assertRaises(ValueError):
                storage.allocate(self.path, size, start)
            self.assertFalse(self.path.exists())


class StorePreparationRegression(unittest.TestCase):
    def setUp(self):
        StoreStorageSafety.setUp(self)
        self.env.mount = Path(self.temporary.name) / 'mount'
        self.env.mount.mkdir()
        self.env.root = self.env.mount / 'root'
        self.archive = Path(self.temporary.name) / 'seed.tar.xz'
        with tarfile.open(self.archive, 'w:xz') as archive:
            entry = tarfile.TarInfo('hello')
            entry.size = 13
            archive.addfile(entry, io.BytesIO(b'verified seed'))
        from contextlib import nullcontext
        fixtures = [patch.object(storage, 'SEED', self.archive),
                    patch.object(storage, 'seed_info', return_value={'unpacked_bytes': 13, 'sha256': 'a' * 64}),
                    patch.object(storage, 'writable', side_effect=lambda *args: nullcontext()),
                    patch.object(storage, 'allocate', side_effect=self.allocate_fixture),
                    patch.object(storage, 'fsync_directory'),
                    patch.object(storage.os, 'fsync'),
                    patch.object(storage.os, 'chown', create=True),
                    patch.object(self.env, '_attach'),
                    patch.object(self.env, 'close'),
                    patch.object(self.env, 'open', return_value=self.env)]
        for fixture in fixtures:
            fixture.start()
            self.addCleanup(fixture.stop)

    @staticmethod
    def allocate_fixture(path, size, **kwargs):
        # Sparse host file is only a mock allocation, never an installed store.
        with path.open('xb') as stream:
            stream.write(b'partial image')
            stream.truncate(size)

    def assert_partial_preserved(self):
        with self.env.image.with_suffix('.ext4.creating').open('rb') as stream:
            self.assertEqual(stream.read(13), b'partial image')
        self.assertEqual(self.env.image.with_suffix('.ext4.creating').stat().st_size, 2 * 1024**3)

    def test_unexpected_mkfs_shrink_is_not_attached_or_promoted(self):
        job = Job()
        def truncated(args, **kwargs):
            if args[0] == 'mkfs.ext4':
                with self.env.image.with_suffix('.ext4.creating').open('r+b') as stream:
                    stream.truncate(4096)
            return 0, ''
        job.command = truncated
        with self.assertRaisesRegex(RuntimeError, 'Размер образа изменился'):
            self.env.initialize(job, 2)
        self.assertEqual(self.env.image.with_suffix('.ext4.creating').stat().st_size, 4096)
        self.env._attach.assert_not_called()
        self.assertFalse(self.env.image.exists())

    def test_success_checks_actual_seed_and_disables_discard(self):
        job = Job()
        self.env.initialize(job, 2)
        self.assertEqual((self.env.root / 'hello').read_bytes(), b'verified seed')
        self.assertTrue(self.env.image.is_file())
        self.assertFalse(self.env.image.with_suffix('.ext4.creating').exists())
        command = next(args for args in job.commands if args[0] == 'mkfs.ext4')
        self.assertIn('nodiscard', command)
        self.assertTrue(storage.allocate.call_args.kwargs['sequential'])
        self.assertEqual(self.env.metadata['runtime_sha256'], 'a' * 64)

    def test_bad_written_seed_is_preserved_as_incomplete_and_not_promoted(self):
        original_extract = storage.extract_seed
        def damaged(root, job):
            original_extract(root, job)
            (root / 'hello').write_bytes(b'damaged bytes')
        with patch.object(storage, 'extract_seed', side_effect=damaged), \
             self.assertRaisesRegex(RuntimeError, 'проверка записанных файлов') as failure:
            self.env.initialize(Job(), 2)
        self.assertIn('store.ext4.creating', str(failure.exception))
        self.assertTrue(self.env.image.with_suffix('.ext4.creating').is_file())
        self.assertFalse(self.env.image.exists())
        self.env.open.assert_not_called()

    def test_format_failure_reports_stage_and_keeps_all_partial_bytes(self):
        job = Job()
        job.command = lambda *args, **kw: (_ for _ in ()).throw(TimeoutError('slow USB'))
        with self.assertRaisesRegex(RuntimeError, 'создание файловой системы') as failure:
            self.env.initialize(job, 2)
        self.assertIn('slow USB', str(failure.exception))
        self.assertIn('store.ext4.creating', str(failure.exception))
        self.assert_partial_preserved()
        self.env._attach.assert_not_called()

    def test_cleanup_error_does_not_hide_original_extraction_failure(self):
        self.env.close.side_effect = RuntimeError('cleanup failed')
        with patch.object(storage, 'extract_seed', side_effect=OSError('USB write failed')), \
             self.assertRaises(RuntimeError) as failure:
            self.env.initialize(Job(), 2)
        self.assertIn('USB write failed', str(failure.exception))
        self.assertIn('cleanup failed', str(failure.exception))
        self.assertFalse(self.env.image.exists())
        self.assert_partial_preserved()

    def test_cancelled_extraction_preserves_partial_and_does_not_publish(self):
        job = Job()
        with patch.object(storage, 'extract_seed', side_effect=storage.Cancelled()), \
             self.assertRaises(storage.Cancelled):
            self.env.initialize(job, 2)
        self.assertIn('сохранены', job.cancel_result)
        self.assertTrue(self.env.image.with_suffix('.ext4.creating').is_file())
        self.assertFalse(self.env.image.exists())
        self.env.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
