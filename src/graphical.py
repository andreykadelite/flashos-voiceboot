"""Native adaptive display, sharing the console's navigation and speech logic."""
from collections import deque
import curses
from pathlib import Path
import time
import tkinter as tk
from tkinter import font


KEYS = {'Up': curses.KEY_UP, 'Down': curses.KEY_DOWN, 'Left': curses.KEY_LEFT,
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
        self.root.configure(background='#0b1220')
        self.root.minsize(640, 480)
        if windowed:
            self.root.geometry('1100x760')
        else:
            self.root.overrideredirect(True)
            self.root.geometry(f'{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0')
            self.root.attributes('-fullscreen', True)
        self.canvas = tk.Canvas(self.root, bg='#0b1220', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.queue = deque()
        self.delay = 150
        self.previous = None
        self.regions = []
        self.root.bind('<KeyPress>', self.key)
        self.root.bind('<Configure>', lambda event: setattr(self, 'previous', None))
        self.canvas.bind('<Button-1>', self.click)
        self.canvas.bind('<Double-Button-1>', self.activate)
        self.canvas.bind('<Button-4>', lambda event: self.queue.append(curses.KEY_UP))
        self.canvas.bind('<Button-5>', lambda event: self.queue.append(curses.KEY_DOWN))
        self.root.protocol('WM_DELETE_WINDOW', lambda: self.queue.append(curses.KEY_F10))
        self.root.update()
        self.root.focus_force()

    def key(self, event):
        if event.keysym in KEYS:
            self.queue.append(KEYS[event.keysym])
        elif event.char:
            self.queue.append(event.char)
        return 'break'

    def click(self, event):
        pad = max(20, min(64, self.canvas.winfo_width() // 24))
        if not pad <= event.x <= self.canvas.winfo_width() - pad:
            return False
        for top, bottom, index in self.regions:
            if top <= event.y <= bottom:
                self.queue.append(curses.KEY_HOME)
                self.queue.extend([curses.KEY_DOWN] * index)
                return True
        return False

    def activate(self, event):
        if self.click(event):
            self.queue.append(10)

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

    def render(self, title, rows, selected=0, footer=''):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        state = (title, tuple(rows), selected, footer, width, height)
        if state == self.previous:
            return
        self.previous = state
        c = self.canvas
        c.delete('all')
        self.regions = []
        pad = max(20, min(64, width // 24))
        body_width = width - pad * 2
        compact = height < 660 or width < 850
        text_size = 17 if compact else 21
        def text(x, y, value, size=16, color='#dce6f1', **kwargs):
            return c.create_text(x, y, text=value, anchor=kwargs.pop('anchor', 'nw'), fill=color,
                font=('DejaVu Sans', size), **kwargs)
        c.create_rectangle(0, 0, width, 5, fill='#52dfc4', outline='')
        text(pad, 21, 'FLASH OS', 16, '#52dfc4')
        if width >= 850:
            text(width - pad, 24, 'Голос • Клавиатура • Восстановление', 12, '#aebfd2', anchor='ne')
        import textwrap
        heading_size = (26 if not compact else 21) if len(title) < 85 else (15 if compact else 18)
        heading_font = font.Font(family='DejaVu Sans', size=heading_size)
        heading_chars = max(20, int(body_width / max(1, heading_font.measure('н'))))
        heading_lines = textwrap.wrap(title, width=heading_chars, break_long_words=True)
        maximum = max(2, height // 5 // heading_font.metrics('linespace'))
        if len(heading_lines) > maximum:
            heading_lines = heading_lines[:maximum - 1] + ['… F3 — прочитать полностью']
        heading = text(pad, 64 if not compact else 57, '\n'.join(heading_lines), heading_size,
                       '#ffffff', width=body_width)
        top = c.bbox(heading)[3] + 22
        bottom = height - (115 if not compact else 103)
        # A fixed navigation area and a wrapping focused row keep every selection visible.
        available = max(110, bottom - top)
        row_font = font.Font(family='DejaVu Sans', size=text_size)
        line_height = row_font.metrics('linespace')
        chars = max(20, int((body_width - 48) / max(1, row_font.measure('н'))))
        wrapped = textwrap.fill(str(rows[selected]) if rows else 'Нет данных.', width=chars,
                                break_long_words=True)
        selected_height = min(available, max(60, (wrapped.count('\n') + 1) * line_height + 28))
        capacity = max(1, int((available - selected_height) // 53) + 1)
        start = max(0, min(selected - capacity // 2, len(rows) - capacity))
        y = top
        for index in range(start, min(len(rows), start + capacity)):
            active = index == selected
            row_height = selected_height if active else 53
            c.create_rectangle(pad, y, width - pad, y + row_height - 5,
                fill='#173e46' if active else '#131f30',
                outline='#52dfc4' if active else '#253347', width=2 if active else 1)
            if active:
                c.create_rectangle(pad, y, pad + 5, y + row_height - 5, fill='#52dfc4', outline='')
            label = wrapped if active else str(rows[index])
            if not active and row_font.measure(label) > body_width - 48:
                while label and row_font.measure(label + '…') > body_width - 48:
                    label = label[:-1]
                label += '…'
            # Very long report rows are fully spoken; smaller display windows show continuation.
            max_lines = max(1, int((row_height - 24) // line_height))
            parts = label.splitlines()
            if len(parts) > max_lines:
                label = '\n'.join(parts[:max_lines]) + '…'
            text(pad + 22, y + 12, label, text_size, '#ffffff' if active else '#cbd8e7')
            self.regions.append((y, y + row_height - 5, index))
            y += row_height
        status_y = height - (95 if not compact else 86)
        c.create_line(pad, status_y - 14, width - pad, status_y - 14, fill='#34445a')
        text(pad, status_y, f'{selected + 1} / {len(rows)}   •   {footer}', 11, '#aec1d5', width=body_width)
        text(pad, height - 56, '↑ ↓  Выбор     Enter  Открыть     Esc  Назад     Пробел  Повтор',
             11 if compact else 12, '#e0eaf6', width=body_width)
        text(pad, height - 30, 'F1  Помощь    F3  Где я    F6  Звук    F8  Отчёт звука    F10  Питание',
             10 if compact else 11, '#aec1d5', width=body_width)

    def close(self):
        self.root.destroy()
