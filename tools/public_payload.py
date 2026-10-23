"""Shared, explicit boundaries for public documentation and system payloads."""
import hashlib
import io
import json
from pathlib import PurePosixPath
import zipfile

BOOT_FILES = {
    'flashos/vmlinuz', 'flashos/initrd', 'flashos/flashos.marker',
    'flashos/efi/BOOTX64.EFI', 'flashos/efi/grubx64.efi',
    'flashos/efi/mmx64.efi', 'flashos/efi/grub.cfg',
    'EFI/debian/grub.cfg', 'ventoy/flashos.env',
}
USB_FILES = {
    'flashos/vmlinuz', 'flashos/initrd', 'flashos/filesystem.squashfs',
    'flashos/flashos.marker', 'ventoy/ventoy.json', 'ventoy/ventoy_grub.cfg',
}
ROOT_DOCS = ('README.md', 'README.en.md', 'LICENSE', 'NOTICE.md',
             'THIRD-PARTY.md', 'CHANGELOG.md', 'CONTRIBUTING.md')
BOOT_STAGED_CONFIGS = {
    'debian-grub.cfg': ('dist/boot/flashos/efi/grub.cfg', 'dist/boot/EFI/debian/grub.cfg'),
    'ventoy_grub.cfg': ('dist/usb/ventoy/ventoy_grub.cfg',),
    'ventoy.json': ('dist/usb/ventoy/ventoy.json',),
}
BOOT_ROOT_CONFIGS = {
    'flashos.service': 'etc/systemd/system/flashos.service',
    'flashos-apps.service': 'etc/systemd/system/flashos-apps.service',
    'pipewire-flashos.conf': 'etc/pipewire/pipewire.conf.d/90-flashos.conf',
    'wireplumber-flashos.conf': 'etc/wireplumber/wireplumber.conf.d/90-flashos.conf',
}
BOOT_ARTIFACTS = sorted({'dist/boot/' + name for name in BOOT_FILES} |
                        {path for paths in BOOT_STAGED_CONFIGS.values() for path in paths} |
                        {'dist/ventoy.disk.img.xz', 'dist/ventoy-efi.img'})


def safe_path(root, path):
    """Reject links, junctions and paths outside the declared tree, even if absent."""
    root = root.absolute()
    path = path.absolute()
    if not path.is_relative_to(root):
        raise ValueError('Path outside public tree: ' + str(path))
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Links are not allowed in public trees: ' + str(part))
        if part == root:
            break
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path escapes public tree: ' + str(path))
    return path


def regular_tree(root, base):
    safe_path(root, base)
    result = []
    for path in sorted(base.rglob('*')):
        safe_path(root, path)
        if path.is_file():
            result.append(path)
        elif not path.is_dir():
            raise ValueError('Non-regular public entry: ' + str(path))
    return result


def public_inventory(root):
    names = json.loads((root / 'tools/public-files.json').read_text(encoding='utf-8'))
    if len(names) != len(set(names)):
        raise ValueError('Duplicate public inventory entry')
    for name in names:
        if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
            raise ValueError('Invalid public inventory entry')
    return set(names)


def documentation(root):
    names = public_inventory(root)
    selected = {name: name for name in names if name.startswith(
        ('docs/ru/', 'docs/en/', 'docs/images/', 'licenses/', 'metadata/'))}
    selected.update({name: name for name in ROOT_DOCS})
    selected['README-FlashOS.md'] = 'README.md'
    for name in set(selected.values()):
        path = safe_path(root, root / name)
        if not path.is_file():
            raise ValueError('Missing public document: ' + name)
    return selected


def payload_files(root, section):
    base = root / 'dist' / section
    docs = documentation(root) if section == 'usb' else {}
    allowed = (USB_FILES | set(docs)) if section == 'usb' else BOOT_FILES
    found = {}
    for path in regular_tree(root, base):
        name = path.relative_to(base).as_posix()
        if section == 'usb' and name.startswith('iso/'):
            continue
        if name not in allowed:
            raise ValueError('Unexpected public payload file: ' + section + '/' + name)
        if name in docs and path.read_bytes() != (root / docs[name]).read_bytes():
            raise ValueError('Stale public document: ' + name)
        found[name] = path
    if set(found) != allowed:
        raise ValueError('Missing payload files: ' + ', '.join(sorted(allowed - set(found))))
    return sorted(found.items())


def python_source_hashes(content):
    """Match only LF/CRLF transport variants, keeping every other source byte."""
    hashes = {hashlib.sha256(content).hexdigest()}
    normalized = content.replace(b'\r\n', b'\n')
    # A bare CR can combine with the following LF after replacement and alter
    # Python's interpreted lines; keep such files strictly byte-for-byte.
    if b'\r' not in normalized:
        hashes.update(hashlib.sha256(variant).hexdigest() for variant in (
            normalized, normalized.replace(b'\n', b'\r\n')))
    return hashes


def boot_source_hashes(root):
    return {path.relative_to(root / 'boot').as_posix(): python_source_hashes(path.read_bytes())
            for path in regular_tree(root, root / 'boot')}


