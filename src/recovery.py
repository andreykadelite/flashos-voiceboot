"""Verified partition backups and file carving, with explicit write targets."""
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

from files import destination, medium_info
from medium import readonly
from toolkit import check_device, devices

ENGINES = {'ext2': 'extfs', 'ext3': 'extfs', 'ext4': 'extfs', 'vfat': 'fat',
           'exfat': 'exfat', 'ntfs': 'ntfs', 'xfs': 'xfs', 'btrfs': 'btrfs'}
MANIFEST = 'flashos-backup.json'
IMAGE = 'partition.pcl'


def image_path(medium, relative):
    relative = Path(relative)
    if relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != 'backups' or not re.fullmatch(r'files-[0-9a-f]{12}', relative.parts[1]):
        raise ValueError('Недопустимая папка резервной копии.')
    path = Path(medium)
    for part in (*relative.parts, IMAGE):
        path = path / part
        if path.is_symlink():
            raise ValueError('Резервная копия содержит ссылку.')
    return path


def read_manifest(image):
    path = image.with_name(MANIFEST)
    if path.is_symlink() or path.stat().st_size > 16384:
        raise ValueError('Недопустимый паспорт образа.')
    data = json.loads(path.read_text(encoding='utf-8'))
    if (data.get('format') != 'flashos-partclone-1' or data.get('file') != IMAGE or
        data.get('fstype') not in ENGINES or type(data.get('source_bytes')) is not int or data['source_bytes'] <= 0 or
        type(data.get('image_bytes')) is not int or data['image_bytes'] <= 0 or
        type(data.get('filesystem_bytes')) is not int or not 0 < data['filesystem_bytes'] <= data['source_bytes'] or
        type(data.get('map_bytes')) is not int or data['map_bytes'] <= 0 or
        any(not re.fullmatch('[0-9a-f]{64}', str(data.get(key, ''))) for key in ('sha256', 'map_sha256', 'used_sha256'))):
        raise ValueError('Паспорт образа неполон или имеет неизвестный формат.')
    return data


def map_extent(path, partition_bytes):
    with path.open(encoding='ascii') as stream:
        for _ in range(32):
            line = stream.readline(4096)
            if not line:
                break
            fields = line.split()
            if len(fields) == 2 and fields[1] == '?':
                extent = int(fields[0], 16)
                if 0 < extent <= partition_bytes:
                    return extent
                break
    raise ValueError('Не удалось определить границу файловой системы в карте Partclone.')


def image_extent(job, image):
    _, raw = job.command(['partclone.info', '-s', str(image), '-L', '/dev/null'],
                        timeout=30, log_output=False, resource='storage')
    blocks = re.search(r'^Device size:.*?=\s*(\d+) Blocks\s*$', raw, re.M)
    block_size = re.search(r'^Block size:\s*(\d+) Byte\s*$', raw, re.M)
    if not blocks or not block_size or not re.search(r'^image format:\s*0002\s*$', raw, re.M):
        raise ValueError('Не удалось прочитать точные размеры образа Partclone.')
    return int(blocks[1]) * int(block_size[1])


def digest(job, path, expected_size):
    results = []
    def receive(line):
        data = json.loads(line)
        if 'percent' in data:
            job.update(f"Проверка SHA256: {data['percent']} процентов.")
        if 'sha256' in data:
            results.append(data['sha256'])
        if 'error' in data:
            raise ValueError(data['error'])
    job.command([sys.executable, str(Path(__file__).with_name('hash_worker.py')), str(path), str(expected_size)],
                timeout=7*24*3600, on_line=receive, log_output=False, resource='storage')
    if len(results) != 1:
        raise ValueError('Контрольная сумма не получена.')
    return results[0]


def used_digest(job, map_path, device, size):
    results = []
    def receive(line):
        data = json.loads(line)
        if 'percent' in data:
            job.update(f"Сверка занятых блоков: {data['percent']} процентов раздела.")
        if 'sha256' in data:
            results.append(data['sha256'])
        if 'error' in data:
            raise ValueError(data['error'])
    job.command([sys.executable, str(Path(__file__).with_name('restore_verify.py')), str(map_path), device, str(size)],
                timeout=7*24*3600, on_line=receive, log_output=False, resource='storage')
    if len(results) != 1:
        raise ValueError('Сверка блоков не завершена.')
    return results[0]


