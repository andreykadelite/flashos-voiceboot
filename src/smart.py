"""Read-only SMART discovery and complete, speech-friendly device reports."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat


@dataclass(frozen=True)
class Target:
    path: str
    driver: str
    title: str
    identity: tuple


def node_identity(path):
    if not path.startswith('/dev/') or '..' in Path(path).parts:
        raise ValueError('Недопустимый путь устройства')
    info = os.stat(path)
    if not (stat.S_ISBLK(info.st_mode) or stat.S_ISCHR(info.st_mode)):
        raise ValueError('Ожидалось устройство накопителя')
    return info.st_dev, info.st_ino, info.st_rdev


def discovery(job, live_disk):
    # The database chooses USB bridge commands; never guess proprietary modes.
    from toolkit import devices
    job.update('Поиск SATA, NVMe, USB и RAID.')
    try:
        code, text = job.command(['smartctl', '--scan-open', '-j'], timeout=45, accepted=None,
                                log_output=False, output_limit=1024**2)
        data = json.loads(text)
    except (OSError, ValueError, RuntimeError):
        code, data = 1, {}
    targets, seen = [], set()
    disks = {item.path: item for item in devices(live_disk, 'disk')}
    def controller(path):
        return re.sub(r'^(\/dev\/nvme\d+)n\d+$', r'\1', path)
    for item in data.get('devices', []):
        path, driver = item.get('name', ''), item.get('type', 'auto')
        if controller(path) == controller(live_disk) or not re.fullmatch(r'[a-z0-9,+/.-]{1,60}', driver):
            continue
        try:
            identity = node_identity(path)
        except (OSError, ValueError):
            continue
        key = (str(Path(path).resolve()), driver)
        if key in seen:
            continue
        seen.add(key)
        disk = next((item for name, item in disks.items() if controller(name) == controller(path)), None)
        title = disk.title if disk else path
        targets.append(Target(path, driver, title + '. ' + driver, identity))
    for path, disk in disks.items():
        if not any(controller(target.path) == controller(path) for target in targets):
            try:
                targets.append(Target(path, 'auto', disk.title, node_identity(path)))
            except (OSError, ValueError):
                code = 1
    job.payload = targets
    job.incomplete = bool(code)
    job.result = f'Найдено накопителей: {len(targets)}.'
    if code:
        job.log('Часть устройств не ответила при поиске.')


FIELDS = {
    'model_name': 'Модель', 'model_family': 'Семейство', 'serial_number': 'Серийный номер',
    'firmware_version': 'Прошивка', 'vendor': 'Производитель', 'product': 'Модель',
    'revision': 'Ревизия', 'rotation_rate': 'Обороты в минуту, 0 означает SSD',
    'logical_block_size': 'Логический сектор, байт', 'physical_block_size': 'Физический сектор, байт',
    'power_on_time': 'Время работы', 'hours': 'Часы', 'minutes': 'Минуты',
    'power_cycle_count': 'Включения', 'power_cycles': 'Включения', 'power_on_hours': 'Часы работы',
    'temperature': 'Температура, градусов Цельсия', 'current': 'Текущая', 'lifetime_min': 'Минимальная за всё время',
    'lifetime_max': 'Максимальная за всё время', 'critical_warning': 'Критические предупреждения',
    'available_spare': 'Доступный резерв, процентов', 'available_spare_threshold': 'Порог резерва, процентов',
    'percentage_used': 'Оценка израсходованного ресурса, процентов',
    'data_units_read': 'Прочитано единиц по 512000 байт', 'data_units_written': 'Записано единиц по 512000 байт',
    'host_reads': 'Команды чтения', 'host_writes': 'Команды записи',
    'controller_busy_time': 'Занятость контроллера, минут', 'unsafe_shutdowns': 'Нештатные отключения',
    'media_errors': 'Ошибки данных и носителя', 'num_err_log_entries': 'Записи журнала ошибок',
    'warning_temp_time': 'Время повышенной температуры, минут', 'critical_comp_time': 'Время критической температуры, минут',
    'temperature_sensors': 'Датчики температуры, градусов Цельсия', 'number': 'Номер',
    'status': 'Состояние', 'string': 'Описание', 'value': 'Значение', 'type': 'Тип',
    'lifetime_hours': 'Часы работы при тесте', 'lba': 'Адрес блока', 'passed': 'Пройдено',
    'read': 'Чтение', 'write': 'Запись', 'verify': 'Проверка', 'total_uncorrected_errors': 'Неисправленные ошибки',
    'total_errors_corrected': 'Исправленные ошибки', 'total_gb_processed': 'Обработано гигабайт',
    'grown_defect_list': 'Добавленные дефекты', 'scsi_grown_defect_list': 'Добавленные дефекты',
    'scsi_non_medium_error_count': 'Ошибки вне носителя', 'scsi_percentage_used_endurance_indicator': 'Израсходованный ресурс, процентов',
    'avail_spare': 'Доступный резерв, процентов', 'spare_thresh': 'Порог резерва, процентов',
    'percent_used': 'Оценка израсходованного ресурса, процентов', 'host_read_commands': 'Команды чтения',
    'host_write_commands': 'Команды записи', 'thm_temp1_trans_count': 'Переходы к первому уровню терморегулирования',
    'thm_temp2_trans_count': 'Переходы ко второму уровню терморегулирования',
    'thm_temp1_total_time': 'Первый уровень терморегулирования, секунд',
    'thm_temp2_total_time': 'Второй уровень терморегулирования, секунд',
    'nvme_smart_health_information_log': 'SMART NVMe', 'scsi_error_counter_log': 'Счётчики ошибок SCSI',
    'ata_device_statistics': 'Статистика ATA', 'ata_smart_error_log': 'Ошибки ATA',
    'ata_smart_extended_error_log': 'Расширенные ошибки ATA', 'ata_smart_self_test_log': 'Самопроверка ATA',
    'nvme_error_information_log': 'Журнал ошибок NVMe', 'nvme_self_test_log': 'Самопроверка NVMe',
    'sn': 'Серийный номер', 'mn': 'Модель', 'fr': 'Прошивка', 'vid': 'Код производителя PCI',
    'ssvid': 'Код производителя подсистемы', 'tnvmcap': 'Полная ёмкость, байт', 'unvmcap': 'Нераспределённая ёмкость, байт',
    'wctemp': 'Порог температурного предупреждения, Кельвинов', 'cctemp': 'Критический порог, Кельвинов',
}
FIELDS.update({f'temperature_sensor_{index}': f'Датчик {index}, градусов Цельсия' for index in range(1, 9)})

ATTRIBUTES = {
    'Raw_Read_Error_Rate': 'Ошибки чтения', 'Reallocated_Sector_Ct': 'Переназначенные секторы',
    'Reallocated_Sector_Count': 'Переназначенные секторы', 'Power_On_Hours': 'Часы работы',
    'Power_Cycle_Count': 'Включения', 'Spin_Retry_Count': 'Повторные раскрутки',
    'Reported_Uncorrect': 'Неисправленные ошибки', 'Command_Timeout': 'Тайм-ауты команд',
    'Reallocated_Event_Count': 'События переназначения', 'Current_Pending_Sector': 'Секторы, ожидающие переназначения',
    'Offline_Uncorrectable': 'Неисправимые секторы', 'UDMA_CRC_Error_Count': 'Ошибки передачи по интерфейсу',
    'Temperature_Celsius': 'Температура', 'Airflow_Temperature_Cel': 'Температура воздушного потока',
    'Wear_Leveling_Count': 'Выравнивание износа', 'Percent_Lifetime_Remain': 'Оставшийся ресурс',
    'SSD_Life_Left': 'Оставшийся ресурс SSD', 'Total_LBAs_Written': 'Записанные логические блоки',
    'Total_LBAs_Read': 'Прочитанные логические блоки', 'Unsafe_Shutdown_Count': 'Нештатные отключения',
}


def flatten(data, prefix='', depth=0):
    """Preserve unknown/vendor fields as well as translated known fields."""
    if depth > 14:
        return [prefix + ': см. полный JSON']
    if isinstance(data, dict):
        rows = []
        for key, value in data.items():
            if key.endswith(('_s', '_le')) and key.rsplit('_', 1)[0] in data:
                continue
            label = FIELDS.get(key, key.replace('_', ' '))
            rows.extend(flatten(value, (prefix + '. ' if prefix else '') + label, depth + 1))
        return rows
    if isinstance(data, list):
        return [row for i, value in enumerate(data) for row in flatten(value, f'{prefix} {i + 1}', depth + 1)]
    value = 'да' if data is True else 'нет' if data is False else 'не указано' if data is None else str(data)
    return [prefix + ': ' + value]


def summary(data):
    state = data.get('smart_status', {}).get('passed')
    rows = ['SMART: ' + ('критических признаков по общей оценке нет' if state is True else
                         'накопитель сообщает неисправность' if state is False else 'оценка состояния недоступна')]
    keys = ['model_name', 'model_family', 'vendor', 'product', 'serial_number', 'firmware_version',
            'rotation_rate', 'logical_block_size', 'physical_block_size', 'power_on_time', 'power_cycle_count', 'temperature']
    rows += flatten({key: data[key] for key in keys if key in data})
    capacity = data.get('user_capacity', {}).get('bytes', data.get('nvme_total_capacity'))
    if capacity is not None:
        rows.append(f'Ёмкость: {int(capacity) / 10**9:.2f} гигабайта')
    support = data.get('smart_support', {})
    if support.get('available') is False:
        rows.append('Устройство или переходник не предоставляет SMART.')
    elif support.get('enabled') is False:
        rows.append('SMART выключен на накопителе. Настройка диска не изменялась.')
    if data.get('in_smartctl_database') is False:
        rows.append('Модель не найдена в базе; смысл некоторых атрибутов зависит от производителя.')
    rows.append('Общая оценка не заменяет просмотр атрибутов и журналов.')
    for message in data.get('smartctl', {}).get('messages', []):
        text = message.get('string', '')
        if 'Self-test Log failed' in text:
            rows.append('Журнал самотестирования не прочитан: накопитель отклонил запрос. Это не результат проваленного теста.')
        else:
            rows.append('Сообщение накопителя: ' + text)
    return rows


def sections(data):
    result = []
    attributes = data.get('ata_smart_attributes', {}).get('table', [])
    if attributes:
        rows = ['Raw — значение производителя; нормализованные значения и единицы могут различаться между моделями.']
        for attr in attributes:
            name = attr.get('name', 'Неизвестный атрибут')
            raw = attr.get('raw', {})
            rows.append(f"ID {attr.get('id')}. {ATTRIBUTES.get(name, name.replace('_', ' '))}. "
                f"Текущее {attr.get('value', 'нет')}, худшее {attr.get('worst', 'нет')}, порог {attr.get('thresh', 'нет')}. "
                f"Raw: {raw.get('string', raw.get('value', 'нет'))}. "
                f"Нарушение порога: {attr.get('when_failed') or 'не отмечено'}.")
        result.append(('Все атрибуты SATA', rows))
    groups = [
        ('Показатели NVMe', ['nvme_smart_health_information_log']),
        ('Статистика SCSI и SAS', [key for key in data if key.startswith('scsi_')]),
        ('Журналы ошибок', ['ata_smart_error_log', 'ata_smart_extended_error_log', 'nvme_error_information_log']),
        ('Журналы самопроверки', ['ata_smart_self_test_log', 'ata_smart_selective_self_test_log', 'nvme_self_test_log']),
        ('Статистика, интерфейс и температуры SATA', ['ata_device_statistics', 'ata_sata_phy_event_counters',
            'ata_sct_status', 'ata_sct_temperature_history', 'ata_sct_erc', 'ata_pending_defects_log']),
    ]
    for label, keys in groups:
        found = {key: data[key] for key in keys if key in data}
        if found:
            # The page already identifies the protocol; do not repeat it on every arrow.
            value = next(iter(found.values()))
            result.append((label, flatten(value if len(found) == 1 and isinstance(value, (dict, list)) else found)))
    covered = {key for _, keys in groups for key in keys} | {'ata_smart_attributes', 'smartctl'}
    result.append(('Идентификация и остальные параметры', flatten({key: value for key, value in data.items() if key not in covered})))
    original = data.get('smartctl', {}).get('output', [])
    if original:
        result.append(('Полный исходный отчёт', original))
    return result


def report_job(target, live_disk):
    def run(job):
        if isinstance(target, Target):
            if node_identity(target.path) != target.identity:
                raise ValueError('Накопитель изменился. Повторите поиск.')
        else:
            from toolkit import check_device
            check_device(target, live_disk)
        driver = getattr(target, 'driver', 'auto')
        job.update('Читаю расширенный SMART.')
        code, text = job.command(['smartctl', '-x', '--json=ov', '-d', driver, target.path],
                                timeout=60, accepted=None, log_output=False, output_limit=4 * 1024**2)
        data = json.loads(text)
        job.attachments['smart.json'] = text
        original = data.get('smartctl', {}).get('output', [])
        job.attachments['smart.txt'] = '\n'.join(original) + '\n'
        job.lines.extend(summary(data))
        job.lines.append('Режим доступа: ' + data.get('device', {}).get('type', driver))
        job.sections = sections(data)
        state = data.get('smart_status', {}).get('passed')
        nvme = data.get('nvme_smart_health_information_log', {})
        critical = state is False or (code > 0 and bool(code & 24)) or bool(nvme.get('critical_warning'))
        job.failed = critical or code < 0 or bool(code & 3)
        job.incomplete = bool(code & 7) or state is None
        job.warning = bool(code & 224) or bool(nvme.get('media_errors'))
        for attribute in data.get('ata_smart_attributes', {}).get('table', []):
            if attribute.get('name') in {'Current_Pending_Sector', 'Offline_Uncorrectable',
                    'Reallocated_Sector_Ct', 'Reallocated_Sector_Count', 'Reported_Uncorrect'}:
                value = attribute.get('raw', {}).get('value')
                if isinstance(value, int) and value > 0:
                    job.warning = True
        if critical:
            job.result = 'SMART: обнаружены признаки неисправности.'
        elif state is None:
            job.result = 'SMART: оценка недоступна. Это не подтверждение неисправности.'
        elif job.incomplete:
            job.result = 'SMART прочитан частично. Общая оценка без критических признаков.'
        elif job.warning:
            job.result = 'SMART: есть предупреждения в показателях или журнале.'
        else:
            job.result = 'SMART: общая оценка без критических признаков.'
        if job.incomplete:
            job.lines.append('Некоторые данные не поддерживаются или не прочитаны. Подробности — в исходном отчёте.')
        if re.fullmatch(r'/dev/nvme\d+(n\d+)?', target.path):
            for name, command in [('nvme-identify', ['nvme', 'id-ctrl', target.path, '-o', 'json']),
                                  ('nvme-health', ['nvme', 'smart-log', target.path, '-o', 'json'])]:
                try:
                    status, raw = job.command(command, timeout=15, accepted=None, log_output=False, output_limit=1024**2)
                    if status == 0:
                        job.attachments[name + '.json'] = raw
                        decoded = json.loads(raw)
                        if name.endswith('health'):
                            # nvme-cli reports raw temperatures in Kelvin; smartctl uses Celsius.
                            decoded = {key: (round(value - 273.15) if value else 'не предоставлена')
                                       if (key == 'temperature' or re.fullmatch(r'temperature_sensor_?\d+', key))
                                       and isinstance(value, (int, float)) else value for key, value in decoded.items()}
                        job.sections.append(('NVMe: ' + ('контроллер' if name.endswith('identify') else 'прямой запрос SMART'),
                                             flatten(decoded)))
                    else:
                        job.log('Дополнительный прямой запрос NVMe недоступен.')
                except (ValueError, OSError, RuntimeError):
                    job.log('Дополнительный запрос NVMe не завершён.')
    return run
