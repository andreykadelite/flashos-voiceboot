#!/usr/bin/env python3
"""Verify shipped Linux drivers offline; never inspect the container's running kernel."""
import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import tempfile

REQUIRED_MODULES = {
    'audio': ['snd_hda_intel', 'snd_usb_audio', 'snd_sof_pci_intel_mtl'],
    'ethernet': ['e1000e', 'igc', 'r8169', 'r8152', 'usbnet', 'cdc_ether'],
    'wifi': ['cfg80211', 'mac80211', 'iwlwifi', 'iwlmvm', 'ath9k', 'ath10k_pci', 'ath11k_pci',
             'ath12k', 'mt7921e', 'mt7925e', 'rtw88_8822ce', 'rtw89_8852be', 'brcmfmac'],
    'graphics': ['i915', 'xe', 'amdgpu', 'radeon', 'nouveau'],
    'storage': ['nvme', 'ahci', 'usb_storage', 'uas', 'xhci_pci'],
}
REQUIRED_PACKAGES = [
    'firmware-linux-free', 'firmware-iwlwifi', 'firmware-realtek', 'firmware-atheros',
    'firmware-mediatek', 'firmware-brcm80211', 'firmware-bnx2', 'firmware-bnx2x',
    'firmware-qlogic', 'firmware-libertas', 'firmware-ti-connectivity',
    'firmware-intel-sound', 'firmware-sof-signed', 'firmware-cirrus',
    'firmware-misc-nonfree', 'firmware-intel-graphics', 'firmware-amd-graphics',
    'firmware-nvidia-graphics', 'intel-microcode', 'amd64-microcode',
    'alsa-ucm-conf', 'alsa-topology-conf', 'wireless-regdb', 'wpasupplicant',
]
NOTEBOOK_DEVICES = [
    {'name': 'Intel Wi-Fi 6E AX211', 'driver': 'iwlwifi',
     'modalias': 'pci:v00008086d00007E40sv00008086sd00000094bc02sc80i00',
     'pci_id': '8086:7e40', 'subsystem': '8086:0094', 'revision': '20'},
    {'name': 'Intel Ethernet I219-V', 'driver': 'e1000e',
     'modalias': 'pci:v00008086d0000550Bsv000017AAsd00003820bc02sc00i00',
     'pci_id': '8086:550b', 'subsystem': '17aa:3820', 'revision': '20'},
]
# Linux cfg/ax210.c uses API 89 for AX211/Meteor Lake, despite modinfo's general max.
# https://github.com/torvalds/linux/blob/v7.1/drivers/net/wireless/intel/iwlwifi/cfg/ax210.c
AX211_FAMILIES = ('iwlwifi-ma-b0-gf-a0', 'iwlwifi-mtl-so-a0-gf-a0')
MICROCODE = ('kernel/x86/microcode/GenuineIntel.bin',
             'kernel/x86/microcode/AuthenticAMD.bin')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def normalize_module(name):
    return name.replace('-', '_')


def listing_paths(listing):
    return {line.strip().removeprefix('squashfs-root/').removeprefix('./')
            for line in listing.splitlines() if line.strip().startswith('squashfs-root/')
            or (line.strip() and not line.startswith(('Parallel ', 'Filesystem ', 'created ')))}


def kernel_versions(paths):
    return {match[1] for path in paths
            if (match := re.search(r'(?:^|/)(?:usr/)?lib/modules/([^/]+)', path))}


def module_inventory(paths, kernel_version, builtins=''):
    inventory = {}
    for path in sorted(set(paths) | set(builtins.splitlines())):
        if path in paths and f'lib/modules/{kernel_version}/' not in path:
            continue
        match = re.search(r'/([^/]+)\.ko(?:\.(?:xz|zst|gz))?$', '/' + path)
        if match:
            inventory[normalize_module(match[1])] = path
    return inventory


