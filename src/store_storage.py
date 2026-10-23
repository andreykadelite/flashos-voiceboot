"""Persistent Debian application root in an ext4 file on the verified boot USB."""
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import stat
import tarfile
import threading
import re

from medium import medium_info, writable, restore_readonly_after_crash, writer_count
from jobs import Cancelled

_STORE_LOCK = threading.RLock()
MOUNT = Path('/run/flashos-apps')
STATE = Path('/run/flashos-apps-state.json')
SEED = Path('/opt/flashos/app-runtime.tar.xz')
SEED_METADATA = Path('/opt/flashos/app-runtime.json')
RESERVE = 256 * 1024**2
# USB flash random writes and cache flushes can take several minutes.
WRITE_TIMEOUT = 900
ALLOCATION_CHUNK = 16 * 1024**2


def safe_path(base, path, directory=False):
    base, path = Path(base), Path(path)
    try:
        relative = path.relative_to(base)
    except ValueError:
        raise ValueError('Путь хранилища выходит за пределы флешки.')
    current = base
    for part in relative.parts:
        if part in ('', '.', '..'):
            raise ValueError('Недопустимый путь хранилища.')
        current /= part
        if current.is_symlink():
            raise ValueError('Ссылки в пути хранилища приложений запрещены.')
        if current.exists() and current != path and not current.is_dir():
            raise ValueError('Путь хранилища занят другим файлом.')
    if path.exists() and (not path.is_dir() if directory else not path.is_file()):
        raise ValueError('Неожиданный тип файла хранилища.')
    return path


def allocate(path, size, start=0, job=None, sequential=False):
    """Reserve actual space in bounded chunks, preserving a failed partial file."""
    if type(size) is not int or type(start) is not int or not 0 <= start < size:
        raise ValueError('Недопустимый размер выделяемого места.')
    # FAT/exFAT use sequential writes: glibc can emulate fallocate with
    # millions of single-byte writes, while reserving size alone is insufficient.
    flags = os.O_WRONLY | (os.O_EXCL | os.O_CREAT if start == 0 else 0)
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    if job is not None:
        job.check()
    descriptor = os.open(path, flags, 0o600)
    try:
        attributes = os.fstat(descriptor)
        if not stat.S_ISREG(attributes.st_mode):
            raise ValueError('Хранилище должно быть обычным файлом.')
        if attributes.st_size != start:
            raise ValueError('Размер файла изменился; выделение места остановлено.')
        native = not sequential and hasattr(os, 'posix_fallocate')
        offset = start
        last_percent = -1
        while offset < size:
            if job is not None:
                job.check()
                percent = (offset - start) * 100 // (size - start)
                if percent != last_percent:
                    job.update(f'Выделение места: {percent}%. Не отключайте USB.')
                    last_percent = percent
            count = min(ALLOCATION_CHUNK, size - offset)
            if native:
                try:
                    os.posix_fallocate(descriptor, offset, count)
                except OSError as error:
                    if error.errno not in (errno.EINVAL, errno.EOPNOTSUPP, errno.ENOSYS, errno.EBADF):
                        raise
                    native = False
            if not native:
                os.lseek(descriptor, offset, os.SEEK_SET)
                view = memoryview(bytes(count))
                while view:
                    if job is not None:
                        job.check()
                    written = os.write(descriptor, view)
                    if written == 0:
                        raise OSError('Не удалось выделить место хранилища.')
                    view = view[written:]
            offset += count
        if job is not None:
            job.update('Место выделено. Сохранение данных на USB; это может занять несколько минут.')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def seed_info():
    data = json.loads(SEED_METADATA.read_text(encoding='utf-8'))
    if type(data.get('format')) is not int or data['format'] != 1 or type(data.get('unpacked_bytes')) is not int or data['unpacked_bytes'] <= 0:
        raise ValueError('Неверный паспорт среды приложений.')
    with SEED.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if data.get('sha256') != digest:
        raise ValueError('Контрольная сумма среды приложений не совпадает.')
    return data


