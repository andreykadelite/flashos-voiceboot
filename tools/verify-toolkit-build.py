"""Verify the staged squashfs contains the reviewed sources and no VM fixture."""
import ast
import hashlib
import json
import lzma
from pathlib import Path
import subprocess
import sys
import tempfile

from public_payload import BOOT_ARTIFACTS, BOOT_ROOT_CONFIGS, boot_source_hashes, python_source_hashes, verify_boot_configs


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
for name, target in BOOT_ROOT_CONFIGS.items():
    content = subprocess.check_output(['unsquashfs', '-cat', str(image), target])
    require(hashlib.sha256(content).hexdigest() in python_source_hashes((project / 'boot' / name).read_bytes()),
            'Stale live configuration: ' + name)
    if name == 'flashos.service':
        require(b'LimitMEMLOCK=infinity' in content, 'Missing memlock setting')
verify_boot_configs(project)
for name, target in {'BOOTX64.EFI': 'usr/lib/shim/shimx64.efi.signed',
                     'grubx64.efi': 'usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed',
                     'mmx64.efi': 'usr/lib/shim/mmx64.efi.signed'}.items():
    content = subprocess.check_output(['unsquashfs', '-cat', str(image), target])
    require(content == (project / 'dist/boot/flashos/efi' / name).read_bytes(), 'Stale signed EFI file: ' + name)
with tempfile.NamedTemporaryFile(suffix='.img') as temporary:
    with lzma.open(project / 'dist/ventoy.disk.img.xz', 'rb') as stream:
        temporary.write(stream.read())
    temporary.flush()
    require(Path(temporary.name).read_bytes() == (project / 'dist/ventoy-efi.img').read_bytes(),
            'Raw Ventoy FAT image differs from the compressed installer image')
    config = subprocess.check_output(['mtype', '-i', temporary.name, '::/grub/grub.cfg'], timeout=30)
oneshot = (project / 'boot/oneshot.cfg').read_bytes().replace(b'\r\n', b'\n')
require(oneshot and config.replace(b'\r\n', b'\n').count(oneshot) == 1,
        'Stale one-shot ISO selection in the Ventoy image')
sys.path.insert(0, str(project / 'src'))
from selection import environment
require((project / 'dist/boot/ventoy/flashos.env').read_bytes() == environment(),
        'Unexpected initial one-shot ISO selection state')

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
terminal = json.loads((project / 'dist/terminal-build-verification.json').read_text())
require(terminal.get('verified') is True and terminal.get('filesystem_sha256') == image_hash,
        'Terminal runtime certification is absent or stale')
result = {'app_store': store, 'terminal': terminal,
          'sources_match': True, 'no_test_fixture': True, 'sources_sha256': hashes,
          'squashfs_sha256': image_hash, 'system_version': system_version, 'hardware': hardware,
          'boot_sources_match': True,
          'boot_sources_sha256': {name: hashlib.sha256((project / 'boot' / name).read_bytes()).hexdigest()
                                 for name in boot_source_hashes(project)},
          'boot_artifact_sha256': {name: hashlib.sha256((project / name).read_bytes()).hexdigest()
                                   for name in BOOT_ARTIFACTS}}
(project / 'dist/toolkit-build-verification.json').write_text(json.dumps(result, indent=2))
print('Reviewed sources match the squashfs; VM test components are absent.')
