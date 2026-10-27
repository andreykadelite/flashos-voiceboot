"""Actual-font layout boundaries and the shared keyboard/pointer contract."""
import os
from pathlib import Path
import sys
import types
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    import graphical
except ImportError:
    graphical = None


@unittest.skipIf(graphical is None, 'Tk and curses are required')
class TextTests(unittest.TestCase):
    def test_unbroken_path_and_wide_letters_fit(self):
        def measure(value):
            return sum(18 if char == 'Ш' else 7 for char in value)
        value = 'Широкая строка /' + 'long-path/' * 30
        lines = graphical.wrap_pixels(value, measure, 120)
        self.assertTrue(all(measure(line) <= 120 for line in lines))
        self.assertEqual(''.join(lines).replace(' ', ''), value.replace(' ', ''))

    def test_omitted_text_is_marked_and_within_width(self):
        lines = graphical.wrap_pixels('Подробный отчёт ' * 40, len, 24, maximum=2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith('…'))
        self.assertTrue(all(len(line) <= 24 for line in lines))

    def test_text_and_focus_have_strong_contrast(self):
        def luminance(hex_color):
            rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in rgb]
            return sum(value * factor for value, factor in zip(linear, (.2126, .7152, .0722)))
        for first, second, minimum in [('ink', 'surface', 7), ('muted', 'background', 4.5),
                                       ('surface', 'selected', 7), ('focus', 'selected', 3)]:
            values = sorted([luminance(graphical.COLORS[first]), luminance(graphical.COLORS[second])])
            self.assertGreaterEqual((values[1] + .05) / (values[0] + .05), minimum)


