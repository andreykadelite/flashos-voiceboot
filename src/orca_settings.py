"""Native Orca speech profiles, shared by the USB preferences helper and session."""
import copy
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import threading
import time
import uuid

VOICES = [('elena', 'Елена, RHVoice'), ('irina', 'Ирина, RHVoice'),
          ('anna', 'Анна, RHVoice'), ('yuriy', 'Юрий, RHVoice'),
          ('aleksandr', 'Александр, RHVoice'), ('espeak', 'Русский, eSpeak NG')]
NAMES = {'elena': 'Elena', 'irina': 'Irina', 'anna': 'Anna', 'yuriy': 'Yuriy',
         'aleksandr': 'Aleksandr', 'espeak': 'Russian'}
DEFAULTS = {'voice': 'elena', 'rate': 50, 'pitch': 50, 'volume': 75}
LIMIT = 262144


class DurabilityError(OSError):
    """The new native profile is committed, but media synchronization failed."""


def validate_values(values):
    if not isinstance(values, dict) or set(values) != set(DEFAULTS):
        raise ValueError('Недопустимые настройки речи Orca.')
    if not isinstance(values['voice'], str) or values['voice'] not in NAMES:
        raise ValueError('Этот голос Orca недоступен.')
    if any(type(values[key]) is not int or not 0 <= values[key] <= 100
           for key in ('rate', 'pitch', 'volume')):
        raise ValueError('Скорость, высота и громкость Orca должны быть от 0 до 100.')
    return dict(values)


def flashos_defaults(values=None):
    """Translate the portable FlashOS voice to Orca's own 0..100 scales."""
    values = values if isinstance(values, dict) else {}
    result = dict(DEFAULTS)
    if isinstance(values.get('voice'), str) and values['voice'] in NAMES:
        result['voice'] = values['voice']
    if type(values.get('rate')) is int:
        result['rate'] = max(0, min(100, round(values['rate'] / 2)))
    if type(values.get('pitch')) is int:
        result['pitch'] = max(0, min(100, round(values['pitch'] / 2)))
    if type(values.get('volume')) is int:
        result['volume'] = max(0, min(100, values['volume']))
    return result


def speech_properties(values):
    values = validate_values(values)
    module = 'espeak-ng' if values['voice'] == 'espeak' else 'rhvoice'
    return {'enableSpeech': True, 'speechServerFactory': 'speechdispatcherfactory',
            'speechServerInfo': ['eSpeak NG' if module == 'espeak-ng' else 'RHVoice', module],
            'voices': {'default': {'family': {'name': NAMES[values['voice']], 'lang': 'ru', 'dialect': ''},
                                  'rate': round(values['rate'] * 0.98, 6),
                                  'average-pitch': round(values['pitch'] * 0.09, 6),
                                  'gain': round(values['volume'] * 0.09, 6)}}}


def new_profile(values):
    result = {'general': {'startingProfile': ['Default', 'default']},
            'profiles': {'default': {'profile': ['Default', 'default'], 'keyboardLayout': 2,
                                    'orcaModifierKeys': ['Caps_Lock', 'Shift_Lock'],
                                    **speech_properties(values)}},
            'pronunciations': {}, 'keybindings': {}}
    result['flashosSpeech'] = {'format': 1, 'mode': 'follow', 'last': snapshot(result)}
    return result


def legacy_profile():
    """Exact old FlashOS seed; user changes never qualify for migration."""
    result = new_profile(DEFAULTS)
    result.pop('flashosSpeech')
    result['profiles']['default'].update({'speechServerInfo': ['Default Synthesizer', 'espeak-ng'],
        'voices': {'default': {'family': {'name': 'Russian', 'lang': 'ru', 'dialect': ''},
                              'rate': 50.0, 'average-pitch': 5.0, 'gain': 10.0}}})
    return result


def profile_path(home):
    home = Path(home)
    current = home
    if current.is_symlink():
        raise RuntimeError('Домашняя папка Orca не должна быть ссылкой.')
    for part in ('.local', 'share', 'orca'):
        current /= part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise RuntimeError('Недопустимая папка настроек Orca.')
    return current / 'user-settings.conf'


