"""Connection navigation stays explicit and returns to the requesting screen."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import network


class NetworkMenuTests(unittest.TestCase):
    def ui(self, choices=()):
        ui = Mock()
        ui.demo = False
        ui.choose.side_effect = choices
        return ui

    def test_escape_and_return_leave_without_connecting(self):
        for choice in (None, 'back'):
            with self.subTest(choice=choice):
                ui = self.ui([choice])
                with patch('network.network_tool') as tool:
                    network.network_menu(ui)
                tool.assert_not_called()
                ui.run_job.assert_not_called()
                self.assertEqual(ui.choose.call_args.args[0], 'Инструменты — подключение к сети')
                self.assertTrue(ui.choose.call_args.kwargs['prompt'])

    def test_connection_returns_to_menu_without_automatic_diagnostics(self):
        ui = self.ui(['wifi', 'back'])
        with patch('network.network_tool') as tool:
            network.network_menu(ui)
        tool.assert_called_once_with(ui, 'wifi')
        self.assertEqual(ui.choose.call_count, 2)
        ui.run_job.assert_not_called()

    def test_failed_connection_allows_another_connection_method(self):
        ui = self.ui(['wifi', 'dhcp', None])
        with patch('network.network_tool', side_effect=[OSError('Адаптер недоступен.'), None]) as tool:
            network.network_menu(ui)
        self.assertEqual([call.args[1] for call in tool.call_args_list], ['wifi', 'dhcp'])
        ui.message.assert_called_once_with('Подключение к сети', 'Адаптер недоступен.')

    def test_invalid_target_returns_to_menu_for_correction(self):
        ui = self.ui(['target', None])
        ui.text_input.return_value = 'https://deb.debian.org/debian'
        network.network_menu(ui)
        ui.run_job.assert_not_called()
        self.assertIn('без протокола', ui.message.call_args.args[1])

    def test_power_request_propagates_to_callers(self):
        class PowerRequest(Exception):
            pass
        ui = self.ui(['wifi'])
        with patch('network.network_tool', side_effect=PowerRequest):
            with self.assertRaises(PowerRequest):
                network.network_menu(ui)
        ui.message.assert_not_called()


class NetworkActionTests(unittest.TestCase):
    def setUp(self):
        self.ui = Mock()
        self.ui.demo = False

    def test_demo_does_not_touch_devices_or_request_credentials(self):
        self.ui.demo = True
        for action in ('wifi', 'dhcp', 'network', 'target', 'profiles'):
            with self.subTest(action=action), patch('network.wired_interfaces') as interfaces, \
                    patch('wifi.wifi_menu') as wifi:
                network.network_tool(self.ui, action)
                interfaces.assert_not_called()
                wifi.assert_not_called()
        self.ui.choose.assert_not_called()
        self.ui.text_input.assert_not_called()
        self.ui.run_job.assert_not_called()

    def test_wifi_uses_existing_password_and_scan_flow(self):
        with patch('wifi.wifi_menu') as wifi:
            network.network_tool(self.ui, 'wifi')
        wifi.assert_called_once_with(self.ui)

    def test_no_wired_adapter_is_explained_without_starting_dhcp(self):
        with patch('network.wired_interfaces', return_value=[]):
            network.network_tool(self.ui, 'dhcp')
        self.ui.choose.assert_not_called()
        self.ui.run_job.assert_not_called()
        self.assertIn('адаптер не найден', self.ui.message.call_args.args[1])

    def test_cancelled_adapter_selection_does_not_start_dhcp(self):
        self.ui.choose.return_value = None
        with patch('network.wired_interfaces', return_value=['eth0']), patch('network.dhcp_job') as dhcp:
            network.network_tool(self.ui, 'dhcp')
        dhcp.assert_not_called()
        self.ui.run_job.assert_not_called()

    def test_dhcp_uses_only_the_explicitly_selected_adapter(self):
        self.ui.choose.return_value = 'enp2s0'
        job = Mock()
        with patch('network.wired_interfaces', return_value=['eth0', 'enp2s0']), \
                patch('network.dhcp_job', return_value=job) as dhcp:
            network.network_tool(self.ui, 'dhcp')
        self.assertEqual(self.ui.choose.call_args.args[1], [('eth0', 'eth0'), ('enp2s0', 'enp2s0')])
        dhcp.assert_called_once_with('enp2s0')
        self.ui.run_job.assert_called_once_with('Подключение сети', job)

    def test_network_diagnosis_uses_the_existing_layered_checks(self):
        network.network_tool(self.ui, 'network')
        self.ui.run_job.assert_called_once_with('Проверка сети', network.network_job)

    def test_saved_profiles_use_full_settings_manager(self):
        with patch('network_settings.manager') as manager:
            network.network_tool(self.ui, 'profiles')
        manager.assert_called_once_with(self.ui)
        manager.return_value.main.assert_called_once()

    def test_quick_dhcp_uses_new_transient_profile_backend(self):
        with patch('network_runtime.temporary_dhcp_job', return_value='worker') as worker:
            self.assertEqual(network.dhcp_job('enp2s0'), 'worker')
        worker.assert_called_once_with('enp2s0')

    def test_cancelled_target_input_does_not_start_a_job(self):
        self.ui.text_input.return_value = None
        network.network_tool(self.ui, 'target')
        self.ui.run_job.assert_not_called()

    def test_target_check_uses_the_entered_repository_name(self):
        self.ui.text_input.return_value = 'deb.debian.org'
        job = Mock()
        with patch('network.target_job', return_value=job) as target:
            network.network_tool(self.ui, 'target')
        target.assert_called_once_with('deb.debian.org')
        self.ui.run_job.assert_called_once_with('Проверка узла', job)


class ToolsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import menu
        except ImportError as error:
            raise unittest.SkipTest('Live interface dependencies unavailable: ' + str(error))
        cls.menu = menu

    def test_common_connection_menu_returns_to_tools(self):
        ui = Mock()
        ui.choose.side_effect = ['connection', None]
        with patch('menu.network_menu') as connect:
            self.menu.tools_menu(ui, Path('/medium'), '/dev/live')
        connect.assert_called_once_with(ui)
        self.assertTrue(all(call.args[0] == 'Инструменты' for call in ui.choose.call_args_list))

    def test_existing_shortcuts_share_connection_dispatch(self):
        ui = Mock()
        ui.choose.side_effect = ['dhcp', 'wifi', 'target', None]
        with patch('menu.network_tool') as tool:
            self.menu.tools_menu(ui, Path('/medium'), '/dev/live')
        self.assertEqual([call.args[1] for call in tool.call_args_list], ['dhcp', 'wifi', 'target'])


if __name__ == '__main__':
    unittest.main()
