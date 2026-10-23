"""Terminal boundaries and real Bash/Readline integration without physical disks."""
import json
import os
from pathlib import Path
import re
import select
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import terminal
from settings import DEFAULTS


class ShellSessionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.session = terminal.ShellSession(self.folder.name)
        self.addCleanup(self.session.close)

    def test_session_is_private_and_cleanup_only_removes_its_own_directory(self):
        sibling = Path(self.folder.name) / 'preserved'
        sibling.write_text('user data')
        path = self.session.path
        self.assertTrue(path.is_dir())
        self.assertEqual(self.session.argv, ['/bin/bash', '--noprofile', '--rcfile', str(self.session.rc), '-i'])
        if os.name == 'posix':
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.session.rc.stat().st_mode & 0o777, 0o600)
        self.session.close()
        self.assertFalse(path.exists())
        self.assertEqual(sibling.read_text(), 'user data')

    def test_environment_removes_shell_injection_and_keeps_history_off_usb(self):
        environment = {'HOME': '/mnt/usb/home', 'HISTFILE': '/mnt/usb/history', 'LANG': 'ru_RU.UTF-8',
                       'BASH_ENV': '/mnt/usb/inject', 'ENV': '/mnt/usb/inject', 'PROMPT_COMMAND': 'bad',
                       'SHELLOPTS': 'xtrace', 'BASHOPTS': 'extdebug', 'BASH_FUNC_injected%%': 'bad'}
        with mock.patch.dict(os.environ, environment, clear=True):
            result = self.session.environment()
        self.assertEqual(result['HISTFILE'], '/dev/null')
        self.assertEqual(result['FLASHOS_TERMINAL_STATE'], str(self.session.path))
        self.assertEqual(result['HOME'], environment['HOME'])
        self.assertEqual(result['TERM'], 'xterm-256color')
        for key in ('BASH_ENV', 'ENV', 'PROMPT_COMMAND', 'SHELLOPTS', 'BASHOPTS', 'BASH_FUNC_injected%%'):
            self.assertNotIn(key, result)
        self.assertEqual(environment['HISTFILE'], '/mnt/usb/history')

    def test_commands_are_consumed_once_and_monotonically(self):
        command = self.session.path / 'command'
        command.write_bytes(b'1\0printf "hello"\0')
        self.assertEqual(self.session.command(), 'printf "hello"')
        self.assertIsNone(self.session.command())
        command.write_bytes(b'0\0older\0')
        self.assertIsNone(self.session.command())
        command.write_bytes(b'2\0newer\0')
        self.assertEqual(self.session.command(), 'newer')

    def test_partial_malformed_and_oversized_command_files_do_not_advance_serial(self):
        command = self.session.path / 'command'
        self.assertIsNone(self.session.command())
        for data in (b'', b'1\0partial', b'invalid\0command\0', b'1\0a\0extra\0',
                     b'-1\0negative\0', b'1\0' + b'x' * 65534 + b'\0', b'9' * 65532 + b'\0x\0'):
            with self.subTest(data=data[:30]):
                command.write_bytes(data)
                self.assertIsNone(self.session.command())
        command.write_bytes(b'1\0valid\0')
        self.assertEqual(self.session.command(), 'valid')

    def test_exact_command_byte_limit_and_invalid_utf8(self):
        command = self.session.path / 'command'
        command.write_bytes(b'1\0' + b'x' * 65533 + b'\0')
        self.assertEqual(self.session.command(), 'x' * 65533)
        command.write_bytes(b'2\0invalid \xff\0')
        self.assertEqual(self.session.command(), 'invalid \ufffd')

    def test_missing_malformed_and_unreadable_state_is_not_ready(self):
        state = self.session.path / 'state'
        self.assertFalse(self.session.state())
        for data in (b'', b'busy\n', b'ready-not-really', b'ready\0', b'\xff'):
            with self.subTest(data=data):
                state.write_bytes(data)
                self.assertFalse(self.session.state())
        state.write_bytes(b'ready\n')
        self.assertTrue(self.session.state())
        state.unlink()
        state.mkdir()
        self.assertFalse(self.session.state())


class PasteTests(unittest.TestCase):
    def test_printable_paths_tabs_and_multiline_are_preserved(self):
        self.assertEqual(terminal.paste_text('printf "Привет"\r\ncd /mnt/Мой_диск\r\tpwd'),
                         'printf "Привет"\ncd /mnt/Мой_диск\n\tpwd')

    def test_control_bytes_and_invisible_format_controls_are_rejected(self):
        for control in ('\x00', '\x1b[201~', '\b', '\x03', '\x04', '\x7f', '\x85', '\u202e', '\u200b'):
            with self.subTest(control=repr(control)), self.assertRaisesRegex(ValueError, 'управляющие'):
                terminal.paste_text('safe' + control + 'text')

    def test_paste_length_limit_is_in_characters_and_rejection_is_atomic(self):
        text = 'Я' * 65536
        self.assertEqual(terminal.paste_text(text), text)
        with self.assertRaisesRegex(ValueError, '64'):
            terminal.paste_text(text + 'x')
        self.assertEqual(text, 'Я' * 65536)


