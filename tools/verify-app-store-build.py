#!/usr/bin/env python3
"""Certify the isolated Debian application runtime embedded in the live image."""
import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import subprocess
import tarfile
import tempfile

REQUIRED = ('usr/bin/apt-get', 'usr/bin/dpkg', 'usr/bin/python3', 'usr/bin/orca',
            'usr/bin/openbox', 'usr/bin/dbus-run-session', 'usr/bin/gnome-terminal',
            'usr/bin/pipewire-pulse', 'usr/bin/speech-dispatcher', 'usr/bin/gsettings',
            'usr/bin/xkbcomp', 'usr/bin/xmodmap', 'usr/bin/xset',
            'usr/share/keyrings/debian-archive-keyring.gpg',
            'etc/flashos-app-root', 'var/lib/flashos-store/base-packages.json',
            'usr/lib/python3/dist-packages/gi/__init__.py',
            'usr/lib/x86_64-linux-gnu/girepository-1.0/Gio-2.0.typelib',
            'usr/lib/x86_64-linux-gnu/girepository-1.0/Atspi-2.0.typelib',
            'usr/share/glib-2.0/schemas/gschemas.compiled',
            'usr/share/glib-2.0/schemas/org.gnome.desktop.interface.gschema.xml',
            'usr/share/glib-2.0/schemas/org.gnome.desktop.a11y.applications.gschema.xml',
            'usr/share/glib-2.0/schemas/org.gnome.desktop.a11y.interface.gschema.xml',
            'usr/lib/speech-dispatcher-modules/sd_espeak-ng',
            'usr/lib/speech-dispatcher-modules/sd_rhvoice', 'usr/bin/RHVoice-test', 'usr/bin/espeak-ng',
            'var/lib/flashos-store/speech-runtime.json')


def require(value, message):
    if not value:
        raise RuntimeError(message)


