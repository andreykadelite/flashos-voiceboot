"""Store navigation follows selected sources and the managed-app registry."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from store import ApplicationStore, SOURCE_LABELS


class NavigationTests(unittest.TestCase):
    def store(self, choices, *, tab=0, source='stable'):
        store = object.__new__(ApplicationStore)
        store.ui = Mock()
        store.ui.choose.side_effect = choices
        store.source, store.query, store.offset, store.tab = source, 'editor', 0, tab
        store.refresh_prompted = True
        store.task = Mock(return_value={'entries': []})
        store.transaction = Mock(return_value=False)
        store.launcher = Mock()
        return store

    def actions(self, store, index=0):
        return [row[1] for row in store.ui.choose.call_args_list[index].args[1]]

    def test_updates_use_every_selected_source(self):
        for source in SOURCE_LABELS:
            with self.subTest(source=source):
                store = self.store([None], tab=2, source=source)
                store.store()
                store.task.assert_called_once_with('Чтение магазина', 'updates', source=source)
                store.transaction.assert_not_called()

    def test_update_all_plans_every_dependency_instead_of_only_visible_apps(self):
        store = self.store([('upgrade-all', None), None], tab=2)
        store.task.return_value = {'entries': [{'package': 'libeditor', 'version': '2'}]}
        store.store()
        store.transaction.assert_called_once_with('upgrade', [])

    def test_source_change_in_updates_refreshes_the_same_tab(self):
        store = self.store([('__tab__', 2), ('source', None), 'backports', None])
        store.store()
        self.assertEqual([(call.args[1], call.kwargs) for call in store.task.call_args_list], [
            ('catalog', {'query': 'editor', 'source': 'stable', 'offset': 0, 'limit': 50}),
            ('updates', {'source': 'stable'}), ('updates', {'source': 'backports'})])
        self.assertEqual(store.tab, 2)
        self.assertEqual(store.source, 'backports')
        store.transaction.assert_not_called()

    def test_installed_inventory_is_independent_of_catalog_filter(self):
        store = self.store([None], tab=1, source='backports')
        store.store()
        store.task.assert_called_once_with('Чтение магазина', 'installed')

    def test_connection_entry_returns_to_same_catalog_without_automatic_refresh(self):
        from unittest.mock import patch
        store = self.store([('network', None), None])
        with patch('store.network_menu') as network:
            store.store()
        network.assert_called_once_with(store.ui)
        self.assertEqual([call.args[1] for call in store.task.call_args_list], ['catalog', 'catalog'])
        self.assertEqual((store.query, store.source, store.tab), ('editor', 'stable', 0))

    def test_uninstalled_package_has_no_launch_or_remove_action(self):
        store = self.store([None])
        store.package({'package': 'mousepad', 'version': '0.6', 'installed_version': None})
        self.assertEqual(self.actions(store), ['install', 'details'])
        store.transaction.assert_not_called()
        store.launcher.launch.assert_not_called()

    def test_managed_package_can_launch_update_and_remove(self):
        entry = {'package': 'mousepad', 'version': '0.6', 'installed_version': '0.5'}
        store = self.store([None])
        store.task.return_value = {'entries': [entry]}
        store.package(entry)
        self.assertEqual(self.actions(store), ['launch', 'upgrade', 'remove', 'details'])
        store.transaction.assert_not_called()

    def test_update_existing_app_uses_upgrade_to_avoid_source_downgrade(self):
        entry = {'package': 'mousepad', 'version': '0.6', 'installed_version': '0.7~bpo'}
        store = self.store(['upgrade', None], source='stable')
        store.task.return_value = {'entries': [entry]}
        store.package(entry)
        self.assertEqual(self.actions(store), ['launch', 'upgrade', 'remove', 'details'])
        store.transaction.assert_called_once_with('upgrade', ['mousepad'])

    def test_runtime_and_automatic_dependencies_are_not_user_app_actions(self):
        for name in ('apt', 'orca', 'libgtk-3-0t64'):
            with self.subTest(package=name):
                store = self.store([None])
                store.package({'package': name, 'version': '1', 'installed_version': '1'})
                self.assertEqual(self.actions(store), ['details'])
                self.assertIn('Компонент среды', store.ui.choose.call_args.args[2])
                store.transaction.assert_not_called()
                store.launcher.launch.assert_not_called()

    def test_successful_install_immediately_refreshes_available_actions(self):
        entry = {'package': 'mousepad', 'version': '0.6', 'installed_version': None}
        installed = {**entry, 'installed_version': '0.6'}
        store = self.store(['install', None])
        store.task.side_effect = [{'entries': []}, {'entries': [installed]}]
        store.transaction.return_value = True
        store.package(entry)
        self.assertEqual(self.actions(store, 0), ['install', 'details'])
        self.assertEqual(self.actions(store, 1), ['launch', 'upgrade', 'remove', 'details'])
        store.transaction.assert_called_once_with('install', ['mousepad'])
        self.assertIsNone(entry['installed_version'], 'Input catalog metadata must not be mutated')

    def test_cancelled_install_keeps_the_uninstalled_actions(self):
        store = self.store(['install', None])
        store.package({'package': 'mousepad', 'version': '0.6', 'installed_version': None})
        self.assertEqual(self.actions(store, 0), self.actions(store, 1))
        self.assertEqual(self.actions(store, 1), ['install', 'details'])
        store.launcher.launch.assert_not_called()

    def test_failed_inventory_does_not_guess_installed_state(self):
        store = self.store([None])
        store.task.return_value = None
        store.package({'package': 'mousepad', 'installed_version': '0.6'})
        store.ui.choose.assert_not_called()
        store.transaction.assert_not_called()
        store.launcher.launch.assert_not_called()

    def test_managed_launch_keeps_launcher_choice_and_returns_to_card(self):
        entry = {'package': 'mousepad', 'version': '0.6', 'installed_version': '0.6'}
        launcher = {'name': 'Mousepad', 'argv': ['/usr/bin/mousepad'], 'terminal': False}
        store = self.store(['launch', launcher, None])
        store.task.side_effect = [{'entries': [entry]}, {'entries': [launcher]}, {'entries': [entry]}]
        store.package(entry)
        store.launcher.launch.assert_called_once_with(launcher)
        self.assertEqual(store.task.call_args_list[1].kwargs, {'package': 'mousepad'})
        self.assertEqual(self.actions(store, 0), self.actions(store, 2))
        store.transaction.assert_not_called()


if __name__ == '__main__':
    unittest.main()
