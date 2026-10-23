"""Catalog equivalence and compressed-record access order without an APT daemon."""
from contextlib import contextmanager, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import store_packages as store


class Version:
    def __init__(self, name, version='1', summary='', description='', *,
                 suite='trixie', component='main', file=0, record=0, arch='amd64', reads=None):
        self.name = name
        self.version = version
        self.architecture = arch
        self.origins = [SimpleNamespace(codename=suite, component=component)]
        self.section = 'utils'
        self.size = 123
        self.installed_size = 456
        self._summary = summary
        self._description = description
        self.reads = reads if reads is not None else []
        self.primitive = SimpleNamespace(ver_str=version, arch=arch,
            translated_description=SimpleNamespace(file_list=[(SimpleNamespace(id=file), record)]))

    @property
    def summary(self):
        self.reads.append(('summary', self.name))
        return self._summary

    @property
    def description(self):
        self.reads.append(('description', self.name))
        return self._description


def package(name, *versions, installed=None):
    return SimpleNamespace(name=name, versions=list(versions),
                           is_installed=installed is not None, installed=installed)


class CatalogTests(unittest.TestCase):
    @contextmanager
    def backend(self, packages, *, raw=None, error=None):
        raw = raw if raw is not None else {item.name: SimpleNamespace(
            version_list=[version.primitive for version in item.versions]) for item in packages}
        progress = object()
        factory = mock.Mock(return_value=raw, side_effect=error)
        apt_pkg = SimpleNamespace(Cache=factory, version_compare=lambda left, right: int(left) - int(right))
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(store, 'LISTS', Path(folder)), \
                mock.patch.object(store, 'cache', return_value=packages) as cache, \
                mock.patch.dict(sys.modules, {'apt_pkg': apt_pkg,
                    'apt.progress.base': SimpleNamespace(OpProgress=lambda: progress)}):
            yield factory, progress, cache

    def test_descriptions_are_read_in_file_record_order_and_results_keep_name_order(self):
        reads = []
        packages = [package(name, Version(name, summary='needle', file=file, record=record, reads=reads))
                    for name, file, record in [('alpha', 1, 2), ('bravo', 0, 9), ('charlie', 0, 1)]]
        with self.backend(packages) as (factory, progress, _):
            result = store.catalog('needle', 'all', 0, 100)
        self.assertEqual(reads[:6], [('summary', 'charlie'), ('description', 'charlie'),
                                   ('summary', 'bravo'), ('description', 'bravo'),
                                   ('summary', 'alpha'), ('description', 'alpha')])
        self.assertEqual([item['package'] for item in result['entries']], ['alpha', 'bravo', 'charlie'])
        factory.assert_called_once_with(progress)

    def test_exact_prefix_name_order_duplicate_descriptions_and_pagination(self):
        packages = [package(name, Version(name, description='target', file=0, record=record))
                    for name, record in [('libtarget', 8), ('aardvark', 2), ('target-extra', 2), ('target', 9)]]
        with self.backend(packages):
            first = store.catalog(' TARGET ', 'all', 0, 2)
            second = store.catalog('target', 'all', 2, 2)
            past = store.catalog('target', 'all', 4, 2)
        self.assertEqual([item['package'] for item in first['entries']], ['target', 'target-extra'])
        self.assertEqual([item['package'] for item in second['entries']], ['aardvark', 'libtarget'])
        self.assertEqual(first['total'], 4)
        self.assertEqual(first['next_offset'], 2)
        self.assertIsNone(second['next_offset'])
        self.assertEqual(past['entries'], [])
        self.assertEqual(past['total'], 4)

    def test_multilingual_casefold_full_description_and_field_boundaries_are_preserved(self):
        packages = [package('target', Version('target', summary='Краткое описание',
            description='Полное описание Straße ' + 'x' * 12000 + ' ПОСЛЕДНЕЕ-СЛОВО'))]
        with self.backend(packages):
            for query in ('КРАТКОЕ', 'полное описание', 'STRASSE', 'последнее-слово',
                          'target краткое', 'описание полное'):
                with self.subTest(query=query):
                    result = store.catalog(query, 'all', 0, 10)
                    self.assertEqual(result['total'], 1)
                    self.assertEqual(result['entries'][0]['package'], 'target')
                    self.assertLessEqual(len(result['entries'][0]['description']), 12000)

    def test_search_uses_selected_source_version_and_keeps_component_filters(self):
        stable = Version('app', version='2', summary='stable-only', suite='trixie', component='main', record=5)
        backport = Version('app', version='3', summary='backport-only', suite='trixie-backports', record=2)
        contrib = Version('extra', summary='stable-only', component='contrib', record=1)
        foreign = Version('foreign', summary='stable-only', suite='sid', record=0)
        packages = [package('app', backport, stable), package('extra', contrib), package('foreign', foreign)]
        with self.backend(packages) as (_, _, cache):
            self.assertEqual(store.catalog('backport-only', 'all', 0, 10)['total'], 0)
            result = store.catalog('backport-only', 'backports', 0, 10)
            self.assertEqual([(item['package'], item['version']) for item in result['entries']], [('app', '3')])
            self.assertEqual([item['package'] for item in store.catalog('stable-only', 'main', 0, 10)['entries']], ['app'])
            self.assertEqual([item['package'] for item in store.catalog('stable-only', 'contrib', 0, 10)['entries']], ['extra'])
            self.assertEqual([item['package'] for item in store.catalog('stable-only', 'stable', 0, 10)['entries']], ['app', 'extra'])
            cache.assert_any_call('backports')

    def test_public_key_matches_version_and_architecture_instead_of_candidate(self):
        wanted = Version('app', version='2', arch='all', summary='needle')
        packages = [package('app', wanted)]
        wrong_version = Version('app', version='3').primitive
        wrong_arch = Version('app', version='2', arch='arm64').primitive
        raw = {'app': SimpleNamespace(version_list=[wrong_version, wrong_arch, wanted.primitive])}
        with self.backend(packages, raw=raw):
            self.assertEqual(store.catalog('needle', 'all', 0, 10)['entries'][0]['version'], '2')

    def test_missing_record_key_or_low_level_cache_falls_back_to_original_read_order(self):
        for failure in ('cache', 'missing-package', 'missing-version', 'missing-description', 'empty-file-list'):
            with self.subTest(failure=failure):
                reads = []
                alpha = Version('alpha', summary='needle', record=9, reads=reads)
                bravo = Version('bravo', summary='needle', record=1, reads=reads)
                packages = [package('alpha', alpha), package('bravo', bravo)]
                raw = {item.name: SimpleNamespace(version_list=[item.versions[0].primitive]) for item in packages}
                error = None
                if failure == 'cache':
                    error = OSError('cache unavailable')
                elif failure == 'missing-package':
                    raw.pop('bravo')
                elif failure == 'missing-version':
                    raw['bravo'].version_list = []
                elif failure == 'missing-description':
                    del bravo.primitive.translated_description
                else:
                    bravo.primitive.translated_description.file_list = []
                with self.backend(packages, raw=raw, error=error):
                    result = store.catalog('needle', 'all', 0, 10)
                self.assertEqual([item['package'] for item in result['entries']], ['alpha', 'bravo'])
                self.assertEqual(reads[:4], [('summary', 'alpha'), ('description', 'alpha'),
                                            ('summary', 'bravo'), ('description', 'bravo')])

    def test_empty_query_does_not_open_extra_cache_and_only_reads_page_metadata(self):
        reads = []
        packages = [package(name, Version(name, reads=reads)) for name in ('bravo', 'alpha', 'charlie')]
        with self.backend(packages) as (factory, _, _):
            result = store.catalog('', 'all', 1, 1)
        factory.assert_not_called()
        self.assertEqual(result['total'], 3)
        self.assertEqual([item['package'] for item in result['entries']], ['bravo'])
        self.assertEqual(reads, [('summary', 'bravo'), ('description', 'bravo')])

    def test_protected_packages_are_excluded_before_key_or_description_reads(self):
        reads = []
        packages = [package('linux-image-test', Version('linux-image-test', summary='needle', reads=reads)),
                    package('normal', Version('normal', summary='needle', reads=reads))]
        with self.backend(packages, raw={'normal': SimpleNamespace(version_list=[packages[1].versions[0].primitive])}):
            result = store.catalog('needle', 'all', 0, 10)
        self.assertEqual([item['package'] for item in result['entries']], ['normal'])
        self.assertNotIn(('summary', 'linux-image-test'), reads)

    def test_no_progress_output_is_emitted_by_catalog(self):
        packages = [package('app', Version('app', summary='needle'))]
        output = io.StringIO()
        with self.backend(packages), redirect_stdout(output):
            store.catalog('needle', 'all', 0, 10)
        self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
