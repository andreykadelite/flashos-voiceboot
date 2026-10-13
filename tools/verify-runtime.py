"""Run inside the build chroot to verify every menu command is installed."""
import shutil
import json
import subprocess
import hashlib

commands = ['lshw', 'lscpu', 'free', 'dmidecode', 'lsblk', 'blkid', 'lspci', 'lsusb',
    'aplay', 'wpctl', 'ip', 'efibootmgr', 'uname', 'journalctl', 'systemctl', 'uptime', 'df',
    'stdbuf', 'memtester', 'stress-ng', 'smartctl', 'e2fsck', 'fsck.fat', 'ntfsfix',
    'xfs_repair', 'btrfs', 'dd', 'ping', 'getent', 'sfdisk', 'findmnt', 'mount', 'umount',
    'ddrescue', 'ddrescuelog', 'dhcpcd', 'RHVoice-test', 'espeak-ng', 'pw-play',
    'fsck.exfat', 'nvme', 'curl', 'sensors', 'ethtool', 'lsscsi', 'iw', 'rfkill', 'cat', 'test',
    'nmcli', 'wpa_supplicant', 'rsync', 'dig', 'traceroute', 'hwinfo', 'mtr', 'photorec',
    'partclone.extfs', 'partclone.fat', 'partclone.exfat', 'partclone.ntfs', 'partclone.xfs',
    'partclone.btrfs', 'partclone.chkimg', 'partclone.restore', 'partclone.info', 'Xorg', 'xinit', 'blockdev', 'udevadm', 'losetup', 'mkfs.ext4', 'resize2fs', 'systemd-run', 'xhost']
missing = [name for name in commands if not shutil.which(name)]
assert not missing, 'Missing menu commands: ' + ', '.join(missing)
print('Verified menu runtime commands:', len(commands))
import tkinter
print('Tkinter:', tkinter.TkVersion)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
assert shutil.which('busctl'), 'Missing NetworkManager checkpoint control'
key = AESGCM.generate_key(bit_length=256)
assert AESGCM(key).decrypt(b'0' * 12, AESGCM(key).encrypt(b'0' * 12, b'probe', b'FlashOS'), b'FlashOS') == b'probe'
assert len(hashlib.scrypt(b'FlashOS runtime probe', salt=b'0' * 16, n=2**17,
                         r=8, p=1, maxmem=256 * 1024**2, dklen=32)) == 32
print('Verified AES-256-GCM network vault and NetworkManager D-Bus control.')
probe = subprocess.run(['smartctl', '--json=ov', '--version'], capture_output=True, text=True, timeout=5, check=True)
assert json.loads(probe.stdout)['smartctl']['output'], 'SMART JSON options did not preserve the original text'
print('Verified actual smartctl JSON option parsing without querying hardware.')
