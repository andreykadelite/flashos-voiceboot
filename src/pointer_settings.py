"""Portable pointer preferences and capability-aware X11/libinput control.

The USB stores preferences, never transient XInput IDs. Re-enumeration applies
them to newly connected devices. No operation disables a physical input device.
"""
import os
import re
import subprocess
import threading


DEFAULTS = {'visible': True, 'size': 32, 'color': 'white', 'speed': 0,
            'acceleration': 'adaptive', 'left_handed': False,
            'natural_scroll': False, 'tap_to_click': True, 'tap_drag': True,
            'disable_while_typing': True, 'scroll_method': 'auto',
            'click_method': 'auto', 'middle_emulation': False,
            'activation': 'double', 'wheel_lines': 1}
LABELS = {'visible': 'Указатель на экране', 'size': 'Размер указателя',
          'color': 'Цвет указателя', 'speed': 'Скорость мыши и тачпада',
          'acceleration': 'Ускорение указателя', 'left_handed': 'Основная кнопка справа',
          'natural_scroll': 'Обратная прокрутка', 'tap_to_click': 'Касание тачпада как щелчок',
          'tap_drag': 'Перетаскивание касанием', 'disable_while_typing': 'Защита тачпада при печати',
          'scroll_method': 'Прокрутка тачпада', 'click_method': 'Кнопки тачпада',
          'middle_emulation': 'Средняя кнопка двумя кнопками',
          'activation': 'Открытие пункта мышью', 'wheel_lines': 'Шаг колеса в меню'}
ON_OFF = [('Выключено', False), ('Включено', True)]
OPTIONS = {key: list(ON_OFF) for key, value in DEFAULTS.items() if type(value) is bool}
OPTIONS.update({
    'size': [(str(size) + ' пикселей', size) for size in (24, 32, 48, 64)],
    'color': [('Белый с чёрной обводкой', 'white'), ('Чёрный с белой обводкой', 'black'),
              ('Жёлтый с чёрной обводкой', 'yellow')],
    'speed': [('Медленно', -75), ('Умеренно медленно', -40), ('Обычная', 0),
              ('Быстро', 40), ('Очень быстро', 75)],
    'acceleration': [('Адаптивное', 'adaptive'), ('Постоянная скорость', 'flat')],
    'scroll_method': [('По возможностям устройства', 'auto'), ('Двумя пальцами', 'two_finger'),
                      ('Вдоль края', 'edge')],
    'click_method': [('По возможностям устройства', 'auto'), ('По области нажатия', 'areas'),
                     ('По числу пальцев', 'fingers')],
    'activation': [('Двойной щелчок', 'double'), ('Один щелчок', 'single')],
    'wheel_lines': [('Один пункт', 1), ('Три пункта', 3), ('Пять пунктов', 5)]})

HELP = ('Эти настройки относятся к мыши, тачпаду и указателю FlashOS и сохраняются на этой флешке. '
        'Один щелчок выбирает пункт и читает его, двойной открывает. Режим одного щелчка можно выбрать отдельно. '
        'Колесо и прокрутка двумя пальцами перемещают выбор. Внизу есть кнопки открытия, '
        'возврата, справки и остановки речи. Дополнительная кнопка возвращает назад: '
        'обычно правая, а в режиме основной кнопки справа — левая. '
        'Скрытие указателя не отключает мышь: клавиатура и щелчки продолжают работать. '
        'Ctrl Alt P в меню FlashOS показывает скрытый указатель. Переключатель основной кнопки меняет левую и правую кнопки. '
        'Параметры устройств применяются только там, где драйвер сообщает об их поддержке. '
        'При подключении USB-мыши настройки применяются автоматически. '
        'Размер и цвет собственного курсора применяются в меню FlashOS; внешние приложения могут '
        'использовать свои курсоры. Раздел «Устройства и применение» показывает обнаруженные устройства и ошибки. '
        'Сенсорный экран не равнозначен тачпаду; многооконные жесты тремя пальцами не назначаются.')


def validate(data):
    """Drop unknown/invalid values without accepting bool as an integer."""
    result = dict(DEFAULTS)
    if not isinstance(data, dict):
        return result
    for key, choices in OPTIONS.items():
        if key not in data:
            continue
        if any(type(data[key]) is type(value) and data[key] == value for _, value in choices):
            result[key] = data[key]
    return result


def value_label(key, value):
    return next((label for label, candidate in OPTIONS[key]
                 if type(candidate) is type(value) and candidate == value), str(value))


def parse_devices(text):
    """Only attached slave pointers; exclude keyboards, masters and XTEST."""
    result = []
    for line in text.splitlines()[:128]:
        match = re.search(r'^.*?([\w].*?)\s+id=(\d+)\s+\[slave\s+pointer', line)
        if match and 'XTEST' not in match[1].upper():
            result.append((int(match[2]), match[1].strip()[:200]))
    return result[:32]


def parse_properties(text):
    properties = {}
    for line in text.splitlines()[:256]:
        match = re.match(r'^\s+(.+?)\s+\(\d+\):\s*(.*?)\s*$', line)
        if match:
            properties[match[1]] = tuple(part.strip() for part in match[2].split(','))
    return properties


