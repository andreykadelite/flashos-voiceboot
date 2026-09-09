#!/usr/bin/env python3
"""Remove build-host state from a disposable live root before packaging."""
import argparse
import os
from pathlib import Path
import re


def mount_points():
    # mountinfo escapes whitespace and backslashes as octal sequences.
    data = Path('/proc/self/mountinfo').read_text()
    return [Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]))
            for line in data.splitlines()]


def sanitize(root):
    root = root.resolve(strict=True)
    if root == Path('/') or not (root / 'opt/flashos/menu.py').is_file():
        raise ValueError('Expected a prepared FlashOS build root, never the host root')
    areas = ('etc', 'var/lib/dbus', 'var/log', 'var/lib/systemd', 'var/lib/NetworkManager')
    for mounted in mount_points():
        if mounted != root and mounted.is_relative_to(root):
            if any(mounted.is_relative_to(root / area) or (root / area).is_relative_to(mounted)
                   for area in areas):
                raise ValueError('Unmount build-state paths before sanitizing: ' + str(mounted))
    # Resolve parents, but not final symlinks: /etc/resolv.conf may point to /run.
    def target(relative):
        path = root / relative
        if not path.parent.resolve().is_relative_to(root):
            raise ValueError('Build root contains an escaping parent symlink: ' + relative)
        return path
    identities = [target(relative) for relative in
                  ('etc/machine-id', 'var/lib/dbus/machine-id', 'etc/resolv.conf')]
    removals = []
    # Inspect all paths first. os.walk never descends into directory symlinks.
    for directory in ('var/log', 'var/lib/systemd', 'var/lib/NetworkManager'):
        base = target(directory)
        if base.is_symlink():
            removals.append(base)
            continue
        if not base.is_dir():
            continue
        for parent, directories, files in os.walk(base, followlinks=False):
            for name in files + [name for name in directories if (Path(parent) / name).is_symlink()]:
                path = target((Path(parent) / name).relative_to(root).as_posix())
                if directory != 'var/lib/systemd' or path.name in ('random-seed', 'secret_key', 'seen-bssids', 'timestamps'):
                    removals.append(path)
    for path in identities:
        path.unlink(missing_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
    target('etc/machine-id').write_text('')
    target('var/lib/dbus/machine-id').symlink_to('/etc/machine-id')
    target('etc/resolv.conf').symlink_to('/run/NetworkManager/resolv.conf')
    for path in removals:
        path.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    sanitize(parser.parse_args().root)
