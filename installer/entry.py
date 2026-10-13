"""Nuitka entry point. There is deliberately no unattended erase command."""
from pathlib import Path
import sys

if '__compiled__' not in globals():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if '--self-test' in sys.argv[1:]:
        from installer.selftest import main as self_test
        return self_test(sys.argv[1:], compiled='__compiled__' in globals())
    from installer.windows import is_admin, elevate
    startup_error = None
    # The packaged EXE requests UAC in its manifest. Source launches must also
    # obtain rights before there is a menu, a selected USB, or a scan thread.
    if not is_admin():
        try:
            elevate()
        except Exception as error:
            startup_error = error
        else:
            return 0
    import traceback
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtGui import QFont
    from installer.model import TITLE
    app = QApplication(sys.argv)
    app.setApplicationName(TITLE)
    app.setOrganizationName('Andrey Zotov')
    app.setFont(QFont('Segoe UI', 10))
    if startup_error is not None:
        QMessageBox.critical(None, TITLE, 'Для запуска установщика нужны права администратора.\n\n' + str(startup_error))
        return 1
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
