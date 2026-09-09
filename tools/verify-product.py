"""Assemble evidence from completed tests of the final production image."""
import hashlib
import json
from pathlib import Path
import subprocess
import re
from datetime import datetime, timezone

root = Path(__file__).resolve().parents[1]
dist = root / 'dist'
verification = json.loads((dist / 'toolkit-build-verification.json').read_text())
assert verification['sources_match'] and verification['no_test_fixture']
unit_log = (dist / 'product-unit-tests.log').read_text()
assert unit_log.strip().endswith('OK')
unit_count = int(re.search(r'Ran (\d+) tests', unit_log)[1])
for name, expected in verification['sources_sha256'].items():
    assert hashlib.sha256((root / 'src' / name).read_bytes()).hexdigest() == expected
results = json.loads((dist / 'product-secure-fixture.json').read_text())
by_name = {item['name']: item for item in results}
required = ['hardware-cpu', 'inventory-memory', 'inventory-disks', 'read-only-filesystem', 'sampled-read',
    'partition-table', 'smart-nvme', 'dhcp', 'network', 'files-mount-readonly', 'files-list-and-text',
    'files-copy-verified', 'files-unmount', 'reports-export', 'network-target-local', 'wifi-scan-hwsim',
    'wifi-wpa2-dhcp-hwsim', 'ddrescue-image', 'source-and-copy-hashes', 'installed-loader']
for name in required:
    assert name in by_name and not by_name[name]['failed'], name
assert by_name['smart-unsupported']['failed'], 'Unsupported SMART must not appear healthy'
secure = (dist / 'vm-secure-product.serial.log').read_text(errors='replace')
bios = (dist / 'vm-bios-product.serial.log').read_text(errors='replace')
assert 'FLASHOS_TOOLKIT_FIXTURE_DONE' in secure and 'secure_boot=1 menu=main' in secure
assert 'FLASHOS_READY' in bios and 'secure_boot=0 menu=main' in bios
packages = subprocess.check_output(['chroot', '/opt/flashos-rootfs', 'dpkg-query', '-W',
    'network-manager', 'wpasupplicant', 'rsync', 'bind9-dnsutils', 'traceroute', 'smartmontools', 'nvme-cli'], text=True)
hashes = {}
for name in ['filesystem.squashfs', 'initrd', 'vmlinuz']:
    with (dist / 'usb/flashos' / name).open('rb') as stream:
        hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
result = {'completed_utc': datetime.now(timezone.utc).isoformat(), 'version': '2026.09.09',
    'unit_tests_passed': unit_count,
    'integration_scenarios': len(results), 'required_scenarios_verified': required,
    'secure_boot_menu': True, 'bios_menu': True, 'no_test_components_on_production': True,
    'physical_host_rebooted': False, 'hardware_wifi_test': 'mac80211_hwsim in QEMU; physical Wi-Fi not tested',
    'packages': packages.splitlines(), 'production_sha256': hashes,
    'source_sha256': verification['sources_sha256']}
(dist / 'product-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print('Verified final production sources, integration scenarios, Secure Boot and BIOS boot evidence.')