def verify(project, image):
    metadata = json.loads(subprocess.check_output(
        ['unsquashfs', '-cat', str(image), 'opt/flashos/app-runtime.json']))
    require(metadata.get('format') == 1 and metadata.get('suite') == 'trixie' and
            metadata.get('architecture') == 'amd64' and metadata.get('user_uid') == 1000,
            'Unsupported application runtime metadata')
    with tempfile.TemporaryDirectory(prefix='flashos-runtime-check-') as folder:
        archive = Path(folder) / 'runtime.tar.xz'
        with archive.open('wb') as output:
            subprocess.run(['unsquashfs', '-cat', str(image), 'opt/flashos/app-runtime.tar.xz'],
                           stdout=output, check=True)
        with archive.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        require(digest == metadata['sha256'] and archive.stat().st_size == metadata['bytes'],
                'Embedded runtime checksum/length mismatch')
        with tarfile.open(archive, 'r:xz') as stream:
            members = stream.getmembers()
            listing = {member.name.removeprefix('./'): member for member in members}
            require(len(members) < 100000, 'Unexpected runtime archive size')
            for member in members:
                name = Path(member.name)
                require(not name.is_absolute() and '..' not in name.parts and
                        not member.isdev() and not member.isfifo(), 'Unsafe runtime archive entry')
                require(not any(part in member.name for part in ('guest_app_store', 'guest_toolkit', 'guest_product')),
                        'Test fixture embedded in app runtime')
            for name in REQUIRED:
                require(name in listing, 'Missing application component: ' + name)
            for name in REQUIRED[:13]:
                require(listing[name].isfile() or listing[name].issym(), 'Invalid runtime executable: ' + name)
            def content(name):
                return stream.extractfile(listing[name]).read()
            require(json.loads(content('etc/flashos-app-root')) ==
                    {'format': 1, 'suite': 'trixie', 'role': 'applications'}, 'App root marker is invalid')
            require(b'exit 101' in content('usr/sbin/policy-rc.d'), 'Runtime allows package services')
            sources = content('etc/apt/sources.list').decode()
            for section in ('trixie ', 'trixie-updates ', 'trixie-security ', 'trixie-backports ',
                            'main contrib non-free non-free-firmware'):
                require(section in sources, 'Missing official app source: ' + section)
            require(sources.count('signed-by=/usr/share/keyrings/debian-archive-keyring.gpg') == 4,
                    'App sources lack archive key binding')
            passwd = content('etc/passwd').decode()
            require(any(line.startswith('flashos-app:x:1000:1000:') for line in passwd.splitlines()),
                    'Application UID differs from launcher')
            base = json.loads(content('var/lib/flashos-store/base-packages.json'))['packages']
            require(all(name in base for name in ('apt', 'dpkg', 'python3-apt', 'orca', 'espeak-ng', 'gnome-terminal', 'libglib2.0-bin',
                                                   'x11-xkb-utils', 'x11-xserver-utils')),
                    'Runtime package baseline does not protect its controls')
            speech = json.loads(content('var/lib/flashos-store/speech-runtime.json'))
            require(speech.get('format') == 1 and speech.get('engines') == ['rhvoice', 'espeak-ng']
                    and speech.get('rhvoice_voices') == ['Aleksandr', 'Anna', 'Elena', 'Irina', 'Yuriy'],
                    'Application speech engines or voices differ from FlashOS')
            speech_assets = speech.get('assets')
            require(isinstance(speech_assets, dict) and 10 <= len(speech_assets) <= 1000,
                    'Speech runtime asset manifest is missing or unbounded')
            for name in speech_assets:
                require(name in listing and name.startswith(('usr/bin/RHVoice', 'usr/lib/libRHVoice',
                    'usr/lib/speech-dispatcher-modules/sd_rhvoice', 'usr/share/RHVoice/',
                    'usr/share/doc/flashos/rhvoice/')), 'Invalid speech asset: ' + name)
            unpacked = sum(member.size for member in members if member.isfile())
            require(unpacked <= metadata['unpacked_bytes'] * 1.01,
                    'Preflight runtime size underestimates required USB storage')
        # One sequential pass avoids repeatedly decompressing the seed for every voice file.
        checked_assets = set()
        with tarfile.open(archive, 'r|xz') as stream:
            for member in stream:
                name = member.name.removeprefix('./')
                if name not in speech_assets:
                    continue
                expected = speech_assets[name]
                require(isinstance(expected, dict), 'Invalid speech asset descriptor')
                if 'link' in expected:
                    require(member.issym() and member.linkname == expected['link'],
                            'Speech library link mismatch: ' + name)
                else:
                    require(member.isfile() and member.size == expected.get('bytes'),
                            'Speech component size/type mismatch: ' + name)
                    with stream.extractfile(member) as content_stream:
                        require(hashlib.file_digest(content_stream, 'sha256').hexdigest() == expected.get('sha256'),
                                'Speech component hash mismatch: ' + name)
                checked_assets.add(name)
        require(checked_assets == set(speech_assets), 'Speech runtime archive is incomplete')
    host_passwd = subprocess.check_output(['unsquashfs', '-cat', str(image), 'etc/passwd']).decode()
    host_group = subprocess.check_output(['unsquashfs', '-cat', str(image), 'etc/group']).decode()
    require(any(line.startswith('flashos-app:x:1000:1000:') for line in host_passwd.splitlines()),
            'Host application UID differs from runtime')
    require(any(line.startswith('pipewire:') and 'flashos-app' in line.split(':')[-1].split(',')
                for line in host_group.splitlines()), 'Application user cannot access system PipeWire socket')
    with image.open('rb') as stream:
        image_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'verified': True, 'filesystem_sha256': image_digest, 'runtime': metadata,
            'base_package_count': len(base), 'required_commands': list(REQUIRED),
            'separate_root': True, 'official_signed_sources': True, 'unprivileged_uid': 1000,
            'speech_engines': speech['engines'], 'speech_voices': speech['rhvoice_voices'],
            'speech_assets_verified': len(checked_assets)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    path = args.project / 'dist/app-store-build-verification.json'
    try:
        result = verify(args.project, args.project / 'dist/usb/flashos/filesystem.squashfs')
    except Exception as error:
        path.write_text(json.dumps({'verified': False, 'error': str(error)}, indent=2) + '\n')
        raise
    path.write_text(json.dumps(result, indent=2) + '\n')
    print('Verified portable Debian app runtime and signed sources.')


if __name__ == '__main__':
    main()
