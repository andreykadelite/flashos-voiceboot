"""A fixed-size GRUB environment block, compatible with grub-editenv."""
from pathlib import Path
import os

HEADER = b'# GRUB Environment Block\n'
SIZE = 1024


def environment(path=''):
    if path and (not path.startswith('/iso/') or any(c in path for c in '\r\n\x00')
                 or '..' in Path(path).parts or not path.lower().endswith('.iso')):
        raise ValueError('Недопустимый путь ISO')
    data = HEADER + (('flashos_next=' + path + '\n').encode('utf-8') if path else b'')
    if len(data) > SIZE:
        raise ValueError('Слишком длинный путь ISO')
    return data.ljust(SIZE, b'#')


def arm(root, image, state_root=None):
    root = Path(root).resolve()
    target = Path(state_root or root) / 'ventoy' / 'flashos.env'
    if not image.bootable or image.path.is_symlink():
        raise ValueError('Этот образ нельзя запустить')
    if '/' + image.path.resolve().relative_to(root).as_posix() != image.relative:
        raise ValueError('Путь образа изменился')
    if image.path.stat().st_size != image.size:
        raise ValueError('Образ изменился. Обновите список клавишей F5')
    # In-place update preserves the preallocated block GRUB can safely rewrite.
    with target.open('r+b', buffering=0) as stream:
        old = stream.read()
        if len(old) != SIZE or not old.startswith(HEADER):
            raise ValueError('Повреждён файл одноразовой загрузки')
        stream.seek(0)
        stream.write(environment(image.relative))
        os.fsync(stream.fileno())


def cancel(root):
    target = Path(root) / 'ventoy' / 'flashos.env'
    with target.open('r+b', buffering=0) as stream:
        if stream.read(len(HEADER)) != HEADER:
            raise ValueError('Повреждён файл одноразовой загрузки')
        stream.seek(0)
        stream.write(environment())
        stream.truncate(SIZE)
        os.fsync(stream.fileno())
