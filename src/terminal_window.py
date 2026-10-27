"""A real VTE terminal with bounded speech and keyboard review of its screen."""
import argparse
import json
import os
from pathlib import Path
import signal
import time

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Vte', '2.91')
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango, Vte

from terminal import ShellSession, paste_text
from terminal_help import HELP
from terminal_speech import SnapshotDiff, TerminalSpeech
from terminal_preferences import (BOOL_LABELS, DEFAULTS, OPTIONS, TerminalPreferences,
                                  character_name, speech_text)
from settings import DurabilityError


PALETTE = ['#111827', '#ff7b86', '#8fe0a4', '#f7d787', '#89b9ff', '#d7a6f5', '#81dce7', '#e4eaf3',
           '#9aaac0', '#ff9ca5', '#b0f3bd', '#ffe5ac', '#adceff', '#e5c2ff', '#a4eef5', '#ffffff']
MODIFIERS = {getattr(Gdk, 'KEY_' + name) for name in (
    'Control_L', 'Control_R', 'Shift_L', 'Shift_R', 'Alt_L', 'Alt_R', 'Meta_L', 'Meta_R',
    'Super_L', 'Super_R', 'Hyper_L', 'Hyper_R', 'Caps_Lock', 'Num_Lock', 'Scroll_Lock',
    'ISO_Level3_Shift', 'ISO_Level5_Shift', 'Mode_switch')}


def rgba(value):
    color = Gdk.RGBA()
    color.parse(value)
    return color


def shortcut_key(event, display):
    """Keep Ctrl shortcuts usable when a secondary XKB layout is active."""
    key = Gdk.keyval_to_lower(event.keyval)
    if event.state & Gdk.ModifierType.CONTROL_MASK and not Gdk.KEY_a <= key <= Gdk.KEY_z:
        found, _, values = Gdk.Keymap.get_for_display(display).get_entries_for_keycode(event.hardware_keycode)
        if found:
            for value in values:
                value = Gdk.keyval_to_lower(value)
                if Gdk.KEY_a <= value <= Gdk.KEY_z:
                    return value
    return key


