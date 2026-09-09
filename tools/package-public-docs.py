#!/usr/bin/env python3
"""Put only the public documentation and notices in the USB payload."""
from pathlib import Path
import shutil
from public_payload import documentation, regular_tree, safe_path

ROOT = Path(__file__).resolve().parents[1]


def package(root):
    destination = safe_path(root, root / 'dist/usb')
    mapping = documentation(root)
    # Inspect source and destination trees before the first write or removal.
    for name in ('docs/ru', 'docs/en', 'docs/images', 'licenses', 'metadata'):
        regular_tree(root, root / name)
    for name in mapping:
        safe_path(root, destination / name)
    for name in ('docs', 'licenses', 'metadata'):
        regular_tree(root, destination / name)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ('docs', 'licenses', 'metadata'):
        target = safe_path(root, destination / name)
        if target.exists():
            shutil.rmtree(target)
    for name, source in sorted(mapping.items()):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / source, target)


if __name__ == '__main__':
    package(ROOT)
