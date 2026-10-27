"""Native adaptive display, sharing the console's navigation and speech logic."""
from collections import deque
import curses
import os
import time
import tkinter as tk
from tkinter import font
from pointer_settings import DEFAULTS as POINTER_DEFAULTS, validate as validate_pointer, PointerMonitor, X11Cursor


# Shared work surface, blue selection and an amber focus rail.
COLORS = {'background': '#eaf0f6', 'surface': '#ffffff', 'ink': '#172c45',
          'muted': '#43566d', 'selected': '#194c79', 'focus': '#edb94f',
          'border': '#b9c8d8'}


def ellipsize(value, measure, width):
    value = str(value)
    if measure(value) <= width:
        return value
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if measure(value[:middle] + '…') <= width:
            low = middle
        else:
            high = middle - 1
    return value[:low].rstrip() + '…'


def wrap_pixels(value, measure, width, maximum=None):
    """Wrap with actual font metrics, including paths without spaces."""
    pending = ' '.join(str(value).split())
    lines = []
    width = max(1, width)
    while pending:
        if measure(pending) <= width:
            lines.append(pending)
            break
        low, high = 1, len(pending)
        while low < high:
            middle = (low + high + 1) // 2
            if measure(pending[:middle]) <= width:
                low = middle
            else:
                high = middle - 1
        end = pending.rfind(' ', 0, low + 1)
        if end <= 0:
            end = low
        lines.append(pending[:end])
        pending = pending[end:].lstrip()
        if maximum is not None and len(lines) >= maximum:
            lines[-1] = ellipsize(lines[-1] + ' ' + pending, measure, width)
            break
    return lines or ['']


KEYS = {'Tab': 9, 'ISO_Left_Tab': curses.KEY_BTAB, 'Up': curses.KEY_UP, 'Down': curses.KEY_DOWN, 'Left': curses.KEY_LEFT,
    'Right': curses.KEY_RIGHT, 'Home': curses.KEY_HOME, 'End': curses.KEY_END,
    'Prior': curses.KEY_PPAGE, 'Next': curses.KEY_NPAGE, 'BackSpace': curses.KEY_BACKSPACE,
    'Return': 10, 'KP_Enter': 10, 'Escape': 27,
    'Insert': curses.KEY_IC, 'Delete': curses.KEY_DC,
    **{f'F{i}': curses.KEY_F0 + i for i in range(1, 13)}}
for keypad, ordinary in {'KP_Up': 'Up', 'KP_Down': 'Down', 'KP_Left': 'Left', 'KP_Right': 'Right',
    'KP_Home': 'Home', 'KP_End': 'End', 'KP_Prior': 'Prior', 'KP_Next': 'Next',
    'KP_Insert': 'Insert', 'KP_Delete': 'Delete'}.items():
    KEYS[keypad] = KEYS[ordinary]