def property_changes(values, properties):
    """Use advertised capability vectors; never synthesize absent properties."""
    values = validate(values)
    result = {}
    scalar = {'libinput Accel Speed': str(values['speed'] / 100),
              'libinput Left Handed Enabled': str(int(values['left_handed'])),
              'libinput Natural Scrolling Enabled': str(int(values['natural_scroll'])),
              'libinput Tapping Enabled': str(int(values['tap_to_click'])),
              'libinput Tapping Drag Enabled': str(int(values['tap_drag'])),
              'libinput Disable While Typing Enabled': str(int(values['disable_while_typing'])),
              'libinput Middle Emulation Enabled': str(int(values['middle_emulation']))}
    for key, value in scalar.items():
        if key in properties:
            result[key] = (value,)
    # Current libinput may expose a third custom-acceleration profile slot.
    available = properties.get('libinput Accel Profiles Available', ())
    index = 0 if values['acceleration'] == 'adaptive' else 1
    if 'libinput Accel Profile Enabled' in properties and len(available) > index and available[index] == '1':
        result['libinput Accel Profile Enabled'] = tuple('1' if item == index else '0'
                                                        for item in range(len(available)))
    for selection, available_key, enabled_key, choices in (
            ('scroll_method', 'libinput Scroll Methods Available', 'libinput Scroll Method Enabled',
             {'two_finger': 0, 'edge': 1}),
            ('click_method', 'libinput Click Methods Available', 'libinput Click Method Enabled',
             {'areas': 0, 'fingers': 1})):
        available = properties.get(available_key, ())
        index = choices.get(values[selection])
        if index is None:
            default = properties.get(enabled_key + ' Default', ())
            if enabled_key in properties and len(default) == len(available):
                result[enabled_key] = default
        elif enabled_key in properties and len(available) > index and available[index] == '1':
            result[enabled_key] = tuple('1' if item == index else '0' for item in range(len(available)))
    return result


def _same_numbers(first, second):
    try:
        return len(first) == len(second) and all(abs(float(a) - float(b)) < .0001
                                                for a, b in zip(first, second))
    except (ValueError, TypeError):
        return first == second


class XInputBackend:
    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def command(self, arguments):
        result = self.runner(['xinput', *arguments], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, encoding='utf-8', errors='replace',
                             timeout=2, check=False, env={**os.environ, 'LC_ALL': 'C'})
        if result.returncode:
            raise RuntimeError((result.stderr or 'XInput: ошибка устройства').strip()[:250])
        return result.stdout

    def apply(self, values):
        values = validate(values)
        devices = parse_devices(self.command(['--list', '--short']))
        report = []
        for identifier, name in devices:
            try:
                properties = parse_properties(self.command(['--list-props', str(identifier)]))
                changes = property_changes(values, properties)
                for key, value in changes.items():
                    if not _same_numbers(properties[key], value):
                        self.command(['--set-prop', str(identifier), key, *value])
                # Readback distinguishes an attempted write from applied settings.
                observed = parse_properties(self.command(['--list-props', str(identifier)])) if changes else properties
                failed = [key for key, value in changes.items() if not _same_numbers(observed.get(key, ()), value)]
                kind = 'тачпад' if 'libinput Tapping Enabled' in properties else 'указательное устройство'
                report.append(name + ' — ' + kind + ': ' +
                              ('не удалось проверить ' + ', '.join(failed) if failed else
                               'проверено параметров: ' + str(len(changes)) if changes else
                               'драйвер не предоставляет настройки libinput; обычные щелчки работают'))
                for setting, available_key, index in (
                        ('scroll_method', 'libinput Scroll Methods Available',
                         {'two_finger': 0, 'edge': 1}.get(values['scroll_method'])),
                        ('click_method', 'libinput Click Methods Available',
                         {'areas': 0, 'fingers': 1}.get(values['click_method']))):
                    available = properties.get(available_key, ())
                    if index is not None and (len(available) <= index or available[index] != '1'):
                        report.append(name + ': ' + LABELS[setting] + ' — выбранный режим не поддерживается')
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                report.append(name + ': ' + str(error)[:250])
        return report or ['Указательные устройства XInput не обнаружены. Подключите мышь и обновите сведения.']


class PointerMonitor:
    """Hotplug polling stays off the GUI thread, including suspended menus."""
    def __init__(self, values, backend=None):
        self.values = validate(values)
        self.backend = backend or XInputBackend()
        self.report = ['Обнаруживаю мыши и тачпады.']
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self.thread = threading.Thread(target=self._run, name='flashos-pointer', daemon=True)
        self.thread.start()

    def configure(self, values):
        with self._lock:
            self.values = validate(values)
        self._wake.set()

    def snapshot(self):
        with self._lock:
            return list(self.report)

    def _run(self):
        while not self._closed.is_set():
            self._wake.clear()
            with self._lock:
                values = dict(self.values)
            try:
                report = self.backend.apply(values)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                report = ['Настройки устройств не применены: ' + str(error)[:250]]
            with self._lock:
                self.report = report
            self._wake.wait(3)

    def close(self):
        self._closed.set()
        self._wake.set()


