#!/usr/bin/env python3
"""Create an isolated QEMU disk and test-only live overlay. Never installed on USB."""
from pathlib import Path
import shutil
import subprocess
import struct
import zlib

project = Path(__file__).resolve().parents[1]
image = project / 'dist/toolkit-fixture.img'
if Path('/tmp/flashos-qmp.sock').exists():
    raise SystemExit('Stop QEMU first')
for command in ['parted', 'losetup', 'mkfs.vfat', 'mkfs.ext4', 'mount', 'umount', 'mksquashfs']:
    if not shutil.which(command):
        raise SystemExit('Install fixture dependency: ' + command)
if not image.exists():
    with image.open('xb') as stream:
        stream.truncate(256 * 1024**2)
    subprocess.run(['parted', '-s', str(image), 'mklabel', 'gpt', 'mkpart', 'ESP', 'fat32', '1MiB', '65MiB',
                    'set', '1', 'esp', 'on', 'mkpart', 'fixture', 'ext4', '65MiB', '100%'], check=True)
    loop = subprocess.check_output(['losetup', '--find', '--show', '--partscan', str(image)], text=True).strip()
    mount = Path('/mnt/flashos-toolkit-fixture')
    mount.mkdir(exist_ok=True)
    try:
        assert not subprocess.run(['mountpoint', '-q', str(mount)]).returncode == 0
        subprocess.run(['mkfs.vfat', '-F', '32', '-n', 'TEST-ESP', loop + 'p1'], check=True)
        subprocess.run(['mkfs.ext4', '-F', '-L', 'TEST-DATA', loop + 'p2'], check=True)
        subprocess.run(['mount', loop + 'p1', str(mount)], check=True)
        try:
            efi = mount / 'EFI/fixture'
            efi.mkdir(parents=True)
            for name in ['BOOTX64.EFI', 'grubx64.efi', 'mmx64.efi']:
                shutil.copyfile(project / 'dist/boot/flashos/efi' / name, efi / name)
            config = 'serial --unit=0 --speed=115200\nterminal_output console serial\necho FLASHOS_INSTALLED_FIXTURE_BOOTED\nsleep 300\nhalt\n'
            (efi / 'grub.cfg').write_text(config)
            debian = mount / 'EFI/debian'
            debian.mkdir()
            (debian / 'grub.cfg').write_text(config)
        finally:
            subprocess.run(['umount', str(mount)], check=True)
    finally:
        subprocess.run(['losetup', '-d', loop], check=True)
overlay = project / 'build/toolkit-test-overlay'
overlay.mkdir(parents=True, exist_ok=True)
# These daemons exist only in the test overlay and never in the production image.
dependency_folder = Path('/opt/flashos-rootfs/tmp/flashos-test-debs')
dependency_folder.mkdir(exist_ok=True)
dependencies = ['hostapd', 'dnsmasq-base', 'libnetfilter-conntrack3', 'libnfnetlink0',
                'libnftables1', 'libnftnl11', 'libjansson4', 'libxtables12', 'libmnl0']
missing = [name for name in dependencies if not list(dependency_folder.glob(name + '_*.deb'))]
if missing:
    subprocess.run(['chroot', '/opt/flashos-rootfs', 'sh', '-c',
        'cd /tmp/flashos-test-debs && exec apt-get download "$@"', 'sh', *missing], check=True)
for package in dependency_folder.glob('*.deb'):
    subprocess.run(['dpkg-deb', '-x', str(package), str(overlay)], check=True)
loop = subprocess.check_output(['losetup', '--find', '--show', '--partscan', str(image)], text=True).strip()
mount = Path('/mnt/flashos-toolkit-fixture')
mount.mkdir(exist_ok=True)
try:
    assert subprocess.run(['mountpoint', '-q', str(mount)]).returncode != 0
    subprocess.run(['mount', loop + 'p2', str(mount)], check=True)
    try:
        folder = mount / 'Documents'
        folder.mkdir(exist_ok=True)
        (folder / 'Пример.txt').write_text('Проверка копирования файлов FlashOS.\n', encoding='utf-8')
        (folder / 'data.bin').write_bytes(bytes(range(256)) * 4096)
        def chunk(tag, data):
            return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 2, 0, 0, 0))
        png += chunk(b'IDAT', zlib.compress((b'\0' + bytes(range(192))) * 64)) + chunk(b'IEND', b'')
        (folder / 'recovery.png').write_bytes(png)
        if not (folder / 'skip-link').is_symlink():
            (folder / 'skip-link').symlink_to('/etc/shadow')
    finally:
        subprocess.run(['umount', str(mount)], check=True)
finally:
    subprocess.run(['losetup', '-d', loop], check=True)
script = overlay / 'opt/flashos/guest_toolkit.py'
script.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(project / 'tests/guest_toolkit.py', script)
shutil.copyfile(project / 'tests/guest_product.py', script.with_name('guest_product.py'))
shutil.copyfile(project / 'tests/guest_recovery.py', script.with_name('guest_recovery.py'))
shutil.copyfile(project / 'tests/guest_graphics.py', script.with_name('guest_graphics.py'))
# Candidate module is tested before the next expensive production repack.
for source in (project / 'src').glob('*.py'):
    shutil.copyfile(source, script.with_name(source.name))
# F12 fault injection is confined to the QEMU overlay; never part of production.
menu = script.with_name('menu.py')
text = menu.read_text()
text = text.replace('    ui = UI(screen, speech, settings, state_root, demo)\n',
    '    ui = UI(screen, speech, settings, state_root, demo)\n    from guest_graphics import verify\n    verify(ui)\n')
menu.write_text(text)
interface = script.with_name('interface.py')
text = interface.read_text().replace('    def global_key(self, key):\n',
    '    def global_key(self, key):\n        if key == curses.KEY_F12 and getattr(self.screen, "graphical", False):\n            from guest_graphics import fault\n            fault(self)\n            return True\n')
interface.write_text(text)
target = project / 'dist/recovery-target.img'
if not target.exists():
    with target.open('xb') as stream:
        stream.truncate(256 * 1024**2)
    subprocess.run(['parted', '-s', str(target), 'mklabel', 'gpt', 'mkpart', 'restore', 'ext4', '1MiB', '100%'], check=True)
units = overlay / 'etc/systemd/system'
units.mkdir(parents=True, exist_ok=True)
shutil.copyfile(project / 'boot/flashos.service', units / 'flashos.service')
(units / 'flashos-toolkit-fixture.service').write_text('''[Unit]
Description=Disposable QEMU toolkit integration checks
After=systemd-udev-settle.service NetworkManager.service
Wants=systemd-udev-settle.service NetworkManager.service
Before=flashos.service
ConditionVirtualization=qemu
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/flashos/guest_toolkit.py
TimeoutStartSec=1800
StandardOutput=append:/dev/ttyS0
StandardError=append:/dev/ttyS0
''')
wants = units / 'multi-user.target.wants'
wants.mkdir(exist_ok=True)
link = wants / 'flashos-toolkit-fixture.service'
if not link.is_symlink():
    link.symlink_to('../flashos-toolkit-fixture.service')
subprocess.run(['mksquashfs', str(overlay), str(project / 'dist/zz-toolkit-test.squashfs'), '-noappend', '-comp', 'xz'], check=True)
