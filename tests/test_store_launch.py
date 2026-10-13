"""Launch validation and cleanup without touching displays, mounts or host units."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import store_launch
import store_session

ENTRY = {'name': 'Текстовый редактор', 'argv': ['/usr/bin/editor', 'file with spaces'], 'terminal': False}


class EntryTests(unittest.TestCase):
    def test_invalid_commands_never_enter_launcher(self):
        broken = [None, {}, {**ENTRY, 'terminal': 1}, {**ENTRY, 'argv': 'sh -c command'},
                  {**ENTRY, 'argv': ['relative']}, {**ENTRY, 'argv': ['/usr/../bin/tool']},
                  {**ENTRY, 'argv': ['/bin/tool', 'a\x00b']}, {**ENTRY, 'argv': ['/bin/tool', 'a\nb']},
                  {**ENTRY, 'argv': ['/bin/tool', None]}, {**ENTRY, 'name': 'a\nb'},
                  {**ENTRY, 'argv': ['/bin/tool'] + ['x'] * 128}]
        for entry in broken:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                store_session.validate_entry(entry)

    def test_shell_metacharacters_remain_literal_arguments(self):
        entry = {**ENTRY, 'argv': ['/bin/tool', '$(touch /tmp/should-not-exist)', 'a;reboot', '$HOME']}
        self.assertEqual(store_session.application_command(entry), entry['argv'])
        self.assertEqual(store_session.application_command({**entry, 'terminal': True}),
                         ['/usr/bin/gnome-terminal', '--wait', '--', '/usr/bin/python3',
                          '/opt/flashos-store/store_session.py', '--terminal-command', *entry['argv']])

    def test_direct_gnome_terminal_waits_without_changing_arguments(self):
        for argv in (['/usr/bin/gnome-terminal'], ['/usr/bin/gnome-terminal', '--', '/bin/echo', '--wait']):
            entry = {**ENTRY, 'argv': argv}
            self.assertEqual(store_session.application_command(entry), [argv[0], '--wait', *argv[1:]])
        argv = ['/usr/bin/gnome-terminal', '--wait', '--', '/bin/true']
        self.assertEqual(store_session.application_command({**ENTRY, 'argv': argv}), argv)

    def test_terminal_nonzero_result_stays_visible_until_input(self):
        order = []
        def run(argv, **kwargs):
            order.append(('run', argv, kwargs))
            return mock.Mock(returncode=7)
        def acknowledge():
            order.append(('acknowledge',))
            return ''
        with mock.patch('store_session.subprocess.run', side_effect=run), \
             mock.patch('builtins.input', side_effect=acknowledge), mock.patch('builtins.print') as print_result:
            self.assertEqual(store_session.terminal_command(['/bin/false']), 7)
        self.assertEqual(order, [('run', ['/bin/false'], {'check': False}), ('acknowledge',)])
        self.assertIn('Код: 7', print_result.call_args.args[0])

    @unittest.skipUnless(os.name == 'posix', 'Linux interactive command')
    def test_terminal_command_inherits_input_and_keeps_completed_output(self):
        helper = Path(store_session.__file__)
        command = [sys.executable, str(helper), '--terminal-command', sys.executable, '-c',
                   'print("value="+input(),flush=True)']
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.stdin.write(b'from-terminal\n')
            process.stdin.flush()
            self.assertEqual(process.stdout.readline(), b'value=from-terminal\n')
            self.assertEqual(process.stdout.readline(), b'\n')
            self.assertIn('Enter', process.stdout.readline().decode())
            self.assertIsNone(process.poll(), 'Short command closed before its result was read')
            process.stdin.write(b'\n')
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    @unittest.skipUnless(os.name == 'posix', 'Linux controlling terminal')
    def test_ctrl_c_interrupts_foreground_command_but_keeps_terminal_open(self):
        import fcntl
        import pty
        import select
        import signal
        import termios
        master, slave = pty.openpty()
        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        process = subprocess.Popen([sys.executable, store_session.__file__, '--terminal-command',
                                    sys.executable, '-c', 'import time;print("ready",flush=True);time.sleep(30)'],
                                   stdin=slave, stdout=slave, stderr=slave, preexec_fn=controlling_terminal)
        os.close(slave)
        output = bytearray()
        def read_until(marker):
            deadline = time.monotonic() + 5
            while marker not in output and time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    output.extend(os.read(master, 4096))
            self.assertIn(marker, output)
        try:
            read_until(b'ready')
            os.write(master, b'\x03')
            read_until('Enter — закрыть окно'.encode())
            self.assertIsNone(process.poll())
            os.write(master, b'\n')
            self.assertEqual(process.wait(timeout=5), 130)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            os.close(master)

    @unittest.skipUnless(os.name == 'posix', 'Linux process lifecycle')
    def test_reader_failure_preserves_application_until_explicit_return(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            app = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
            active = mock.Mock()
            active.poll.return_value = None
            failed_reader = mock.Mock()
            failed_reader.poll.return_value = 1
            outcome = []
            thread = threading.Thread(target=lambda: outcome.append(store_session.wait_application(
                app, active, {'Orca': failed_reader}, threading.Event(), runtime)), daemon=True)
            try:
                thread.start()
                deadline = time.monotonic() + 3
                warning = runtime / 'warning.json'
                while not warning.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(warning.exists())
                self.assertTrue(json.loads(warning.read_text())['preserved_app'])
                self.assertIsNone(app.poll(), 'Reader failure terminated an application with user data')
                self.assertTrue(thread.is_alive())
                (runtime / 'return').touch()
                thread.join(timeout=3)
                self.assertEqual(outcome, [0])
            finally:
                (runtime / 'return').touch()
                thread.join(timeout=3)
                app.terminate()
                app.wait(timeout=3)

    def test_failed_warning_write_does_not_stop_application(self):
        active = mock.Mock()
        active.poll.return_value = None
        failed = mock.Mock()
        failed.poll.return_value = 1
        stop = threading.Event()
        timer = threading.Timer(.15, stop.set)
        try:
            timer.start()
            with tempfile.TemporaryDirectory() as directory, \
                 mock.patch.object(Path, 'write_text', side_effect=OSError('full runtime')) as write, \
                 mock.patch('builtins.print'):
                self.assertEqual(store_session.wait_application(active, active, {'Orca': failed}, stop, Path(directory)), 0)
                write.assert_called_once()
            active.terminate.assert_not_called()
            active.kill.assert_not_called()
        finally:
            timer.cancel()


    def test_private_preferences_apply_without_changing_unrelated_environment(self):
        import store_preferences
        values = {store_preferences.KEYS['text_scale']: 1.5,
                  store_preferences.KEYS['contrast']: True,
                  store_preferences.KEYS['animations']: False,
                  store_preferences.KEYS['cursor_size']: 32,
                  store_preferences.REQUIRED[0]: True,
                  store_preferences.REQUIRED[1]: True}
        backend = mock.Mock()
        backend.get.side_effect = lambda schema, key: values[(schema, key)]
        module = mock.Mock()
        module.session_environment.side_effect = lambda: store_preferences.session_environment(backend)
        with mock.patch.dict(sys.modules, {'preferences': module}), \
             mock.patch.dict(os.environ, {'DISPLAY': ':0', 'HOME': '/home/flashos-app'}, clear=True):
            store_session.apply_session_preferences()
            self.assertEqual(os.environ['GDK_DPI_SCALE'], '1.5')
            self.assertEqual(os.environ['GDK_SCALE'], '1')
            self.assertEqual(os.environ['XCURSOR_SIZE'], '32')
            self.assertEqual(os.environ['GTK_THEME'], 'HighContrast')
            self.assertEqual(os.environ['HOME'], '/home/flashos-app')
            self.assertEqual(os.environ['DISPLAY'], ':0')
            # Existing custom theme and values outside the utility choices are preserved.
            values[store_preferences.KEYS['text_scale']] = 1.1
            values[store_preferences.KEYS['cursor_size']] = 27
            values[store_preferences.KEYS['contrast']] = False
            with mock.patch.dict(os.environ, {'GTK_THEME': 'UserTheme'}, clear=True):
                store_session.apply_session_preferences()
                self.assertEqual(dict(os.environ), {'GTK_THEME': 'UserTheme'})


    def test_hangup_confirms_window_close_but_preserves_completed_cli_errors(self):
        entry = {**ENTRY, 'terminal': True}
        with tempfile.TemporaryDirectory() as directory, mock.patch('builtins.print'):
            runtime = Path(directory)
            for completed, expected in ((None, 0), (0, 0), (7, 7), (137, 137)):
                with self.subTest(completed=completed):
                    (runtime/'terminal-hangup.json').write_text(json.dumps(
                        {'format':1,'signal':'SIGHUP','command_returncode':completed}))
                    self.assertEqual(store_session.terminal_exit_status(entry,137,runtime), expected)
                    diagnostic = json.loads((runtime/'terminal-exit.json').read_text())
                    self.assertEqual(diagnostic['frontend_returncode'],137)
                    self.assertEqual(diagnostic['returncode'],expected)

    def test_unconfirmed_terminal_kill_remains_error_even_after_successful_cli(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch('builtins.print'):
            runtime=Path(directory)
            (runtime/'terminal-completed.json').write_text(json.dumps({'format':1,'command_returncode':0}))
            self.assertEqual(store_session.terminal_exit_status({**ENTRY,'terminal':True},137,runtime,0),137)
            (runtime/'terminal-hangup.json').write_text(json.dumps(
                {'format':1,'signal':'SIGHUP','command_returncode':True}))
            self.assertEqual(store_session.terminal_exit_status({**ENTRY,'terminal':True},137,runtime,0),137)

    def test_direct_gui_and_explicit_terminal_commands_do_not_hide_exit137(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch('builtins.print'):
            runtime=Path(directory)
            (runtime/'terminal-hangup.json').write_text(json.dumps(
                {'format':1,'signal':'SIGHUP','command_returncode':None}))
            self.assertEqual(store_session.terminal_exit_status(ENTRY,137,runtime),137)
            explicit={**ENTRY,'argv':['/usr/bin/gnome-terminal','--','/usr/bin/tool']}
            self.assertEqual(store_session.terminal_exit_status(explicit,137,runtime),137)
            bare={**ENTRY,'argv':['/usr/bin/gnome-terminal']}
            self.assertEqual(store_session.terminal_exit_status(bare,137,runtime),0)

    def test_explicit_return_and_stop_remain_success_with_failed_terminal_code(self):
        app=mock.Mock()
        app.poll.return_value=137
        app.returncode=137
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)
            stop=threading.Event();stop.set()
            self.assertEqual(store_session.wait_application(app,app,{},stop,runtime,{**ENTRY,'terminal':True}),0)
            stop.clear();(runtime/'return').touch()
            self.assertEqual(store_session.wait_application(app,app,{},stop,runtime,{**ENTRY,'terminal':True}),0)

    @unittest.skipUnless(os.name == 'posix', 'Linux controlling terminal hangup')
    def test_real_pty_window_hangup_has_positive_receipt_and_bounded_exit(self):
        import fcntl,pty,select,signal,termios
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)
            master,slave=pty.openpty()
            def controlling_terminal():
                os.setsid();fcntl.ioctl(0,termios.TIOCSCTTY,0)
            runner=('import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
                    'import store_session;store_session.RUNTIME=Path(sys.argv[2]);'
                    'sys.exit(store_session.terminal_command([sys.executable,"-c",sys.argv[3]]))')
            process=subprocess.Popen([sys.executable,'-c',runner,str(Path(store_session.__file__).parent),directory,
                                      'import time;print("ready",flush=True);time.sleep(30)'],
                                     stdin=slave,stdout=slave,stderr=slave,preexec_fn=controlling_terminal)
            os.close(slave)
            output=bytearray()
            try:
                deadline=time.monotonic()+5
                while b'ready' not in output and time.monotonic()<deadline:
                    if select.select([master],[],[],.1)[0]:output.extend(os.read(master,4096))
                self.assertIn(b'ready',output)
                # VTE explicitly sends HUP to the PTY process group before disposal.
                os.killpg(process.pid,signal.SIGHUP)
                os.close(master);master=None
                self.assertEqual(process.wait(timeout=5),0)
                receipt=json.loads((runtime/'terminal-hangup.json').read_text())
                self.assertEqual(receipt,{'format':1,'signal':'SIGHUP','command_returncode':None})
                with mock.patch('builtins.print'):
                    self.assertEqual(store_session.terminal_exit_status({**ENTRY,'terminal':True},137,runtime),0)
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait()
                if master is not None:os.close(master)

    @unittest.skipUnless(os.name == 'posix', 'Linux command exit and acknowledgement')
    def test_real_cli_exit137_is_recorded_before_enter_and_remains_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)
            runner=('import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
                    'import store_session;store_session.RUNTIME=Path(sys.argv[2]);'
                    'sys.exit(store_session.terminal_command([sys.executable,"-c","raise SystemExit(137)"]))')
            process=subprocess.Popen([sys.executable,'-c',runner,str(Path(store_session.__file__).parent),directory],
                                     stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                self.assertEqual(process.stdout.readline(),b'\n')
                self.assertIn('137',process.stdout.readline().decode())
                self.assertIsNone(process.poll())
                self.assertEqual(json.loads((runtime/'terminal-completed.json').read_text())['command_returncode'],137)
                process.stdin.write(b'\n');process.stdin.flush()
                self.assertEqual(process.wait(timeout=5),137)
                with mock.patch('builtins.print'):
                    self.assertEqual(store_session.terminal_exit_status({**ENTRY,'terminal':True},137,runtime,0),137)
            finally:
                if process.poll() is None:process.kill();process.wait()
                process.stdin.close();process.stdout.close();process.stderr.close()


    def test_delayed_hangup_receipt_is_observed_before_bounded_deadline(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch('builtins.print'):
            runtime=Path(directory)
            def acknowledge():
                store_session.terminal_receipt('terminal-hangup.json',
                    {'format':1,'signal':'SIGHUP','command_returncode':None},runtime)
            timer=threading.Timer(.05,acknowledge)
            try:
                timer.start()
                started=time.monotonic()
                self.assertEqual(store_session.terminal_exit_status({**ENTRY,'terminal':True},137,runtime,.5),0)
                self.assertLess(time.monotonic()-started,.5)
            finally:
                timer.join()

    @unittest.skipUnless(os.name == 'posix', 'Linux killed command followed by window hangup')
    def test_real_sigkill_cli_error_survives_later_window_hangup(self):
        import signal
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)
            runner=('import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
                    'import store_session;store_session.RUNTIME=Path(sys.argv[2]);'
                    'sys.exit(store_session.terminal_command([sys.executable,"-c",'
                    '"import os,signal;os.kill(os.getpid(),signal.SIGKILL)"]))')
            process=subprocess.Popen([sys.executable,'-c',runner,str(Path(store_session.__file__).parent),directory],
                                     stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                self.assertEqual(process.stdout.readline(),b'\n')
                self.assertIn('137',process.stdout.readline().decode())
                self.assertIsNone(process.poll())
                self.assertEqual(json.loads((runtime/'terminal-completed.json').read_text())['command_returncode'],137)
                process.send_signal(signal.SIGHUP)
                deadline=time.monotonic()+2
                while not (runtime/'terminal-hangup.json').exists() and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(json.loads((runtime/'terminal-hangup.json').read_text())['command_returncode'],137)
                process.stdin.write(b'\n');process.stdin.flush()
                self.assertEqual(process.wait(timeout=5),137)
                with mock.patch('builtins.print'):
                    self.assertEqual(store_session.terminal_exit_status({**ENTRY,'terminal':True},137,runtime),137)
            finally:
                if process.poll() is None:process.kill();process.wait()
                process.stdin.close();process.stdout.close();process.stderr.close()


    def test_hangup_between_completed_process_and_status_assignment_preserves137(self):
        callbacks={}
        hup=1
        def register(number,handler):
            previous=callbacks.get(number)
            callbacks[number]=handler
            return previous
        class Completed:
            first=True
            @property
            def returncode(self):
                if self.first:
                    self.first=False
                    callbacks[hup]()
                return 137
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)
            with mock.patch.object(store_session,'RUNTIME',runtime), \
                 mock.patch.object(store_session.signal,'SIGHUP',hup,create=True), \
                 mock.patch('store_session.signal.signal',side_effect=register), \
                 mock.patch('store_session.subprocess.run',return_value=Completed()), \
                 mock.patch('builtins.input') as acknowledge, mock.patch('builtins.print'):
                self.assertEqual(store_session.terminal_command(['/bin/tool']),137)
                self.assertEqual(json.loads((runtime/'terminal-hangup.json').read_text())['command_returncode'],137)
                self.assertEqual(json.loads((runtime/'terminal-completed.json').read_text())['command_returncode'],137)
                self.assertEqual(store_session.terminal_exit_status({**ENTRY,'terminal':True},137,runtime),137)
                acknowledge.assert_not_called()

    def test_log_has_fixed_maximum_and_retains_latest_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.log'
            log = store_session.BoundedLog(path, limit=1024)
            try:
                for _ in range(100):
                    log.write(b'x' * 250)
                log.write(b'FINAL OUTPUT')
            finally:
                log.close()
            self.assertLessEqual(path.stat().st_size, 1024)
            self.assertTrue(path.read_bytes().endswith(b'FINAL OUTPUT'))

    def test_orca_profile_survives_future_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = store_session.seed_preferences(Path(directory))
            path = folder / 'user-settings.conf'
            data = json.loads(path.read_text())
            self.assertEqual(data['profiles']['default']['voices']['default']['family']['lang'], 'ru')
            path.write_text('user profile')
            store_session.seed_preferences(Path(directory))
            self.assertEqual(path.read_text(), 'user profile')

    @unittest.skipUnless(os.name == 'posix', 'Linux process termination')
    def test_child_ignoring_term_is_killed_with_bounded_wait(self):
        child = subprocess.Popen([sys.executable, '-c',
                                  'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("ready",flush=True);time.sleep(30)'],
                                 stdout=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline(), b'ready\n')
            started = time.monotonic()
            store_session.terminate_children([child], timeout=.1)
            self.assertLess(time.monotonic() - started, 3)
            self.assertIsNotNone(child.poll())
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stdout.close()


class LauncherTests(unittest.TestCase):
    def launcher(self, root=None, graphical=True):
        ui = mock.Mock()
        ui.screen.graphical = graphical
        return store_launch.StoreLauncher(mock.Mock(root=root), ui)

    def test_console_refusal_does_not_authorize_or_start_process(self):
        launcher = self.launcher(graphical=False)
        with mock.patch.object(launcher, '_prepare') as prepare, self.assertRaisesRegex(RuntimeError, 'графическом'):
            launcher.launch(ENTRY)
        prepare.assert_not_called()

    def test_transient_session_has_nonroot_identity_and_controlled_cgroup(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'root'
            helper = root / 'opt/flashos-store/store_session.py'
            helper.parent.mkdir(parents=True)
            helper.touch()
            launcher = self.launcher(root)
            with mock.patch.object(store_launch, 'RUNTIME_BASE', base / 'runtime'), \
                 mock.patch.dict(os.environ, {'DISPLAY': ':0'}), \
                 mock.patch('store_launch.os.geteuid', return_value=0, create=True), \
                 mock.patch('store_launch.os.chown', create=True), \
                 mock.patch.object(launcher, '_command') as command:
                argv = launcher._prepare(ENTRY)
                self.assertIn('--property=User=1000', argv)
                self.assertIn('--property=KillMode=control-group', argv)
                self.assertIn('--property=BindsTo=flashos.service', argv)
                self.assertIn('--property=RootDirectory=' + str(root), argv)
                self.assertIn('--expand-environment=no', argv)
                self.assertEqual(command.call_args.args[0], ['xhost', '+SI:localuser:flashos-app'])
                self.assertEqual(json.loads((launcher.runtime / 'config.json').read_text(encoding='utf-8')), ENTRY)

    def test_startup_failure_leaves_screen_visible_and_runs_cleanup(self):
        launcher = self.launcher()
        with mock.patch.object(launcher, 'running', return_value=False), \
             mock.patch.object(launcher, '_prepare', return_value=['systemd-run']), \
             mock.patch.object(launcher, '_command', side_effect=RuntimeError('start failed')), \
             mock.patch.object(launcher, 'close') as close:
            with self.assertRaisesRegex(RuntimeError, 'start failed'):
                launcher.launch(ENTRY)
        close.assert_called_once()
        launcher.ui.screen.suspend.assert_not_called()
        launcher.ui.screen.resume.assert_not_called()

    def test_application_error_returns_focus_and_closes_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = self.launcher()
            launcher.runtime = Path(directory)
            (launcher.runtime / 'result.json').write_text(json.dumps({'returncode': 3, 'error': 'missing font'}))
            with mock.patch.object(launcher, 'running', side_effect=[False, True, False]), \
                 mock.patch.object(launcher, '_prepare', return_value=['systemd-run']), \
                 mock.patch.object(launcher, '_command'), mock.patch.object(launcher, 'close') as close:
                with self.assertRaisesRegex(RuntimeError, 'missing font'):
                    launcher.launch(ENTRY)
            close.assert_called_once()
            launcher.ui.screen.suspend.assert_called_once()
            launcher.ui.screen.resume.assert_called_once()

    def test_reader_warning_is_spoken_once_without_closing_application(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = self.launcher()
            launcher.runtime = Path(directory)
            warning = 'Озвучка остановилась. Сохраните документ.'
            (launcher.runtime / 'warning.json').write_text(json.dumps({'warning': warning}))
            with mock.patch.object(launcher, 'close') as close:
                launcher._announce_warning()
                launcher._announce_warning()
            launcher.ui.speech.say.assert_called_once_with(warning)
            close.assert_not_called()

    def test_stop_timeout_escalates_for_all_unit_processes(self):
        launcher = self.launcher()
        launcher.unit = 'flashos-app-test.service'
        with mock.patch.object(launcher, 'running', side_effect=[True, False, False]), \
             mock.patch.object(launcher, '_command', side_effect=[subprocess.TimeoutExpired('stop', 20), mock.Mock()]) as command:
            launcher.close()
        self.assertEqual(command.call_args.args[0],
                         ['systemctl', 'kill', '--kill-whom=all', '--signal=KILL', 'flashos-app-test.service'])
        self.assertIsNone(launcher.unit)

    def test_stuck_unit_retains_runtime_and_blocks_unmount(self):
        launcher = self.launcher()
        launcher.unit = 'flashos-app-test.service'
        launcher.runtime = Path('/run/flashos-app-user/test')
        with mock.patch.object(launcher, 'running', return_value=True), \
             mock.patch.object(launcher, '_command'), mock.patch('store_launch.time.monotonic', side_effect=[0, 6]), \
             mock.patch('store_launch.shutil.rmtree') as remove:
            with self.assertRaisesRegex(RuntimeError, 'нельзя отключать'):
                launcher.close()
        self.assertIsNotNone(launcher.unit)
        remove.assert_not_called()

    def test_cleanup_only_removes_temporary_runtime_keeps_user_home(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime_base = base / 'runtime'
            runtime = runtime_base / 'session'
            runtime.mkdir(parents=True)
            home = base / 'home'
            home.mkdir()
            profile = home / 'profile'
            profile.write_text('saved settings')
            launcher = self.launcher()
            launcher.runtime = runtime
            with mock.patch.object(store_launch, 'RUNTIME_BASE', runtime_base):
                launcher.close()
            self.assertFalse(runtime.exists())
            self.assertEqual(profile.read_text(), 'saved settings')