def progress(job, prefix):
    def receive(line):
        matches = re.findall(r'([0-9]+(?:\.[0-9]+)?)%', line)
        if matches:
            job.update(prefix + ': ' + matches[-1] + ' процентов.')
    return receive


def backup_job(device, live, medium):
    def run(job):
        check_device(device, live, unmounted=True)
        if device.kind != 'part' or device.fstype not in ENGINES:
            raise ValueError('Выберите раздел с поддерживаемой файловой системой.')
        _, fstype = medium_info(job, Path(medium), live)
        # Worst case includes bitmap, checksums and alignment, without guessing used space.
        required = device.size + device.size // 100 + 256 * 1024**2
        if shutil.disk_usage(medium).free < required or (fstype == 'vfat' and required >= 2**32):
            raise ValueError('Для безопасного создания образа недостаточно места либо достигнут предел FAT32.')
        with destination(job, medium, live, 'backups') as folder:
            image = folder / IMAGE
            check_device(device, live, unmounted=True)
            block_map = folder / 'blocks.map'
            job.command(['partclone.' + ENGINES[device.fstype], '-D', '-s', device.path, '-o', str(block_map),
                '-L', str(folder / 'domain.log')], timeout=3600, log_output=False, resource='storage')
            extent = map_extent(block_map, device.size)
            reference = used_digest(job, block_map, device.path, extent)
            job.command(['partclone.' + ENGINES[device.fstype], '-c', '-s', device.path, '-o', str(image),
                '-L', str(folder / 'partclone.log'), '-f', '2', '-B'], timeout=7*24*3600,
                on_line=progress(job, 'Создание образа'), log_output=False, resource='storage', capture_output=False)
            job.update('Проверка блоков резервного образа.')
            job.command(['partclone.chkimg', '-s', str(image), '-L', str(folder / 'check.log')],
                        timeout=7*24*3600, log_output=False, resource='storage', capture_output=False)
            length = image.stat().st_size
            if image_extent(job, image) != extent:
                raise ValueError('Размер файловой системы в образе и карте отличается.')
            checksum = digest(job, image, length)
            if used_digest(job, block_map, device.path, extent) != reference:
                raise ValueError('Исходные данные изменились во время копирования. Образ не подтверждён.')
            job.command(['sync', '-f', str(image)], timeout=120, resource='storage')
            data = {'format': 'flashos-partclone-1', 'file': IMAGE, 'source_bytes': device.size,
                'fstype': device.fstype, 'source': device.path, 'model': device.model, 'serial': device.serial,
                'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'image_bytes': length, 'sha256': checksum,
                'filesystem_bytes': extent,
                'map_bytes': block_map.stat().st_size,
                'map_sha256': digest(job, block_map, block_map.stat().st_size), 'used_sha256': reference}
            job.check()
            with (folder / MANIFEST).open('x', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            job.command(['sync', '-f', str(folder)], timeout=120, resource='storage')
            job.payload = str(folder.relative_to(medium))
            job.result = 'Образ создан. Блоки и SHA256 проверены. Папка: ' + job.payload
    return run


def verify_image(job, medium, relative, live):
    medium_info(job, Path(medium), live)
    image = image_path(medium, relative)
    data = read_manifest(image)
    block_map = image.with_name('blocks.map')
    if digest(job, block_map, data['map_bytes']) != data['map_sha256']:
        raise ValueError('Карта блоков повреждена. Запись запрещена.')
    from restore_verify import ranges
    if not sum(1 for _ in ranges(block_map, data['filesystem_bytes'])):
        raise ValueError('Карта блоков пуста.')
    if digest(job, image, data['image_bytes']) != data['sha256']:
        raise ValueError('SHA256 не совпадает. Образ повреждён или изменён. Запись запрещена.')
    job.update('Проверка внутренних контрольных сумм образа.')
    job.command(['partclone.chkimg', '-s', str(image), '-L', '/dev/null'],
                timeout=7*24*3600, log_output=False, resource='storage', capture_output=False)
    if image_extent(job, image) != data['filesystem_bytes']:
        raise ValueError('Размер образа не совпадает с паспортом. Запись запрещена.')
    job.result = 'Образ прочитан полностью. SHA256 и внутренние контрольные суммы совпадают.'
    return image, data


def restore_job(relative, device, live, medium):
    def run(job):
        if device.kind != 'part':
            raise ValueError('Восстановление разрешено только на отдельный раздел.')
        check_device(device, live, unmounted=True)
        # Keep the image filesystem read-only throughout verification and restoration.
        with readonly(job, medium, live):
            image, data = verify_image(job, medium, relative, live)
            if device.size < data['source_bytes']:
                raise ValueError('Целевой раздел меньше исходного. Запись не начата.')
            check_device(device, live, unmounted=True)
            job.check()
            job.log('Целевой раздел: ' + device.title + '. ' + device.details)
            job.log('После начала записи отмена оставляет раздел частично восстановленным.')
            job.cancel_result = 'Восстановление остановлено. Целевой раздел может быть записан частично. Не загружайте его; повторите восстановление из проверенного образа.'
            job.command(['partclone.restore', '-s', str(image), '-O', device.path, '-L', '/dev/null', '-f', '2', '-B'],
                timeout=7*24*3600, on_line=progress(job, 'Восстановление'), log_output=False, resource='storage', capture_output=False)
            job.command(['blockdev', '--flushbufs', device.path], timeout=120, resource='storage')
            # Re-read the written used blocks, checking them against the source image's checksums.
            job.update('Повторное чтение восстановленных данных.')
            if used_digest(job, image.with_name('blocks.map'), device.path, data['filesystem_bytes']) != data['used_sha256']:
                raise RuntimeError('Запись завершилась, но данные на целевом разделе отличаются от исходных. Восстановление не подтверждено.')
            job.result = 'Раздел восстановлен. Записанные блоки повторно прочитаны и совпадают с образом. UUID файловой системы скопирован из образа.'
            try:
                code, detail = job.command(['udevadm', 'trigger', '--action=change', '--settle',
                    '/sys/class/block/' + Path(device.path).name], timeout=30, accepted=None, log_output=False)
            except (OSError, RuntimeError) as error:
                code, detail = 1, str(error)
            if code:
                job.warning = True
                job.result += ' Сведения об устройстве не обновились. Перезапустите флешку перед следующей работой с разделом.'
                job.log(detail)
    return run


def catalog_job(medium, live):
    def run(job):
        medium_info(job, Path(medium), live)
        entries = []
        root = Path(medium) / 'backups'
        if root.is_symlink():
            raise ValueError('Папка backups является ссылкой.')
        if root.exists():
            for folder in sorted(root.iterdir()):
                if len(entries) >= 500:
                    job.warning = True
                    break
                try:
                    relative = str(folder.relative_to(medium))
                    image = image_path(medium, relative)
                    data = read_manifest(image)
                    entries.append((data['created'] + '. ' + str(data['model']) + '. ' + data['fstype'] + '. ' + folder.name,
                                    relative))
                except (OSError, ValueError, KeyError):
                    job.log('Незавершённая или неизвестная копия: ' + folder.name)
        job.payload = entries
        job.result = f'Копий с паспортом: {len(entries)}. Целостность проверяется отдельно.'
    return run


def photorec_job(device, live, medium):
    def run(job):
        if device.kind != 'part':
            raise ValueError('Выберите отдельный раздел для поиска файлов.')
        check_device(device, live, unmounted=True)
        medium_info(job, Path(medium), live)
        if shutil.disk_usage(medium).free < device.size + 256 * 1024**2:
            raise ValueError('Для поиска файлов требуется свободное место не меньше размера исходного раздела плюс 256 МиБ.')
        with destination(job, medium, live, 'recovery') as folder:
            check_device(device, live, unmounted=True)
            options = 'options,mode_ext2,' if device.fstype in ('ext2', 'ext3', 'ext4') else ''
            job.update('Поиск файлов по содержимому. Исходный раздел не изменяется.')
            job.command(['photorec', '/log', '/d', str(folder / 'recovered'), '/cmd', device.path,
                'partition_none,' + options + 'fileopt,everything,enable,search'],
                timeout=7*24*3600, log_output=False, cwd=str(folder), resource='storage', capture_output=False)
            job.command(['sync', '-f', str(folder)], timeout=120, resource='storage')
            from files import worker
            plan = worker(job, 'plan', folder)
            job.warning = True
            job.result = ('Поиск PhotoRec завершён. Файлов в папке результата, включая журнал: ' + str(plan['files']) +
                '. Папка: ' + str(folder.relative_to(medium)) + '. Имена и папки исходных файлов не сохраняются. Откройте найденные файлы для проверки.')
    return run


def repair_job(device, live):
    def run(job):
        check_device(device, live, unmounted=True)
        commands = {'ext2': ['e2fsck', '-p'], 'ext3': ['e2fsck', '-p'], 'ext4': ['e2fsck', '-p'],
                    'vfat': ['fsck.fat', '-a'], 'exfat': ['fsck.exfat', '-p']}
        if device.kind != 'part' or device.fstype not in commands:
            raise ValueError('Автоисправление доступно для ext2/3/4, FAT и exFAT. NTFS ремонтируйте штатным chkdsk в Windows.')
        job.update('Исправление файловой системы. Не выключайте компьютер.')
        job.cancel_result = 'Ремонт остановлен. Файловая система могла измениться. Сохраните данные и проверьте раздел перед использованием.'
        code, output = job.command([*commands[device.fstype], device.path], timeout=24*3600, accepted=None)
        job.attachments['filesystem-repair.txt'] = output
        job.command(['blockdev', '--flushbufs', device.path], timeout=120, resource='storage')
        accepted = (0, 1) if device.fstype == 'vfat' else (0, 1, 2)
        if code not in accepted:
            raise RuntimeError('Автоматическое исправление не завершено. Код ' + str(code) + '. Откройте журнал; не повторяйте ремонт без копии данных.')
        checks = {'vfat': ['fsck.fat', '-n'], 'exfat': ['fsck.exfat', '-n']}
        verify_code, verify_output = job.command([*checks.get(device.fstype, ['e2fsck', '-f', '-n']), device.path],
                                                timeout=24*3600, accepted=None)
        job.attachments['filesystem-recheck.txt'] = verify_output
        if verify_code:
            raise RuntimeError('Повторная проверка обнаружила оставшиеся ошибки. Ремонт не подтверждён. Сохраните журналы.')
        job.warning = code != 0
        job.result = 'Проверка с исправлением завершена.' + (' Были изменения. Перезапустите флешку перед следующими действиями с разделом.' if code else ' Изменения не потребовались.')
    return run


def recovery_menu(ui, medium, live):
    while True:
        action = ui.choose('Резервные копии и восстановление', [('Создать образ раздела', 'backup'),
            ('Проверить резервный образ', 'verify'), ('Восстановить раздел из образа', 'restore'),
            ('Найти удалённые файлы через PhotoRec', 'carve'), ('Исправить файловую систему', 'repair')])
        if action is None:
            return
        if ui.demo:
            ui.message('Демонстрация', 'Операции с разделами доступны после загрузки с флешки.')
            continue
        relative = None
        if action in ('verify', 'restore'):
            listing = ui.run_job('Поиск резервных копий', catalog_job(medium, live), review=False, save=False)
            if listing.failed or listing.cancelled:
                continue
            relative = ui.choose('Резервный образ', listing.payload)
            if not relative:
                continue
            if action == 'verify':
                ui.run_job('Проверка резервного образа', lambda job: verify_image(job, medium, relative, live))
                continue
        available = [d for d in devices(live, 'part') if not d.mounted]
        if action == 'backup':
            available = [d for d in available if d.fstype in ENGINES]
        if action == 'repair':
            available = [d for d in available if d.fstype in ('ext2', 'ext3', 'ext4', 'vfat', 'exfat')]
        device = ui.choose('Целевой раздел: данные будут заменены' if action == 'restore' else 'Выбор раздела',
                           [(d.title, d, d.details) for d in available])
        if not device:
            continue
        prompt = {'backup': 'Создать проверяемую копию этого раздела на флешке?',
                  'carve': 'Найти файлы на этом разделе и сохранить найденное на флешку?',
                  'repair': 'Исправление изменит файловую систему. Сначала сохраните образ или важные файлы. Продолжить?',
                  'restore': 'Все данные целевого раздела будут заменены содержимым образа ' + str(relative) + '. При остановке раздел останется неполным. Продолжить?'}[action]
        if ui.confirm(device.title + '. ' + device.details + '. ' + prompt):
            functions = {'backup': lambda: backup_job(device, live, medium),
                         'restore': lambda: restore_job(relative, device, live, medium),
                         'carve': lambda: photorec_job(device, live, medium),
                         'repair': lambda: repair_job(device, live)}
            ui.run_job({'backup': 'Создание резервного образа', 'restore': 'Восстановление раздела',
                        'carve': 'Поиск удалённых файлов', 'repair': 'Исправление файловой системы'}[action], functions[action]())
