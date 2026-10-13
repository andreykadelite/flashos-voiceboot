#!/usr/bin/env python3
"""Install a pinned, checksum-verified upstream SOF release in the build root."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile

project = Path(__file__).resolve().parents[1]
root = Path('/opt/flashos-rootfs')
archive = project / 'downloads/sof-bin-2026.09.2.tar.gz'
expected = '5f7bc30b2b5c1e6b0da55a33fc44e2b7f56ae9b06952edcde614f51ef6b943e1'
url = 'https://github.com/thesofproject/sof-bin/releases/download/v2026.09.2/sof-bin-2026.09.2.tar.gz'
if not archive.exists():
    subprocess.run(['curl', '-fL', '--retry', '3', '-o', str(archive), url], check=True)
with archive.open('rb') as stream:
    if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
        raise SystemExit('SOF release SHA256 mismatch')
extracted = Path('/opt/flashos-sof-release')
extracted.mkdir(exist_ok=True)
with tarfile.open(archive) as bundle:
    bundle.extractall(extracted, filter='data')
release = extracted / 'sof-bin-2026.09.2'
destination = root / 'usr/lib/firmware/intel'
assert destination.resolve().is_relative_to(root.resolve())
destination.mkdir(parents=True, exist_ok=True)
# Same firmware copy operation as upstream install.sh, without optional debug tools.
for directory in sorted(release.glob('sof*')):
    if directory.is_dir():
        subprocess.run(['rsync', '-a', str(directory), str(destination) + '/'], check=True)
record = root / 'usr/share/doc/flashos'
record.mkdir(parents=True, exist_ok=True)
for license_file in release.glob('LICENCE.*'):
    shutil.copyfile(license_file, record / license_file.name)
(record / 'sof-release.txt').write_text(f'SOF v2026.09.2\n{url}\nSHA256 {expected}\n')
