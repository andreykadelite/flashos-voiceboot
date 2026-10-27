"""USB preference validation, hotplug and libinput capability boundaries."""
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pointer_settings as pointer


DEVICE_LIST = '''Virtual core pointer                     id=2 [master pointer  (3)]
  ↳ Virtual core XTEST pointer              id=4 [slave  pointer  (2)]
  ↳ USB Optical Mouse                      id=9 [slave  pointer  (2)]
  ↳ ELAN Touchpad                          id=12 [slave  pointer  (2)]
Virtual core keyboard                      id=3 [master keyboard (2)]
  ↳ Keyboard                               id=8 [slave  keyboard (3)]
'''
PROPERTIES = '''Device 'USB Optical Mouse':
    Device Enabled (151): 1
    libinput Accel Speed (152): 0.000000
    libinput Left Handed Enabled (153): 0
    libinput Natural Scrolling Enabled (154): 0
    libinput Accel Profiles Available (155): 1, 1, 1
    libinput Accel Profile Enabled (156): 1, 0, 0
    libinput Tapping Enabled (157): 0
    libinput Tapping Drag Enabled (158): 1
    libinput Disable While Typing Enabled (159): 1
    libinput Scroll Methods Available (160): 1, 0, 0
    libinput Scroll Method Enabled (161): 1, 0, 0
    libinput Scroll Method Enabled Default (162): 1, 0, 0
'''


class PointerSettingsTests(unittest.TestCase):
    def test_types_ranges_and_unknown_values_fall_back(self):
        invalid = {'size': True, 'speed': 1000, 'visible': 1, 'wheel_lines': 5.0,
                   'color': '$(command)', 'activation': ['single'], 'custom': 1}
        self.assertEqual(pointer.validate(invalid), pointer.DEFAULTS)
        self.assertEqual(pointer.validate({'visible': False, 'size': 64})['size'], 64)
        self.assertFalse(pointer.validate({'visible': False})['visible'])

    def test_device_detection_excludes_keyboards_master_and_test_devices(self):
        self.assertEqual(pointer.parse_devices(DEVICE_LIST), [(9, 'USB Optical Mouse'), (12, 'ELAN Touchpad')])
        self.assertEqual(pointer.parse_devices('keyboard id=5 [slave keyboard (3)]'), [])

    def test_only_advertised_libinput_properties_are_changed(self):
        properties = pointer.parse_properties(PROPERTIES)
        changes = pointer.property_changes({**pointer.DEFAULTS, 'speed': 40,
                                           'acceleration': 'flat', 'left_handed': True}, properties)
        self.assertEqual(changes['libinput Accel Speed'], ('0.4',))
        self.assertEqual(changes['libinput Accel Profile Enabled'], ('0', '1', '0'))
        self.assertEqual(changes['libinput Left Handed Enabled'], ('1',))
        self.assertNotIn('Device Enabled', changes)
        self.assertNotIn('libinput Click Method Enabled', changes)
        self.assertFalse(any(key.endswith('Default') for key in changes))

    def test_unsupported_scroll_method_is_not_written(self):
        properties = pointer.parse_properties(PROPERTIES)
        changes = pointer.property_changes({**pointer.DEFAULTS, 'scroll_method': 'edge'}, properties)
        self.assertNotIn('libinput Scroll Method Enabled', changes)
        properties['libinput Scroll Method Enabled'] = ('0', '0', '0')
        changes = pointer.property_changes(pointer.DEFAULTS, properties)
        self.assertEqual(changes['libinput Scroll Method Enabled'], ('1', '0', '0'))

    def test_backend_reads_back_and_does_not_rewrite_equal_numbers(self):
        properties = pointer.parse_properties(PROPERTIES)
        values = {**pointer.DEFAULTS, 'tap_to_click': False}
        responses = [mock.Mock(returncode=0, stdout='Mouse id=9 [slave pointer (2)]', stderr=''),
                     mock.Mock(returncode=0, stdout=PROPERTIES, stderr=''),
                     mock.Mock(returncode=0, stdout=PROPERTIES, stderr='')]
        runner = mock.Mock(side_effect=responses)
        report = pointer.XInputBackend(runner).apply(values)
        self.assertIn('проверено параметров:', report[0])
        self.assertEqual(len(runner.call_args_list), 3)
        self.assertFalse(any('--set-prop' in call.args[0] for call in runner.call_args_list))
        self.assertTrue(all(call.kwargs['timeout'] == 2 for call in runner.call_args_list))

    def test_failed_readback_is_reported_without_disabling_devices(self):
        runner = mock.Mock(side_effect=[
            mock.Mock(returncode=0, stdout='Mouse id=9 [slave pointer (2)]', stderr=''),
            mock.Mock(returncode=0, stdout=PROPERTIES, stderr=''),
            mock.Mock(returncode=0, stdout='', stderr=''),
            mock.Mock(returncode=0, stdout=PROPERTIES, stderr='')])
        report = pointer.XInputBackend(runner).apply(pointer.DEFAULTS)
        self.assertIn('не удалось проверить libinput Tapping Enabled', report[0])
        mutation = runner.call_args_list[2].args[0]
        self.assertEqual(mutation, ['xinput', '--set-prop', '9', 'libinput Tapping Enabled', '1'])

    def test_monitor_retries_after_unavailable_backend_and_receives_new_settings(self):
        backend = mock.Mock()
        first, second = threading.Event(), threading.Event()
        def apply(values):
            if not first.is_set():
                first.set()
                raise FileNotFoundError('xinput')
            second.set()
            return ['USB Mouse']
        backend.apply.side_effect = apply
        monitor = pointer.PointerMonitor(pointer.DEFAULTS, backend)
        try:
            self.assertTrue(first.wait(2))
            monitor.configure({**pointer.DEFAULTS, 'speed': 40})
            self.assertTrue(second.wait(2))
            monitor.thread.join(.01)
            self.assertEqual(backend.apply.call_args.args[0]['speed'], 40)
        finally:
            monitor.close()
            monitor.thread.join(2)
        self.assertFalse(monitor.thread.is_alive())

    def test_cursor_has_transparent_background_outline_and_visible_fill(self):
        for size in (24, 32, 48, 64):
            for color in ('white', 'black', 'yellow'):
                with self.subTest(size=size, color=color):
                    pixels = pointer.cursor_pixels(size, color)
                    self.assertEqual(len(pixels), size * size)
                    self.assertEqual(len(set(pixels)), 3)
                    self.assertEqual(pixels[-1], 0)


