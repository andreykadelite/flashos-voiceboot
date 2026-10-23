#!/usr/bin/env python3
"""Export the prepared system tree; this is not a bootable disk-image writer."""
import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import zipfile
from public_payload import payload_files, safe_path, verified_system

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from installer.model import SYSTEM_VERSION


def main(argv=()):
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-large-local', action='store_true',
                        help='Allow a local ZIP64 archive larger than GitHub release limit')
    args = parser.parse_args(argv)
    verified_system(ROOT, SYSTEM_VERSION)
    files = []
    for section in ('usb', 'boot'):
        files.extend(path for _, path in payload_files(ROOT, section))
    files += [safe_path(ROOT, ROOT / 'dist' / name) for name in
              ('ventoy.disk.img.xz', 'ventoy-efi.img', 'packages.tsv', 'toolkit-build-verification.json')]
    out = ROOT / 'release' / ('FlashOS-' + SYSTEM_VERSION + '-system.zip')
    out.parent.mkdir(exist_ok=True)
    sums = []
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=out.name + '.', suffix='.tmp',
                                         dir=out.parent, delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for path in files:
                archive.write(path, path.relative_to(ROOT).as_posix())
                with path.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                sums.append(digest + '  ' + path.relative_to(ROOT).as_posix())
            archive.writestr('SYSTEM-SHA256SUMS.txt', '\n'.join(sums) + '\n')
            archive.writestr('READ-ME.txt', 'FlashOS system build tree. Extract at the matching source checkout root to populate dist/.\nThis archive is not an ISO/raw disk image. Use FlashOS-Setup.exe to prepare USB media.\nRead dist/usb/README files and licenses before redistributing. ISO files are not included.\n')
        if temporary.stat().st_size >= 2 * 1024**3 and not args.allow_large_local:
            raise ValueError('Release asset exceeds GitHub size limit')
        with temporary.open('rb+') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, out)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(out.name, out.stat().st_size)


if __name__ == '__main__':
    main(None)