def extract_seed(destination, job=None):
    """Data filter rejects traversal, devices and link escapes; absolute in-root
    links such as /var/run -> /run are converted to safe relative links."""
    with tarfile.open(SEED, 'r:xz') as archive:
        members = archive.getmembers()
        if len(members) > 100000:
            raise ValueError('Архив среды содержит слишком много файлов.')
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or member.isdev() or member.isfifo():
                raise ValueError('Недопустимый путь или специальный файл в среде приложений.')
            if member.issym() and member.linkname.startswith('/'):
                member.linkname = posixpath.relpath(member.linkname.lstrip('/'), str(name.parent))
            if member.islnk() and member.linkname.startswith('/'):
                raise ValueError('Недопустимая жёсткая ссылка в среде приложений.')
        last_percent = -1
        def checked_members():
            nonlocal last_percent
            for index, member in enumerate(members):
                if job is not None:
                    job.check()
                    percent = index * 100 // max(1, len(members))
                    if percent != last_percent:
                        job.update(f'Распаковка среды приложений: {percent}%. Не отключайте USB.')
                        last_percent = percent
                yield member
        archive.extractall(destination, members=checked_members(), filter='data')


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def rename_preserving(source, destination):
    """Atomic rename with no replacement, including a competing creation."""
    if os.name == 'nt':
        os.rename(source, destination)
        return
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def verify_prepared_root(root, job=None):
    """Verify all seeded file bytes before promoting a completed preparation."""
    root = root.resolve()
    with tarfile.open(SEED, 'r|xz') as archive:
        for member in archive:
            if job is not None:
                job.check()
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or member.isdev() or member.isfifo():
                raise ValueError('Недопустимый путь в среде приложений.')
            candidate = root / member.name
            if not candidate.resolve().is_relative_to(root):
                raise ValueError('Среда содержит ссылку за пределы хранилища.')
            if member.isdir():
                if not candidate.is_dir():
                    raise ValueError('Подготовка среды не завершена: отсутствует папка.')
            elif member.issym():
                expected = member.linkname
                if expected.startswith('/'):
                    expected = posixpath.relpath(expected.lstrip('/'), str(name.parent))
                if not candidate.is_symlink() or str(candidate.readlink()) != expected:
                    raise ValueError('Подготовка среды не завершена: неверная ссылка.')
            elif member.isfile():
                if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size != member.size:
                    raise ValueError('Подготовка среды не завершена: отсутствует или неполон файл.')
                with archive.extractfile(member) as expected, candidate.open('rb') as actual:
                    if hashlib.file_digest(expected, 'sha256').digest() != hashlib.file_digest(actual, 'sha256').digest():
                        raise ValueError('Подготовка среды не завершена: содержимое файла отличается.')
            elif member.islnk():
                target = root / member.linkname
                if not target.resolve().is_relative_to(root) or not candidate.is_file() or not os.path.samefile(candidate, target):
                    raise ValueError('Подготовка среды не завершена: неверная жёсткая ссылка.')


