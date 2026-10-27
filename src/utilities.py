"""Speaking utilities and fixed Debian application accessibility preferences."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from store_client import result_json
from store_preferences import DEFAULTS
from orca_settings import VOICES as ORCA_VOICES, DEFAULTS as ORCA_DEFAULTS, flashos_defaults
import pointer_settings as pointer

RUNTIME_BASE = Path('/run/flashos-preferences')
LABELS = {'text_scale': 'Размер текста', 'contrast': 'Высокая контрастность',
          'animations': 'Анимация', 'cursor_size': 'Размер указателя'}
OPTIONS = {'text_scale': [('100 процентов', 1.0), ('125 процентов', 1.25),
                         ('150 процентов', 1.5), ('200 процентов', 2.0)],
           'contrast': [('Выключена', False), ('Включена', True)],
           'animations': [('Выключена', False), ('Включена', True)],
           'cursor_size': [('24 пикселя', 24), ('32 пикселя', 32), ('48 пикселей', 48)]}
HELP = ('Все пункты утилиты озвучиваются FlashOS и доступны с клавиатуры. '
        'Стрелки выбирают пункт, Enter открывает его, Escape возвращает назад. '
        'Настройки сохраняются вместе с приложениями на этой флешке и применяются при следующем запуске. '
        'Параметры внешнего вида не меняют голос. Для речи есть отдельный пункт «Настройки экранного диктора Orca». '
        'Высокая контрастность и размер текста поддерживаются приложениями GTK; другие программы '
        'могут использовать собственные настройки. Перед изменениями закройте приложения и сохраните документы.')


class PreferencesClient:
    def __init__(self, environment):
        self.environment = environment

    def call(self, job, action, request=None):
        if action not in ('status', 'set', 'reset', 'enable', 'orca-status', 'orca-set', 'orca-reset', 'orca-preview'):
            raise ValueError('Неизвестное действие утилиты настроек.')
        job.update('Открываю настройки приложений на флешке.')
        self.environment.open(job)
        with self.environment.lock():
            helper = self.environment.root / 'opt/flashos-store/preferences.py'
            if not helper.is_file() or helper.is_symlink():
                raise RuntimeError('Компонент настроек отсутствует в среде приложений.')
            RUNTIME_BASE.mkdir(mode=0o700, exist_ok=True)
            if RUNTIME_BASE.is_symlink() or RUNTIME_BASE.stat().st_uid != 0:
                raise RuntimeError('Служебная папка настроек занята другим объектом.')
            runtime = RUNTIME_BASE / uuid.uuid4().hex
            runtime.mkdir(mode=0o700)
            os.chown(runtime, 1000, 1000)
            unit = 'flashos-store-preferences-' + uuid.uuid4().hex + '.service'
            properties = ['RootDirectory=' + str(self.environment.root), 'User=1000', 'Group=1000',
                          'WorkingDirectory=/home/flashos-app', 'PrivateDevices=yes', 'PrivatePIDs=yes',
                          'PrivateIPC=yes', 'NoNewPrivileges=yes', 'RestrictNamespaces=yes',
                          'ProtectKernelTunables=yes', 'ProtectKernelModules=yes', 'ProtectControlGroups=yes',
                          'CapabilityBoundingSet=', 'AmbientCapabilities=', 'KillMode=control-group',
                          'TimeoutStopSec=5s', 'RuntimeMaxSec=90s', 'BindsTo=flashos.service',
                          'After=flashos.service', 'BindPaths=' + str(runtime) + ':/run/user/1000']
            if action == 'orca-preview':
                properties.append('SupplementaryGroups=pipewire')
            command = ['systemd-run', '--quiet', '--wait', '--pipe', '--collect', '--service-type=exec',
                       '--expand-environment=no', '--unit=' + unit]
            command += ['--property=' + item for item in properties]
            command += ['--setenv=HOME=/home/flashos-app', '--setenv=USER=flashos-app',
                        '--setenv=LOGNAME=flashos-app', '--setenv=LANG=C.UTF-8',
                        '--setenv=XDG_RUNTIME_DIR=/run/user/1000',
                        '--setenv=XDG_CONFIG_HOME=/home/flashos-app/.config', '--setenv=GSETTINGS_BACKEND=dconf',
                        '--setenv=PIPEWIRE_RUNTIME_DIR=/run/pipewire', '--setenv=PIPEWIRE_REMOTE=pipewire-0',
                        '--setenv=PULSE_SERVER=unix:/run/user/1000/pulse/native',
                        '--', '/usr/bin/dbus-run-session', '--', '/usr/bin/python3',
                        '/opt/flashos-store/preferences.py', action]
            mutation = action not in ('status', 'orca-status', 'orca-preview')
            try:
                if request is not None:
                    path = runtime / 'request.json'
                    with path.open('x', encoding='utf-8') as stream:
                        os.chmod(path, 0o600)
                        json.dump(request, stream, ensure_ascii=False)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.chown(path, 1000, 1000)
                    command += ['--request', '/run/user/1000/request.json']
                if mutation:
                    job.check()
                    job.cancellation_deferred = True
                    job.update('Сохраняю настройки. Дождитесь проверки записи.')
                elif action == 'orca-preview':
                    job.update('Пробная речь Orca. Escape останавливает пример и возвращает в настройки.')
                code, output = job.command(command, timeout=120, accepted=None, output_limit=32768,
                    cleanup=mutation, resource='applications')
                data = result_json(output)
                if code:
                    raise RuntimeError('Утилита настроек завершилась с ошибкой (код ' + str(code) +
                                       '). Повторите чтение настроек, чтобы проверить сохранённые значения.')
                if mutation:
                    job.cancel_event.clear()
                job.payload = data
                job.result = data.get('message', 'Настройки проверены.')
                return data
            finally:
                if mutation:
                    job.cancellation_deferred = False
                # A cancelled systemd-run client must not leave its private D-Bus writing the profile.
                try:
                    stopped = subprocess.run(['systemctl', 'stop', unit], stdin=subprocess.DEVNULL,
                        capture_output=True, text=True, timeout=15, check=False)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError('Утилита ещё завершает работу. Повторите чтение настроек позже.') from error
                if stopped.returncode and not any(text in (stopped.stderr or '').lower()
                        for text in ('not found', 'could not be found', 'not loaded')):
                    raise RuntimeError('Не удалось подтвердить завершение утилиты настроек.')
                if runtime.parent != RUNTIME_BASE or runtime.is_symlink():
                    raise RuntimeError('Недопустимая служебная папка настроек.')
                shutil.rmtree(runtime)


class Utilities:
    def __init__(self, ui, applications):
        self.ui, self.applications = ui, applications
        self.client = PreferencesClient(applications.environment)

    def main(self):
        while True:
            selection = self.ui.choose('Утилиты',
                [('Настройки доступности приложений Debian — GSettings', 'preferences'),
                 ('Настройки экранного диктора Orca — голос и речь', 'orca'),
                 ('Доступность — курсор, мышь и тачпад', 'pointer'),
                 ('Настройки терминала с озвучкой', 'terminal'),
                 ('Как пользоваться утилитой настроек', 'help')], prompt=True)
            if selection is None:
                return
            if selection == 'help':
                self.ui.message('Утилита настроек', HELP)
            elif selection == 'preferences':
                self.preferences()
            elif selection == 'orca':
                self.orca()
            elif selection == 'pointer':
                self.pointer()
            elif selection == 'terminal':
                from terminal import launch
                launch(self.ui, self.ui.state_root, preferences=True)

    def pointer(self):
        self.ui.speak_wait('Курсор, мышь и тачпад. ' + pointer.HELP, title='Указатель и устройства ввода')
        while True:
            values = pointer.validate(self.ui.settings.data.get('pointer'))
            items = [(label + ': ' + pointer.value_label(key, values[key]), key)
                     for key, label in pointer.LABELS.items()]
            items += [('Устройства и применение настроек', 'status'),
                      ('Прочитать все настройки', 'read'), ('Стандартные настройки указателя', 'reset'),
                      ('Справка о мыши, тачпаде и сохранении', 'help')]
            selection = self.ui.choose('Курсор, мышь и тачпад', items, help_text=pointer.HELP, prompt=True)
            if selection is None:
                return
            if selection == 'help':
                self.ui.message('Указатель и устройства ввода', pointer.HELP)
                continue
            if selection == 'read':
                self.ui.view('Настройки указателя', [label + ': ' + pointer.value_label(key, values[key])
                             for key, label in pointer.LABELS.items()])
                continue
            if selection == 'status':
                screen = self.ui.screen
                lines = screen.pointer_status() if getattr(screen, 'graphical', False) else [
                    'Изменения сохраняются; указатель и настройки устройств применятся при графическом запуске.']
                self.ui.view('Устройства и применение', lines + [pointer.HELP])
                continue
            if selection == 'reset':
                if not self.ui.confirm('Вернуть стандартные настройки курсора, мыши и тачпада?'):
                    continue
                updated = dict(pointer.DEFAULTS)
                message = 'Стандартные настройки указателя восстановлены'
            elif selection in pointer.LABELS:
                value = self.ui.choose(pointer.LABELS[selection], [('Отмена', None)] + pointer.OPTIONS[selection], prompt=True)
                if value is None:
                    continue
                updated = {**values, selection: value}
                message = pointer.LABELS[selection] + ': ' + pointer.value_label(selection, value)
            else:
                continue
            self.ui.change_settings({'pointer': updated}, message)
            if getattr(self.ui.screen, 'graphical', False):
                self.ui.screen.configure_pointer(updated)

    def task(self, action, request=None):
        result = self.ui.run_job('Настройки доступности приложений',
            lambda job: self.client.call(job, action, request), review=False, save=action != 'status')
        return None if result.failed or result.cancelled or result.incomplete else result.payload

    @staticmethod
    def value_label(key, value):
        if key == 'text_scale':
            return str(round(value * 100)) + ' процентов'
        if key == 'cursor_size':
            return str(value) + ' пикселей'
        return 'включена' if value else 'выключена'

    def preferences(self):
        if not self.ui.demo:
            if self.applications.launcher.running():
                self.ui.message('Сначала закройте приложение',
                    'Сохраните документы и закройте приложение перед изменением его настроек. '
                    'Alt F4 закрывает окно. Control Alt Escape возвращает в FlashOS.')
                return
            if not self.applications.ready():
                return
        self.ui.speak_wait('Настройки доступности приложений. ' + HELP,
                           title='Настройки доступности приложений')
        while True:
            data = {'values': {**DEFAULTS, 'toolkit_accessibility': True, 'screen_reader_enabled': True}} \
                if self.ui.demo else self.task('status')
            if data is None:
                recovery = self.ui.choose('Не удалось прочитать настройки',
                    [('Отмена', None), ('Повторить чтение', 'retry'),
                     ('Восстановить стандартные параметры этой утилиты', 'reset')], prompt=True)
                if recovery is None:
                    return
                if recovery == 'reset' and self.ui.confirm(
                        'Восстановить только размер текста, контрастность, анимацию и указатель? '
                        'Профили Orca, документы и прочие настройки сохранятся.'):
                    result = self.task('reset')
                    if result is not None:
                        self.ui.message('Настройки восстановлены', result['message'])
                continue
            values = data['values']
            reader = values['toolkit_accessibility'] and values['screen_reader_enabled']
            items = [(LABELS[key] + ': ' + self.value_label(key, values[key]), key) for key in LABELS]
            items += [('Доступность для экранного диктора: ' + ('включена' if reader else 'требует восстановления'), 'reader'),
                      ('Прочитать все настройки', 'status'), ('Сбросить параметры этой утилиты', 'reset'),
                      ('О доступности и сохранении настроек', 'help')]
            selection = self.ui.choose('Настройки приложений Debian', items, prompt=True)
            if selection is None:
                return
            if selection == 'help':
                self.ui.message('Доступность настроек', HELP)
                continue
            if selection == 'status':
                self.ui.view('Сохранённые настройки приложений',
                    [LABELS[key] + ': ' + self.value_label(key, values[key]) for key in LABELS] +
                    ['Доступность интерфейса: ' + ('включена' if values['toolkit_accessibility'] else 'выключена'),
                     'Экранный диктор: ' + ('включён' if values['screen_reader_enabled'] else 'выключен'), HELP])
                continue
            if selection == 'reader' and reader:
                self.ui.message('Экранный диктор',
                    'Доступность включена. Orca работает при запуске внешнего приложения. '
                    'Встроенная озвучка FlashOS продолжает работать независимо от этих настроек.')
                continue
            action, request = 'enable', None
            if selection == 'reset':
                question = ('Вернуть стандартный размер текста, контрастность, анимацию и размер указателя? '
                            'Настройки Orca, приложения, документы и остальные параметры сохранятся.')
                action = 'reset'
            elif selection in LABELS:
                value = self.ui.choose(LABELS[selection], [('Отмена', None)] + OPTIONS[selection], prompt=True)
                if value is None:
                    continue
                question = 'Установить ' + LABELS[selection].lower() + ': ' + self.value_label(selection, value) + '?'
                action, request = 'set', {'key': selection, 'value': value}
            else:
                question = 'Восстановить включённую доступность и экранный диктор для приложений?'
            if self.ui.demo:
                self.ui.message('Демонстрация', 'Чтение и навигация доступны. Изменение настроек выполняется после загрузки с флешки.')
                continue
            if self.ui.confirm(question):
                result = self.task(action, request)
                if result is not None:
                    self.ui.message('Настройки сохранены', result['message'])

    def orca_task(self, action, values=None, mode=None):
        data = getattr(getattr(self.ui, 'settings', None), 'data', None)
        request = {'initial': flashos_defaults(data)}
        if values is not None:
            request['values'] = values
        if mode is not None:
            request['mode'] = mode
        result = self.ui.run_job('Настройки экранного диктора Orca',
            lambda job: self.client.call(job, action, request), review=False,
            save=action not in ('orca-status', 'orca-preview'))
        return None if result.failed or result.cancelled or result.incomplete else result.payload

    def orca(self):
        if not self.ui.demo:
            if self.applications.launcher.running():
                self.ui.message('Сначала закройте приложение',
                    'Сохраните документы и закройте приложение перед изменением речи Orca. '
                    'Alt F4 закрывает окно. Control Alt Escape возвращает в FlashOS.')
                return
            if not self.applications.ready():
                return
        help_text = ('Orca — экранный диктор приложений из магазина. По умолчанию его речь следует '
            'настройкам FlashOS при каждом запуске приложения. Можно выбрать отдельный голос, скорость, '
            'высоту и громкость. Изменения сохраняются на этой флешке и применяются при следующем запуске. '
            'Шкалы речи Orca от 0 до 100 отличаются от шкал FlashOS: скорость и высота 50 — середина шкалы. '
            'Эта утилита не отключает экранный диктор. Произвольные настройки Orca, произношения, '
            'брайлевские настройки и горячие клавиши сохраняются. Caps Lock плюс пробел в приложении '
            'открывает собственное окно настройки Orca. F7 останавливает озвучку FlashOS. '
            'Escape останавливает пробный пример. В приложении Control прерывает текущую речь Orca.')
        self.ui.speak_wait(help_text, title='Настройки экранного диктора Orca')
        while True:
            data = {'values': flashos_defaults(getattr(getattr(self.ui, 'settings', None), 'data', None)),
                    'mode': 'follow', 'module': 'rhvoice', 'voice_name': 'Elena', 'speech_enabled': True} \
                   if self.ui.demo else self.orca_task('orca-status')
            if data is None:
                return
            values = data['values']
            voice_label = dict(ORCA_VOICES).get(values['voice'], data.get('voice_name') or 'Собственный голос Orca')
            labels = {'rate': 'Скорость', 'pitch': 'Высота', 'volume': 'Громкость'}
            items = [('Режим: ' + ('как в FlashOS' if data['mode'] == 'follow' else 'отдельные настройки Orca'), 'mode'),
                     ('Голос и синтезатор: ' + voice_label, 'voice')]
            items += [(label + ': ' + (str(values[key]) + ' из 100' if values[key] is not None
                       else 'собственный параметр Orca'), key) for key, label in labels.items()]
            items += [('Прочитать настройки', 'status'), ('Вернуть речь как в FlashOS', 'reset'),
                      ('Прослушать пример выбранным голосом Orca', 'preview'),
                      ('Как работают настройки Orca', 'help')]
            choice = self.ui.choose('Речь экранного диктора Orca', items, prompt=True)
            if choice is None:
                return
            if choice == 'help':
                self.ui.message('Речь Orca', help_text)
                continue
            if choice == 'status':
                self.ui.view('Сохранённая речь Orca', [item[0] for item in items[:5]] +
                    ['Озвучка: ' + ('включена' if data['speech_enabled'] else 'выключена в собственном профиле Orca'), help_text])
                continue
            if choice == 'preview':
                if self.ui.demo:
                    self.ui.message('Демонстрация', 'Пробная речь Orca доступна после загрузки с флешки.')
                else:
                    self.orca_task('orca-preview')
                continue
            mode, action = 'custom', 'orca-set'
            draft = {key: value if value is not None else ORCA_DEFAULTS[key] for key, value in values.items()}
            if choice == 'mode':
                mode = self.ui.choose('Режим речи Orca', [('Отмена', None),
                    ('Как в FlashOS при каждом запуске приложения', 'follow'),
                    ('Отдельные настройки Orca', 'custom')], prompt=True)
                if mode is None:
                    continue
                if mode == 'custom' and any(value is None for value in values.values()):
                    self.ui.message('Собственный профиль Orca',
                        'Собственные параметры сохранены. Для их изменения используйте настройки Orca в приложении, '
                        'либо явно выберите один из голосов этой утилиты.')
                    continue
            elif choice == 'reset':
                mode, action = 'follow', 'orca-reset'
            elif choice == 'voice':
                value = self.ui.choose('Голос Orca', [('Отмена', None)] +
                    [(label, voice) for voice, label in ORCA_VOICES], prompt=True)
                if value is None:
                    continue
                draft['voice'] = value
            elif choice in labels:
                if values['voice'] is None:
                    self.ui.message('Сначала выберите голос',
                        'Собственный голос Orca сохранён без изменений. Выберите голос этой утилиты, '
                        'либо измените скорость, высоту и громкость в собственном окне Orca.')
                    continue
                text = self.ui.text_input(labels[choice] + ' Orca, от 0 до 100. Сейчас ' + str(draft[choice]), limit=3)
                if text is None:
                    continue
                try:
                    value = int(text)
                    if not text.isascii() or not text.isdecimal() or not 0 <= value <= 100:
                        raise ValueError()
                except ValueError:
                    self.ui.message('Недопустимое значение', 'Введите целое число от 0 до 100.')
                    continue
                draft[choice] = value
            if self.ui.demo:
                self.ui.message('Демонстрация', 'Изменение речи Orca доступно после загрузки с флешки.')
                continue
            question = ('Следовать голосу и параметрам речи FlashOS при каждом запуске приложения?' if mode == 'follow'
                        else 'Сохранить отдельную речь Orca: ' + dict(ORCA_VOICES)[draft['voice']] +
                        ', скорость ' + str(draft['rate']) + ', высота ' + str(draft['pitch']) +
                        ', громкость ' + str(draft['volume']) + ' из 100?')
            if mode == 'custom' and any(value is None for value in values.values()):
                question += ' Нераспознаваемые параметры речи заменятся указанными значениями.'
            if self.ui.confirm(question):
                result = self.orca_task(action, draft, mode)
                if result is not None:
                    self.ui.message('Речь Orca сохранена', result['message'])
