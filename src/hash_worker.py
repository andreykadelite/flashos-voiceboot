"""Isolate potentially stuck file reads in a cancellable child process."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


def calculate(path, size):
    def identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    digest, total, previous = hashlib.sha256(), 0, -1
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
            raise ValueError('Образ изменился. Обновите список.')
        while chunk := source.read(4 * 1024**2):
            digest.update(chunk)
            total += len(chunk)
            percent = min(100, total * 100 // max(1, size))
            if percent != previous:
                print(json.dumps({'percent': percent}), flush=True)
                previous = percent
        if total != size or identity(before) != identity(os.fstat(source.fileno())) or identity(before) != identity(path.stat()):
            raise ValueError('Образ изменился во время чтения.')
    print(json.dumps({'sha256': digest.hexdigest()}), flush=True)


if __name__ == '__main__':
    try:
        calculate(Path(sys.argv[1]), int(sys.argv[2]))
    except Exception as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), flush=True)
        sys.exit(1)