@unittest.skipUnless(graphical is not None and (os.name == 'nt' or os.environ.get('DISPLAY')), 'Real Tk display required')
class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.screen = graphical.GraphicalScreen(windowed=True)

    def tearDown(self):
        self.screen.close()

    def test_all_resolutions_keep_selection_and_navigation_visible(self):
        for width, height in ((640, 480), (800, 600), (1024, 768), (1366, 768)):
            self.screen.root.geometry(f'{width}x{height}')
            self.screen.root.update()
            for long, selected in ((False, 0), (False, 49), (True, 25)):
                with self.subTest(size=(width, height), long=long, selected=selected):
                    rows = [f'{i + 1}. Приложение Debian' for i in range(50)]
                    if long:
                        rows[selected] += ' — описание и путь ' * 45
                    self.screen.render('Магазин Debian — Установленные' if not long else 'Длинный заголовок проверки ' * 12,
                                       rows, selected, 'Громкость 60%.' if not long else 'Подробное состояние подготовки ' * 10,
                                       tabs=('Доступные', 'Установленные', 'Обновления'),
                                       detail='Описание выбранного приложения ' * 8 if long else '')
                    self.screen.root.update_idletasks()
                    bounds = dict(item for item in self.screen.layout_bounds if item[0] != 'text')
                    self.assertLess(bounds['list'][3], bounds['footer'][1])
                    self.assertIn(selected, [region[2] for region in self.screen.regions])
                    for kind, (left, top, right, bottom) in self.screen.layout_bounds:
                        if kind == 'row-text':
                            self.assertLess(bottom, bounds['footer'][1])
                        if kind == 'text':
                            self.assertGreaterEqual(left, 0)
                            self.assertGreaterEqual(top, 0)
                            self.assertLessEqual(right, width)
                            self.assertLessEqual(bottom, height)

    def test_pointer_tabs_rows_and_keyboard_share_actions(self):
        self.screen.render('Магазин', ['Один', 'Два', 'Три'], tabs=('Доступные', 'Установленные', 'Обновления'))
        left, right, top, bottom, index = self.screen.tab_regions[2]
        self.screen.click(types.SimpleNamespace(x=(left + right) / 2, y=(top + bottom) / 2))
        self.assertEqual(self.screen.queue.popleft(), ('tab', 2))
        top, bottom, index = self.screen.regions[-1]
        self.screen.click(types.SimpleNamespace(x=80, y=(top + bottom) / 2))
        self.assertEqual(self.screen.queue.popleft(), ('row', 'Магазин', index, False))
        self.assertFalse(self.screen.queue)
        self.screen.queue.clear()
        self.screen.key(types.SimpleNamespace(keysym='F3', char=''))
        self.assertEqual(self.screen.queue.popleft(), graphical.curses.KEY_F3)

    def test_pointer_controls_wheel_and_single_click(self):
        self.screen.render('Меню', ['Один', 'Два', 'Три'])
        self.screen.pointer_values.update(activation='single', wheel_lines=3)
        top, bottom, index = self.screen.regions[-1]
        self.screen.click(types.SimpleNamespace(x=80, y=(top + bottom) / 2))
        self.assertEqual(self.screen.queue.popleft(), ('row', 'Меню', index, True))
        self.screen.activate(types.SimpleNamespace(x=80, y=(top + bottom) / 2))
        self.assertFalse(self.screen.queue, 'Second press of a single-activation double click acted twice')
        self.screen.scroll(1)
        self.assertEqual(self.screen.queue.popleft(), ('scroll', 'Меню', 3))
        for left, right, top, bottom, key in self.screen.action_regions:
            self.screen.click(types.SimpleNamespace(x=(left + right) / 2, y=(top + bottom) / 2))
            self.assertEqual(self.screen.queue.popleft(), key)

    def test_native_mouse_events_reach_the_same_safe_actions(self):
        import shutil
        import subprocess
        if not shutil.which('xdotool'):
            self.skipTest('xdotool is required to inject real X11 pointer events')
        self.screen.render('Меню', ['Один', 'Два', 'Три'])
        self.screen.root.update()
        top, bottom, index = self.screen.regions[-1]
        subprocess.run(['xdotool', 'mousemove', str(self.screen.canvas.winfo_rootx() + 80),
                        str(int(self.screen.canvas.winfo_rooty() + (top + bottom) / 2)),
                        'click', '--repeat', '2', '--delay', '80', '1'], check=True, timeout=5)
        self.screen.root.update()
        events = list(self.screen.queue)
        self.assertIn(('row', 'Меню', index, False), events)
        self.assertIn(('row', 'Меню', index, True), events)
        self.screen.queue.clear()
        subprocess.run(['xdotool', 'click', '3', 'click', '5'], check=True, timeout=5)
        self.screen.root.update()
        self.assertEqual(list(self.screen.queue), [27, ('scroll', 'Меню', 1)])

    @unittest.skipUnless(os.name == 'posix', 'Russian XKB layout requires X11')
    def test_russian_layout_preserves_physical_pointer_rescue_shortcut(self):
        import shutil
        import subprocess
        from pointer_settings import DEFAULTS
        if not shutil.which('xdotool') or not shutil.which('setxkbmap'):
            self.skipTest('xdotool and setxkbmap are required for real XKB input')
        self.screen.configure_pointer({**DEFAULTS, 'visible': False})
        self.screen.root.update()
        try:
            subprocess.run(['setxkbmap', '-layout', 'ru'], check=True, timeout=5)
            subprocess.run(['xdotool', 'key', '--clearmodifiers', 'Cyrillic_ze'],
                           check=True, timeout=5)
            self.screen.root.update()
            self.assertEqual(self.screen.queue.pop(), 'з', 'Ordinary Russian input was intercepted')
            subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+alt+Cyrillic_ze'],
                           check=True, timeout=5)
            self.screen.root.update()
            self.assertTrue(self.screen.pointer_values['visible'])
            self.assertFalse(self.screen.pointer_cursor.hidden)
            self.assertIn(('pointer-visible', True), self.screen.queue)
            self.screen.queue.clear()
            self.screen.configure_pointer({**DEFAULTS, 'visible': False})
            self.screen.root.event_generate('<Control-Alt-KeyPress-Cyrillic_ZE>')
            self.screen.root.update()
            self.assertTrue(self.screen.pointer_values['visible'])
            self.assertEqual(self.screen.queue.pop(), ('pointer-visible', True))
        finally:
            subprocess.run(['setxkbmap', '-layout', 'us'], check=True, timeout=5)

    @unittest.skipUnless(os.name == 'posix', 'Native Xcursor/XFixes requires X11')
    def test_x11_cursor_size_color_visibility_and_rescue(self):
        import ctypes as c
        from pointer_settings import DEFAULTS
        class CursorImage(c.Structure):
            _fields_ = [('x', c.c_short), ('y', c.c_short), ('width', c.c_ushort),
                        ('height', c.c_ushort), ('xhot', c.c_ushort), ('yhot', c.c_ushort),
                        ('serial', c.c_ulong), ('pixels', c.POINTER(c.c_ulong))]
        for size, color in ((24, 'white'), (48, 'black'), (64, 'yellow')):
            self.screen.configure_pointer({**DEFAULTS, 'size': size, 'color': color})
            self.assertEqual(self.screen.pointer_error, '')
            cursor = self.screen.pointer_cursor
            self.screen.canvas.event_generate('<Motion>', x=80, y=160, warp=True)
            self.screen.root.update()
            cursor.xfixes.XFixesGetCursorImage.argtypes = [c.c_void_p]
            cursor.xfixes.XFixesGetCursorImage.restype = c.POINTER(CursorImage)
            image = cursor.xfixes.XFixesGetCursorImage(cursor.display)
            try:
                self.assertEqual((image.contents.width, image.contents.height), (size, size))
                self.assertIn({'white': 0xffffffff, 'black': 0xff111111, 'yellow': 0xffffdf00}[color],
                              [image.contents.pixels[i] & 0xffffffff for i in range(size * size)])
            finally:
                cursor.xlib.XFree.argtypes = [c.c_void_p]
                cursor.xlib.XFree(image)
        self.screen.configure_pointer({**DEFAULTS, 'visible': False})
        self.assertTrue(self.screen.pointer_cursor.hidden)
        self.screen.reveal_pointer()
        self.assertFalse(self.screen.pointer_cursor.hidden)
        self.assertEqual(self.screen.queue.pop(), ('pointer-visible', True))


class WindowManagerTests(unittest.TestCase):
    def test_window_controls_preserve_safe_return_and_close(self):
        from store_session import openbox_config
        config = ET.fromstring(openbox_config(Path('/run/user/1000')))
        ns = {'ob': 'http://openbox.org/3.4/rc'}
        keys = {item.attrib['key']: item.find('ob:action', ns) for item in config.findall('ob:keyboard/ob:keybind', ns)}
        self.assertEqual(keys['A-F4'].attrib['name'], 'Close')
        self.assertEqual(keys['C-A-Escape'].find('ob:command', ns).text, '/usr/bin/touch /run/user/1000/return')
        self.assertEqual(keys['A-F10'].attrib['name'], 'ToggleMaximize')
        self.assertEqual(keys['A-S-Tab'].attrib['name'], 'PreviousWindow')
        self.assertEqual(keys['A-F7'].attrib['name'], 'Move')
        self.assertEqual(keys['A-F8'].attrib['name'], 'Resize')
        self.assertNotIn('A-space', keys)
        self.assertEqual(config.find('ob:focus/ob:followMouse', ns).text, 'no')


if __name__ == '__main__':
    unittest.main()
