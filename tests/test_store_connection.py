"""Connection recovery preserves browsing and rechecks installation consent."""
import contextlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from store import ApplicationStore
from store_client import StoreClient, StoreConnectionError, result_json


def connection_failure(*, retry_safe=True, cancelled=False):
    return SimpleNamespace(failed=True, cancelled=cancelled, incomplete=False,
                           store_connection_error=StoreConnectionError('Нет связи', retry_safe))


def success(data):
    return SimpleNamespace(failed=False, cancelled=False, incomplete=False, payload=data)


class ConnectionTests(unittest.TestCase):
    def store(self, jobs, choices):
        store = object.__new__(ApplicationStore)
        store.ui = Mock()
        store.ui.focus = {}
        store.ui.run_job.side_effect = jobs
        store.ui.choose.side_effect = choices
        store.client = Mock()
        store.source, store.query, store.offset, store.tab = 'backports', 'editor', 50, 2
        return store

    def test_backend_preserves_network_type_but_not_other_errors(self):
        output = json.dumps({'format': 1, 'ok': False, 'error': 'DNS',
                             'error_kind': 'network', 'retry_safe': True})
        with self.assertRaises(StoreConnectionError) as raised:
            result_json(output)
        self.assertTrue(raised.exception.retry_safe)
        with self.assertRaises(RuntimeError) as raised:
            result_json('{"format":1,"ok":false,"error":"Подпись недействительна"}')
        self.assertNotIsInstance(raised.exception, StoreConnectionError)

    def test_only_explicit_backend_true_allows_retry(self):
        for value in (None, 'true', 1, False):
            with self.subTest(value=value), self.assertRaises(StoreConnectionError) as raised:
                result_json(json.dumps({'format': 1, 'ok': False, 'error_kind': 'network',
                                        'retry_safe': value}))
            self.assertFalse(raised.exception.retry_safe)

    def test_setup_returns_to_explicit_retry_and_preserves_browsing(self):
        store = self.store([connection_failure(), success({'message': 'Обновлено'})],
                           ['network', 'retry'])
        with patch('store.network_menu') as network:
            self.assertEqual(store.task('Каталог', 'refresh'), {'message': 'Обновлено'})
        network.assert_called_once_with(store.ui)
        self.assertEqual(store.ui.run_job.call_count, 2)
        self.assertEqual((store.source, store.query, store.offset, store.tab),
                         ('backports', 'editor', 50, 2))
        self.assertTrue(store.ui.choose.call_args.kwargs['prompt'])

    def test_escape_from_setup_does_not_start_an_automatic_download(self):
        store = self.store([connection_failure()], ['network', None])
        with patch('store.network_menu'):
            self.assertIsNone(store.task('Каталог', 'refresh'))
        self.assertEqual(store.ui.run_job.call_count, 1)

    def test_repeated_connection_failure_can_be_cancelled(self):
        store = self.store([connection_failure(), connection_failure()], ['retry', None])
        self.assertIsNone(store.task('Каталог', 'refresh'))
        self.assertEqual(store.ui.run_job.call_count, 2)

    def test_storage_failure_and_cancellation_do_not_offer_network(self):
        for job in (SimpleNamespace(failed=True, cancelled=False, incomplete=False),
                    connection_failure(cancelled=True), success({'cancelled': True})):
            with self.subTest(job=job):
                store = self.store([job], [])
                self.assertIsNone(store.task('Каталог', 'refresh'))
                store.ui.choose.assert_not_called()

    def test_cancel_requested_during_failed_mutation_never_offers_recovery(self):
        from jobs import Job
        def failing_download(job):
            job.cancel()
            error = StoreConnectionError('Соединение прервано', retry_safe=True)
            job.store_connection_error = error
            raise error
        job = Job('Загрузка', failing_download).start()
        self.assertTrue(job.done.wait(2))
        self.assertTrue(job.failed)
        self.assertFalse(job.cancelled)  # Failed worker and cancellation may overlap.
        store = self.store([job], [])
        self.assertIsNone(store.task('Установка', 'apply', {}))
        store.ui.choose.assert_not_called()

    def test_unsafe_partial_write_can_open_setup_but_cannot_be_replayed(self):
        store = self.store([connection_failure(retry_safe=False)], ['network', None])
        with patch('store.network_menu') as network:
            self.assertIsNone(store.task('Восстановление', 'repair'))
        network.assert_called_once()
        actions = [row[1] for row in store.ui.choose.call_args.args[1]]
        self.assertNotIn('retry', actions)
        self.assertEqual(store.ui.run_job.call_count, 1)

    def test_failed_install_rebuilds_plan_and_requires_confirmation(self):
        old = {'changes': [{'package': 'nano', 'action': 'install', 'to_version': '1'}],
               'required_bytes': 100, 'transaction_id': 'old'}
        new = dict(old, transaction_id='new')
        store = self.store([success(old), connection_failure(), success(new)],
                           [True, 'network', 'retry', False])
        with patch('store.network_menu'):
            self.assertFalse(store.transaction('install', ['nano']))
        self.assertEqual(store.ui.run_job.call_count, 3)  # No second apply after cancellation.
        titles = [call.args[0] for call in store.ui.run_job.call_args_list]
        self.assertEqual(titles, ['Проверка зависимостей и места',
                                 'Изменение приложений на флешке', 'Проверка зависимостей и места'])
        self.assertEqual(len([call for call in store.ui.choose.call_args_list
                              if call.args[0].startswith('Установить ')]), 2)

    def test_offline_catalog_and_installed_queries_do_not_request_connection(self):
        for action in ('catalog', 'installed', 'launchers', 'plan'):
            store = self.store([success({'entries': []})], [])
            self.assertEqual(store.task('Сохранённые данные', action), {'entries': []})
            store.ui.choose.assert_not_called()

    def test_client_marks_connection_failure_and_refreshes_storage_on_each_call(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            environment = Mock(root=Path(folder))
            environment.lock.side_effect = lambda: contextlib.nullcontext()
            client = StoreClient(environment)
            for _ in range(2):
                job = Mock()
                job.command.return_value = (1, json.dumps({'format': 1, 'ok': False,
                    'error_kind': 'network', 'retry_safe': True, 'error': 'DNS'}))
                with self.assertRaises(StoreConnectionError):
                    client.call(job, 'refresh')
                self.assertIsInstance(job.store_connection_error, StoreConnectionError)
            self.assertEqual(environment.open.call_count, 2)


if __name__ == '__main__':
    unittest.main()
