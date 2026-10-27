import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from terminal_preferences import DEFAULTS, TerminalPreferences, character_name, speech_text, validate
from settings import DurabilityError


class TerminalPreferencesTests(unittest.TestCase):
    def test_defaults_echo_characters_and_only_the_first_prompt(self):
        self.assertEqual(DEFAULTS['input_echo'], 'characters')
        self.assertEqual(DEFAULTS['prompt_mode'], 'once')

    def test_validation_rejects_bool_for_numeric_and_unknown_values(self):
        self.assertEqual(validate({'font_size': True, 'scrollback': 2000000,
                                   'input_echo': 'passwords', 'auto_read': 'yes'}), DEFAULTS)
        self.assertEqual(validate(None), DEFAULTS)

    def test_save_survives_reopen_and_preserves_unrelated_voice_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder) / 'flashos'
            parent.mkdir()
            voice = parent / 'settings.json'
            voice.write_text('{"voice":"irina"}')
            preferences = TerminalPreferences(folder)
            preferences.save({'input_echo': 'both', 'font_size': 22, 'middle_paste': False})
            reopened = TerminalPreferences(folder)
            self.assertEqual(reopened.data['input_echo'], 'both')
            self.assertEqual(reopened.data['font_size'], 22)
            self.assertFalse(reopened.data['middle_paste'])
            self.assertEqual(voice.read_text(), '{"voice":"irina"}')
            self.assertEqual(list(parent.glob('*.tmp')), [])

    def test_failed_write_preserves_previous_file_and_in_memory_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            preferences = TerminalPreferences(folder)
            preferences.save({'font_size': 22})
            before = preferences.path.read_bytes()
            with patch('terminal_preferences.os.replace', side_effect=OSError('read only')):
                with self.assertRaises(OSError):
                    preferences.save({'font_size': 14})
            self.assertEqual(preferences.data['font_size'], 22)
            self.assertEqual(preferences.path.read_bytes(), before)
            self.assertEqual(list(preferences.path.parent.glob('*.tmp')), [])

    @unittest.skipUnless(os.name == 'posix', 'Directory fsync is a POSIX durability operation')
    def test_directory_fsync_failure_reports_already_committed_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            preferences = TerminalPreferences(folder)
            with patch('terminal_preferences.os.fsync', side_effect=[None, OSError('device error')]):
                with self.assertRaises(DurabilityError):
                    preferences.save({'font_size': 22})
            self.assertEqual(preferences.data['font_size'], 22)
            self.assertEqual(TerminalPreferences(folder).data['font_size'], 22)

    def test_corrupt_and_oversized_files_recover_to_defaults_and_report_error(self):
        for value in ('[]', '{broken', 'x' * 16385):
            with self.subTest(value=value[:10]), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'flashos/terminal.json'
                path.parent.mkdir()
                path.write_text(value)
                preferences = TerminalPreferences(folder)
                self.assertEqual(preferences.data, DEFAULTS)
                self.assertTrue(preferences.error)

    def test_character_names_preserve_unicode_and_name_silent_punctuation(self):
        self.assertEqual(character_name('я'), 'я')
        self.assertEqual(character_name('Ж', True), 'Заглавная Ж')
        self.assertEqual(character_name(' '), 'пробел')
        self.assertEqual(character_name('/'), 'косая черта')
        self.assertEqual(character_name('_'), 'подчёркивание')
        self.assertEqual(speech_text('/tmp/a_b', 'none'), '/tmp/a_b')
        self.assertIn('подчёркивание', speech_text('/tmp/a_b', 'some'))


if __name__ == '__main__':
    unittest.main()
