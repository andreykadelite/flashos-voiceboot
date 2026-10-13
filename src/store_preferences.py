"""Whitelisted, persistent application preferences; run only as the app user."""
import argparse
import configparser
import io
import json
import os
from pathlib import Path
import sys
import uuid

HOME = Path('/home/flashos-app')
INTERFACE = 'org.gnome.desktop.interface'
A11Y = 'org.gnome.desktop.a11y.interface'
READERS = 'org.gnome.desktop.a11y.applications'
CHOICES = {'text_scale': (1.0, 1.25, 1.5, 2.0), 'contrast': (False, True),
           'animations': (False, True), 'cursor_size': (24, 32, 48)}
DEFAULTS = {'text_scale': 1.0, 'contrast': False, 'animations': True, 'cursor_size': 24}
KEYS = {'text_scale': (INTERFACE, 'text-scaling-factor'),
        'contrast': (A11Y, 'high-contrast'), 'animations': (INTERFACE, 'enable-animations'),
        'cursor_size': (INTERFACE, 'cursor-size')}
REQUIRED = ((INTERFACE, 'toolkit-accessibility'), (READERS, 'screen-reader-enabled'))
INI_KEYS = ('gtk-theme', 'gtk-enable-animations', 'gtk-cursor-theme-size')


def validate_request(request):
    if not isinstance(request, dict) or set(request) != {'key', 'value'}:
        raise ValueError('Недопустимый запрос настройки.')
    key, value = request['key'], request['value']
    if not isinstance(key, str) or key not in CHOICES:
        raise ValueError('Эта настройка недоступна в утилите.')
    expected = bool if key in ('contrast', 'animations') else int if key == 'cursor_size' else float
    if key == 'text_scale' and type(value) in (float, int):
        value = float(value)
    elif type(value) is not expected:
        raise ValueError('Недопустимый тип значения настройки.')
    if value not in CHOICES[key]:
        raise ValueError('Недопустимое значение настройки.')
    return key, value


class SettingsBackend:
    def __init__(self):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        source = Gio.SettingsSchemaSource.get_default()
        self.settings = {}
        required = set(KEYS.values()) | set(REQUIRED) | {(INTERFACE, 'gtk-theme')}
        for schema, key in required:
            descriptor = source.lookup(schema, True) if source is not None else None
            if descriptor is None or not descriptor.has_key(key):
                raise RuntimeError('Отсутствует компонент настроек доступности: ' + schema + '/' + key)
            if schema not in self.settings:
                self.settings[schema] = Gio.Settings.new(schema)

    def get(self, schema, key):
        return self.settings[schema].get_value(key).unpack()

    def change(self, values):
        previous = {(schema, key): self.settings[schema].get_value(key) for schema, key in values}
        try:
            for (schema, key), value in values.items():
                setting = self.settings[schema]
                if not setting.is_writable(key):
                    raise RuntimeError('Настройка запрещена политикой: ' + key)
                variant = self.GLib.Variant(setting.get_value(key).get_type_string(), value)
                if not setting.set_value(key, variant):
                    raise RuntimeError('Не удалось сохранить настройку: ' + key)
            self.Gio.Settings.sync()
            if any(self.get(*key) != value for key, value in values.items()):
                raise RuntimeError('Проверка сохранённых настроек не пройдена.')
        except Exception:
            for (schema, key), value in previous.items():
                self.settings[schema].set_value(key, value)
            self.Gio.Settings.sync()
            raise


def status(backend):
    values = {name: backend.get(*key) for name, key in KEYS.items()}
    values['toolkit_accessibility'] = backend.get(*REQUIRED[0])
    values['screen_reader_enabled'] = backend.get(*REQUIRED[1])
    if (type(values['text_scale']) not in (int, float) or not 0.5 <= values['text_scale'] <= 4
            or type(values['cursor_size']) is not int or not 1 <= values['cursor_size'] <= 256
            or any(type(values[name]) is not bool for name in ('contrast', 'animations',
                         'toolkit_accessibility', 'screen_reader_enabled'))):
        raise RuntimeError('Профиль настроек повреждён. Сначала восстановите настройки доступности.')
    return values


