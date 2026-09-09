"""Verify the staged squashfs contains the reviewed sources and no VM fixture."""
import hashlib
import json
from pathlib import Path
import subprocess

project = Path(__file__).resolve().parents[1]
image = project / 'dist/usb/flashos/filesystem.squashfs'
hashes = {}
for source in sorted((project / 'src').glob('*.py')):
    content = subprocess.check_output(['unsquashfs', '-cat', str(image), 'opt/flashos/' + source.name])
    assert content == source.read_bytes(), 'Stale source in squashfs: ' + source.name
    hashes[source.name] = hashlib.sha256(content).hexdigest()
listing = subprocess.check_output(['unsquashfs', '-ls', str(image)], text=True)
assert 'guest_toolkit.py' not in listing and 'flashos-toolkit-fixture.service' not in listing
assert 'guest_product.py' not in listing and 'usr/sbin/hostapd' not in listing and 'usr/sbin/dnsmasq' not in listing
assert 'guest_recovery.py' not in listing and 'guest_graphics.py' not in listing
assert 'etc/systemd/system/smartmontools.service' in listing
assert 'usr/bin/RHVoice-test' in listing
assert 'LimitMEMLOCK=infinity' in subprocess.check_output(['unsquashfs', '-cat', str(image),
    'etc/systemd/system/flashos.service'], text=True)
assert subprocess.check_output(['unsquashfs', '-cat', str(image), 'etc/systemd/system/flashos.service']) == (project / 'boot/flashos.service').read_bytes()
for name in ['initrd', 'vmlinuz']:
    assert (project / 'dist/usb/flashos' / name).read_bytes() == (project / 'dist/boot/flashos' / name).read_bytes()
result = {'sources_match': True, 'no_test_fixture': True, 'sources_sha256': hashes}
(project / 'dist/toolkit-build-verification.json').write_text(json.dumps(result, indent=2))
print('Reviewed sources match the squashfs; VM test components are absent.')
