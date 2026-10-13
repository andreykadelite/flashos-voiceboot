"""Proxy secrets travel through RAM files, and never through store argv/reports."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import network_proxy as proxy
from network_profiles import NetworkProfile, ProfileScope, ProxySettings
from store_client import StoreClient, StoreConnectionError


def profile():
    return NetworkProfile(str(uuid.uuid4()), 'Сеть', 'wired', ProfileScope('a'*64, '02:34:56:78:9a:bc'),
        proxy=ProxySettings('manual', 'http://proxy.example:3128', 'http://proxy.example:3128',
                            ('localhost', '.example.org'), 'имя @'), secret_ref=str(uuid.uuid4()))


class ProxyTests(unittest.TestCase):
    def test_stale_or_no_active_profile_cannot_apply_saved_proxy(self):
        with patch('network_runtime.active_profile', return_value=None):
            self.assertEqual(proxy.proxy_environment(Mock()), {})
            proxy.ensure_credentials(Mock())

    def test_authentication_is_escaped_and_requires_unlocked_vault(self):
        ui = SimpleNamespace(network_settings=SimpleNamespace(vault=Mock(unlocked=True)))
        ui.network_settings.vault.get.return_value = {'proxy_password': 'p@ss:/ слово'}
        with patch('network_runtime.active_profile', return_value=profile()):
            env = proxy.proxy_environment(ui)
            self.assertIn('%40', env['http_proxy'])
            self.assertNotIn('p@ss', env['http_proxy'])
            self.assertEqual(env['HTTP_PROXY'], env['http_proxy'])
            ui.network_settings.vault.unlocked = False
            with self.assertRaises(RuntimeError):
                proxy.proxy_environment(ui)

    def test_explicit_empty_proxy_password_differs_from_missing_password(self):
        ui = SimpleNamespace(network_settings=SimpleNamespace(vault=Mock(unlocked=True)))
        ui.network_settings.vault.get.return_value = {'proxy_password': ''}
        with patch('network_runtime.active_profile', return_value=profile()):
            self.assertIn(':@proxy.example:3128', proxy.proxy_environment(ui)['http_proxy'])
            ui.network_settings.vault.get.return_value = {}
            with self.assertRaises(RuntimeError):
                proxy.proxy_environment(ui)

    def test_apt_bypass_and_direct_mode_are_explicit(self):
        config = proxy.apt_configuration({'http_proxy': 'http://proxy.example:3128', 'no_proxy': '.debian.org'})
        self.assertIn('Acquire::http::Proxy "http://proxy.example:3128";', config)
        self.assertIn('Acquire::http::Proxy::deb.debian.org "DIRECT";', config)
        self.assertIn('Acquire::https::Proxy "DIRECT";', config)
        self.assertTrue(proxy.bypasses('192.0.2.8', ('192.0.2.0/24',)))
        self.assertFalse(proxy.bypasses('evildebian.org', ('.debian.org',)))

    def test_redaction_hides_encoded_raw_password_and_url_userinfo(self):
        env = {'http_proxy': 'http://user:p%40ss@proxy.example:3128'}
        result = proxy.redact_proxy('p@ss p%40ss http://user:p%40ss@proxy.example:3128', env)
        self.assertNotIn('p@ss', result)
        self.assertNotIn('p%40ss', result)
        self.assertNotIn('user:', result)

    @unittest.skipUnless(os.name == 'posix', 'RAM ownership and Linux modes')
    def test_ram_configuration_is_private_and_removed_after_failure(self):
        path = None
        with patch('network_proxy.proxy_environment', return_value={'http_proxy': 'http://user:password@proxy.example:3128'}):
            with self.assertRaisesRegex(RuntimeError, 'failure'):
                with proxy.apt_proxy(Mock()) as (directory, environment):
                    path = directory / 'apt.conf'
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                    self.assertIn('password', path.read_text())
                    raise RuntimeError('failure')
        self.assertFalse(path.exists())

    @unittest.skipUnless(os.name == 'posix', 'Linux uid and permissions')
    def test_application_handoff_checks_shape_and_file_type(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            path = runtime / 'network-proxy.json'
            with patch('network_proxy.proxy_environment', return_value={'http_proxy': 'http://user:pass@proxy.example:3128'}):
                proxy.write_session_proxy(Mock(), runtime)
            self.assertEqual(proxy.read_session_proxy(runtime)['http_proxy'], 'http://user:pass@proxy.example:3128')
            path.chmod(0o644)
            with self.assertRaises(RuntimeError):
                proxy.read_session_proxy(runtime)
            path.chmod(0o600)
            path.write_text('{"LD_PRELOAD":"/bad"}')
            with self.assertRaises(RuntimeError):
                proxy.read_session_proxy(runtime)
            path.unlink()
            os.mkfifo(path, 0o600)
            os.chown(path, 1000, 1000)
            with self.assertRaises(RuntimeError):
                proxy.read_session_proxy(runtime)

    def test_store_mounts_proxy_file_without_credentials_in_argv_or_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            environment = Mock(root=Path(folder))
            environment.lock.return_value.__enter__ = Mock()
            environment.lock.return_value.__exit__ = Mock(return_value=False)
            env = {'http_proxy': 'http://user:p%40ss@proxy.example:3128'}
            @contextmanager
            def handoff(ui):
                yield Path('/run/flashos-proxy-test'), env
            job = Mock()
            def command(argv, **options):
                self.assertNotIn('p%40ss', ' '.join(argv))
                self.assertIn('--setenv=APT_CONFIG=/run/flashos-proxy/apt.conf', argv)
                self.assertIn('--property=BindReadOnlyPaths=/run/flashos-proxy-test:/run/flashos-proxy', argv)
                self.assertFalse(options['log_output'])
                options['on_line']('Failed http://user:p%40ss@proxy.example:3128 p@ss')
                return 0, json.dumps({'format': 1, 'ok': False, 'error_kind': 'network', 'retry_safe': True,
                                     'error': 'Cannot reach http://user:p%40ss@proxy.example:3128 p@ss'})
            job.command.side_effect = command
            with patch('network_proxy.apt_proxy', handoff):
                with self.assertRaises(StoreConnectionError) as raised:
                    StoreClient(environment, Mock()).call(job, 'refresh')
            self.assertNotIn('p@ss', str(raised.exception))
            self.assertNotIn('p%40ss', str(job.log.call_args_list))

    def test_backend_redacts_long_credentials_before_truncation(self):
        import contextlib
        import io
        import store_packages
        password = 'long-secret-' * 200
        environment = {'http_proxy': 'http://user:' + password + '@proxy.example:3128'}
        output = 'E: Failed to fetch http://user:' + password + '@proxy.example:3128 Connection timed out\n'
        child = SimpleNamespace(stdout=io.StringIO(output), wait=Mock(return_value=100))
        shown = io.StringIO()
        with patch.dict(os.environ, {'APT_CONFIG': '/run/flashos-proxy/apt.conf'}), \
             patch('network_proxy.read_apt_proxy', return_value=environment), \
             patch('store_packages.subprocess.Popen', return_value=child), contextlib.redirect_stderr(shown):
            with self.assertRaises(store_packages.StoreNetworkError) as raised:
                store_packages.command(['apt-get', 'update'], retry_safe=True)
        self.assertNotIn('long-secret', shown.getvalue())
        self.assertNotIn('long-secret', str(raised.exception))

    @unittest.skipUnless(os.name == 'posix', 'Linux uid and permissions')
    def test_long_unicode_password_roundtrips_through_application_ram(self):
        from urllib.parse import quote
        url = 'http://user:' + quote('🔑' * 256, safe='') + '@proxy.example:3128'
        environment = {name: url for name in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY')}
        with tempfile.TemporaryDirectory() as folder, patch('network_proxy.proxy_environment', return_value=environment):
            runtime = Path(folder)
            proxy.write_session_proxy(Mock(), runtime)
            self.assertGreater((runtime / 'network-proxy.json').stat().st_size, 8192)
            self.assertEqual(proxy.read_session_proxy(runtime), environment)


if __name__ == '__main__':
    unittest.main()
