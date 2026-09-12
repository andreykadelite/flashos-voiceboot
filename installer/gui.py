"""Qt Widgets interface: UIA names, native focus, responsive layout, live worker events."""
import ctypes
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import (QAccessible, QAccessibleAnnouncementEvent, QDesktopServices,
                           QKeySequence, QShortcut)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QGridLayout, QLabel, QComboBox, QGroupBox,
                               QProgressBar, QPlainTextEdit, QScrollArea, QFileDialog,
                               QMessageBox, QDialog)
from installer.backend import Operation
from installer.accessibility import KeyboardButton, ReadOnlyText
from installer.progress import ProgressState
from installer.bundle import Bundle
from installer.dialogs import ConfirmErase, InfoDialog, label
from installer.model import TITLE, VERSION, SYSTEM_VERSION, identity, fingerprint, disk_label, unavailable_reason
from installer.windows import inventory, is_admin


class ScanThread(QThread):
    result = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            self.result.emit(inventory())
        except Exception as error:
            self.failed.emit(str(error))


class JobThread(QThread):
    event = Signal(object)

    def __init__(self, request, parent):
        super().__init__(parent)
        self.operation = Operation(request, self.event.emit)

    def run(self):
        self.operation.run()


STYLE = '''
QMainWindow, QScrollArea { background: #eef3f6; }
QDialog { background: #eef3f6; color: #172e42; }
QCheckBox { color: #172e42; }
QTabWidget::pane { background: #eef3f6; border: 1px solid #8297a7; }
QTabBar::tab { color: #172e42; background: #dde7ee; border: 1px solid #8297a7; padding: 8px 12px; }
QTabBar::tab:selected { background: white; border-bottom-color: white; }
QTabBar::tab:hover { background: #cbdde8; }
QWidget#content { background: #eef3f6; }
QLabel { color: #172e42; }
QLabel#title { font-size: 26px; font-weight: 700; color: #123b50; }
QLabel#subtitle { color: #475b6a; }
QGroupBox { background: white; color: #172e42; border: 1px solid #9aabb7; border-radius: 9px; margin-top: 13px; padding: 16px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 5px; }
QComboBox, QPlainTextEdit, QLineEdit, QTableWidget, QTextBrowser { background: white; color: #172e42; border: 1px solid #8297a7; border-radius: 4px; padding: 6px; selection-background-color: #096a70; selection-color: white; }
QComboBox { min-height: 28px; padding-right: 28px; }
QPushButton { background: #f6f9fb; color: #183a50; border: 1px solid #778e9e; border-radius: 5px; padding: 9px 14px; min-height: 22px; }
QPushButton:hover { background: #e0edf2; }
QPushButton#install { background: #075e66; color: white; font-weight: 600; border: 1px solid #075e66; }
QPushButton#install:hover { background: #084b52; }
QPushButton:disabled { background: #e7ebee; color: #596873; border-color: #a8b4bc; }
QPushButton#install:disabled, QComboBox:disabled { background: #e7ebee; color: #596873; border-color: #a8b4bc; }
QPushButton:focus, QComboBox:focus, QPlainTextEdit:focus, QTextBrowser:focus, QTableWidget:focus, QLineEdit:focus, QCheckBox:focus, QProgressBar:focus { border: 3px solid #1766ae; }
QProgressBar { border: 1px solid #8297a7; border-radius: 4px; background: white; color: #163448; text-align: center; min-height: 23px; }
QProgressBar::chunk { background: #9cd4d5; }
'''


