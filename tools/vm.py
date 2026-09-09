#!/usr/bin/env python3
"""QEMU smoke tests; the VM receives only the disposable image, never host disks."""
import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import time

root = Path(__file__).resolve().parents[1]
sock = '/tmp/flashos-qmp.sock'


def command(name, arguments=None):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(sock)
        stream = connection.makefile('rwb', buffering=0)
        stream.readline()
        stream.write(b'{"execute":"qmp_capabilities"}\n')
        while 'return' not in json.loads(stream.readline()):
            pass
        request = {'execute': name}
        if arguments:
            request['arguments'] = arguments
        stream.write(json.dumps(request).encode() + b'\n')
        while True:
            response = json.loads(stream.readline())
            if 'return' in response or 'error' in response:
                return response


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['bios', 'uefi', 'secure', 'debug', 'screen', 'key', 'quit', 'status', 'add-usb-audio', 'remove-usb-audio'])
    parser.add_argument('argument', nargs='?')
    parser.add_argument('--memory', type=int, choices=[2048, 4096, 8192], default=2048)
    parser.add_argument('--label', choices=['default', 'windows', 'rescan', 'audio', 'audio-usb', 'audio-none', 'audio-ac97', 'toolkit', 'toolkit-final', 'audit', 'product', 'recovery'], default='default')
    parser.add_argument('--recovery-fixture', action='store_true')
    parser.add_argument('--audit-fixture', action='store_true', help='Disposable NVMe file and QEMU user network, no host disks')
    parser.add_argument('--audio', choices=['hda', 'usb', 'ac97', 'none'], default='hda')
    parser.add_argument('--toolkit-fixture', action='store_true', help='Attach only the fixed disposable toolkit fixture image')
    args = parser.parse_args()
    if args.action in ('bios', 'uefi', 'secure', 'debug'):
        if Path(sock).exists():
            raise SystemExit('An existing VM socket must be checked before starting another VM')
        prefix = f'vm-{args.action}' + (f'-{args.label}' if args.label != 'default' else '')
        cmd = ['qemu-system-x86_64', '-machine', 'q35', '-m', str(args.memory), '-smp', '2', '-cpu', 'max',
               '-display', 'none', '-daemonize', '-net', 'none',
               '-qmp', f'unix:{sock},server=on,wait=off',
               '-serial', f'file:{root}/dist/{prefix}.serial.log',
               '-drive', f'file={root}/dist/flashos-test.img,format=raw,if=none,id=usb',
               '-device', 'qemu-xhci', '-device', 'usb-storage,drive=usb,bootindex=1',
               '-audiodev', f'wav,id=snd,path={root}/dist/{prefix}.wav']
        if args.toolkit_fixture:
            fixture = root / 'dist/toolkit-fixture.img'
            if not fixture.is_file() or fixture.stat().st_size != 256 * 1024**2:
                raise SystemExit('Create the disposable toolkit fixture first')
            cmd += ['-drive', f'file={fixture},format=raw,if=none,id=fixture',
                    '-device', 'virtio-blk-pci,drive=fixture,serial=FLASHOS-TEST-FIXTURE,bootindex=2']
        if args.audit_fixture:
            fixture = root / 'dist/audit-nvme.img'
            if not fixture.exists():
                with fixture.open('xb') as stream:
                    stream.truncate(64 * 1024**2)
            if fixture.is_symlink() or not fixture.is_file() or fixture.stat().st_size != 64 * 1024**2:
                raise SystemExit('Invalid disposable NVMe fixture')
            cmd += ['-drive', f'file={fixture},format=raw,if=none,id=auditnvme',
                    '-device', 'nvme,drive=auditnvme,serial=FLASHOS-AUDIT-NVME',
                    '-netdev', 'user,id=auditnet', '-device', 'e1000e,netdev=auditnet']
        if args.recovery_fixture:
            fixture = root / 'dist/recovery-target.img'
            if fixture.is_symlink() or not fixture.is_file() or fixture.stat().st_size != 256 * 1024**2:
                raise SystemExit('Invalid disposable restore target')
            cmd += ['-drive', f'file={fixture},format=raw,if=none,id=restoretarget',
                    '-device', 'virtio-blk-pci,drive=restoretarget,serial=FLASHOS-RESTORE']
        if args.audio == 'hda':
            cmd += ['-device', 'ich9-intel-hda', '-device', 'hda-duplex,audiodev=snd']
        elif args.audio == 'usb':
            cmd += ['-device', 'usb-audio,id=audio-usb,audiodev=snd']
        elif args.audio == 'ac97':
            cmd += ['-device', 'AC97,audiodev=snd']
        if args.action == 'debug':
            cmd += ['-kernel', str(root / 'dist/usb/flashos/vmlinuz'), '-initrd', str(root / 'dist/usb/flashos/initrd'),
                    '-append', 'boot=live live-media-path=/flashos debug=1 console=ttyS0,115200 systemd.log_target=console']
        elif args.action != 'bios':
            suffix = '.ms' if args.action == 'secure' else ''
            varfile = root / f'dist/vm-{args.action}.vars.fd'
            if not varfile.exists():
                shutil.copyfile(f'/usr/share/OVMF/OVMF_VARS_4M{suffix}.fd', varfile)
            codefile = '/usr/share/OVMF/OVMF_CODE_4M' + ('.secboot' if args.action == 'secure' else '') + '.fd'
            cmd += ['-drive', f'if=pflash,format=raw,readonly=on,file={codefile}',
                    '-drive', f'if=pflash,format=raw,file={varfile}']
            if args.action == 'secure':
                cmd += ['-global', 'driver=cfi.pflash01,property=secure,value=on']
        subprocess.run(cmd, check=True)
    elif args.action == 'screen':
        print(command('screendump', {'filename': str(root / 'dist' / (args.argument or 'vm-screen.png')), 'format': 'png'}))
    elif args.action == 'key':
        print(command('human-monitor-command', {'command-line': 'sendkey ' + args.argument}))
        time.sleep(1)
    elif args.action == 'add-usb-audio':
        print(command('device_add', {'driver': 'usb-audio', 'id': 'audio-usb', 'audiodev': 'snd'}))
    elif args.action == 'remove-usb-audio':
        print(command('device_del', {'id': 'audio-usb'}))
    else:
        print(command('query-status' if args.action == 'status' else 'quit'))
