"""Verified data archive with unique objects and bounded streaming extraction."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import time
import zipfile
from installer._release import MANIFEST_SHA256


def payload_path():
    module_path = Path(__file__).resolve()
    embedded = module_path.parent / 'payload.zip'
    if '__compiled__' in globals():
        executable = Path(sys.argv[0]).resolve()
        if executable.parent != module_path.parents[1]:
            # Onefile modules run in an extraction directory. The large system
            # ZIP follows the outer EXE's PE sections and is read in place.
            # A damaged ZIP must fail rather than select an unrelated sidecar.
            return executable
        return embedded
    if embedded.exists():
        return embedded
    return module_path.parents[1] / 'build/installer/payload.zip'


def safe_relative(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '\\' in value or ':' in value or '..' in path.parts or not path.parts:
        raise ValueError('Недопустимый путь в комплекте.')
    return path


class Bundle:
    def __init__(self, path=None, expected_hash=MANIFEST_SHA256):
        self.path = Path(path) if path else payload_path()
        with zipfile.ZipFile(self.path) as archive:
            if archive.getinfo('manifest.json').file_size > 2 * 1024**2:
                raise ValueError('Слишком большой паспорт комплекта.')
            raw = archive.read('manifest.json')
            if hashlib.sha256(raw).hexdigest() != expected_hash:
                raise ValueError('Встроенный комплект повреждён: не совпал паспорт файлов.')
            self.manifest = json.loads(raw)
            if self.manifest['schema'] != 1:
                raise ValueError('Неизвестный формат комплекта.')
            for record in self.manifest['files']:
                safe_relative(record['path'])
                if record['section'] not in ('usb', 'boot', 'engine', 'info'):
                    raise ValueError('Недопустимый раздел комплекта.')

    def text(self, path):
        record = next(x for x in self.manifest['files'] if x['section'] == 'info' and x['path'] == path)
        with zipfile.ZipFile(self.path) as archive:
            data = archive.read('objects/' + record['sha256'])
        if hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('Повреждён справочный файл.')
        return data.decode('utf-8-sig')

    def extract(self, destination, emit, cancelled, required=None):
        destination = Path(destination)
        records = [r for r in self.manifest['files'] if required is None or (r['section'], r['path']) in required]
        selected = {r['sha256']: self.manifest['objects'][r['sha256']] for r in records}
        total = sum(selected.values())
        if shutil.disk_usage(destination.parent).free < total + 512 * 1024**2:
            raise ValueError(f'Для подготовки системы нужно {total / 1024**3 + .5:.1f} ГиБ свободного места на компьютере.')
        destination.mkdir()
        objects = destination / 'objects'
        objects.mkdir()
        written, last = 0, 0
        emit(dict(kind='phase', phase='prepare', message='Проверка и подготовка встроенной системы' if required is None else 'Подготовка эталонов для проверки флешки', percent=0, bytes=0, total_bytes=total))
        with zipfile.ZipFile(self.path) as archive:
            for digest, size in selected.items():
                if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                    raise ValueError('Недопустимая контрольная сумма.')
                info = archive.getinfo('objects/' + digest)
                if info.file_size != size:
                    raise ValueError('Размер файла в комплекте не совпал.')
                hasher = hashlib.sha256()
                with archive.open(info) as source, (objects / digest).open('xb') as target:
                    while block := source.read(4 * 1024**2):
                        if cancelled():
                            raise InterruptedError('Подготовка отменена. Флешка не изменена.')
                        target.write(block)
                        hasher.update(block)
                        written += len(block)
                        if time.monotonic() - last > .4:
                            emit(dict(kind='progress', phase='prepare', message='Проверка комплекта', percent=round(written * 100 / max(1, total), 1), bytes=written, total_bytes=total))
                            last = time.monotonic()
                if hasher.hexdigest() != digest:
                    raise ValueError('Повреждён встроенный файл. Скачайте исправный EXE заново.')
        engine = destination / 'engine'
        engine.mkdir()
        for record in records:
            if record['section'] == 'engine':
                target = engine / safe_relative(record['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(objects / record['sha256'], target)
        (destination / 'manifest.json').write_text(json.dumps(self.manifest, ensure_ascii=False), encoding='utf-8')
        emit(dict(kind='progress', phase='prepare', message='Комплект проверен', percent=100, bytes=written, total_bytes=total))
        return destination