def read_bytes(path):
    if Path(path).is_symlink():
        raise RuntimeError('Файл настроек Orca не должен быть ссылкой.')
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise RuntimeError('Недопустимый файл настроек Orca.')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            content = stream.read(LIMIT + 1)
        if len(content) > LIMIT:
            raise RuntimeError('Файл настроек Orca слишком большой.')
        return content
    finally:
        os.close(descriptor)


def _pairs(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError('Повторяющийся параметр Orca.')
        data[key] = value
    return data


def decode(content):
    try:
        data = json.loads(content.decode('utf-8'), object_pairs_hook=_pairs)
        if not isinstance(data, dict) or not all(isinstance(data.get(key), dict)
                for key in ('general', 'profiles', 'pronunciations', 'keybindings')):
            raise ValueError('Неполный профиль Orca.')
        selected_profile(data)
        return data
    except (ValueError, UnicodeError, KeyError, TypeError) as error:
        raise RuntimeError('Профиль Orca повреждён и сохранён без изменений.') from error


def selected_profile(data):
    start = data['general'].get('startingProfile', ['Default', 'default'])
    if not isinstance(start, list) or len(start) != 2 or not isinstance(start[1], str):
        raise ValueError('Недопустимый стартовый профиль Orca.')
    profile = data['profiles'].get(start[1])
    if not isinstance(profile, dict):
        raise ValueError('Стартовый профиль Orca отсутствует.')
    return profile


def snapshot(data):
    profile = {**data['general'], **selected_profile(data)}
    return copy.deepcopy({key: profile.get(key) for key in
                          ('enableSpeech', 'speechServerFactory', 'speechServerInfo', 'voices')})


def follows_flashos(data):
    marker = data.get('flashosSpeech')
    return (isinstance(marker, dict) and marker.get('format') == 1
            and marker.get('mode') == 'follow' and marker.get('last') == snapshot(data))


def update_speech(data, values):
    profile = selected_profile(data)
    properties = speech_properties(values)
    voices = profile.get('voices', data['general'].get('voices', {}))
    voices = copy.deepcopy(voices) if isinstance(voices, dict) else {}
    current = voices.get('default', {})
    voices['default'] = {**(current if isinstance(current, dict) else {}), **properties.pop('voices')['default']}
    for name, style in voices.items():
        if name != 'default' and isinstance(style, dict) and 'family' in style:
            style['family'] = copy.deepcopy(voices['default']['family'])
    profile.update(properties)
    profile['voices'] = voices


def atomic_write(path, data, previous):
    """Commit one authoritative Orca file; no competing sidecar can go stale."""
    content = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if len(content) > LIMIT:
        raise RuntimeError('Новые настройки Orca слишком большие.')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.orca-' + uuid.uuid4().hex + '.tmp')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if read_bytes(path) != previous:
            raise RuntimeError('Настройки Orca изменились другим процессом. Повторите чтение.')
        os.replace(temporary, path)
        if os.name == 'posix':
            try:
                directory = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError as error:
                raise DurabilityError('Профиль Orca записан, но подтверждение сохранения на флешке не получено.') from error
    finally:
        temporary.unlink(missing_ok=True)


def seed_profile(home, initial=None):
    path = profile_path(home)
    previous = read_bytes(path)
    if previous is None:
        atomic_write(path, new_profile(validate_values(initial or DEFAULTS)), None)
    else:
        # Preserve even an unreadable existing profile; the settings utility reports it.
        try:
            data = decode(previous)
        except RuntimeError:
            return path.parent
        if data == legacy_profile():
            atomic_write(path, new_profile(validate_values(initial or DEFAULTS)), previous)
        elif follows_flashos(data):
            updated = copy.deepcopy(data)
            update_speech(updated, validate_values(initial or DEFAULTS))
            updated['flashosSpeech'] = {'format': 1, 'mode': 'follow', 'last': snapshot(updated)}
            if updated != data:
                atomic_write(path, updated, previous)
    return path.parent


def read_status(home, initial=None):
    seed_profile(home, initial)
    data = decode(read_bytes(profile_path(home)))
    profile = {**data['general'], **selected_profile(data)}
    server = profile.get('speechServerInfo', ['', 'default'])
    module = server[1] if isinstance(server, list) and len(server) == 2 and isinstance(server[1], str) else 'unknown'
    voices = profile.get('voices', {})
    voice = voices.get('default', {}) if isinstance(voices, dict) else {}
    family = voice.get('family', {}) if isinstance(voice, dict) else {}
    name = family.get('name', '') if isinstance(family, dict) else ''
    known_voice = next((key for key, display in NAMES.items()
                       if display == name and module == ('espeak-ng' if key == 'espeak' else 'rhvoice')), None)
    values = {'voice': known_voice, 'rate': None, 'pitch': None, 'volume': None}
    for key, native, factor in (('rate', 'rate', 0.98), ('pitch', 'average-pitch', 0.09), ('volume', 'gain', 0.09)):
        value = voice.get(native) if isinstance(voice, dict) else None
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= factor * 100:
            values[key] = max(0, min(100, round(value / factor)))
    return {'values': values, 'module': module, 'voice_name': str(name)[:120],
            'mode': 'follow' if follows_flashos(data) else 'custom',
            'speech_enabled': profile.get('enableSpeech', True) is not False,
            'message': 'Настройки Orca прочитаны из его собственного профиля на флешке.'}


def execute(action, home, request=None):
    request = {} if request is None else request
    if not isinstance(request, dict) or set(request) - {'initial', 'values', 'mode'}:
        raise ValueError('Недопустимый запрос настроек Orca.')
    initial = validate_values(request.get('initial') or DEFAULTS)
    if action == 'orca-status':
        return read_status(home, initial)
    if action not in ('orca-set', 'orca-reset'):
        raise ValueError('Неизвестное действие настройки Orca.')
    seed_profile(home, initial)
    path = profile_path(home)
    previous = read_bytes(path)
    data = decode(previous)
    mode = 'follow' if action == 'orca-reset' else request.get('mode', 'custom')
    if mode not in ('follow', 'custom'):
        raise ValueError('Недопустимый режим речи Orca.')
    values = initial if mode == 'follow' else validate_values(request.get('values'))
    # Keep pronunciations, key bindings, braille, other profiles and voice styles.
    update_speech(data, values)
    data['flashosSpeech'] = {'format': 1, 'mode': mode, 'last': snapshot(data)}
    atomic_write(path, data, previous)
    result = read_status(home, initial)
    result['message'] = 'Речь Orca сохранена на флешке и проверена. Настройки применятся при следующем запуске приложения.'
    return result


def write_initial(runtime, settings):
    path = Path(runtime) / 'speech-defaults.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(flashos_defaults(settings), stream)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)
    os.chown(path, 1000, 1000)


def read_initial(runtime):
    path = Path(runtime) / 'speech-defaults.json'
    content = read_bytes(path)
    if content is None:
        return dict(DEFAULTS)
    if len(content) > 4096 or path.stat().st_uid != 1000 or path.stat().st_mode & 0o077:
        raise RuntimeError('Недопустимый исходный голос Orca.')
    return validate_values(json.loads(content.decode('utf-8'), object_pairs_hook=_pairs))


def dispatcher_config(module='rhvoice'):
    if module not in ('rhvoice', 'espeak-ng'):
        module = 'rhvoice'
    return ('LogLevel 1\nDefaultLanguage "ru"\nDefaultModule ' + module + '\n'
            'AddModule "rhvoice" "sd_rhvoice" "rhvoice.conf"\n'
            'AddModule "espeak-ng" "sd_espeak-ng" "espeak-ng.conf"\nAudioOutputMethod "pulse"\n')


def spawn_speech_dispatcher(argv, **options):
    # SD 0.12 ignores SIGPIPE only after module initialization. Keep Python's
    # ignored disposition so an early closed module pipe returns EPIPE instead.
    return subprocess.Popen(argv, restore_signals=False, **options)


def wait_speech_ready(paths, processes, stop=None, return_file=None):
    """Allow cold voice modules to load, while cancellation and child death stay prompt."""
    deadline = time.monotonic() + 45
    while True:
        if ((stop is not None and stop.is_set()) or
                (return_file is not None and return_file.exists())):
            return False
        if any(process.poll() is not None for process in processes):
            raise RuntimeError('Не удалось запустить компоненты речи Orca.')
        if all(path.exists() for path in paths):
            return True
        if time.monotonic() >= deadline:
            raise RuntimeError('Не удалось запустить компоненты речи Orca.')
        time.sleep(.05)


def preview(home, initial, runtime=Path('/run/user/1000')):
    """Speak through Orca's actual Speech Dispatcher factory, never FlashOS TTS."""
    status = read_status(home, initial)
    if status['values']['voice'] not in NAMES:
        raise RuntimeError('Для собственного голоса используйте окно настройки Orca в приложении.')
    runtime = Path(runtime)
    config = runtime / 'speech-preview'
    config.mkdir(mode=0o700)
    modules = config / 'modules'
    modules.mkdir()
    (modules / 'rhvoice.conf').write_text('LogLevel 1\n', encoding='utf-8')
    (modules / 'espeak-ng.conf').write_bytes(Path('/etc/speech-dispatcher/modules/espeak-ng.conf').read_bytes())
    (config / 'speechd.conf').write_text(dispatcher_config(status['module']), encoding='utf-8')
    socket = runtime / 'speech-dispatcher/speechd.sock'
    socket.parent.mkdir(mode=0o700)
    processes, server = [], None
    previous_address = os.environ.get('SPEECHD_ADDRESS')
    try:
        pulse = subprocess.Popen(['/usr/bin/pipewire-pulse'], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(pulse)
        speech = spawn_speech_dispatcher(['/usr/bin/speech-dispatcher', '--run-single',
            '--communication-method=unix_socket', '--socket-path=' + str(socket),
            '--config-dir=' + str(config), '--timeout=0', '--log-dir=' + str(runtime)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, 'LD_LIBRARY_PATH': '/run/flashos-rhvoice-libs'})
        processes.append(speech)
        wait_speech_ready((socket, runtime / 'pulse/native'), processes)
        os.environ['SPEECHD_ADDRESS'] = 'unix_socket:' + str(socket)
        import speechd
        from orca import speechdispatcherfactory
        from orca.acss import ACSS
        server = speechdispatcherfactory.SpeechServer.get_speech_server(['FlashOS preview', status['module']])
        if server is None:
            raise RuntimeError('Orca не удалось открыть выбранный синтезатор.')
        data = decode(read_bytes(profile_path(home)))
        voice = snapshot(data)['voices']['default']
        # _apply_acss is the exact installed Orca implementation used for app speech.
        server._apply_acss(ACSS(voice))
        done, completed = threading.Event(), []
        def event(kind, *args):
            if kind in (speechd.CallbackType.END, speechd.CallbackType.CANCEL):
                completed.append(kind)
                done.set()
        server._client.speak('Это пример речи экранного диктора Orca. Настройки сохраняются на флешке.',
                             callback=event, event_types=(speechd.CallbackType.END, speechd.CallbackType.CANCEL))
        if not done.wait(30) or completed != [speechd.CallbackType.END]:
            raise RuntimeError('Пробная речь Orca не завершилась. Проверьте звуковое устройство.')
        return {'message': 'Пример произнесён выбранным синтезатором Orca.', 'module': status['module'],
                'voice': status['values']['voice']}
    finally:
        if server is not None:
            server.shutdown()
        if previous_address is None:
            os.environ.pop('SPEECHD_ADDRESS', None)
        else:
            os.environ['SPEECHD_ADDRESS'] = previous_address
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
