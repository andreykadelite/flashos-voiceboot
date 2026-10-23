import io
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from audio import Output
from speech import Speech

BUILTIN = Output('analog', '10', 10, 'Встроенный звук', 1)
USB = Output('usb', '20', 20, 'USB наушники', 0)
HDMI = Output('hdmi', '30', 30, 'HDMI', 2)


class Process:
    def __init__(self, status=0):
        self.stdin, self.stdout = io.BytesIO(), io.BytesIO()
        self.returncode = status

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class FakeAudio:
    def __init__(self, outputs):
        self.outputs = outputs
        self.activated = []

    def discover(self):
        return self.outputs

    def activate(self, output):
        self.activated.append(output.name)

    def player(self, output, volume):
        return ['pw-play', '--target=' + output.name, '-']


class SpeechTests(unittest.TestCase):
    def test_handoff_preserves_selected_output_across_discovery_and_renumbering(self):
        audio = FakeAudio([USB, BUILTIN, HDMI])
        speech = Speech(audio, watch=False, preferred_output=HDMI.name)
        self.addCleanup(speech.close)
        self.assertEqual(speech.output_name, HDMI.name)
        self.assertEqual(speech._current(), HDMI)
        renumbered = Output('hdmi', '85', 85, HDMI.title, 2)
        audio.outputs = [USB, renumbered, BUILTIN]
        speech.refresh(announce=False)
        self.assertEqual(speech._current(), renumbered)
        self.assertEqual(speech.output_name, HDMI.name)

    def test_handoff_missing_output_uses_available_device_and_recovers_hotplug(self):
        audio = FakeAudio([BUILTIN])
        speech = Speech(audio, watch=False, preferred_output=HDMI.name)
        self.addCleanup(speech.close)
        self.assertEqual(speech.output_name, BUILTIN.name)
        self.assertIsNone(speech.manual)
        audio.outputs = []
        speech.refresh(announce=False)
        self.assertIsNone(speech.output_name)
        audio.outputs = [USB]
        speech.refresh(announce=False)
        self.assertEqual(speech.output_name, USB.name)

    def test_rhvoice_launch_failure_uses_espeak_on_same_output(self):
        speech, audio = self.make_speech([BUILTIN])
        speech.voice = 'yuriy'
        with patch('speech.Path.is_file', return_value=True), patch('speech.subprocess.Popen',
                side_effect=[OSError('not executable'), Process(), Process()]) as spawn:
            speech.say('Проверка', wait=True)
        self.assertTrue(speech.fallback_voice)
        self.assertEqual(spawn.call_args_list[1].args[0][0], 'espeak-ng')
        self.assertEqual(speech.failed, {})
        self.assertEqual(speech.error, '')

    def make_speech(self, outputs):
        audio = FakeAudio(outputs)
        speech = Speech(audio, watch=False)
        self.addCleanup(speech.close)
        return speech, audio

    def test_failed_output_retries_next_and_is_not_immediately_reselected(self):
        speech, audio = self.make_speech([USB, BUILTIN])
        with patch('speech.subprocess.Popen', side_effect=[Process(), Process(1), Process(), Process()]) as spawn:
            speech.say('Проверка речи', wait=True)
        self.assertEqual(speech._current().name, 'analog')
        self.assertEqual(speech.error, '')
        self.assertEqual(spawn.call_count, 4)
        speech.refresh()
        self.assertEqual(speech._current().name, 'analog')

    def test_usb_hotplug_replays_current_selection_and_unplug_falls_back(self):
        speech, audio = self.make_speech([BUILTIN])
        speech.last = 'Выбрана Windows'
        with patch.object(speech, '_schedule') as speak:
            audio.outputs = [USB, BUILTIN]
            speech.refresh()
            self.assertEqual(speech._current(), USB)
            self.assertIn('Выбрана Windows', speak.call_args.args[0])
            audio.outputs = [BUILTIN]
            speech.refresh()
            self.assertEqual(speech._current(), BUILTIN)
            self.assertEqual(speak.call_count, 2)

    def test_late_first_device_automatically_repeats_pending_help(self):
        speech, audio = self.make_speech([])
        speech.say('Стрелки — выбор системы')
        with patch.object(speech, '_schedule') as speak:
            audio.outputs = [USB]
            speech.refresh()
            self.assertIn('Стрелки — выбор системы', speak.call_args.args[0])

    def test_manual_choice_survives_polling_and_card_renumbering(self):
        speech, audio = self.make_speech([USB, BUILTIN, HDMI])
        with patch.object(speech, '_schedule'):
            speech.next_output()
            self.assertEqual(speech.manual, BUILTIN.name)
            renumbered = Output('analog', '51', 51, BUILTIN.title, 1)
            audio.outputs = [USB, renumbered, HDMI]
            speech.refresh()
            self.assertEqual(speech._current(), renumbered)

    def test_all_devices_removed_do_not_keep_stale_selection(self):
        speech, audio = self.make_speech([USB])
        audio.outputs = []
        speech.refresh()
        self.assertEqual(speech.outputs, [])
        self.assertIsNone(speech._current())
        self.assertIn('автоматически', speech.error)

    def test_transient_audio_service_failure_can_recover(self):
        speech, audio = self.make_speech([BUILTIN])
        speech.last = 'Debian'
        with patch.object(speech, '_schedule') as speak:
            with patch.object(audio, 'discover', side_effect=subprocess.TimeoutExpired('pw-dump', 3)):
                speech.refresh()
            self.assertEqual(speech._current(), BUILTIN)
            speech.refresh()
            speak.assert_not_called()

    def test_hotplug_waiter_follows_replacement_worker(self):
        speech, audio = self.make_speech([BUILTIN])
        entered = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        calls = []
        def activate(output):
            index = len(calls)
            calls.append(output.name)
            entered[index].set()
            release[index].wait(2)
        with patch.object(audio, 'activate', side_effect=activate), patch('speech.subprocess.Popen', side_effect=lambda *a, **k: Process()):
            waiter = threading.Thread(target=speech.say, args=('Запускаю Windows', True))
            waiter.start()
            self.assertTrue(entered[0].wait(1))
            speech.say_if_idle('Диагностика сохранена')
            self.assertEqual(speech.last, 'Запускаю Windows')
            self.assertTrue(waiter.is_alive())
            with patch.object(audio, 'discover', side_effect=subprocess.TimeoutExpired('pw-dump', 3)):
                speech.refresh()
            self.assertEqual(speech._current(), BUILTIN)
            audio.outputs = [USB, BUILTIN]
            speech.refresh()
            self.assertTrue(entered[1].wait(1))
            release[0].set()
            waiter.join(0.05)
            self.assertTrue(waiter.is_alive(), 'Old worker released the Enter waiter')
            release[1].set()
            waiter.join(1)
            self.assertFalse(waiter.is_alive())

    def test_fallback_respects_previous_failure_cooldown(self):
        speech, audio = self.make_speech([USB, BUILTIN, HDMI])
        speech.failed[USB.name] = time.monotonic() + 20
        speech.refresh(announce=False)
        with patch('speech.subprocess.Popen', side_effect=[Process(), Process(1), Process(), Process()]):
            speech.say('Проверка', wait=True)
        self.assertEqual(audio.activated, ['analog', 'hdmi'])

    def test_absolute_deadline_stops_many_failed_outputs(self):
        speech, audio = self.make_speech([USB, BUILTIN, HDMI])
        now = [0]
        def slow_failure(output):
            now[0] += 91
            raise OSError('Simulated slow hardware')
        with patch('speech.time.monotonic', side_effect=lambda: now[0]), patch.object(audio, 'activate', side_effect=slow_failure) as activate, patch('speech.subprocess.Popen') as spawn:
            speech.say('Проверка', wait=True)
        self.assertEqual(activate.call_count, 1)
        spawn.assert_not_called()
        self.assertIn('истекло', speech.error)

    def test_expired_failures_retry_without_a_keypress(self):
        speech, audio = self.make_speech([BUILTIN])
        speech.last = 'Повтор'
        speech.failed[BUILTIN.name] = 0
        speech.retry_at = 0.001
        with patch.object(speech, '_schedule') as speak:
            speech.refresh()
            speak.assert_called_once()
