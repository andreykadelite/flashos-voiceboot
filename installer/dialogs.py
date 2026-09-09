from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QDialogButtonBox,
                               QCheckBox, QTabWidget, QWidget, QTableWidget, QTableWidgetItem,
                               QLineEdit, QHeaderView, QTextBrowser)
from installer.model import disk_label, TITLE


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
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setAccessibleName('Накопитель, данные которого будут удалены')
        details.setPlainText(disk_label(disk) + '\nСерийный номер: ' + (disk['serial'] or 'не сообщён устройством')
                             + '\nДействие: ' + ('установка FlashOS, exFAT' if action == 'install' else 'форматирование, ' + filesystem)
                             + '\nРазметка: ' + style)
        layout.addWidget(details)
        self.agree = QCheckBox('Подтверждаю удаление всех данных')
        self.agree.setAccessibleName(self.agree.text())
        layout.addWidget(self.agree)
        buttons = QDialogButtonBox()
        self.confirm = buttons.addButton('Удалить данные и продолжить', QDialogButtonBox.AcceptRole)
        cancel = buttons.addButton('Отмена', QDialogButtonBox.RejectRole)
        self.confirm.setEnabled(False)
        self.confirm.setAutoDefault(False)
        cancel.setDefault(True)
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
            view = QTextBrowser()
            view.setAccessibleName(title)
            view.setOpenExternalLinks(False)
            view.setMarkdown(text)
            layout.addWidget(view)
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
            table.setColumnCount(3)
            table.setHorizontalHeaderLabels(['Компонент', 'Версия', 'Назначение'])
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            table.horizontalHeader().setStretchLastSection(True)
            rows = [(x['name'], x['version'], x['purpose']) for x in bundle.manifest['components']]
            rows += [(parts[0], parts[1], 'Пакет загрузочной системы') for line in bundle.text('packages.tsv').splitlines() if len(parts := line.split('\t')) == 2]
            table.setRowCount(len(rows))
            for row, values in enumerate(rows):
                for col, value in enumerate(values):
                    table.setItem(row, col, QTableWidgetItem(value))
            search.textChanged.connect(lambda value: [table.setRowHidden(i, value.casefold() not in ' '.join(row).casefold()) for i, row in enumerate(rows)])
            page_layout.addWidget(table)
            tabs.addTab(package_page, 'Программы и версии')
            licenses = QTextBrowser()
            licenses.setAccessibleName('Лицензии, голоса и авторство')
            licenses.setMarkdown(bundle.text('VOICE-LICENSES.md') + '\n\n' + bundle.text('AUTHORSHIP.md'))
            tabs.addTab(licenses, 'Лицензии и авторство')
            notices = QPlainTextEdit()
            notices.setReadOnly(True)
            notices.setAccessibleName('Лицензии Qt, Python и других компонентов')
            notices.setPlainText(bundle.text('THIRD-PARTY.txt'))
            tabs.addTab(notices, 'Сторонние библиотеки')
            layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText('Закрыть')
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
