#!/usr/bin/env python3
"""Alter only the disposable VM disk for copy/remove and video regression checks."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess

project = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action', choices=['cli', 'hide-debian', 'hide-all', 'restore', 'verify', 'update-system', 'diagnostics', 'fixture-on', 'fixture-off', 'reports', 'repair-test-state'])
args = parser.parse_args()
image = project / 'dist/flashos-test.img'
if Path('/tmp/flashos-qmp.sock').exists():
    raise SystemExit('Stop the VM before changing the disposable disk')
assert image.resolve().parent == (project / 'dist').resolve() and image.is_file()
if subprocess.check_output(['losetup', '-j', str(image)], text=True).strip():
    raise SystemExit('The test image already has a loop device')
mount = Path('/mnt/flashos-test-edit')
mount.mkdir(exist_ok=True)
if subprocess.run(['mountpoint', '-q', str(mount)]).returncode == 0:
    raise SystemExit('The test mount point is occupied')
loop = subprocess.check_output(['losetup', '--find', '--show', '--partscan', str(image)], text=True).strip()
try:
    if args.action == 'repair-test-state':
        main_result = subprocess.run(['fsck.exfat', '-p', loop + 'p1'], capture_output=True, text=True)
        (project / 'dist/toolkit-test-exfat-repair.log').write_text(main_result.stdout + main_result.stderr)
        if main_result.returncode not in (0, 1):
            raise RuntimeError('Disposable exFAT repair needs inspection')
        print(main_result.stdout)
        result = subprocess.run(['fsck.fat', '-a', loop + 'p3'], capture_output=True, text=True)
        (project / 'dist/toolkit-test-fat-repair.log').write_text(result.stdout + result.stderr)
        if result.returncode not in (0, 1):
            raise RuntimeError('Disposable FAT repair needs inspection')
        print(result.stdout)
    subprocess.run(['mount', loop + 'p1', str(mount)], check=True)
    try:
        assert (mount / 'flashos/flashos.marker').is_file()
        held = mount / 'test-held'
        if args.action == 'fixture-on':
            shutil.copyfile(project / 'dist/zz-toolkit-test.squashfs', mount / 'flashos/zz-toolkit-test.squashfs')
        elif args.action == 'fixture-off':
            (mount / 'flashos/zz-toolkit-test.squashfs').unlink(missing_ok=True)
        elif args.action == 'update-system':
            for name in ('flashos', 'ventoy'):
                shutil.copytree(project / 'dist/usb' / name, mount / name, dirs_exist_ok=True)
        elif args.action == 'cli':
            config = mount / 'ventoy/ventoy.json'
            data = json.loads(config.read_text())
            data['theme'] = {'display_mode': 'CLI'}
            config.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        elif args.action.startswith('hide-'):
            held.mkdir(exist_ok=True)
            for iso in (mount / 'iso').glob('*.iso'):
                if args.action == 'hide-all' or 'debian' in iso.name:
                    target = held / iso.name
                    if target.exists():
                        raise RuntimeError('A test backup already exists')
                    iso.rename(target)
        elif args.action == 'restore':
            for iso in held.glob('*.iso'):
                target = mount / 'iso' / iso.name
                if target.exists():
                    raise RuntimeError('The ISO is already in the test menu')
                iso.rename(target)
        elif args.action == 'verify':
            expected = json.loads((project / 'dist/windows-source.json').read_text(encoding='utf-8-sig'))
            source = mount / 'iso' / expected['File']
            with source.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            assert source.stat().st_size == expected['Bytes']
            assert digest.lower() == expected['SHA256'].lower(), 'The test ISO differs from the physical USB ISO'
            print('Test ISO SHA256 matches physical USB:', digest)
        print('ISO directory:', sorted(p.name for p in (mount / 'iso').glob('*.iso')))
        os.sync()
    finally:
        subprocess.run(['umount', str(mount)], check=True)
    if args.action in ('update-system', 'diagnostics', 'reports'):
        subprocess.run(['mount', loop + 'p3', str(mount)], check=True)
        try:
            if args.action == 'update-system':
                data_uuid = subprocess.check_output(['blkid', '-s', 'UUID', '-o', 'value', loop + 'p1'], text=True).strip()
                shutil.copytree(project / 'dist/boot', mount, dirs_exist_ok=True)
                for config in [mount / 'flashos/efi/grub.cfg', mount / 'EFI/debian/grub.cfg']:
                    content = config.read_text()
                    assert content.count('@DATA_UUID@') == 1
                    config.write_text(content.replace('@DATA_UUID@', data_uuid))
            elif args.action == 'reports':
                folder = project / 'dist/toolkit-guest-reports'
                folder.mkdir(exist_ok=True)
                for file in (mount / 'flashos').glob('*.json'):
                    shutil.copyfile(file, folder / file.name)
                shutil.copytree(mount / 'flashos/reports', folder / 'reports', dirs_exist_ok=True)
                print('Guest reports copied to', folder)
            else:
                report = mount / 'flashos/audio-last.json'
                shutil.copyfile(report, project / 'dist/audio-guest-last.json')
                data = json.loads(report.read_text())
                print(json.dumps({k: v for k, v in data.items() if k != 'pipewire'}, ensure_ascii=False, indent=2))
            os.sync()
        finally:
            subprocess.run(['umount', str(mount)], check=True)
finally:
    subprocess.run(['losetup', '-d', loop], check=True)
