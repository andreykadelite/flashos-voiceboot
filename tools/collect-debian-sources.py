#!/usr/bin/env python3
"""Archive exact Debian source packages for a prepared FlashOS build root.

Run as root in the Debian builder after enabling matching deb-src repositories.
Does not install packages or write to physical disks. Downloads are checked
against the SHA256 recorded in apt's signed source indexes.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/flashos-rootfs'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    raw = subprocess.check_output(['dpkg-query', '--admindir=' + str(args.root / 'var/lib/dpkg'),
        '-W', '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n'], text=True)
    packages = [dict(zip(('binary', 'binary_version', 'source', 'source_version'), line.split('\t')))
                for line in raw.splitlines()]
    (args.output / 'packages.json').write_text(json.dumps(packages, indent=2) + '\n')
    sources = sorted({(p['source'], p['source_version']) for p in packages})
    records, errors = [], []

    def fetch(pair):
        name, version = pair
        # Ask from an empty directory: apt otherwise omits URIs for cached files.
        with tempfile.TemporaryDirectory(prefix='flashos-source-uris-') as query_dir:
            proc = subprocess.run(['apt-get', '-qq', '--print-uris', '--download-only', 'source',
                                   name + '=' + version], text=True, capture_output=True, cwd=query_dir)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip())
        files = []
        for line in proc.stdout.splitlines():
            match = re.match(r"'([^']+)'\s+(\S+)\s+(\d+)\s+SHA256:([0-9a-f]{64})", line)
            if not match:
                continue
            url, filename, size, digest = match.groups()
            if Path(filename).name != filename:
                raise ValueError('Unsafe source filename')
            target = args.output / filename
            def valid():
                if not target.is_file() or target.stat().st_size != int(size):
                    return False
                with target.open('rb') as stream:
                    return hashlib.file_digest(stream, 'sha256').hexdigest() == digest
            if not valid():
                temporary = target.with_name(target.name + '.part')
                for attempt in range(3):
                    try:
                        with urllib.request.urlopen(url, timeout=90) as incoming, temporary.open('wb') as out:
                            while chunk := incoming.read(4 * 1024 * 1024):
                                out.write(chunk)
                        temporary.replace(target)
                        if not valid():
                            raise RuntimeError('SHA256 mismatch: ' + filename)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
            files.append(dict(url=url, file=filename, bytes=int(size), sha256=digest))
        if not files:
            raise RuntimeError('No SHA256 source records: ' + proc.stdout[:300])
        return dict(package=name, version=version, files=files)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        pending = {pool.submit(fetch, pair): pair for pair in sources}
        for future in concurrent.futures.as_completed(pending):
            pair = pending[future]
            try:
                record = future.result()
                records.append(record)
                print('OK', *pair, flush=True)
            except Exception as error:
                errors.append(dict(package=pair[0], version=pair[1], error=str(error)))
                print('ERROR', *pair, str(error)[:200], flush=True)
            (args.output / 'sources.json').write_text(json.dumps(sorted(records, key=lambda r: r['package']), indent=2) + '\n')
            (args.output / 'errors.json').write_text(json.dumps(errors, indent=2) + '\n')
    print(json.dumps(dict(packages=len(packages), sources=len(records), errors=len(errors))))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