class TerminalWindow(Gtk.Window):
    def __init__(self, speech, directory, windowed=False, preferences=None, open_preferences=False):
        super().__init__(title='FlashOS — доступный терминал')
        self.speech = speech
        self.preferences = preferences or TerminalPreferences()
        self.options = dict(self.preferences.data)
        self.reader = TerminalSpeech(speech, max_messages=self.options['queue_limit'],
                                     chunk_chars=self.options['chunk_chars'])
        self.reader.paused = not self.options['auto_read']
        self.diff = SnapshotDiff(max_lines=220)
        self.session = ShellSession()
        self.directory = str(Path(directory).resolve())
        if not Path(self.directory).is_dir():
            self.directory = str(Path.home())
        self.closed = False
        self.alive = False
        self.starting = True
        self.returncode = 0
        self.pid = None
        self.was_ready = True
        self.input_blocked = False
        self.last_input = 0.0
        self.last_cursor_row = None
        self.review_row = None
        self.review_lines = None
        self.echo_mode = self.options['input_echo']
        self.font_size = self.options['font_size']
        self.prompt_read = False
        self.prompt_rows = set()
        self.input_rows = set()
        self.input_text = {}
        self.input_prefix = {}
        self.prompt_serial = None
        self.prompt_row = None
        self.submitted_row = None
        self.submitted_draft = ''
        self.echo_word = ''
        self.typing_pending = False
        self.dialog = None
        self.set_default_size(1100, 760)
        self.set_size_request(640, 480)
        if not windowed:
            self.set_decorated(False)
            root_window = Gdk.get_default_root_window()
            self.set_default_size(root_window.get_width(), root_window.get_height())
            self.move(0, 0)
            self.fullscreen()
        self.connect('delete-event', lambda *_: self.request_close() or True)
        self.connect('key-press-event', self.key)
        self.connect('destroy', self.destroyed)
        css = Gtk.CssProvider()
        css.load_from_data(b'''
window { background: #eaf0f6; color: #172c45; }
button { font: 11pt "DejaVu Sans"; padding: 7px 10px; border: 1px solid #899db4;
         border-radius: 5px; background: #ffffff; color: #172c45; }
button:focus { outline: 3px solid #ad6500; outline-offset: -3px; background: #f9e8bd; }
button:hover { background: #dce9f8; }
label { color: #172c45; }
.heading { font: bold 22pt "DejaVu Sans"; }
.subtitle { font: 10pt "DejaVu Sans"; color: #43566d; }
.status { font: 10pt "DejaVu Sans"; padding: 8px 12px; }
''')
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        self.add(layout)
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        header.set_margin_start(18)
        header.set_margin_end(18)
        header.set_margin_top(14)
        title = Gtk.Label(label='Терминал', xalign=0)
        title.get_style_context().add_class('heading')
        header.pack_start(title, False, False, 0)
        subtitle = Gtk.Label(label='Bash · системная среда · права root', xalign=0)
        subtitle.get_style_context().add_class('subtitle')
        header.pack_start(subtitle, False, False, 0)
        layout.pack_start(header, False, False, 0)
        toolbar = Gtk.Box(spacing=6)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        self.buttons = []
        self.read_button = self.button(toolbar, 'Читать экран', self.read_screen, 'Ctrl Shift R')
        self.auto_button = self.button(toolbar, 'Авточтение: вкл', self.toggle_auto, 'Ctrl Shift A')
        self.button(toolbar, 'Стоп речи', self.stop_speech, 'Ctrl Shift Пробел')
        self.button(toolbar, 'Справка', self.show_help, 'Ctrl Shift F1')
        self.button(toolbar, 'Закрыть', self.request_close, 'Ctrl Shift Q')
        layout.pack_start(toolbar, False, False, 0)
        settings_bar = Gtk.Box(spacing=6)
        settings_bar.set_margin_start(12)
        settings_bar.set_margin_end(12)
        self.button(settings_bar, 'Настройки терминала', self.show_preferences, 'Ctrl Shift P')
        layout.pack_start(settings_bar, False, False, 0)
        body = Gtk.Box(spacing=0)
        body.set_margin_start(12)
        body.set_margin_end(12)
        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(5000)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_audible_bell(False)
        self.terminal.set_allow_hyperlink(False)
        self.terminal.set_font(Pango.FontDescription.from_string('DejaVu Sans Mono 14'))
        self.terminal.set_colors(rgba('#e7eff9'), rgba('#111827'), [rgba(value) for value in PALETTE])
        self.terminal.set_color_cursor(rgba('#f7d787'))
        self.terminal.set_color_highlight(rgba('#305780'))
        self.terminal.set_color_highlight_foreground(rgba('#ffffff'))
        self.apply_options()
        self.terminal.get_accessible().set_name('Терминал Bash: команды и вывод программ')
        self.terminal.get_accessible().set_description('Ctrl Shift F1: справка по озвучке и клавиатуре')
        self.terminal.connect('key-press-event', self.key)
        self.terminal.connect('button-press-event', self.mouse)
        self.terminal.connect('paste-clipboard', self.native_paste)
        self.terminal.connect('child-exited', self.child_exited)
        body.pack_start(self.terminal, True, True, 0)
        self.adjustment = self.terminal.get_vadjustment()
        body.pack_start(Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL, adjustment=self.adjustment), False, False, 0)
        layout.pack_start(body, True, True, 0)
        self.status = Gtk.Label(xalign=0)
        self.status.set_line_wrap(True)
        self.status.get_style_context().add_class('status')
        layout.pack_end(self.status, False, False, 0)
        self.update_status()
        self.show_all()
        self.terminal.grab_focus()
        self.get_window().focus(Gdk.CURRENT_TIME)
        GLib.timeout_add(80, self.poll)
        GLib.timeout_add(75, self.reader.tick)
        GLib.idle_add(self.spawn)
        if open_preferences:
            GLib.idle_add(self.show_preferences)

    def button(self, toolbar, label, action, shortcut):
        button = Gtk.Button(label=label)
        button.set_tooltip_text(shortcut)
        button.connect('clicked', lambda *_: action())
        button.connect('focus-in-event', lambda widget, _: self.speak(
            widget.get_label() + '. ' + shortcut, priority=True) and False)
        self.buttons.append(button)
        toolbar.pack_start(button, False, False, 0)
        return button

    def spawn(self):
        env = [key + '=' + value for key, value in self.session.environment().items()]
        # GI exposes child_setup_data even when child_setup is None.
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT, self.directory, self.session.argv, env,
            GLib.SpawnFlags(int(GLib.SpawnFlags.DEFAULT) | Vte.SPAWN_NO_SYSTEMD_SCOPE),
            None, None, -1, None, self.spawned, None)
        return False

    def spawned(self, terminal, pid, error, *_):
        if self.closed:
            return
        self.starting = False
        if error:
            self.returncode = 1
            self.speak('Не удалось запустить Bash. ' + str(error), priority=True)
            self.status.set_text('Bash не запущен. Закройте окно и повторите запуск.')
        else:
            self.pid = pid
            self.alive = True
            if self.options['welcome']:
                self.speak('Терминал Bash. Права root. Озвучка ввода и вывода настраивается. '
                           'Ctrl Shift P — настройки; Ctrl Shift F1 — справка; '
                           'Ctrl Shift Пробел — стоп речи.', priority=True)
            if self.preferences.error:
                self.speak(self.preferences.error)

    def text_range(self, start, end):
        value, _ = self.terminal.get_text_range_format(Vte.Format.TEXT, max(0, start), 0,
                                                       max(0, end), max(0, self.terminal.get_column_count()))
        return value or ''

    def screen_lines(self, start, end):
        # VTE joins soft-wrapped rows in a multi-row TEXT export. Read physical
        # rows explicitly so snapshot coordinates still match the PTY cursor.
        return [self.text_range(row, row).rstrip('\n') for row in range(start, end + 1)]

    @staticmethod
    def find_prompt(lines, start, label):
        if not label:
            return None
        position = ''.join(lines).rfind(label)
        if position < 0:
            return None
        ending = position + len(label) - 1
        offset = 0
        first = None
        for index, line in enumerate(lines):
            if first is None and position < offset + len(line):
                first = (start + index, position - offset)
            if first is not None and ending < offset + len(line):
                return first[0], start + index, first[1]
            offset += len(line)
        return None

    def poll(self):
        if self.closed:
            return False
        if self.starting:
            return True
        _, row = self.terminal.get_cursor_position()
        start = max(0, row - 199)
        lines = self.screen_lines(start, row)
        command = self.session.command()
        ready = self.session.state()
        serial = self.session.prompt_serial()
        if command is not None and not self.reader.paused:
            # Consume command metadata even when its VTE frame is still pending.
            self.reader.stopped = False
            if command and self.options['speak_commands']:
                self.speak('Команда: ' + speech_text(command, self.options['punctuation']), priority=True)
            self.update_status()
        # Diff against a speech-only screen. Prompt/input rows must be removed
        # BEFORE the diff: a suffix such as " # " or the latest typed letters
        # no longer contains the prompt prefix and cannot be filtered afterward.
        if self.last_cursor_row is not None and row < self.last_cursor_row:
            if ready and serial == self.prompt_serial and time.monotonic() - self.last_input < .3:
                # Readline can move back across a soft wrap after the first
                # post-key poll. Refresh that draft rather than calling it clear.
                self.typing_pending = True
            else:
                self.prompt_rows.clear()
                self.input_rows.clear()
                self.input_text.clear()
                self.input_prefix.clear()
                self.prompt_row = None
        prompt_label = self.session.prompt_text()
        new_prompt = serial is not None and serial != self.prompt_serial
        prompt_span = self.find_prompt(lines, start, prompt_label)
        prompt_text = None
        # Only a freshly published Bash prompt can introduce a prompt row.
        # A program printing "FlashOS example # result" remains ordinary output.
        if ready and new_prompt:
            stale_editor = False
            if prompt_span:
                candidate, _, column = prompt_span
                tail = ''.join(lines[candidate - start:])[column:]
                stale_editor = candidate == self.submitted_row and tail.startswith(self.submitted_draft)
            if not prompt_span or stale_editor:
                # Bash publishes ready/serial before VTE draws the new PS1.
                # Keep the output baseline intact until that fresh prompt is
                # visible; an old submitted editor is not the new generation.
                return True
        if ready and prompt_span and new_prompt:
            candidate, last, column = prompt_span
            self.prompt_serial = serial
            self.prompt_row = candidate
            self.prompt_rows.update(range(candidate, last + 1))
            for prompt_row in range(candidate, last + 1):
                self.input_text[prompt_row] = lines[prompt_row - start]
                self.input_prefix[prompt_row] = lines[prompt_row - start][:column] if prompt_row == candidate else ''
            prompt_text = prompt_label
        if ready and self.input_blocked and command is not None and lines and lines[-1].startswith('...> '):
            self.prompt_rows.add(row)
            self.input_text[row] = lines[-1]
        # A row reused by clear/reflow/full-screen programs is no longer input.
        for offset, line in enumerate(lines):
            absolute = start + offset
            old = self.input_text.get(absolute)
            if old is not None and not ready and line != old:
                self.input_rows.discard(absolute)
                self.prompt_rows.discard(absolute)
                self.input_text.pop(absolute, None)
                self.input_prefix.pop(absolute, None)
        capturing_input = ready and self.typing_pending and command is None and not self.input_blocked
        if capturing_input:
            # Wrapped readline drafts are input even on physical rows lacking
            # the visible prefix. No raw keyboard text is read from child apps.
            latest = max((value for value in self.prompt_rows if start <= value <= row), default=row)
            if time.monotonic() - self.last_input < .3:
                self.input_rows.update(range(latest, row + 1))
            self.typing_pending = False
        for offset, line in enumerate(lines):
            absolute = start + offset
            if capturing_input and absolute in self.prompt_rows | self.input_rows:
                self.input_text[absolute] = line
        changes = self.diff.update(self.speech_snapshot(lines, start), start_row=start)
        self.prompt_rows.intersection_update(range(start, row + 1))
        self.input_rows.intersection_update(range(start, row + 1))
        self.input_text = {key: value for key, value in self.input_text.items() if start <= key <= row}
        self.input_prefix = {key: value for key, value in self.input_prefix.items() if start <= key <= row}
        announce_prompt = prompt_text and (not self.prompt_read or self.options['prompt_mode'] == 'always')
        if ready and (not self.input_blocked or command is not None or new_prompt or not self.was_ready):
            self.input_blocked = False
        if self.last_cursor_row is not None and row - self.last_cursor_row > 199:
            self.reader.enqueue_output('Часть быстрого вывода не озвучена. '
                                       'Используйте чтение истории, less или вывод команды в файл.')
        if changes:
            self.reader.enqueue_output(speech_text('\n'.join(changes), self.options['punctuation']))
        if announce_prompt:
            if self.options['prompt_mode'] != 'off':
                self.speak(speech_text(prompt_text, self.options['punctuation']))
            self.prompt_read = True
        self.was_ready = ready
        self.last_cursor_row = row
        return True

    def baseline(self):
        _, row = self.terminal.get_cursor_position()
        start = max(0, row - 199)
        lines = self.screen_lines(start, row)
        for offset, line in enumerate(lines):
            if start + offset in self.prompt_rows | self.input_rows:
                self.input_text[start + offset] = line
        self.diff.reset(self.speech_snapshot(lines, start), start_row=start)

    def speech_snapshot(self, lines, start):
        result = []
        muted = self.prompt_rows | self.input_rows
        for offset, line in enumerate(lines):
            absolute = start + offset
            if absolute in muted:
                old = self.input_text.get(absolute, line)
                # Keep program output on either side of a prompt/draft. This
                # also handles printf without a final newline before Bash PS1.
                line = self.input_prefix.get(absolute, '') + (line[len(old):] if line.startswith(old) else '')
            result.append(line)
        return result

    def command_line(self, text):
        prompt = self.session.prompt_text()
        if prompt and prompt in text:
            return text.rsplit(prompt, 1)[-1]
        return '' if prompt and text.endswith(prompt.rstrip()) else text

    def input_before_cursor(self, column, row):
        value, _ = self.terminal.get_text_range_format(Vte.Format.TEXT, max(0, row - 199), 0,
                                                       row, max(0, column))
        lines = (value or '').splitlines()
        line = lines[-1] if lines else ''
        if line.startswith('...> '):
            return line[5:]
        return self.command_line(line)

    def remember_submission(self):
        _, row = self.terminal.get_cursor_position()
        start = max(0, row - 199)
        lines = self.screen_lines(start, row)
        span = self.find_prompt(lines, start, self.session.prompt_text())
        if span:
            candidate, _, column = span
            self.submitted_row = candidate
            self.submitted_draft = ''.join(lines[candidate - start:])[column:]

    def speak(self, text, *, priority=False):
        was_stopped = self.reader.stopped
        accepted = self.reader.speak(text, priority=priority)
        if was_stopped and not self.reader.stopped and self.status_detail is None:
            self.update_status()
        return accepted

    def update_status(self, text=None):
        self.status_detail = text
        if text is None:
            mode = 'выкл' if self.reader.paused else 'остановлено' if self.reader.stopped else 'вкл'
            text = f'Авточтение: {mode} · Ctrl Shift ↑/↓ — читать строки · Ctrl Tab — кнопки · Ctrl Shift F1 — помощь'
        self.status.set_text(text)

    def stop_speech(self):
        self.reader.stop()
        self.update_status()

    def toggle_auto(self):
        if self.reader.paused or self.reader.stopped:
            self.reader.resume()
            label = 'Автоматическое чтение включено. Читается только новый вывод.'
        else:
            self.reader.pause()
            label = 'Автоматическое чтение выключено. Ручное чтение доступно.'
        self.auto_button.set_label('Авточтение: выкл' if self.reader.paused else 'Авточтение: вкл')
        self.speak(label, priority=True)
        self.update_status()
        self.save_preference({'auto_read': not self.reader.paused})

    def read_screen(self):
        text = self.terminal.get_text_format(Vte.Format.TEXT)
        self.speak('Экран. ' + ((text or '').strip() or 'Пусто.'), priority=True)

    def read_line(self, row=None):
        if row is None:
            _, row = self.terminal.get_cursor_position()
        text = self.text_range(row, row).strip()
        self.speak(text or 'Пустая строка.', priority=True)
        return False

    def review(self, direction=None, edge=None):
        # VTE's adjustment is relative to its retained buffer, whereas cursor rows
        # are absolute and can be renumbered on reflow. Review a plain-text snapshot
        # instead of guessing that conversion or moving a running application's cursor.
        if self.review_lines is None or edge == 'last':
            stream = Gio.MemoryOutputStream.new_resizable()
            try:
                self.terminal.write_contents_sync(stream, Vte.WriteFlags.DEFAULT, None)
                stream.close(None)
                content = stream.steal_as_bytes().get_data().decode('utf-8', errors='replace')
            finally:
                if not stream.is_closed():
                    stream.close(None)
            self.review_lines = content.splitlines()[-self.options['scrollback']:] or ['']
            self.review_row = len(self.review_lines) - 1
        last = len(self.review_lines) - 1
        self.review_row = 0 if edge == 'first' else last if edge == 'last' else max(0, min(last, self.review_row + direction))
        text = self.review_lines[self.review_row].strip() or 'Пустая строка.'
        self.update_status(f'История: строка {self.review_row + 1} из {last + 1}. {text[:180]}')
        self.speak(text, priority=True)

    def zoom(self, step):
        self.font_size = max(9, min(28, self.font_size + step))
        self.terminal.set_font(Pango.FontDescription.from_string(f'DejaVu Sans Mono {self.font_size}'))
        self.baseline()
        self.save_preference({'font_size': self.font_size})
        self.speak(f'Шрифт {self.font_size}.', priority=True)

    def save_preference(self, changes):
        try:
            self.preferences.save(changes)
            self.options.update(changes)
        except DurabilityError as error:
            self.options.update(self.preferences.data)
            self.speak(str(error), priority=True)
        except OSError as error:
            self.speak('Настройка действует в этой сессии. Не удалось подтвердить сохранение на флешке. '
                       + str(error), priority=True)

    def apply_options(self):
        self.echo_mode = self.options['input_echo']
        self.font_size = self.options['font_size']
        self.terminal.set_scrollback_lines(self.options['scrollback'])
        self.terminal.set_scroll_on_output(self.options['scroll_on_output'])
        self.terminal.set_audible_bell(self.options['audible_bell'])
        self.terminal.set_font(Pango.FontDescription.from_string(f'DejaVu Sans Mono {self.font_size}'))
        shape = {'block': Vte.CursorShape.BLOCK, 'ibeam': Vte.CursorShape.IBEAM,
                 'underline': Vte.CursorShape.UNDERLINE}[self.options['cursor_shape']]
        self.terminal.set_cursor_shape(shape)
        self.terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON if self.options['cursor_blink'] else Vte.CursorBlinkMode.OFF)

    def show_preferences(self):
        if self.dialog is not None:
            self.dialog.present()
            return False
        dialog = Gtk.Dialog(title='Настройки терминала', transient_for=self, modal=True)
        dialog.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        dialog.set_default_size(620, 470)
        dialog.add_button('Стандартные', Gtk.ResponseType.REJECT)
        dialog.add_button('Отмена', Gtk.ResponseType.CANCEL)
        dialog.add_button('Сохранить', Gtk.ResponseType.OK)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        grid = Gtk.Grid(column_spacing=14, row_spacing=12)
        grid.set_margin_start(16)
        grid.set_margin_end(16)
        grid.set_margin_top(12)
        grid.set_margin_bottom(12)
        controls = {}
        for index, (key, (label, choices)) in enumerate(OPTIONS.items()):
            text = Gtk.Label(label=label, xalign=0)
            text.set_line_wrap(True)
            grid.attach(text, 0, index, 1, 1)
            control = Gtk.ComboBoxText()
            for _, value_label in choices:
                control.append_text(value_label)
            control.set_active(next(i for i, (value, _) in enumerate(choices) if value == self.options[key]))
            control.get_accessible().set_name(label)
            control.connect('focus-in-event', lambda widget, _, title=label:
                            self.speak(title + '. ' + (widget.get_active_text() or ''), priority=True) and False)
            control.connect('changed', lambda widget, title=label:
                            self.speak(title + '. ' + (widget.get_active_text() or ''), priority=True))
            controls[key] = control
            grid.attach(control, 1, index, 1, 1)
        for index, (key, label) in enumerate(BOOL_LABELS.items(), start=len(OPTIONS)):
            control = Gtk.CheckButton(label=label)
            control.set_active(self.options[key])
            control.connect('focus-in-event', lambda widget, _:
                            self.speak(widget.get_label() + ('. Включено' if widget.get_active() else '. Выключено'), priority=True) and False)
            control.connect('toggled', lambda widget:
                            self.speak(widget.get_label() + ('. Включено' if widget.get_active() else '. Выключено'), priority=True))
            controls[key] = control
            grid.attach(control, 0, index, 2, 1)
        scroll.add(grid)
        dialog.get_content_area().pack_start(scroll, True, True, 0)
        self.dialog = dialog
        self.preference_controls = controls
        def response(_, code):
            if code == Gtk.ResponseType.REJECT:
                for key, control in controls.items():
                    if key in OPTIONS:
                        control.set_active(next(i for i, (value, _) in enumerate(OPTIONS[key][1]) if value == DEFAULTS[key]))
                    else:
                        control.set_active(DEFAULTS[key])
                self.speak('Выбраны стандартные настройки. Нажмите Сохранить для применения.', priority=True)
                return
            if code != Gtk.ResponseType.OK:
                self.close_dialog()
                return
            values = {key: OPTIONS[key][1][control.get_active()][0] if key in OPTIONS else control.get_active()
                      for key, control in controls.items()}
            durability_warning = ''
            try:
                self.preferences.save(values)
            except DurabilityError as error:
                durability_warning = str(error)
            except OSError as error:
                self.speak('Настройки не удалось сохранить. ' + str(error), priority=True)
                return
            self.options = dict(self.preferences.data)
            self.close_dialog()
            self.reader.close()
            self.reader = TerminalSpeech(self.speech, self.options['queue_limit'], self.options['chunk_chars'])
            # Existing timeout follows the old instance, install the replacement.
            GLib.timeout_add(75, self.reader.tick)
            if not self.options['auto_read']:
                self.reader.pause()
            self.apply_options()
            self.baseline()
            self.auto_button.set_label('Авточтение: выкл' if self.reader.paused else 'Авточтение: вкл')
            self.update_status()
            self.speak(durability_warning or 'Настройки терминала сохранены на флешке.', priority=True)
        dialog.connect('response', response)
        dialog.connect('key-press-event', self.dialog_key)
        dialog.show_all()
        dialog.get_window().focus(Gdk.CURRENT_TIME)
        self.speak('Настройки терминала. Tab выбирает параметр. Стрелки меняют значение, пробел переключает флажок. '
                   'Сохранить применяет изменения и записывает их на флешку. Escape отменяет.', priority=True)
        return False

    def show_help(self):
        if self.dialog is not None:
            self.dialog.present()
            return
        dialog = Gtk.Dialog(title='Терминал: клавиатура и озвучка', transient_for=self, modal=True)
        dialog.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        dialog.set_default_size(620, 440)
        dialog.add_button('Закрыть справку', Gtk.ResponseType.CLOSE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_left_margin(16)
        view.set_right_margin(16)
        view.get_buffer().set_text(HELP)
        view.get_accessible().set_name('Справка по терминалу')
        scroll.add(view)
        dialog.get_content_area().pack_start(scroll, True, True, 0)
        self.dialog = dialog
        self.speak(HELP, priority=True)
        dialog.connect('response', lambda *_: self.close_dialog())
        dialog.connect('key-press-event', self.dialog_key)
        dialog.show_all()
        dialog.get_window().focus(Gdk.CURRENT_TIME)

    def dialog_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close_dialog()
            return True
        if event.state & Gdk.ModifierType.CONTROL_MASK and event.state & Gdk.ModifierType.SHIFT_MASK:
            if event.keyval == Gdk.KEY_space:
                self.stop_speech()
                return True
        if event.keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Left, Gdk.KEY_Right,
                            Gdk.KEY_Up, Gdk.KEY_Down):
            GLib.idle_add(self.announce_dialog_focus)
        return False

    def announce_dialog_focus(self):
        if self.dialog is not None:
            focus = self.dialog.get_focus()
            if isinstance(focus, Gtk.Button):
                self.speak(focus.get_label().replace('_', ''), priority=True)
        return False

    def close_dialog(self):
        dialog, self.dialog = self.dialog, None
        if dialog:
            dialog.destroy()
        self.reader.stop()
        self.terminal.grab_focus()
        if not self.closed:
            self.get_window().focus(Gdk.CURRENT_TIME)
        self.update_status()

    def question(self, title, text, action):
        if self.dialog is not None:
            return
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE, text=title)
        dialog.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        dialog.format_secondary_text(text)
        dialog.add_button('Отмена', Gtk.ResponseType.CANCEL)
        dialog.add_button('Продолжить', Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        self.dialog = dialog
        self.speak(title + '. ' + text + ' По умолчанию отмена.', priority=True)
        def response(_, code):
            self.close_dialog()
            if code == Gtk.ResponseType.OK:
                action()
        dialog.connect('response', response)
        dialog.connect('key-press-event', self.dialog_key)
        dialog.show_all()
        dialog.get_window().focus(Gdk.CURRENT_TIME)

    def paste(self, primary=False):
        selection = Gdk.SELECTION_PRIMARY if primary else Gdk.SELECTION_CLIPBOARD
        def received(_, text, __):
            if self.closed or text is None:
                return
            try:
                value = paste_text(text)
            except ValueError as error:
                self.speak(str(error), priority=True)
                return
            def insert():
                if not self.closed and self.alive:
                    self.last_input = time.monotonic()
                    self.typing_pending = True
                    self.echo_word = ''
                    self.review_lines = None
                    self.terminal.paste_text(value)
                    self.terminal.grab_focus()
            if '\n' in value:
                self.question('Вставить несколько строк?',
                              'Текст содержит переносы строк. Программа может выполнить команды сразу после вставки.', insert)
            else:
                insert()
        Gtk.Clipboard.get(selection).request_text(received, None)

    def mouse(self, widget, event):
        if event.button == 2:
            if self.options['middle_paste']:
                self.paste(primary=True)
            return True
        return False

    def native_paste(self, terminal):
        GObject.signal_stop_emission_by_name(terminal, 'paste-clipboard')
        self.paste()

    def echo_key(self, key):
        if self.closed or self.input_blocked or not self.session.state() or self.echo_mode == 'off':
            return False
        column, row = self.terminal.get_cursor_position()
        lines = self.text_range(max(0, row - 80), row).splitlines()
        text = (lines[-1] if lines else '').rstrip()
        if key in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Home, Gdk.KEY_End, Gdk.KEY_Tab) and (
                self.options['completion_echo'] if key == Gdk.KEY_Tab else self.options['history_echo']):
            self.speak(self.command_line(text) or 'Пустая команда.', priority=True)
        elif key in (Gdk.KEY_Left, Gdk.KEY_Right) and self.options['cursor_echo']:
            value, _ = self.terminal.get_text_range_format(Vte.Format.TEXT, row, column, row, column + 1)
            self.reader.echo(character_name((value or ' ')[:1], self.options['capital_letters']), self.options['interrupt_on_type'])
        return False

    def key(self, widget, event):
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        key = shortcut_key(event, self.get_display())
        if shift and not ctrl and key in (Gdk.KEY_Insert, Gdk.KEY_KP_Insert):
            self.paste(primary=True)
            return True
        if ctrl and shift:
            actions = {Gdk.KEY_a: self.toggle_auto, Gdk.KEY_space: self.stop_speech,
                       Gdk.KEY_r: self.read_screen, Gdk.KEY_l: self.read_line,
                       Gdk.KEY_Up: lambda: self.review(-1), Gdk.KEY_Down: lambda: self.review(1),
                       Gdk.KEY_Home: lambda: self.review(edge='first'), Gdk.KEY_End: lambda: self.review(edge='last'),
                       Gdk.KEY_F1: self.show_help, Gdk.KEY_p: self.show_preferences, Gdk.KEY_q: self.request_close,
                       Gdk.KEY_c: lambda: self.terminal.copy_clipboard_format(Vte.Format.TEXT),
                       Gdk.KEY_v: self.paste, Gdk.KEY_F6: lambda: self.speech.next_output(),
                       Gdk.KEY_F7: self.stop_speech}
            if key in actions:
                actions[key]()
                return True
            if key == Gdk.KEY_e:
                modes = ['characters', 'words', 'both', 'off']
                self.echo_mode = modes[(modes.index(self.echo_mode) + 1) % len(modes)]
                self.echo_word = ''
                label = {'words': 'слова', 'characters': 'символы', 'both': 'символы и слова', 'off': 'выключена'}[self.echo_mode]
                self.speak('Озвучка ввода: ' + label, priority=True)
                self.save_preference({'input_echo': self.echo_mode})
                return True
        if ctrl and key in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add, Gdk.KEY_minus, Gdk.KEY_underscore, Gdk.KEY_KP_Subtract):
            self.zoom(-1 if key in (Gdk.KEY_minus, Gdk.KEY_underscore, Gdk.KEY_KP_Subtract) else 1)
            return True
        if ctrl and key in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            if self.terminal.is_focus():
                self.read_button.grab_focus()
            else:
                self.terminal.grab_focus()
                self.speak('Ввод команд.', priority=True)
            return True
        if key == Gdk.KEY_Escape and not self.terminal.is_focus():
            self.terminal.grab_focus()
            self.speak('Ввод команд.', priority=True)
            return True
        if widget is self.terminal and event.keyval not in MODIFIERS and self.session.state() and not self.input_blocked:
            self.review_lines = None
            self.last_input = time.monotonic()
            self.typing_pending = True
            _, input_row = self.terminal.get_cursor_position()
            self.input_rows.add(input_row)
            if (key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
                    or ctrl and key in (Gdk.KEY_j, Gdk.KEY_m, Gdk.KEY_c)):
                self.remember_submission()
            if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) or (ctrl and key in (Gdk.KEY_j, Gdk.KEY_m)):
                if self.echo_mode in ('words', 'both') and not self.options['speak_commands'] and self.echo_word:
                    self.reader.echo(self.echo_word, self.options['interrupt_on_type'])
                self.baseline()
                self.input_blocked = True
                self.typing_pending = False
                self.echo_word = ''
            if not ctrl and key in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right,
                                    Gdk.KEY_Home, Gdk.KEY_End, Gdk.KEY_Tab):
                GLib.timeout_add(35, self.echo_key, key)
                self.echo_word = ''
            if ctrl:
                self.echo_word = ''
            if not ctrl and not event.state & Gdk.ModifierType.MOD1_MASK and self.echo_mode != 'off':
                if key in (Gdk.KEY_BackSpace, Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
                    self.echo_word = self.echo_word[:-1] if key == Gdk.KEY_BackSpace else ''
                if key in (Gdk.KEY_BackSpace, Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and self.options['delete_echo']:
                    column, row = self.terminal.get_cursor_position()
                    if key == Gdk.KEY_BackSpace:
                        prefix = self.input_before_cursor(column, row)
                        value = prefix[-1:]  # Includes the previous row at a soft wrap.
                    else:
                        value, _ = self.terminal.get_text_range_format(Vte.Format.TEXT, row, column, row, column + 1)
                    if value:
                        self.reader.echo('Удалено ' + character_name(value[:1], self.options['capital_letters']),
                                         self.options['interrupt_on_type'])
                else:
                    codepoint = Gdk.keyval_to_unicode(event.keyval)
                    value = chr(codepoint) if codepoint else event.string
                    if value and value.isprintable():
                        if value.isspace():
                            if self.echo_mode in ('words', 'both') and self.echo_word:
                                self.reader.echo(self.echo_word, self.options['interrupt_on_type'])
                            self.echo_word = ''
                        else:
                            self.echo_word = (self.echo_word + value)[-4000:]
                        if self.echo_mode in ('characters', 'both'):
                            self.reader.echo(character_name(value, self.options['capital_letters']), self.options['interrupt_on_type'])
        return False

    def child_exited(self, terminal, status):
        self.alive = False
        if self.closed:
            return
        self.update_status('Оболочка завершена. Вывод доступен для чтения. Ctrl Shift Q — закрыть окно.')
        if self.options['announce_exit']:
            self.reader.enqueue_output('Оболочка завершена. Код: ' + str(os.waitstatus_to_exitcode(status)) + '.')

    def request_close(self):
        if self.closed:
            return
        if self.alive or self.starting:
            self.question('Закрыть терминал?', 'Сессия и запущенные в ней программы будут остановлены.', self.finish)
        else:
            self.finish()

    def finish(self):
        if not self.closed:
            self.closed = True
            self.reader.close()
            self.destroy()

    def destroyed(self, *_):
        self.closed = True
        if self.dialog is not None:
            dialog, self.dialog = self.dialog, None
            dialog.destroy()
        self.reader.close()
        self.session.close()
        if Gtk.main_level():
            Gtk.main_quit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings', default='{}')
    parser.add_argument('--output', default=None)
    parser.add_argument('--directory', default=str(Path.home()))
    parser.add_argument('--windowed', action='store_true')
    parser.add_argument('--state-root', default=None)
    parser.add_argument('--preferences', action='store_true')
    args = parser.parse_args()
    from settings import validate
    from speech import Speech
    speech = Speech(preferred_output=args.output)
    try:
        speech.configure(validate(json.loads(args.settings)))
        window = TerminalWindow(speech, args.directory, args.windowed,
                                TerminalPreferences(args.state_root), args.preferences)
        for name in ('SIGTERM', 'SIGHUP'):
            signal.signal(getattr(signal, name), lambda *_: GLib.idle_add(window.finish))
        Gtk.main()
        return window.returncode
    finally:
        speech.close()


if __name__ == '__main__':
    raise SystemExit(main())