def resolve_aliases(alias_text, modalias):
    matches = set()
    for line in alias_text.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == 'alias' and fnmatch.fnmatchcase(modalias, fields[1]):
            matches.add(normalize_module(fields[2]))
    return sorted(matches)


def installed_packages(status):
    installed = {}
    for stanza in re.split(r'\n\s*\n', status):
        fields = dict(line.split(': ', 1) for line in stanza.splitlines()
                      if ': ' in line and not line[0].isspace())
        if fields.get('Status', '').split()[-2:] == ['ok', 'installed'] and 'Package' in fields:
            installed[fields['Package']] = fields.get('Version', '')
    return installed


def check_inventory(paths, kernel_version, initrd_paths, alias_text, status, builtins='',
                    required_modules=None, required_packages=None, devices=None):
    required_modules = REQUIRED_MODULES if required_modules is None else required_modules
    required_packages = REQUIRED_PACKAGES if required_packages is None else required_packages
    devices = NOTEBOOK_DEVICES if devices is None else devices
    require(kernel_version in kernel_versions(paths),
            f'Squashfs has no modules for requested kernel {kernel_version}')
    require(kernel_versions(initrd_paths) == {kernel_version},
            f'Initrd kernel directories {sorted(kernel_versions(initrd_paths))} do not match {kernel_version}')
    modules = module_inventory(paths, kernel_version, builtins)
    missing = sorted({name for group in required_modules.values() for name in group if name not in modules})
    require(not missing, f'Missing shipped kernel drivers: {", ".join(missing)}; install the complete Debian kernel modules')
    packages = installed_packages(status)
    missing = sorted(set(required_packages) - packages.keys())
    require(not missing, f'Missing installed firmware/runtime packages: {", ".join(missing)}; rebuild the live root')
    require(set(MICROCODE) <= initrd_paths,
            'Initrd lacks Intel/AMD early microcode; set both INITRAMFS modes to early, SCANCPUS=no and rebuild it')
    notebook = []
    for device in devices:
        matches = resolve_aliases(alias_text, device['modalias'])
        require(device['driver'] in matches,
                f'{device["name"]} ({device["pci_id"]}) has no {device["driver"]} match in shipped modules.alias')
        notebook.append({**device, 'matched_modules': matches})
    return {'modules': {group: {name: modules[name] for name in names}
                        for group, names in required_modules.items()},
            'installed_packages': {name: packages[name] for name in required_packages},
            'notebook_devices': notebook, 'early_microcode': list(MICROCODE)}


def firmware_path(request, paths, config):
    for base in ('lib/firmware/', 'usr/lib/firmware/'):
        for suffix, option in (('', None), ('.xz', 'CONFIG_FW_LOADER_COMPRESS_XZ=y'),
                               ('.zst', 'CONFIG_FW_LOADER_COMPRESS_ZSTD=y')):
            candidate = base + request + suffix
            if candidate in paths and (option is None or option in config.splitlines()):
                return candidate
    raise RuntimeError(f'Missing loadable firmware {request}; install firmware-iwlwifi and enable its compression in the kernel')


def notebook_firmware(requests, paths, config):
    declared = [name for name in requests if any(name.startswith(family + '-') or name == family + '.pnvm'
                                               for family in AX211_FAMILIES)]
    require(declared, 'iwlwifi module does not declare the AX211/Meteor Lake firmware family')
    for family in AX211_FAMILIES:
        if not any(name.startswith(family + '-') for name in declared):
            continue
        try:
            files = {family + suffix: firmware_path(family + suffix, paths, config)
                     for suffix in ('-89.ucode', '.pnvm')}
            return {'runtime_api': 89, 'family': family, 'declared_by_module': declared, 'files': files}
        except RuntimeError:
            continue
    raise RuntimeError('AX211 firmware API 89 or its PNVM is missing; include ma-b0-gf-a0 (or legacy mtl-so-a0-gf-a0) firmware')


