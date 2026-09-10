import re
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QDialogButtonBox,
                               QCheckBox, QTabWidget, QWidget, QTableWidget, QTableWidgetItem,
                               QLineEdit, QHeaderView, QComboBox, QHBoxLayout)
from installer.model import disk_label, TITLE
from installer.accessibility import ReadOnlyText, ReadOnlyBrowser, KeyboardButton


def label(text):
    widget = QLabel(text)
    widget.setWordWrap(True)
    return widget


class ConfirmErase(QDialog):
    def __init__(self, disk, action, style, filesystem, parent):
        super().__init__(parent)
        self.setWindowTitle('Подтверждение удаления данных')
        self.setMinimumWidth(430)
        self.resize(580, 370)
        layout = QVBoxLayout(self)
        layout.addWidget(label('Все разделы и файлы на выбранной флешке будут удалены. Сохраните нужные данные на другой диск.'))
        details = ReadOnlyText()
        details.setAccessibleName('Накопитель, данные которого будут удалены')
        details.setPlainText(disk_label(disk) + '\nСерийный номер: ' + (disk['serial'] or 'не сообщён устройством')
                             + '\nДействие: ' + ('установка FlashOS, exFAT' if action == 'install' else 'форматирование, ' + filesystem)
                             + '\nРазметка: ' + style)
        layout.addWidget(details)
        self.agree = QCheckBox('Подтверждаю удаление всех данных')
        self.agree.setAccessibleName(self.agree.text())
        layout.addWidget(self.agree)
        buttons = QDialogButtonBox()
        self.confirm = KeyboardButton('Удалить данные и продолжить')
        cancel = KeyboardButton('Отмена')
        buttons.addButton(self.confirm, QDialogButtonBox.AcceptRole)
        buttons.addButton(cancel, QDialogButtonBox.RejectRole)
        self.confirm.setEnabled(False)
        self.confirm.setAutoDefault(False)
        self.agree.toggled.connect(self.confirm.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        cancel.setFocus()


class InfoDialog(QDialog):
    def __init__(self, title, parent, text=None, bundle=None):
        super().__init__(parent)
        self.setWindowTitle(title + ' — ' + TITLE)
        self.resize(820, 600)
        layout = QVBoxLayout(self)
        if bundle is None:
            layout.addWidget(label('Разделы выбираются стрелками. Tab — перейти к тексту; стрелки — читать; Escape — закрыть справку.'))
            self.contents = QComboBox()
            self.contents.setAccessibleName('Раздел справки')
            sections = re.split(r'(?m)(?=^## )', text or '')
            for section in sections:
                self.contents.addItem(section.splitlines()[0].lstrip('# ').strip(), section)
            layout.addWidget(self.contents)
            find_row = QHBoxLayout()
            self.search = QLineEdit()
            self.search.setAccessibleName('Поиск в выбранном разделе справки, Ctrl+F')
            self.search.setPlaceholderText('Найти в этом разделе')
            next_button = KeyboardButton('Найти далее')
            self.search_result = label('')
            find_row.addWidget(self.search, 1)
            find_row.addWidget(next_button)
            self.view = ReadOnlyBrowser()
            self.view.setAccessibleName(title)
            self.contents.currentIndexChanged.connect(lambda: self.view.setMarkdown(self.contents.currentData() or ''))
            self.view.setMarkdown(self.contents.currentData() or '')
            layout.addWidget(self.view)
            layout.addLayout(find_row)
            layout.addWidget(self.search_result)
            def find_next():
                query = self.search.text().strip()
                if not query:
                    self.search.setFocus()
                    return
                found = self.view.find(query)
                if not found:
                    self.view.moveCursor(QTextCursor.Start)
                    found = self.view.find(query)
                self.search_result.setText('Найдено: ' + query if found else 'Не найдено в выбранном разделе: ' + query)
                if found:
                    self.view.setFocus()
                else:
                    self.search.setAccessibleDescription(self.search_result.text())
                    self.search.setFocus()
            next_button.clicked.connect(find_next)
            self.search.returnPressed.connect(find_next)
            for key, callback in [('Ctrl+F', self.search.setFocus), ('F3', find_next)]:
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.activated.connect(callback)
            QWidget.setTabOrder(self.contents, self.view)
            QWidget.setTabOrder(self.view, self.search)
            QWidget.setTabOrder(self.search, next_button)
            self.contents.setFocus()
        else:
            tabs = QTabWidget()
            tabs.setAccessibleName('Разделы сведений о компонентах')
            package_page = QWidget()
            package_page.setObjectName('content')
            page_layout = QVBoxLayout(package_page)
            search = QLineEdit()
            search.setPlaceholderText('Название или версия')
            search.setAccessibleName('Поиск компонента')
            page_layout.addWidget(search)
            table = QTableWidget()
            table.setAccessibleName('Название, версия и назначение компонентов')
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(['Компонент', 'Версия', 'Где находится', 'Назначение'])
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setSelectionMode(QTableWidget.SingleSelection)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            table.horizontalHeader().setStretchLastSection(True)
            rows = [(x['name'], x['version'], x.get('scope', 'Встроенная система'), x['purpose']) for x in bundle.manifest['components']]
            rows += [(parts[0], parts[1], 'Linux-образ внутри EXE', 'Пакет загрузочной системы') for line in bundle.text('packages.tsv').splitlines() if len(parts := line.split('\t')) == 2]
            table.setRowCount(len(rows))
            for row, values in enumerate(rows):
                for col, value in enumerate(values):
                    table.setItem(row, col, QTableWidgetItem(value))
            count = label(f'В составе EXE: {len(rows)} записей')
            def filter_rows(value):
                visible = []
                for i, row in enumerate(rows):
                    matched = value.casefold() in ' '.join(row).casefold()
                    table.setRowHidden(i, not matched)
                    if matched:
                        visible.append(i)
                table.setCurrentCell(visible[0] if visible else -1, 0 if visible else -1)
                count.setText(f'Найдено: {len(visible)} из {len(rows)}. Tab — таблица; стрелки — строки и столбцы.')
            search.textChanged.connect(filter_rows)
            page_layout.addWidget(count)
            page_layout.addWidget(label('Показаны библиотеки установщика и содержимое загрузочной системы, включённой в этот EXE. Пользовательские ISO и программы сборки сюда не входят.'))
            page_layout.addWidget(table)
            filter_rows('')
            tabs.addTab(package_page, 'Состав EXE')
            licenses = ReadOnlyBrowser()
            licenses.setAccessibleName('Лицензии, голоса и авторство')
            licenses.setMarkdown(bundle.text('VOICE-LICENSES.md') + '\n\n' + bundle.text('AUTHORSHIP.md'))
            tabs.addTab(licenses, 'Лицензии и авторство')
            notices = ReadOnlyText()
            notices.setAccessibleName('Лицензии Qt, Python и других компонентов')
            notices.setPlainText(bundle.text('THIRD-PARTY.txt'))
            tabs.addTab(notices, 'Сторонние библиотеки')
            layout.addWidget(tabs)
            search.setFocus()
        buttons = QDialogButtonBox()
        buttons.addButton(KeyboardButton('Закрыть'), QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
