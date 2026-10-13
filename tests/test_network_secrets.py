import errno
from pathlib import Path
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import network_secrets as secrets

try:
    secrets._crypto()
    CRYPTO_AVAILABLE = True
except secrets.VaultUnavailable:
    CRYPTO_AVAILABLE = False


@unittest.skipUnless(CRYPTO_AVAILABLE, 'python3-cryptography is required for authenticated vault tests')
class NetworkSecretVault(unittest.TestCase):
    password = 'portable vault password'
    wifi = 'private-wifi-password-never-persist-plain'
    proxy = 'private-proxy-password-never-persist-plain'

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.vault = secrets.Vault(self.root)
        self.profile = str(uuid.uuid4())
        self.other = str(uuid.uuid4())

    def create(self):
        self.vault.create(self.password)
        return self.vault

    def assert_no_plaintext(self):
        for path in self.root.rglob('*'):
            if path.is_file():
                raw = path.read_bytes()
                for value in (self.password, self.wifi, self.proxy):
                    self.assertNotIn(value.encode(), raw, path.name)

    def test_separate_instance_unlocks_persisted_wifi_and_proxy_credentials(self):
        self.create().set(self.profile, {'wifi_password': self.wifi, 'proxy_password': self.proxy})
        self.vault.lock()
        reopened = secrets.Vault(self.root)
        self.assertTrue(reopened.exists)
        self.assertFalse(reopened.unlocked)
        reopened.unlock(self.password)
        self.assertEqual(reopened.get(self.profile), {'wifi_password': self.wifi, 'proxy_password': self.proxy})
        self.assert_no_plaintext()

    def test_wifi_and_proxy_can_be_updated_independently_and_get_is_a_copy(self):
        self.create().set(self.profile, {'wifi_password': self.wifi})
        self.vault.set(self.profile, {'proxy_password': self.proxy})
        found = self.vault.get(self.profile)
        found['wifi_password'] = 'changed outside vault'
        self.assertEqual(self.vault.get(self.profile)['wifi_password'], self.wifi)
        self.assertEqual(self.vault.get(self.profile)['proxy_password'], self.proxy)

    def test_wrong_password_leaves_locked_and_keeps_encrypted_file(self):
        self.create().set(self.profile, {'wifi_password': self.wifi})
        before = self.vault.path.read_bytes()
        with self.assertRaises(secrets.VaultAuthenticationError):
            self.vault.unlock('wrong vault password')
        self.assertFalse(self.vault.unlocked)
        self.assertEqual(self.vault.path.read_bytes(), before)
        with self.assertRaises(secrets.LockedVault):
            self.vault.get(self.profile)

    def test_modified_ciphertext_is_rejected_without_replacing_or_deleting_it(self):
        self.create().set(self.profile, {'wifi_password': self.wifi})
        altered = bytearray(self.vault.path.read_bytes())
        altered[-1] ^= 1
        self.vault.path.write_bytes(altered)
        with self.assertRaises(secrets.VaultAuthenticationError):
            self.vault.unlock(self.password)
        self.assertEqual(self.vault.path.read_bytes(), altered)
        self.assertFalse(self.vault.unlocked)

    def test_header_salt_is_authenticated_and_unknown_version_rejected_before_kdf(self):
        self.create()
        original = self.vault.path.read_bytes()
        altered = bytearray(original)
        altered[10] ^= 1
        self.vault.path.write_bytes(altered)
        with self.assertRaises(secrets.VaultAuthenticationError):
            self.vault.unlock(self.password)
        altered = bytearray(original)
        altered[8] = 99
        self.vault.path.write_bytes(altered)
        with patch.object(secrets.hashlib, 'scrypt') as derive:
            with self.assertRaises(secrets.CorruptVault):
                self.vault.unlock(self.password)
        derive.assert_not_called()

    def test_password_change_keeps_credentials_and_invalidates_old_password(self):
        self.create().set(self.profile, {'wifi_password': self.wifi, 'proxy_password': self.proxy})
        self.vault.change_password('new common vault password')
        with self.assertRaises(secrets.VaultAuthenticationError):
            self.vault.unlock(self.password)
        self.vault.unlock('new common vault password')
        self.assertEqual(self.vault.get(self.profile)['proxy_password'], self.proxy)
        self.assert_no_plaintext()

    def test_password_change_requires_existing_unlock(self):
        with self.assertRaises(secrets.LockedVault):
            self.vault.change_password('new common vault password')

    def test_create_cannot_reset_existing_vault_even_when_locked(self):
        self.create().set(self.profile, {'wifi_password': self.wifi})
        before = self.vault.path.read_bytes()
        self.vault.lock()
        with self.assertRaises(secrets.VaultError):
            self.vault.create('another common vault password')
        self.assertEqual(self.vault.path.read_bytes(), before)
        self.vault.unlock(self.password)
        self.assertEqual(self.vault.get(self.profile)['wifi_password'], self.wifi)

    def test_profile_deletion_preserves_other_credentials(self):
        self.create().set(self.profile, {'wifi_password': self.wifi})
        self.vault.set(self.other, {'proxy_password': self.proxy})
        self.assertTrue(self.vault.delete(self.profile))
        self.assertFalse(self.vault.delete(self.profile))
        self.vault.lock()
        self.vault.unlock(self.password)
        self.assertEqual(self.vault.get(self.profile), {})
        self.assertEqual(self.vault.get(self.other), {'proxy_password': self.proxy})

    def test_lock_overwrites_owned_key_and_clears_plaintext_cache(self):
        self.create().set(self.profile, {'wifi_password': self.wifi})
        owned_key = self.vault._key
        self.vault.lock()
        self.assertEqual(bytes(owned_key), b'\0' * 32)
        self.assertEqual(self.vault._data, {})
        self.assertFalse(self.vault.unlocked)

    def test_each_save_uses_new_ciphertext_without_plaintext_temporary_files(self):
        self.create().set(self.profile, {'wifi_password': self.wifi})
        before = self.vault.path.read_bytes()
        seen = []
        original = secrets._Folder.replace
        def observe(folder, source, target):
            raw = (folder.path / source).read_bytes()
            seen.append(raw)
            self.assertNotIn(self.wifi.encode(), raw)
            self.assertNotIn(self.password.encode(), raw)
            return original(folder, source, target)
        with patch.object(secrets._Folder, 'replace', observe):
            self.vault.set(self.profile, {'wifi_password': self.wifi})
        self.assertNotEqual(before, self.vault.path.read_bytes())
        self.assertEqual(len(seen), 1)
        self.assertEqual([path.name for path in self.vault.path.parent.iterdir()], ['secrets.bin'])

    def test_failed_atomic_replace_preserves_previous_credentials_and_removes_ciphertext_temp(self):
        self.create().set(self.profile, {'wifi_password': self.wifi})
        before = self.vault.path.read_bytes()
        with patch.object(secrets._Folder, 'replace', side_effect=OSError(errno.ENOSPC, 'Full')):
            with self.assertRaises(OSError):
                self.vault.set(self.profile, {'proxy_password': self.proxy})
        self.assertEqual(self.vault.path.read_bytes(), before)
        self.assertEqual(self.vault.get(self.profile), {'wifi_password': self.wifi})
        self.assertEqual([path.name for path in self.vault.path.parent.iterdir()], ['secrets.bin'])
        self.assert_no_plaintext()

    def test_initial_creation_never_reserves_an_empty_destination_before_replace(self):
        def interrupted(folder, source, target):
            self.assertFalse((folder.path / target).exists())
            self.assertGreater((folder.path / source).stat().st_size, secrets.HEADER.size)
            raise OSError(errno.EIO, 'Interrupted initial encrypted replacement')
        with patch.object(secrets._Folder, 'replace', interrupted):
            with self.assertRaises(OSError):
                self.vault.create(self.password)
        self.assertFalse(self.vault.path.exists())
        self.assertFalse(self.vault.unlocked)
        self.assertFalse(self.vault.exists)
        self.assertEqual(list(self.vault.path.parent.iterdir()), [])
        self.create()
        self.vault.lock()
        self.vault.unlock(self.password)

    def test_failed_directory_flush_reports_committed_encrypted_file_and_keeps_memory_consistent(self):
        self.create()
        with patch.object(secrets._Folder, 'sync', side_effect=OSError(errno.EIO, 'Flush failure')):
            with self.assertRaises(secrets.VaultDurabilityError):
                self.vault.set(self.profile, {'wifi_password': self.wifi})
        self.assertEqual(self.vault.get(self.profile)['wifi_password'], self.wifi)
        self.vault.lock()
        self.vault.unlock(self.password)
        self.assertEqual(self.vault.get(self.profile)['wifi_password'], self.wifi)
        self.assert_no_plaintext()

    def test_concurrent_instance_cannot_overwrite_a_newer_vault(self):
        self.create()
        older = secrets.Vault(self.root).unlock(self.password)
        self.vault.set(self.profile, {'wifi_password': self.wifi})
        with self.assertRaises(secrets.VaultConflictError):
            older.set(self.other, {'proxy_password': self.proxy})
        self.vault.lock()
        self.vault.unlock(self.password)
        self.assertEqual(self.vault.get(self.profile)['wifi_password'], self.wifi)
        self.assertEqual(self.vault.get(self.other), {})

    def test_truncated_and_oversized_files_are_rejected_before_password_derivation(self):
        self.create()
        for raw in (b'truncated', b'x' * (secrets.MAX_FILE + 1)):
            self.vault.path.write_bytes(raw)
            with patch.object(secrets.hashlib, 'scrypt') as derive:
                with self.assertRaises(secrets.CorruptVault):
                    self.vault.unlock(self.password)
            derive.assert_not_called()

    def test_password_and_secret_inputs_are_bounded_without_echoing_the_value(self):
        for password in ('', 'short', 'x' * (secrets.MAX_PASSWORD_BYTES + 1)):
            with self.assertRaises(secrets.VaultError):
                self.vault.create(password)
        self.create()
        for reference in ('../outside', self.profile.upper(), self.profile.replace('-', ''), str(uuid.UUID(int=0))):
            with self.assertRaises(secrets.VaultError):
                self.vault.set(reference, {'wifi_password': self.wifi})
        for record in ({'private_key': self.wifi}, {'wifi_password': b'bytes'},
                       {'proxy_password': 'x' * (secrets.MAX_SECRET_BYTES + 1)}):
            with self.assertRaises(secrets.VaultError) as caught:
                self.vault.set(self.profile, record)
            self.assertNotIn(self.wifi, str(caught.exception))

    @unittest.skipUnless(os.name == 'posix', 'POSIX directory descriptors and file modes')
    def test_linux_vault_file_has_0600_permissions(self):
        self.create()
        self.assertEqual(stat.S_IMODE(self.vault.path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink fixtures')
    def test_vault_file_symlink_and_parent_symlink_cannot_access_other_files(self):
        self.create()
        original = self.vault.path.read_bytes()
        target = self.root / 'other-encrypted-file'
        target.write_bytes(original)
        self.vault.path.unlink()
        self.vault.path.symlink_to(target)
        with self.assertRaises(secrets.CorruptVault):
            self.vault.unlock(self.password)
        self.assertEqual(target.read_bytes(), original)
        self.vault.path.unlink()
        self.vault.path.parent.rmdir()
        outside = self.root / 'outside'
        outside.mkdir()
        self.vault.path.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(secrets.CorruptVault):
            self.vault.create(self.password)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink fixtures')
    def test_symlink_ancestor_of_state_root_cannot_redirect_vault_creation(self):
        actual = self.root / 'actual'
        nested = actual / 'nested'
        nested.mkdir(parents=True)
        link = self.root / 'redirect'
        link.symlink_to(actual, target_is_directory=True)
        vault = secrets.Vault(link / 'nested')
        with self.assertRaises(secrets.CorruptVault):
            vault.create(self.password)
        self.assertEqual(list(nested.iterdir()), [])

    @unittest.skipUnless(os.name == 'posix', 'POSIX hard link fixtures')
    def test_hard_linked_vault_is_rejected(self):
        self.create()
        os.link(self.vault.path, self.root / 'other-hard-link')
        with self.assertRaises(secrets.CorruptVault):
            self.vault.unlock(self.password)

    @unittest.skipUnless(os.name == 'posix', 'POSIX FIFO fixtures')
    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        self.vault.path.parent.mkdir(parents=True)
        os.mkfifo(self.vault.path)
        with self.assertRaises(secrets.CorruptVault):
            self.vault.unlock(self.password)


class NetworkSecretDependency(unittest.TestCase):
    def test_absent_crypto_fails_explicitly_without_plaintext_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = secrets.Vault(directory)
            with patch.object(secrets, '_crypto', side_effect=secrets.VaultUnavailable('Crypto unavailable')):
                with self.assertRaises(secrets.VaultUnavailable):
                    vault.create('a common vault password')
            self.assertFalse(vault.exists)
            self.assertEqual(list(Path(directory).rglob('*')), [])