def command(arguments, binary=False):
    try:
        completed = subprocess.run([str(item) for item in arguments], capture_output=True,
                                   text=not binary, check=True, timeout=180)
        return completed.stdout
    except FileNotFoundError as error:
        raise RuntimeError(f'Required build tool missing: {arguments[0]}') from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode(errors='replace') if binary else error.stderr
        raise RuntimeError(f'{arguments[0]} failed: {detail.strip()[:400]}') from error


def image_bytes(image, path, seen=None):
    seen = set() if seen is None else seen
    require(path not in seen and len(seen) < 8, f'Invalid image symlink chain: {path}')
    seen.add(path)
    try:
        return command(['unsquashfs', '-cat', image, path], binary=True)
    except RuntimeError as error:
        listing = command(['unsquashfs', '-ll', image, path])
        match = re.search(r'squashfs-root/' + re.escape(path) + r' -> (.+)$', listing, re.MULTILINE)
        if not match:
            raise error
        target = match[1]
        resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/')
                                     else str(PurePosixPath(path).parent / target))
        require(resolved != '..' and not resolved.startswith('../'), f'Escaping image symlink: {path}')
        return image_bytes(image, resolved, seen)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_load_plan(output, root, module_name):
    """Check modprobe's dry-run output, including every referenced dependency."""
    loaded = []
    for line in output.splitlines():
        fields = line.split()
        if fields[:1] == ['builtin'] and len(fields) == 2:
            loaded.append(normalize_module(fields[1]))
        elif fields[:1] == ['insmod'] and len(fields) >= 2:
            path = Path(fields[1])
            require(path.resolve().is_relative_to(root.resolve()) and path.is_file(),
                    f'Module dependency absent from shipped image: {fields[1]}')
            name = re.fullmatch(r'(.+)\.ko(?:\.(?:xz|zst|gz))?', path.name)
            require(name is not None, f'Invalid module load plan: {line}')
            loaded.append(normalize_module(name[1]))
        else:
            raise RuntimeError(f'Unexpected module load plan for {module_name}: {line}')
    require(module_name in loaded,
            f'{module_name} is blocked or absent in the shipped modprobe indexes/configuration')
    return loaded


def verify_module_resolution(image, paths, module_base, kernel_version):
    # libkmod loads binary indexes; a correct-looking modules.alias text is insufficient.
    for name in ('modules.dep.bin', 'modules.alias.bin', 'modules.builtin.bin'):
        require(module_base + '/' + name in paths, f'Missing shipped modprobe index: {name}')
    with tempfile.TemporaryDirectory(prefix='flashos-modules-') as folder:
        root = Path(folder) / 'root'
        command(['unsquashfs', '-no-progress', '-d', root, image, module_base])
        # Support both Debian usrmerge and an image with a real /lib directory.
        if not (root / 'lib').exists():
            (root / 'lib').symlink_to('usr/lib', target_is_directory=True)
        if not (root / 'usr/lib').exists():
            (root / 'usr').mkdir(exist_ok=True)
            (root / 'usr/lib').symlink_to('../lib', target_is_directory=True)
        config = Path(folder) / 'modprobe.d'
        config.mkdir()
        seen = set()
        for directory in ('etc/modprobe.d/', 'usr/local/lib/modprobe.d/',
                          'usr/lib/modprobe.d/', 'lib/modprobe.d/'):
            for path in sorted(paths):
                if not path.startswith(directory) or not path.endswith('.conf'):
                    continue
                name = PurePosixPath(path).name
                if name not in seen:
                    (config / name).write_bytes(image_bytes(image, path))
                    seen.add(name)
        arguments = ['modprobe', '-d', root, '-S', kernel_version, '-C', config]
        plans = {}
        for name in sorted({name for group in REQUIRED_MODULES.values() for name in group}):
            output = command([*arguments, '--use-blacklist', '--show-depends', name])
            plans[name] = check_load_plan(output, root, name)
        for device in NOTEBOOK_DEVICES:
            aliases = command([*arguments, '--resolve-alias', device['modalias']]).splitlines()
            require(device['driver'] in aliases,
                    f'{device["pci_id"]} lacks {device["driver"]} in the shipped binary alias index')
        return plans


