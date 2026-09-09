"""Structured, speakable inventory. Preserve original tool output as attachments."""
import json
import re

from hardware import firmware_state


def size(value):
    try:
        number = int(value)
    except (ValueError, TypeError):
        return 'не сообщён'
    return f'{number / 1024**3:.2f} ГиБ' if number >= 1024**3 else f'{number / 1024**2:.1f} МиБ'


def useful(value):
    return bool(value and str(value).strip().lower() not in {
        'unknown', 'not specified', 'not provided', 'none', 'other', 'default string',
        'to be filled by o.e.m.', 'to be filled by oem', '00000000'})


def dmi_records(text):
    records = []
    current = None
    for line in text.splitlines():
        match = re.match(r'Handle (0x[0-9A-Fa-f]+), DMI type (\d+),', line)
        if match:
            current = {'handle': match[1], 'dmi_type': int(match[2])}
            records.append(current)
        elif current is not None and line[:1].isspace() and ':' in line:
            key, _, value = line.strip().partition(':')
            current[key] = value.strip()
    return records


def fields(record, names):
    return [label + ': ' + str(record[key]) for key, label in names if useful(record.get(key))]


def memory_sections(raw, meminfo):
    values = {key: int(value) * 1024 for key, value in re.findall(r'^(\w+):\s*(\d+)\s+kB', meminfo, re.M)}
    rows = [label + ': ' + size(values[key]) for key, label in (
        ('MemTotal', 'Доступно системе всего'), ('MemAvailable', 'Доступно для новых программ'),
        ('MemFree', 'Полностью свободно'), ('Cached', 'Кэш файлов'), ('SwapTotal', 'Подкачка всего'),
        ('SwapFree', 'Подкачка свободна')) if key in values]
    records = dmi_records(raw)
    arrays = [r for r in records if r['dmi_type'] == 16 and r.get('Use') == 'System Memory']
    handles = {r['handle'] for r in arrays}
    slots = [r for r in records if r['dmi_type'] == 17 and
             (not handles or r.get('Array Handle') in handles)]
    empty = [r for r in slots if r.get('Size') in ('No Module Installed', 'Not Installed')]
    populated = [r for r in slots if r not in empty and useful(r.get('Size'))]
    rows += [f'Прошивка описывает слотов: {len(slots)}. С модулем: {len(populated)}. Пустых: {len(empty)}.',
             'Данные о слотах сообщает BIOS. Они могут включать распаянную память и недоступные разъёмы.'] if slots else [
             'Прошивка не предоставила сведений о модулях памяти.']
    unknown = len(slots) - len(empty) - len(populated)
    if unknown:
        rows.append(f'Состояние слотов не определено: {unknown}.')
    sections = [('Память: объём и использование', rows)]
    for index, record in enumerate(arrays, 1):
        ecc = record.get('Error Correction Type', 'Unknown')
        ecc = {'None': 'не предусмотрена', 'Single-bit ECC': 'ECC, один бит',
               'Multi-bit ECC': 'ECC, несколько бит', 'Unknown': 'неизвестно'}.get(ecc, ecc)
        sections.append((f'Массив памяти {index}', fields(record, [
            ('Maximum Capacity', 'Предел ёмкости по данным BIOS'), ('Number Of Devices', 'Разъёмов по данным BIOS')]) +
            ['Коррекция ошибок по данным BIOS: ' + ecc]))
    for index, record in enumerate(slots, 1):
        title = 'Слот ' + record.get('Locator', str(index))
        if record in empty:
            sections.append((title + ': пусто', ['Модуль не установлен по данным BIOS.']))
            continue
        title += ': ' + record.get('Size', 'объём неизвестен') + ' ' + record.get('Type', '')
        sections.append((title, fields(record, [
            ('Locator', 'Расположение'), ('Bank Locator', 'Банк'), ('Size', 'Объём'), ('Type', 'Тип памяти'),
            ('Form Factor', 'Формат модуля'), ('Manufacturer', 'Производитель'), ('Part Number', 'Артикул'),
            ('Serial Number', 'Серийный номер'), ('Speed', 'Заявленная скорость'),
            ('Configured Memory Speed', 'Настроенная скорость'), ('Configured Clock Speed', 'Настроенная частота'),
            ('Rank', 'Рангов'), ('Total Width', 'Полная ширина'), ('Data Width', 'Ширина данных'),
            ('Configured Voltage', 'Рабочее напряжение')]) or ['Подробностей нет.']))
    return sections


def cpu_sections(data):
    records = {}
    def visit(entries):
        for item in entries:
            records[item.get('field', '').rstrip(':')] = item.get('data')
            visit(item.get('children', []))
    visit(data.get('lscpu', []))
    main = fields(records, [('Model name', 'Модель'), ('Vendor ID', 'Производитель'),
        ('Architecture', 'Архитектура'), ('CPU(s)', 'Логических процессоров'),
        ('On-line CPU(s) list', 'Включённые процессоры'), ('Socket(s)', 'Сокетов'),
        ('Core(s) per socket', 'Ядер на сокет по данным lscpu'), ('Thread(s) per core', 'Потоков на ядро по данным lscpu'),
        ('CPU max MHz', 'Максимальная частота, мегагерц'), ('CPU min MHz', 'Минимальная частота, мегагерц'),
        ('Virtualization', 'Аппаратная виртуализация'), ('Hypervisor vendor', 'Гипервизор'),
        ('Virtualization type', 'Тип виртуализации'), ('NUMA node(s)', 'Узлов NUMA')])
    cache = fields(records, [(key, key.replace(' cache', ', кэш')) for key in records if 'cache' in key])
    flags = records.get('Flags') or ''
    capabilities = ['64-битные программы: ' + ('да' if 'lm' in flags.split() else 'не указано'),
                    'Виртуализация Intel VT-x или AMD-V: ' + ('да' if {'vmx', 'svm'} & set(flags.split()) else 'не указана'),
                    'Шифрование AES: ' + ('да' if 'aes' in flags.split() else 'не указано')]
    return [('Процессор: основные сведения', main or ['Нет данных.']),
            ('Кэш процессора', cache or ['Нет данных.']), ('Возможности процессора', capabilities)]


