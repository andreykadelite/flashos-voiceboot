"""Meaningful keyboard and transaction boundary checks for the spoken store."""
import curses
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from interface import UI
from store_client import result_json
from store import ApplicationStore, rows, TABS


class Speech:
    error = ''
    volume = 60
    def __init__(self): self.spoken = []
    def say(self, value, **options): self.spoken.append(value)


class Screen:
    graphical = True
    def __init__(self, keys): self.keys = iter(keys); self.frames = []
    def keypad(self, enabled): pass
    def timeout(self, value): pass
    def getch(self): return next(self.keys)
    def render(self, *args, **options): self.frames.append((args, options))


class KeyboardTests(unittest.TestCase):
    def ui(self, keys):
        screen, speech = Screen(keys), Speech()
        ui = UI(screen, speech, object(), Path('/tmp'), demo=True)
        return ui, screen, speech

    def test_tabs_are_keyboard_reachable_in_both_directions(self):
        for key, previous, expected in [(curses.KEY_RIGHT,0,1),(9,2,0),(curses.KEY_LEFT,0,2),(curses.KEY_BTAB,1,0)]:
            ui, screen, speech = self.ui([key])
            self.assertEqual(ui.choose('Магазин', [('Поиск', 'search')], tabs=TABS, tab_index=previous), ('__tab__', expected))
            self.assertEqual(screen.frames[-1][1]['tabs'], TABS)
            self.assertIn('Поиск', speech.spoken)

    def test_pointer_tab_selection_matches_keyboard_contract(self):
        ui, _, _ = self.ui([('tab', 2)])
        self.assertEqual(ui.choose('Магазин', [('Поиск', None)], tabs=TABS), ('__tab__', 2))

    def test_search_and_source_shortcuts_do_not_activate_installation(self):
        for key, expected in [(curses.KEY_F2, 'search'), (curses.KEY_F4, 'source')]:
            ui, _, _ = self.ui([key])
            self.assertEqual(ui.choose('Магазин', [('Установить', 'install')],
                tabs=TABS, shortcuts={key:expected}), expected)

    def test_confirmation_initial_enter_cancels(self):
        ui, _, speech = self.ui([10])
        self.assertFalse(ui.confirm('Установить?'))
        self.assertIn('Отмена', speech.spoken[0])


class ResultTests(unittest.TestCase):
    def test_progress_does_not_corrupt_structured_result(self):
        self.assertEqual(result_json('Download 20%\n{"format":1,"ok":true,"data":{"entries":[]}}\n'), {'entries':[]})

    def test_backend_error_and_truncation_never_report_success(self):
        for output in ['{"format":1,"ok":false,"error":"Недостаточно места"}', '{"format":1,"ok":true']:
            with self.assertRaises(RuntimeError): result_json(output)

    def test_backend_entries_and_empty_list_shapes(self):
        self.assertEqual(rows({'entries':[{'package':'nano'}]}), [{'package':'nano'}])
        self.assertEqual(rows({'entries':[]}), [])


class ConfirmationTests(unittest.TestCase):
    def test_cancelled_plan_does_not_start_apply(self):
        store = object.__new__(ApplicationStore)
        store.source = 'stable'
        class FakeUI:
            focus = {}
            def choose(self, *args, **kwargs): return False
        store.ui = FakeUI()
        calls = []
        def task(title, action, request=None, **options):
            calls.append(action)
            return {'changes':[{'package':'nano','action':'install','to_version':'1'}],
                    'download_bytes':10,'required_bytes':100}
        store.task = task
        self.assertFalse(store.transaction('install', ['nano']))
        self.assertEqual(calls, ['plan'])

    def test_dependency_review_returns_to_cancel_by_default(self):
        store = object.__new__(ApplicationStore)
        store.source = 'all'
        class FakeUI:
            focus = {}
            choices = iter(['plan', False])
            reviewed = []
            def choose(self, *args, **kwargs): return next(self.choices)
            def view(self, title, lines): self.reviewed.extend(lines)
        store.ui = FakeUI()
        calls = []
        def task(title, action, request=None, **options):
            calls.append(action)
            return {'changes':[{'package':'mousepad','action':'install','to_version':'1'},
                               {'package':'dependency','action':'install','to_version':'2'}],
                    'required_bytes':1024}
        store.task = task
        self.assertFalse(store.transaction('install', ['mousepad']))
        self.assertTrue(any('dependency' in row for row in store.ui.reviewed))
        self.assertEqual(calls, ['plan'])


if __name__ == '__main__':
    unittest.main()
