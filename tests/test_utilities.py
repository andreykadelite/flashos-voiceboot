"""Preference validation, rollback and spoken utility boundaries."""
import configparser
import curses
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import store_preferences as prefs
import utilities
from interface import UI
from orca_settings import DEFAULTS as ORCA_DEFAULTS


class Backend:
    def __init__(self):
        self.values = {prefs.KEYS[name]: value for name, value in prefs.DEFAULTS.items()}
        self.values.update({key: True for key in prefs.REQUIRED})
        self.values[(prefs.INTERFACE, 'gtk-theme')] = 'UserTheme'
        self.values[('custom', 'unrelated')] = 'preserved'
        self.failure = False

    def get(self, schema, key):
        return self.values[schema, key]

    def change(self, values):
        if self.failure:
            self.failure = False
            raise RuntimeError('profile commit failed')
        self.values.update(values)


class PreferenceTests(unittest.TestCase):
    def test_rejects_unlisted_values_types_and_arbitrary_schema_requests(self):
        invalid = [{'key':'text_scale','value':True}, {'key':'contrast','value':1},
                   {'key':'cursor_size','value':24.0}, {'key':'text_scale','value':10},
                   {'key':'screen-reader-enabled','value':False}, {'key':[],'value':1},
                   {'key':'contrast','value':True,'schema':'anything'}, None]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                prefs.validate_request(request)

    def test_text_scale_only_changes_its_key_and_required_accessibility(self):
        backend = Backend()
        backend.values[prefs.REQUIRED[0]] = False
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            custom = home / '.config/gtk-3.0/settings.ini'
            custom.parent.mkdir(parents=True)
            custom.write_text('[Settings]\ngtk-theme=UserTheme\n[Other]\nsetting=keep\n')
            result = prefs.execute('set', backend, home, {'key':'text_scale','value':1.5})
            self.assertEqual(result['values']['text_scale'], 1.5)
            self.assertTrue(result['values']['toolkit_accessibility'])
            self.assertEqual(custom.read_text(), '[Settings]\ngtk-theme=UserTheme\n[Other]\nsetting=keep\n')
            self.assertEqual(backend.values[prefs.INTERFACE, 'gtk-theme'], 'UserTheme')
            self.assertEqual(backend.values['custom', 'unrelated'], 'preserved')

    def test_contrast_applies_to_both_gtk_profiles_preserving_other_settings(self):
        backend = Backend()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for version in ('gtk-3.0','gtk-4.0'):
                path = home / '.config' / version / 'settings.ini'
                path.parent.mkdir(parents=True)
                path.write_text('[Settings]\ngtk-font-name=User Font 14\ngtk-enable-animations=false\n'
                                '[Application]\ncustom=keep\n')
            prefs.execute('set', backend, home, {'key':'contrast','value':True})
            for version in ('gtk-3.0','gtk-4.0'):
                data = configparser.RawConfigParser()
                data.read(home / '.config' / version / 'settings.ini')
                self.assertEqual(data['Settings']['gtk-theme'], 'HighContrast')
                self.assertEqual(data['Settings']['gtk-font-name'], 'User Font 14')
                self.assertEqual(data['Settings']['gtk-enable-animations'], 'false')
                self.assertEqual(data['Application']['custom'], 'keep')
            self.assertTrue(backend.get(*prefs.KEYS['contrast']))

    def test_corrupt_second_profile_is_detected_before_any_write(self):
        backend = Backend()
        original = dict(backend.values)
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for version, content in [('gtk-3.0','[Settings]\ncustom=keep\n'),('gtk-4.0','invalid-data')]:
                path = home / '.config' / version / 'settings.ini'
                path.parent.mkdir(parents=True)
                path.write_text(content)
            first = home / '.config/gtk-3.0/settings.ini'
            before = first.read_bytes()
            with self.assertRaisesRegex(RuntimeError, 'повреждён'):
                prefs.execute('set', backend, home, {'key':'contrast','value':True})
            self.assertEqual(first.read_bytes(), before)
            self.assertEqual(backend.values, original)

    @unittest.skipUnless(os.name == 'posix', 'Real symlinks are checked in the Linux suite')
    def test_symlink_profile_cannot_write_outside_application_home(self):
        backend = Backend()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            home.mkdir()
            outside = Path(directory) / 'outside'
            outside.mkdir()
            (home / '.config').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'ссылкой'):
                prefs.execute('set', backend, home, {'key':'contrast','value':True})
            self.assertEqual(list(outside.iterdir()), [])

    def test_failed_backend_commit_restores_profile_bytes_and_all_settings(self):
        backend = Backend()
        original = dict(backend.values)
        backend.failure = True
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            first = home / '.config/gtk-3.0/settings.ini'
            first.parent.mkdir(parents=True)
            first.write_bytes(b'[Settings]\ncustom=keep\n')
            with self.assertRaisesRegex(RuntimeError, 'commit failed'):
                prefs.execute('set', backend, home, {'key':'contrast','value':True})
            self.assertEqual(first.read_bytes(), b'[Settings]\ncustom=keep\n')
            self.assertFalse((home / '.config/gtk-4.0/settings.ini').exists())
            self.assertEqual(backend.values, original)

    def test_reset_recovers_out_of_range_values_without_touching_orca_or_documents(self):
        backend = Backend()
        backend.values[prefs.KEYS['text_scale']] = 20.0
        backend.values[prefs.REQUIRED[1]] = False
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            orca = home / '.local/share/orca/user-settings.conf'
            orca.parent.mkdir(parents=True)
            orca.write_text('user-reader-profile')
            document = home / 'document.txt'
            document.write_text('unsaved-work-preserved')
            result = prefs.execute('reset', backend, home)
            self.assertEqual(result['values']['text_scale'], 1.0)
            self.assertTrue(result['values']['screen_reader_enabled'])
            self.assertEqual(orca.read_text(), 'user-reader-profile')
            self.assertEqual(document.read_text(), 'unsaved-work-preserved')
            self.assertEqual(backend.values['custom','unrelated'], 'preserved')

    def test_enable_reader_keeps_custom_theme_and_visual_preferences(self):
        backend = Backend()
        backend.values[prefs.REQUIRED[1]] = False
        prefs.execute('enable', backend)
        self.assertTrue(backend.get(*prefs.REQUIRED[1]))
        self.assertEqual(backend.get(prefs.INTERFACE, 'gtk-theme'), 'UserTheme')


