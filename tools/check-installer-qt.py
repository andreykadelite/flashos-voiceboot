"""Non-destructive Qt keyboard, layout, modal and asynchronous-state tests."""
from pathlib import Path
import json
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import Qt, QPoint, QThread, QTimer
from PySide6.QtTest import QTest
from PySide6.QtGui import QTextCursor
import installer.gui as gui
from installer.dialogs import ConfirmErase, InfoDialog
from tests.test_installer import usb

root = Path(__file__).resolve().parents[1]
app = QApplication([])
disks = [usb(model='Тестовая флешка A'), usb(number=10, model='Тестовая флешка B', path='other', pnp='other')]
results = []
with patch.object(gui, 'inventory', return_value=disks):
    window = gui.MainWindow()
    assert not window.install.isEnabled() and not window.verify.isEnabled()
    window.scan_timer.stop()
    window.show()
    window.activateWindow()
    for _ in range(20):
        app.processEvents()
        QTest.qWait(20)
    assert window.selected() is None, 'Multiple drives must require selection'
    window.drives.setCurrentIndex(2)
    assert window.selected()['number'] == 10
    window.scanned([disks[0]])
    assert window.selected() is None, 'Removed device must never select a different USB automatically'
    window.drives.setCurrentIndex(1)
    assert window.selected()['number'] == 9
    for width, height in ((640, 480), (800, 600), (1000, 800), (1280, 900)):
        window.resize(width, height)
        app.processEvents()
        for widget in window.action_buttons + [window.style, window.filesystem, window.drives]:
            assert widget.width() > 0 and widget.isEnabled()
            assert widget.geometry().right() <= widget.parentWidget().width(), widget.accessibleName()
        window.log.setFocus()
        app.processEvents()
        viewport = window.centralWidget().viewport()
        point = window.log.mapTo(viewport, QPoint(0, 0))
        if point.y() >= viewport.height():
            window.grab().save(str(root / 'dist/installer-focus-failure.png'))
            print('Focus details:', width, point.y(), viewport.height(), app.focusWidget())
        assert point.y() < viewport.height(), 'Keyboard focus must scroll the log into view'
        window.drives.setFocus()
        app.processEvents()
        window.grab().save(str(root / f'dist/installer-qt-{width}x{height}.png'))
        results.append(dict(width=width, height=height, focus_scroll=True, buttons_inside=True))
    dialog = ConfirmErase(disks[0], 'install', 'MBR', 'exFAT', window)
    dialog.show()
    app.processEvents()
    assert not dialog.confirm.isEnabled()
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert dialog.result() == QDialog.Rejected
    dialog = ConfirmErase(disks[0], 'install', 'MBR', 'exFAT', window)
    dialog.show()
    app.processEvents()
    dialog.agree.setFocus()
    QTest.keyClick(dialog.agree, Qt.Key_Space)
    assert dialog.confirm.isEnabled()
    dialog.grab().save(str(root / 'dist/installer-confirm.png'))
    dialog.reject()
    window.append_log('Контрольная строка журнала')
    cursor = window.log.textCursor()
    cursor.movePosition(QTextCursor.End)
    cursor.select(QTextCursor.LineUnderCursor)
    window.log.setTextCursor(cursor)
    selected = cursor.selectedText()
    window.append_log('Новое событие во время чтения')
    assert window.log.textCursor().selectedText() == selected
    window.on_event(dict(kind='phase', phase='copy', message='Тест сообщения о записи', percent=None))
    window.on_event(dict(kind='progress', phase='copy', message='Тест прогресса', percent=37.5, bytes=100_000_000))
    assert window.progress.value() == 38 and window.progress.maximum() == 100 and 'МиБ/с' in window.metrics.text()
    window.on_event(dict(kind='complete', message='Проверка завершена'))
    assert window.progress.text().startswith('100.0%')
    with patch.object(gui, 'is_admin', return_value=True), patch.object(gui, 'JobThread', side_effect=OSError('test denied')), patch.object(gui.QMessageBox, 'warning'):
        window.start_action('verify')
        assert not window.busy
    info = InfoDialog('Компоненты', window, bundle=window.bundle)
    info.show()
    app.processEvents()
    info.grab().save(str(root / 'dist/installer-components.png'))
    info.reject()
    class SlowScan(QThread):
        def run(self):
            self.msleep(250)
    slow = SlowScan(window)
    window.scan = slow
    slow.start()
    QTest.qWait(20)
    closed = []
    app.lastWindowClosed.connect(lambda: closed.append(True))
    with patch.object(gui, 'is_admin', return_value=False), patch.object(gui, 'elevate') as elevated:
        window.start_action('verify')
        assert elevated.called
    assert window.isVisible(), 'Closing must wait for discovery without hiding the last window'
    QTimer.singleShot(2000, lambda: app.exit(99))
    result = app.exec()
    assert result == 0 and closed and not window.isVisible(), 'Last-window shutdown must quit the event loop after the worker finishes'
(root / 'dist/installer-qt-verification.json').write_text(json.dumps({'layouts':results,'multi_usb':True,'confirmation':True,'log_selection':True,'progress_and_announcement':True,'preparation_error':True}, ensure_ascii=False, indent=2), encoding='utf-8')
print('Qt checks passed: layout, keyboard, confirmation, progress, log selection, error handling.')