def cursor_pixels(size, color):
    """Opaque outlined arrow in ARGB; no raster/image library dependency."""
    fill = {'white': 0xffffffff, 'black': 0xff111111, 'yellow': 0xffffdf00}[color]
    outline = 0xffffffff if color == 'black' else 0xff000000
    pixels = []
    for y in range(size):
        for x in range(size):
            px, py = x * 24 / size, y * 24 / size
            inside = (1 <= px <= 2 + py * .67 and 1 <= py <= 18) or (7 <= px <= 10 and 14 <= py <= 23)
            edge = px < 2.5 or py < 2.5 or px > .67 * py or 16 < py < 19 or (py > 17 and (px < 8 or px > 9))
            pixels.append((outline if edge else fill) if inside else 0)
    return pixels


class X11Cursor:
    """XFixes visibility is global and balanced; appearance targets our windows."""
    def __init__(self):
        import ctypes as c
        self.c = c
        self.xlib = c.CDLL('libX11.so.6')
        self.xcursor = c.CDLL('libXcursor.so.1')
        self.xfixes = c.CDLL('libXfixes.so.3')
        self.xlib.XOpenDisplay.argtypes, self.xlib.XOpenDisplay.restype = [c.c_char_p], c.c_void_p
        self.display = self.xlib.XOpenDisplay(None)
        if not self.display:
            raise OSError('Сервер X11 недоступен')
        self.xlib.XDefaultRootWindow.argtypes, self.xlib.XDefaultRootWindow.restype = [c.c_void_p], c.c_ulong
        self.root = self.xlib.XDefaultRootWindow(self.display)
        self.xlib.XDefineCursor.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong]
        self.xlib.XFreeCursor.argtypes = [c.c_void_p, c.c_ulong]
        self.xlib.XFlush.argtypes = [c.c_void_p]
        self.xlib.XCloseDisplay.argtypes = [c.c_void_p]
        self.xfixes.XFixesHideCursor.argtypes = [c.c_void_p, c.c_ulong]
        self.xfixes.XFixesShowCursor.argtypes = [c.c_void_p, c.c_ulong]
        self.xfixes.XFixesChangeCursorByName.argtypes = [c.c_void_p, c.c_ulong, c.c_char_p]
        self.xfixes.XFixesQueryVersion.argtypes = [c.c_void_p, c.POINTER(c.c_int), c.POINTER(c.c_int)]
        major, minor = c.c_int(4), c.c_int(0)
        self.visibility = bool(self.xfixes.XFixesQueryVersion(self.display, c.byref(major), c.byref(minor))) and major.value >= 4
        self.hidden = False
        class Image(c.Structure):
            _fields_ = [('version', c.c_uint), ('size', c.c_uint), ('width', c.c_uint),
                        ('height', c.c_uint), ('xhot', c.c_uint), ('yhot', c.c_uint),
                        ('delay', c.c_uint), ('pixels', c.POINTER(c.c_uint32))]
        self.xcursor.XcursorImageCreate.argtypes, self.xcursor.XcursorImageCreate.restype = [c.c_int, c.c_int], c.POINTER(Image)
        self.xcursor.XcursorImageLoadCursor.argtypes = [c.c_void_p, c.POINTER(Image)]
        self.xcursor.XcursorImageLoadCursor.restype = c.c_ulong
        self.xcursor.XcursorImageDestroy.argtypes = [c.POINTER(Image)]
        self.xcursor.XcursorSetDefaultSize.argtypes = [c.c_void_p, c.c_int]

    def configure(self, values, windows):
        visible = values['visible']
        if self.visibility and self.hidden == visible:
            (self.xfixes.XFixesShowCursor if visible else self.xfixes.XFixesHideCursor)(self.display, self.root)
            self.hidden = not visible
        size = values['size']
        self.xcursor.XcursorSetDefaultSize(self.display, size)
        image = self.xcursor.XcursorImageCreate(size, size)
        if not image:
            raise OSError('Не удалось создать изображение указателя')
        try:
            image.contents.xhot = image.contents.yhot = max(1, size // 24)
            for index, pixel in enumerate(cursor_pixels(size, values['color'])):
                image.contents.pixels[index] = pixel
            cursor = self.xcursor.XcursorImageLoadCursor(self.display, image)
            if not cursor:
                raise OSError('Сервер X11 не поддерживает выбранный курсор')
            try:
                for window in [self.root, *windows]:
                    self.xlib.XDefineCursor(self.display, window, cursor)
                for name in (b'left_ptr', b'default', b'arrow'):
                    self.xfixes.XFixesChangeCursorByName(self.display, cursor, name)
            finally:
                self.xlib.XFreeCursor(self.display, cursor)
        finally:
            self.xcursor.XcursorImageDestroy(image)
        self.xlib.XFlush(self.display)

    def close(self):
        if self.display:
            if self.hidden and self.visibility:
                self.xfixes.XFixesShowCursor(self.display, self.root)
            self.xlib.XFlush(self.display)
            self.xlib.XCloseDisplay(self.display)
            self.display = None