def settings_files(home, values, reset=False, changed_keys=()):
    """Prepare both GTK profiles before changing anything; preserve other settings."""
    result = []
    for version in ('gtk-3.0', 'gtk-4.0'):
        folder = home / '.config' / version
        for parent in (home, home / '.config', folder):
            if parent.is_symlink():
                raise RuntimeError('Папка настроек не должна быть символической ссылкой.')
            if parent.exists() and not parent.is_dir():
                raise RuntimeError('Папка настроек занята обычным файлом.')
        path = folder / 'settings.ini'
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_size > 262144)):
            raise RuntimeError('Недопустимый файл настроек GTK.')
        previous = path.read_bytes() if path.exists() else None
        parser = configparser.RawConfigParser(strict=True)
        parser.optionxform = str
        if previous is not None:
            try:
                parser.read_string(previous.decode('utf-8'))
            except (UnicodeError, configparser.Error) as error:
                raise RuntimeError('Файл настроек GTK повреждён и сохранён без изменений.') from error
        if not parser.has_section('Settings'):
            parser.add_section('Settings')
        if reset:
            for key in INI_KEYS:
                parser.remove_option('Settings', key)
        else:
            if 'contrast' in changed_keys:
                parser.set('Settings', 'gtk-theme', 'HighContrast' if values['contrast'] else 'Adwaita')
            if 'animations' in changed_keys:
                parser.set('Settings', 'gtk-enable-animations', 'true' if values['animations'] else 'false')
            if 'cursor_size' in changed_keys:
                parser.set('Settings', 'gtk-cursor-theme-size', str(values['cursor_size']))
        output = io.StringIO()
        parser.write(output)
        result.append((path, previous, output.getvalue().encode('utf-8')))
    return result


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.settings-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        if os.name == 'posix':
            fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def execute(action, backend, home=HOME, request=None):
    if action == 'orca-preview':
        from orca_settings import preview, DEFAULTS as ORCA_DEFAULTS
        if not isinstance(request, dict) or set(request) != {'initial'}:
            raise ValueError('Недопустимый запрос пробной речи Orca.')
        return preview(home, request.get('initial') or ORCA_DEFAULTS)
    if action in ('orca-status', 'orca-set', 'orca-reset'):
        from orca_settings import execute as execute_orca
        result = execute_orca(action, home, request)
        # The speech utility can restore accessibility but never turn it off.
        if action != 'orca-status':
            backend.change({key: True for key in REQUIRED})
        return result
    if action == 'status':
        return {'values': status(backend), 'message': 'Настройки приложений прочитаны.'}
    if action not in ('set', 'reset', 'enable'):
        raise ValueError('Неизвестное действие утилиты настроек.')
    if action == 'reset':
        values = dict(DEFAULTS)
        changed = set(KEYS)
    else:
        previous = status(backend)
        values = {name: previous[name] for name in KEYS}
        changed = set()
        if action == 'set':
            key, value = validate_request(request)
            values[key] = value
            changed.add(key)
    # Accessibility cannot be disabled through this utility, including on reset.
    changes = {key: True for key in REQUIRED}
    changes.update({KEYS[name]: values[name] for name in changed})
    if 'contrast' in changed:
        changes[(INTERFACE, 'gtk-theme')] = 'HighContrast' if values['contrast'] else 'Adwaita'
    ini_changed = changed.intersection(('contrast', 'animations', 'cursor_size'))
    files = settings_files(home, values, reset=action == 'reset', changed_keys=changed) if ini_changed else []
    original = {key: backend.get(*key) for key in changes}
    written = []
    try:
        for path, old, new in files:
            written.append((path, old))
            atomic_write(path, new)
        backend.change(changes)
    except Exception:
        for path, old in reversed(written):
            if old is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, old)
        backend.change(original)
        raise
    return {'values': status(backend), 'message': 'Настройки сохранены на флешке и проверены. '
            'Они применятся при следующем запуске приложений.'}


def session_environment(backend=None):
    """Apply preferences without a desktop daemon; do not replace custom themes."""
    backend = backend or SettingsBackend()
    values = status(backend)
    result = {}
    if values['text_scale'] in CHOICES['text_scale']:
        result['GDK_DPI_SCALE'] = str(float(values['text_scale']))
        result['GDK_SCALE'] = '1'
    if values['cursor_size'] in CHOICES['cursor_size']:
        result['XCURSOR_SIZE'] = str(values['cursor_size'])
    if values['contrast']:
        result['GTK_THEME'] = 'HighContrast'
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('status', 'set', 'reset', 'enable', 'orca-status', 'orca-set', 'orca-reset', 'orca-preview'))
    parser.add_argument('--request', type=Path)
    args = parser.parse_args()
    try:
        if os.geteuid() != 1000 or os.environ.get('HOME') != str(HOME):
            raise RuntimeError('Настройки выполняются только от пользователя приложений FlashOS.')
        marker = json.loads(Path('/etc/flashos-app-root').read_text(encoding='utf-8'))
        if marker != {'format': 1, 'suite': 'trixie', 'role': 'applications'}:
            raise RuntimeError('Настройки доступны только в среде приложений FlashOS.')
        request = None
        if args.request is not None:
            if args.request.is_symlink() or args.request.stat().st_size > 4096:
                raise ValueError('Недопустимый запрос настроек.')
            request = json.loads(args.request.read_text(encoding='utf-8'))
        data = execute(args.action, SettingsBackend(), request=request)
        print(json.dumps({'format': 1, 'ok': True, 'data': data}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'format': 1, 'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
