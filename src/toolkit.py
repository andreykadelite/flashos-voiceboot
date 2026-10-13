"""Hardware reports and explicitly selected diagnostics. Internal disks are read only."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

from medium import writable
from smart import summary as smart_summary, report_job as smart_job
from hardware import temperature_job, readable_lines, firmware_state
from network import network_job


REPORTS = {
    'summary': ('Сведения о компьютере', [['lshw', '-short'], ['lscpu'], ['free', '-h']]),
    'cpu': ('Процессор', [['lscpu']]),
    'ram': ('Память и модули', [['free', '-h'], ['dmidecode', '-t', 'memory']]),
    'board': ('Материнская плата и BIOS', [['dmidecode', '-t', 'system'], ['dmidecode', '-t', 'baseboard'], ['dmidecode', '-t', 'bios']]),
    'disks': ('Диски и разделы', [['lsblk', '-o', 'NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINTS,MODEL'], ['blkid']]),
    'pci': ('Устройства PCI и драйверы', [['lspci', '-nnk']]),
    'usb': ('Устройства USB', [['lsusb'], ['lsusb', '-t']]),
    'video': ('Видеокарты и дисплеи', [['lshw', '-class', 'display']]),
    'audio': ('Звуковые устройства', [['aplay', '-l'], ['wpctl', 'status']]),
    'network': ('Сеть, адреса и маршруты', [['ip', '-br', 'link'], ['ip', '-br', 'address'], ['ip', 'route'], ['ip', '-6', 'route'], ['iw', 'dev'], ['rfkill', 'list']]),
    'storage': ('Контроллеры, RAID и составные тома', [['lsscsi', '-g', '-s'], ['lsblk', '--tree', '-o', 'NAME,TYPE,SIZE,FSTYPE,UUID,PKNAME,MOUNTPOINTS'], ['cat', '/proc/mdstat']]),
    'firmware': ('Загрузка UEFI и Secure Boot', [['efibootmgr', '-v'], ['uname', '-a']]),
    'kernel': ('Ошибки ядра', [['journalctl', '-k', '-b', '-p', 'warning', '--no-pager', '-n', '120']]),
    'services': ('Состояние служб', [['systemctl', '--failed', '--no-pager'], ['uptime'], ['df', '-h']]),
}


def report_job(key):
    if key in ('summary', 'cpu', 'ram', 'board', 'disks'):
        from inventory import report_job as inventory_job
        return inventory_job(key)
    def run(job):
        failures = 0
        if key == 'firmware':
            job.lines.extend(firmware_state())
        for command in REPORTS[key][1]:
            job.check()
            job.update('Читаю: ' + command[0])
            try:
                code, text = job.command(command, timeout=30, accepted=None, log_output=False)
                rows = readable_lines(text)
            except (OSError, RuntimeError) as error:
                code, text, rows = 1, str(error), [str(error)]
            titles = {'lshw': 'Устройства', 'lscpu': 'Параметры процессора', 'free': 'Использование памяти',
                      'dmidecode': 'Данные прошивки: ' + command[-1], 'lsblk': 'Диски и разделы',
                      'blkid': 'Файловые системы и идентификаторы', 'lspci': 'PCI и драйверы', 'lsusb': 'USB',
                      'aplay': 'Звуковые карты', 'wpctl': 'Звуковые выходы', 'ip': 'Сетевые параметры',
                      'iw': 'Беспроводные устройства', 'rfkill': 'Блокировки радио', 'lsscsi': 'Контроллеры и накопители',
                      'cat': 'Состояние RAID', 'efibootmgr': 'Записи загрузки', 'uname': 'Версия ядра',
                      'journalctl': 'Журнал ошибок', 'systemctl': 'Неисправные службы', 'uptime': 'Время работы', 'df': 'Свободное место'}
            job.sections.append((titles.get(command[0], command[0]) + ('. Недоступно' if code else ''), rows or ['Нет записей.']))
            job.attachments[f'{len(job.sections)}-{command[0]}.txt'] = text
            if code:
                failures += 1
        total = len(REPORTS[key][1])
        job.failed = failures == total
        job.incomplete = bool(failures)
        job.result = ('Сведения недоступны.' if job.failed else
                      f'Получено разделов: {total - failures} из {total}.' if failures else 'Сведения готовы.')
    return run


@dataclass(frozen=True)
class Device:
    path: str
    kind: str
    size: int
    model: str
    serial: str
    fstype: str
    mounted: bool
    identity: str
    disk: str
    fingerprint: tuple = ()

    @property
    def title(self):
        number = self.serial or next((value for value in self.fingerprint[:3] if value), '')
        return (f'{self.model or self.fstype or self.kind}, {self.size / 1024**3:.2f} гибибайта. {Path(self.path).name}'
                + ('. Номер оканчивается на ' + number[-6:] if number else '') + ('; используется' if self.mounted else ''))

    @property
    def details(self):
        identifiers = [('Серийный номер', self.serial)] + list(zip(('WWN', 'UUID', 'PARTUUID'), self.fingerprint[:3]))
        return '. '.join(title + ': ' + value for title, value in identifiers if value) + '. Файловая система: ' + (self.fstype or 'не определена')


def devices(live_disk, kind=None):
    data = json.loads(subprocess.check_output(['lsblk', '--tree', '-J', '-b', '-p', '-o',
        'PATH,KNAME,TYPE,SIZE,MODEL,SERIAL,WWN,UUID,PARTUUID,FSTYPE,MOUNTPOINTS,MAJ:MIN'], text=True, timeout=5))
    result, blocked = {}, set()
    def descendants(node, exclude=False):
        exclude = exclude or node['path'] == live_disk
        if exclude:
            blocked.add(node['path'])
        for child in node.get('children', []):
            descendants(child, exclude)
    for node in data['blockdevices']:
        descendants(node)
    def walk(node, disk='', model='', serial='', sequence=''):
        if node.get('type') == 'disk':
            disk = node['path']
            model, serial = node.get('model') or '', node.get('serial') or ''
            try:
                sequence = Path('/sys/class/block', Path(node.get('kname') or disk).name, 'diskseq').read_text().strip()
            except OSError:
                sequence = ''
        valid_type = node.get('type') in ('disk', 'part', 'lvm', 'crypt', 'mpath', 'md') or node.get('type', '').startswith('raid')
        wanted = (not kind or kind == node['type'] or (kind == 'filesystem' and bool(node.get('fstype'))))
        if valid_type and node['path'] not in blocked and wanted:
            result[node['path']] = Device(node['path'], node['type'], int(node.get('size') or 0), model, serial,
                node.get('fstype') or '', any(node.get('mountpoints') or []), str(node.get('maj:min')), disk,
                (node.get('wwn'), node.get('uuid'), node.get('partuuid'), sequence))
        for child in node.get('children', []):
            walk(child, disk, model, serial, sequence)
    for node in data['blockdevices']:
        walk(node)
    return list(result.values())


def check_device(device, live_disk, unmounted=False):
    current = next((item for item in devices(live_disk) if item.path == device.path), None)
    if current is None or (current.size, current.identity, current.serial, current.fstype, current.fingerprint) != (
            device.size, device.identity, device.serial, device.fstype, device.fingerprint):
        raise ValueError('Устройство изменилось. Выберите его заново.')
    if not stat.S_ISBLK(os.stat(device.path).st_mode):
        raise ValueError('Ожидалось блочное устройство')
    if unmounted:
        assert_unused(device.path)
    return current


def assert_unused(path):
    data = json.loads(subprocess.check_output(['lsblk', '--tree', '-J', '-p', '-o',
        'PATH,TYPE,MOUNTPOINTS', path], text=True, timeout=5))
    def used(node):
        holders = Path('/sys/class/block', Path(node['path']).name, 'holders')
        return (any(node.get('mountpoints') or []) or
                (holders.is_dir() and any(holders.iterdir())) or
                any(used(child) for child in node.get('children', [])))
    if any(used(node) for node in data['blockdevices']):
        raise ValueError('Накопитель или его разделы используются системой. Операция отменена.')


def memory_budget(mode, meminfo=None):
    text = meminfo if meminfo is not None else Path('/proc/meminfo').read_text()
    match = re.search(r'^MemAvailable:\s+(\d+) kB', text, re.M)
    if not match:
        raise ValueError('Не удалось определить свободную память')
    available = int(match[1]) // 1024
    budget = min(available * 3 // 4, available - 768)
    if budget < 32:
        raise ValueError('Недостаточно свободной памяти для теста с работающей озвучкой')
    return min(budget, {'quick': 128, 'normal': 1024, 'extended': budget}[mode])


MEMORY_STAGES = {'Stuck Address': 'Адреса памяти', 'Random Value': 'Случайные значения',
    'Compare XOR': 'Исключающее ИЛИ', 'Compare SUB': 'Вычитание', 'Compare MUL': 'Умножение',
    'Compare DIV': 'Деление', 'Compare OR': 'Побитовое ИЛИ', 'Compare AND': 'Побитовое И',
    'Sequential Increment': 'Последовательное увеличение', 'Solid Bits': 'Сплошные биты',
    'Block Sequential': 'Последовательные блоки', 'Checkerboard': 'Шахматный узор',
    'Bit Spread': 'Разнесённые биты', 'Bit Flip': 'Инверсия битов', 'Walking Ones': 'Бегущие единицы',
    'Walking Zeroes': 'Бегущие нули', '8-bit Writes': 'Запись по 8 бит', '16-bit Writes': 'Запись по 16 бит'}


def memory_job(mode):
    def run(job):
        size = memory_budget(mode)
        loops = 3 if mode == 'extended' else 1
        job.log(f'Проверяется {size} мегабайт, проходов: {loops}. Память системы и прошивки недоступна этому тесту.')
        job.update(f'Проверка {size} мегабайт памяти. Подготовка.')
        locked = False
        tested_size = size
        def parse(line):
            nonlocal locked, tested_size
            actual = re.search(r'got\s+(\d+)MB', line)
            if actual:
                tested_size = int(actual[1])
            if 'locked.' in line and 'unlocked' not in line:
                locked = True
            if 'FAILURE:' in line:
                job.error_count += 1
                job.update('Обнаружена ошибка памяти. Подробности записываются в отчёт.')
            if 'Loop ' in line:
                job.update('Проход ' + line.split('Loop ', 1)[1].strip(' :'))
            for english, russian in MEMORY_STAGES.items():
                if english in line:
                    job.update(russian + ('. Пройдено.' if 'ok' in line else '. Проверка.'))
        code, _ = job.command(['stdbuf', '-oL', '-eL', 'memtester', f'{size}M', str(loops)],
                             timeout=24 * 3600, on_line=parse, accepted=None)
        if (code > 0 and code & 6) or job.error_count:
            job.failed = True
            job.result = 'Обнаружены ошибки памяти. Сохраните отчёт и проверьте модули отдельно.'
        elif code or not locked:
            job.failed = True
            job.result = 'Тест не завершён достоверно: не удалось выделить или закрепить память. Это не подтверждение неисправности.'
        else:
            job.result = f'Проверка завершена: {tested_size} мегабайт, проходов {loops}. Ошибок в проверенной области не обнаружено.'
    return run


def cpu_job(seconds):
    def run(job):
        job.update(f'Нагрузка процессора, {seconds} секунд. Escape — остановка.')
        job.command(['stress-ng', '--cpu', '0', '--cpu-method', 'all', '--verify',
                     '--timeout', f'{seconds}s', '--metrics-brief'], timeout=seconds + 30)
        job.result = 'Нагрузочная проверка процессора завершена без сообщения об ошибках.'
    return run


def filesystem_job(device, live_disk):
    def run(job):
        check_device(device, live_disk, unmounted=True)
        fs = device.fstype
        if fs in ('ext2', 'ext3', 'ext4'):
            command = ['e2fsck', '-f', '-n', device.path]
        elif fs in ('vfat', 'fat', 'fat32'):
            command = ['fsck.fat', '-n', device.path]
        elif fs == 'exfat':
            command = ['fsck.exfat', '-n', device.path]
        elif fs == 'ntfs':
            command = ['ntfsfix', '-n', device.path]
            job.log('NTFS: только базовая проверка метаданных. Это не полная проверка chkdsk.')
        elif fs == 'xfs':
            command = ['xfs_repair', '-n', device.path]
        elif fs == 'btrfs':
            command = ['btrfs', 'check', '--readonly', device.path]
        else:
            raise ValueError('Эта файловая система не поддерживается проверкой без исправления')
        job.update('Проверка файловой системы. Исправление отключено.')
        code, _ = job.command(command, timeout=24 * 3600, accepted=None)
        job.failed = bool(code)
        job.result = ('Проверка завершена без сообщений об ошибках.' if code == 0 else
                      'Проверка завершилась с замечаниями или ошибкой. Смотрите отчёт; изменения не вносились.')
    return run


def read_job(device, live_disk):
    def run(job):
        check_device(device, live_disk)
        job.log('Выборочная проверка чтения: 32 участка по 1 мегабайту. Данные не изменяются.')
        block = min(device.size, 1024**2)
        if not block:
            raise ValueError('Неизвестен размер устройства')
        for index in range(32):
            job.check()
            offset = ((device.size - block) * index // 31) // 4096 * 4096
            _, text = job.command(['dd', 'if=' + device.path, 'of=/dev/null', 'bs=64K',
                'iflag=fullblock,skip_bytes,count_bytes', f'skip={offset}', f'count={block}'], timeout=30)
            transferred = re.search(r'^(\d+) bytes?', text, re.M)
            if not transferred or int(transferred[1]) != block:
                raise OSError('Не удалось прочитать участок полностью')
            job.update(f'Прочитано участков: {index + 1} из 32')
        job.result = 'Все 32 выбранных участка прочитаны. Это выборочный тест, не проверка всей поверхности.'
    return run


def checksum_job(image):
    def run(job):
        result = {}
        def progress(line):
            data = json.loads(line)
            result.update(data)
            if 'percent' in data:
                job.update(f"Проверка образа: {data['percent']} процентов")
            if 'error' in data:
                job.log(data['error'])
        job.command([sys.executable, str(Path(__file__).with_name('hash_worker.py')), str(image.path), str(image.size)],
                    timeout=24 * 3600, log_output=False, on_line=progress, resource='storage')
        value = result.get('sha256')
        if not value:
            raise ValueError('Контрольная сумма не получена.')
        job.log('Образ: ' + image.path.name)
        job.log('SHA256: ' + value)
        sidecar = image.path.with_name(image.path.name + '.sha256')
        if sidecar.is_file() and not sidecar.is_symlink() and sidecar.stat().st_size < 4096:
            tokens = sidecar.read_text().split()
            expected = tokens[0].lower() if tokens else ''
            if re.fullmatch('[0-9a-f]{64}', expected):
                job.failed = value != expected
                job.result = 'Контрольная сумма ' + ('совпадает.' if not job.failed else 'НЕ совпадает. Образ может быть повреждён.')
                return
        job.incomplete = True
        job.result = 'SHA256 вычислена. Подходящего эталона рядом с ISO нет; целостность по эталону не подтверждена.'
    return run


def backup_table_job(device, live_disk):
    def run(job):
        check_device(device, live_disk)
        job.command(['sfdisk', '--dump', device.path], timeout=15)
        job.result = 'Таблица разделов сохранена в текстовом отчёте. Это не резервная копия файлов.'
    return run


def rescue_job(device, live_disk, medium):
    medium = Path(medium)
    def run(job):
        check_device(device, live_disk, unmounted=True)
        mounted = json.loads(subprocess.check_output(['findmnt', '-J', '-M', str(medium), '-o', 'SOURCE,FSTYPE'], text=True, timeout=5))
        if mounted['filesystems'][0].get('fstype') == 'vfat' and device.size >= 4 * 1024**3:
            raise ValueError('FAT32 не поддерживает файл такого размера. Для образа нужен раздел exFAT или NTFS.')
        source = str(Path(mounted['filesystems'][0]['source']).resolve())
        parent = subprocess.check_output(['lsblk', '-ndo', 'PKNAME', source], text=True, timeout=5).strip()
        if '/dev/' + parent != live_disk or not (medium / 'flashos/flashos.marker').is_file():
            raise ValueError('Не подтверждён раздел назначения на загрузочной флешке')
        if shutil.disk_usage(medium).free < device.size + 256 * 1024**2:
            raise ValueError('На флешке недостаточно места для полного образа выбранного устройства')
        job.check()
        with writable(job, medium, live_disk):
            folder = medium / 'recovery'
            if folder.is_symlink():
                raise ValueError('Недопустимая папка назначения')
            folder.mkdir(exist_ok=True)
            destination = subprocess.check_output(['findmnt', '-n', '-o', 'SOURCE', '-T', str(folder)], text=True, timeout=5).strip()
            if folder.stat().st_dev != medium.stat().st_dev or str(Path(destination).resolve()) != source:
                raise ValueError('Папка recovery принадлежит другому разделу. Копирование отменено.')
            output = folder / (Path(device.path).name + '-' + uuid.uuid4().hex[:10] + '.img')
            job.log('Образ: ' + str(output))
            job.log('Карта восстановления: ' + str(output) + '.map')
            job.update('Копирование в образ. Частичный файл и карта сохранятся при остановке.')
            def progress(line):
                match = re.search(r'pct rescued:\s*([0-9.]+)%', line)
                if match:
                    job.update('Скопировано ' + match[1] + ' процентов.')
            job.command(['ddrescue', '-n', '--', device.path, str(output), str(output) + '.map'],
                        timeout=7 * 24 * 3600, on_line=progress, capture_output=False)
            job.command(['ddrescuelog', '--show-status', str(output) + '.map'], timeout=10)
            status, _ = job.command(['ddrescuelog', '--done-status', str(output) + '.map'], timeout=10, accepted=None)
            job.incomplete = status != 0
            job.result = ('Копирование завершено: по карте прочитаны все области.' if status == 0 else
                          'Образ неполный: остались непрочитанные области. Файл и карта сохранены.')
    return run
