"""Password-unlocked network credentials; only authenticated ciphertext is persisted.

The live Linux root and credential cache use RAM. FAT permissions do not protect
the USB: confidentiality and integrity come from AES-256-GCM and a salted scrypt
key. Locking clears owned caches; Python cannot erase copies held by callers.
"""
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import threading
import uuid

MAGIC = b'FOSNETV\0'
VERSION = 1
KDF = 1  # Versioned, fixed-cost scrypt; file contents cannot increase its cost.
HEADER = struct.Struct('>8sBB16s12sI')
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**17, 8, 1
MAX_PASSWORD_BYTES = 1024
MAX_SECRET_BYTES = 4096
MAX_PROFILES = 128
MAX_PLAINTEXT = 1024 * 1024
MAX_FILE = HEADER.size + MAX_PLAINTEXT + 16
SECRET_FIELDS = frozenset(('wifi_password', 'proxy_password'))


class VaultError(RuntimeError):
    pass


class LockedVault(VaultError):
    pass


class VaultAuthenticationError(VaultError):
    pass


class CorruptVault(VaultError):
    pass


class VaultUnavailable(VaultError):
    pass


class VaultConflictError(VaultError):
    pass


class VaultDurabilityError(VaultError):
    """The encrypted replacement exists, but the directory flush was not confirmed."""


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.exceptions import InvalidTag
    except ImportError:
        raise VaultUnavailable('Для защищённого хранилища нужен компонент python3-cryptography.') from None
    return AESGCM, InvalidTag


def _password(value):
    if not isinstance(value, str) or len(value) < 8:
        raise VaultError('Пароль хранилища должен содержать не менее 8 символов.')
    try:
        encoded = value.encode('utf-8')
    except UnicodeError:
        raise VaultError('Пароль хранилища содержит недопустимые символы.') from None
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise VaultError('Пароль хранилища слишком длинный.')
    return encoded


def _key(password, salt):
    return bytearray(hashlib.scrypt(_password(password), salt=salt, n=SCRYPT_N,
                                   r=SCRYPT_R, p=SCRYPT_P, maxmem=256 * 1024**2, dklen=32))


