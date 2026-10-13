"""Unprivileged, single-application X11 session inside the USB app root."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import signal
import shutil
import subprocess
import threading
import time

UID = 1000
HOME = Path('/home/flashos-app')
RUNTIME = Path('/run/user/1000')
LOG_LIMIT = 1024 * 1024


def validate_entry(entry):
    if not isinstance(entry, dict) or type(entry.get('terminal')) is not bool:
        raise ValueError('Недопустимый запуск приложения.')
    argv = entry.get('argv')
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 128
            or any(not isinstance(arg, str) or len(arg) > 4096 or any(c in arg for c in '\x00\r\n') for arg in argv)
            or sum(len(arg) for arg in argv) > 16384):
        raise ValueError('Недопустимые аргументы приложения.')
    executable = PurePosixPath(argv[0])
    if not executable.is_absolute() or '..' in executable.parts:
        raise ValueError('Команда приложения должна иметь абсолютный путь.')
    name = entry.get('name')
    if not isinstance(name, str) or not name.strip() or len(name) > 300 or any(c in name for c in '\x00\r\n'):
        raise ValueError('Недопустимое название приложения.')
    return {'name': name, 'argv': list(argv), 'terminal': entry['terminal']}


def application_command(entry):
    entry = validate_entry(entry)
    if entry['terminal']:
        # GNOME Terminal owns the PTY; this fixed helper never assembles a shell command.
        return ['/usr/bin/gnome-terminal', '--wait', '--', '/usr/bin/python3',
                '/opt/flashos-store/store_session.py', '--terminal-command', *entry['argv']]
    argv = list(entry['argv'])
    if PurePosixPath(argv[0]).name == 'gnome-terminal':
        option_end = argv.index('--') if '--' in argv else len(argv)
        if '--wait' not in argv[1:option_end]:
            argv.insert(1, '--wait')
    return argv


def terminal_receipt(name, data, runtime=None):
    path = (RUNTIME if runtime is None else runtime) / name
    temporary = path.with_name(name + '.tmp-' + str(os.getpid()))
    created = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        created = True
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        return True
    except OSError:
        # Missing acknowledgement keeps the original failure status; never assume a close.
        return False
    finally:
        if created:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def terminal_command(argv):
    entry = validate_entry({'name': 'Терминальное приложение', 'argv': argv, 'terminal': False})
    code, hangup_code, hung_up = None, None, False
    completed = None
    def hangup(*_):
        nonlocal hung_up, hangup_code
        if hung_up:
            return
        hung_up, hangup_code = True, code
        # HUP can arrive after run() returned but before its status was assigned.
        if hangup_code is None and completed is not None:
            status = completed.returncode
            hangup_code = 128 - status if status < 0 else status
        terminal_receipt('terminal-hangup.json',
                         {'format': 1, 'signal': 'SIGHUP', 'command_returncode': hangup_code})
    # VTE sends HUP to the PTY group when its window is disposed. Keep the command's
    # completed status separate from that intentional close, including a real exit 137.
    hangup_signal = getattr(signal, 'SIGHUP', None)
    previous_hangup = signal.signal(hangup_signal, hangup) if hangup_signal is not None else None
    previous_interrupt = signal.signal(signal.SIGINT, lambda *_: None)
    try:
        try:
            completed = subprocess.run(entry['argv'], check=False)
            code = completed.returncode
            code = 128 - code if code < 0 else code
            terminal_receipt('terminal-completed.json', {'format': 1, 'command_returncode': code})
        finally:
            signal.signal(signal.SIGINT, previous_interrupt)
        if not hung_up:
            try:
                print('\nКоманда завершилась. Код: ' + str(code) + '. Enter — закрыть окно.', flush=True)
                input()
            except (EOFError, KeyboardInterrupt):
                pass
            except OSError:
                if not hung_up:
                    raise
        return (hangup_code if hangup_code is not None else 0) if hung_up else code
    finally:
        if hangup_signal is not None:
            signal.signal(hangup_signal, previous_hangup)


def terminal_exit_status(entry, frontend_code, runtime, receipt_timeout=2):
    """Interpret VTE's synthetic 137 only when the wrapper confirms a PTY hangup."""
    wrapped = entry['terminal']
    bare = not wrapped and entry['argv'] == ['/usr/bin/gnome-terminal']
    if not wrapped and not bare:
        return frontend_code
    code, reason = frontend_code, 'command-status'
    if frontend_code == 137 and bare:
        # An interactive shell window has no explicit application command to verify.
        code, reason = 0, 'terminal-window-close'
    elif frontend_code == 137 and wrapped:
        reason = 'unconfirmed-termination'
        deadline = time.monotonic() + receipt_timeout
        while True:
            path = runtime / 'terminal-hangup.json'
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024:
                    record = json.loads(path.read_text(encoding='utf-8'))
                    completed = record.get('command_returncode')
                    valid_code = completed is None or (type(completed) is int and 0 <= completed <= 255)
                    if (type(record.get('format')) is int and record['format'] == 1
                            and record.get('signal') == 'SIGHUP' and 'command_returncode' in record and valid_code):
                        code = 0 if completed is None else completed
                        reason = 'terminal-hangup' if completed is None else 'completed-before-hangup'
                        break
            except (OSError, ValueError, AttributeError):
                pass
            if time.monotonic() >= deadline:
                break
            time.sleep(.02)
    diagnostic = {'format': 1, 'frontend_returncode': frontend_code, 'returncode': code, 'reason': reason}
    terminal_receipt('terminal-exit.json', diagnostic, runtime)
    print('FlashOS terminal frontend status ' + str(frontend_code) + ', session status ' + str(code) + ' (' + reason + ')', flush=True)
    return code


