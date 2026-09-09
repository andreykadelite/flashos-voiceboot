"""Bounded, read-only ISO discovery. File names never become shell commands."""
from dataclasses import dataclass
from pathlib import Path
import os
import re
import struct
import unicodedata


def spoken_text(text):
    return ''.join(c for c in text if not unicodedata.category(c).startswith('C')).strip()[:240]


@dataclass(frozen=True)
class Image:
    path: Path
    relative: str
    title: str
    size: int
    bootable: bool
    reason: str = ''
    modes: tuple = ()


def inspect_iso(path):
    """Check ISO descriptors and El Torito, without mounting arbitrary media."""
    label, modes = '', set()
    with path.open('rb') as stream:
        for index in range(16, 80):
            stream.seek(index * 2048)
            block = stream.read(2048)
            if len(block) != 2048:
                break
            if block[1:6] != b'CD001':
                continue
            if block[0] == 1:
                label = block[40:72].decode('ascii', 'replace').strip()
            elif block[0] == 0 and block[7:30].startswith(b'EL TORITO SPECIFICATION'):
                catalog_sector = struct.unpack_from('<I', block, 71)[0]
                position = stream.tell()
                stream.seek(catalog_sector * 2048)
                catalog = stream.read(2048)
                stream.seek(position)
                if len(catalog) == 2048 and catalog[0] == 1 and catalog[30:32] == b'\x55\xaa':
                    valid = sum(struct.unpack('<16H', catalog[:32])) & 0xffff == 0
                    if valid:
                        def add(platform, entry):
                            if entry[0] == 0x88 and platform in (0, 0xef):
                                modes.add('bios' if platform == 0 else 'uefi')
                        add(catalog[1], catalog[32:64])
                        cursor = 64
                        while cursor + 32 <= len(catalog) and catalog[cursor] in (0x90, 0x91):
                            last = catalog[cursor] == 0x91
                            platform = catalog[cursor + 1]
                            count = struct.unpack_from('<H', catalog, cursor + 2)[0]
                            cursor += 32
                            for _ in range(min(count, (len(catalog) - cursor) // 32)):
                                add(platform, catalog[cursor:cursor + 32])
                                cursor += 32
                            if last:
                                break
            if block[0] == 255:
                break
    return spoken_text(label), bool(modes), tuple(sorted(modes))


def discover(root, firmware=None):
    root = Path(root).resolve()
    directory = root / 'iso'
    results = []
    if not directory.is_dir() or directory.is_symlink():
        return results
    for current, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not Path(current, d).is_symlink())
        for name in sorted(files):
            path = Path(current, name)
            if path.suffix.lower() != '.iso' or path.is_symlink():
                continue
            try:
                path.resolve().relative_to(directory.resolve())
                label, bootable, modes = inspect_iso(path)
                size = path.stat().st_size
                relative = '/' + path.relative_to(root).as_posix()
                title = spoken_text(re.sub(r'[_-]+', ' ', path.stem))
                if label and label.casefold() not in title.casefold():
                    title += ' — ' + label
                reason = '' if bootable else 'Загрузочный каталог не найден или повреждён'
                if bootable and firmware and firmware not in modes:
                    bootable, reason = False, 'Этот ISO не поддерживает текущий режим ' + firmware.upper()
                if (any(c in relative for c in '\r\n\x00') or len(relative.encode('utf-8')) > 511
                        or len(path.name.encode('utf-8')) > 255
                        or len((relative.rsplit('/', 1)[0] + '/').encode('utf-8')) > 399):
                    bootable, reason = False, 'Слишком длинное или недопустимое имя файла'
                results.append(Image(path, relative, title, size, bootable, reason, modes))
            except (OSError, ValueError) as error:
                results.append(Image(path, '', spoken_text(path.stem), 0, False,
                                     'Не удалось прочитать образ: ' + spoken_text(str(error))))
    return sorted(results, key=lambda item: item.relative.casefold())
