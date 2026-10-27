"""Launch one USB application as a bounded, unprivileged systemd session."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from store_session import validate_entry

RUNTIME_BASE = Path('/run/flashos-app-user')


class StoreLauncher:
    def __init__(self, environment, ui):
        self.environment, self.ui = environment, ui
        self.unit = None
        self.runtime = None
        self.authorized = False
        self.display = ''
        self.last_warning = ''

    @staticmethod
    def _command(argv, timeout=10, check=True, env=None):
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=timeout, env=env)
        if check and result.returncode:
            raise RuntimeError('Не удалось выполнить ' + argv[0] + ': ' + result.stderr.strip()[:500])
        return result

    def running(self):
        if not self.unit:
            return False
        result = self._command(['systemctl', 'show', self.unit, '--property=ActiveState', '--value'], check=False)
        if result.returncode:
            if any(text in result.stderr.lower() for text in ('not found', 'could not be found', 'not loaded')):
                return False
            raise RuntimeError('Не удалось проверить завершение приложения: ' + result.stderr[:300])
        return result.stdout.strip() in ('active', 'activating', 'reloading', 'deactivating')

    def _prepare(self, entry):
        root = Path(self.environment.root)
        if not root.is_absolute() or not (root / 'opt/flashos-store/store_session.py').is_file():
            raise RuntimeError('Среда приложений не подготовлена.')
        self.display = os.environ.get('DISPLAY', '')
        if not re.fullmatch(r':\d+(?:\.\d+)?', self.display):
            raise RuntimeError('Для приложений нужен локальный графический экран FlashOS.')
        if os.geteuid() != 0:
            raise RuntimeError('Служба запуска приложений требует прав администратора.')
        RUNTIME_BASE.mkdir(mode=0o700, exist_ok=True)
        if RUNTIME_BASE.is_symlink() or RUNTIME_BASE.stat().st_uid != 0:
            raise RuntimeError('Недопустимая служебная папка запуска приложений.')
        self.runtime = RUNTIME_BASE / uuid.uuid4().hex
        self.runtime.mkdir(mode=0o700)
        os.chown(self.runtime, 1000, 1000)
        config = self.runtime / 'config.json'
        with config.open('x', encoding='utf-8') as stream:
            json.dump(entry, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(config, 1000, 1000)
        config.chmod(0o600)
        from network_proxy import write_session_proxy
        write_session_proxy(self.ui, self.runtime)
        from orca_settings import write_initial
        data = getattr(getattr(self.ui, 'settings', None), 'data', None)
        write_initial(self.runtime, data)
        self._command(['xhost', '+SI:localuser:flashos-app'], env={**os.environ, 'DISPLAY': self.display, 'LC_ALL': 'C'})
        self.authorized = True
        self.unit = 'flashos-app-' + uuid.uuid4().hex + '.service'
        properties = ['RootDirectory=' + str(root), 'User=1000', 'Group=1000',
                      'WorkingDirectory=/home/flashos-app', 'KillMode=control-group', 'TimeoutStopSec=12s',
                      'SendSIGKILL=yes', 'BindsTo=flashos.service', 'After=flashos.service',
                      'CapabilityBoundingSet=', 'AmbientCapabilities=', 'NoNewPrivileges=yes',
                      'BindPaths=' + str(self.runtime) + ':/run/user/1000']
        if Path('/dev/dri').is_dir():
            properties[-1] += ' /dev/dri'
        # Group names are resolved against the host, whose uid 1000 owns this session.
        properties.append('SupplementaryGroups=audio video render pipewire')
        environment = {'DISPLAY': self.display, 'HOME': '/home/flashos-app', 'USER': 'flashos-app',
                       'LOGNAME': 'flashos-app', 'PATH': '/usr/local/bin:/usr/bin:/bin',
                       'LANG': 'C.UTF-8', 'LANGUAGE': 'ru', 'XDG_RUNTIME_DIR': '/run/user/1000',
                       'PIPEWIRE_RUNTIME_DIR': '/run/pipewire', 'PIPEWIRE_REMOTE': 'pipewire-0',
                       'PULSE_SERVER': 'unix:/run/user/1000/pulse/native',
                       'PULSE_RUNTIME_PATH': '/run/user/1000/pulse',
                       'SPEECHD_ADDRESS': 'unix_socket:/run/user/1000/speech-dispatcher/speechd.sock',
                       'NO_AT_BRIDGE': '0', 'GTK_MODULES': 'atk-bridge',
                       'QT_ACCESSIBILITY': '1', 'QT_LINUX_ACCESSIBILITY_ALWAYS_ON': '1'}
        if os.environ.get('XCURSOR_SIZE') in ('24', '32', '48', '64'):
            environment['XCURSOR_SIZE'] = os.environ['XCURSOR_SIZE']
        command = ['systemd-run', '--quiet', '--collect', '--service-type=exec', '--expand-environment=no',
                   '--unit=' + self.unit]
        command += ['--property=' + item for item in properties]
        command += ['--setenv=' + name + '=' + value for name, value in environment.items()]
        command += ['--', '/usr/bin/dbus-run-session', '--', '/usr/bin/python3',
                    '/opt/flashos-store/store_session.py', '--config', '/run/user/1000/config.json']
        return command

    def _announce_warning(self):
        if self.runtime is None:
            return
        warning_file = self.runtime / 'warning.json'
        try:
            if warning_file.is_symlink() or not warning_file.is_file() or warning_file.stat().st_size > 4096:
                return
            warning = json.loads(warning_file.read_text(encoding='utf-8')).get('warning')
            if isinstance(warning, str) and warning and warning != self.last_warning:
                self.last_warning = warning
                self.ui.speech.say(warning)
        except (OSError, ValueError, AttributeError):
            # A damaged notification cannot justify terminating a running application.
            return

    def launch(self, entry):
        from network_proxy import ensure_credentials
        ensure_credentials(self.ui)
        entry = validate_entry(entry)
        if not getattr(self.ui.screen, 'graphical', False):
            raise RuntimeError('Запуск доступен в графическом режиме. Магазин работает и в текстовом меню.')
        if self.running():
            raise RuntimeError('Сначала закройте текущее приложение.')
        suspended = False
        try:
            self.last_warning = ''
            command = self._prepare(entry)
            self.ui.speech.say('Запускаю ' + entry['name'] + '. Ctrl Alt Escape — закрыть приложение и вернуться в меню. '
                               'Alt F4 — закрыть окно. Orca озвучивает доступные элементы.')
            self._command(command, timeout=20)
            self.ui.screen.suspend()
            suspended = True
            while self.running():
                # Keep Tk alive while another application owns focus; do not steal its keys.
                self.ui.screen.root.update()
                self._announce_warning()
                time.sleep(.1)
            result_path = self.runtime / 'result.json'
            if result_path.is_symlink() or not result_path.is_file() or result_path.stat().st_size > 4096:
                raise RuntimeError('Приложение завершилось без подтверждения. Подробности в журнале запуска.')
            result = json.loads(result_path.read_text(encoding='utf-8'))
            if result.get('returncode') != 0:
                raise RuntimeError('Приложение завершилось с ошибкой: ' + str(result.get('error') or result.get('returncode'))[:500])
        finally:
            try:
                self.close()
            finally:
                if suspended:
                    self.ui.screen.resume()
                    self.ui.speech.say('Приложение закрыто. Возврат в магазин.')

    def close(self):
        if self.unit:
            if self.running():
                try:
                    self._command(['systemctl', 'stop', self.unit], timeout=20)
                except (RuntimeError, subprocess.TimeoutExpired):
                    self._command(['systemctl', 'kill', '--kill-whom=all', '--signal=KILL', self.unit], check=False)
                deadline = time.monotonic() + 5
                while self.running() and time.monotonic() < deadline:
                    time.sleep(.1)
                if self.running():
                    raise RuntimeError('Приложение ещё завершает работу. Среду на флешке нельзя отключать.')
            self.unit = None
        if self.authorized:
            self._command(['xhost', '-SI:localuser:flashos-app'],
                          env={**os.environ, 'DISPLAY': self.display, 'LC_ALL': 'C'})
            self.authorized = False
        if self.runtime:
            if self.runtime.parent != RUNTIME_BASE or self.runtime.is_symlink():
                raise RuntimeError('Недопустимая служебная папка приложения.')
            shutil.rmtree(self.runtime)
            self.runtime = None