def certified_hashes_match(certified, current):
    return (isinstance(certified, dict) and set(certified) == set(current)
            and all(isinstance(certified[name], str) and certified[name] in hashes
                    for name, hashes in current.items()))


def verify_boot_configs(root):
    """Both EFI paths and the USB menu must contain the reviewed configuration."""
    for name, targets in BOOT_STAGED_CONFIGS.items():
        source = safe_path(root, root / 'boot' / name).read_bytes()
        for target in targets:
            content = safe_path(root, root / target).read_bytes()
            if hashlib.sha256(content).hexdigest() not in python_source_hashes(source):
                raise ValueError('Stale boot configuration: ' + target)


def boot_artifact_hashes(root):
    text_files = {path for paths in BOOT_STAGED_CONFIGS.values() for path in paths}
    result = {}
    for name in BOOT_ARTIFACTS:
        path = safe_path(root, root / name)
        if name in text_files:
            result[name] = python_source_hashes(path.read_bytes())
        else:
            with path.open('rb') as stream:
                result[name] = {hashlib.file_digest(stream, 'sha256').hexdigest()}
    return result


def verified_system(root, version):
    """Require evidence for this exact live image and the current application sources."""
    report = json.loads(safe_path(root, root / 'dist/toolkit-build-verification.json').read_text(encoding='utf-8'))
    sources = {path.relative_to(root / 'src').as_posix(): python_source_hashes(path.read_bytes())
               for path in regular_tree(root, root / 'src') if path.suffix == '.py'}
    certified_sources = report.get('sources_sha256')
    sources_match = certified_hashes_match(certified_sources, sources)
    image = safe_path(root, root / 'dist/usb/flashos/filesystem.squashfs')
    with image.open('rb') as stream:
        image_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    if (not sources or report.get('sources_match') is not True or report.get('no_test_fixture') is not True
            or not sources_match or report.get('squashfs_sha256') != image_hash
            or report.get('system_version') != version):
        raise ValueError('Live system is stale or unverified. Repack it and run tools/verify-toolkit-build.py before packaging.')
    if (report.get('boot_sources_match') is not True or
            not certified_hashes_match(report.get('boot_sources_sha256'), boot_source_hashes(root))):
        raise ValueError('Boot sources are stale or unverified. Repack and certify the build before packaging.')
    verify_boot_configs(root)
    if not certified_hashes_match(report.get('boot_artifact_sha256'), boot_artifact_hashes(root)):
        raise ValueError('Boot artifacts changed after verification. Repack and certify the build before packaging.')
    store = report.get('app_store', {})
    if store.get('verified') is not True or store.get('filesystem_sha256') != image_hash:
        raise ValueError('Application runtime is absent or stale. Run tools/verify-app-store-build.py and certify the build.')
    terminal = report.get('terminal', {})
    if terminal.get('verified') is not True or terminal.get('filesystem_sha256') != image_hash:
        raise ValueError('Terminal runtime is absent or stale. Run tools/verify-terminal-build.py and certify the build.')
    hardware = report.get('hardware', {})
    if hardware.get('verified') is not True:
        raise ValueError('Hardware drivers are unverified. Run tools/verify-hardware-build.py and certify the build again.')
    for name in ('filesystem.squashfs', 'vmlinuz', 'initrd'):
        path = safe_path(root, root / 'dist/usb/flashos' / name)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if hardware.get('artifact_sha256', {}).get(name) != digest:
            raise ValueError('Boot files changed after hardware verification: ' + name)
        if name != 'filesystem.squashfs' and (safe_path(root, root / 'dist/boot/flashos' / name).read_bytes()
                                            != path.read_bytes()):
            raise ValueError('Boot partition does not match verified hardware: ' + name)
    for section in ('usb', 'boot'):
        marker = safe_path(root, root / 'dist' / section / 'flashos/flashos.marker')
        if marker.read_text(encoding='utf-8').strip() != 'FlashOS ' + version:
            raise ValueError('Live system version does not match the installer: ' + section)


def pinned_zip(root, filename):
    pins = json.loads((root / 'metadata/build-inputs.json').read_text(encoding='utf-8'))
    matches = [item for item in pins if item['file'] == filename]
    if len(matches) != 1:
        raise ValueError('Missing unique build-input pin: ' + filename)
    data = safe_path(root, root / 'downloads' / filename).read_bytes()
    pin = matches[0]
    if len(data) != pin['bytes'] or hashlib.sha256(data).hexdigest() != pin['sha256']:
        raise ValueError('Build-input checksum mismatch: ' + filename)
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = set()
    for item in archive.infolist():
        name = item.filename.rstrip('/')
        parts = PurePosixPath(name).parts
        if not parts or name.startswith('/') or '\\' in name or '..' in parts or ':' in name:
            archive.close()
            raise ValueError('Unsafe build-input ZIP member')
        if name.casefold() in names:
            archive.close()
            raise ValueError('Duplicate build-input ZIP member')
        names.add(name.casefold())
    return archive