def process_starttime(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        # /proc stat field22; the comm field can contain spaces and parentheses.
        return Path('/proc/' + str(pid) + '/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def runtime_state(path):
    attributes = Path(path).lstat()
    if not stat.S_ISREG(attributes.st_mode) or attributes.st_uid != 0 or attributes.st_mode & 0o077:
        raise ValueError('Небезопасный паспорт подключённого хранилища.')
    return json.loads(Path(path).read_text(encoding='utf-8'))


class StoreEnvironment:
    def __init__(self, medium, live_disk):
        self.medium = Path(medium)
        self.live_disk = live_disk
        self.image = self.medium / 'flashos/applications/store.ext4'
        self.mount = MOUNT
        self.state = STATE
        self.root = self.mount / 'root'
        self.loop = None
        self._loop_image = None
        self._mounted = False
        self._bindings = []
        self._lease = None
        self.metadata = {}
        self._medium_source = None
        self._owns_state = False

    @contextmanager
    def lock(self):
        with _STORE_LOCK:
            yield self

    def status(self):
        safe_path(self.medium, self.image)
        exists = self.image.is_file()
        return {'initialized': exists, 'mounted': self._mounted, 'root': str(self.root),
                'image': str(self.image), 'image_bytes': self.image.stat().st_size if exists else 0,
                'free_bytes': shutil.disk_usage(self.mount).free if self._mounted else None,
                'outer_free_bytes': shutil.disk_usage(self.medium).free, 'metadata': dict(self.metadata)}

    def _validate(self, job, size=None, additional=0):
        self._medium_source, fstype = medium_info(job, self.medium, self.live_disk)
        safe_path(self.medium, self.image)
        if size is not None:
            if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 256:
                raise ValueError('Размер хранилища должен быть от 1 до 256 ГиБ.')
            if fstype == 'vfat' and size * 1024**3 >= 4 * 1024**3:
                raise ValueError('FAT32 ограничивает файл хранилища: выберите до 3 ГиБ либо exFAT/NTFS.')
        existing = self.image.parent
        while not existing.exists() and existing != self.medium:
            existing = existing.parent
        if existing.stat().st_dev != self.medium.stat().st_dev:
            raise ValueError('Папка хранилища находится на другом файловом разделе.')
        if additional and shutil.disk_usage(self.medium).free < additional + RESERVE:
            raise ValueError('На флешке недостаточно свободного места для хранилища и резерва.')
        return fstype

    def _attach(self, job, image, check=True):
        if self.loop or self._mounted:
            raise RuntimeError('Хранилище уже подключено.')
        safe_path(self.medium, image)
        code, _ = job.command(['findmnt', '-n', '-M', str(self.mount)], timeout=5,
                              accepted=None, log_output=False)
        if code == 0:
            raise RuntimeError('Папка хранилища занята другим подключением.')
        if code != 1:
            raise RuntimeError('Не удалось проверить папку подключения хранилища.')
        if self.mount.is_symlink():
            raise ValueError('Папка подключения является ссылкой.')
        self.mount.mkdir(mode=0o700, exist_ok=True)
        _, loop = job.command(['losetup', '--find', '--show', '--', str(image)],
                              timeout=20, log_output=False, cleanup=True)
        self.loop = loop.strip()
        if not self.loop.startswith('/dev/loop') or not self.loop[9:].isdigit():
            self.loop = None
            raise RuntimeError('Неожиданное loop-устройство хранилища.')
        _, actual = job.command(['losetup', '--list', '--noheadings', '--output', 'BACK-FILE',
                                 self.loop], timeout=5, log_output=False)
        if str(Path(actual.strip()).resolve()) != str(image.resolve()):
            self.loop = None
            raise RuntimeError('Loop-устройство относится к другому файлу.')
        self._loop_image = image
        self._save_state()
        _, kind = job.command(['blkid', '-p', '-s', 'TYPE', '-o', 'value', self.loop],
                              timeout=10, log_output=False)
        if kind.strip() != 'ext4':
            raise ValueError('Файл хранилища не содержит ext4.')
        if check:
            code, _ = job.command(['e2fsck', '-f', '-n', self.loop], timeout=300,
                                  accepted=None, log_output=False)
            if code != 0:
                raise RuntimeError('Проверка хранилища не подтвердила чистую ext4. Файл сохранён; требуется отдельное восстановление.')
        job.command(['mount', '-t', 'ext4', '-o', 'rw,nosuid,nodev', '--',
                     self.loop, str(self.mount)], timeout=30, cleanup=True)
        self._mounted = True

    def _save_state(self):
        """Root-owned volatile record for cleanup after a GUI process crash."""
        if self.state.is_symlink():
            raise ValueError('Паспорт подключённого хранилища является ссылкой.')
        data = {'format': 1, 'medium': str(self.medium.resolve()),
                'live_disk': self.live_disk, 'image': str(self._loop_image.resolve()),
                'mount': str(self.mount), 'loop': self.loop, 'source': self._medium_source,
                'restore_ro': restore_readonly_after_crash(self.medium),
                'owner_pid': os.getpid(), 'owner_starttime': process_starttime(os.getpid())}
        temporary = self.state.with_suffix('.new')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(data, stream)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state)
            self._owns_state = True
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _bind(self, job, arguments, target):
        job.command(arguments, timeout=20, cleanup=True)
        self._bindings.append(target)

    def refresh_runtime(self, job):
        """Refresh ephemeral host integration without persisting machine state."""
        for relative in ('proc', 'sys', 'dev', 'run', 'tmp'):
            target = safe_path(self.root, self.root / relative, directory=True)
            target.mkdir(exist_ok=True)
        self._bind(job, ['mount', '-t', 'proc', '-o', 'nosuid,nodev,noexec', 'proc',
                         str(self.root / 'proc')], self.root / 'proc')
        self._bind(job, ['mount', '--bind', '/sys', str(self.root / 'sys')], self.root / 'sys')
        job.command(['mount', '-o', 'remount,bind,ro', str(self.root / 'sys')], timeout=20, cleanup=True)
        for relative, mode in (('dev', '0755'), ('run', '0755'), ('tmp', '1777')):
            self._bind(job, ['mount', '-t', 'tmpfs', '-o', 'nosuid,nodev,mode=' + mode,
                             'tmpfs', str(self.root / relative)], self.root / relative)
        for name in ('null', 'zero', 'random', 'urandom', 'full', 'tty'):
            target = self.root / 'dev' / name
            target.touch()
            self._bind(job, ['mount', '--bind', '/dev/' + name, str(target)], target)
        (self.root / 'dev/pts').mkdir()
        self._bind(job, ['mount', '-t', 'devpts', '-o', 'newinstance,ptmxmode=0666,mode=0620,gid=5',
                         'devpts', str(self.root / 'dev/pts')], self.root / 'dev/pts')
        (self.root / 'dev/ptmx').symlink_to('pts/ptmx')
        user = self.root / 'run/user/1000'
        user.mkdir(mode=0o700, parents=True)
        os.chown(user, 1000, 1000)
        for host, relative in (('/tmp/.X11-unix', 'tmp/.X11-unix'), ('/run/pipewire', 'run/pipewire')):
            if Path(host).is_dir():
                target = self.root / relative
                target.mkdir(exist_ok=True)
                self._bind(job, ['mount', '--bind', host, str(target)], target)
        policy = safe_path(self.root, self.root / 'usr/sbin/policy-rc.d')
        policy.write_text('#!/bin/sh\nexit 101\n', encoding='utf-8')
        policy.chmod(0o755)
        self.refresh_resolver()
        self.refresh_speech_runtime(job)
        helpers = safe_path(self.root, self.root / 'opt/flashos-store', directory=True)
        helpers.mkdir(parents=True, exist_ok=True)
        for name, target in (('store_packages.py', 'packages.py'), ('store_session.py', 'store_session.py'),
                             ('store_preferences.py', 'preferences.py'), ('network_proxy.py', 'network_proxy.py'),
                             ('orca_settings.py', 'orca_settings.py')):
            source = Path(__file__).with_name(name)
            if source.is_file():
                shutil.copyfile(source, safe_path(self.root, helpers / target))

    def refresh_speech_runtime(self, job, host=Path('/')):
        """Refresh the same read-only voices even in an older persistent app root."""
        host = Path(host)
        assets = ['usr/share/RHVoice', 'usr/lib/speech-dispatcher-modules/sd_rhvoice']
        libraries = sorted((host / 'usr/lib').glob('libRHVoice*.so*'))
        if not (host / assets[0]).is_dir() or not (host / assets[1]).is_file() or not libraries:
            raise RuntimeError('Общие голоса RHVoice отсутствуют в системе FlashOS.')
        # Data and the module are read-only; persistent user preferences stay separate.
        for relative in assets:
            source = host / relative
            directory = source.is_dir()
            target = safe_path(self.root, self.root / relative, directory=directory)
            target.parent.mkdir(parents=True, exist_ok=True)
            if directory:
                target.mkdir(exist_ok=True)
            elif not target.exists():
                target.touch()
            self._bind(job, ['mount', '--bind', str(source), str(target)], target)
            job.command(['mount', '-o', 'remount,bind,ro', str(target)], timeout=20, cleanup=True)
        # Keep library upgrades in RAM. A previous app root can contain different
        # RHVoice packages; never rewrite their libraries or linker aliases on USB.
        folder = safe_path(self.root, self.root / 'run/flashos-rhvoice-libs', directory=True)
        folder.mkdir(mode=0o755)
        for source in libraries:
            if source.is_symlink():
                continue
            target = safe_path(self.root, folder / source.name)
            target.touch()
            self._bind(job, ['mount', '--bind', str(source), str(target)], target)
            job.command(['mount', '-o', 'remount,bind,ro', str(target)], timeout=20, cleanup=True)
        for source in libraries:
            if not source.is_symlink():
                continue
            alias = os.readlink(source)
            if '/' in alias or not alias.startswith('libRHVoice') or alias not in {path.name for path in libraries}:
                raise RuntimeError('Недопустимая ссылка общей библиотеки RHVoice.')
            (folder / source.name).symlink_to(alias)

    def refresh_resolver(self, source=Path('/etc/resolv.conf')):
        # Installed applications and cached catalogs also work on an offline PC.
        try:
            text = source.read_text(encoding='utf-8')
        except FileNotFoundError:
            text = '# No active host DNS configuration. Offline applications remain available.\n'
        resolver = self.root / 'etc/resolv.conf'
        if resolver.is_symlink():
            resolver.unlink()
        safe_path(self.root, resolver)
        resolver.write_text(text, encoding='utf-8')

    def _cleanup_stale_state(self, job):
        if self.state.exists() or self.state.is_symlink():
            data = runtime_state(self.state)
            identity = data.get('owner_starttime')
            if not identity or process_starttime(data.get('owner_pid')) == identity:
                raise RuntimeError('Хранилище используется другим процессом. Закройте его перед повторным подключением.')
            shutdown(job, self.medium, self.state)

    def open(self, job):
        with self.lock():
            self._validate(job)
            if self._mounted:
                self.refresh_resolver()
                return self
            if not self.image.is_file():
                raise ValueError('Хранилище ещё не создано.')
            if self.image.stat().st_size < 64 * 1024**2:
                raise ValueError('Файл хранилища слишком мал.')
            self._cleanup_stale_state(job)
            self._lease = writable(job, self.medium, self.live_disk)
            try:
                self._lease.__enter__()
            except BaseException:
                self._lease = None
                raise
            try:
                self._attach(job, self.image)
                passport = safe_path(self.mount, self.mount / 'metadata.json')
                self.metadata = json.loads(passport.read_text(encoding='utf-8'))
                if self.metadata.get('format') != 1 or self.metadata.get('user_uid') != 1000:
                    raise ValueError('Неподдерживаемый паспорт хранилища.')
                safe_path(self.mount, self.root, directory=True)
                if not (self.root / 'usr/bin/apt-get').is_file():
                    raise ValueError('Среда приложений неполна.')
                self.refresh_runtime(job)
            except BaseException:
                self.close(job)
                raise
            return self

    def _preparation(self, job):
        self._validate(job)
        creating = safe_path(self.medium, self.image.with_suffix('.ext4.creating'))
        if not creating.is_file():
            raise ValueError('Незавершённая подготовка не найдена.')
        self._require_offline(job, creating)
        return creating

    def _require_offline(self, job, image):
        """Never check, resize or move a file still used by another app root."""
        self.close(job)
        self._cleanup_stale_state(job)
        _, loops = job.command(['losetup', '--associated', str(image), '--noheadings',
                                '--output', 'NAME'], timeout=10, log_output=False)
        if loops.strip():
            raise RuntimeError('Образ остаётся подключённым; его изменение запрещено.')

    def recover_preparation(self, job):
        """Finish only a clean, complete seed; never replace an existing store."""
        with self.lock():
            if self.image.exists() or self.image.is_symlink():
                raise ValueError('Хранилище уже существует. Его данные сохранены.')
            creating = safe_path(self.medium, self.image.with_suffix('.ext4.creating'))
            seed = seed_info()
            if creating.is_file() and creating.stat().st_size < seed['unpacked_bytes'] + RESERVE:
                raise ValueError('Файл подготовки неполон. Сохраните незавершённый образ в архив и создайте новый. Данные не удалены.')
            creating = self._preparation(job)
            with writable(job, self.medium, self.live_disk):
                try:
                    self._attach(job, creating)
                    passport = safe_path(self.mount, self.mount / 'metadata.json')
                    data = json.loads(passport.read_text(encoding='utf-8'))
                    if data.get('format') != 1 or data.get('user_uid') != 1000 or data.get('runtime_sha256') != seed['sha256']:
                        raise ValueError('Не подтверждена готовая среда. Временный файл сохранён.')
                    job.update('Проверка готовности сохранённой среды приложений.')
                    verify_prepared_root(self.root, job)
                    home = safe_path(self.root, self.root / 'home/flashos-app', directory=True)
                    home.mkdir(parents=True, exist_ok=True)
                    for item in [home, *home.rglob('*')]:
                        os.chown(item, 1000, 1000, follow_symlinks=False)
                finally:
                    self.close(job)
                with creating.open('rb') as stream:
                    os.fsync(stream.fileno())
                rename_preserving(creating, self.image)
                fsync_directory(self.image.parent)
                self.open(job)
            job.result = 'Сохранённая подготовка проверена и завершена; хранилище подключено.'
            return self

    def archive_preparation(self, job):
        """Retain every partial byte under a new dated name; never delete it."""
        with self.lock():
            creating = self._preparation(job)
            with writable(job, self.medium, self.live_disk):
                directory = safe_path(self.medium, self.image.parent / 'incomplete', directory=True)
                directory.mkdir(exist_ok=True)
                if directory.stat().st_dev != self.medium.stat().st_dev:
                    raise ValueError('Папка сохранения находится на другом разделе.')
                timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
                archived = safe_path(self.medium, directory / ('store-' + timestamp + '.ext4.creating'))
                with creating.open('rb') as stream:
                    os.fsync(stream.fileno())
                rename_preserving(creating, archived)
                fsync_directory(directory)
                fsync_directory(self.image.parent)
            job.payload = str(archived)
            job.result = 'Незавершённый файл сохранён: ' + str(archived.relative_to(self.medium)) + '. Можно повторить создание при наличии свободного места.'
            return archived

    def initialize(self, job, size_gib=2):
        with self.lock():
            if self.image.exists() or self.image.is_symlink():
                raise ValueError('Хранилище уже существует. Его данные сохранены.')
            job.update('Проверка среды приложений и свободного места на флешке.')
            data = seed_info()
            size = size_gib * 1024**3 if isinstance(size_gib, int) and not isinstance(size_gib, bool) else 0
            filesystem = self._validate(job, size_gib, size)
            if size < data['unpacked_bytes'] + RESERVE:
                raise ValueError('Размер хранилища недостаточен для среды приложений.')
            creating = self.image.with_suffix('.ext4.creating')
            stage = 'подключение флешки для записи'
            try:
                with writable(job, self.medium, self.live_disk):
                    safe_path(self.medium, self.image.parent, directory=True)
                    self.image.parent.mkdir(exist_ok=True)
                    safe_path(self.medium, creating)
                    if creating.exists():
                        raise ValueError('Найдена незавершённая подготовка. Выберите её проверку или сохранение в архив.')
                    self._cleanup_stale_state(job)
                    stage = 'выделение места'
                    allocate(creating, size, job=job, sequential=filesystem in ('exfat', 'vfat'))
                    job.check()
                    stage = 'создание файловой системы приложений'
                    job.update('Создание файловой системы приложений. Не отключайте USB.')
                    # Discard would undo reservation by punching holes in an image.
                    job.command(['mkfs.ext4', '-F', '-E', 'nodiscard', '-L', 'FLASHOS-APPS',
                                 str(creating)], timeout=WRITE_TIMEOUT, cleanup=True)
                    if creating.stat().st_size != size:
                        raise RuntimeError('Размер образа изменился при создании файловой системы. Временный файл сохранён.')
                    try:
                        stage = 'подключение образа приложений'
                        job.update('Подключение образа приложений.')
                        self._attach(job, creating, check=False)
                        self.root.mkdir()
                        stage = 'распаковка среды приложений'
                        extract_seed(self.root, job)
                        stage = 'проверка записанных файлов приложений'
                        job.update('Проверка записанных файлов приложений. Не отключайте USB.')
                        verify_prepared_root(self.root, job)
                        self.metadata = {'format': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
                                         'runtime_version': data.get('version', 'trixie'),
                                         'runtime_sha256': data['sha256'], 'user_uid': 1000}
                        (self.mount / 'metadata.json').write_text(json.dumps(self.metadata, indent=2) + '\n')
                        home = safe_path(self.root, self.root / 'home/flashos-app', directory=True)
                        home.mkdir(parents=True, exist_ok=True)
                        for item in [home, *home.rglob('*')]:
                            os.chown(item, 1000, 1000, follow_symlinks=False)
                        stage = 'сохранение среды на USB'
                        job.update('Сохранение среды на USB. На медленной флешке это может занять несколько минут.')
                        job.command(['sync', '-f', str(self.mount)], timeout=WRITE_TIMEOUT, cleanup=True)
                    except BaseException as error:
                        try:
                            self.close(job)
                        except Exception as cleanup_error:
                            raise RuntimeError(str(error) + '; отключение образа: ' + str(cleanup_error)) from error
                        raise
                    else:
                        self.close(job)
                    with creating.open('rb') as stream:
                        os.fsync(stream.fileno())
                    stage = 'завершение подготовки'
                    job.check()
                    rename_preserving(creating, self.image)
                    fsync_directory(self.image.parent)
                    stage = 'открытие готового хранилища'
                    job.update('Открытие готового хранилища приложений.')
                    self.open(job)
            except Cancelled:
                job.cancel_result = 'Подготовка остановлена. Записанные данные сохранены; при следующем входе выберите проверку подготовки.'
                raise
            except Exception as error:
                preserved = self.image if self.image.exists() else creating
                detail = (' Файл сохранён: ' + str(preserved.relative_to(self.medium)) + '.') if preserved.exists() else ''
                raise RuntimeError('Подготовка не завершена. Этап: ' + stage + '. ' + str(error) + detail) from error
            job.result = 'Хранилище приложений создано; программы и настройки сохраняются на флешке.'
            return self

    def close(self, job):
        with self.lock():
            while self._bindings:
                target = self._bindings[-1]
                job.command(['umount', '--', str(target)], timeout=30, cleanup=True)
                self._bindings.pop()
            if self._mounted:
                job.command(['sync', '-f', str(self.mount)], timeout=WRITE_TIMEOUT, cleanup=True)
                job.command(['umount', '--', str(self.mount)], timeout=30, cleanup=True)
                self._mounted = False
            if self.loop:
                _, actual = job.command(['losetup', '--list', '--noheadings', '--output', 'BACK-FILE',
                                         self.loop], timeout=5, log_output=False, cleanup=True)
                if self._loop_image is None or str(Path(actual.strip()).resolve()) != str(self._loop_image.resolve()):
                    raise RuntimeError('Loop-устройство изменилось; отключение запрещено.')
                job.command(['losetup', '-d', self.loop], timeout=20, cleanup=True)
                self.loop = None
                self._loop_image = None
            if self._owns_state and not self.loop and not self._mounted:
                # Retry safely if detach succeeded but removal of state failed.
                shutdown(job, self.medium, self.state)
                self._owns_state = False
            if self._lease is not None:
                lease, self._lease = self._lease, None
                lease.__exit__(None, None, None)
            return self

    def repair_image(self, job):
        """Offline conservative ext4 repair; preserve the image on every failure."""
        with self.lock():
            self._validate(job)
            if not self.image.is_file():
                raise ValueError('Хранилище ещё не создано.')
            self._require_offline(job, self.image)
            with writable(job, self.medium, self.live_disk):
                _, kind = job.command(['blkid', '-p', '-s', 'TYPE', '-o', 'value',
                                      str(self.image)], timeout=10, log_output=False)
                if kind.strip() != 'ext4':
                    raise ValueError('Файл хранилища не содержит ext4.')
                code, _ = job.command(['e2fsck', '-f', '-p', str(self.image)], timeout=600,
                                      accepted=None, cleanup=True)
                if code not in (0, 1, 2):
                    raise RuntimeError('Автоматическое восстановление не завершено. Файл сохранён; требуется отдельная проверка ext4.')
                _, details = job.command(['dumpe2fs', '-h', str(self.image)], timeout=20,
                                         log_output=False)
                fields = dict(line.split(':', 1) for line in details.splitlines() if ':' in line)
                filesystem_bytes = int(fields['Block count'].strip()) * int(fields['Block size'].strip())
                if filesystem_bytes > self.image.stat().st_size:
                    raise RuntimeError('Файл меньше ext4; уменьшение файловой системы запрещено.')
                # Finish an interrupted grow only into space already allocated.
                if filesystem_bytes < self.image.stat().st_size:
                    job.command(['resize2fs', str(self.image)], timeout=600, cleanup=True)
                code, _ = job.command(['e2fsck', '-f', '-n', str(self.image)], timeout=300,
                                      accepted=None, log_output=False)
                if code != 0:
                    raise RuntimeError('Повторная проверка ext4 не подтвердила восстановление. Файл сохранён.')
                self.open(job)
            job.result = 'Хранилище проверено; журнал и безопасно исправимые ошибки ext4 восстановлены.'
            return self

    def grow(self, job, size_gib):
        with self.lock():
            self._validate(job, size_gib)
            if not self.image.is_file():
                raise ValueError('Хранилище ещё не создано.')
            old = self.image.stat().st_size
            size = size_gib * 1024**3
            if size <= old:
                raise ValueError('Новый размер должен быть больше прежнего. Уменьшение запрещено.')
            filesystem = self._validate(job, size_gib, size - old)
            self._require_offline(job, self.image)
            with writable(job, self.medium, self.live_disk):
                code, _ = job.command(['e2fsck', '-f', '-n', str(self.image)], timeout=300,
                                      accepted=None, log_output=False)
                if code != 0:
                    raise RuntimeError('Перед увеличением требуется чистая ext4; исходный файл сохранён.')
                code, _ = job.command(['e2fsck', '-f', '-p', str(self.image)], timeout=300,
                                      accepted=None, cleanup=True)
                if code not in (0, 1, 2):
                    raise RuntimeError('Офлайн проверка перед увеличением не завершена; файл сохранён.')
                allocate(self.image, size, old, job=job, sequential=filesystem in ('exfat', 'vfat'))
                job.command(['resize2fs', str(self.image)], timeout=600, cleanup=True)
                with self.image.open('rb') as stream:
                    os.fsync(stream.fileno())
                self.open(job)
            job.result = 'Хранилище увеличено; существующие программы и настройки сохранены.'
            return self


def shutdown(job, medium, state=STATE):
    """Recover only the exact root-owned app mount, never arbitrary host loops."""
    state = Path(state)
    if not state.exists() and not state.is_symlink():
        return
    data = runtime_state(state)
    medium = Path(medium)
    mount = MOUNT
    image = medium / 'flashos/applications/store.ext4'
    recorded_image = Path(data.get('image', ''))
    if (data.get('format') != 1 or data.get('medium') != str(medium.resolve()) or
            data.get('mount') != str(mount) or recorded_image not in
            (image, image.with_suffix('.ext4.creating')) or
            not re.fullmatch(r'/dev/loop[0-9]+', data.get('loop', ''))):
        raise ValueError('Паспорт не относится к загрузочному хранилищу приложений.')
    safe_path(medium, recorded_image)
    source, _ = medium_info(job, medium, data['live_disk'], cleanup=True)
    if data.get('source') is not None and data['source'] != source:
        raise ValueError('Раздел хранилища изменился; отключение запрещено.')
    loop = data['loop']
    loop_code, actual = job.command(['losetup', '--list', '--noheadings', '--output', 'BACK-FILE',
                             loop], timeout=5, accepted=None, cleanup=True, log_output=False)
    if loop_code != 0:
        raise RuntimeError('Не удалось проверить loop-устройство; паспорт сохранён.')
    attached = bool(actual.strip())
    if attached and str(Path(actual.strip()).resolve()) != str(recorded_image.resolve()):
        raise ValueError('Loop-устройство изменилось; отключение запрещено.')
    code, raw = job.command(['findmnt', '-J', '-R', '-M', str(mount),
                             '-o', 'TARGET,SOURCE,FSTYPE'], timeout=5, accepted=None,
                            cleanup=True, log_output=False)
    if code not in (0, 1):
        raise RuntimeError('Не удалось проверить подключение хранилища; паспорт сохранён.')
    if not attached:
        # Idempotent retry after detach succeeded but state unlink did not.
        if code != 1 or not recorded_image.is_file() or recorded_image.stat().st_dev != medium.stat().st_dev or data.get('source') != source:
            raise ValueError('Не подтверждено отключение хранилища; паспорт сохранён.')
        _, associated = job.command(['losetup', '--associated', str(recorded_image),
                                     '--noheadings', '--output', 'NAME'], timeout=10,
                                    cleanup=True, log_output=False)
        if associated.strip():
            raise ValueError('Образ подключён другим loop-устройством; паспорт сохранён.')
    elif code == 0:
        entries = json.loads(raw)['filesystems']
        if len(entries) != 1 or entries[0]['target'] != str(mount) or entries[0]['source'] != loop or entries[0]['fstype'] != 'ext4':
            raise ValueError('Подключение хранилища изменилось; отключение запрещено.')
        # Stop app sessions before unmounting their namespace bindings.
        service_code, services = job.command(['systemctl', 'list-units', '--all', '--type=service',
                                    '--plain', '--no-legend', 'flashos-app-*.service', 'flashos-store-*.service'],
                                   timeout=10, accepted=None, cleanup=True, log_output=False)
        if service_code != 0:
            if Path('/run/systemd/system').is_dir():
                raise RuntimeError('Не удалось проверить работающие приложения; хранилище сохранено подключённым.')
            # File-backed Docker acceptance has no systemd PID1 or app units.
            services = ''
        units = [line.split()[0] for line in services.splitlines() if line.split()]
        for unit in units:
            if not re.fullmatch(r'flashos-(app|store)-[a-zA-Z0-9-]+\.service', unit):
                raise ValueError('Неожиданная служба среды приложений.')
            job.command(['systemctl', 'stop', unit], timeout=200, cleanup=True)
        targets = []
        def collect(entry):
            for child in entry.get('children', []):
                target = Path(child['target'])
                if not target.is_relative_to(mount) or target == mount:
                    raise ValueError('Подключение выходит за пределы среды приложений.')
                collect(child)
                targets.append(target)
        collect(entries[0])
        for target in targets:
            job.command(['umount', '--', str(target)], timeout=30, cleanup=True)
        job.command(['sync', '-f', str(mount)], timeout=WRITE_TIMEOUT, cleanup=True)
        job.command(['umount', '--', str(mount)], timeout=30, cleanup=True)
    if attached:
        job.command(['losetup', '-d', loop], timeout=20, cleanup=True)
    if data.get('restore_ro') and not writer_count(medium):
        medium_info(job, medium, data['live_disk'], cleanup=True)
        job.command(['mount', '-o', 'remount,ro', str(medium)], timeout=30, cleanup=True)
    state.unlink()


if __name__ == '__main__':
    import argparse
    from jobs import Job
    parser = argparse.ArgumentParser(description='FlashOS app storage shutdown')
    parser.add_argument('--shutdown', action='store_true', required=True)
    parser.add_argument('--medium', default='/run/live/medium')
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Storage cleanup requires root.')
    shutdown(Job('Закрытие хранилища приложений', None), arguments.medium)