def disk_sections(data):
    sections, seen = [], set()
    kinds = {'disk': 'накопитель', 'part': 'раздел', 'crypt': 'открытый зашифрованный том',
             'lvm': 'логический том LVM', 'rom': 'оптический привод', 'loop': 'файл образа'}
    def visit(nodes, parent=''):
        for node in nodes:
            path = node.get('path') or node.get('name') or '?'
            if path not in seen and node.get('type') not in ('loop', 'rom'):
                seen.add(path)
                rows = ['Устройство: ' + path, 'Тип: ' + kinds.get(node.get('type'), node.get('type', '?')),
                        'Объём: ' + size(node.get('size'))]
                if parent:
                    rows.append('Родительское устройство: ' + parent)
                rows += fields(node, [('model', 'Модель'), ('vendor', 'Производитель'), ('serial', 'Серийный номер'),
                    ('rev', 'Версия прошивки'), ('tran', 'Подключение'), ('wwn', 'WWN'), ('fstype', 'Файловая система'),
                    ('fsver', 'Версия файловой системы'), ('label', 'Метка'), ('uuid', 'UUID'), ('partuuid', 'UUID раздела'),
                    ('pttype', 'Таблица разделов'), ('log-sec', 'Логический сектор, байт'),
                    ('phy-sec', 'Физический сектор, байт')])
                if node.get('type') == 'disk' and node.get('rota') is not None:
                    rows.append('Вращающийся носитель по данным драйвера: ' + ('да' if node['rota'] else 'нет'))
                rows.append('Запись разрешена драйвером: ' + ('нет' if node.get('ro') else 'да'))
                mounts = [item for item in node.get('mountpoints', []) if item]
                rows.append('Смонтирован: ' + (', '.join(mounts) if mounts else 'нет'))
                if node.get('fsavail') is not None:
                    rows.append('Свободно в смонтированной файловой системе: ' + size(node['fsavail']))
                if not mounts and node.get('fstype'):
                    rows.append('Свободное место здесь не определяется: файловая система не смонтирована.')
                sections.append((f"{path}: {node.get('model') or node.get('label') or node.get('fstype') or kinds.get(node.get('type'), '')}, {size(node.get('size'))}", rows))
            visit(node.get('children', []), path)
    visit(data.get('blockdevices', []))
    return sections or [('Диски', ['Устройства не найдены.'])]


def report_job(key):
    def run(job):
        failures = []
        successes = []
        def query(name, command, structured=False):
            job.update('Получаю сведения: ' + name)
            try:
                code, raw = job.command(command, timeout=30, accepted=None, log_output=False)
                job.attachments[name + ('.json' if structured else '.txt')] = raw
                if code:
                    failures.append(name)
                parsed = json.loads(raw) if structured else raw
                if code == 0:
                    successes.append(name)
                return parsed
            except (OSError, RuntimeError, ValueError) as error:
                failures.append(name)
                job.log(name + ': ' + str(error))
                return {} if structured else ''
        if key in ('board', 'summary'):
            records = dmi_records(query('dmi-board', ['dmidecode', '-t', '0,1,2']))
            for record in records:
                rows = fields(record, [('Manufacturer', 'Производитель'), ('Product Name', 'Модель'),
                    ('Version', 'Версия'), ('Serial Number', 'Серийный номер'), ('UUID', 'UUID'),
                    ('Vendor', 'Изготовитель BIOS'), ('Release Date', 'Дата BIOS'),
                    ('BIOS Revision', 'Ревизия BIOS'), ('Firmware Revision', 'Ревизия встроенного контроллера')])
                job.sections.append(({0: 'BIOS', 1: 'Компьютер', 2: 'Материнская плата'}[record['dmi_type']], rows or ['Нет данных.']))
            job.sections.append(('Режим загрузки', firmware_state()))
        if key in ('cpu', 'summary'):
            job.sections += cpu_sections(query('cpu', ['lscpu', '-J'], True))
        if key in ('ram', 'summary'):
            job.sections += memory_sections(query('dmi-memory', ['dmidecode', '-t', '16,17']),
                                            query('meminfo', ['cat', '/proc/meminfo']))
        if key in ('disks', 'summary'):
            job.sections += disk_sections(query('disks', ['lsblk', '--tree', '-J', '-b', '-o',
                'PATH,NAME,TYPE,SIZE,MODEL,VENDOR,SERIAL,REV,TRAN,WWN,FSTYPE,FSVER,LABEL,UUID,PARTUUID,PTTYPE,LOG-SEC,PHY-SEC,ROTA,RO,MOUNTPOINTS,FSAVAIL'], True))
        job.incomplete = bool(failures)
        job.failed = not successes
        if failures:
            job.sections.insert(0, ('Недоступные источники', list(dict.fromkeys(failures))))
        job.result = ('Сведения недоступны.' if job.failed else
                      'Сведения получены частично. Откройте недоступные источники.' if failures else 'Сведения готовы.')
    return run
