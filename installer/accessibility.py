"""Native Qt text/caret and button semantics shared by the installer dialogs."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QTextBrowser


TEXT_KEYS = ('Стрелки — чтение строк и символов; Ctrl+стрелки — слова; '
             'Home/End — начало/конец строки; Ctrl+Home/Ctrl+End — начало/конец текста; '
             'Shift со стрелками — выделение; Ctrl+C — копировать; Tab — следующий элемент.')


def readable(widget):
    widget.setReadOnly(True)
    # ReadOnly alone enables mouse selection, but arrows only scroll the viewport.
    # A real caret is required by Qt's native UIA TextPattern implementation.
    widget.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    widget.setFocusPolicy(Qt.StrongFocus)
    widget.setTabChangesFocus(True)
    widget.setAccessibleDescription(TEXT_KEYS)
    return widget


class ReadOnlyText(QPlainTextEdit):
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        readable(self)
        self.pending_text = None

    def snapshot(self, text, force=False):
        """Hold a readable snapshot while focused; F6 explicitly refreshes it."""
        if self.hasFocus() and not force:
            self.pending_text = text
        else:
            self.pending_text = None
            if text != self.toPlainText():
                self.setPlainText(text)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if self.pending_text is not None:
            self.snapshot(self.pending_text, force=True)

    def append_entry(self, text):
        bar = self.verticalScrollBar()
        previous_scroll = bar.value()
        follow = previous_scroll >= bar.maximum() - 2 and not self.hasFocus()
        reader = self.textCursor()
        anchor = QTextCursor(self.document())
        anchor.setPosition(reader.anchor())
        anchor.setKeepPositionOnInsert(True)
        reader.setKeepPositionOnInsert(True)
        old_count = self.blockCount()
        writer = QTextCursor(self.document())
        writer.movePosition(QTextCursor.End)
        writer.insertText(('\n' if not self.document().isEmpty() else '') + text)
        # QTextCursor tracks offsets when the bounded log evicts old blocks.
        # Never temporarily put the widget's caret on the newly appended line.
        position = reader.position()
        reader.setPosition(anchor.position())
        reader.setPosition(position, QTextCursor.KeepAnchor)
        self.setTextCursor(reader)
        removed = max(0, old_count + text.count('\n') + 1 - self.blockCount())
        bar.setValue(bar.maximum() if follow else max(0, previous_scroll - removed))


class ReadOnlyBrowser(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        readable(self)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)


class KeyboardButton(QPushButton):
    """Enter activates the focused button even outside a default-button dialog."""
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setAutoDefault(False)
        self.setDefault(False)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() == Qt.NoModifier:
            if not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)
