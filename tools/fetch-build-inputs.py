#!/usr/bin/env python3
"""Fetch pinned public build inputs and verify their recorded SHA256."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    (ROOT / 'downloads').mkdir(exist_ok=True)
    for item in json.loads((ROOT / 'metadata/build-inputs.json').read_text(encoding='utf-8')):
        name = item['file']
        if Path(name).name != name:
            raise ValueError('Invalid input filename')
        path = ROOT / 'downloads' / name
        def valid():
            if not path.is_file():
                return False
            with path.open('rb') as stream:
                return hashlib.file_digest(stream, 'sha256').hexdigest() == item['sha256']
        if not valid():
            temporary = path.with_suffix(path.suffix + '.part')
            with urllib.request.urlopen(item['url'], timeout=120) as source, temporary.open('wb') as target:
                while chunk := source.read(4 * 1024 * 1024):
                    target.write(chunk)
            with temporary.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                    raise ValueError('SHA256 mismatch: ' + name)
            temporary.replace(path)
        print('Verified:', name)


if __name__ == '__main__':
    main()
