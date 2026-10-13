"""Speech skipping must keep navigation, consent and process cleanup intact."""
import curses
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from audio import Output
from interface import UI, OpenPower
from speech import Speech
from settings import DEFAULTS, DurabilityError
import menu


class Screen:
    graphical = True
    def __init__(self, keys):
        self.keys = iter(keys)
        self.frames = []
        self.timeouts = []
    def keypad(self, enabled): pass
    def timeout(self, value): self.timeouts.append(value)
    def getch(self): return next(self.keys)
    def get_wch(self):
        value = self.getch()
        return chr(value) if isinstance(value, int) and value < 256 else value
    def render(self, *args, **kwargs): self.frames.append((args, kwargs))


class Voice:
    error = ''
    volume = 75
    def __init__(self):
        self.stopped = 0
        self.spoken = []
        self.completion = threading.Event()
    def say(self, value):
        self.completion.set()
        self.completion = threading.Event()
        self.spoken.append(value)
        return self.completion
    def stop(self):
        self.stopped += 1
        self.completion.set()
    def signal(self, *args, **kwargs): pass
    def say_if_idle(self, value): pass


class SkippingTests(unittest.TestCase):
    def ui(self, keys):
        self.voice = Voice()
        self.screen = Screen(keys)
        return UI(self.screen, self.voice, Mock(), '/tmp', demo=True)

    def test_enter_space_and_f7_stop_without_confirming(self):
        for key in (10, ord(' '), curses.KEY_F7):
            with self.subTest(key=key):
                ui = self.ui([key, -1, 10])
                self.assertFalse(ui.speak_wait('Важное предупреждение'))
                self.assertTrue(self.voice.completion.is_set())
                self.assertFalse(ui.confirm('Установить пакеты?'))
                self.assertIn('Отмена', self.voice.spoken[-1])
                self.assertEqual(self.screen.timeouts, [150, 0, 150])

    def test_buffered_enter_repeats_do_not_activate_next_screen(self):
        ui = self.ui([10, 10, 10, -1, curses.KEY_DOWN, 10])
        ui.speak_wait('Вводное сообщение')
        self.assertEqual(ui.choose('Действие', [('Отмена', False), ('Продолжить', True)]), True)
        self.assertEqual(self.voice.stopped, 1)
        self.assertEqual(ui.pending_keys, __import__('collections').deque())

    def test_navigation_after_a_skip_is_not_discarded(self):
        ui = self.ui([10, 10, curses.KEY_DOWN, 10])
        ui.speak_wait('Сообщение')
        self.assertEqual(ui.choose('Меню', [('Первый', 1), ('Второй', 2)]), 2)

    def test_arrow_escape_and_tab_keep_their_menu_meaning(self):
        for key, expected in ((curses.KEY_DOWN, 2), (27, None), (9, ('__tab__', 1))):
            with self.subTest(key=key):
                ui = self.ui([key, 10])
                self.assertFalse(ui.speak_wait('Сообщение'))
                options = {'tabs': ['Один', 'Два']} if key == 9 else {}
                self.assertEqual(ui.choose('Меню', [('Первый', 1), ('Второй', 2)], **options), expected)

    def test_f10_during_speech_or_buffered_after_skip_opens_power(self):
        for keys in ([curses.KEY_F10], [10, 10, curses.KEY_F10]):
            with self.subTest(keys=keys):
                ui = self.ui(keys)
                with self.assertRaises(OpenPower):
                    ui.speak_wait('Сообщение')
                self.assertEqual(self.voice.stopped, 1)
                self.assertTrue(self.voice.completion.is_set())

    def test_display_exception_stops_the_utterance(self):
        ui = self.ui([])
        ui.draw = Mock(side_effect=RuntimeError('Display unavailable'))
        with self.assertRaisesRegex(RuntimeError, 'Display unavailable'):
            ui.speak_wait('Сообщение')
        self.assertEqual(self.voice.stopped, 1)

    def test_finished_or_absent_audio_does_not_read_a_key(self):
        ui = self.ui([])
        def complete(value):
            done = threading.Event()
            done.set()
            return done
        self.voice.say = complete
        self.assertTrue(ui.speak_wait('Сообщение'))
        self.assertEqual(self.voice.stopped, 0)

    def test_utterance_completion_and_timeout_both_release_the_ui(self):
        ui = self.ui([])
        def finish():
            self.voice.completion.set()
            return -1
        self.screen.getch = finish
        self.assertTrue(ui.speak_wait('Сообщение'))
        self.assertEqual(self.voice.stopped, 0)
        ui = self.ui([])
        with patch('interface.time.monotonic', side_effect=[0, 101]):
            self.assertFalse(ui.speak_wait('Сообщение'))
        self.assertEqual(self.voice.stopped, 1)

    def test_f7_in_secret_input_neither_submits_nor_changes_password(self):
        ui = self.ui([ord('a'), curses.KEY_F7, ord('b'), 10])
        self.assertEqual(ui.text_input('Пароль', secret=True), 'ab')
        self.assertEqual(self.voice.stopped, 1)
        self.assertNotIn('ab', self.voice.spoken)

    def test_f7_in_keyboard_test_stops_speech(self):
        ui = self.ui([curses.KEY_F7, 27])
        ui.keyboard_test()
        self.assertEqual(self.voice.stopped, 1)
        self.assertEqual(len(self.voice.spoken), 1)

    def test_f7_during_a_job_does_not_cancel_work(self):
        ui = self.ui([])
        ui.settings.data = {'progress_seconds': 30}
        first = [True]
        def read():
            time.sleep(.005)
            if first.pop() if first else False:
                return curses.KEY_F7
            return -1
        self.screen.getch = read
        def run(job):
            until = time.monotonic() + .08
            while time.monotonic() < until:
                job.check()
                time.sleep(.005)
            job.result = 'Завершено'
        result = ui.run_job('Проверка', run, review=False, save=False)
        self.assertFalse(result.cancelled)
        self.assertFalse(result.failed)
        self.assertEqual(result.result, 'Завершено')
        self.assertEqual(self.voice.stopped, 1)

    def test_f10_from_settings_error_waits_for_deferred_job_completion(self):
        for error in (OSError('USB unavailable'), DurabilityError('Sync unavailable')):
            with self.subTest(error=type(error).__name__):
                ui = self.ui([ord('+'), curses.KEY_F10])
                ui.settings.data = dict(DEFAULTS)
                ui.settings.save.side_effect = error
                self.voice.configure = Mock()
                release = threading.Event()
                writing = threading.Event()
                jobs = []
                read = self.screen.getch
                def finish_after_power_request():
                    self.assertTrue(writing.wait(1))
                    try:
                        return read()
                    except StopIteration:
                        if not release.is_set() and ui.active_job.cancel_event.is_set():
                            self.assertFalse(ui.active_job.done.is_set())
                            release.set()
                        time.sleep(.005)
                        return -1
                self.screen.getch = finish_after_power_request
                def write(job):
                    jobs.append(job)
                    job.cancellation_deferred = True
                    writing.set()
                    job.cancel_event.wait(2)
                    if not release.wait(2):
                        raise RuntimeError('Power request bypassed the live job loop')
                    job.cancellation_deferred = False
                try:
                    with self.assertRaises(OpenPower):
                        ui.run_job('Запись пакетов', write, review=False, save=False)
                    self.assertTrue(release.is_set())
                    self.assertTrue(jobs[0].done.is_set())
                    self.assertTrue(jobs[0].cancelled)
                    self.assertFalse(jobs[0].failed)
                finally:
                    release.set()

    def test_iso_reboot_does_not_wait_for_name_or_audio_completion(self):
        ui = Mock(demo=False)
        ui.state_root = Path('/tmp')
        ui.speech = Voice()
        image = types.SimpleNamespace(title='Длинное название системы', bootable=True)
        ui.choose.return_value = image
        with patch('menu.discover', return_value=[image]), patch('menu.arm') as arm, \
                patch('menu.return_to_usb', return_value=True), patch('menu.subprocess.run') as reboot:
            menu.iso_menu(ui, Path('/tmp'))
        arm.assert_called_once_with(Path('/tmp'), image, ui.state_root)
        reboot.assert_called_once_with(['systemctl', 'reboot'], check=True, timeout=15)
        self.assertFalse(ui.speech.completion.is_set(), 'Reboot was delayed until speech finished')