class TerminalLauncherTests(unittest.TestCase):
    def setUp(self):
        self.ui = mock.Mock(demo=False)
        self.ui.screen.graphical = True
        self.ui.settings.data = dict(DEFAULTS)
        self.process = mock.Mock(returncode=0)
        self.process.poll.side_effect = [None, 0]

    def test_selected_audio_device_is_passed_as_one_argument(self):
        name = 'alsa_output.usb-Selected_Headset.analog-stereo'
        self.ui.speech.output_name = name
        with mock.patch('terminal.subprocess.Popen', return_value=self.process) as spawn, \
                mock.patch('terminal.subprocess.run'), mock.patch('time.sleep'):
            terminal.launch(self.ui, '/tmp')
        argv = spawn.call_args.args[0]
        self.assertEqual(argv[argv.index('--output') + 1], name)
        self.assertEqual(argv[-2:], ['--directory', '/tmp'])

    def test_production_launch_is_cgroup_scoped_and_restores_menu(self):
        environment = {'DISPLAY': ':7', 'XAUTHORITY': '/run/display/auth', 'PIPEWIRE_REMOTE': 'pipewire-0'}
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch('terminal.uuid.uuid4', return_value=mock.Mock(hex='fixed')), \
                mock.patch('terminal.subprocess.Popen', return_value=self.process) as spawn, \
                mock.patch('terminal.subprocess.run') as run, mock.patch('time.sleep'):
            terminal.launch(self.ui, '/mnt/usb/Рабочая папка')
        argv = spawn.call_args.args[0]
        for flag in ('--collect', '--wait', '--pipe', '--service-type=exec', '--expand-environment=no',
                     '--property=KillMode=control-group', '--property=TimeoutStopSec=5s',
                     '--property=SendSIGKILL=yes', '--property=BindsTo=flashos.service',
                     '--property=After=flashos.service', '--setenv=DISPLAY=:7',
                     '--setenv=XAUTHORITY=/run/display/auth', '--setenv=PIPEWIRE_REMOTE=pipewire-0'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[-1], '/mnt/usb/Рабочая папка')
        self.assertEqual(json.loads(argv[argv.index('--settings') + 1]),
                         {key: DEFAULTS[key] for key in ('voice', 'rate', 'pitch', 'volume', 'signals')})
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ['systemctl', 'stop', 'flashos-terminal-fixed.service'])
        self.assertEqual(run.call_args.kwargs['timeout'], 12)
        self.ui.speech.stop.assert_called_once()
        self.ui.screen.suspend.assert_called_once()
        self.ui.screen.root.update.assert_called_once()
        self.ui.screen.resume.assert_called_once()
        self.ui.speech.say.assert_called_once_with('Терминал закрыт. Инструменты.')

    def test_failed_spawn_still_stops_possible_unit_and_resumes_menu(self):
        with mock.patch('terminal.subprocess.Popen', side_effect=OSError('cannot execute')), \
                mock.patch('terminal.subprocess.run') as run:
            with self.assertRaisesRegex(OSError, 'cannot execute'):
                terminal.launch(self.ui, '/tmp')
        self.assertEqual(run.call_args.args[0][:2], ['systemctl', 'stop'])
        self.ui.screen.resume.assert_called_once()

    def test_gui_loop_failure_still_stops_unit_and_restores_menu(self):
        self.ui.screen.root.update.side_effect = RuntimeError('display gone')
        with mock.patch('terminal.subprocess.Popen', return_value=self.process), \
                mock.patch('terminal.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'display gone'):
                terminal.launch(self.ui, '/tmp')
        self.assertEqual(run.call_args.args[0][:2], ['systemctl', 'stop'])
        self.ui.screen.resume.assert_called_once()

    def test_terminal_only_shows_explanation_in_text_mode(self):
        self.ui.screen.graphical = False
        with mock.patch('terminal.subprocess.Popen') as spawn:
            terminal.launch(self.ui, '/tmp')
        spawn.assert_not_called()
        self.ui.message.assert_called_once()
        self.ui.screen.suspend.assert_not_called()


class BashPTY:
    """Own a real interactive Bash and controlling PTY in an isolated RAM session."""
    def __init__(self, session, directory):
        import pty
        self.session = session
        self.buffer = b''
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                os.chdir(directory)
                environment = session.environment()
                environment.update(HOME=str(directory), INPUTRC='/dev/null', LC_ALL='C.UTF-8')
                os.execvpe(session.argv[0], session.argv, environment)
            finally:
                os._exit(127)
        os.set_blocking(self.fd, False)
        try:
            self.wait_prompt()
        except BaseException:
            self.close()
            raise

    def read(self, timeout=.05):
        if select.select([self.fd], [], [], timeout)[0]:
            try:
                self.buffer = (self.buffer + os.read(self.fd, 65536))[-131072:]
            except OSError:
                pass

    def until(self, predicate, message, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            self.read()
        raise AssertionError(message + ': ' + repr(self.buffer[-3000:]))

    def write(self, text):
        os.write(self.fd, text.encode('utf-8') if isinstance(text, str) else text)

    @property
    def output(self):
        return re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', self.buffer).replace(b'\r', b'')

    def wait_prompt(self):
        self.until(lambda: self.session.state() and self.buffer.endswith(b'# '), 'Bash prompt did not appear')

    def command(self):
        result = []
        def submitted():
            value = self.session.command()
            if value is not None:
                result.append(value)
                return True
            return False
        self.until(submitted, 'Readline did not report a command')
        return result[0]

    def run(self, text):
        self.buffer = b''
        self.write(text + '\r')
        command = self.command()
        self.wait_prompt()
        return command

    def close(self):
        try:
            # The shell and child job may have distinct process groups. Both are
            # confined to this PTY session; tests also interrupt any running job.
            self.buffer = b''
            self.write(b'\x03')
            self.until(lambda: self.session.state() and self.buffer.endswith(b'# '),
                       'Cleanup prompt', timeout=.3)
            self.write(b'exit\r')
        except (OSError, AssertionError):
            pass
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if os.waitpid(self.pid, os.WNOHANG)[0]:
                break
            self.read()
        else:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(self.pid, 0)
        os.close(self.fd)


@unittest.skipUnless(os.name == 'posix' and Path('/bin/bash').is_file(), 'Real Bash/PTY requires Linux')
class BashReadlineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.session = terminal.ShellSession(self.folder.name)
        self.addCleanup(self.session.close)
        self.bash = BashPTY(self.session, self.folder.name)
        self.addCleanup(self.bash.close)

    def test_enter_reports_actual_submitted_command_and_executes_it(self):
        command = "printf '__EXECUTED__\\n'"
        self.assertEqual(self.bash.run(command), command)
        self.assertIn(b'\n__EXECUTED__\n', self.bash.output)
        self.assertIsNone(self.session.command())

    def test_history_up_executes_and_reports_recalled_command(self):
        command = "printf '__HISTORY__\\n'"
        self.assertEqual(self.bash.run(command), command)
        self.bash.buffer = b''
        self.bash.write(b'\x1b[A\r')
        self.assertEqual(self.bash.command(), command)
        self.bash.wait_prompt()
        self.assertIn(b'\n__HISTORY__\n', self.bash.output)

    def test_tab_completion_reports_completed_filename(self):
        (Path(self.folder.name) / 'completion-unique.txt').write_text('fixture')
        self.bash.buffer = b''
        self.bash.write("printf '%s\\n' completion-un\t\r")
        command = self.bash.command()
        self.bash.wait_prompt()
        self.assertEqual(command.rstrip(), "printf '%s\\n' completion-unique.txt")
        self.assertNotIn('\t', command)
        self.assertIn(b'\ncompletion-unique.txt\n', self.bash.output)

    def test_ctrl_c_cancels_partial_input_without_reporting_it(self):
        self.bash.buffer = b''
        self.bash.write(b'printf SHOULD_NOT_EXECUTE\x03')
        self.bash.wait_prompt()
        self.assertIsNone(self.session.command())
        self.assertEqual(self.bash.run("printf '__AFTER_CANCEL__\\n'"), "printf '__AFTER_CANCEL__\\n'")

    def test_ctrl_c_stops_foreground_job_and_returns_usable_prompt(self):
        self.bash.buffer = b''
        self.bash.write('sleep 30\r')
        self.assertEqual(self.bash.command(), 'sleep 30')
        self.bash.until(lambda: not self.session.state(), 'Foreground job was not marked busy')
        self.bash.write(b'\x03')
        self.bash.wait_prompt()
        self.assertEqual(self.bash.run("printf '__AFTER_JOB__\\n'"), "printf '__AFTER_JOB__\\n'")

    def test_read_s_does_not_publish_hidden_input_as_command_or_history(self):
        command = "read -s -p 'SECRET>' secret; printf '\\n__READ_DONE__\\n'"
        self.bash.buffer = b''
        self.bash.write(command + '\r')
        self.assertEqual(self.bash.command(), command)
        self.bash.until(lambda: self.bash.buffer.endswith(b'SECRET>') and not self.session.state(), 'Hidden read did not start')
        secret = 'PRIVATE-password-83'
        self.bash.write(secret + '\r')
        self.bash.wait_prompt()
        self.assertIsNone(self.session.command())
        self.assertNotIn(secret.encode(), (self.session.path / 'command').read_bytes())
        self.assertNotIn(secret.encode(), self.bash.buffer)
        self.bash.run("printf '<HISTFILE=%s>\\n' \"$HISTFILE\"")
        self.assertIn(b'<HISTFILE=/dev/null>', self.bash.buffer)
        self.assertFalse((Path(self.folder.name) / '.bash_history').exists())

    def test_status_of_previous_command_is_preserved_by_readline_hook(self):
        self.bash.run('false')
        self.bash.run("printf '__STATUS_%s__\\n' \"$?\"")
        self.assertIn(b'\n__STATUS_1__\n', self.bash.output)

    def test_empty_enter_returns_to_ready_without_disabling_next_command(self):
        self.assertEqual(self.bash.run(''), '')
        self.assertTrue(self.session.state())
        self.assertEqual(self.bash.run("printf '__AFTER_EMPTY__\\n'"), "printf '__AFTER_EMPTY__\\n'")

    def test_bracketed_multiline_paste_waits_for_enter_and_reports_whole_block(self):
        value = "printf '__PASTE_ONE__\\n'\nprintf '__PASTE_TWO__\\n'\n"
        self.bash.buffer = b''
        self.bash.write('\x1b[200~' + value + '\x1b[201~')
        self.bash.until(lambda: b'__PASTE_TWO__' in self.bash.buffer, 'Bracketed paste was not displayed')
        self.assertTrue(self.session.state())
        self.assertIsNone(self.session.command())
        self.assertNotIn(b'\n__PASTE_ONE__\n', self.bash.output)
        self.bash.write('\r')
        self.assertEqual(self.bash.command().strip(), value.strip())
        self.bash.wait_prompt()
        self.assertIn(b'\n__PASTE_ONE__\n__PASTE_TWO__\n', self.bash.output)

    def test_backslash_continuation_is_ready_reports_each_line_and_executes_normally(self):
        self.bash.buffer = b''
        first = "printf '%s\\n' \\"
        self.bash.write(first + '\r')
        self.assertEqual(self.bash.command(), first)
        self.bash.until(lambda: self.bash.buffer.endswith(b'...> ') and self.session.state(),
                        'Ready backslash continuation prompt')
        second = "'__CONTINUED__'"
        self.bash.write(second + '\r')
        self.assertEqual(self.bash.command(), second)
        self.bash.wait_prompt()
        self.assertIn(b'\n__CONTINUED__\n', self.bash.output)
        self.assertTrue(self.session.state())

    def test_quoted_multiline_is_ready_reports_each_line_and_preserves_newline(self):
        self.bash.buffer = b''
        first = "printf '%s\\n' '__QUOTE_FIRST__"
        self.bash.write(first + '\r')
        self.assertEqual(self.bash.command(), first)
        self.bash.until(lambda: self.bash.buffer.endswith(b'...> ') and self.session.state(),
                        'Ready quoted continuation prompt')
        second = "__QUOTE_SECOND__'"
        self.bash.write(second + '\r')
        self.assertEqual(self.bash.command(), second)
        self.bash.wait_prompt()
        self.assertIn(b'\n__QUOTE_FIRST__\n__QUOTE_SECOND__\n', self.bash.output)
        self.assertTrue(self.session.state())

    def test_read_es_does_not_publish_hidden_readline_input_or_repeat_previous_command(self):
        command = "read -e -s -p 'SECRET>' secret; printf '\\n__READ_LENGTH_%s__\\n' \"${#secret}\""
        self.bash.buffer = b''
        self.bash.write(command + '\r')
        self.assertEqual(self.bash.command(), command)
        self.bash.until(lambda: self.bash.buffer.endswith(b'SECRET>') and not self.session.state(), 'Hidden Readline did not start')
        secret = 'PRIVATE-password-91'
        self.bash.write(secret + '\r')
        self.bash.wait_prompt()
        self.assertIsNone(self.session.command())
        self.assertNotIn(secret.encode(), (self.session.path / 'command').read_bytes())
        self.assertNotIn(secret.encode(), self.bash.buffer)
        self.assertIn(('\n__READ_LENGTH_' + str(len(secret)) + '__\n').encode(), self.bash.output)
        self.assertEqual(self.bash.run("printf '__NEXT_COMMAND__\\n'"), "printf '__NEXT_COMMAND__\\n'")


if __name__ == '__main__':
    unittest.main()
