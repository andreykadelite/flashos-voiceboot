#!/usr/bin/env python3
"""Certify the packaged live terminal runtime without executing image contents."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

LIB = 'usr/lib/x86_64-linux-gnu/'
REQUIRED_FILES = {
    'bash': ('usr/bin/bash',),
    'python3-gi': ('usr/lib/python3/dist-packages/gi/__init__.py',),
    'gir1.2-gtk-3.0': (LIB + 'girepository-1.0/Gtk-3.0.typelib',),
    'gir1.2-vte-2.91': (LIB + 'girepository-1.0/Vte-2.91.typelib',),
    'libatk-adaptor': (LIB + 'gtk-2.0/modules/libatk-bridge.so',),
    'libvte-2.91-0': (),
    'libgtk-3-0t64': (),
}
VERSIONED_FILES = {
    'python3-gi': r'usr/lib/python3/dist-packages/gi/_gi\.cpython-\d+-x86_64-linux-gnu\.so',
    'libvte-2.91-0': re.escape(LIB) + r'libvte-2\.91\.so\.0(?:\.\d+)+',
    'libgtk-3-0t64': re.escape(LIB) + r'libgtk-3\.so\.0(?:\.\d+)+',
}
LIBRARY_LINKS = {'libvte-2.91-0': LIB + 'libvte-2.91.so.0',
                 'libgtk-3-0t64': LIB + 'libgtk-3.so.0'}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def image_output(image, option, path):
    try:
        return subprocess.run(['unsquashfs', option, str(image), path], check=True,
                              capture_output=True, timeout=120).stdout
    except FileNotFoundError as error:
        raise RuntimeError('Required build tool missing: unsquashfs') from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode(errors='replace').strip()[:300]
        raise RuntimeError(f'Cannot read terminal component {path}: {detail}') from error


def installed_records(status):
    records = {}
    for stanza in re.split(r'\n\s*\n', status):
        fields = dict(line.split(': ', 1) for line in stanza.splitlines()
                      if ': ' in line and not line[0].isspace())
        name = fields.get('Package')
        if name not in REQUIRED_FILES:
            continue
        require(name not in records, 'Ambiguous installed terminal package: ' + name)
        require(fields.get('Status') in ('install ok installed', 'hold ok installed') and
                bool(fields.get('Version', '').strip()),
                'Terminal package is not configured and installed: ' + name)
        records[name] = fields
    require(set(REQUIRED_FILES) <= records.keys(),
            'Missing installed terminal packages: ' + ', '.join(sorted(set(REQUIRED_FILES) - records.keys())))
    return records


def package_checksums(content, package):
    checksums = {}
    for line in content.decode().splitlines():
        parts = line.split(None, 1)
        require(len(parts) == 2 and re.fullmatch(r'[0-9a-f]{32}', parts[0]),
                'Invalid packaged checksum manifest: ' + package)
        digest, path = parts
        require(not PurePosixPath(path).is_absolute() and '..' not in PurePosixPath(path).parts
                and path not in checksums, 'Unsafe or duplicate packaged checksum: ' + package)
        checksums[path] = digest
    return checksums


def verify(image: Path) -> dict:
    require(image.is_file(), 'Missing squashfs image: ' + str(image))
    records = installed_records(image_output(image, '-cat', 'var/lib/dpkg/status').decode())
    hashes, links = {}, {}
    for package, required_paths in REQUIRED_FILES.items():
        record = records[package]
        info_name = package
        if record.get('Multi-Arch') == 'same':
            require(record.get('Architecture') == 'amd64', 'Unsupported terminal architecture: ' + package)
            info_name += ':amd64'
        checksums = package_checksums(image_output(image, '-cat',
            'var/lib/dpkg/info/' + info_name + '.md5sums'), package)
        paths = list(required_paths)
        if package in VERSIONED_FILES:
            matching = [path for path in checksums if re.fullmatch(VERSIONED_FILES[package], path)]
            require(len(matching) == 1, 'Missing or ambiguous packaged terminal library: ' + package)
            paths.extend(matching)
        for path in paths:
            require(path in checksums, 'Terminal file is absent from package manifest: ' + path)
            content = image_output(image, '-cat', path)
            # dpkg records the archive's MD5; report stronger hashes for subsequent certification.
            require(content and hashlib.md5(content, usedforsecurity=False).hexdigest() == checksums[path],
                    'Packaged terminal file checksum mismatch: ' + path)
            hashes[path] = hashlib.sha256(content).hexdigest()
        if package in LIBRARY_LINKS:
            link = LIBRARY_LINKS[package]
            listing = image_output(image, '-ll', link).decode()
            targets = [line.split(' -> ', 1)[1] for line in listing.splitlines()
                       if line.startswith('l') and 'squashfs-root/' + link + ' -> ' in line]
            require(targets == [PurePosixPath(matching[0]).name], 'Invalid terminal library link: ' + link)
            links[link] = targets[0]
    with image.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'verified': True, 'filesystem_sha256': digest,
            'required_package_versions': {name: record['Version'] for name, record in records.items()},
            'packaged_files_sha256': hashes, 'library_links': links}


def main(argv=None):
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=project / 'dist/usb/flashos/filesystem.squashfs')
    parser.add_argument('--output', type=Path, default=project / 'dist/terminal-build-verification.json')
    args = parser.parse_args(argv)
    try:
        result = verify(args.image)
    except (RuntimeError, OSError, UnicodeError, subprocess.TimeoutExpired) as error:
        result = {'verified': False, 'error': str(error)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    if not result['verified']:
        parser.exit(1, 'Terminal verification failed: ' + result['error'] + '\n')
    print('Verified installed terminal packages and packaged GTK/VTE/GI/ATK files.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
