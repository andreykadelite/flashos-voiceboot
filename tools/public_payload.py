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
