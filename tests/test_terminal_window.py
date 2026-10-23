"""Real GTK/VTE and Bash integration; all commands use a temporary workspace."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    gi.require_version('Vte', '2.91')
    from gi.repository import Gdk, GLib, Gtk
    from terminal_window import TerminalWindow
    GTK_AVAILABLE = bool(os.environ.get('DISPLAY')) and Gtk.init_check([])[0]
except (ImportError, ValueError):
    GTK_AVAILABLE = False


class RecordingSpeech:
    def __init__(self):
        self.completion = threading.Event()
        self.completion.set()
        self.calls = []
        self.stops = 0
        self.outputs_advanced = 0

    def say(self, text):
        self.calls.append(text)
        self.completion = threading.Event()
        self.completion.set()
        return self.completion

    def stop(self):
        self.stops += 1
        self.completion.set()

    def next_output(self):
        self.outputs_advanced += 1


@unittest.skipUnless(GTK_AVAILABLE and shutil.which('xdotool'), 'GTK3/VTE, an X display and xdotool are required')
class TerminalWindowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='flashos-terminal-qa-')
        self.addCleanup(self.temporary.cleanup)
        self.speech = RecordingSpeech()
        self.callback_errors = []
        self.checks = {}
        self.exception_hook = sys.excepthook
        sys.excepthook = lambda kind, value, tb: self.callback_errors.append(str(value))
        self.addCleanup(setattr, sys, 'excepthook', self.exception_hook)
        self.window = TerminalWindow(self.speech, self.temporary.name, windowed=True)
        self.addCleanup(lambda: self.window.finish() if not self.window.closed else None)
        self.window.echo_mode = 'off'
        self.wait_for(lambda: self.window.alive and self.window.session.state(), 'Bash prompt')
        self.wait_for(lambda: self.window.is_active() and self.window.terminal.is_focus(), 'bare X keyboard focus')

    def tearDown(self):
        if not self.window.closed:
            self.evidence('screen')
            if self.window.dialog is not None:
                self.window.close_dialog()
            self.window.terminal.feed_child(b'\x03exit\n')
            self.pump(.15)
            self.window.finish()
        self.pump(.15)
        directory = os.environ.get('FLASHOS_TERMINAL_QA_EVIDENCE')
        if directory:
            destination = Path(directory)
            destination.mkdir(parents=True, exist_ok=True)
            with (destination / (self._testMethodName + '.json')).open('x', encoding='utf-8') as stream:
                json.dump({'speech': self.speech.calls, 'callback_errors': self.callback_errors,
                           'checks': self.checks},
                          stream, ensure_ascii=False, indent=2)
        self.assertEqual(self.callback_errors, [], 'GTK signal/timeout callback raised an exception')

    @staticmethod
    def pump(duration=.05):
        deadline = time.monotonic() + duration
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            for _ in range(100):
                if not context.pending():
                    break
                context.iteration(False)
            time.sleep(.005)

    def wait_for(self, predicate, description, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(.02)
            self.assertEqual(self.callback_errors, [], description + ': GTK callback raised an exception')
            if predicate():
                return
        self.fail(description + ' timed out; speech tail: ' + repr(self.speech.calls[-5:]) +
                  '; callbacks: ' + repr(self.callback_errors) + '; status: ' + self.window.status.get_text())

    def keys(self, *keys):
        subprocess.run(['xdotool', 'key', '--clearmodifiers', *keys], check=True, timeout=5)
        self.pump(.08)

    def type_text(self, text):
        subprocess.run(['xdotool', 'type', '--clearmodifiers', '--delay', '0', '--', text],
                       check=True, timeout=5)
        self.pump(.08)

    def screen(self):
        _, row = self.window.terminal.get_cursor_position()
        return self.window.text_range(max(0, row - 199), row)

    def command(self, text, marker):
        self.type_text(text)
        self.keys('Return')
        self.wait_for(lambda: marker in self.screen().splitlines() and self.window.session.state(), marker)
        self.pump(.2)

    def evidence(self, suffix):
        directory = os.environ.get('FLASHOS_TERMINAL_QA_EVIDENCE')
        if directory and not self.window.closed:
            destination = Path(directory)
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / (self._testMethodName + '-' + suffix + '.png')
            if path.exists():
                raise FileExistsError(path)
            root = Gdk.get_default_root_window()
            image = Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())
            image.savev(str(path), 'png', [], [])

    def test_command_execution_and_output_are_spoken_in_order(self):
        command = "printf 'QA_OUTPUT_ONE\\nQA_OUTPUT_TWO\\n'"
        self.command(command, 'QA_OUTPUT_TWO')
        self.wait_for(lambda: any(text.startswith('Команда: ' + command) for text in self.speech.calls),
                      'submitted command speech')
        self.wait_for(lambda: any('QA_OUTPUT_ONE\nQA_OUTPUT_TWO' in text and not text.startswith('Команда:')
                                 for text in self.speech.calls), 'ordered command output speech')

    def test_ctrl_c_interrupts_a_foreground_program_and_returns_to_readline(self):
        self.type_text('sleep 30')
        self.keys('Return')
        self.wait_for(lambda: not self.window.session.state(), 'foreground sleep')
        self.keys('ctrl+c')
        self.wait_for(self.window.session.state, 'prompt after interrupt')
        self.command("printf 'QA_AFTER_INTERRUPT\\n'", 'QA_AFTER_INTERRUPT')

    def test_hidden_password_input_is_neither_visible_nor_spoken(self):
        self.window.echo_mode = 'characters'
        for flags, suffix in (('-s', 'RAW'), ('-e -s', 'READLINE')):
            with self.subTest(flags=flags):
                marker = 'QA_SECRET_DONE_' + suffix
                command = "read " + flags + " -p 'QA PASSWORD: ' qa_secret; printf '\\n" + marker + "\\n'"
                self.type_text(command)
                self.keys('Return')
                self.wait_for(lambda: not self.window.session.state() and 'QA PASSWORD:' in self.screen(), 'hidden read')
                secret = 'qaHiddenPassword28719' + suffix
                prior_calls = len(self.speech.calls)
                self.type_text(secret)
                self.keys('Return')
                self.wait_for(lambda: marker in self.screen().splitlines() and self.window.session.state(),
                              'hidden read completion')
                self.pump(.3)
                self.assertNotIn(secret, self.screen())
                self.assertNotIn(secret, '\n'.join(self.speech.calls))
                self.assertNotIn(secret, ''.join(self.speech.calls[prior_calls:]))
                self.assertNotIn(secret.encode(), (self.window.session.path / 'command').read_bytes())

    def test_background_output_is_spoken_while_shell_is_ready(self):
        self.command("(sleep .3; printf '\\nQA_BACKGROUND_OUTPUT\\n') &", 'QA_BACKGROUND_OUTPUT')
        self.wait_for(lambda: any('QA_BACKGROUND_OUTPUT' in text and not text.startswith('Команда:')
                                 for text in self.speech.calls), 'background output speech')

    def test_output_arriving_after_command_metadata_is_read_without_reading_the_next_draft(self):
        self.window.baseline()
        self.window.last_input = time.monotonic()
        with patch.object(self.window.session, 'command', side_effect=['printf QA_LATE_OUTPUT', None]):
            self.window.poll()
            self.window.terminal.feed(b'\r\nQA_LATE_OUTPUT\r\n')
            self.window.poll()
        self.wait_for(lambda: any('QA_LATE_OUTPUT' in text and not text.startswith('Команда:')
                                 for text in self.speech.calls), 'late VTE output speech')
        self.type_text('QA_NEXT_DRAFT_' + 'privateNextDraft' * 16)
        self.pump(.3)
        self.assertNotIn('privateNextDraft', '\n'.join(self.speech.calls))
        self.keys('ctrl+c')
        self.wait_for(self.window.session.state, 'prompt after next draft cancellation')

    def test_unsubmitted_draft_is_not_automatically_read_when_input_echo_is_off(self):
        draft = 'QA_UNSUBMITTED_DRAFT'
        self.type_text(draft)
        self.pump(.3)
        self.assertNotIn(draft, '\n'.join(self.speech.calls))
        self.keys('ctrl+c')
        self.wait_for(self.window.session.state, 'prompt after draft cancellation')

    def test_audio_hotkeys_delegate_output_selection_and_stop_speech(self):
        self.keys('ctrl+shift+F6')
        self.assertEqual(self.speech.outputs_advanced, 1)
        self.keys('ctrl+shift+F7')
        self.assertTrue(self.window.reader.stopped)

    def test_primary_paste_draft_is_silent_with_input_echo_off(self):
        draft = 'QA_PRIMARY_DRAFT_' + 'privateDraftSegment' * 16
        Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY).set_text(draft, -1)
        self.window.paste(primary=True)
        self.wait_for(lambda: 'QA_PRIMARY_DRAFT_' in self.screen(), 'primary paste insertion')
        self.pump(.3)
        self.assertNotIn('privateDraftSegment', '\n'.join(self.speech.calls))
        self.keys('ctrl+c')
        self.wait_for(self.window.session.state, 'prompt after paste draft cancellation')

    def test_window_sizes_keep_toolbar_terminal_and_status_visible(self):
        self.checks['sizes'] = []
        for width, height in ((640, 480), (800, 600), (1024, 768), (1366, 768)):
            with self.subTest(width=width, height=height):
                self.window.resize(width, height)
                self.pump(.2)
                actual_width, actual_height = self.window.get_size()
                record = {'requested': [width, height], 'actual': [actual_width, actual_height], 'widgets': []}
                self.checks['sizes'].append(record)
                self.evidence(f'{width}x{height}')
                self.assertEqual((actual_width, actual_height), (width, height), 'minimum layout exceeds requested screen')
                for widget in [*self.window.buttons, self.window.terminal, self.window.status]:
                    x, y = widget.translate_coordinates(self.window, 0, 0)
                    allocation = widget.get_allocation()
                    record['widgets'].append([type(widget).__name__, x, y, allocation.width, allocation.height])
                    self.assertTrue(widget.get_visible())
                    self.assertGreater(allocation.width, 0)
                    self.assertGreater(allocation.height, 0)
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x + allocation.width, actual_width)
                    self.assertLessEqual(y + allocation.height, actual_height)

    def test_retained_scrollback_review_and_visible_screen_are_readable_after_ring_discard(self):
        self.command("printf 'QA_SCROLL_%05d\\n' {1..5100}", 'QA_SCROLL_05100')
        self.wait_for(lambda: any(text.startswith('Часть быстрого вывода не озвучена.')
                                 for text in self.speech.calls), 'fast output omission notice')
        self.keys('ctrl+shift+Home')
        self.wait_for(lambda: bool(re.fullmatch(r'QA_SCROLL_\d{5}', self.speech.calls[-1])), 'first retained scrollback row')
        first_line = self.speech.calls[-1]
        self.assertGreater(int(first_line.rsplit('_', 1)[1]), 1)
        self.assertLess(int(first_line.rsplit('_', 1)[1]), 200)
        self.window.adjustment.set_value(self.window.adjustment.get_lower())
        self.pump(.2)
        self.keys('ctrl+shift+r')
        self.wait_for(lambda: any(text.startswith('Экран.') and re.search(r'QA_SCROLL_00[01]\d{2}', text)
                                 for text in self.speech.calls), 'visible screen at retained beginning')

    def test_screen_review_reads_actual_vte_rows_and_returns_to_cursor(self):
        self.command("printf 'QA_REVIEW_ONE\\nQA_REVIEW_TWO\\n'", 'QA_REVIEW_TWO')
        self.keys('ctrl+shift+Up')
        self.wait_for(lambda: self.speech.calls[-1] == 'QA_REVIEW_TWO', 'previous row speech')
        self.keys('ctrl+shift+Up')
        self.wait_for(lambda: self.speech.calls[-1] == 'QA_REVIEW_ONE', 'second previous row speech')
        _, row = self.window.terminal.get_cursor_position()
        current_line = self.window.text_range(row, row).strip() or 'Пустая строка.'
        self.keys('ctrl+shift+End')
        self.wait_for(lambda: self.speech.calls[-1] == current_line, 'current prompt speech after review End')

    def test_toolbar_keyboard_focus_and_help_return_to_terminal(self):
        self.keys('ctrl+Tab')
        self.assertTrue(self.window.read_button.is_focus())
        self.wait_for(lambda: any(text.startswith('Читать экран') for text in self.speech.calls), 'toolbar focus speech')
        self.keys('Tab')
        self.assertTrue(self.window.auto_button.is_focus())
        self.keys('Escape')
        self.assertTrue(self.window.terminal.is_focus())
        self.keys('ctrl+shift+F1')
        self.wait_for(lambda: self.window.dialog is not None, 'help dialog')
        self.evidence('help')
        self.keys('Escape')
        self.wait_for(lambda: self.window.dialog is None, 'help closed')
        self.assertTrue(self.window.terminal.is_focus())

    def test_auto_pause_discards_new_output_and_stop_resumes_on_next_command(self):
        self.keys('ctrl+shift+a')
        self.assertTrue(self.window.reader.paused)
        self.command("printf 'QA_PAUSED_OUTPUT\\n'", 'QA_PAUSED_OUTPUT')
        self.assertFalse(any('QA_PAUSED_OUTPUT' in text for text in self.speech.calls))
        self.keys('ctrl+shift+space')
        self.command("printf 'QA_STILL_PAUSED_OUTPUT\\n'", 'QA_STILL_PAUSED_OUTPUT')
        self.assertTrue(self.window.reader.paused)
        self.assertTrue(self.window.reader.stopped)
        self.assertTrue(self.window.status.get_text().startswith('Авточтение: выкл'))
        self.assertFalse(any('QA_STILL_PAUSED_OUTPUT' in text for text in self.speech.calls))
        self.keys('ctrl+shift+a')
        self.assertFalse(self.window.reader.paused)
        self.keys('ctrl+shift+space')
        self.assertTrue(self.window.reader.stopped)
        self.assertTrue(self.window.status.get_text().startswith('Авточтение: остановлено'))
        self.command("printf 'QA_RESUMED_OUTPUT\\n'", 'QA_RESUMED_OUTPUT')
        self.wait_for(lambda: any('QA_RESUMED_OUTPUT' in text for text in self.speech.calls), 'resumed output speech')
        self.assertFalse(self.window.reader.stopped)
        self.assertTrue(self.window.status.get_text().startswith('Авточтение: вкл'))

    def test_manual_speech_refreshes_stopped_status_preserving_pause_and_history_details(self):
        self.command("printf 'QA_STATUS_REVIEW\\n'", 'QA_STATUS_REVIEW')
        for shortcut in ('ctrl+shift+r', 'ctrl+shift+l', 'ctrl+plus', 'ctrl+Tab'):
            with self.subTest(shortcut=shortcut):
                self.keys('ctrl+shift+space')
                self.assertTrue(self.window.reader.stopped)
                self.keys(shortcut)
                self.assertFalse(self.window.reader.stopped)
                self.assertTrue(self.window.status.get_text().startswith('Авточтение: вкл'))
        self.keys('Escape')
        self.keys('ctrl+shift+a')
        self.keys('ctrl+shift+space')
        self.keys('ctrl+shift+r')
        self.assertTrue(self.window.reader.paused)
        self.assertFalse(self.window.reader.stopped)
        self.assertTrue(self.window.status.get_text().startswith('Авточтение: выкл'))
        self.keys('ctrl+shift+space')
        self.keys('ctrl+shift+Up')
        self.assertTrue(self.window.reader.paused)
        self.assertFalse(self.window.reader.stopped)
        self.assertTrue(self.window.status.get_text().startswith('История: строка '))

    def test_multiline_paste_requires_confirmation_before_entering_shell(self):
        value = "printf 'QA_PASTE_ONE\\n'\nprintf 'QA_PASTE_TWO\\n'\n"
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(value, -1)
        self.keys('ctrl+shift+v')
        self.wait_for(lambda: self.window.dialog is not None, 'paste confirmation')
        self.assertNotIn('QA_PASTE_ONE', self.screen())
        self.keys('Escape')
        self.wait_for(lambda: self.window.dialog is None, 'paste cancellation')
        self.assertNotIn('QA_PASTE_ONE', self.screen())
        self.keys('ctrl+shift+v')
        self.wait_for(lambda: self.window.dialog is not None, 'second paste confirmation')
        self.window.dialog.response(Gtk.ResponseType.OK)
        self.pump(.15)
        self.keys('Return')
        self.wait_for(lambda: 'QA_PASTE_TWO' in self.screen().splitlines(), 'confirmed paste output')

    def test_shift_insert_multiline_primary_paste_cannot_bypass_confirmation(self):
        value = 'QA_SHIFT_INSERT_FIRST\nQA_SHIFT_INSERT_SECOND'
        Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY).set_text(value, -1)
        self.keys('shift+Insert')
        self.wait_for(lambda: self.window.dialog is not None, 'Shift Insert confirmation')
        self.assertNotIn('QA_SHIFT_INSERT_FIRST', self.screen())
        self.keys('Escape')
        self.wait_for(lambda: self.window.dialog is None, 'Shift Insert cancellation')
        self.assertNotIn('QA_SHIFT_INSERT_FIRST', self.screen())

    def test_native_paste_action_uses_confirmation_and_inserts_once(self):
        value = "printf 'QA_ACTION_FIRST\\n'\nprintf 'QA_ACTION_SECOND\\n'\n"
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(value, -1)
        self.window.terminal.emit('paste-clipboard')
        self.wait_for(lambda: self.window.dialog is not None, 'native clipboard action confirmation')
        self.assertNotIn('QA_ACTION_FIRST', self.screen())
        self.window.dialog.response(Gtk.ResponseType.OK)
        self.pump(.15)
        self.assertIsNone(self.window.dialog)
        self.keys('Return')
        self.wait_for(lambda: 'QA_ACTION_SECOND' in self.screen().splitlines(), 'native action paste output')
        self.assertEqual(self.screen().splitlines().count('QA_ACTION_SECOND'), 1)

    def test_close_confirmation_defaults_to_cancel_and_then_stops_shell(self):
        self.keys('ctrl+shift+q')
        self.wait_for(lambda: self.window.dialog is not None, 'close confirmation')
        for _ in range(4):
            self.keys('Tab')
            focused = self.window.dialog.get_focus()
            if isinstance(focused, Gtk.Button) and focused.get_label() == 'Продолжить':
                break
        self.assertEqual(self.window.dialog.get_focus().get_label(), 'Продолжить')
        self.wait_for(lambda: self.speech.calls[-1] == 'Продолжить', 'confirmation continue focus speech')
        self.keys('shift+Tab')
        self.assertEqual(self.window.dialog.get_focus().get_label(), 'Отмена')
        self.wait_for(lambda: self.speech.calls[-1] == 'Отмена', 'confirmation cancel focus speech')
        self.keys('Return')
        self.wait_for(lambda: self.window.dialog is None, 'default cancellation')
        self.assertFalse(self.window.closed)
        self.assertTrue(self.window.alive)
        self.keys('ctrl+shift+q')
        self.wait_for(lambda: self.window.dialog is not None, 'second close confirmation')
        self.window.dialog.response(Gtk.ResponseType.OK)
        self.wait_for(lambda: self.window.closed, 'confirmed close')
        self.wait_for(lambda: not Path('/proc', str(self.window.pid)).exists(), 'Bash exit after closing PTY')


if __name__ == '__main__':
    unittest.main()