class UtilityUITests(unittest.TestCase):
    def utility(self, selections, confirm=True, demo=False):
        ui = mock.Mock(demo=demo)
        ui.choose.side_effect = selections
        ui.confirm.return_value = confirm
        apps = mock.Mock()
        apps.launcher.running.return_value = False
        apps.ready.return_value = True
        utility = utilities.Utilities(ui, apps)
        utility.task = mock.Mock(return_value={'values':{**prefs.DEFAULTS,
                          'toolkit_accessibility':True,'screen_reader_enabled':True}, 'message':'saved'})
        return utility, ui, apps

    def test_real_voice_menu_displays_labels_and_saves_identifiers_for_every_voice(self):
        for index, (voice, label) in enumerate(utilities.ORCA_VOICES):
            with self.subTest(voice=voice):
                screen = mock.Mock(graphical=True)
                screen.getch.side_effect = ([curses.KEY_DOWN, 10] +
                    [curses.KEY_DOWN] * (index + 1) + [10] +
                    [curses.KEY_DOWN, 10, 27, 27])
                done = threading.Event()
                done.set()
                speech = mock.Mock(error='', volume=75)
                speech.say.return_value = done
                ui = UI(screen, speech, mock.Mock(data={}), '/tmp')
                apps = mock.Mock()
                apps.launcher.running.return_value = False
                apps.ready.return_value = True
                utility = utilities.Utilities(ui, apps)
                utility.orca_task = mock.Mock(return_value={
                    'values': dict(ORCA_DEFAULTS), 'mode': 'follow',
                    'module': 'rhvoice', 'voice_name': 'Elena',
                    'speech_enabled': True, 'message': 'saved'})

                utility.orca()

                utility.orca_task.assert_any_call(
                    'orca-set', {**ORCA_DEFAULTS, 'voice': voice}, 'custom')
                voice_frames = [call.args[1] for call in screen.render.call_args_list
                                if call.args[0] == 'Голос Orca']
                self.assertTrue(voice_frames)
                self.assertEqual(voice_frames[-1][index + 1], str(index + 2) + '. ' + label)
                speech.say.assert_any_call(label)

    def test_cancelled_setting_never_starts_mutation(self):
        utility, ui, _ = self.utility(['text_scale',1.5,None], confirm=False)
        utility.preferences()
        self.assertEqual([call.args[0] for call in utility.task.call_args_list], ['status','status'])
        ui.confirm.assert_called_once()

    def test_confirmed_setting_has_bounded_request_and_success_is_announced(self):
        utility, ui, _ = self.utility(['contrast',True,None])
        utility.preferences()
        self.assertIn(mock.call('set',{'key':'contrast','value':True}), utility.task.call_args_list)
        ui.message.assert_called_with('Настройки сохранены','saved')

    def test_active_application_is_preserved_and_no_profile_command_runs(self):
        utility, ui, apps = self.utility([])
        apps.launcher.running.return_value = True
        utility.preferences()
        utility.task.assert_not_called()
        apps.launcher.close.assert_not_called()
        ui.message.assert_called_once()

    def test_demo_navigation_never_initializes_storage_or_changes_profile(self):
        utility, ui, apps = self.utility(['animations',False,None], demo=True)
        utility.preferences()
        apps.ready.assert_not_called()
        utility.task.assert_not_called()
        ui.confirm.assert_not_called()
        self.assertEqual(ui.message.call_args.args[0], 'Демонстрация')

    def test_unreadable_profile_offers_spoken_confirmed_reset(self):
        utility, ui, _ = self.utility(['reset',None])
        complete = {'values':{**prefs.DEFAULTS,'toolkit_accessibility':True,
                    'screen_reader_enabled':True},'message':'recovered'}
        utility.task.side_effect = [None,complete,complete]
        utility.preferences()
        self.assertEqual([call.args[0] for call in utility.task.call_args_list], ['status','reset','status'])
        first = ui.choose.call_args_list[0]
        self.assertEqual(first.args[1][0], ('Отмена',None))
        ui.confirm.assert_called_once()
        ui.message.assert_called_once_with('Настройки восстановлены','recovered')

    @unittest.skipUnless(os.name == 'posix', 'The speaking menu uses native Linux curses')
    def test_main_menu_contains_and_dispatches_utilities(self):
        import menu
        ui = mock.Mock()
        ui.choose.side_effect = ['utilities', StopIteration]
        speech = mock.Mock(outputs=[], error='')
        settings = mock.Mock(error='')
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch('menu.UI',return_value=ui), mock.patch('menu.discover',return_value=[]), \
             mock.patch('store.ApplicationStore') as app_store, \
             mock.patch('utilities.Utilities') as constructor, mock.patch('builtins.open', mock.mock_open()):
            with self.assertRaises(StopIteration):
                menu.run_menu(mock.Mock(),Path(directory),speech,True,Path(directory),settings)
        choices = [item[:2] for item in ui.choose.call_args_list[0].args[1]]
        self.assertIn(('Утилиты','utilities'), choices)
        self.assertIn(('Инструменты','tools'),choices)
        constructor.return_value.main.assert_called_once()
        app_store.return_value.close.assert_not_called()


