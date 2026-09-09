"""Select installed UEFI loaders without changing the permanent boot order."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import tempfile


def mounted_at(folder):
    # Our generated /run path has no escapes; mountinfo reads kernel metadata only.
    return any(line.split()[4] == str(folder) for line in Path('/proc/self/mountinfo').read_text().splitlines())


@dataclass(frozen=True)
class BootEntry:
    number: str
    title: str
    path: str
    internal: bool


def parse_entries(text, partitions, live_disk):
    order_match = re.search(r'^BootOrder:\s*([0-9A-Fa-f,]+)', text, re.M)
    order = order_match[1].upper().split(',') if order_match else []
    entries = []
    for line in text.splitlines():
        match = re.match(r'^Boot([0-9A-Fa-f]{4})\*\s+(.+?)\s+((?:HD|PciRoot|VenHw)\(.+)$', line)
        if not match:
            continue
        number, title, path = match.groups()
        guid = re.search(r'HD\(\d+,GPT,([0-9A-Fa-f-]{36}),', path, re.I)
        if not guid or not re.search(r'File\(\\EFI\\.+\.efi\)', path, re.I):
            continue
        part = partitions.get(guid[1].lower())
        if not part or part['disk'] == live_disk:
            continue
        title = ''.join(c if c.isprintable() else ' ' for c in title)[:160]
        entries.append(BootEntry(number.upper(), title, path, part['transport'] != 'usb'))
    return sorted(entries, key=lambda entry: (not entry.internal,
        order.index(entry.number) if entry.number in order else 65536, entry.number))


def installed_entries(live_disk, job=None, only=None):
    if not Path('/sys/firmware/efi').exists():
        return []
    def output(command):
        return job.command(command, timeout=5, log_output=False)[1] if job else subprocess.check_output(command, text=True, timeout=5)
    listing = json.loads(output(['lsblk', '--tree', '-J', '-p', '-o', 'PATH,TYPE,PARTUUID,TRAN,FSTYPE']))
    partitions = {}
    def walk(node, disk='', transport=''):
        if node.get('type') == 'disk':
            disk, transport = node['path'], node.get('tran') or ''
        if node.get('partuuid'):
            partitions[node['partuuid'].lower()] = {'disk': disk, 'transport': transport,
                'path': node['path'], 'fstype': node.get('fstype')}
        for child in node.get('children', []):
            walk(child, disk, transport)
    for node in listing['blockdevices']:
        walk(node)
    text = output(['efibootmgr', '-v'])
    entries = parse_entries(text, partitions, live_disk)
    if only is not None:
        entries = [entry for entry in entries if entry == only]
    groups = {}
    for entry in entries:
        guid = re.search(r'HD\(\d+,GPT,([0-9A-Fa-f-]{36}),', entry.path, re.I)[1].lower()
        groups.setdefault(guid, []).append(entry)
    found = []
    for guid, candidates in groups.items():
        if job:
            job.check()
        part = partitions[guid]
        if part.get('fstype') != 'vfat':
            continue
        folder = Path(tempfile.mkdtemp(prefix='flashos-esp-', dir='/run'))
        mounted = False
        def execute(command, cleanup=False):
            if job:
                job.command(command, timeout=5, log_output=False, cleanup=cleanup)
            else:
                subprocess.run(command, check=True, capture_output=True, timeout=5)
        try:
            execute(['mount', '-t', 'vfat', '-o', 'ro,nosuid,nodev,noexec', part['path'], str(folder)])
            mounted = True
            for entry in candidates:
                file = re.search(r'File\((\\EFI\\.+\.efi)\)', entry.path, re.I)[1]
                components = file.lstrip('\\').split('\\')
                if all(value not in ('', '.', '..') and '/' not in value for value in components):
                    command = ['test', '-f', str(folder.joinpath(*components))]
                    status = (job.command(command, timeout=5, accepted=(0, 1), log_output=False, resource='storage')[0]
                              if job else subprocess.run(command, timeout=5, capture_output=True).returncode)
                    if status == 0:
                        found.append(entry)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            if job:
                job.incomplete = True
                job.log('Раздел загрузки недоступен: ' + str(error))
        finally:
            # A cancelled mount may have succeeded just before SIGINT arrived.
            if mounted or mounted_at(folder):
                execute(['umount', str(folder)], cleanup=True)
            folder.rmdir()
    return [entry for entry in entries if entry in found]


def select_once(entry, live_disk):
    if entry not in installed_entries(live_disk, only=entry):
        raise ValueError('Загрузочная запись изменилась. Обновите список.')
    subprocess.run(['efibootmgr', '-n', entry.number], check=True, capture_output=True, timeout=5)


def reboot_once(entry, live_disk):
    select_once(entry, live_disk)
    try:
        subprocess.run(['systemctl', 'reboot'], check=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as original:
        try:
            text = subprocess.check_output(['efibootmgr'], text=True, timeout=5)
            current = re.search(r'^BootNext:\s*([0-9A-Fa-f]{4})$', text, re.M)
            if current and current[1].upper() == entry.number:
                subprocess.run(['efibootmgr', '-N'], check=True, capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as cleanup:
            raise RuntimeError('Перезапуск не выполнен; не удалось отменить разовую загрузку. ' + str(cleanup)) from original
        raise
