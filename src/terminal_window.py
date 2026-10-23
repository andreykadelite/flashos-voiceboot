"""A real VTE terminal with bounded speech and keyboard review of its screen."""
import argparse
import json
import os
from pathlib import Path
import re
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


class TerminalWindow(Gtk.Window):
    def __init__(self, speech, directory, windowed=False):
        super().__init__(title='FlashOS — доступный терминал')
        self.speech = speech
        self.reader = TerminalSpeech(speech)
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
        self.output_grace_until = 0.0
        self.last_cursor_row = None
        self.review_row = None
        self.review_lines = None
        self.echo_mode = 'words'
        self.font_size = 14
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
            self.speak('Терминал Bash. Права root. Команды и вывод озвучиваются. '
                              'Ctrl Shift F1 — справка; Ctrl Shift Пробел — стоп речи.', priority=True)

    def text_range(self, start, end):
        value, _ = self.terminal.get_text_range_format(Vte.Format.TEXT, max(0, start), 0,
                                                       max(0, end), max(0, self.terminal.get_column_count() - 1))
        return value or ''

    def poll(self):
        if self.closed:
            return False
        if self.starting:
            return True
        _, row = self.terminal.get_cursor_position()
        start = max(0, row - 199)
        lines = self.text_range(start, row).splitlines()
        changes = self.diff.update(lines, start_row=start)
        command = self.session.command()
        ready = self.session.state()
        now = time.monotonic()
        if command is not None:
            # Readline metadata, the prompt marker and VTE rendering arrive on
            # independent event sources. Keep a fast command's delayed last bytes.
            self.output_grace_until = now + 1
        if ready:
            self.input_blocked = False
        if command and not self.reader.paused:
            self.speak('Команда: ' + command, priority=True)
        if (command is not None or not ready or not self.was_ready or now < self.output_grace_until
                or now - self.last_input > .15):
            if self.last_cursor_row is not None and row - self.last_cursor_row > 199:
                self.reader.enqueue_output('Часть быстрого вывода не озвучена. '
                                           'Используйте чтение истории, less или вывод команды в файл.')
            output = [line for line in changes if not line.startswith(('FlashOS ', '...> '))]
            if output:
                self.reader.enqueue_output('\n'.join(output))
        self.was_ready = ready
        self.last_cursor_row = row
        return True

    def baseline(self):
        _, row = self.terminal.get_cursor_position()
        start = max(0, row - 199)
        self.diff.reset(self.text_range(start, row).splitlines(), start_row=start)

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
            self.review_lines = content.splitlines()[-5000:] or ['']
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
        self.speak(f'Шрифт {self.font_size}.', priority=True)

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
                    self.output_grace_until = 0.0
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
        text = self.text_range(row, row).rstrip()
        if key in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Home, Gdk.KEY_End):
            self.speak(re.sub(r'^FlashOS .*? # ', '', text) or 'Пустая команда.', priority=True)
        elif key in (Gdk.KEY_Left, Gdk.KEY_Right):
            value, _ = self.terminal.get_text_range_format(Vte.Format.TEXT, row, column, row, column)
            self.speak((value or '').strip() or 'Пробел.', priority=True)
        elif key == Gdk.KEY_space and self.echo_mode == 'words':
            prefix, _ = self.terminal.get_text_range_format(Vte.Format.TEXT, row, 0, row, max(0, column - 1))
            words = re.sub(r'^FlashOS .*? # ', '', prefix or '').split()
            if words:
                self.speak(words[-1], priority=True)
        return False

    def key(self, widget, event):
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        key = Gdk.keyval_to_lower(event.keyval)
        if shift and not ctrl and key in (Gdk.KEY_Insert, Gdk.KEY_KP_Insert):
            self.paste(primary=True)
            return True
        if ctrl and shift:
            actions = {Gdk.KEY_a: self.toggle_auto, Gdk.KEY_space: self.stop_speech,
                       Gdk.KEY_r: self.read_screen, Gdk.KEY_l: self.read_line,
                       Gdk.KEY_Up: lambda: self.review(-1), Gdk.KEY_Down: lambda: self.review(1),
                       Gdk.KEY_Home: lambda: self.review(edge='first'), Gdk.KEY_End: lambda: self.review(edge='last'),
                       Gdk.KEY_F1: self.show_help, Gdk.KEY_q: self.request_close,
                       Gdk.KEY_c: lambda: self.terminal.copy_clipboard_format(Vte.Format.TEXT),
                       Gdk.KEY_v: self.paste, Gdk.KEY_F6: lambda: self.speech.next_output(),
                       Gdk.KEY_F7: self.stop_speech}
            if key in actions:
                actions[key]()
                return True
            if key == Gdk.KEY_e:
                modes = ['words', 'characters', 'off']
                self.echo_mode = modes[(modes.index(self.echo_mode) + 1) % len(modes)]
                label = {'words': 'слова', 'characters': 'символы', 'off': 'выключена'}[self.echo_mode]
                self.speak('Озвучка ввода: ' + label, priority=True)
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
            self.output_grace_until = 0.0
            if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) or (ctrl and key in (Gdk.KEY_j, Gdk.KEY_m)):
                self.baseline()
                self.input_blocked = True
            if not ctrl and key in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right,
                                    Gdk.KEY_Home, Gdk.KEY_End, Gdk.KEY_space):
                GLib.idle_add(self.echo_key, key)
            elif not ctrl and self.echo_mode == 'characters' and event.string and event.string.isprintable():
                self.speak(event.string, priority=True)
        return False

    def child_exited(self, terminal, status):
        self.alive = False
        if self.closed:
            return
        self.update_status('Оболочка завершена. Вывод доступен для чтения. Ctrl Shift Q — закрыть окно.')
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
    args = parser.parse_args()
    from settings import validate
    from speech import Speech
    speech = Speech(preferred_output=args.output)
    try:
        speech.configure(validate(json.loads(args.settings)))
        window = TerminalWindow(speech, args.directory, args.windowed)
        for name in ('SIGTERM', 'SIGHUP'):
            signal.signal(getattr(signal, name), lambda *_: GLib.idle_add(window.finish))
        Gtk.main()
        return window.returncode
    finally:
        speech.close()


if __name__ == '__main__':
    raise SystemExit(main())
