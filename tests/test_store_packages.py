"""Checks for store transaction integrity, repository policy, and desktop argv."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import sys
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
spec = importlib.util.spec_from_file_location(
    'store_packages', Path(__file__).resolve().parents[1] / 'src/store_packages.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


class PackageStoreTests(unittest.TestCase):
    def test_package_names_cannot_be_options_or_shell_fragments(self):
        for name in ('--allow-unauthenticated', 'mousepad;rm', '../pkg', 'pkg=1', 'Pkg', 'pkg:i386', 'pkg\n'):
            with self.subTest(name=name), self.assertRaises(store.StoreError):
                store.names([name])
        self.assertEqual(store.names(['mousepad:amd64', 'python3-gi', 'libgtk-3-0t64']), ['mousepad', 'python3-gi', 'libgtk-3-0t64'])

    def test_official_sources_reject_mixed_suites_and_disabled_signatures(self):
        uri = 'https://deb.debian.org/debian'
        store.validate_repository(uri, 'trixie', ['main', 'non-free'])
        for change in (
            {'uri': 'http://deb.debian.org/debian'},
            {'uri': 'https://deb.debian.org.evil/debian'},
            {'uri': 'https://deb.debian.org/debian?another=repo'},
            {'suite': 'unstable'},
            {'suite': 'testing'},
            {'fields': {'trusted': 'yes'}},
            {'fields': {'allow-insecure': 'true'}},
            {'fields': {'signed-by': '/tmp/custom-key.gpg'}},
        ):
            with self.subTest(change=change), self.assertRaises(store.StoreError):
                store.validate_repository(**({'uri': uri, 'suite': 'trixie', 'components': ['main']} | change))

    def test_snapshot_includes_content_not_just_status_and_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status = root / 'status'
            lists = root / 'lists'
            lists.mkdir()
            status.write_text('first')
            index = lists / 'packages'
            index.write_text('first')
            with mock.patch.object(store, 'STATUS', status), mock.patch.object(store, 'LISTS', lists):
                before = store.snapshot()
                (lists / 'lock').write_text('lock changes are not repository changes')
                self.assertEqual(before, store.snapshot())
                index.write_text('other')
                with self.assertRaisesRegex(store.StoreError, 'изменился'):
                    store.require_snapshot(before)
                index.write_text('first')
                status.write_text('other')
                with self.assertRaises(store.StoreError):
                    store.require_snapshot(before)

    def test_registry_atomic_roundtrip_and_interrupted_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with mock.patch.object(store, 'STATE', state), mock.patch.object(store, 'baseline', return_value={'apt'}):
                store.atomic_json(state / 'state.json', {'format': 1, 'packages': ['mousepad', 'apt']})
                self.assertEqual(store.registry(), {'mousepad'})
                store.atomic_json(state / 'journal.json', {'phase': 'applying', 'plan': {}})
                with self.assertRaisesRegex(store.StoreError, 'прервана'):
                    store.require_healthy()
                store.atomic_json(state / 'journal.json', {'phase': 'complete'})
                self.assertIsNone(store.pending())
                self.assertEqual(list(state.glob('*.tmp-*')), [])

    def test_runtime_removal_and_kernel_install_fail_but_library_upgrade_allowed(self):
        store.allowed_changes([{'package': 'libgtk-3-0t64', 'action': 'upgrade'}], {'libgtk-3-0t64'})
        for change in (
            {'package': 'libgtk-3-0t64', 'action': 'remove'},
            {'package': 'dpkg', 'action': 'upgrade'},
            {'package': 'linux-image-amd64', 'action': 'install'},
            {'package': 'essential-test', 'action': 'remove', 'essential': True},
        ):
            with self.subTest(change=change), self.assertRaises(store.StoreError):
                store.allowed_changes([change], {'libgtk-3-0t64'})

    def test_exact_plan_versions_are_passed_as_argv_and_removal_not_autoremove(self):
        plan = {'source': 'backports', 'changes': [
            {'package': 'mousepad', 'to_version': '1:0.6.3-1~bpo13+1'},
            {'package': 'old-editor', 'to_version': None}]}
        argv = store.apt_arguments(plan)
        self.assertIn('mousepad=1:0.6.3-1~bpo13+1', argv)
        self.assertIn('old-editor-', argv)
        self.assertIn('--no-auto-remove', argv)
        self.assertIn('trixie-backports', argv)
        plan['changes'][0]['to_version'] = '1; /bin/sh'
        with self.assertRaises(store.StoreError):
            store.apt_arguments(plan)

    def test_simulation_must_show_complete_dep_changes_and_exact_versions(self):
        plan = {'source': 'all', 'changes': [
            {'package': 'mousepad', 'to_version': '0.6.4-1'},
            {'package': 'lib-editor', 'to_version': '1.2'},
            {'package': 'old-editor', 'to_version': None}]}
        output = ('Inst mousepad (0.6.4-1 Debian:13/stable [amd64])\n'
                  'Inst lib-editor [1.1] (1.2 Debian:13/stable [amd64])\n'
                  'Remv old-editor [1.0]\nConf mousepad (0.6.4-1)\n')
        with mock.patch.object(store, 'command', return_value=(0, output)):
            store.simulate(plan)
        for bad in (output.replace('1.2 ', '1.3 '), output + 'Remv critical-runtime [1.0]\n'):
            with mock.patch.object(store, 'command', return_value=(0, bad)), self.assertRaises(store.StoreError):
                store.simulate(plan)

    def test_insufficient_space_and_inodes_block_even_an_apparently_small_download(self):
        with mock.patch.object(store.shutil, 'disk_usage', return_value=SimpleNamespace(free=32)):
            with self.assertRaisesRegex(store.StoreError, 'места'):
                store.check_space(128, Path(tempfile.gettempdir()))
        with mock.patch.object(store.shutil, 'disk_usage', return_value=SimpleNamespace(free=2**40)):
            self.assertEqual(store.check_space(128, Path(tempfile.gettempdir())), 2**40)

    def test_registry_tracks_requested_apps_preserving_existing_and_removing_only_previewed(self):
        plan = {'operation': 'install', 'requested': ['mousepad'],
                'changes': [{'package': 'lib-editor', 'action': 'install'}]}
        with mock.patch.object(store, 'baseline', return_value={'apt'}):
            self.assertEqual(store.updated_registry({'firefox-esr', 'apt'}, plan), {'mousepad', 'firefox-esr'})
            plan = {'operation': 'remove', 'requested': ['mousepad'],
                    'changes': [{'package': 'mousepad', 'action': 'remove'}]}
            self.assertEqual(store.updated_registry({'mousepad', 'firefox-esr'}, plan), {'firefox-esr'})

    def test_apply_rejects_modified_plan_before_download_or_package_mutation(self):
        plan = {'format': 1, 'operation': 'install', 'source': 'stable', 'requested': ['mousepad'],
                'changes': [], 'snapshot': {'status': 'a', 'lists': 'b'}, 'required_bytes': 999,
                'download_bytes': 0, 'transaction_id': 'a' * 32}
        fresh = plan | {'required_bytes': 998}
        with mock.patch.object(store, 'require_healthy'), mock.patch.object(store, 'require_snapshot'), \
             mock.patch.object(store, 'make_plan', return_value=fresh), mock.patch.object(store, 'command') as command:
            with self.assertRaisesRegex(store.StoreError, 'изменён'):
                store.apply(plan)
            command.assert_not_called()

    def test_desktop_substitutions_are_literal_argv_and_file_parameters_removed(self):
        desktop = Path('/usr/share/applications/editor.desktop')
        with mock.patch.object(store, 'executable', return_value='/usr/bin/editor'):
            argv = store.desktop_argv('editor --caption "%c" %i %F --literal "$(touch /tmp/pwn)" %%',
                                      'Текстовый редактор', 'editor-icon', desktop)
        self.assertEqual(argv, ['/usr/bin/editor', '--caption', 'Текстовый редактор',
                                '--icon', 'editor-icon', '--literal', '$(touch /tmp/pwn)', '%'])
        with mock.patch.object(store, 'executable', return_value='/usr/bin/editor'):
            for text in ('editor "unterminated', 'editor %Z', 'editor --files=%F'):
                with self.subTest(text=text), self.assertRaises(store.StoreError):
                    store.desktop_argv(text, 'Editor', '', desktop)

    def test_desktop_files_outside_package_application_directory_are_rejected(self):
        for path in ('relative.desktop', '/tmp/editor.desktop', '/usr/share/applications/sub/editor.desktop'):
            with self.subTest(path=path), self.assertRaises(store.StoreError):
                store.parse_desktop(path, 'mousepad')

    def test_verify_ignores_preserved_config_but_detects_changed_program(self):
        def command(argv, **kwargs):
            if '--verify' in argv:
                return 0, '??5?????? c /etc/editor.conf\n'
            return 0, ''
        with mock.patch.object(store, 'command', side_effect=command), mock.patch.object(store, 'cache', return_value={
                'mousepad': SimpleNamespace(is_installed=True, installed=SimpleNamespace(version='1'))}):
            store.verify_packages([{'package': 'mousepad', 'to_version': '1'}])
        def altered(argv, **kwargs):
            if '--verify' in argv:
                return 0, '??5?????? /usr/bin/editor\n'
            return 0, ''
        with mock.patch.object(store, 'command', side_effect=altered), mock.patch.object(store, 'cache', return_value={
                'mousepad': SimpleNamespace(is_installed=True, installed=SimpleNamespace(version='1'))}), self.assertRaises(store.StoreError):
            store.verify_packages([{'package': 'mousepad', 'to_version': '1'}])


    def test_release_codename_accepts_stable_and_security_while_archive_names_change(self):
        def version(number, codename, archive):
            return SimpleNamespace(version=str(number), origins=[SimpleNamespace(
                codename=codename, archive=archive, component='main')])
        package = SimpleNamespace(versions=[version(3, 'trixie-backports', 'stable-backports'),
                                           version(2, 'trixie-security', 'stable-security'),
                                           version(1, 'trixie', 'stable')])
        api = SimpleNamespace(version_compare=lambda left, right: int(left) - int(right))
        with mock.patch.dict(sys.modules, {'apt_pkg': api}):
            self.assertEqual(store.selected_version(package, 'all').version, '2')
            self.assertEqual(store.selected_version(package, 'main').version, '2')
            self.assertEqual(store.selected_version(package, 'backports').version, '3')

    def test_backports_source_survives_old_installed_version_disappearing_from_indexes(self):
        package = SimpleNamespace(name='tmux', is_installed=True,
                                  installed=SimpleNamespace(origins=[]))
        with mock.patch.object(store, 'registry_sources', return_value={'tmux': 'backports'}):
            self.assertEqual(store.effective_source(package, 'all'), 'backports')
            self.assertEqual(store.effective_source(package, 'stable'), 'stable')

    def test_postinstall_version_or_unexpected_still_installed_removal_is_rejected(self):
        packages = {'editor': SimpleNamespace(is_installed=True, installed=SimpleNamespace(version='2'))}
        store.verify_versions([{'package': 'editor', 'to_version': '2'}], packages)
        for expected in ('1', None):
            with self.subTest(expected=expected), self.assertRaises(store.StoreError):
                store.verify_versions([{'package': 'editor', 'to_version': expected}], packages)

    def test_inode_exhaustion_rejected_on_linux(self):
        if not hasattr(store.os, 'statvfs'):
            return
        with mock.patch.object(store.shutil, 'disk_usage', return_value=SimpleNamespace(free=2**40)), \
             mock.patch.object(store.os, 'statvfs', return_value=SimpleNamespace(f_favail=10)):
            with self.assertRaisesRegex(store.StoreError, 'файловых'):
                store.check_space(128, Path(tempfile.gettempdir()))


    def test_terminal_only_packages_launch_their_owned_binaries_without_a_shell(self):
        files = ['/usr/bin/nano', '/usr/bin/nano-helper', '/usr/share/doc/nano/README',
                 '/etc/nano.conf', '/tmp/injected', '/usr/bin/sub/hidden']
        with mock.patch.object(store, 'executable', side_effect=lambda value: value):
            entries = store.terminal_entries(files, 'nano')
        self.assertEqual(entries[0]['argv'], ['/usr/bin/nano'])
        self.assertTrue(all(entry['terminal'] for entry in entries))
        self.assertEqual({entry['argv'][0] for entry in entries}, {'/usr/bin/nano', '/usr/bin/nano-helper'})

    def test_hidden_or_invalid_desktop_files_do_not_become_command_fallbacks(self):
        with mock.patch.object(store, 'require_healthy'), mock.patch.object(store, 'registry', return_value={'editor'}), \
             mock.patch.object(store, 'command', return_value=(0, '/usr/bin/editor\n/usr/share/applications/editor.desktop\n')), \
             mock.patch.object(store, 'parse_desktop', return_value=None):
            self.assertEqual(store.launchers('editor')['entries'], [])


    def test_cache_cleanup_guards_root_then_only_cleans_downloads_and_reports_space(self):
        with mock.patch.object(store, 'ensure_root') as guard, mock.patch.object(store, 'require_healthy') as healthy, \
             mock.patch.object(store.shutil, 'disk_usage', side_effect=[SimpleNamespace(free=100), SimpleNamespace(free=300)]), \
             mock.patch.object(store, 'command') as command:
            result = store.clean_cache()
        guard.assert_called_once()
        healthy.assert_called_once()
        command.assert_called_once_with(['apt-get', *store.APT_OPTIONS, 'clean'])
        self.assertEqual(result['freed_bytes'], 200)
        self.assertEqual(result['free_bytes'], 300)

    def test_interrupted_package_mutation_blocks_cache_cleanup_before_any_deletion(self):
        with mock.patch.object(store, 'ensure_root'), \
             mock.patch.object(store, 'require_healthy', side_effect=store.StoreError('Установка была прервана.')), \
             mock.patch.object(store, 'command') as command, \
             mock.patch.object(store.shutil, 'disk_usage') as usage:
            with self.assertRaises(store.StoreError):
                store.clean_cache()
        command.assert_not_called()
        usage.assert_not_called()


    def test_removal_can_free_nearly_full_storage_but_still_has_a_journal_budget(self):
        class Cache(dict):
            broken_count = 0
        package = SimpleNamespace(is_installed=True, mark_delete=mock.Mock())
        change = {'package': 'nano', 'action': 'remove', 'from_version': '1',
                  'to_version': None, 'download_bytes': 0, 'essential': False}
        with mock.patch.object(store, 'require_healthy'), \
             mock.patch.object(store, 'cache', return_value=Cache(nano=package)), \
             mock.patch.object(store, 'snapshot', return_value={'status': 'same', 'lists': 'same'}), \
             mock.patch.object(store, 'changes_from_cache', return_value=[change]), \
             mock.patch.object(store, 'baseline', return_value=set()), \
             mock.patch.object(store, 'simulate'), \
             mock.patch.object(store.shutil, 'disk_usage', return_value=SimpleNamespace(free=64 * 1024**2)), \
             mock.patch.object(store.os, 'statvfs', create=True, return_value=SimpleNamespace(f_favail=128)):
            plan = store.make_plan({'action': 'remove', 'packages': ['nano'], 'source': 'stable'})
            self.assertGreater(plan['required_bytes'], 0)
            self.assertLess(plan['required_bytes'], 64 * 1024**2)
            with mock.patch.object(store.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
                with self.assertRaisesRegex(store.StoreError, 'места'):
                    store.make_plan({'action': 'remove', 'packages': ['nano'], 'source': 'stable'})
            with self.assertRaises(store.StoreError):
                store.check_transaction_space(store.RESERVE, [change | {'action': 'install', 'to_version': '2'}])
            with mock.patch.object(store.os, 'statvfs', return_value=SimpleNamespace(f_favail=0)):
                with self.assertRaisesRegex(store.StoreError, 'файловых'):
                    store.check_transaction_space(plan['required_bytes'], [change])

    def test_metapackage_launchers_use_only_direct_installed_nonbaseline_gui_owners(self):
        def version(name):
            return SimpleNamespace(package=SimpleNamespace(name=name))
        suite = SimpleNamespace(installed=SimpleNamespace(section='metapackages', dependencies=[
            SimpleNamespace(installed_target_versions=[version('writer'), version('calc')]),
            SimpleNamespace(installed_target_versions=[version('baseline-terminal')]),
            SimpleNamespace(installed_target_versions=[])]))
        files = {'suite': '/usr/share/doc/suite/README\n',
                 'writer': '/usr/share/applications/writer.desktop\n/usr/bin/cli-helper\n',
                 'calc': '/usr/share/applications/calc.desktop\n/usr/share/applications/hidden.desktop\n'}
        def query(argv, **kwargs):
            self.assertEqual(argv[:2], ['dpkg-query', '-L'])
            return 0, files[argv[2]]
        def desktop(path, package):
            if Path(path).name == 'hidden.desktop':
                return None
            return {'id': Path(path).name, 'package': package, 'name': Path(path).stem,
                    'argv': ['/usr/bin/' + Path(path).stem], 'terminal': False}
        with mock.patch.object(store, 'require_healthy'), \
             mock.patch.object(store, 'registry', return_value={'suite'}), \
             mock.patch.object(store, 'cache', return_value={'suite': suite}), \
             mock.patch.object(store, 'baseline', return_value={'baseline-terminal'}), \
             mock.patch.object(store, 'command', side_effect=query) as command, \
             mock.patch.object(store, 'parse_desktop', side_effect=desktop):
            entries = store.launchers('suite')['entries']
        self.assertEqual({entry['owner_package'] for entry in entries}, {'writer', 'calc'})
        self.assertTrue(all(entry['package'] == 'suite' and not entry['terminal'] for entry in entries))
        self.assertEqual(command.call_count, 3)
        self.assertEqual({entry['id'] for entry in entries}, {'writer.desktop', 'calc.desktop'})

    def test_metapackage_resolution_does_not_invent_launchers_for_libraries_or_hidden_owned_entries(self):
        with mock.patch.object(store, 'cache', return_value={'library': SimpleNamespace(
                installed=SimpleNamespace(section='libs'))}), mock.patch.object(store, 'command') as command:
            self.assertEqual(store.metapackage_entries('library'), [])
            command.assert_not_called()
        with mock.patch.object(store, 'require_healthy'), \
             mock.patch.object(store, 'registry', return_value={'suite'}), \
             mock.patch.object(store, 'command', return_value=(0, '/usr/share/applications/suite.desktop\n')), \
             mock.patch.object(store, 'parse_desktop', return_value=None), \
             mock.patch.object(store, 'metapackage_entries') as fallback:
            self.assertEqual(store.launchers('suite')['entries'], [])
            fallback.assert_not_called()


if __name__ == '__main__':
    unittest.main()
