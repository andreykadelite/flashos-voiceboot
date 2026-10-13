#!/usr/bin/env python3
"""Certify package changes and atomically repack a prepared application seed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def repack(project, runtime, live):
    project, runtime, live = project.resolve(), runtime.resolve(), live.resolve()
    if runtime == live or not all((path / 'etc/debian_version').is_file() for path in (runtime, live)):
        raise ValueError('Expected distinct provisioned Debian roots')
    if json.loads((runtime / 'etc/flashos-app-root').read_text()) != {
            'format': 1, 'suite': 'trixie', 'role': 'applications'}:
        raise ValueError('Application root marker is missing or invalid')
    command = ['chroot', str(runtime), 'dpkg-query', '-W',
               '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n']
    rows = [line.split('\t') for line in subprocess.check_output(command, text=True).splitlines()]
    packages = [{'binary': row[0], 'binary_version': row[1], 'source': row[2], 'source_version': row[3]}
                for row in rows]
    previous = json.loads((project / 'metadata/app-runtime-packages.json').read_text())
    indexed = {row['binary']: row for row in packages}
    before = {row['binary']: row for row in previous}
    if any(indexed.get(name) != item for name, item in before.items()):
        raise ValueError('Application seed contains unexpected package upgrades or removals')
    additions = set(indexed) - set(before)
    if additions - {'espeak-ng'}:
        raise ValueError('Unexpected new application packages: ' + ', '.join(sorted(additions)))
    source_pairs = {(row['source'], row['source_version']) for row in previous}
    source_pairs.update((row['source'], row['source_version']) for row in
                        json.loads((project / 'metadata/debian-packages.json').read_text()))
    if any((row['source'], row['source_version']) not in source_pairs for row in packages):
        raise ValueError('A new source-package version requires source archival before packaging')
    baseline = runtime / 'var/lib/flashos-store/base-packages.json'
    baseline.write_text(json.dumps({'format': 1, 'packages': sorted(indexed)}, indent=2) + '\n')
    subprocess.run(['chroot', str(runtime), 'apt-get', 'clean'], check=True)
    for name in ('etc/machine-id', 'var/lib/dbus/machine-id', 'etc/resolv.conf'):
        (runtime / name).unlink(missing_ok=True)
    for name in ('var/lib/apt/lists', 'var/log', 'tmp', 'run'):
        directory = runtime / name
        if not directory.resolve().is_relative_to(runtime):
            raise ValueError('Seed cleanup directory escapes the runtime')
        directory.mkdir(parents=True, exist_ok=True)
        for entry in directory.iterdir():
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
            elif entry.is_dir():
                shutil.rmtree(entry)
            else:
                raise ValueError('Unexpected special file in seed state directory')
    (runtime / 'tmp').chmod(0o1777)
    destination = live / 'opt/flashos'
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / 'app-runtime.tar.xz'
    temporary = archive.with_suffix(archive.suffix + '.tmp')
    subprocess.run(['tar', '--numeric-owner', '--one-file-system',
                    '--exclude=./proc/*', '--exclude=./dev/*', '--exclude=./sys/*',
                    '--exclude=./run/*', '--exclude=./tmp/*', '-I', 'xz -T4 -6',
                    '-C', str(runtime), '-cf', str(temporary), '.'], check=True)
    with temporary.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    size = 0
    for directory, subdirectories, files in os.walk(runtime, followlinks=False):
        if Path(directory) == runtime:
            subdirectories[:] = [name for name in subdirectories if name not in ('proc', 'dev', 'sys', 'run', 'tmp')]
        for name in files:
            path = Path(directory) / name
            if path.is_file() and not path.is_symlink():
                size += path.stat().st_size
    record = {'format': 1, 'suite': 'trixie', 'architecture': 'amd64', 'user_uid': 1000,
              'sha256': digest, 'bytes': temporary.stat().st_size, 'unpacked_bytes': size}
    temporary.replace(archive)
    metadata = destination / 'app-runtime.json'
    staged = metadata.with_suffix('.tmp')
    staged.write_text(json.dumps(record, indent=2) + '\n')
    staged.replace(metadata)
    lines = ''.join(row['binary'] + '\t' + row['binary_version'] + '\n' for row in packages)
    (project / 'metadata/app-runtime-packages.json').write_text(json.dumps(packages, indent=2) + '\n')
    (project / 'metadata/app-runtime-packages.tsv').write_text(lines)
    (project / 'dist/app-runtime-packages.tsv').write_text(lines)
    result = {**record, 'packages': len(packages), 'added_packages': sorted(additions),
              'package_upgrades': 0, 'source_versions_covered': True}
    output = project / 'build/speech-orca-20261013'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'app-seed-verification.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--live', type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(repack(arguments.project, arguments.runtime, arguments.live)))
