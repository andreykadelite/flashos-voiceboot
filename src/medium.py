"""Coordinate temporary and long-lived writers on the verified boot USB."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import threading

_guard = threading.RLock()
_writers = {}


def medium_info(job, medium, live_disk, cleanup=False):
    medium = Path(medium)
    marker = medium / 'flashos/flashos.marker'
    if medium.is_symlink() or (medium / 'flashos').is_symlink() or marker.is_symlink() or not marker.is_file():
        raise ValueError('Не подтверждена загрузочная флешка для сохранения файлов.')
    _, raw = job.command(['findmnt', '-J', '-M', str(medium), '-o', 'SOURCE,FSTYPE'],
                         timeout=5, log_output=False, cleanup=cleanup)
    entry = json.loads(raw)['filesystems'][0]
    source = str(Path(entry['source']).resolve())
    _, parent = job.command(['lsblk', '-ndo', 'PKNAME', source], timeout=5, log_output=False, cleanup=cleanup)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', parent.strip()) or '/dev/' + parent.strip() != live_disk:
        raise ValueError('Не подтверждена загрузочная флешка для сохранения файлов.')
    if entry['fstype'] not in ('exfat', 'vfat', 'ntfs3', 'ntfs', 'fuseblk'):
        raise ValueError('Неожиданная файловая система загрузочной флешки.')
    return source, entry['fstype']


def _key(medium):
    return os.path.abspath(str(medium))


@contextmanager
def writable(job, medium, live_disk):
    """Keep DATA writable until its last application or rescue writer closes."""
    key = _key(medium)
    acquired = False
    try:
        with _guard:
            source, _ = medium_info(job, medium, live_disk)
            record = _writers.get(key)
            if record is not None and record['source'] != source:
                raise ValueError('Загрузочное устройство изменилось.')
            first = record is None
            if first:
                _, options = job.command(['findmnt', '-n', '-M', str(medium), '-o', 'OPTIONS'],
                                        timeout=5, log_output=False)
                record = {'source': source, 'count': 0,
                          'restore_ro': 'rw' not in options.strip().split(',')}
                _writers[key] = record
            # Register before remount: cancellation can follow successful mount.
            record['count'] += 1
            acquired = True
            if first:
                job.command(['mount', '-o', 'remount,rw', str(medium)], timeout=20)
        yield source
    finally:
        if acquired:
            with _guard:
                record['count'] -= 1
                if record['count'] == 0:
                    try:
                        # Recheck the same mount before any cleanup mutation.
                        actual, _ = medium_info(job, medium, live_disk, cleanup=True)
                        if actual != source:
                            raise ValueError('Загрузочное устройство изменилось; переподключение запрещено.')
                        if record['restore_ro']:
                            job.command(['mount', '-o', 'remount,ro', str(medium)],
                                        timeout=20, cleanup=True)
                    finally:
                        _writers.pop(key, None)


@contextmanager
def readonly(job, medium, live_disk):
    """Hold out new writers throughout restore verification and disk writes."""
    with _guard:
        if _writers.get(_key(medium), {}).get('count', 0):
            raise RuntimeError('Закройте программы и хранилище приложений перед восстановлением раздела.')
        source, _ = medium_info(job, medium, live_disk)
        job.command(['mount', '-o', 'remount,ro', str(medium)], timeout=20)
        yield source


def require_readonly(job, medium, live_disk):
    with readonly(job, medium, live_disk):
        pass


def writer_count(medium):
    with _guard:
        return _writers.get(_key(medium), {}).get('count', 0)


def restore_readonly_after_crash(medium):
    """Expose the initial mount mode to the root-owned runtime recovery record."""
    with _guard:
        return bool(_writers.get(_key(medium), {}).get('restore_ro', False))
