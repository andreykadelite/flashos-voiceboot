#!/usr/bin/env python3
"""Create a disposable Ventoy disk image. Never accepts a block device."""
import argparse
from pathlib import Path
import lzma
import os
import struct
import subprocess
import uuid

project = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--size-gib', type=int, choices=range(4, 129), default=4)
parser.add_argument('--data-root', type=Path, default=project / 'dist/usb')
args = parser.parse_args()
data_root = args.data_root.resolve(strict=True)
if not (data_root / 'flashos/flashos.marker').is_file():
    raise SystemExit('The data source is not a FlashOS payload')
if Path('/tmp/flashos-qmp.sock').exists():
    raise SystemExit('Stop the test VM before replacing its disk')
image = project / 'dist/flashos-test.img'
assert image.resolve().parent == (project / 'dist').resolve()
if image.is_symlink() or (image.exists() and not image.is_file()):
    raise SystemExit('The disposable image must be a regular file, never a device or symlink')
if subprocess.check_output(['losetup', '-j', str(image)], text=True).strip():
    raise SystemExit('Detach the test image loop device before replacing it')
sectors = args.size_gib * 1024**3 // 512
payload_items = [data_root / name for name in ('flashos', 'ventoy', 'iso', 'docs', 'README-FlashOS.md')
                 if (data_root / name).exists()]
payload_bytes = sum(p.stat().st_size for item in payload_items
                    for p in ([item] if item.is_file() else item.rglob('*')) if p.is_file())
if payload_bytes > sectors * 512 - 768 * 1024**2:
    raise SystemExit('The payload does not fit; increase --size-gib')
part3 = sectors - 1048576
part2 = part3 - 65536
mbr = bytearray((project / 'tools/ventoy-1.1.17/boot/boot.img').read_bytes())
mbr[384:400] = uuid.uuid4().bytes
mbr[440:444] = mbr[384:388]
for index, (active, type_, first, count) in enumerate([(0x80, 0x07, 2048, part2 - 2048), (0, 0xef, part2, 65536), (0, 0x0c, part3, 1048576)]):
    mbr[446 + index * 16:462 + index * 16] = struct.pack('<B3sB3sII', active, b'\xfe\xff\xff', type_, b'\xfe\xff\xff', first, count)
mbr[494:510] = b'\0' * 16
mbr[510:] = b'\x55\xaa'
with image.open('wb') as disk:
    disk.truncate(sectors * 512)
    disk.seek(0)
    disk.write(mbr)
    disk.write(lzma.decompress((project / 'tools/ventoy-1.1.17/boot/core.img.xz').read_bytes()))
    disk.seek(part2 * 512)
    disk.write((project / 'dist/ventoy-efi.img').read_bytes())
loop = subprocess.check_output(['losetup', '--find', '--show', '--partscan', str(image)], text=True).strip()
mount = Path('/mnt/flashos-test-disk')
mount.mkdir(exist_ok=True)
try:
    subprocess.run(['mkfs.exfat', '-L', 'FLASHOS', loop + 'p1'], check=True)
    data_uuid = subprocess.check_output(['blkid', '-s', 'UUID', '-o', 'value', loop + 'p1'], text=True).strip()
    subprocess.run(['mount', loop + 'p1', str(mount)], check=True)
    try:
        for item in payload_items:
            print('Copying', item, flush=True)
            subprocess.run(['cp', '-r', str(item), str(mount)], check=True)
        (mount / 'ventoy/debug').touch()
        os.sync()
    finally:
        subprocess.run(['umount', str(mount)], check=True)
    subprocess.run(['mkfs.vfat', '-F', '32', '-n', 'FLASHBOOT', loop + 'p3'], check=True)
    subprocess.run(['mount', loop + 'p3', str(mount)], check=True)
    try:
        subprocess.run(['cp', '-r', str(project / 'dist/boot') + '/.', str(mount)], check=True)
        for config in [mount / 'flashos/efi/grub.cfg', mount / 'EFI/debian/grub.cfg']:
            content = config.read_text()
            assert content.count('@DATA_UUID@') == 1, 'Unexpected UUID template'
            config.write_text(content.replace('@DATA_UUID@', data_uuid))
        os.sync()
    finally:
        subprocess.run(['umount', str(mount)], check=True)
finally:
    subprocess.run(['losetup', '-d', loop], check=True)
print(image)
