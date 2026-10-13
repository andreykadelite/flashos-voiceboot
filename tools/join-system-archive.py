"""Reassemble a release system ZIP, checking each part and the complete archive."""
import argparse
import hashlib
import json
from pathlib import Path
import re

CHUNK_BYTES = 4 * 1024**2
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def filename(value):
    if (not isinstance(value, str) or not value or value in ('.', '..')
            or '/' in value or '\\' in value or ':' in value):
        raise ValueError('Manifest filenames must be plain basenames.')
    return value


def record(value):
    if (not isinstance(value, dict) or type(value.get('bytes')) is not int
            or value['bytes'] <= 0 or not isinstance(value.get('sha256'), str)
            or not DIGEST.fullmatch(value['sha256'])):
        raise ValueError('Invalid size or SHA256 in the archive manifest.')
    filename(value.get('file'))
    return value


def join(manifest_path, output=None):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if not isinstance(manifest, dict) or manifest.get('format') != 1:
        raise ValueError('Unsupported archive manifest.')
    archive = record(manifest.get('archive'))
    parts = manifest.get('parts')
    if not isinstance(parts, list) or not parts:
        raise ValueError('No archive parts in the manifest.')
    parts = [record(part) for part in parts]
    expected_names = [archive['file'] + f'.{index:03d}'
                      for index in range(1, len(parts) + 1)]
    if [part['file'] for part in parts] != expected_names:
        raise ValueError('Parts are missing, duplicated or out of order.')
    if sum(part['bytes'] for part in parts) != archive['bytes']:
        raise ValueError('The total part size differs from the archive size.')
    output = Path(output) if output is not None else manifest_path.parent / archive['file']
    # Exclusive creation also protects an input part supplied as the output path.
    with output.open('xb') as destination:
        complete = False
        try:
            archive_hash = hashlib.sha256()
            for part in parts:
                part_hash = hashlib.sha256()
                size = 0
                with (manifest_path.parent / part['file']).open('rb') as source:
                    while block := source.read(CHUNK_BYTES):
                        size += len(block)
                        if size > part['bytes']:
                            raise ValueError('Part is larger than expected: ' + part['file'])
                        part_hash.update(block)
                        archive_hash.update(block)
                        destination.write(block)
                if size != part['bytes'] or part_hash.hexdigest() != part['sha256']:
                    raise ValueError('Size or SHA256 mismatch: ' + part['file'])
            if archive_hash.hexdigest() != archive['sha256']:
                raise ValueError('The complete archive SHA256 does not match.')
            complete = True
        finally:
            if not complete:
                destination.close()
                output.unlink()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print('Verified archive:', join(args.manifest, args.output))


if __name__ == '__main__':
    main()
