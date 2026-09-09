#!/usr/bin/env python3
"""Patch configuration only. Signed Ventoy EFI binaries stay byte-for-byte original."""
from pathlib import Path
import lzma
import hashlib
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from selection import environment

image = ROOT / 'dist/ventoy-efi.img'
archive = ROOT / 'downloads/ventoy-1.1.17-linux.tar.gz'
if hashlib.sha256(archive.read_bytes()).hexdigest() != '7fb4ed08cef6a6b4d39dd19260d8c80291a78dfdf9af7d461571e23cbbc43805':
    raise RuntimeError('Official Ventoy archive checksum mismatch')
with tarfile.open(archive) as source_tar:
    members = [item for item in source_tar.getmembers() if item.name.endswith('/ventoy/ventoy.disk.img.xz')]
    if len(members) != 1:
        raise RuntimeError('Unexpected official Ventoy archive')
    original_xz = source_tar.extractfile(members[0]).read()
image.write_bytes(lzma.decompress(original_xz))
config = ROOT / 'dist/ventoy-grub.cfg'
source = subprocess.check_output(['mtype', '-i', str(image), '::/grub/grub.cfg'], text=True, timeout=30)
anchor = '#Update Secure Boot Policy'
if source.count(anchor) != 1:
    raise RuntimeError('Unexpected Ventoy version: configuration patch needs review')
source = source.replace(anchor, (ROOT / 'boot/oneshot.cfg').read_text() + '\n' + anchor)
config.write_text(source)
subprocess.run(['grub-script-check', str(config)], check=True)
subprocess.run(['mcopy', '-o', '-i', str(image), str(config), '::/grub/grub.cfg'], check=True, timeout=30)
# Ventoy's embedded Windows XZ decoder supports the original archive's CRC32,
# not Python's default CRC64. The decompressed FAT image is identical.
(ROOT / 'dist/ventoy.disk.img.xz').write_bytes(lzma.compress(image.read_bytes(), preset=6, check=lzma.CHECK_CRC32))
(ROOT / 'dist/boot/ventoy/flashos.env').write_bytes(environment())
