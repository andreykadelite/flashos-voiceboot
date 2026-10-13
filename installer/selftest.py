"""Bounded compiled startup diagnostic; physical devices are never enumerated."""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback


def report_path(value):
    path = Path(value).resolve()
    if (path.name != 'compiled-startup.json' or
            not path.parent.name.startswith('installer-compat-') or
            path.parent.parent.name != 'build' or not path.parent.is_dir()):
        raise ValueError('The report must be build/installer-compat-*/compiled-startup.json')
    if path.exists():
        raise ValueError('The diagnostic report already exists')
    return path


@contextmanager
def device_guards():
    # Importing these modules does not scan or change any drive. All external
    # device boundaries are replaced before the real window can start a worker.
    from installer import windows, backend, gui
    calls = {'inventory_mock_calls': 0, 'blocked_actions': []}
    def empty_inventory():
        calls['inventory_mock_calls'] += 1
        return []
    def deny(*args, **kwargs):
        calls['blocked_actions'].append('device or elevation action')
        raise RuntimeError('Device actions are unavailable in the startup diagnostic')
    overrides = [
        (windows, 'inventory', empty_inventory), (gui, 'inventory', empty_inventory),
        (backend, 'inventory', empty_inventory), (windows, 'elevate', deny),
        (windows, 'powershell', deny), (backend, 'powershell', deny),
        (windows, 'is_admin', lambda: False), (gui, 'is_admin', lambda: False),
        (gui, 'JobThread', deny), (backend.Operation, '__init__', deny),
        (gui.MainWindow, 'start_action', deny), (gui.MainWindow, 'open_iso', deny),
        (gui.MainWindow, 'save_log', deny),
    ]
    original = [(owner, name, getattr(owner, name)) for owner, name, value in overrides]
    try:
        for owner, name, value in overrides:
            setattr(owner, name, value)
        yield gui, calls
    finally:
        for owner, name, value in reversed(original):
            setattr(owner, name, value)


def exercise_window(compiled):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont
    from installer.model import TITLE, VERSION, SYSTEM_VERSION
    from installer._release import MANIFEST_SHA256, ENGINE_SCRIPT
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName(TITLE)
    app.setOrganizationName('Andrey Zotov')
    app.setFont(QFont('Segoe UI', 10))
    window = None
    with device_guards() as (gui, calls):
        try:
            window = gui.MainWindow()
            window.scan_timer.stop()
            window.show()
            window.activateWindow()
            window.drives.setFocus()
            deadline = time.monotonic() + 8
            while not window.scanned_once or (window.scan and window.scan.isRunning()):
                app.processEvents()
                if time.monotonic() >= deadline:
                    raise TimeoutError('The mock scan did not finish')
                time.sleep(.01)
            app.processEvents()
            buttons = {name: getattr(window, name).isEnabled()
                       for name in ('install', 'verify', 'format', 'iso')}
            if any(buttons.values()) or window.disks or window.selected() is not None:
                raise AssertionError('A device action became available without a device')
            controls = [window.drives, window.refresh, window.details, window.status,
                        window.progress, window.log, *window.action_buttons]
            if not all(control.accessibleName() for control in controls):
                raise AssertionError('An expected accessible control is unnamed')
            window.status.setFocus()
            app.processEvents()
            if app.focusWidget() is not window.status:
                raise AssertionError('Keyboard focus did not reach the status control')
            document = window.bundle.text('AUTHORSHIP.md')
            record = next(r for r in window.bundle.manifest['files']
                          if r['section'] == 'info' and r['path'] == 'AUTHORSHIP.md')
            if len(document.encode('utf-8')) > 128 * 1024:
                raise ValueError('Unexpected diagnostic document size')
            if calls['blocked_actions']:
                raise AssertionError('The startup diagnostic attempted a forbidden action')
            return dict(
                ok=True, compiled=bool(compiled), executable=sys.executable,
                argv0=str(Path(sys.argv[0]).resolve()),
                bundle_path=str(window.bundle.path.resolve()),
                manifest_sha256=MANIFEST_SHA256,
                engine_script_sha256=hashlib.sha256(ENGINE_SCRIPT.encode('utf-8')).hexdigest(),
                installer_version=VERSION,
                system_version=SYSTEM_VERSION, window_visible=window.isVisible(),
                window_title=window.windowTitle(), keyboard_focus=True,
                accessible_controls=len(controls), disk_actions_enabled=buttons,
                disk_enumeration=False, disk_writes=False, elevation=False,
                disk_worker_started=False, inventory_mock_calls=calls['inventory_mock_calls'],
                forbidden_action_attempts=calls['blocked_actions'],
                tiny_info=dict(path=record['path'], sha256=record['sha256'],
                               bytes=record['bytes'], verified=True))
        finally:
            if window is not None:
                window.scan_timer.stop()
                window.progress_timer.stop()
                if window.scan and window.scan.isRunning():
                    window.scan.wait(2000)
                window.close()
                app.processEvents()
            app.quit()


def main(argv=None, *, compiled=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args(argv)
    target = report_path(args.report)
    started = time.monotonic()
    try:
        result = exercise_window(compiled)
    except Exception as error:
        result = dict(ok=False, compiled=bool(compiled), error=str(error),
                      traceback=traceback.format_exc(), disk_enumeration=False,
                      disk_writes=False, elevation=False)
    result['duration_seconds'] = round(time.monotonic() - started, 3)
    with target.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return 0 if result['ok'] else 1
