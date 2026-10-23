"""Launch the live system's spoken terminal independently of the USB app store."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


SHELL_RC = r'''# FlashOS: session history lives only in RAM.
HISTFILE=/dev/null
HISTSIZE=1000
HISTFILESIZE=0
HISTCONTROL=ignoreboth
PS1='\[\e[1;36m\]FlashOS \w\[\e[0m\] # '
# Prompt decoding consumes one backslash before printf sees the newline.
PS2='$(builtin printf "ready\\n" > "$FLASHOS_TERMINAL_STATE/state")...> '
__flashos_serial=0
__flashos_ready() { builtin printf 'ready\n' > "$FLASHOS_TERMINAL_STATE/state"; }
__flashos_accept() {
    local __flashos_state
    IFS= builtin read -r __flashos_state < "$FLASHOS_TERMINAL_STATE/state"
    [[ $__flashos_state == ready ]] || return 0
    builtin printf 'accepted\n' > "$FLASHOS_TERMINAL_STATE/state"
    ((__flashos_serial+=1))
    builtin printf '%s\0%s\0' "$__flashos_serial" "$READLINE_LINE" > "$FLASHOS_TERMINAL_STATE/command"
}
PROMPT_COMMAND=__flashos_ready
PS0='$(builtin printf "busy\n" > "$FLASHOS_TERMINAL_STATE/state")'
bind -x '"\C-x\C-t":__flashos_accept'
bind '"\C-x\C-a":accept-line'
bind '"\C-m":"\C-x\C-t\C-x\C-a"'
bind '"\C-j":"\C-x\C-t\C-x\C-a"'
bind 'set enable-bracketed-paste on'
alias ls='ls --color=auto'
'''


class ShellSession:
    """Readline reports submitted commands, never input to password programs."""
    def __init__(self, directory=None):
        parent = '/run' if directory is None and os.access('/run', os.W_OK) else directory
        self.temporary = tempfile.TemporaryDirectory(prefix='flashos-terminal-', dir=parent)
        self.path = Path(self.temporary.name)
        self.path.chmod(0o700)
        self.rc = self.path / 'bashrc'
        self.rc.write_text(SHELL_RC, encoding='utf-8')
        self.rc.chmod(0o600)
        self.serial = 0

    @property
    def argv(self):
        return ['/bin/bash', '--noprofile', '--rcfile', str(self.rc), '-i']

    def environment(self):
        value = {key: val for key, val in os.environ.items()
                 if key not in ('BASH_ENV', 'ENV', 'PROMPT_COMMAND', 'SHELLOPTS', 'BASHOPTS')
                 and not key.startswith('BASH_FUNC_')}
        value.update(FLASHOS_TERMINAL_STATE=str(self.path), TERM='xterm-256color',
                     COLORTERM='truecolor', HISTFILE='/dev/null', LANG='C.UTF-8')
        return value

    def state(self):
        try:
            with (self.path / 'state').open('rb') as stream:
                return stream.read(16).strip() == b'ready'
        except OSError:
            return False

    def command(self):
        try:
            with (self.path / 'command').open('rb') as stream:
                data = stream.read(65537)
            if len(data) > 65536 or not data.endswith(b'\0'):
                return None
            serial, command, ending = data.split(b'\0')
            number = int(serial)
            if ending or number <= self.serial:
                return None
            self.serial = number
            return command.decode('utf-8', errors='replace')
        except (OSError, ValueError):
            return None

    def close(self):
        self.temporary.cleanup()


def paste_text(value):
    """Clipboard data must not contain keystrokes disguised as control bytes."""
    value = str(value).replace('\r\n', '\n').replace('\r', '\n')
    if len(value) > 65536:
        raise ValueError('Вставка ограничена 64 тысячами символов.')
    if any(not char.isprintable() and char not in '\n\t' for char in value):
        raise ValueError('Вставка содержит управляющие символы. Используйте обычный текст.')
    return value


def launch(ui, working_directory):
    if not getattr(ui.screen, 'graphical', False):
        ui.message('Терминал', 'Полноценный терминал с озвучкой доступен в графическом режиме FlashOS. '
                   'Диагностика и остальные инструменты работают и в текстовом меню.')
        return
    settings = {name: ui.settings.data[name] for name in ('voice', 'rate', 'pitch', 'volume', 'signals')}
    command = [sys.executable, str(Path(__file__).with_name('terminal_window.py')),
               '--settings', json.dumps(settings, ensure_ascii=False)]
    output = getattr(ui.speech, 'output_name', None)
    if isinstance(output, str) and output:
        command.extend(['--output', output])
    command.extend(['--directory', str(working_directory)])
    unit = None
    if not ui.demo:
        unit = 'flashos-terminal-' + uuid.uuid4().hex + '.service'
        prefix = ['systemd-run', '--quiet', '--collect', '--wait', '--pipe', '--service-type=exec',
                  '--expand-environment=no', '--unit=' + unit,
                  '--property=KillMode=control-group', '--property=TimeoutStopSec=5s',
                  '--property=SendSIGKILL=yes', '--property=BindsTo=flashos.service',
                  '--property=After=flashos.service']
        for name in ('DISPLAY', 'XAUTHORITY', 'LANG', 'LC_ALL', 'XDG_RUNTIME_DIR',
                     'PIPEWIRE_RUNTIME_DIR', 'PIPEWIRE_REMOTE', 'PULSE_SERVER'):
            if name in os.environ:
                prefix.append('--setenv=' + name + '=' + os.environ[name])
        command = prefix + ['--'] + command
    process = None
    ui.speech.stop()
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=errors, start_new_session=True)
            ui.screen.suspend()
            import time
            while process.poll() is None:
                ui.screen.root.update()
                time.sleep(.05)
            if process.returncode:
                errors.seek(0)
                detail = errors.read(8192).decode('utf-8', errors='replace')
                raise RuntimeError('Не удалось открыть терминал. ' + detail[-1500:])
    finally:
        try:
            if unit:
                subprocess.run(['systemctl', 'stop', unit], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12)
            elif process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        finally:
            ui.screen.resume()
            ui.speech.say('Терминал закрыт. Инструменты.')
