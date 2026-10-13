#!/usr/bin/env python3
"""Copy FlashOS's reviewed RHVoice runtime into the separate app seed."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def provision(live, runtime):
    live, runtime = live.resolve(), runtime.resolve()
    if live == runtime or not all((root / 'etc/debian_version').is_file() for root in (live, runtime)):
        raise ValueError('Expected distinct provisioned Debian live and application roots')
    libraries = sorted((live / 'usr/lib').glob('libRHVoice*.so*'))
    if not libraries:
        raise ValueError('The reviewed live RHVoice libraries are missing')
    assets = [Path('usr/bin/RHVoice-test'), Path('usr/lib/speech-dispatcher-modules/sd_rhvoice'),
              Path('usr/share/RHVoice'), Path('usr/share/doc/flashos/rhvoice')]
    assets += [path.relative_to(live) for path in libraries]
    for relative in assets:
        source = live / relative
        if not source.exists() or not source.resolve().is_relative_to(live):
            raise ValueError('Missing or external speech component: ' + str(relative))
        if source.is_symlink() and Path(source.readlink()).parts != (source.readlink().name,):
            raise ValueError('RHVoice library link must name another shipped library')
    manifest = {}
    for relative in assets:
        source, destination = live / relative, runtime / relative
        if not destination.parent.resolve().is_relative_to(runtime):
            raise ValueError('Application speech destination escapes its root')
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            destination.unlink(missing_ok=True)
            destination.symlink_to(source.readlink())
        elif source.is_dir():
            shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
        entries = sorted(source.rglob('*')) if source.is_dir() else [source]
        for entry in entries:
            name = entry.relative_to(live).as_posix()
            copied = runtime / name
            if entry.is_symlink():
                if entry.readlink() != copied.readlink():
                    raise ValueError('Speech link differs from the live version: ' + name)
                manifest[name] = {'link': str(entry.readlink())}
            elif entry.is_file():
                digest = hashlib.sha256(entry.read_bytes()).hexdigest()
                if hashlib.sha256(copied.read_bytes()).hexdigest() != digest:
                    raise ValueError('Copied speech data differs: ' + name)
                manifest[name] = {'sha256': digest, 'bytes': entry.stat().st_size}
    subprocess.run(['chroot', str(runtime), 'ldconfig'], check=True)
    for executable in ('/usr/lib/speech-dispatcher-modules/sd_rhvoice', '/usr/bin/RHVoice-test',
                       '/usr/lib/speech-dispatcher-modules/sd_espeak-ng', '/usr/bin/espeak-ng'):
        output = subprocess.check_output(['chroot', str(runtime), 'ldd', executable], text=True)
        if 'not found' in output:
            raise ValueError('Missing speech dependency for ' + executable + ': ' + output)
    result = subprocess.run(['chroot', str(runtime), '/usr/lib/speech-dispatcher-modules/sd_rhvoice'],
                            input='INIT\nLIST VOICES\nQUIT\n', text=True, capture_output=True, timeout=15)
    voices = ('Aleksandr', 'Anna', 'Elena', 'Irina', 'Yuriy')
    if result.returncode or not all(voice in result.stdout for voice in voices):
        raise ValueError('The RHVoice Speech Dispatcher module did not enumerate all five voices')
    record = {'format': 1, 'engines': ['rhvoice', 'espeak-ng'], 'rhvoice_version': '1.18.4',
              'rhvoice_voices': list(voices), 'assets': manifest,
              'dependencies_resolved': True, 'voice_protocol_verified': True}
    destination = runtime / 'var/lib/flashos-store/speech-runtime.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    arguments = parser.parse_args()
    record = provision(arguments.live, arguments.runtime)
    print(json.dumps({'engines': record['engines'], 'voices': record['rhvoice_voices'],
                      'verified_assets': len(record['assets'])}))
