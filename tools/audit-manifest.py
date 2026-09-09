"""Record the exact official components used by the diagnostic update."""
import hashlib
import json
from pathlib import Path

project = Path(__file__).resolve().parents[1]
rootfs = Path('/opt/flashos-rootfs')
packages = dict(line.split('\t', 1) for line in (project / 'dist/packages.tsv').read_text().splitlines())
names = ['smartmontools', 'nvme-cli', 'exfatprogs', 'lsscsi', 'sg3-utils', 'iw', 'rfkill', 'curl', 'gpg',
         'memtester', 'stress-ng', 'gddrescue', 'lm-sensors', 'ethtool', 'dhcpcd-base']
versions = {key: value for key, value in packages.items() if key.split(':')[0] in names or key.startswith('firmware-')}
database = rootfs / 'var/lib/smartmontools/drivedb/drivedb.h'
result = {'components': versions, 'database_sha256': hashlib.sha256(database.read_bytes()).hexdigest(),
          'database_update_log': Path('/tmp/flashos-drivedb-update.log').read_text(),
          'sources': ['https://deb.debian.org/debian/', 'https://security.debian.org/debian-security/',
                      'https://github.com/smartmontools/smartmontools', 'https://github.com/linux-nvme/nvme-cli'],
          'system_files': {}}
for name in ['vmlinuz', 'initrd', 'filesystem.squashfs']:
    path = project / 'dist/usb/flashos' / name
    with path.open('rb') as stream:
        result['system_files'][name] = {'bytes': path.stat().st_size, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
verification = json.loads((project / 'dist/toolkit-build-verification.json').read_text())
result['source_verification'] = verification
(project / 'dist/audit-component-manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print('Recorded', len(versions), 'package versions and final system hashes.')
