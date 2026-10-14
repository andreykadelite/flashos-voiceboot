"""File rescue from an explicitly selected read-only filesystem to the boot USB."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import shutil
import sys
import uuid

from inventory import size
from medium import medium_info, writable
from toolkit import check_device, devices


MOUNT_TYPES = {'ext2': ('ext2', ''), 'ext3': ('ext3', ',noload'), 'ext4': ('ext4', ',noload'),
    'vfat': ('vfat', ''), 'exfat': ('exfat', ''), 'ntfs': ('ntfs-3g', ',norecover'),
    'xfs': ('xfs', ',norecovery'), 'btrfs': ('btrfs', ',nologreplay,skip_balance')}


def mount_options(fstype):
    if fstype not in MOUNT_TYPES:
        raise ValueError('Этот тип тома пока нельзя открыть в файловом инструменте.')
    driver, options = MOUNT_TYPES[fstype]
    return driver, 'ro,nosuid,nodev,noexec' + options


def worker(job, action, root, relative='.', extra=()):
    result, errors = [], []
    def receive(line, complete=False):
        try:
            data = json.loads(line)
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        if 'progress' in data and not complete:
            job.update(data['progress'])
        if 'result' in data and complete:
            result.append(data['result'])
        if 'error' in data and complete:
            errors.append(data['error'])
    code, output = job.command([sys.executable, str(Path(__file__).with_name('file_worker.py')), action,
        str(root), str(relative), *extra], timeout=3600 if action == 'plan' else 30,
        on_line=receive, accepted=None, log_output=False, resource='storage', output_limit=1024**2)
    # Job's live line callbacks are bounded; a complete page or text preview can
    # be longer. Decode final records from the separately bounded capture.
    for line in output.splitlines():
        receive(line, complete=True)
    if code or not result:
        raise RuntimeError(errors[-1] if errors else 'Прочитать файлы не удалось.')
    return result[-1]


class Volume:
    def __init__(self, device, live_disk):
        self.device, self.live_disk = device, live_disk
        self.root = Path('/run/flashos-files-' + uuid.uuid4().hex)
        self.attempted = False

    def open(self, job):
        check_device(self.device, self.live_disk, unmounted=True)
        driver, options = mount_options(self.device.fstype)
        self.root.mkdir(mode=0o700)
        self.attempted = True
        job.command(['mount', '-t', driver, '-o', options, '--', self.device.path, str(self.root)], timeout=30)
        self.verify(job)
        job.result = 'Раздел открыт только для чтения.'

    def verify(self, job):
        _, raw = job.command(['findmnt', '-J', '-M', str(self.root), '-o', 'SOURCE,OPTIONS'], timeout=5, log_output=False)
        mounted = json.loads(raw)['filesystems'][0]
        if 'ro' not in mounted['options'].split(',') or Path(mounted['source']).resolve() != Path(self.device.path).resolve():
            raise RuntimeError('Не подтверждено открытие выбранного раздела только для чтения.')

    def close(self, job):
        if self.attempted:
            code, _ = job.command(['umount', '--', str(self.root)], timeout=15, accepted=None, cleanup=True)
            # Query the mount table rather than stat a potentially hung filesystem.
            active, _ = job.command(['findmnt', '-n', '-M', str(self.root)], timeout=5,
                                    accepted=None, cleanup=True, log_output=False)
            if active == 0:
                raise RuntimeError('Раздел остаётся подключённым. Закройте другие действия с ним и перезапустите флешку.')
            self.root.rmdir()
            self.attempted = False
        job.result = 'Раздел закрыт.'


@contextmanager
def destination(job, medium, live_disk, category):
    medium = Path(medium)
    source, _ = medium_info(job, medium, live_disk)
    if category not in ('recovery', 'reports-export', 'backups'):
        raise ValueError('Недопустимая папка назначения.')
    with writable(job, medium, live_disk):
        folder = medium / category
        if folder.is_symlink():
            raise ValueError('Папка назначения является ссылкой.')
        folder.mkdir(exist_ok=True)
        _, actual = job.command(['findmnt', '-n', '-o', 'SOURCE', '-T', str(folder)], timeout=5, log_output=False)
        if str(Path(actual.strip()).resolve()) != source or folder.stat().st_dev != medium.stat().st_dev:
            raise ValueError('Папка назначения находится на другом разделе.')
        output = folder / ('files-' + uuid.uuid4().hex[:12])
        output.mkdir()
        job.log('Папка копии: ' + str(output.relative_to(medium)))
        yield output


def copy_job(volume, relative, medium):
    def run(job):
        check_device(volume.device, volume.live_disk)
        volume.verify(job)
        _, fstype = medium_info(job, Path(medium), volume.live_disk)
        options = ['--windows'] if fstype in ('exfat', 'vfat', 'ntfs', 'fuseblk') else []
        if fstype == 'vfat':
            options += ['--fat32']
        job.update('Подсчёт файлов и проверка имён.')
        plan = worker(job, 'plan', volume.root, relative, options)
        required = plan['bytes'] + plan['files'] * 65536 + 256 * 1024**2
        if shutil.disk_usage(medium).free < required:
            raise ValueError('На флешке недостаточно свободного места для выбранных файлов.')
        job.log(f"Файлов: {plan['files']}. Объём: {size(plan['bytes'])}. Пропущено ссылок и специальных файлов: {plan['skipped']}.")
        source = str(volume.root / relative) + ('/' if plan['directory'] else '')
        volume.verify(job)
        with destination(job, medium, volume.live_disk, 'recovery') as output:
            def progress(line):
                match = re.search(r'\s(\d+)%', line)
                if match:
                    job.update('Копирование: ' + match[1] + ' процентов.')
            common = ['rsync', '-rt', '--no-links', '--no-specials', '--no-devices', '--one-file-system', '--protect-args', '--info=nonreg0']
            job.command([*common, '--partial', '--fsync', '--info=progress2', '--', source, str(output) + '/'],
                        timeout=7*24*3600, on_line=progress, log_output=False, capture_output=False)
            job.update('Проверяю содержимое скопированных файлов.')
            volume.verify(job)
            _, differences = job.command([*common, '--checksum', '--dry-run', '--itemize-changes', '--', source, str(output) + '/'],
                                         timeout=7*24*3600, log_output=False)
            # Time precision varies on FAT/exFAT; content/size/type differences matter here.
            changes = [line for line in differences.splitlines() if line and not
                       re.fullmatch(r'\.[fd]\.\.t\.{6} .*', line)]
            if changes:
                job.attachments['copy-differences.txt'] = differences
                raise RuntimeError('Копия сохранена, но контрольное сравнение обнаружило различия. Откройте отчёт.')
            job.warning = bool(plan['skipped'])
            job.payload = str(output)
            job.result = ('Файлы скопированы и проверены по содержимому. ' +
                          (f"Пропущено ссылок и специальных файлов: {plan['skipped']}. " if plan['skipped'] else '') +
                          'Папка: ' + str(output.relative_to(medium)))
    return run


def export_job(state_root, medium, live_disk):
    def run(job):
        source = Path(state_root) / 'flashos/reports'
        if source.is_symlink():
            raise ValueError('Недопустимая папка отчётов.')
        with destination(job, medium, live_disk, 'reports-export') as output:
            job.command(['rsync', '-rt', '--no-links', '--no-specials', '--no-devices', '--one-file-system', '--fsync',
                         '--', str(source) + '/', str(output) + '/'], timeout=600, log_output=False)
            audio = Path(state_root) / 'flashos/audio-last.json'
            exists, _ = job.command(['test', '-f', str(audio)], timeout=5, accepted=None, log_output=False)
            if exists == 0:
                job.command(['rsync', '-t', '--no-links', '--fsync', '--', str(audio), str(output) + '/'],
                            timeout=60, log_output=False)
            job.result = 'Отчёты скопированы: ' + str(output.relative_to(medium)) + '. Исходные отчёты сохранены.'
    return run


def browser_menu(ui, medium, live_disk):
    choices = [(device.title, device, device.details) for device in devices(live_disk, 'filesystem')
               if device.fstype in MOUNT_TYPES and not device.mounted]
    device = ui.choose('Раздел для просмотра файлов', choices)
    if not device:
        return
    volume = Volume(device, live_disk)
    try:
        opened = ui.run_job('Открытие раздела', volume.open, review=False, save=False)
        if opened.failed or opened.cancelled:
            return
        relative, offset = Path('.'), 0
        while True:
            def read(job):
                volume.verify(job)
                job.payload = worker(job, 'list', volume.root, relative, ['--offset', str(offset)])
                job.result = 'Список файлов готов.'
            result = ui.run_job('Чтение папки', read, review=False, save=False)
            if result.failed or result.cancelled:
                return
            entries = [('Скопировать содержимое этой папки на флешку', ('copy', relative)),
                       ('Объём и свободное место раздела', ('space', relative))]
            if relative != Path('.'):
                entries += [('На папку выше', ('parent', relative.parent))]
            entries += [(('Папка ' if row['directory'] else 'Файл ') + row['name'],
                         ('dir' if row['directory'] else 'file', relative / row['name']),
                         'Объём: ' + size(row['bytes'])) for row in result.payload['items']]
            if offset:
                entries += [('Предыдущие 250 имён', ('page', offset - 250))]
            if offset + 250 < result.payload['total']:
                entries += [('Следующие 250 имён', ('page', offset + 250))]
            selected = ui.choose('Файлы: /' + str(relative), entries,
                'Стрелки — выбор. Enter — открыть. F3 — путь и размер. Escape — закрыть раздел. Ссылки и специальные файлы скрыты.')
            if selected is None:
                return
            action, path = selected
            if action in ('dir', 'parent'):
                relative, offset = path, 0
            elif action == 'page':
                offset = path
            elif action == 'space':
                def space(job):
                    data = worker(job, 'space', volume.root)
                    job.result = 'Всего: ' + size(data['total']) + '. Свободно пользователю: ' + size(data['free']) + '.'
                ui.run_job('Место на разделе', space)
            else:
                if action == 'file':
                    action = ui.choose(str(path), [('Скопировать на флешку', 'copy'), ('Прочитать как текст', 'text')])
                if action == 'copy' and ui.confirm('Скопировать ' + str(path) + ' в новую папку recovery на флешке?'):
                    ui.run_job('Копирование файлов', copy_job(volume, path, medium))
                elif action == 'text':
                    def preview(job):
                        data = worker(job, 'text', volume.root, path)
                        job.sections = [(str(path), data['lines'])]
                        job.result = 'Первые 64 КиБ файла.' if data['truncated'] else 'Текст файла.'
                    ui.run_job('Просмотр текста', preview, save=False)
    finally:
        if volume.attempted:
            ui.run_job('Закрытие раздела', volume.close, review=False, save=False)
