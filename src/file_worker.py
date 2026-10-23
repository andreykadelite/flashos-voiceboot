"""Bounded child for filesystem traversal; never follow links or special files."""
import argparse
import codecs
import heapq
import json
import os
from pathlib import Path
import re
import stat
import unicodedata


def checked_path(root, relative):
    root = Path(root)
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Путь выходит за пределы выбранного раздела.')
    current = root
    device = root.stat().st_dev
    for part in relative.parts:
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_dev != device:
            raise ValueError('Переход по ссылке или на другой раздел запрещён.')
    if not (current.is_file() or current.is_dir()):
        raise ValueError('Это не обычный файл или папка.')
    return current


def listing(root, relative, offset=0):
    path = checked_path(root, relative)
    device = path.stat().st_dev
    total = 0
    def rows():
        nonlocal total
        with os.scandir(path) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if info.st_dev != device or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    continue
                total += 1
                yield {'name': entry.name, 'directory': stat.S_ISDIR(info.st_mode), 'bytes': info.st_size}
    selected = heapq.nsmallest(offset + 250, rows(), key=lambda r: (not r['directory'], r['name'].casefold(), r['name']))
    return {'items': selected[offset:], 'total': total, 'offset': offset}


def compatible_name(name):
    return not (name[-1:] in ('.', ' ') or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or
        re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.I))


def plan(root, relative, windows=False, fat32=False):
    path = checked_path(root, relative)
    device = path.stat().st_dev
    total, count, skipped, last_reported = 0, 0, 0, 0
    pending = [path]
    while pending:
        current = pending.pop()
        info = current.lstat()
        if info.st_dev != device or stat.S_ISLNK(info.st_mode):
            skipped += 1
        elif stat.S_ISDIR(info.st_mode):
            names = set()
            with os.scandir(current) as entries:
                for entry in entries:
                    pending.append(current / entry.name)
                    if windows:
                        child = entry.stat(follow_symlinks=False)
                        if child.st_dev != device or not (stat.S_ISDIR(child.st_mode) or stat.S_ISREG(child.st_mode)):
                            continue
                        normalized = unicodedata.normalize('NFC', entry.name).casefold()
                        if normalized in names or not compatible_name(entry.name):
                            raise ValueError('Имена нельзя без потерь перенести на раздел Windows: ' + str(current / entry.name) +
                                             '. Выберите отдельные файлы или сохраните накопитель в образ.')
                        names.add(normalized)
        elif stat.S_ISREG(info.st_mode):
            if fat32 and info.st_size >= 4 * 1024**3:
                raise ValueError('Файл больше предела FAT32: ' + str(current))
            if windows and not compatible_name(current.name):
                raise ValueError('Недопустимое для Windows имя файла: ' + current.name)
            total += info.st_size
            count += 1
        else:
            skipped += 1
        if count >= last_reported + 500:
            print(json.dumps({'progress': f'Найдено файлов: {count}. Объём {total / 1024**3:.2f} ГиБ.'}), flush=True)
            last_reported = count
    return {'files': count, 'bytes': total, 'skipped': skipped, 'directory': path.is_dir()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['list', 'plan', 'text', 'space'])
    parser.add_argument('root')
    parser.add_argument('relative')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--windows', action='store_true')
    parser.add_argument('--fat32', action='store_true')
    args = parser.parse_args()
    if args.action == 'list':
        result = listing(args.root, args.relative, max(0, args.offset))
    elif args.action == 'plan':
        result = plan(args.root, args.relative, args.windows, args.fat32)
    elif args.action == 'space':
        info = os.statvfs(checked_path(args.root, args.relative))
        result = {'total': info.f_blocks * info.f_frsize, 'free': info.f_bavail * info.f_frsize}
    else:
        path = checked_path(args.root, args.relative)
        with path.open('rb') as stream:
            raw = stream.read(65537)
        if b'\0' in raw and not raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            raise ValueError('Файл не похож на текст. Его можно скопировать.')
        encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        try:
            # A preview boundary can split a UTF-8 character or UTF-16 surrogate.
            # Discard only that incomplete suffix, keeping the file's encoding.
            decoder = codecs.getincrementaldecoder(encoding)()
            text = decoder.decode(raw[:65536], final=len(raw) <= 65536)
        except UnicodeDecodeError:
            text = raw[:65536].decode('cp1251', errors='replace')
        result = {'lines': text.splitlines(), 'truncated': len(raw) > 65536}
    print(json.dumps({'result': result}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=True), flush=True)
        raise SystemExit(1)
