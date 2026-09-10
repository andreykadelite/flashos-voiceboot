"""Native widget regressions; no disk writes and no desktop input injection."""
import importlib.util
import os
import unittest
from unittest.mock import patch, MagicMock

HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor, QAccessible
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog
    from installer.accessibility import ReadOnlyText, ReadOnlyBrowser, KeyboardButton
    from installer.dialogs import InfoDialog, ConfirmErase


@unittest.skipUnless(HAS_QT, 'Install PySide6 for native Qt accessibility tests')
class AccessibleTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_arrows_move_caret_and_native_accessible_text_position(self):
        for cls in (ReadOnlyText, ReadOnlyBrowser):
            view = cls()
            view.setPlainText('Первая строка\nВторая строка\nПоследняя строка')
            view.show()
            view.setFocus()
            self.app.processEvents()
            view.moveCursor(QTextCursor.End)
            end = view.textCursor().position()
            QTest.keyClick(view, Qt.Key_Up)
            self.assertLess(view.textCursor().position(), end)
            interface = QAccessible.queryAccessibleInterface(view).textInterface()
            self.assertIsNotNone(interface)
            self.assertEqual(view.textCursor().position(), interface.cursorPosition())
            QTest.keyClick(view, Qt.Key_Home, Qt.ControlModifier)
            self.assertEqual(0, interface.cursorPosition())
            QTest.keyClick(view, Qt.Key_Down, Qt.ShiftModifier)
            self.assertIn('Первая', view.textCursor().selectedText())
            original = view.toPlainText()
            QTest.keyClicks(view, 'must not edit')
            QTest.keyClick(view, Qt.Key_Delete)
            self.assertEqual(original, view.toPlainText())
            view.close()

    def test_new_log_entry_does_not_move_reader_or_selection(self):
        view = ReadOnlyText()
        view.setPlainText('one\ntwo\nthree')
        view.show()
        view.setFocus()
        self.app.processEvents()
        view.moveCursor(QTextCursor.End)
        QTest.keyClick(view, Qt.Key_Up)
        before = view.textCursor().position()
        view.append_entry('four')
        self.assertEqual(before, view.textCursor().position())
        QTest.keyClick(view, Qt.Key_Home)
        QTest.keyClick(view, Qt.Key_End, Qt.ShiftModifier)
        selected = view.textCursor().selectedText()
        view.append_entry('five')
        self.assertEqual(selected, view.textCursor().selectedText())
        view.close()

    def test_initial_log_and_end_caret_never_select_new_output(self):
        view = ReadOnlyText()
        view.append_entry('first')
        self.assertFalse(view.textCursor().hasSelection())
        self.assertEqual(0, view.textCursor().position())
        view.moveCursor(QTextCursor.End)
        end = view.textCursor().position()
        view.append_entry('second')
        self.assertFalse(view.textCursor().hasSelection())
        self.assertEqual(end, view.textCursor().position())
        view.selectAll()
        selected = view.textCursor().selectedText()
        view.append_entry('third')
        self.assertEqual(selected, view.textCursor().selectedText())

    def test_bounded_log_tracks_line_when_old_blocks_are_removed(self):
        view = ReadOnlyText()
        view.setMaximumBlockCount(4)
        view.setPlainText('one\ntwo\nthree\nfour')
        cursor = view.textCursor()
        cursor.setPosition(4)
        cursor.select(QTextCursor.BlockUnderCursor)
        view.setTextCursor(cursor)
        view.append_entry('five')
        self.assertEqual('two', view.textCursor().selectedText())

    def test_focused_status_snapshot_is_not_replaced_until_requested(self):
        view = ReadOnlyText('old\nstatus')
        view.show()
        view.setFocus()
        self.app.processEvents()
        view.snapshot('new status')
        self.assertEqual('old\nstatus', view.toPlainText())
        view.snapshot('new status', force=True)
        self.assertEqual('new status', view.toPlainText())
        view.close()

    def test_button_accepts_enter_and_space_without_dialog_default(self):
        button = KeyboardButton('Action')
        calls = []
        button.clicked.connect(lambda: calls.append(True))
        QTest.keyClick(button, Qt.Key_Return)
        QTest.keyClick(button, Qt.Key_Space)
        self.assertEqual(2, len(calls))

    def test_help_contents_arrows_and_tab_to_readable_text(self):
        dialog = InfoDialog('Справка', None, text='# Start\nintro\n\n## Keys\nfirst\nsecond\n\n## USB\nports')
        dialog.show()
        dialog.contents.setFocus()
        self.app.processEvents()
        QTest.keyClick(dialog.contents, Qt.Key_Down)
        self.assertIn('Keys', dialog.view.toPlainText())
        QTest.keyClick(dialog.contents, Qt.Key_Tab)
        self.assertTrue(dialog.view.hasFocus())
        before = dialog.view.textCursor().position()
        QTest.keyClick(dialog.view, Qt.Key_Down)
        self.assertGreater(dialog.view.textCursor().position(), before)
        QTest.keyClick(dialog.view, Qt.Key_Escape)
        self.assertFalse(dialog.isVisible())


