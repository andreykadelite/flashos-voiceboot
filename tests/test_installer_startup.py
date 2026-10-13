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


class DiagnosticRoutingTests(unittest.TestCase):
    def test_self_test_routes_before_privilege_checks(self):
        from installer import entry, selftest
        with patch.object(sys, 'argv', ['entry.py', '--self-test', '--report', 'unused']), \
             patch.object(selftest, 'main', return_value=0) as diagnostic:
            self.assertEqual(0, entry.main())
        diagnostic.assert_called_once_with(['--self-test', '--report', 'unused'], compiled=False)

    def test_report_path_rejects_other_names_and_existing_reports(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from installer.selftest import report_path
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)/'build'/'installer-compat-test'
            directory.mkdir(parents=True)
            target = directory/'compiled-startup.json'
            self.assertEqual(target.resolve(), report_path(str(target)))
            with self.assertRaises(ValueError):
                report_path(str(directory/'different.json'))
            target.touch()
            with self.assertRaises(ValueError):
                report_path(str(target))

    def test_failed_diagnostic_is_reported_without_running_an_installer(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import json
        from installer import selftest
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)/'build'/'installer-compat-test'
            directory.mkdir(parents=True)
            target = directory/'compiled-startup.json'
            with patch.object(selftest, 'exercise_window', side_effect=RuntimeError('safe failure')):
                self.assertEqual(1, selftest.main(['--self-test', '--report', str(target)], compiled=True))
            report = json.loads(target.read_text(encoding='utf-8'))
            self.assertFalse(report['ok'])
            self.assertTrue(report['compiled'])
            self.assertFalse(report['disk_writes'])
            self.assertEqual('safe failure', report['error'])


class CompiledDiagnosticPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        path = Path(__file__).resolve().parents[1]/'tools/check-installer-startup.py'
        specification = importlib.util.spec_from_file_location('compiled_startup_check', path)
        cls.check = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(cls.check)

    def fixture_pe(self, payload=b''):
        import struct
        manifest = (b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1">'
                    b'<trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security>'
                    b'<requestedPrivileges><requestedExecutionLevel level="requireAdministrator" '
                    b'uiAccess="false" /></requestedPrivileges></security></trustInfo></assembly>')
        count = 2 if payload else 1
        raw_size = ((len(payload)+511)//512)*512 if payload else 0
        result = bytearray(0x600+raw_size)
        result[:2] = b'MZ'
        struct.pack_into('<I', result, 60, 0x80)
        result[0x80:0x84] = b'PE\0\0'
        struct.pack_into('<HH', result, 0x84, 0x8664, count)
        struct.pack_into('<H', result, 0x94, 240)
        struct.pack_into('<H', result, 0x98, 0x20b)
        struct.pack_into('<II', result, 0x98+112+16, 0x1000, 0x400)
        table = 0x98+240
        result[table:table+5] = b'.rsrc'
        struct.pack_into('<IIII', result, table+8, 0x400, 0x1000, 0x400, 0x200)
        if payload:
            struct.pack_into('<IIII', result, table+48, raw_size, 0x2000, raw_size, 0x600)
            result[0x600:0x600+len(payload)] = payload
        for relative, identifier, child in ((0, 24, 0x80000018), (24, 1, 0x80000030), (48, 1033, 72)):
            struct.pack_into('<HH', result, 0x200+relative+12, 0, 1)
            struct.pack_into('<II', result, 0x200+relative+16, identifier, child)
        struct.pack_into('<II', result, 0x200+72, 0x1100, len(manifest))
        result[0x300:0x300+len(manifest)] = manifest
        return bytes(result)

    def fixture_onefile(self):
        import struct
        inner = self.fixture_pe()
        payload = b'KAX'+'FlashOS-Setup.exe\0'.encode('utf-16le')+struct.pack('<Q', len(inner))+inner+b'\0\0'
        return self.fixture_pe(payload)

    def test_only_two_validated_manifest_attributes_change(self):
        core = self.fixture_onefile()
        changed, ranges = self.check.patch_core(core)
        self.assertEqual(len(core), len(changed))
        self.assertEqual(2, len(ranges))
        self.assertEqual(0, changed.count(self.check.ATTRIBUTE))
        self.assertEqual(2, changed.count(self.check.REPLACEMENT))
        allowed = {i for row in ranges for i in range(row['offset'], row['offset']+row['bytes'])}
        self.assertTrue(all(a == b or i in allowed for i, (a, b) in enumerate(zip(core, changed))))

    def test_manifest_patch_refuses_missing_or_additional_attributes(self):
        with self.assertRaises(ValueError):
            self.check.patch_core(self.fixture_pe())
        core = bytearray(self.fixture_onefile())
        core[-len(self.check.ATTRIBUTE):] = self.check.ATTRIBUTE
        with self.assertRaises(ValueError):
            self.check.patch_core(bytes(core))

    def test_uncompressed_inner_exe_must_be_present(self):
        core = self.fixture_onefile().replace(b'KAX', b'KAY', 1)
        with self.assertRaises(ValueError):
            self.check.patch_core(core)

    def test_copy_preserves_complete_overlay_and_production(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        core = self.fixture_onefile()
        overlay = b'PK\3\4 preserved full archive \0 bytes'*20
        with TemporaryDirectory() as temporary:
            source = Path(temporary)/'production.exe'
            target = Path(temporary)/'qa.exe'
            source.write_bytes(core+overlay)
            report = self.check.copy_for_qa(source, target)
            self.assertTrue(report['overlay_unchanged'])
            self.assertEqual(core+overlay, source.read_bytes())
            self.assertEqual(overlay, target.read_bytes()[len(core):])


@unittest.skipUnless(os.name == 'nt' and importlib.util.find_spec('PySide6'), 'Windows Qt startup')
class DiagnosticBoundaryTests(unittest.TestCase):
    def test_all_device_boundaries_are_replaced_and_restored(self):
        from installer import selftest, windows, gui, backend
        original = windows.inventory
        with selftest.device_guards() as (guarded_gui, calls):
            self.assertIs(gui, guarded_gui)
            self.assertEqual([], windows.inventory())
            self.assertEqual([], gui.inventory())
            self.assertEqual([], backend.inventory())
            for callback in (windows.elevate, windows.powershell, backend.powershell,
                             gui.JobThread, gui.MainWindow.start_action, backend.Operation):
                with self.assertRaises(RuntimeError):
                    callback(None, None)
            self.assertEqual(3, calls['inventory_mock_calls'])
            self.assertEqual(6, len(calls['blocked_actions']))
        self.assertIs(original, windows.inventory)