def high_contrast():
    class HIGHCONTRAST(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint), ('dwFlags', ctypes.c_uint), ('scheme', ctypes.c_wchar_p)]
    state = HIGHCONTRAST()
    state.cbSize = ctypes.sizeof(state)
    ctypes.windll.user32.SystemParametersInfoW(0x42, state.cbSize, ctypes.byref(state), 0)
    return bool(state.dwFlags & 1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(TITLE)
        self.resize(1000, 800)
        self.setMinimumSize(460, 400)
        self.disks, self.scan, self.job = [], None, None
        self.busy = False
        self.closing = False
        self.scanned_once = False
        self.scan_error = ''
        self.stats = ProgressState()
        self.terminal_event = None
        self.bundle = Bundle()
        self._layout_columns = 0
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName('content')
        self.layout = QVBoxLayout(content)
        self.layout.setContentsMargins(22, 18, 22, 18)
        self.layout.setSpacing(12)
        heading = label(TITLE)
        heading.setObjectName('title')
        self.layout.addWidget(heading)
        subtitle = label(f'Говорящая загрузочная флешка · FlashOS {SYSTEM_VERSION} · Установщик {VERSION}')
        subtitle.setObjectName('subtitle')
        self.layout.addWidget(subtitle)
        self.summary = label('Поиск подключённых USB-накопителей…')
        self.layout.addWidget(self.summary)

        drive_box = QGroupBox('1. Выберите флешку')
        drive_layout = QVBoxLayout(drive_box)
        row = QHBoxLayout()
        self.drives = QComboBox()
        self.drives.setAccessibleName('USB-накопитель для установки или форматирования')
        self.drives.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.drives.setMinimumContentsLength(16)
        self.refresh = self.button('Обновить', self.start_scan, 'Обновить список USB-накопителей, F5')
        row.addWidget(self.drives, 1)
        row.addWidget(self.refresh)
        drive_layout.addLayout(row)
        self.details = ReadOnlyText()
        self.details.setAccessibleName('Сведения о выбранном накопителе')
        self.details.setMaximumHeight(90)
        drive_layout.addWidget(self.details)
        self.drives.currentIndexChanged.connect(self.selected_changed)
        self.layout.addWidget(drive_box)

        options = QGroupBox('2. Параметры и действия')
        options_layout = QVBoxLayout(options)
        self.options_grid = QGridLayout()
        self.style_label = QLabel('Разметка:')
        self.style = QComboBox()
        self.style.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.style.setMinimumContentsLength(12)
        self.style.addItems(['MBR — BIOS и UEFI', 'GPT — для больших накопителей'])
        self.style.setAccessibleName('Тип разметки накопителя')
        self.style_label.setBuddy(self.style)
        self.fs_label = QLabel('Обычное форматирование:')
        self.fs_label.setWordWrap(True)
        self.filesystem = QComboBox()
        self.filesystem.addItems(['exFAT', 'NTFS', 'FAT32'])
        self.filesystem.setAccessibleName('Файловая система для кнопки Форматировать')
        self.fs_label.setBuddy(self.filesystem)
        options_layout.addLayout(self.options_grid)
        self.actions_grid = QGridLayout()
        self.install = self.button('Установить', lambda: self.start_action('install'), 'Установить FlashOS с удалением всех данных')
        self.install.setObjectName('install')
        self.verify = self.button('Проверить', lambda: self.start_action('verify'), 'Проверить файлы установленной FlashOS')
        self.format = self.button('Форматировать', lambda: self.start_action('format'), 'Удалить все данные и создать обычную USB-флешку')
        self.iso = self.button('Открыть iso', self.open_iso, 'Открыть папку ISO выбранной флешки')
        self.components = self.button('Состав EXE', self.show_components, 'Программы, версии и лицензии')
        self.help = self.button('Справка', self.show_help, 'Справка, F1')
        self.action_buttons = [self.install, self.verify, self.format, self.iso, self.components, self.help]
        options_layout.addLayout(self.actions_grid)
        options_layout.addWidget(label('Установка и форматирование удаляют все файлы выбранной флешки. Сначала сохраните нужные данные.'))
        self.layout.addWidget(options)

        progress_box = QGroupBox('3. Ход работы')
        progress_layout = QVBoxLayout(progress_box)
        self.stage_label = label('Готов к работе')
        progress_layout.addWidget(self.stage_label)
        self.status = ReadOnlyText('Готов к работе. Выберите USB-накопитель.')
        self.status.setAccessibleName('Состояние операции, F6')
        self.status.setAccessibleDescription(self.status.accessibleDescription() + ' F6 обновляет снимок состояния, повторное F6 читает его вслух.')
        self.status.setMaximumHeight(110)
        progress_layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setAccessibleName('Прогресс текущего этапа')
        self.progress.setFocusPolicy(Qt.StrongFocus)
        self.progress.setValue(0)
        progress_layout.addWidget(self.progress)
        self.metrics = label('Время: —    Скорость: —')
        progress_layout.addWidget(self.metrics)
        status_row = QHBoxLayout()
        self.stop_button = self.button('Остановить', self.stop_job, 'Остановить операцию после безопасного завершения текущей записи')
        self.stop_button.setEnabled(False)
        self.save = self.button('Сохранить журнал', self.save_log, 'Сохранить журнал, Ctrl+S')
        status_row.addWidget(self.stop_button)
        status_row.addStretch()
        status_row.addWidget(self.save)
        progress_layout.addLayout(status_row)
        self.log = ReadOnlyText()
        self.log.setAccessibleName('Журнал работы в реальном времени, Ctrl+L')
        self.log.setMinimumHeight(150)
        self.log.document().setMaximumBlockCount(10000)
        progress_layout.addWidget(label('F6 — обновить снимок состояния · Ctrl+L — журнал · Ctrl+End — последняя запись. При чтении снимок сохраняется.'))
        progress_layout.addWidget(self.log, 1)
        self.layout.addWidget(progress_box, 1)
        self.layout.addWidget(label('Разработка: Зотов Андрей Анатольевич · Telegram @zotovsmol · andreykadelite@ya.ru'))
        scroll.setWidget(content)
        self.setCentralWidget(scroll)
        QApplication.instance().focusChanged.connect(self.keep_focus_visible)
        if not high_contrast():
            self.setStyleSheet(STYLE)
        self.reflow()
        order = [self.drives, self.refresh, self.details, self.style, self.filesystem,
                 *self.action_buttons, self.status, self.progress, self.stop_button, self.save, self.log]
        for first, second in zip(order, order[1:]):
            QWidget.setTabOrder(first, second)
        self.drives.setFocus()
        for keys, callback in [('F1', self.show_help), ('F5', self.start_scan), ('F6', self.read_status), ('Ctrl+L', self.log.setFocus), ('Ctrl+S', self.save_log)]:
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(callback)
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(10000)
        self.scan_timer.timeout.connect(self.start_scan)
        self.scan_timer.start()
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(1000)
        self.progress_timer.timeout.connect(self.render_progress)
        self.selected_changed()
        QTimer.singleShot(0, self.start_scan)
        self.append_log('Встроенный комплект найден. Интернет для установки не требуется.')
        self.append_log('Права администратора: ' + ('есть.' if is_admin() else 'отсутствуют. Для операций запустите установщик с правами администратора.'))

    def button(self, text, callback, accessible):
        button = KeyboardButton(text)
        button.setAccessibleName(accessible)
        button.clicked.connect(callback)
        button.setAutoDefault(False)
        return button

    def keep_focus_visible(self, old, new):
        scroll = self.centralWidget()
        if new is not None and scroll.isAncestorOf(new):
            scroll.ensureWidgetVisible(new, 12, 12)
            # Reflow and wrapped labels may settle after focusChanged is emitted.
            QTimer.singleShot(0, self.scroll_to_focus)

    def scroll_to_focus(self):
        widget = QApplication.focusWidget()
        if widget is not None and self.centralWidget().isAncestorOf(widget):
            self.centralWidget().ensureWidgetVisible(widget, 12, 12)

    def reflow(self):
        columns = 3 if self.width() >= 800 else 2
        if columns == self._layout_columns:
            return
        self._layout_columns = columns
        for grid in (self.options_grid, self.actions_grid):
            while grid.count():
                grid.takeAt(0)
        if columns == 3:
            for column, widget in enumerate((self.style_label, self.style, self.fs_label, self.filesystem)):
                self.options_grid.addWidget(widget, 0, column)
        else:
            for i, widget in enumerate((self.style_label, self.style, self.fs_label, self.filesystem)):
                self.options_grid.addWidget(widget, i // 2, i % 2)
        for i, button in enumerate(self.action_buttons):
            self.actions_grid.addWidget(button, i // columns, i % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'action_buttons'):
            self.reflow()

    def selected(self):
        index = self.drives.currentData()
        return next((d for d in self.disks if fingerprint(d) == index), None)

    def selected_changed(self):
        disk = self.selected()
        if not disk:
            self.details.snapshot('Выберите флешку в списке. Внутренние диски не предлагаются.')
        else:
            volumes = ', '.join(v['letter'] + ': ' + (v['label'] or 'без метки') + ' ' + v['filesystem'] for v in disk['volumes'])
            free = sum(v['free'] for v in disk['volumes']) / 1024**3
            self.details.snapshot(disk_label(disk) + '\nСерийный номер: ' + (disk['serial'] or 'не сообщён') + f' · Свободно: {free:.1f} ГиБ' + '\nРазделы: ' + (volumes or 'нет') + '\n' + (unavailable_reason(disk, style='GPT') or 'USB-накопитель доступен.'))
            if disk['size'] >= 2 * 1024**4:
                self.style.setCurrentIndex(1)
        for button in (self.install, self.verify, self.format, self.iso):
            button.setEnabled(bool(disk) and not self.busy)

    def start_scan(self):
        if self.closing or self.busy or QApplication.activeModalWidget() or self.drives.view().isVisible() or (self.scan and self.scan.isRunning()):
            return
        self.scan = ScanThread(self)
        self.scan.result.connect(self.scanned)
        self.scan.failed.connect(self.scan_failed)
        self.scan.finished.connect(self.scan_finished)
        self.scan.finished.connect(self.scan.deleteLater)
        self.scan.start()

    def scan_finished(self):
        self.scan = None

    def scanned(self, disks):
        if self.busy or QApplication.activeModalWidget() or self.drives.view().isVisible():
            return
        self.scan_error = ''
        selected = self.drives.currentData()
        usb = [d for d in disks if d['bus'] == 'USB']
        old = [(fingerprint(d), disk_label(d)) for d in self.disks]
        self.disks = usb
        self.summary.setText(f'USB-накопителей: {len(usb)}. Внутренние диски скрыты. Система в комплекте: {SYSTEM_VERSION}.')
        if not self.scanned_once or old != [(fingerprint(d), disk_label(d)) for d in usb]:
            self.drives.blockSignals(True)
            self.drives.clear()
            self.drives.addItem('Выберите USB-накопитель' if usb else 'USB-накопители не найдены', None)
            for disk in usb:
                self.drives.addItem(disk_label(disk), fingerprint(disk))
            match = self.drives.findData(selected) if selected else -1
            # Never silently replace a removed selected device with another USB.
            self.drives.setCurrentIndex(match if match > 0 else (1 if not self.scanned_once and len(usb) == 1 else 0))
            self.drives.blockSignals(False)
        self.scanned_once = True
        self.selected_changed()

    def scan_failed(self, message):
        self.summary.setText('Не удалось обновить список USB-накопителей. Нажмите F5 для повторной попытки.')
        if message != self.scan_error:
            self.append_log(message)
            self.scan_error = message

    def start_action(self, action):
        disk = self.selected()
        if self.closing or self.busy or not disk:
            return
        style = 'MBR' if self.style.currentIndex() == 0 else 'GPT'
        filesystem = self.filesystem.currentText()
        reason = unavailable_reason(disk, action, style, filesystem)
        if reason:
            QMessageBox.warning(self, TITLE, reason)
            return
        if not is_admin():
            QMessageBox.warning(self, TITLE, 'Для операции нужны права администратора. Закройте установщик и запустите его от имени администратора.')
            return
        if action != 'verify' and ConfirmErase(disk, action, style, filesystem, self).exec() != QDialog.Accepted:
            return
        request = dict(action=action, target=identity(disk), style=style, filesystem=filesystem, erase_confirmed=action != 'verify')
        try:
            self.job = JobThread(request, self)
        except Exception as error:
            QMessageBox.warning(self, TITLE, 'Не удалось подготовить защищённый каталог операции.\n\n' + str(error))
            return
        self.busy = True
        self.terminal_event = None
        self.stats.reset()
        self._logged_checkpoint = -1
        self.stats.message = 'Подготовка операции. Проверяем устройство и права доступа.'
        self.progress.setRange(0, 0)
        self.render_progress()
        # Move only the control that is about to disappear from keyboard navigation.
        if QApplication.focusWidget() in (self.drives, self.refresh, self.style, self.filesystem, self.install, self.verify, self.format, self.iso):
            self.status.setFocus()
        for widget in (self.drives, self.refresh, self.style, self.filesystem, self.install, self.verify, self.format, self.iso):
            widget.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText('Остановить')
        self.append_log(('Проверка' if action == 'verify' else 'Установка' if action == 'install' else 'Форматирование') + ': ' + disk_label(disk))
        self.job.event.connect(self.on_event)
        self.job.finished.connect(self.job_finished)
        self.progress_timer.start()
        self.job.start()

    def job_finished(self):
        if not self.terminal_event:
            self.on_event(dict(kind='error', message='Рабочий процесс завершился без полного отчёта. Проверьте журнал; при прерывании записи повторите установку.'))
        self.busy = False
        self.progress_timer.stop()
        if self.stop_button.hasFocus():
            self.status.setFocus()
        self.stop_button.setEnabled(False)
        for widget in (self.drives, self.refresh, self.style, self.filesystem):
            widget.setEnabled(True)
        self.selected_changed()
        self.start_scan()
        job, self.job = self.job, None
        if job:
            job.deleteLater()

    def append_log(self, text):
        self.log.append_entry(datetime.now().strftime('%H:%M:%S') + '  ' + text)

    def announce(self, text, explicit=False):
        if not explicit and (QApplication.activeModalWidget() or isinstance(QApplication.focusWidget(), (QPlainTextEdit,))):
            return
        event = QAccessibleAnnouncementEvent(self.status, text)
        QAccessible.updateAccessibility(event)

    def on_event(self, event):
        kind, message = event['kind'], event.get('message', '')
        if kind == 'finished':
            self.append_log(message)
            return
        if kind in ('phase', 'complete', 'error', 'cancelled'):
            self.append_log(message)
            self.announce(message, explicit=kind in ('complete', 'error', 'cancelled'))
        elif kind == 'log':
            self.append_log(message)
        if kind in ('complete', 'error', 'cancelled'):
            self.terminal_event = event
        self.stats.accept(event)
        if kind == 'progress' and event.get('phase') == 'copy' and self.stats.percent is not None:
            checkpoint = int(self.stats.percent) // 10
            if checkpoint > getattr(self, '_logged_checkpoint', -1):
                self._logged_checkpoint = checkpoint
                self.append_log(f'Запись на USB: {self.stats.percent:.1f}%. {message}')
        self.render_progress()

    def render_progress(self):
        percent = self.stats.percent
        self.progress.setRange(0, 100 if percent is not None or self.stats.terminal else 0)
        if percent is not None:
            self.progress.setValue(round(percent))
            self.progress.setFormat(f'{percent:.1f}% · осталось {100-percent:.1f}% этапа')
        elif self.stats.terminal:
            self.progress.setValue(0)
            self.progress.setFormat('Операция не завершена')
        self.progress.setAccessibleDescription(self.stats.summary())
        self.metrics.setText(self.stats.metrics())
        self.stage_label.setText(self.stats.message + ('\nСейчас: ' + self.stats.file if self.stats.file and not self.stats.terminal else ''))
        self.status.snapshot(self.stats.summary())

    def read_status(self):
        self.status.snapshot(self.stats.summary(), force=True)
        already_focused = self.status.hasFocus()
        self.status.setFocus()
        if already_focused:
            self.announce(self.stats.summary(), explicit=True)

    def stop_job(self):
        if self.busy and self.job:
            if self.stats.stopping or self.stats.terminal:
                return
            try:
                self.job.operation.cancel()
            except OSError as error:
                self.append_log('Не удалось передать запрос остановки: ' + str(error))
                return
            self.stats.stopping = True
            if self.stop_button.hasFocus():
                self.status.setFocus()
            self.stop_button.setEnabled(False)
            self.stop_button.setText('Ожидание остановки…')
            self.append_log('Запрос остановки принят. Не отключайте флешку до завершения операции.')
            self.render_progress()
            self.announce(self.stats.summary())

    def open_iso(self):
        disk = self.selected()
        if disk and not self.busy:
            for volume in disk['volumes']:
                path = Path(volume['letter'] + ':\\iso')
                if volume['label'] == 'FLASHOS' and path.is_dir():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
                    return
            QMessageBox.information(self, TITLE, 'Папка iso на выбранном накопителе не найдена. Сначала установите FlashOS.')

    def save_log(self):
        if QApplication.activeModalWidget():
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Сохранить журнал', 'FlashOS-Installer.log', 'Текстовый журнал (*.log *.txt)')
        if path:
            try:
                Path(path).write_text(self.log.toPlainText(), encoding='utf-8-sig')
            except OSError as error:
                QMessageBox.warning(self, TITLE, str(error))

    def show_help(self):
        if not QApplication.activeModalWidget():
            InfoDialog('Справка', self, text=self.bundle.text('HELP-RU.md')).exec()

    def show_components(self):
        if not QApplication.activeModalWidget():
            InfoDialog('Компоненты', self, bundle=self.bundle).exec()

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, TITLE, 'Операция ещё выполняется. Нажмите «Остановить» и дождитесь итогового сообщения.')
            event.ignore()
            return
        if self.scan and self.scan.isRunning():
            self.closing = True
            self.scan_timer.stop()
            self.scan.finished.connect(self.close)
            event.ignore()
            return
        event.accept()
