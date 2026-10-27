"""Validated USB preferences and speech formatting for the native terminal."""
import json
import os
from pathlib import Path
import uuid
from settings import DurabilityError


OPTIONS = {
    'input_echo': ('Озвучка ввода', [('characters', 'Символы'), ('words', 'Слова'), ('both', 'Символы и слова'), ('off', 'Выключена')]),
    'prompt_mode': ('Приглашение оболочки', [('once', 'Один раз при открытии'), ('always', 'После каждой команды'), ('off', 'Не читать')]),
    'punctuation': ('Знаки в командах и выводе', [('some', 'Основные знаки'), ('all', 'Все знаки'), ('none', 'Как произносит голос')]),
    'queue_limit': ('Очередь речи', [(20, '20 фрагментов'), (80, '80 фрагментов'), (200, '200 фрагментов')]),
    'chunk_chars': ('Длина фрагмента речи', [(200, '200 символов'), (800, '800 символов'), (1600, '1600 символов')]),
    'font_size': ('Размер шрифта', [(value, str(value)) for value in range(9, 29)]),
    'scrollback': ('Строк в истории экрана', [(1000, '1000'), (5000, '5000'), (20000, '20000')]),
    'cursor_shape': ('Форма текстового курсора', [('block', 'Блок'), ('ibeam', 'Вертикальная черта'), ('underline', 'Подчёркивание')]),
}
BOOL_LABELS = {
    'auto_read': 'Автоматически читать новый вывод',
    'speak_commands': 'Читать принятую команду после Enter',
    'history_echo': 'Читать выбранную команду истории',
    'cursor_echo': 'Читать символ при перемещении курсора',
    'delete_echo': 'Читать удаляемый символ',
    'completion_echo': 'Читать строку после автодополнения Tab',
    'interrupt_on_type': 'Прерывать текущую речь при вводе',
    'capital_letters': 'Объявлять заглавные буквы',
    'welcome': 'Читать подсказку при открытии',
    'announce_exit': 'Объявлять завершение оболочки',
    'scroll_on_output': 'Прокручивать экран при новом выводе',
    'cursor_blink': 'Мигание текстового курсора',
    'audible_bell': 'Звуковой сигнал терминала',
    'middle_paste': 'Вставлять выделение средней кнопкой мыши',
}
DEFAULTS = {'input_echo': 'characters', 'prompt_mode': 'once', 'punctuation': 'none',
            'queue_limit': 80, 'chunk_chars': 800, 'font_size': 14,
            'scrollback': 5000, 'cursor_shape': 'block',
            **{key: key in ('auto_read', 'speak_commands', 'history_echo', 'cursor_echo',
                           'delete_echo', 'completion_echo', 'interrupt_on_type',
                           'welcome', 'announce_exit', 'middle_paste') for key in BOOL_LABELS}}


def validate(data):
    result = dict(DEFAULTS)
    if not isinstance(data, dict):
        return result
    for key, (_, choices) in OPTIONS.items():
        if any(type(data.get(key)) is type(value) and data[key] == value for value, _ in choices):
            result[key] = data[key]
    for key in BOOL_LABELS:
        if type(data.get(key)) is bool:
            result[key] = data[key]
    return result


class TerminalPreferences:
    """No terminal contents or commands are ever written to this file."""
    def __init__(self, state_root=None):
        self.path = Path(state_root) / 'flashos/terminal.json' if state_root is not None else None
        self.data = dict(DEFAULTS)
        self.error = ''
        if self.path is None:
            return
        try:
            if self.path.stat().st_size > 16384:
                raise ValueError('Файл настроек терминала слишком большой.')
            with self.path.open(encoding='utf-8') as stream:
                content = stream.read(16385)
            if len(content.encode('utf-8')) > 16384:
                raise ValueError('Файл настроек терминала слишком большой.')
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError('Настройки должны быть объектом JSON.')
            self.data = validate(data)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, RecursionError) as error:
            self.error = 'Настройки терминала не прочитаны. Использованы стандартные значения. ' + str(error)

    def save(self, changes):
        data = validate({**self.data, **changes})
        if self.path is None:
            self.data = data
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name('.terminal-' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.data = data
            if os.name == 'posix':
                try:
                    directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except OSError as error:
                    raise DurabilityError('Файл настроек терминала записан, но подтверждение сохранения на носителе не получено.') from error
        finally:
            temporary.unlink(missing_ok=True)
        self.error = ''


SYMBOLS = {' ': 'пробел', '\t': 'табуляция', '.': 'точка', ',': 'запятая', ':': 'двоеточие',
           ';': 'точка с запятой', '/': 'косая черта', '\\': 'обратная косая черта',
           '-': 'дефис', '_': 'подчёркивание', '+': 'плюс', '=': 'равно', '*': 'звёздочка',
           '#': 'решётка', '$': 'доллар', '%': 'процент', '&': 'амперсанд', '|': 'вертикальная черта',
           '~': 'тильда', '^': 'крышка', '@': 'собака', '!': 'восклицательный знак',
           '?': 'вопросительный знак', '<': 'меньше', '>': 'больше', '(': 'открывающая круглая скобка',
           ')': 'закрывающая круглая скобка', '[': 'открывающая квадратная скобка',
           ']': 'закрывающая квадратная скобка', '{': 'открывающая фигурная скобка',
           '}': 'закрывающая фигурная скобка', "'": 'апостроф', '"': 'кавычка', '`': 'обратный апостроф'}


def character_name(value, capitals=False):
    if not value:
        return 'Пробел'
    if value in SYMBOLS:
        return SYMBOLS[value]
    return ('Заглавная ' if capitals and value.isupper() else '') + value


def speech_text(text, punctuation):
    if punctuation == 'none':
        return text
    signs = set(SYMBOLS) - {' ', '\t'}
    if punctuation == 'some':
        signs -= set('.,:;!?\'"()[]{}')
    return ''.join(' ' + SYMBOLS[char] + ' ' if char in signs else char for char in text)
