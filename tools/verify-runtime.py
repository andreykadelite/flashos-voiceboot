"""Run inside the build chroot to verify every menu command is installed."""
import shutil
import json
import subprocess

commands = ['lshw', 'lscpu', 'free', 'dmidecode', 'lsblk', 'blkid', 'lspci', 'lsusb',
    'aplay', 'wpctl', 'ip', 'efibootmgr', 'uname', 'journalctl', 'systemctl', 'uptime', 'df',
    'stdbuf', 'memtester', 'stress-ng', 'smartctl', 'e2fsck', 'fsck.fat', 'ntfsfix',
    'xfs_repair', 'btrfs', 'dd', 'ping', 'getent', 'sfdisk', 'findmnt', 'mount', 'umount',
    'ddrescue', 'ddrescuelog', 'dhcpcd', 'RHVoice-test', 'espeak-ng', 'pw-play',
    'fsck.exfat', 'nvme', 'curl', 'sensors', 'ethtool', 'lsscsi', 'iw', 'rfkill', 'cat', 'test',
    'nmcli', 'wpa_supplicant', 'rsync', 'dig', 'traceroute', 'hwinfo', 'mtr', 'photorec',
    'partclone.extfs', 'partclone.fat', 'partclone.exfat', 'partclone.ntfs', 'partclone.xfs',
    'partclone.btrfs', 'partclone.chkimg', 'partclone.restore', 'partclone.info', 'Xorg', 'xinit', 'blockdev', 'udevadm']
missing = [name for name in commands if not shutil.which(name)]
assert not missing, 'Missing menu commands: ' + ', '.join(missing)
print('Verified menu runtime commands:', len(commands))
import tkinter
print('Tkinter:', tkinter.TkVersion)
probe = subprocess.run(['smartctl', '--json=ov', '--version'], capture_output=True, text=True, timeout=5, check=True)
assert json.loads(probe.stdout)['smartctl']['output'], 'SMART JSON options did not preserve the original text'
print('Verified actual smartctl JSON option parsing without querying hardware.')