class WorkerTests(unittest.TestCase):
    def test_stop_releases_waiter_reaps_stubborn_processes_and_prevents_hotplug_replay(self):
        output = Output('analog', '10', 10, 'Встроенный звук', 1)
        audio = Mock()
        audio.discover.return_value = [output]
        audio.player.return_value = ['player']
        speech = Speech(audio, watch=False)
        self.addCleanup(speech.close)
        ready = threading.Event()
        processes = []
        workers = []
        real_thread = threading.Thread
        class Process:
            def __init__(self):
                import io
                self.stdin, self.stdout = io.BytesIO(), io.BytesIO()
                self.returncode = None
                self.exited = threading.Event()
            def poll(self): return self.returncode
            def terminate(self): pass  # Model a player ignoring SIGTERM.
            def kill(self): self.returncode = -9; self.exited.set()
            def wait(self, timeout=None):
                if not self.exited.wait(timeout):
                    raise AssertionError('Speech process was not killed')
                return self.returncode
        def spawn(*args, **kwargs):
            process = Process()
            processes.append(process)
            if len(processes) == 2:
                ready.set()
            return process
        def track(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            workers.append(worker)
            return worker
        with patch('speech.threading.Thread', side_effect=track), patch('speech.subprocess.Popen', side_effect=spawn):
            done = speech.say('Сохранённый текст')
            self.assertTrue(ready.wait(1))
            speech.stop()
            self.assertTrue(done.wait(.1))
            for worker in workers:
                worker.join(1)
                self.assertFalse(worker.is_alive())
            audio.discover.return_value = [Output('usb', '20', 20, 'USB', 0), output]
            speech.refresh()
            self.assertEqual(len(processes), 2, 'Stopped speech replayed on USB hotplug')
        self.assertEqual(speech.last, 'Сохранённый текст')
        self.assertTrue(all(process.exited.is_set() for process in processes))


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux curses terminal')
class NativeCursesTests(unittest.TestCase):
    def test_curses_f7_skips_speech_and_a_separate_enter_still_cancels_confirmation(self):
        import pty
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'stage'
            receipt = Path(directory) / 'result.json'
            source = str(Path(__file__).resolve().parents[1] / 'src')
            code = '''
import curses,json,sys,threading,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from interface import UI
class Voice:
    error=''
    volume=75
    stopped=0
    def say(self,text):
        self.done=threading.Event()
        return self.done
    def stop(self):
        self.stopped+=1
        self.done.set()
voice=Voice()
def run(screen):
    ui=UI(screen,voice,object(),'/tmp',demo=True)
    Path(sys.argv[2]).write_text('ready')
    started=time.monotonic()
    skipped=ui.speak_wait('Сообщение о доступности')
    elapsed=time.monotonic()-started
    Path(sys.argv[2]).write_text('confirm')
    confirmed=ui.confirm('Изменить систему?')
    Path(sys.argv[3]).write_text(json.dumps({'skipped':skipped,'confirmed':confirmed,'stopped':voice.stopped,'elapsed':elapsed}))
curses.wrapper(run)
'''
            master, slave = pty.openpty()
            child = subprocess.Popen([sys.executable, '-B', '-c', code, source, str(marker), str(receipt)],
                stdin=slave, stdout=slave, stderr=slave, env={**os.environ, 'TERM': 'xterm'})
            os.close(slave)
            def stage(value):
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    if marker.exists() and marker.read_text() == value:
                        return
                    if child.poll() is not None:
                        self.fail('Curses process exited before ' + value)
                    time.sleep(.01)
                self.fail('Curses event loop did not reach ' + value)
            try:
                stage('ready')
                os.write(master, b'\x1b[18~')
                stage('confirm')
                os.write(master, b'\r')
                self.assertEqual(child.wait(timeout=4), 0)
                result = json.loads(receipt.read_text())
                self.assertFalse(result['skipped'])
                self.assertFalse(result['confirmed'])
                self.assertEqual(result['stopped'], 1)
                self.assertLess(result['elapsed'], 1)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                os.close(master)


@unittest.skipUnless(sys.platform.startswith('linux') and os.environ.get('DISPLAY'), 'Linux Tk display')
class NativeTkTests(unittest.TestCase):
    def test_real_tk_event_loop_processes_skip_and_power(self):
        from graphical import GraphicalScreen
        for key, expected_error in (('F7', None), ('F10', OpenPower)):
            with self.subTest(key=key):
                screen = GraphicalScreen(windowed=True)
                try:
                    voice = Voice()
                    ui = UI(screen, voice, Mock(), '/tmp', demo=True)
                    screen.root.after(30, lambda: screen.root.event_generate('<KeyPress-' + key + '>'))
                    started = time.monotonic()
                    if expected_error:
                        with self.assertRaises(expected_error):
                            ui.speak_wait('Длинная инструкция', 'Проверка')
                    else:
                        self.assertFalse(ui.speak_wait('Длинная инструкция', 'Проверка'))
                    self.assertLess(time.monotonic() - started, 1)
                    self.assertEqual(voice.stopped, 1)
                finally:
                    screen.close()


if __name__ == '__main__':
    unittest.main()
