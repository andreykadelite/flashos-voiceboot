"""Recover an actually deleted PNG from a newly created disposable filesystem."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import uuid
import zlib

project = Path(__file__).resolve().parents[1]
rootfs = Path('/opt/flashos-rootfs')
folder = rootfs / 'tmp' / ('photorec-deleted-' + uuid.uuid4().hex)
folder.mkdir()
assert folder.resolve().parent == rootfs / 'tmp'
image = folder / 'source.img'
with image.open('xb') as stream:
    stream.truncate(32 * 1024**2)
subprocess.run(['mkfs.ext4', '-F', str(image)], capture_output=True, check=True)
mount = folder / 'mounted'
mount.mkdir()
loop = subprocess.check_output(['losetup', '--find', '--show', str(image)], text=True).strip()
try:
    assert subprocess.check_output(['losetup', '-n', '-O', 'BACK-FILE', loop], text=True).strip() == str(image)
    subprocess.run(['mount', loop, str(mount)], check=True)
    try:
        def chunk(tag, data):
            return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 2, 0, 0, 0))
        png += chunk(b'IDAT', zlib.compress((b'\0' + bytes(reversed(range(192)))) * 64)) + chunk(b'IEND', b'')
        path = mount / 'deleted.png'
        with path.open('xb') as stream:
            stream.write(png)
            stream.flush()
            os.fsync(stream.fileno())
        expected = hashlib.sha256(png).hexdigest()
        path.unlink()
        assert not path.exists()
    finally:
        subprocess.run(['umount', str(mount)], check=True)
finally:
    subprocess.run(['losetup', '-d', loop], check=True)
before = hashlib.sha256(image.read_bytes()).hexdigest()
inside = '/' + str(folder.relative_to(rootfs))
with (folder / 'stdout.log').open('w') as log:
    subprocess.run(['chroot', str(rootfs), 'photorec', '/log', '/logname', inside + '/photorec.log',
        '/d', inside + '/recovered', '/cmd', inside + '/source.img',
        'partition_none,options,mode_ext2,fileopt,everything,disable,png,enable,search'],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=120, check=True,
        env={**os.environ, 'TERM': 'linux', 'LC_ALL': 'C'})
found = [p for p in folder.glob('recovered.*/*.png') if hashlib.sha256(p.read_bytes()).hexdigest() == expected]
assert found, 'Deleted PNG was not recovered'
assert before == hashlib.sha256(image.read_bytes()).hexdigest()
result = {'deleted_file_recovered': True, 'sha256_matches': True, 'source_image_unchanged': True,
          'source_bytes': image.stat().st_size, 'sha256': expected, 'test_directory': str(folder)}
(project / 'dist/photorec-deleted-verification.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result))