def wait_application(app, wm, speech_services, stop, runtime, entry=None):
    return_file = runtime / 'return'
    warned = set()
    while app.poll() is None and not stop.is_set() and not return_file.exists():
        if wm.poll() is not None:
            raise RuntimeError('Оконный менеджер остановился.')
        failed = {name for name, process in speech_services.items() if process.poll() is not None}
        if failed and failed != warned:
            warning = ('Озвучка приложения остановилась: ' + ', '.join(sorted(failed)) + '. '
                       'Приложение продолжает работать. Сохраните документы. '
                       'Ctrl Alt Escape закрывает приложение и возвращает говорящее меню.')
            # Reader failure must never close an application holding unsaved documents.
            temporary = runtime / 'warning.tmp'
            try:
                temporary.write_text(json.dumps({'warning': warning, 'preserved_app': True}, ensure_ascii=False),
                                     encoding='utf-8')
                temporary.replace(runtime / 'warning.json')
            except OSError:
                print(warning, flush=True)
            warned = failed
        time.sleep(.1)
    if stop.is_set() or return_file.exists():
        return 0
    return terminal_exit_status(entry, app.returncode, runtime) if entry is not None else app.returncode


def openbox_config(runtime):
    # Only fixed commands enter this configuration; keep app/user preferences intact.
    return '''<?xml version="1.0"?><openbox_config xmlns="http://openbox.org/3.4/rc">
<focus><focusNew>yes</focusNew><followMouse>no</followMouse></focus>
<theme><name>Clearlooks</name><titleLayout>LMC</titleLayout>
<font place="ActiveWindow"><name>DejaVu Sans</name><size>12</size><weight>Bold</weight></font>
<font place="InactiveWindow"><name>DejaVu Sans</name><size>12</size></font></theme>
<keyboard><keybind key="C-A-Escape"><action name="Execute"><command>/usr/bin/touch /run/user/1000/return</command></action></keybind>
<keybind key="A-F4"><action name="Close"/></keybind>
<keybind key="A-Tab"><action name="NextWindow"/></keybind>
<keybind key="A-S-Tab"><action name="PreviousWindow"/></keybind>
<keybind key="A-F7"><action name="Move"/></keybind>
<keybind key="A-F8"><action name="Resize"/></keybind>
<keybind key="A-F10"><action name="ToggleMaximize"/></keybind>
</keyboard>
<mouse>
<context name="Titlebar"><mousebind button="Left" action="Drag"><action name="Move"/></mousebind>
<mousebind button="Left" action="DoubleClick"><action name="ToggleMaximize"/></mousebind></context>
<context name="Maximize"><mousebind button="Left" action="Click"><action name="ToggleMaximize"/></mousebind></context>
<context name="Close"><mousebind button="Left" action="Click"><action name="Close"/></mousebind></context>
<context name="Bottom"><mousebind button="Left" action="Drag"><action name="Resize"/></mousebind></context>
<context name="BLCorner"><mousebind button="Left" action="Drag"><action name="Resize"/></mousebind></context>
<context name="BRCorner"><mousebind button="Left" action="Drag"><action name="Resize"/></mousebind></context>
</mouse>
</openbox_config>'''