class GraphicalScreen:
    graphical = True

    def __init__(self, windowed=False):
        self.root = tk.Tk(className='FlashOS')
        self.root.title('FlashOS — говорящая система восстановления')
        self.root.configure(background=COLORS['background'])
        self.root.minsize(640, 480)
        if windowed:
            self.root.geometry('1100x760')
        else:
            self.root.overrideredirect(True)
            self.root.geometry(f'{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0')
            self.root.attributes('-fullscreen', True)
        self.canvas = tk.Canvas(self.root, bg=COLORS['background'], highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.queue = deque()
        self.delay = 150
        self.previous = None
        self.regions = []
        self.tab_regions = []
        self.action_regions = []
        self.pointer_values = dict(POINTER_DEFAULTS)
        self.pointer_monitor = None
        self.pointer_cursor = None
        self.pointer_error = ''
        self.pointer_title = ''
        self._wheel_remainder = 0
        self.root.bind('<KeyPress>', self.key)
        self.root.bind('<Configure>', lambda event: setattr(self, 'previous', None))
        self.canvas.bind('<Button-1>', self.click)
        self.canvas.bind('<Double-Button-1>', self.activate)
        self.canvas.bind('<Button-3>', lambda event: self.queue.append(27))
        self.canvas.bind('<Button-4>', lambda event: self.scroll(-1))
        self.canvas.bind('<Button-5>', lambda event: self.scroll(1))
        self.canvas.bind('<MouseWheel>', self.wheel)
        for keysym in ('p', 'P', 'Cyrillic_ze', 'Cyrillic_ZE'):
            try:
                self.root.bind('<Control-Alt-' + keysym + '>', self.reveal_pointer)
            except tk.TclError:
                # Some non-X11 Tk builds omit the X11 Cyrillic keysym table.
                continue
        self.root.protocol('WM_DELETE_WINDOW', lambda: self.queue.append(curses.KEY_F10))
        self.root.update()
        self.root.focus_force()

    def configure_pointer(self, values):
        self.pointer_values = validate_pointer(values)
        self.pointer_error = ''
        os.environ['XCURSOR_SIZE'] = str(self.pointer_values['size'])
        if os.name == 'posix' and os.environ.get('DISPLAY'):
            if self.pointer_monitor is None:
                self.pointer_monitor = PointerMonitor(self.pointer_values)
            else:
                self.pointer_monitor.configure(self.pointer_values)
            try:
                if self.pointer_cursor is None:
                    self.pointer_cursor = X11Cursor()
                self.root.update_idletasks()
                self.pointer_cursor.configure(self.pointer_values,
                    [self.root.winfo_id(), self.canvas.winfo_id()])
                if not self.pointer_values['visible'] and not self.pointer_cursor.visibility:
                    self.pointer_error = 'Сервер X11 не поддерживает скрытие указателя во внешних окнах.'
            except (OSError, AttributeError) as error:
                self.pointer_error = 'Внешний вид указателя X11: ' + str(error)[:250]
        # A Tk fallback preserves visibility if Xcursor is unavailable.
        self.canvas.configure(cursor='none' if not self.pointer_values['visible'] else
                              '' if self.pointer_cursor else 'arrow')

    def pointer_status(self):
        lines = self.pointer_monitor.snapshot() if self.pointer_monitor else [
            'Настройки драйвера применяются в графическом режиме Linux после загрузки с флешки.']
        return ([self.pointer_error] if self.pointer_error else []) + lines

    def reveal_pointer(self, event=None):
        self.configure_pointer({**self.pointer_values, 'visible': True})
        # UI persists this rescue action using the same preferences path.
        self.queue.append(('pointer-visible', True))
        return 'break'

    def scroll(self, amount):
        self.queue.append(('scroll', self.pointer_title, amount * self.pointer_values['wheel_lines']))
        return 'break'

    def wheel(self, event):
        # Windows uses 120 per notch; high-resolution touchpads may send less.
        delta = event.delta
        if self.root.tk.call('tk', 'windowingsystem') == 'aqua':
            return self.scroll(-1 if delta > 0 else 1) if delta else 'break'
        self._wheel_remainder += delta
        steps = int(self._wheel_remainder / 120)
        self._wheel_remainder -= steps * 120
        return self.scroll(-steps) if steps else 'break'

    def key(self, event):
        if event.keysym in KEYS:
            self.queue.append(KEYS[event.keysym])
        elif event.char:
            self.queue.append(event.char)
        return 'break'

    def click(self, event):
        for left, right, top, bottom, key in self.action_regions:
            if left <= event.x <= right and top <= event.y <= bottom:
                self.queue.append(key)
                return True
        for left, right, top, bottom, index in self.tab_regions:
            if left <= event.x <= right and top <= event.y <= bottom:
                self.queue.append(('tab', index))
                return True
        pad = max(16, min(40, self.canvas.winfo_width() // 24))
        if not pad <= event.x <= self.canvas.winfo_width() - pad:
            return False
        for top, bottom, index in self.regions:
            if top <= event.y <= bottom:
                self.queue.append(('row', self.pointer_title, index,
                                   self.pointer_values['activation'] == 'single'))
                return True
        return False

    def activate(self, event):
        if self.pointer_values['activation'] == 'single':
            # The first press already selected/opened the target. A second
            # press of the same double click must not act in the next menu.
            return False
        for top, bottom, index in self.regions:
            if top <= event.y <= bottom:
                pad = max(16, min(40, self.canvas.winfo_width() // 24))
                if pad <= event.x <= self.canvas.winfo_width() - pad:
                    self.queue.append(('row', self.pointer_title, index, True))
                    return True
        return False

    def keypad(self, enabled):
        pass

    def timeout(self, milliseconds):
        self.delay = milliseconds

    def get_wch(self):
        until = time.monotonic() + self.delay / 1000
        while True:
            self.root.update()
            if self.queue:
                value = self.queue.popleft()
                return chr(value) if isinstance(value, int) and value < 256 else value
            if time.monotonic() >= until:
                raise curses.error('No key')
            time.sleep(.01)

    def getch(self):
        try:
            value = self.get_wch()
            return ord(value) if isinstance(value, str) else value
        except curses.error:
            return -1

    def getmaxyx(self):
        return self.root.winfo_height() // 24, self.root.winfo_width() // 12

    def render(self, title, rows, selected=0, footer='', tabs=None, tab_index=0, detail=''):
        self.pointer_title = title
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        selected = max(0, min(selected, len(rows) - 1))
        state = (title, tuple(rows), selected, footer, width, height, tuple(tabs or ()), tab_index, detail)
        if state == self.previous:
            return
        self.previous = state
        c = self.canvas
        c.delete('all')
        self.regions, self.tab_regions, self.action_regions = [], [], []
        pad = max(16, min(40, width // 24))
        body_width = max(1, width - pad * 2)
        compact = height < 660 or width < 850
        body_font = font.Font(family='DejaVu Sans', size=15 if compact else 18)
        small_font = font.Font(family='DejaVu Sans', size=9 if compact else 10)
        title_font = font.Font(family='DejaVu Sans', size=20 if compact else 26, weight='bold')
        tab_font = font.Font(family='DejaVu Sans', size=11 if compact else 13, weight='bold')
        detail_font = font.Font(family='DejaVu Sans', size=10 if compact else 12)
        self.fonts = (body_font, small_font, title_font, tab_font, detail_font)
        self.layout_bounds = []
        def text(x, y, value, face=body_font, color=COLORS['ink'], **kwargs):
            item = c.create_text(x, y, text=value, anchor=kwargs.pop('anchor', 'nw'), fill=color,
                                 font=face, **kwargs)
            self.layout_bounds.append(('text', c.bbox(item)))
            return item

        # Reserve navigation first so reports and long titles cannot cover the controls.
        navigation = (['← → / Tab Вкладки · F2 Поиск · F4 Источник · F5 Обновить',
                       '↑ ↓ Выбор · Enter Открыть · Esc Назад · Пробел Повтор',
                       'F1 Помощь · F3 Описание · F6 Звук · F7 Стоп речи · F10 Питание'] if tabs else
                      ['↑ ↓ Выбор · Enter Открыть · Esc Назад · Пробел Повтор',
                       'F1 Помощь · F3 Описание · F6 Звук · F7 Стоп речи · F10 Питание',
                       'F8 Отчёт звука · + / − Громкость'])
        nav_lines = [part for line in navigation for part in wrap_pixels(line, small_font.measure, body_width)]
        status = wrap_pixels(footer, small_font.measure, body_width, maximum=2)
        small_height = small_font.metrics('linespace')
        footer_top = height - (len(nav_lines) + len(status)) * small_height - 84
        c.create_rectangle(0, footer_top, width, height, fill=COLORS['surface'], outline='')
        c.create_line(pad, footer_top, width - pad, footer_top, fill=COLORS['border'])
        text(pad, footer_top + 10, '\n'.join(status), small_font, COLORS['muted'])
        text(pad, footer_top + 18 + len(status) * small_height, '\n'.join(nav_lines), small_font)
        button_top = height - 50
        button_width = body_width / 4
        for number, (label, key) in enumerate((('Назад', 27), ('Открыть', 10),
                                               ('Справка', curses.KEY_F1), ('Стоп речи', curses.KEY_F7))):
            left, right = pad + number * button_width, pad + (number + 1) * button_width - 6
            c.create_rectangle(left, button_top, right, button_top + 44,
                               fill=COLORS['background'], outline=COLORS['border'])
            text((left + right) / 2, button_top + 22, label, small_font, anchor='center')
            self.action_regions.append((left, right, button_top, button_top + 44, key))

        text(pad, 15, 'FLASH OS', tab_font, COLORS['selected'])
        position = f'{selected + 1} из {len(rows)}' if rows else 'Нет пунктов'
        text(width - pad, 17, position, small_font, COLORS['muted'], anchor='ne')
        y = 22 + tab_font.metrics('linespace')
        title_lines = wrap_pixels(title, title_font.measure, body_width, maximum=2)
        text(pad, y, '\n'.join(title_lines), title_font)
        y += len(title_lines) * title_font.metrics('linespace') + 8
        if detail:
            detail_lines = wrap_pixels(detail, detail_font.measure, body_width, maximum=1 if compact else 2)
            text(pad, y, '\n'.join(detail_lines), detail_font, COLORS['muted'])
            y += len(detail_lines) * detail_font.metrics('linespace') + 10
        if tabs:
            tab_width = body_width / len(tabs)
            tab_height = tab_font.metrics('linespace') + 20
            for index, tab in enumerate(tabs):
                left, right = pad + index * tab_width, pad + (index + 1) * tab_width - 6
                active = index == tab_index
                c.create_rectangle(left, y, right, y + tab_height,
                    fill=COLORS['selected'] if active else COLORS['surface'],
                    outline=COLORS['selected'] if active else COLORS['border'], width=2)
                if active:
                    c.create_rectangle(left, y + tab_height - 4, right, y + tab_height,
                                       fill=COLORS['focus'], outline='')
                label = ellipsize(tab, tab_font.measure, right - left - 20)
                text(left + 10, y + 8, label, tab_font, COLORS['surface'] if active else COLORS['ink'])
                self.tab_regions.append((left, right, y, y + tab_height, index))
            y += tab_height + 14
        else:
            y += 6

        available = max(1, footer_top - y - 14)
        line_height = body_font.metrics('linespace')
        normal_height = line_height + (20 if compact else 24)
        maximum_lines = max(1, int((available - 22) // line_height))
        focused = wrap_pixels(str(rows[selected]) if rows else 'Нет данных.',
                              body_font.measure, body_width - 48, maximum=maximum_lines)
        focused_height = min(available, len(focused) * line_height + 22)
        capacity = max(1, int((available - focused_height) // normal_height) + 1)
        start = max(0, min(selected - capacity // 2, len(rows) - capacity))
        for index in range(start, min(len(rows), start + capacity)):
            active = index == selected
            row_height = focused_height if active else normal_height
            bottom = y + row_height - 5
            c.create_rectangle(pad, y, width - pad, bottom,
                fill=COLORS['selected'] if active else COLORS['surface'],
                outline=COLORS['selected'] if active else COLORS['border'], width=2 if active else 1)
            if active:
                c.create_rectangle(pad + 2, y + 2, pad + 7, bottom - 2, fill=COLORS['focus'], outline='')
            label = '\n'.join(focused) if active else ellipsize(rows[index], body_font.measure, body_width - 48)
            item = text(pad + 22, y + 8, label, body_font, COLORS['surface'] if active else COLORS['ink'])
            self.layout_bounds.append(('row-text', c.bbox(item)))
            self.regions.append((y, bottom, index))
            y += row_height
        self.layout_bounds.append(('list', (pad, self.regions[0][0] if self.regions else y,
                                            width - pad, y)))
        self.layout_bounds.append(('footer', (0, footer_top, width, height)))

    def suspend(self):
        self.queue.clear()
        self.root.withdraw()
        self.root.update()

    def resume(self):
        self.queue.clear()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.previous = None
        self.root.update()
        self.configure_pointer(self.pointer_values)

    def close(self):
        if self.pointer_monitor is not None:
            self.pointer_monitor.close()
        if self.pointer_cursor is not None:
            self.pointer_cursor.close()
        self.root.destroy()