def verify_build(image, initrd, vmlinuz, kernel_version):
    require(bool(re.fullmatch(r'[A-Za-z0-9_.+~-]+', kernel_version)), 'Invalid explicit kernel version')
    for path in (image, initrd, vmlinuz):
        require(path.is_file(), f'Shipped artifact missing: {path}')
    paths = listing_paths(command(['unsquashfs', '-ls', image]))
    initrd_paths = listing_paths(command(['lsinitramfs', initrd]))
    module_base = next((path.removesuffix('/modules.alias') for path in sorted(paths)
                        if path.endswith(f'lib/modules/{kernel_version}/modules.alias')), None)
    require(module_base is not None, f'Missing modules.alias for {kernel_version}')
    decode = lambda path: image_bytes(image, path).decode('utf-8', errors='replace')
    builtins = decode(module_base + '/modules.builtin') if module_base + '/modules.builtin' in paths else ''
    result = check_inventory(paths, kernel_version, initrd_paths, decode(module_base + '/modules.alias'),
                             decode('var/lib/dpkg/status'), builtins)
    exported_hash = sha256(vmlinuz)
    require(hashlib.sha256(image_bytes(image, f'boot/vmlinuz-{kernel_version}')).hexdigest() == exported_hash,
            f'Exported vmlinuz differs from squashfs boot/vmlinuz-{kernel_version}; restage matching boot files')
    require(hashlib.sha256(image_bytes(image, f'boot/initrd.img-{kernel_version}')).hexdigest() == sha256(initrd),
            f'Exported initrd differs from squashfs boot/initrd.img-{kernel_version}; rebuild and restage matching boot files')
    module_path = module_inventory(paths, kernel_version, builtins)['iwlwifi']
    with tempfile.TemporaryDirectory(prefix='flashos-hardware-') as folder:
        module_file = Path(folder) / PurePosixPath(module_path).name
        module_file.write_bytes(image_bytes(image, module_path))
        declarations = command(['modinfo', '-F', 'firmware', module_file]).splitlines()
    firmware = notebook_firmware(declarations, paths, decode(f'boot/config-{kernel_version}'))
    for request, path in firmware['files'].items():
        require(len(image_bytes(image, path)) > 32, f'Empty/truncated AX211 firmware: {request}')
    result['module_load_plans'] = verify_module_resolution(image, paths, module_base, kernel_version)
    result.update({'verified': True, 'completed_utc': datetime.now(timezone.utc).isoformat(),
                   'kernel_version': kernel_version, 'notebook_model': 'Lenovo 21MS',
                   'notebook_firmware': firmware,
                   'artifact_sha256': {'filesystem.squashfs': sha256(image), 'initrd': sha256(initrd),
                                       'vmlinuz': exported_hash},
                   'physical_devices_tested': False})
    return result


def main(argv=None):
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-version', required=True)
    for option, filename in (('image', 'filesystem.squashfs'), ('initrd', 'initrd'), ('vmlinuz', 'vmlinuz')):
        parser.add_argument('--' + option, type=Path, default=project / 'dist/usb/flashos' / filename)
    parser.add_argument('--output', type=Path, default=project / 'dist/hardware-build-verification.json')
    args = parser.parse_args(argv)
    try:
        result = verify_build(args.image, args.initrd, args.vmlinuz, args.kernel_version)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        result = {'verified': False, 'kernel_version': args.kernel_version, 'error': str(error)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not result['verified']:
        parser.exit(1, f'Hardware verification failed: {result["error"]}\n')
    print(f'Verified shipped {args.kernel_version} drivers, notebook PCI aliases, firmware and boot artifacts.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