def seed_preferences(home, initial=None):
    from orca_settings import seed_profile
    return seed_profile(home, initial)


def enable_accessibility():
    """Use Orca's required GI runtime instead of an optional gsettings executable."""
    from gi.repository import Gio
    source = Gio.SettingsSchemaSource.get_default()
    for schema, key in (('org.gnome.desktop.interface', 'toolkit-accessibility'),
                        ('org.gnome.desktop.a11y.applications', 'screen-reader-enabled')):
        if source is None or source.lookup(schema, True) is None:
            raise RuntimeError('В среде приложений отсутствует схема доступности: ' + schema)
        settings = Gio.Settings.new(schema)
        if not settings.set_boolean(key, True):
            raise RuntimeError('Не удалось включить доступность приложений: ' + key)
    Gio.Settings.sync()


def apply_session_preferences():
    """Apply the private USB profile before starting Orca and GTK applications."""
    try:
        from preferences import session_environment
    except ModuleNotFoundError as error:
        # A profile from a previous image can still launch before opening Utilities.
        if error.name != 'preferences':
            raise
        return
    os.environ.update(session_environment())


class BoundedLog:
    def __init__(self, path, limit=LOG_LIMIT):
        self.limit, self.lock = limit, threading.Lock()
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        self.stream = os.fdopen(fd, 'w+b', buffering=0)

    def write(self, content):
        with self.lock:
            self.stream.seek(0, 2)
            if self.stream.tell() + len(content) > self.limit:
                self.stream.seek(0)
                kept = self.stream.read()[-self.limit // 2:]
                self.stream.seek(0)
                self.stream.truncate()
                self.stream.write(kept)
            self.stream.write(content[-self.limit // 2:])

    def close(self):
        with self.lock:
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.stream.close()


def terminate_children(processes, timeout=5):
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + timeout
    for process in reversed(processes):
        try:
            process.wait(timeout=max(.05, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def run_session(entry, home=HOME, runtime=RUNTIME):
    from network_proxy import PROXY_VARIABLES, read_session_proxy, redact_proxy
    from orca_settings import read_initial, read_status, dispatcher_config, wait_speech_ready, spawn_speech_dispatcher
    for name in PROXY_VARIABLES:
        os.environ.pop(name, None)
    proxy_environment = read_session_proxy(runtime)
    os.environ.update(proxy_environment)
    entry = validate_entry(entry)
    if os.geteuid() != UID:
        raise RuntimeError('Приложения запускаются только от непривилегированного пользователя.')
    if not Path(entry['argv'][0]).is_file() or not os.access(entry['argv'][0], os.X_OK):
        raise RuntimeError('Исполняемый файл приложения отсутствует или недоступен.')
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    return_file = runtime / 'return'
    return_file.unlink(missing_ok=True)
    config = runtime / 'openbox.xml'
    config.write_text(openbox_config(runtime), encoding='utf-8')
    speech_config = runtime / 'speech-config'
    speech_config.mkdir()
    shutil.copytree('/etc/speech-dispatcher/modules', speech_config / 'modules', symlinks=True)
    (speech_config / 'modules/rhvoice.conf').write_text('LogLevel 1\n', encoding='utf-8')
    initial = read_initial(runtime)
    preferences = seed_preferences(home, initial)
    speech_settings = read_status(home, initial)
    (speech_config / 'speechd.conf').write_text(dispatcher_config(speech_settings['module']), encoding='utf-8')
    log_dir = home / '.local/state/flashos'
    log_dir.mkdir(parents=True, exist_ok=True)
    log = BoundedLog(log_dir / 'last-session.log')
    processes, readers = [], []
    def spawn(argv, env=None, speech_dispatcher=False):
        creator = spawn_speech_dispatcher if speech_dispatcher else subprocess.Popen
        process = creator(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, cwd=home, env=env)
        processes.append(process)
        def drain():
            pending = b''
            dropping = False
            try:
                while chunk := os.read(process.stdout.fileno(), 4096):
                    pending += chunk
                    if dropping:
                        if b'\n' not in pending:
                            pending = b''
                            continue
                        pending = pending.split(b'\n', 1)[1]
                        dropping = False
                    lines = pending.split(b'\n')
                    pending = lines.pop()
                    for line in lines:
                        log.write((redact_proxy(line.decode('utf-8', errors='replace'), proxy_environment) + '\n').encode('utf-8'))
                    if len(pending) > 16384:
                        # Discard oversized partial lines; splitting may expose a credential across chunks.
                        pending = b''
                        dropping = True
                        log.write(b'[Oversized application log line omitted]\n')
            finally:
                if pending:
                    log.write(redact_proxy(pending.decode('utf-8', errors='replace'), proxy_environment).encode('utf-8'))
                process.stdout.close()
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        readers.append(reader)
        return process
    def wait_for(path, process, seconds=12):
        deadline = time.monotonic() + seconds
        while not path.exists():
            if stop.is_set():
                return False
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Не удалось запустить компонент доступности: ' + str(path.name))
            time.sleep(.05)
        return True
    try:
        wm = spawn(['/usr/bin/openbox', '--sm-disable', '--config-file', str(config),
                    '--startup', '/usr/bin/touch /run/user/1000/wm-ready'])
        if not wait_for(runtime / 'wm-ready', wm):
            return 0
        pulse = spawn(['/usr/bin/pipewire-pulse'])
        if not wait_for(runtime / 'pulse/native', pulse):
            return 0
        speech_socket = runtime / 'speech-dispatcher/speechd.sock'
        speech_socket.parent.mkdir()
        speech = spawn(['/usr/bin/speech-dispatcher', '--run-single', '--communication-method=unix_socket',
                        '--socket-path=' + str(speech_socket), '--config-dir=' + str(speech_config), '--timeout=0',
                        '--log-dir=' + str(runtime)],
                       env={**os.environ, 'LD_LIBRARY_PATH': '/run/flashos-rhvoice-libs'}, speech_dispatcher=True)
        if not wait_speech_ready((speech_socket, runtime / 'pulse/native'), (speech, pulse),
                                 stop, return_file=return_file):
            return 0
        enable_accessibility()
        apply_session_preferences()
        orca = spawn(['/usr/bin/orca', '--enable=speech', '--user-prefs-dir=' + str(preferences)])
        # Give AT-SPI time to subscribe before the first application window appears.
        if stop.wait(1):
            return 0
        if orca.poll() is not None:
            raise RuntimeError('Экранный диктор Orca завершился до запуска приложения.')
        app = spawn(application_command(entry))
        return wait_application(app, wm, {'Orca': orca, 'Speech Dispatcher': speech, 'PipeWire Pulse': pulse},
                                stop, runtime, entry)
    finally:
        # systemd additionally kills the entire cgroup, including double-forked children.
        terminate_children(processes)
        for reader in readers:
            reader.join(timeout=1)
        log.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--config', type=Path)
    mode.add_argument('--terminal-command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.terminal_command is not None:
        return terminal_command(args.terminal_command)
    result = {'returncode': 1, 'error': ''}
    try:
        info = args.config.lstat()
        if (args.config.is_symlink() or info.st_size > 65536 or info.st_uid != UID
                or info.st_mode & 0o077):
            raise ValueError('Invalid session configuration')
        result['returncode'] = run_session(json.loads(args.config.read_text(encoding='utf-8')))
        diagnostic = RUNTIME / 'terminal-exit.json'
        try:
            if diagnostic.is_file() and not diagnostic.is_symlink() and diagnostic.stat().st_size <= 1024:
                result['terminal_exit'] = json.loads(diagnostic.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
    except (OSError, ValueError, RuntimeError) as error:
        result['error'] = str(error)[:500]
        print('FlashOS application session: ' + result['error'], flush=True)
    finally:
        (RUNTIME / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result['returncode']



if __name__ == '__main__':
    raise SystemExit(main())
