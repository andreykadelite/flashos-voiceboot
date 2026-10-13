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
        self.assertEqual(self.screen.queue.popleft(), graphical.curses.KEY_HOME)
        self.assertEqual(list(self.screen.queue), [graphical.curses.KEY_DOWN] * index)
        self.screen.queue.clear()
        self.screen.key(types.SimpleNamespace(keysym='F3', char=''))
        self.assertEqual(self.screen.queue.popleft(), graphical.curses.KEY_F3)


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
