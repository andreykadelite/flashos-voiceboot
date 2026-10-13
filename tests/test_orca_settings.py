"""Portable native Orca speech settings and explicit, non-destructive UI choices."""
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import orca_settings as prefs
import settings
import store_preferences
import store_storage
import utilities


class NativeOrcaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.initial = prefs.flashos_defaults(settings.DEFAULTS)

    def data(self):
        return json.loads(prefs.profile_path(self.home).read_text(encoding='utf-8'))

    def test_same_selected_synthesizers_as_flashos_and_default_is_elena(self):
        self.assertEqual({key for key, _ in prefs.VOICES}, {key for key, _ in settings.VOICES})
        result = prefs.execute('orca-status', self.home, {'initial': self.initial})
        self.assertEqual(result['values']['voice'], 'elena')
        self.assertEqual(result['module'], 'rhvoice')
        self.assertEqual(result['mode'], 'follow')
        self.assertEqual(self.data()['profiles']['default']['voices']['default']['family']['name'], 'Elena')

    def test_follow_changes_current_flashos_voice_but_custom_stays_independent(self):
        prefs.seed_profile(self.home, self.initial)
        changed = {**self.initial, 'voice': 'irina', 'rate': 73, 'pitch': 31, 'volume': 80}
        prefs.seed_profile(self.home, changed)
        self.assertEqual(prefs.read_status(self.home, changed)['values'], changed)
        manual = {**changed, 'voice': 'espeak'}
        prefs.execute('orca-set', self.home, {'initial': changed, 'values': manual})
        prefs.seed_profile(self.home, self.initial)
        result = prefs.read_status(self.home, self.initial)
        self.assertEqual(result['values'], manual)
        self.assertEqual(result['mode'], 'custom')

    def test_exact_old_seed_migrates_but_one_user_change_prevents_migration(self):
        path = prefs.profile_path(self.home)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(prefs.legacy_profile()))
        prefs.seed_profile(self.home, self.initial)
        self.assertEqual(prefs.read_status(self.home, self.initial)['values']['voice'], 'elena')
        old = prefs.legacy_profile()
        old['profiles']['default']['keyboardLayout'] = 1
        content = json.dumps(old)
        path.write_text(content)
        prefs.seed_profile(self.home, self.initial)
        self.assertEqual(path.read_text(), content)

    def test_real_orca_user_voice_edit_is_not_overwritten_by_follow(self):
        prefs.seed_profile(self.home, self.initial)
        data = self.data()
        data['profiles']['default']['voices']['default']['family']['name'] = 'Anna'
        path = prefs.profile_path(self.home)
        content = json.dumps(data)
        path.write_text(content)
        prefs.seed_profile(self.home, {**self.initial, 'voice': 'yuriy'})
        self.assertEqual(path.read_text(), content)
        self.assertEqual(prefs.read_status(self.home, self.initial)['mode'], 'custom')

    def test_speech_reset_keeps_other_profiles_and_accessibility_preferences(self):
        prefs.seed_profile(self.home, self.initial)
        data = self.data()
        data['general']['custom'] = 'keep'
        data['profiles']['other'] = {'profile': ['Other', 'other'], 'voices': {'default': {'gain': 3}}}
        active = data['profiles']['default']
        active.update({'enableBraille': True, 'customKey': ['keep'], 'enableSpeech': False})
        active['voices']['uppercase'] = {'average-pitch': 8.1, 'family': {'name': 'Elena', 'lang': 'ru'}}
        data['pronunciations']['custom'] = ['x', 'test']
        data['keybindings']['custom'] = ['keep']
        prefs.profile_path(self.home).write_text(json.dumps(data))
        prefs.execute('orca-reset', self.home, {'initial': self.initial})
        saved = self.data()
        self.assertEqual(saved['profiles']['other'], data['profiles']['other'])
        self.assertEqual(saved['pronunciations'], data['pronunciations'])
        self.assertEqual(saved['keybindings'], data['keybindings'])
        self.assertTrue(saved['profiles']['default']['enableBraille'])
        self.assertTrue(saved['profiles']['default']['enableSpeech'])
        self.assertEqual(saved['profiles']['default']['voices']['uppercase']['average-pitch'], 8.1)
        self.assertEqual(prefs.read_status(self.home, self.initial)['mode'], 'follow')

    def test_selected_starting_profile_is_changed_without_resetting_other_profile(self):
        prefs.seed_profile(self.home, self.initial)
        data = self.data()
        before = copy.deepcopy(data['profiles']['default'])
        data['general']['startingProfile'] = ['Work', 'work']
        data['profiles']['work'] = copy.deepcopy(before)
        data['profiles']['work']['profile'] = ['Work', 'work']
        prefs.profile_path(self.home).write_text(json.dumps(data))
        prefs.execute('orca-set', self.home, {'initial': self.initial,
            'values': {**self.initial, 'voice': 'yuriy'}})
        saved = self.data()
        self.assertEqual(saved['profiles']['default'], before)
        self.assertEqual(saved['profiles']['work']['voices']['default']['family']['name'], 'Yuriy')

    def test_malformed_existing_profile_is_preserved_and_edit_is_refused(self):
        path = prefs.profile_path(self.home)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'user profile')
        prefs.seed_profile(self.home, self.initial)
        with self.assertRaisesRegex(RuntimeError, 'повреждён'):
            prefs.execute('orca-reset', self.home, {'initial': self.initial})
        self.assertEqual(path.read_bytes(), b'user profile')

    def test_no_password_or_arbitrary_settings_key_can_enter_speech_request(self):
        bad = [{**self.initial, 'voice': []}, {**self.initial, 'rate': True},
               {**self.initial, 'pitch': -1}, {**self.initial, 'volume': 101},
               {**self.initial, 'command': 'anything'}]
        for values in bad:
            with self.subTest(values=values), self.assertRaises(ValueError):
                prefs.validate_values(values)
        with self.assertRaises(ValueError):
            prefs.execute('orca-set', self.home, {'initial': self.initial, 'values': self.initial, 'path': 'anywhere'})

    @unittest.skipUnless(os.name == 'posix', 'Linux file-system safety')
    def test_symlink_and_fifo_cannot_read_or_write_outside_profile(self):
        outside = self.home / 'outside'
        outside.write_text('keep')
        path = prefs.profile_path(self.home)
        path.parent.mkdir(parents=True)
        path.symlink_to(outside)
        with self.assertRaises(RuntimeError):
            prefs.seed_profile(self.home, self.initial)
        self.assertEqual(outside.read_text(), 'keep')
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(RuntimeError):
            prefs.seed_profile(self.home, self.initial)

    @unittest.skipUnless(os.name == 'posix', 'Linux directory durability')
    def test_post_replace_flush_failure_reports_committed_profile_without_rollback(self):
        prefs.seed_profile(self.home, self.initial)
        original = prefs.os.fsync
        count = [0]
        def fsync(fd):
            count[0] += 1
            if count[0] == 2:
                raise OSError('directory flush failed')
            return original(fd)
        with mock.patch.object(prefs.os, 'fsync', side_effect=fsync), self.assertRaises(prefs.DurabilityError):
            prefs.execute('orca-set', self.home, {'initial': self.initial,
                'values': {**self.initial, 'voice': 'anna'}})
        self.assertEqual(self.data()['profiles']['default']['voices']['default']['family']['name'], 'Anna')
        self.assertEqual(list(prefs.profile_path(self.home).parent.glob('.orca-*.tmp')), [])

    def test_dispatcher_contains_both_modules_and_no_untrusted_values(self):
        config = prefs.dispatcher_config('any\nunsafe')
        self.assertIn('DefaultModule rhvoice', config)
        self.assertIn('AddModule "espeak-ng"', config)
        self.assertNotIn('unsafe', config)

    def test_custom_module_with_same_voice_name_is_not_misidentified_as_rhvoice(self):
        prefs.seed_profile(self.home, self.initial)
        data = self.data()
        data['profiles']['default']['speechServerInfo'] = ['Custom', 'other-module']
        prefs.profile_path(self.home).write_text(json.dumps(data))
        result = prefs.read_status(self.home, self.initial)
        self.assertIsNone(result['values']['voice'])
        self.assertEqual(result['mode'], 'custom')

    def test_preview_waits_for_cold_dispatcher_then_uses_actual_factory_path_and_cleans_up(self):
        prefs.seed_profile(self.home, self.initial)
        runtime = self.home / 'runtime'
        runtime.mkdir()
        socket = runtime / 'speech-dispatcher/speechd.sock'
        pulse_socket = runtime / 'pulse/native'
        elapsed = [0.0]
        original_exists, original_read = Path.exists, Path.read_bytes
        def exists(path):
            return elapsed[0] >= 18 if path in (socket, pulse_socket) else original_exists(path)
        def read_bytes(path):
            if str(path) == '/etc/speech-dispatcher/modules/espeak-ng.conf':
                return b'LogLevel 1\n'
            return original_read(path)
        def sleep(seconds):
            self.assertEqual(seconds, .05)
            elapsed[0] += seconds
        processes = [mock.Mock(), mock.Mock()]
        for process in processes:
            process.poll.return_value = None
        server = mock.Mock()
        server._client.speak.side_effect = lambda text, callback, event_types: callback('end')
        factory = mock.Mock()
        def open_server(info):
            self.assertGreaterEqual(elapsed[0], 18)
            self.assertEqual(info, ['FlashOS preview', 'rhvoice'])
            return server
        factory.SpeechServer.get_speech_server.side_effect = open_server
        speechd = mock.Mock()
        speechd.CallbackType.END, speechd.CallbackType.CANCEL = 'end', 'cancel'
        with mock.patch.object(Path, 'exists', exists), mock.patch.object(Path, 'read_bytes', read_bytes), \
             mock.patch.object(prefs.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             mock.patch.object(prefs.time, 'sleep', side_effect=sleep), \
             mock.patch.object(prefs.subprocess, 'Popen', side_effect=processes), \
             mock.patch.dict(sys.modules, {'speechd': speechd,
                'orca': mock.Mock(speechdispatcherfactory=factory),
                'orca.acss': mock.Mock(ACSS=lambda voice: voice)}), \
             mock.patch.dict(os.environ, {'SPEECHD_ADDRESS': 'original'}):
            result = prefs.preview(self.home, self.initial, runtime)
            self.assertEqual(os.environ['SPEECHD_ADDRESS'], 'original')
        self.assertEqual(result['voice'], 'elena')
        self.assertLess(elapsed[0], 18.1)
        server._apply_acss.assert_called_once()
        server._client.speak.assert_called_once()
        server.shutdown.assert_called_once()
        for process in processes:
            process.terminate.assert_called_once()
            process.wait.assert_called_once()


class SpeechReadinessTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Real inherited SIGPIPE behavior is Linux-only')
    def test_dispatcher_spawn_handles_a_real_closed_pipe_without_sigpipe_termination(self):
        command = ['/bin/sh', '-c', 'read trigger; printf payload; printf after_pipe >&2']
        def run(creator):
            child = creator(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                # Release the writer only after its pipe has no readers.
                child.stdout.close()
                child.stdout = None
                _, error = child.communicate(input=b'go\n', timeout=3)
                return child.returncode, error
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=3)
                for stream in (child.stdin, child.stdout, child.stderr):
                    if stream is not None:
                        stream.close()
        baseline, baseline_error = run(subprocess.Popen)
        self.assertEqual(baseline, -signal.SIGPIPE)
        self.assertNotIn(b'after_pipe', baseline_error)
        restored, error = run(prefs.spawn_speech_dispatcher)
        self.assertEqual(restored, 0)
        self.assertIn(b'after_pipe', error)

    def wait(self, ready_at=None, death_at=None, cancel_at=None, return_at=None, with_stop=True):
        elapsed = [0.0]
        paths = [mock.Mock(), mock.Mock()]
        for path in paths:
            path.exists.side_effect = lambda: ready_at is not None and elapsed[0] >= ready_at
        processes = [mock.Mock(), mock.Mock()]
        processes[0].poll.side_effect = lambda: 1 if death_at is not None and elapsed[0] >= death_at else None
        processes[1].poll.return_value = None
        stop = mock.Mock()
        stop.is_set.side_effect = lambda: cancel_at is not None and elapsed[0] >= cancel_at
        return_file = mock.Mock()
        return_file.exists.side_effect = lambda: return_at is not None and elapsed[0] >= return_at
        def sleep(seconds):
            self.assertEqual(seconds, .05)
            elapsed[0] += seconds
        try:
            with mock.patch.object(prefs.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                 mock.patch.object(prefs.time, 'sleep', side_effect=sleep):
                return prefs.wait_speech_ready(paths, processes, stop if with_stop else None, return_file)
        finally:
            self.elapsed = elapsed[0]

    def test_cold_socket_ready_after_eighteen_seconds_is_accepted(self):
        self.assertTrue(self.wait(ready_at=18))
        self.assertGreaterEqual(self.elapsed, 18)
        self.assertLess(self.elapsed, 18.1)

    def test_child_death_is_detected_without_waiting_for_the_long_deadline(self):
        with self.assertRaisesRegex(RuntimeError, 'речи Orca'):
            self.wait(ready_at=18, death_at=.15)
        self.assertLess(self.elapsed, .3)

    def test_cancellation_wins_even_when_child_exits_on_the_same_poll(self):
        self.assertFalse(self.wait(ready_at=18, death_at=.15, cancel_at=.15))
        self.assertLess(self.elapsed, .3)

    def test_missing_socket_has_a_bounded_readiness_deadline(self):
        with self.assertRaisesRegex(RuntimeError, 'речи Orca'):
            self.wait()
        self.assertGreaterEqual(self.elapsed, 45)
        self.assertLess(self.elapsed, 45.1)

    def test_ready_socket_does_not_hide_a_dead_child(self):
        with self.assertRaisesRegex(RuntimeError, 'речи Orca'):
            self.wait(ready_at=0, death_at=0)
        self.assertEqual(self.elapsed, 0)

    def test_keyboard_return_during_cold_start_cancels_on_the_next_poll(self):
        self.assertFalse(self.wait(ready_at=18, death_at=.15, return_at=.15))
        self.assertLess(self.elapsed, .3)

    def test_keyboard_return_precedes_ready_and_dead_child_without_a_stop_event(self):
        self.assertFalse(self.wait(ready_at=0, death_at=0, return_at=0, with_stop=False))
        self.assertEqual(self.elapsed, 0)


class OrcaUtilityTests(unittest.TestCase):
    def utility(self, choices, confirm=True, demo=False):
        ui = mock.Mock(demo=demo)
        ui.settings.data = dict(settings.DEFAULTS)
        ui.choose.side_effect = choices
        ui.confirm.return_value = confirm
        apps = mock.Mock()
        apps.launcher.running.return_value = False
        apps.ready.return_value = True
        utility = utilities.Utilities(ui, apps)
        utility.orca_task = mock.Mock(return_value={'values': dict(prefs.DEFAULTS), 'module': 'rhvoice',
            'voice_name': 'Elena', 'mode': 'follow', 'speech_enabled': True, 'message': 'saved'})
        return utility, ui, apps

    def test_cancelled_voice_and_confirmation_never_mutate(self):
        utility, ui, _ = self.utility(['voice', None, 'voice', 'anna', None], confirm=False)
        utility.orca()
        self.assertEqual([call.args[0] for call in utility.orca_task.call_args_list], ['orca-status'] * 3)
        ui.confirm.assert_called_once()
        ui.speak_wait.assert_called_once()

    def test_explicit_voice_switch_stores_native_manual_profile(self):
        utility, ui, _ = self.utility(['voice', 'espeak', None])
        utility.orca()
        self.assertIn(mock.call('orca-set', {**prefs.DEFAULTS, 'voice': 'espeak'}, 'custom'),
                      utility.orca_task.call_args_list)
        ui.message.assert_called_once_with('Речь Orca сохранена', 'saved')

    def test_numeric_zero_is_not_confused_with_cancel_and_invalid_range_is_refused(self):
        utility, ui, _ = self.utility(['rate', 'rate', None])
        ui.text_input.side_effect = ['101', '0']
        utility.orca()
        self.assertIn(mock.call('orca-set', {**prefs.DEFAULTS, 'rate': 0}, 'custom'),
                      utility.orca_task.call_args_list)
        self.assertEqual(ui.confirm.call_count, 1)

    def test_preview_does_not_mutate_profile(self):
        utility, ui, _ = self.utility(['preview', None])
        utility.orca()
        self.assertIn(mock.call('orca-preview'), utility.orca_task.call_args_list)
        ui.confirm.assert_not_called()

    def test_active_application_remains_running_without_any_profile_action(self):
        utility, ui, apps = self.utility([])
        apps.launcher.running.return_value = True
        utility.orca()
        utility.orca_task.assert_not_called()
        apps.launcher.close.assert_not_called()

    def test_demo_cannot_write_or_start_real_preview(self):
        utility, ui, apps = self.utility(['voice', 'anna', 'preview', None], demo=True)
        utility.orca()
        utility.orca_task.assert_not_called()
        apps.ready.assert_not_called()
        ui.confirm.assert_not_called()


@unittest.skipUnless(os.name == 'posix', 'Linux shared speech binds')
class SharedSpeechTests(unittest.TestCase):
    def test_old_library_and_orca_profile_bytes_stay_unchanged_and_shared_libraries_are_in_ram(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            host, root = base / 'host', base / 'root'
            (host / 'usr/share/RHVoice').mkdir(parents=True)
            module = host / 'usr/lib/speech-dispatcher-modules/sd_rhvoice'
            module.parent.mkdir(parents=True)
            module.write_bytes(b'module')
            library = host / 'usr/lib/libRHVoice_core.so.10.3.0'
            library.write_bytes(b'new read-only library')
            (host / 'usr/lib/libRHVoice_core.so.10').symlink_to(library.name)
            old = root / 'usr/lib/libRHVoice_core.so.10'
            old.parent.mkdir(parents=True)
            old.write_bytes(b'old user package')
            (root / 'run').mkdir()
            environment = store_storage.StoreEnvironment(base / 'usb', '/dev/test')
            environment.root = root
            job = mock.Mock()
            environment.refresh_speech_runtime(job, host)
            self.assertEqual(old.read_bytes(), b'old user package')
            self.assertEqual(os.readlink(root / 'run/flashos-rhvoice-libs/libRHVoice_core.so.10'), library.name)
            mounts = [call.args[0] for call in job.command.call_args_list]
            self.assertTrue(any(str(root / 'run/flashos-rhvoice-libs' / library.name) in argv for argv in mounts))
            self.assertTrue(all('remount,bind,ro' in mounts[i + 1] for i in range(0, len(mounts), 2)))


if __name__ == '__main__':
    unittest.main()
