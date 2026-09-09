"""Nuitka entry point. There is deliberately no unattended erase command."""
from pathlib import Path
import sys

if '__compiled__' not in globals():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    import traceback
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtGui import QFont
    from installer.model import TITLE
    app = QApplication(sys.argv)
    app.setApplicationName(TITLE)
    app.setOrganizationName('Andrey Zotov')
    app.setFont(QFont('Segoe UI', 10))
    window = None
    reporting = False
    def report_exception(kind, value, trace):
        nonlocal reporting
        if reporting:
            return
        reporting = True
        try:
            detail = ''.join(traceback.format_exception(kind, value, trace))
            if window is not None:
                window.append_log(detail)
            QMessageBox.critical(window, TITLE, 'Не удалось выполнить действие.\n\n' + str(value) + '\n\nПодробности записаны в журнал окна.')
        finally:
            reporting = False
    sys.excepthook = report_exception
    try:
        from installer.gui import MainWindow
        window = MainWindow()
        window.show()
    except Exception as error:
        QMessageBox.critical(None, TITLE, 'Не удалось открыть установщик.\n\n' + str(error))
        return 1
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
