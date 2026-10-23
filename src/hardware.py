"""Read available sensors without probing or changing hardware settings."""
import json
import math
from pathlib import Path


LABELS = {'Architecture': 'Архитектура', 'CPU(s)': 'Логические процессоры', 'Model name': 'Модель процессора',
    'Vendor ID': 'Производитель', 'Thread(s) per core': 'Потоков на ядро', 'Core(s) per socket': 'Ядер на сокет',
    'Socket(s)': 'Сокетов', 'CPU max MHz': 'Максимальная частота, мегагерц', 'CPU min MHz': 'Минимальная частота, мегагерц',
    'Virtualization': 'Виртуализация', 'Flags': 'Возможности процессора', 'Manufacturer': 'Производитель',
    'Product Name': 'Модель', 'Version': 'Версия', 'Serial Number': 'Серийный номер', 'Size': 'Размер',
    'Locator': 'Расположение', 'Bank Locator': 'Банк памяти', 'Type': 'Тип', 'Speed': 'Скорость',
    'Configured Memory Speed': 'Настроенная скорость памяти', 'Part Number': 'Артикул',
    'Maximum Capacity': 'Максимальная ёмкость', 'Number Of Devices': 'Количество модулей',
    'Release Date': 'Дата выпуска', 'BIOS Revision': 'Ревизия BIOS', 'Firmware Revision': 'Ревизия прошивки',
    'Error Correction Type': 'Коррекция ошибок', 'Total Width': 'Полная ширина', 'Data Width': 'Ширина данных'}


def readable_lines(text):
    rows = []
    for line in text.splitlines():
        label, separator, value = line.strip().partition(':')
        rows.append(LABELS.get(label, label) + separator + value)
    return rows


def firmware_state(root=Path('/sys/firmware/efi')):
    if not root.is_dir():
        return ['Режим загрузки: BIOS. Secure Boot в этом режиме не действует.']
    rows = ['Режим загрузки: UEFI.']
    for name, title in [('SecureBoot', 'Secure Boot'), ('SetupMode', 'Режим настройки ключей')]:
        try:
            data = (root / 'efivars' / (name + '-8be4df61-93ca-11d2-aa0d-00e098032b8c')).read_bytes()
            rows.append(title + ': ' + ('включён' if data[4] == 1 else 'выключен' if data[4] == 0 else 'неизвестно'))
        except (OSError, IndexError):
            rows.append(title + ': прочитать состояние не удалось.')
    return rows


def battery_lines(battery):
    def read(name):
        try:
            return (battery / name).read_text().strip()
        except OSError:
            return None
    if read('type') != 'Battery':
        return []
    rows = ['Аккумулятор ' + battery.name]
    states = {'Charging': 'заряжается', 'Discharging': 'разряжается', 'Full': 'полностью заряжен',
              'Not charging': 'не заряжается', 'Unknown': 'неизвестно'}
    for field, title in [('manufacturer', 'Производитель'), ('model_name', 'Модель'), ('capacity', 'Заряд, процентов'),
                         ('status', 'Состояние'), ('cycle_count', 'Циклы')]:
        value = read(field)
        if value is not None:
            rows.append(title + ': ' + states.get(value, value))
    for prefix, unit in [('energy', 'ватт часов'), ('charge', 'ампер часов')]:
        try:
            full, design = float(read(prefix + '_full')), float(read(prefix + '_full_design'))
            if design > 0:
                rows += [f'Полная ёмкость: {full / 10**6:.2f} {unit}',
                         f'Паспортная ёмкость: {design / 10**6:.2f} {unit}',
                         f'Отношение полной ёмкости к паспортной: {100 * full / design:.1f} процентов']
                break
        except (TypeError, ValueError):
            continue
    return rows


def temperature_job(job):
    count = 0
    partial = False
    sensor_rows = []
    try:
        status, raw = job.command(['sensors', '-j'], timeout=10, accepted=None, log_output=False)
        job.attachments['sensors.json'] = raw
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('Некорректный формат показаний sensors.')
        if status:
            partial = True
            job.log('Не все показания sensors прочитаны. Код завершения: ' + str(status))
        units = {'temp': ('градусов Цельсия', 1), 'fan': ('оборотов в минуту', 1),
                 'in': ('вольт', 1), 'curr': ('ампер', 1), 'power': ('ватт', 1), 'humidity': ('процентов', 1)}
        limits = {'input': 'Текущее', 'min': 'Минимум', 'max': 'Максимум', 'crit': 'Критический предел',
                  'lcrit': 'Нижний критический предел', 'alarm': 'Тревога', 'crit_alarm': 'Критическая тревога',
                  'fault': 'Ошибка датчика', 'average': 'Среднее'}
        for chip, features in data.items():
            if not isinstance(features, dict):
                partial = True
                continue
            for label, values in features.items():
                if not isinstance(values, dict):
                    continue
                for key, value in values.items():
                    job.check()
                    metric, _, detail = key.partition('_')
                    unit = next((item[0] for prefix, item in units.items() if metric.startswith(prefix)), '')
                    if detail in limits:
                        if not isinstance(value, (int, float)) or isinstance(value, bool):
                            partial = True
                            continue
                        alarm = 'alarm' in detail or detail == 'fault'
                        try:
                            # JSON integers can exceed the float range used by
                            # both isfinite() and the general numeric formatter.
                            if not math.isfinite(value):
                                partial = True
                                continue
                            display = ('да' if value else 'нет') if alarm else f'{value:g} {unit}'
                        except (OverflowError, ValueError):
                            partial = True
                            continue
                        sensor_rows.append(f'{chip}, {label}. {limits[detail]}: {display}')
                        count += detail in ('input', 'average')
                        if alarm and value:
                            job.warning = True
    except (OSError, ValueError, RuntimeError) as error:
        partial = True
        job.log('Основной запрос датчиков недоступен: ' + str(error))
    # ACPI zones may be exposed without a hwmon driver.
    for zone in sorted(Path('/sys/class/thermal').glob('thermal_zone*')):
        job.check()
        try:
            value = int((zone / 'temp').read_text()) / 1000
            label = (zone / 'type').read_text().strip()
            sensor_rows.append(f'Тепловая зона {label}: {value:.1f} градусов Цельсия')
            count += 1
        except (OSError, ValueError):
            pass
    batteries = [row for path in sorted(Path('/sys/class/power_supply').glob('*')) for row in battery_lines(path)]
    if sensor_rows:
        job.sections.append(('Температуры, вентиляторы и напряжения', sensor_rows))
    if batteries:
        job.sections.append(('Аккумуляторы', batteries))
    job.incomplete = partial or (not count and not batteries)
    if job.warning:
        job.result = 'Датчик сообщает тревогу. Проверьте показания.'
    elif count or batteries:
        job.result = 'Показания получены частично.' if partial else 'Показания готовы.'
    else:
        job.result = 'Поддерживаемые датчики не найдены.'
