"""Verify the staged squashfs contains the reviewed sources and no VM fixture."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


project = Path(__file__).resolve().parents[1]
image = project / 'dist/usb/flashos/filesystem.squashfs'
hashes = {}
system_version = None
for source in sorted((project / 'src').glob('*.py')):
    content = subprocess.check_output(['unsquashfs', '-cat', str(image), 'opt/flashos/' + source.name])
    require(content == source.read_bytes(), 'Stale source in squashfs: ' + source.name)
    hashes[source.name] = hashlib.sha256(content).hexdigest()
    if source.name == 'help_content.py':
        assignments = [node for node in ast.parse(content).body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets)]
        require(len(assignments) == 1, 'Expected one live system version')
        system_version = ast.literal_eval(assignments[0].value)
        require(isinstance(system_version, str) and system_version, 'Invalid live system version')
listing = subprocess.check_output(['unsquashfs', '-ls', str(image)], text=True)
for fixture in ('guest_toolkit.py', 'flashos-toolkit-fixture.service', 'guest_product.py',
                'guest_app_store.py', 'guest_app_observer.py', 'guest_network_qa.py',
                'guest_network_config_qa.py', 'guest_speech_qa.py',
                'zz-speech-orca-qa.squashfs', 'flashos-speech-qa', 'probe-apt-unit.py',
                'flashos-app-store-acceptance.service', 'flashos-app-observer.service', 'usr/sbin/hostapd', 'usr/sbin/dnsmasq', 'guest_recovery.py', 'guest_graphics.py'):
    require(fixture not in listing, 'Test component in squashfs: ' + fixture)
require('etc/systemd/system/smartmontools.service' in listing, 'Missing smartmontools service')
require('usr/bin/RHVoice-test' in listing, 'Missing RHVoice runtime')
service = subprocess.check_output(['unsquashfs', '-cat', str(image), 'etc/systemd/system/flashos.service'])
require(b'LimitMEMLOCK=infinity' in service, 'Missing memlock setting')
require(service == (project / 'boot/flashos.service').read_bytes(), 'Stale FlashOS service')
store_service = subprocess.check_output(['unsquashfs', '-cat', str(image), 'etc/systemd/system/flashos-apps.service'])
require(store_service == (project / 'boot/flashos-apps.service').read_bytes(), 'Stale app storage cleanup service')

for name in ['initrd', 'vmlinuz']:
    require((project / 'dist/usb/flashos' / name).read_bytes() == (project / 'dist/boot/flashos' / name).read_bytes(),
            'Boot partition mismatch: ' + name)
with image.open('rb') as stream:
    image_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
require(system_version is not None, 'Live system version was not verified')
hardware = json.loads((project / 'dist/hardware-build-verification.json').read_text())
require(hardware.get('verified') is True, 'Hardware verification must pass before source certification')
for name in ('filesystem.squashfs', 'vmlinuz', 'initrd'):
    path = project / 'dist/usb/flashos' / name
    with path.open('rb') as stream:
        require(hardware['artifact_sha256'].get(name) == hashlib.file_digest(stream, 'sha256').hexdigest(),
                'Stale hardware verification: ' + name)
store = json.loads((project / 'dist/app-store-build-verification.json').read_text())
require(store.get('verified') is True and store.get('filesystem_sha256') == image_hash,
        'Application runtime certification is absent or stale')
result = {'app_store': store, 'sources_match': True, 'no_test_fixture': True, 'sources_sha256': hashes,
          'squashfs_sha256': image_hash, 'system_version': system_version, 'hardware': hardware}
(project / 'dist/toolkit-build-verification.json').write_text(json.dumps(result, indent=2))
print('Reviewed sources match the squashfs; VM test components are absent.')