class ClientTests(unittest.TestCase):
    def test_profile_runs_with_nonroot_identity_private_dbus_and_no_x_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'root'
            helper = root / 'opt/flashos-store/preferences.py'
            helper.parent.mkdir(parents=True)
            helper.write_text('test')
            environment = mock.Mock(root=root)
            environment.lock.return_value = mock.MagicMock()
            job = mock.Mock()
            job.command.return_value = (0,json.dumps({'format':1,'ok':True,'data':{'message':'saved'}}))
            with mock.patch.object(utilities,'RUNTIME_BASE',base/'runtime'), \
                 mock.patch('utilities.os.chown', create=True), \
                 mock.patch('utilities.subprocess.run',return_value=mock.Mock(returncode=0)):
                if os.name != 'posix':
                    # Windows reports owner 0 for pathlib.stat, matching runtime root ownership.
                    pass
                utilities.PreferencesClient(environment).call(job,'set',{'key':'contrast','value':True})
            command = job.command.call_args.args[0]
            self.assertIn('--property=User=1000',command)
            self.assertIn('--property=NoNewPrivileges=yes',command)
            self.assertIn('--property=CapabilityBoundingSet=',command)
            self.assertIn('/usr/bin/dbus-run-session',command)
            self.assertNotIn('xhost',command)
            self.assertIn('/opt/flashos-store/preferences.py',command)
            self.assertTrue(job.command.call_args.kwargs['cleanup'])
            self.assertFalse(job.cancellation_deferred)
            self.assertEqual(list((base/'runtime').iterdir()),[])

    def test_collected_completed_unit_is_accepted_and_runtime_is_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'root'
            helper = root / 'opt/flashos-store/preferences.py'
            helper.parent.mkdir(parents=True)
            helper.write_text('test')
            environment = mock.Mock(root=root)
            environment.lock.return_value = mock.MagicMock()
            job = mock.Mock()
            job.command.return_value = (0,json.dumps({'format':1,'ok':True,'data':{'message':'saved'}}))
            completed = mock.Mock(returncode=5,stderr='Unit flashos-store-preferences.service not loaded.')
            with mock.patch.object(utilities,'RUNTIME_BASE',base/'runtime'), \
                 mock.patch('utilities.os.chown',create=True), \
                 mock.patch('utilities.subprocess.run',return_value=completed) as stop:
                result = utilities.PreferencesClient(environment).call(job,'status')
            self.assertEqual(result['message'],'saved')
            self.assertTrue(stop.call_args.kwargs['text'])
            self.assertEqual(list((base/'runtime').iterdir()),[])


if __name__ == '__main__':
    unittest.main()
