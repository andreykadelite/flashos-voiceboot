"""Disk identity and operation rules, shared by the interface and tests."""
import hashlib
import json

TITLE = 'Установщик Flash OS'
VERSION = '1.0.2'
SYSTEM_VERSION = '2026.09.09.3'
MINIMUM_BYTES = 4_000_000_000
MBR_LIMIT = 2 * 1024**4
IDENTITY_KEYS = ('number', 'model', 'serial', 'size', 'sector', 'path', 'pnp')


def identity(disk):
    return {key: disk.get(key) for key in IDENTITY_KEYS}


def fingerprint(disk):
    return hashlib.sha256(json.dumps(identity(disk), sort_keys=True).encode()).hexdigest()


def disk_label(disk):
    letters = ', '.join(v['letter'] + ':' for v in disk.get('volumes', []) if v.get('letter'))
    return f"{disk['model']} · {disk['size'] / 1024**3:.1f} ГиБ · диск {disk['number']}" + (f' · {letters}' if letters else '')


def unavailable_reason(disk, action='install', style='MBR', filesystem='exFAT'):
    if disk.get('bus') != 'USB':
        return 'Разрешены только USB-накопители.'
    if disk.get('boot') or disk.get('system') or disk.get('protected'):
        return 'Накопитель используется Windows, файлом подкачки или установщиком.'
    if disk.get('readonly') or disk.get('offline'):
        return 'Накопитель отключён или защищён от записи.'
    if not disk.get('path') or not disk.get('pnp'):
        return 'Windows не предоставила идентификаторы устройства.'
    if disk.get('sector') != 512:
        return 'Для этой версии загрузчика нужен логический сектор 512 байт.'
    if action == 'install' and disk['size'] < MINIMUM_BYTES:
        return 'Для FlashOS нужна флешка объёмом не менее 4 ГБ.'
    if action not in ('install', 'format', 'verify'):
        return 'Неизвестное действие.'
    if style not in ('MBR', 'GPT'):
        return 'Неизвестный тип разметки.'
    if action != 'verify' and style == 'MBR' and disk['size'] >= MBR_LIMIT:
        return 'Для накопителя от 2 ТиБ выберите GPT.'
    if filesystem not in ('exFAT', 'NTFS', 'FAT32'):
        return 'Неизвестная файловая система.'
    if action == 'format' and filesystem == 'FAT32' and disk['size'] > 32_000_000_000:
        return 'Форматирование FAT32 здесь доступно для накопителей до 32 ГБ.'
    return ''


def validate_request(request, current):
    if identity(current) != request.get('target'):
        raise ValueError('Выбранный накопитель изменился. Обновите список и выберите его снова.')
    reason = unavailable_reason(current, request['action'], request['style'], request['filesystem'])
    if reason:
        raise ValueError(reason)
    if request['action'] != 'verify' and request.get('erase_confirmed') is not True:
        raise ValueError('Удаление данных не подтверждено.')
