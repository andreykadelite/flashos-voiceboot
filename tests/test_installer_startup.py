"""Startup privilege transition must finish before the selectable menu exists."""
import importlib.util
import os
import sys
import unittest
from unittest.mock import patch, MagicMock


@unittest.skipUnless(os.name == 'nt' and importlib.util.find_spec('PySide6'), 'Windows Qt startup')
class StartupTests(unittest.TestCase):
    def run_startup(self, admin, elevation_error=None):
        from installer import entry, windows, gui
        from PySide6 import QtWidgets
        order = []
        application = MagicMock()
        menu = MagicMock()
        application.exec.return_value = 0
        def elevate():
            order.append('elevate')
            if elevation_error:
                raise elevation_error
        def create_application(*args):
            order.append('application')
            return application
        def create_window():
            order.append('window')
            return menu
        with patch.object(sys, 'excepthook'), \
             patch.object(windows, 'is_admin', return_value=admin), \
             patch.object(windows, 'elevate', side_effect=elevate) as elevated, \
             patch.object(QtWidgets, 'QApplication', side_effect=create_application), \
             patch.object(QtWidgets.QMessageBox, 'critical') as critical, \
             patch.object(gui, 'MainWindow', side_effect=create_window) as window:
            code = entry.main()
        return code, order, elevated, critical, window, application, menu

    def test_ordinary_launch_requests_rights_before_creating_any_menu(self):
        code, order, elevated, critical, window, app, menu = self.run_startup(False)
        self.assertEqual(0, code)
        self.assertEqual(['elevate'], order)
        elevated.assert_called_once_with()
        window.assert_not_called()
        app.exec.assert_not_called()
        critical.assert_not_called()
        menu.show.assert_not_called()

    def test_admin_launch_opens_one_menu_without_another_elevation(self):
        code, order, elevated, critical, window, app, menu = self.run_startup(True)
        self.assertEqual(0, code)
        self.assertEqual(['application', 'window'], order)
        elevated.assert_not_called()
        window.assert_called_once_with()
        menu.show.assert_called_once_with()
        app.exec.assert_called_once_with()
        critical.assert_not_called()

    def test_cancelled_uac_reports_failure_without_menu_or_retry_loop(self):
        code, order, elevated, critical, window, app, menu = self.run_startup(False, RuntimeError('UAC cancelled'))
        self.assertEqual(1, code)
        self.assertEqual(['elevate', 'application'], order)
        elevated.assert_called_once_with()
        critical.assert_called_once()
        self.assertIn('UAC cancelled', critical.call_args.args[2])
        window.assert_not_called()
        app.exec.assert_not_called()
        menu.show.assert_not_called()