@unittest.skipUnless(os.name == 'posix', 'Native curses menu tested on Linux')
class PointerMenuTests(unittest.TestCase):
    def ui(self, keys):
        from interface import UI
        screen = mock.Mock(graphical=True)
        screen.getch.side_effect = keys
        settings = mock.Mock(data={'pointer': dict(pointer.DEFAULTS)})
        return UI(screen, mock.Mock(volume=75, error=''), settings, '/tmp')

    def test_click_speaks_only_target_and_double_click_opens(self):
        ui = self.ui([('row', 'Menu', 2, False), ('row', 'Menu', 2, True)])
        self.assertEqual(ui.choose('Menu', [('One', 1), ('Two', 2), ('Three', 3)]), 3)
        spoken = [call.args[0] for call in ui.speech.say.call_args_list]
        self.assertEqual(spoken, ['One', 'Three', 'Three'])

    def test_stale_pointer_event_cannot_activate_a_new_confirmation(self):
        ui = self.ui([('row', 'Earlier menu', 1, True), 10])
        self.assertFalse(ui.confirm('Erase?'))

    def test_wheel_clamps_at_boundaries_and_keyboard_remains_unchanged(self):
        ui = self.ui([('scroll', 'Menu', 50), 10])
        self.assertEqual(ui.choose('Menu', [('One', 1), ('Two', 2)]), 2)

    def test_reveal_rescue_preserves_other_preferences(self):
        ui = self.ui([])
        ui.change_settings = mock.Mock()
        self.assertTrue(ui.global_key(('pointer-visible', True)))
        ui.change_settings.assert_called_once_with({'pointer': pointer.DEFAULTS}, 'Указатель показан')

    def test_text_input_preserves_rescue_and_ignores_row_scroll_events(self):
        ui = self.ui([])
        ui.screen.get_wch.side_effect = [('row', 'Input', 1, True), ('scroll', 'Input', 3),
                                         ('pointer-visible', True), 'a', '\n']
        ui.change_settings = mock.Mock()
        self.assertEqual(ui.text_input('Input'), 'a')
        ui.change_settings.assert_called_once_with({'pointer': pointer.DEFAULTS}, 'Указатель показан')

    def test_pointer_event_during_explanation_does_not_confirm_next_action(self):
        ui = self.ui([('row', 'Explanation', 1, True), 10])
        ui.speech.say.return_value = threading.Event()
        self.assertFalse(ui.speak_wait('Explanation', 'Explanation'))
        self.assertFalse(ui.confirm('Erase?'))

    def test_pointer_rescue_is_preserved_in_keyboard_test(self):
        ui = self.ui([])
        ui.screen.get_wch.side_effect = [('row', 'Проверка клавиатуры', 1, True),
                                         ('pointer-visible', True), '\x1b']
        ui.change_settings = mock.Mock()
        ui.keyboard_test()
        ui.change_settings.assert_called_once_with({'pointer': pointer.DEFAULTS}, 'Указатель показан')

    def test_job_controls_announce_state_stop_speech_and_cancel_safely(self):
        import curses
        import time
        ui = self.ui([])
        ui.settings.data['progress_seconds'] = 30
        events = iter([10, curses.KEY_F1, curses.KEY_F7, ('pointer-visible', True),
                       ('row', 'Job', 1, True), ('scroll', 'Job', 1), 27])
        def read():
            time.sleep(.005)
            return next(events, -1)
        ui.screen.getch.side_effect = read
        ui.change_settings, ui.view = mock.Mock(), mock.Mock()
        def run(job):
            while True:
                job.check()
                time.sleep(.005)
        result = ui.run_job('Job', run, review=False, save=False)
        self.assertTrue(result.cancelled)
        ui.speech.stop.assert_called_once()
        ui.change_settings.assert_called_once_with({'pointer': pointer.DEFAULTS}, 'Указатель показан')
        self.assertTrue(any('Прошло' in call.args[0] for call in ui.speech.say.call_args_list))


if __name__ == '__main__':
    unittest.main()
