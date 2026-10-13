"""Saving portable profiles must not cross PC, credential or cancellation boundaries."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from network_profiles import NetworkProfile, ProfileScope, WifiSettings, IPSettings, DurabilityError
from network_settings import NetworkSettings, edit_ip, edit_proxy
from interface import UI, OpenPower
import curses

PC, MAC = 'a' * 64, '02:34:56:78:9a:bc'


def profile(**values):
    return NetworkProfile(str(uuid.uuid4()), 'Дом', 'wired', ProfileScope(PC, MAC), **values)


class SettingsTests(unittest.TestCase):
    def settings(self, folder):
        ui = Mock(state_root=Path(folder), demo=False)
        settings = NetworkSettings(ui)
        settings.vault = Mock(unlocked=True)
        return settings

    def test_copy_changes_credentials_without_modifying_original(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = self.settings(folder)
            original = profile(secret_ref=str(uuid.uuid4()))
            copy = replace(original, id=str(uuid.uuid4()), name='Копия')
            settings.store.save([original, copy])
            settings.vault.get.side_effect = lambda ref: {'wifi_password': 'old-wifi', 'proxy_password': 'old-proxy'} if ref == original.secret_ref else {}
            settings.save(copy, {'wifi_password': 'new-wifi'})
            saved = {p.id: p for p in settings.store.load()}
            self.assertEqual(saved[original.id], original)
            self.assertNotEqual(saved[copy.id].secret_ref, original.secret_ref)
            settings.vault.set.assert_called_once_with(saved[copy.id].secret_ref,
                {'wifi_password': 'new-wifi', 'proxy_password': 'old-proxy'})

    def test_one_autoconnect_per_adapter_preserves_other_pc(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = self.settings(folder)
            old = profile(autoconnect=True)
            other = replace(old, id=str(uuid.uuid4()), scope=ProfileScope('b'*64, MAC))
            new = profile(autoconnect=True)
            settings.store.save([old, other])
            settings.save(new, {})
            values = {p.id: p for p in settings.store.load()}
            self.assertFalse(values[old.id].autoconnect)
            self.assertTrue(values[other.id].autoconnect)
            self.assertTrue(values[new.id].autoconnect)

    def test_failed_profile_write_restores_password_and_original_error(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = self.settings(folder)
            current = profile(secret_ref=str(uuid.uuid4()))
            settings.store.save([current])
            settings.vault.get.return_value = {'proxy_password': 'old'}
            with patch.object(settings.store, 'save', side_effect=OSError('disk full')) as save:
                with self.assertRaisesRegex(OSError, 'disk full'):
                    settings.save(current, {'proxy_password': 'new'})
                save.assert_called_once()
            new_ref = settings.vault.set.call_args.args[0]
            self.assertNotEqual(new_ref, current.secret_ref)
            settings.vault.delete.assert_called_once_with(new_ref)
            settings.vault.set.assert_called_once_with(new_ref, {'proxy_password': 'new'})
            self.assertEqual(settings.store.load(), (current,))

    def test_postcommit_sync_failure_keeps_both_credential_generations(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = self.settings(folder)
            current = profile(secret_ref=str(uuid.uuid4()))
            settings.store.save([current])
            settings.vault.get.return_value = {'proxy_password': 'old'}
            with patch.object(settings.store, 'save', side_effect=DurabilityError('sync failed')):
                with self.assertRaises(DurabilityError):
                    settings.save(current, {'proxy_password': 'new'})
            self.assertNotEqual(settings.vault.set.call_args.args[0], current.secret_ref)
            settings.vault.delete.assert_not_called()

    def test_vault_sync_failure_does_not_commit_profile_or_delete_old_secret(self):
        from network_secrets import VaultDurabilityError
        with tempfile.TemporaryDirectory() as folder:
            settings = self.settings(folder)
            current = profile(secret_ref=str(uuid.uuid4()))
            settings.store.save([current])
            settings.vault.get.return_value = {'wifi_password': 'unchanged'}
            settings.vault.set.side_effect = VaultDurabilityError('sync failed')
            with self.assertRaises(VaultDurabilityError):
                settings.save(current, {'proxy_password': 'new'})
            self.assertEqual(settings.store.load(), (current,))
            settings.vault.delete.assert_not_called()

    def test_cancelled_vault_does_not_write_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = self.settings(folder)
            with patch.object(settings, 'unlock', return_value=False):
                with self.assertRaises(RuntimeError):
                    settings.save(profile(), {'proxy_password': 'secret'})
            self.assertEqual(settings.store.load(), ())
            settings.vault.set.assert_not_called()

    def test_cancelled_connection_confirmation_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = self.settings(folder)
            current = profile()
            transaction = Mock(pending=True)
            settings.ui.run_job.return_value = SimpleNamespace(failed=False, cancelled=False, payload=transaction)
            settings.ui.confirm_timed.return_value = False
            with patch.object(settings, 'choose_device', return_value='enp0s1'), patch('network_settings.machine_id', return_value=PC), patch('network_settings.device_id', return_value=MAC):
                settings.connect(current)
            transaction.confirm.assert_not_called()
            transaction.rollback.assert_called_once()

    def test_power_menu_during_confirmation_also_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = self.settings(folder)
            transaction = Mock(pending=True)
            settings.ui.run_job.return_value = SimpleNamespace(failed=False, cancelled=False, payload=transaction)
            settings.ui.confirm_timed.side_effect = OpenPower
            with patch.object(settings, 'choose_device', return_value='enp0s1'), patch('network_settings.machine_id', return_value=PC), patch('network_settings.device_id', return_value=MAC):
                with self.assertRaises(OpenPower):
                    settings.connect(profile())
            transaction.rollback.assert_called_once()

    def test_cancel_any_ip_wizard_step_preserves_original(self):
        original = IPSettings('manual', ('192.0.2.5/24',), '192.0.2.1', 'manual', ('1.1.1.1',))
        ui = Mock()
        ui.choose.side_effect = ['manual']
        ui.text_input.return_value = None
        self.assertIs(edit_ip(ui, original, 4), original)
        ui.choose.side_effect = ['auto', None]
        self.assertIs(edit_ip(ui, original, 4), original)

    def test_disable_family_clears_all_old_addresses_dns_routes(self):
        ui = Mock()
        ui.choose.return_value = 'disabled'
        self.assertEqual(edit_ip(ui, IPSettings('manual', ('192.0.2.5/24',)), 4), IPSettings(method='disabled'))

    def test_proxy_wizard_cancellation_preserves_original(self):
        ui = Mock()
        ui.choose.return_value = 'manual'
        ui.text_input.side_effect = ['http://proxy.example:3128', None]
        from network_profiles import ProxySettings
        original = ProxySettings()
        self.assertIs(edit_proxy(ui, original), original)


class TimedConfirmationTests(unittest.TestCase):
    def ui(self, keys):
        ui = object.__new__(UI)
        from collections import deque
        ui.pending_keys = deque()
        ui.screen, ui.speech, ui.draw, ui.global_key = Mock(), Mock(), Mock(), Mock()
        ui.screen.getch.side_effect = keys
        return ui

    def test_default_enter_cancels_and_explicit_selection_accepts(self):
        self.assertFalse(self.ui([10]).confirm_timed('Сеть', 30))
        self.assertTrue(self.ui([curses.KEY_DOWN, 10]).confirm_timed('Сеть', 30))

    def test_escape_and_expiration_cancel(self):
        self.assertFalse(self.ui([27]).confirm_timed('Сеть', 30))
        ui = self.ui([])
        with patch('interface.time.monotonic', side_effect=[100, 131]):
            self.assertFalse(ui.confirm_timed('Сеть', 30))
        ui.screen.getch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
