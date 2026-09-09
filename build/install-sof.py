#!/usr/bin/env python3
"""Install a pinned, checksum-verified upstream SOF release in the build root."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile

project = Path(__file__).resolve().parents[1]
root = Path('/opt/flashos-rootfs')
archive = project / 'downloads/sof-bin-2025.12.2.tar.gz'
expected = '533f63e3a6d94c09ce05a782657b675fa683ff20787c0979226cf563ec79f517'
url = 'https://github.com/thesofproject/sof-bin/releases/download/v2025.12.2/sof-bin-2025.12.2.tar.gz'
if not archive.exists():
    subprocess.run(['curl', '-fL', '--retry', '3', '-o', str(archive), url], check=True)
with archive.open('rb') as stream:
    if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
        raise SystemExit('SOF release SHA256 mismatch')
extracted = Path('/opt/flashos-sof-release')
extracted.mkdir(exist_ok=True)
with tarfile.open(archive) as bundle:
    bundle.extractall(extracted, filter='data')
release = extracted / 'sof-bin-2025.12.2'
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
(record / 'sof-release.txt').write_text(f'SOF v2025.12.2\n{url}\nSHA256 {expected}\n')
