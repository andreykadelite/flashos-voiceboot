"""Small, validated settings file on the USB service partition."""
import json
import os
from pathlib import Path

VOICES = [
    ('elena', 'Елена, RHVoice'), ('irina', 'Ирина, RHVoice'),
    ('anna', 'Анна, RHVoice'), ('yuriy', 'Юрий, RHVoice'),
    ('aleksandr', 'Александр, RHVoice'), ('espeak', 'eSpeak, резервный голос'),
]
DEFAULTS = {'voice': 'elena', 'rate': 100, 'pitch': 100, 'volume': 75,
            'signals': True, 'progress_seconds': 30}


class DurabilityError(OSError):
    pass


def validate(data):
    result = dict(DEFAULTS)
    if not isinstance(data, dict):
        return result
    if isinstance(data.get('voice'), str) and data['voice'] in {item[0] for item in VOICES}:
        result['voice'] = data['voice']
    for key, low, high in [('rate', 50, 250), ('pitch', 50, 150), ('volume', 10, 100),
                           ('progress_seconds', 15, 120)]:
        if type(data.get(key)) is int:
            result[key] = max(low, min(high, data[key]))
    if type(data.get('signals')) is bool:
        result['signals'] = data['signals']
    return result


class Settings:
    def __init__(self, state_root):
        self.path = Path(state_root) / 'flashos/settings.json'
        self.data = dict(DEFAULTS)
        self.error = ''
        try:
            if self.path.stat().st_size > 4096:
                raise ValueError('Settings file is too large')
            with self.path.open(encoding='utf-8') as stream:
                content = stream.read(4097)
            if len(content.encode('utf-8')) > 4096:
                raise ValueError('Settings file is too large')
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError('Settings must be a JSON object')
            self.data = validate(parsed)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, RecursionError) as error:
            self.error = 'Настройки не прочитаны. Использованы стандартные значения. ' + str(error)

    def save(self, changes):
        data = validate({**self.data, **changes})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        self.data = data
        try:
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as error:
            self.error = 'Файл настроек записан, но подтверждение сохранения на носителе не получено.'
            raise DurabilityError(self.error) from error
        self.error = ''