def _reference(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise VaultError('Некорректный идентификатор сетевого профиля.') from None
    return value


def _record(value):
    if not isinstance(value, dict) or not value.keys() <= SECRET_FIELDS:
        raise VaultError('В хранилище разрешены только пароли Wi-Fi и прокси.')
    result = {}
    for name, secret in value.items():
        try:
            if not isinstance(secret, str) or len(secret.encode('utf-8')) > MAX_SECRET_BYTES:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise VaultError('Некорректное или слишком длинное значение сетевого пароля.') from None
        result[name] = secret
    return result


def _records(value):
    if not isinstance(value, dict) or len(value) > MAX_PROFILES:
        raise CorruptVault('Некорректное содержимое защищённого хранилища.')
    try:
        return {_reference(name): _record(record) for name, record in value.items()}
    except VaultError:
        raise CorruptVault('Некорректное содержимое защищённого хранилища.') from None


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('Duplicate object key')
        result[name] = value
    return result


class _Folder:
    """Keep Linux writes relative to opened, non-symlink directory descriptors."""
    def __init__(self, path, fd=None):
        self.path, self.fd = path, fd

    def open(self, name, flags, mode=0o600):
        flags |= getattr(os, 'O_BINARY', 0)
        if self.fd is not None:
            return os.open(name, flags, mode, dir_fd=self.fd)
        target = self.path / name
        if target.is_symlink():
            raise CorruptVault('Ссылки вместо файлов хранилища запрещены.')
        return os.open(target, flags, mode)

    def replace(self, source, target):
        if self.fd is not None:
            os.replace(source, target, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        else:
            os.replace(self.path / source, self.path / target)

    def unlink(self, name):
        if self.fd is not None:
            os.unlink(name, dir_fd=self.fd)
        else:
            (self.path / name).unlink()

    def sync(self):
        if self.fd is not None:
            os.fsync(self.fd)


class Vault:
    def __init__(self, state_root):
        self.state_root = Path(state_root).absolute()
        self.path = self.state_root / 'flashos/network/secrets.bin'
        self._key = None
        self._salt = None
        self._digest = None
        self._data = {}
        self._mutex = threading.RLock()

    @property
    def unlocked(self):
        return self._key is not None

    @property
    def exists(self):
        try:
            with self._directory() as folder:
                return self._read(folder, missing=True) is not None
        except FileNotFoundError:
            return False

    @contextmanager
    def _directory(self, create=False):
        descriptor = None
        try:
            def linked(path):
                return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())
            if any(linked(path) for path in (self.state_root, *self.state_root.parents)) or not self.state_root.is_dir():
                raise CorruptVault('Служебный раздел хранилища недоступен или заменён ссылкой.')
            if os.name == 'posix':
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
                descriptor = os.open(self.state_root, flags)
                for name in ('flashos', 'network'):
                    if create:
                        try:
                            os.mkdir(name, 0o700, dir_fd=descriptor)
                        except FileExistsError:
                            pass
                    next_descriptor = os.open(name, flags, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = next_descriptor
                # Cooperating vault instances cannot race creation or overwrite one another.
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield _Folder(self.path.parent, descriptor)
            else:
                current = self.state_root
                for name in ('flashos', 'network'):
                    current /= name
                    if create:
                        current.mkdir(mode=0o700, exist_ok=True)
                    if current.is_symlink():
                        raise CorruptVault('Ссылки вместо папок хранилища запрещены.')
                    if not current.is_dir():
                        raise FileNotFoundError()
                yield _Folder(current)
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.ENOTDIR):
                raise CorruptVault('Ссылки и посторонние файлы вместо хранилища запрещены.') from None
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _read(self, folder, missing=False):
        try:
            descriptor = folder.open('secrets.bin', os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        except FileNotFoundError:
            if missing:
                return None
            raise VaultError('Защищённое хранилище ещё не создано.') from None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not HEADER.size + 16 <= info.st_size <= MAX_FILE:
                raise CorruptVault('Файл защищённого хранилища повреждён или имеет недопустимый размер.')
            data = bytearray()
            while len(data) <= MAX_FILE:
                chunk = os.read(descriptor, min(65536, MAX_FILE + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) != info.st_size or len(data) > MAX_FILE:
                raise CorruptVault('Файл защищённого хранилища изменился во время чтения.')
            return bytes(data)
        finally:
            os.close(descriptor)

    def _require_unlocked(self):
        if not self.unlocked:
            raise LockedVault('Сначала разблокируйте защищённое хранилище общим паролем.')

    def lock(self):
        with self._mutex:
            if self._key is not None:
                self._key[:] = b'\0' * len(self._key)
            self._key, self._salt, self._digest = None, None, None
            self._data.clear()

    def _activate(self, data, key, salt, digest):
        self.lock()
        self._data = data
        self._key, self._salt, self._digest = key, salt, digest

    def create(self, password):
        with self._mutex:
            AESGCM, _ = _crypto()
            salt = os.urandom(16)
            key = _key(password, salt)
            try:
                self._save({}, key, salt, create=True, cipher=AESGCM)
            finally:
                if self._key is not key:
                    key[:] = b'\0' * len(key)
            return self

    def unlock(self, password):
        with self._mutex:
            self.lock()
            AESGCM, InvalidTag = _crypto()
            _password(password)
            with self._directory() as folder:
                blob = self._read(folder)
            header = blob[:HEADER.size]
            magic, version, kdf, salt, nonce, length = HEADER.unpack(header)
            if (magic != MAGIC or version != VERSION or kdf != KDF or
                    length != len(blob) - HEADER.size or not 16 <= length <= MAX_PLAINTEXT + 16):
                raise CorruptVault('Неизвестный формат или повреждённый заголовок защищённого хранилища.')
            key = _key(password, salt)
            try:
                try:
                    plain = AESGCM(bytes(key)).decrypt(nonce, blob[HEADER.size:], header)
                except InvalidTag:
                    raise VaultAuthenticationError('Неверный пароль или повреждённое защищённое хранилище.') from None
                try:
                    payload = json.loads(plain, object_pairs_hook=_unique_object)
                    if not isinstance(payload, dict) or set(payload) != {'format', 'profiles'} or type(payload['format']) is not int or payload['format'] != VERSION:
                        raise ValueError()
                    data = _records(payload['profiles'])
                except (ValueError, UnicodeError, KeyError, TypeError, RecursionError):
                    raise CorruptVault('Некорректное содержимое защищённого хранилища.') from None
                self._activate(data, key, salt, hashlib.sha256(blob).digest())
            finally:
                if self._key is not key:
                    key[:] = b'\0' * len(key)
            return self

    def get(self, profile_id):
        with self._mutex:
            self._require_unlocked()
            return dict(self._data.get(_reference(profile_id), {}))

    def set(self, profile_id, values):
        with self._mutex:
            self._require_unlocked()
            profile_id, values = _reference(profile_id), _record(values)
            data = {name: dict(record) for name, record in self._data.items()}
            data[profile_id] = {**data.get(profile_id, {}), **values}
            if len(data) > MAX_PROFILES:
                raise VaultError('Достигнуто максимальное число сохранённых сетевых паролей.')
            key = bytearray(self._key)
            try:
                self._save(data, key, self._salt)
            finally:
                if self._key is not key:
                    key[:] = b'\0' * len(key)
            return dict(data[profile_id])

    def delete(self, profile_id):
        with self._mutex:
            self._require_unlocked()
            profile_id = _reference(profile_id)
            if profile_id not in self._data:
                return False
            data = {name: dict(record) for name, record in self._data.items() if name != profile_id}
            key = bytearray(self._key)
            try:
                self._save(data, key, self._salt)
            finally:
                if self._key is not key:
                    key[:] = b'\0' * len(key)
            return True

    def change_password(self, new_password):
        with self._mutex:
            self._require_unlocked()
            salt = os.urandom(16)
            key = _key(new_password, salt)
            try:
                self._save({name: dict(record) for name, record in self._data.items()}, key, salt)
            finally:
                if self._key is not key:
                    key[:] = b'\0' * len(key)

    def _save(self, data, key, salt, create=False, cipher=None):
        if cipher is None:
            cipher, _ = _crypto()
        plain = json.dumps({'format': VERSION, 'profiles': data}, ensure_ascii=False, separators=(',', ':')).encode()
        if len(plain) > MAX_PLAINTEXT:
            raise VaultError('Защищённое хранилище превышает допустимый размер.')
        nonce = os.urandom(12)
        header = HEADER.pack(MAGIC, VERSION, KDF, salt, nonce, len(plain) + 16)
        blob = header + cipher(bytes(key)).encrypt(nonce, plain, header)
        digest = hashlib.sha256(blob).digest()
        temporary = '.secrets-' + uuid.uuid4().hex + '.tmp'
        with self._directory(create=True) as folder:
            current = self._read(folder, missing=True)
            if create:
                if current is not None:
                    raise VaultError('Хранилище уже существует. Разблокируйте его; сброс требует отдельного подтверждения.')
            elif current is None or hashlib.sha256(current).digest() != self._digest:
                raise VaultConflictError('Хранилище изменилось. Заблокируйте его и разблокируйте снова перед сохранением.')
            try:
                descriptor = folder.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0))
                try:
                    pending = memoryview(blob)
                    while pending:
                        written = os.write(descriptor, pending)
                        if not written:
                            raise OSError('Encrypted vault write did not progress')
                        pending = pending[written:]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                # The directory lock covers the existence check and replacement.
                # Never reserve an empty destination: a power loss must leave only
                # the old authenticated file or the fully written new ciphertext.
                folder.replace(temporary, 'secrets.bin')
                self._activate(data, key, salt, digest)
                try:
                    folder.sync()
                except OSError:
                    raise VaultDurabilityError('Зашифрованный файл записан, но подтверждение сохранения на носителе не получено.') from None
            finally:
                try:
                    folder.unlink(temporary)
                except FileNotFoundError:
                    pass


NetworkSecrets = Vault
