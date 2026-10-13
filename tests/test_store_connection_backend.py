"""APT transport recovery respects cancellation, integrity errors and transaction boundaries."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
spec = importlib.util.spec_from_file_location(
    'store_connection_packages', Path(__file__).resolve().parents[1] / 'src/store_packages.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


def child(output, code=100):
    return SimpleNamespace(stdout=io.StringIO(output), wait=mock.Mock(return_value=code))


class StoreConnectionBackendTests(unittest.TestCase):
    def setUp(self):
        self.cancelled = mock.patch.object(store, 'CANCELLED', False)
        self.cancelled.start()
        self.addCleanup(self.cancelled.stop)

    def test_explicit_apt_transport_errors_are_typed_and_use_c_locale(self):
        for detail in (
            "E: Failed to fetch https://deb.debian.org/debian/pool/editor.deb Temporary failure resolving 'deb.debian.org'",
            "E: Could not resolve 'security.debian.org'",
            'E: Could not connect to deb.debian.org:443 (192.0.2.1), connection timed out',
            'E: Unable to connect to deb.debian.org:443',
            'E: Failed to connect to deb.debian.org:443',
            'E: Error reading from server - read (104: Connection reset by peer)',
            'E: connect (101: Network is unreachable)',
            'E: connect (113: No route to host)',
            'E: Connection refused',
        ):
            with self.subTest(detail=detail), mock.patch.object(
                    store.subprocess, 'Popen', return_value=child(detail)) as process:
                with self.assertRaises(store.StoreNetworkError) as raised:
                    store.command(['apt-get', 'update'], progress=False, retry_safe=True)
                self.assertTrue(raised.exception.retry_safe)
                self.assertIn(detail, str(raised.exception))
                self.assertEqual(process.call_args.kwargs['env']['LC_ALL'], 'C')

    def test_transport_does_not_override_integrity_repository_storage_or_version_failures(self):
        for detail in (
            'E: The following signatures couldn\'t be verified because the public key is not available: NO_PUBKEY ABC',
            'E: The repository is not signed.',
            'E: GPG error: invalid signature',
            'E: The repository does not have a Release file.',
            "E: Clearsigned file isn't valid, got 'NOSPLIT'",
            'E: Certificate verification failed: The certificate is NOT trusted.',
            'E: Hash Sum mismatch',
            'E: Failed to fetch https://deb.debian.org/example.deb 404 Not Found',
            'E: 403 Forbidden',
            'E: No space left on device',
            'E: Read-only file system',
            'E: Permission denied',
            'E: Could not get lock /var/lib/dpkg/lock',
            'E: Package editor has no installation candidate',
            "E: Version '2' for 'editor' was not found",
        ):
            # A preceding transient warning cannot make an unrelated fatal error retryable.
            output = "W: Temporary failure resolving 'deb.debian.org'\n" + detail
            with self.subTest(detail=detail), mock.patch.object(
                    store.subprocess, 'Popen', return_value=child(output)):
                with self.assertRaises(store.StoreError) as raised:
                    store.command(['apt-get', 'update'], progress=False, retry_safe=True)
                self.assertNotIsInstance(raised.exception, store.StoreNetworkError)

    def test_truncated_http_response_is_transport_failure_without_overriding_fatal_errors(self):
        # Actual C-locale APT output from the disposable proxy's truncated response.
        output = ('Get:1 http://proxy-probe.invalid/debian trixie InRelease [100 kB]\n'
                  'Err:1 http://proxy-probe.invalid/debian trixie InRelease\n'
                  '  Error reading from server. Remote end closed connection [IP: 127.0.0.1 45093]\n'
                  'Reading package lists...\n'
                  'E: Failed to fetch http://proxy-probe.invalid/debian/dists/trixie/InRelease  '
                  'Error reading from server. Remote end closed connection [IP: 127.0.0.1 45093]\n'
                  'E: Some index files failed to download. They have been ignored, or old ones used instead.\n')
        with mock.patch.object(store.subprocess, 'Popen', return_value=child(output)):
            with self.assertRaises(store.StoreNetworkError) as raised:
                store.command(['apt-get', 'update'], progress=False, retry_safe=True)
            self.assertTrue(raised.exception.retry_safe)
        for fatal in ('E: Hash Sum mismatch', "E: Clearsigned file isn't valid, got 'NOSPLIT'",
                      'E: GPG error: invalid signature', 'E: No space left on device',
                      'E: Read-only file system', 'E: Certificate verification failed'):
            with self.subTest(fatal=fatal), mock.patch.object(
                    store.subprocess, 'Popen', return_value=child(output + fatal)):
                with self.assertRaises(store.StoreError) as raised:
                    store.command(['apt-get', 'update'], progress=False, retry_safe=True)
                self.assertNotIsInstance(raised.exception, store.StoreNetworkError)

    def test_only_failed_apt_commands_offer_transport_recovery(self):
        detail = 'E: Connection timed out\n'
        for argv in (['dpkg', '--configure', '-a'], ['apt-mark', 'auto', 'editor']):
            with self.subTest(argv=argv), mock.patch.object(
                    store.subprocess, 'Popen', return_value=child(detail)):
                with self.assertRaises(store.StoreError) as raised:
                    store.command(argv, progress=False, retry_safe=True)
                self.assertNotIsInstance(raised.exception, store.StoreNetworkError)
        for code, accepted in ((0, (0,)), (100, None), (100, (0, 100))):
            with self.subTest(code=code, accepted=accepted), mock.patch.object(
                    store.subprocess, 'Popen', return_value=child(detail, code)):
                self.assertEqual(store.command(['apt-get', 'update'], progress=False,
                                              accepted=accepted), (code, detail))

    def test_mutating_apt_transport_errors_are_not_safe_to_repeat_by_default(self):
        with mock.patch.object(store.subprocess, 'Popen', return_value=child('E: Connection refused')):
            with self.assertRaises(store.StoreNetworkError) as raised:
                store.command(['apt-get', 'install', 'editor'], progress=False)
            self.assertFalse(raised.exception.retry_safe)

    def test_cancelled_command_does_not_offer_network_recovery(self):
        with mock.patch.object(store, 'CANCELLED', True), mock.patch.object(
                store.subprocess, 'Popen', return_value=child('E: Connection timed out')):
            with self.assertRaises(store.StoreError) as raised:
                store.command(['apt-get', 'update'], progress=False, retry_safe=True)
            self.assertNotIsInstance(raised.exception, store.StoreNetworkError)

    def test_classifier_uses_the_bounded_failure_tail(self):
        output = 'W: Connection timed out\n' + 'ordinary diagnostic\n' * 40
        with mock.patch.object(store.subprocess, 'Popen', return_value=child(output)):
            with self.assertRaises(store.StoreError) as raised:
                store.command(['apt-get', 'update'], progress=False, retry_safe=True)
            self.assertNotIsInstance(raised.exception, store.StoreNetworkError)
            self.assertNotIn('Connection timed out', str(raised.exception))

    def main_result(self, action, error):
        output = io.StringIO()
        with mock.patch.object(store, 'ensure_root'), mock.patch.object(store, 'repositories', return_value=[]), \
                mock.patch.object(store, 'store_lock', return_value=contextlib.nullcontext()), \
                mock.patch.object(store, 'repair', side_effect=error), mock.patch.object(store.signal, 'signal'), \
                contextlib.redirect_stdout(output):
            self.assertEqual(store.main([action]), 1)
        return json.loads(output.getvalue())

    def test_network_error_json_contains_retry_contract(self):
        for retry_safe in (False, True):
            with self.subTest(retry_safe=retry_safe):
                data = self.main_result('repair', store.StoreNetworkError('Connection timed out', retry_safe))
                self.assertEqual(data['error_kind'], 'network')
                self.assertIs(data['retry_safe'], retry_safe)
                self.assertFalse(data['ok'])

    def test_generic_or_late_cancelled_error_json_does_not_offer_network_retry(self):
        generic = self.main_result('repair', store.StoreError('Ошибка проверки подписи'))
        self.assertEqual(generic['error_kind'], 'operation')
        self.assertNotIn('retry_safe', generic)
        with mock.patch.object(store, 'CANCELLED', True):
            cancelled = self.main_result('repair', store.StoreNetworkError('Connection timed out', True))
        self.assertEqual(cancelled['error_kind'], 'operation')
        self.assertNotIn('retry_safe', cancelled)

    def test_refresh_explicitly_allows_retry_before_installation(self):
        with mock.patch.object(store, 'ensure_root'), mock.patch.object(store, 'repositories', return_value=[]), \
                mock.patch.object(store, 'store_lock', return_value=contextlib.nullcontext()), \
                mock.patch.object(store, 'require_healthy'), mock.patch.object(store, 'command') as command, \
                mock.patch.object(store.signal, 'signal'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(store.main(['refresh']), 0)
        self.assertEqual(command.call_args.kwargs, {'cancellable': True, 'retry_safe': True})
        self.assertIn('APT::Update::Error-Mode=any', command.call_args.args[0])

    def test_download_failure_does_not_create_transaction_journal(self):
        plan = {'format': 1, 'operation': 'install', 'source': 'all', 'requested': ['editor'],
                'changes': [{'package': 'editor', 'to_version': '1'}],
                'snapshot': {}, 'required_bytes': 100, 'download_bytes': 30,
                'transaction_id': 'a' * 32}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(store, 'STATE', Path(directory)), \
                mock.patch.object(store, 'require_healthy'), mock.patch.object(store, 'require_snapshot'), \
                mock.patch.object(store, 'make_plan', return_value=plan), \
                mock.patch.object(store, 'check_transaction_space'), mock.patch.object(store, 'atomic_json') as write, \
                mock.patch.object(store.subprocess, 'Popen', return_value=child('E: Connection timed out')) as process:
            with self.assertRaises(store.StoreNetworkError) as raised:
                store.apply(plan)
            self.assertTrue(raised.exception.retry_safe)
            self.assertIn('--download-only', process.call_args.args[0])
            write.assert_not_called()
            self.assertFalse((Path(directory) / 'journal.json').exists())

    def test_network_failure_after_journal_preserves_repair_requirement(self):
        plan = {'format': 1, 'operation': 'install', 'source': 'all', 'requested': ['editor'],
                'changes': [{'package': 'editor', 'to_version': '1'}],
                'snapshot': {}, 'required_bytes': 100, 'download_bytes': 30,
                'transaction_id': 'a' * 32}
        processes = [child('', 0), child('editor\n', 0), child('E: Connection timed out')]
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(store, 'STATE', Path(directory)), \
                mock.patch.object(store, 'require_healthy'), mock.patch.object(store, 'require_snapshot'), \
                mock.patch.object(store, 'make_plan', return_value=plan), \
                mock.patch.object(store, 'check_transaction_space'), mock.patch.object(store, 'simulate'), \
                mock.patch.object(store, 'registry', return_value={'editor'}), \
                mock.patch.object(store.subprocess, 'Popen', side_effect=processes):
            with self.assertRaises(store.StoreNetworkError) as raised:
                store.apply(plan)
            self.assertFalse(raised.exception.retry_safe)
            self.assertEqual(store.pending()['phase'], 'applying')


if __name__ == '__main__':
    unittest.main()