@unittest.skipUnless(HAS_QT and os.name == 'nt', 'Windows Qt installer')
class WindowStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import installer.gui as gui
        from tests.test_installer import usb
        self.gui = gui
        self.disks = [usb(), usb(number=10, path='other', pnp='other')]
        self.patches = [patch.object(gui, 'inventory', return_value=self.disks),
                        patch.object(gui, 'Bundle'), patch.object(gui, 'high_contrast', return_value=False)]
        for item in self.patches:
            item.start()
        self.window = gui.MainWindow()
        self.window.scan_timer.stop()
        self.window.show()
        self.app.processEvents()
        if self.window.scan:
            self.window.scan.wait(5000)
        self.app.processEvents()
        self.window.drives.setCurrentIndex(1)

    def tearDown(self):
        self.window.busy = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()

    def test_removed_target_is_never_replaced_by_remaining_device(self):
        self.window.scanned(self.disks[1:])
        self.assertIsNone(self.window.selected())
        self.assertFalse(self.window.install.isEnabled())

    def test_background_inventory_keeps_combo_and_text_focus(self):
        self.window.details.setFocus()
        self.app.processEvents()
        self.window.details.moveCursor(QTextCursor.End)
        before = self.window.details.textCursor().position()
        self.window.scanned(self.disks)
        self.assertTrue(self.window.details.hasFocus())
        self.assertEqual(before, self.window.details.textCursor().position())

    def test_error_and_finished_event_do_not_unlock_or_open_modal(self):
        self.window.busy = True
        self.window.log.setFocus()
        self.app.processEvents()
        with patch.object(self.gui.QMessageBox, 'warning') as warning:
            self.window.on_event(dict(kind='error', message='test failure'))
            self.window.on_event(dict(kind='finished', message='cleanup finished'))
            self.assertTrue(self.window.busy)
            self.assertTrue(self.window.log.hasFocus())
            warning.assert_not_called()
            self.window.job_finished()
            self.assertFalse(self.window.busy)

    def test_f6_exposes_remaining_percentage_current_file_and_usb_speed(self):
        self.window.on_event(dict(kind='phase', phase='copy', message='Запись системы'))
        self.window.on_event(dict(kind='progress', phase='copy', message='filesystem.squashfs', percent=37.5, bytes=1000000, total_bytes=2000000))
        self.window.read_status()
        text = self.window.status.toPlainText()
        self.assertIn('осталось 62.5%', text)
        self.assertIn('filesystem.squashfs', text)
        self.assertIn('Запись на USB', text)
        self.assertEqual(100, self.window.progress.maximum())
        self.assertIn('filesystem.squashfs', QAccessible.queryAccessibleInterface(self.window.stage_label).text(QAccessible.Name))
        self.assertIn('Запись на USB', QAccessible.queryAccessibleInterface(self.window.metrics).text(QAccessible.Name))

    def test_closing_window_cannot_relaunch_elevation(self):
        self.window.closing = True
        with patch.object(self.gui, 'elevate') as elevate:
            self.window.start_action('verify')
            elevate.assert_not_called()
