"""Read-only hash of used ranges from Partclone's ddrescue domain map."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


def ranges(path, limit):
    previous = 0
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, encoding='ascii') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Карта блоков не является файлом.')
        for line in stream:
            if len(line) > 4096:
                raise ValueError('Недопустимая строка карты.')
            parts = line.split()
            if not parts or line.startswith('#') or len(parts) == 2:
                continue
            if len(parts) != 3 or parts[2] not in ('+', '?'):
                raise ValueError('Неизвестный формат карты блоков.')
            start, length = int(parts[0], 16), int(parts[1], 16)
            if start != previous or length <= 0 or start + length > limit:
                raise ValueError('Карта выходит за границы исходного раздела.')
            previous = start + length
            if parts[2] == '+':
                yield start, length
    if previous != limit:
        raise ValueError('Карта не покрывает исходный раздел целиком; сверка неполна.')


def calculate(map_path, device, limit):
    digest, total, blocks, previous = hashlib.sha256(), 0, 0, -1
    descriptor = os.open(device, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb', buffering=0) as stream:
        if not stat.S_ISBLK(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Для сверки требуется блочное устройство.')
        for start, length in ranges(map_path, limit):
            stream.seek(start)
            remaining = length
            while remaining:
                chunk = stream.read(min(4 * 1024**2, remaining))
                if not chunk:
                    raise ValueError('Не удалось полностью прочитать записанные блоки.')
                digest.update(chunk)
                remaining -= len(chunk)
                total += len(chunk)
                percent = (start + length - remaining) * 100 // limit
                if percent != previous:
                    print(json.dumps({'percent': percent}), flush=True)
                    previous = percent
            blocks += 1
    if not blocks or not total:
        raise ValueError('В карте нет проверяемых блоков.')
    return digest.hexdigest()


if __name__ == '__main__':
    try:
        print(json.dumps({'sha256': calculate(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]))}), flush=True)
    except Exception as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), flush=True)
        sys.exit(1)
