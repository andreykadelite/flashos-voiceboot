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
import shutil
import subprocess
import tempfile
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/flashos-rootfs'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--additional-root', type=Path, action='append', default=[],
                        help='Include another prepared Debian root in source coverage')
    parser.add_argument('--existing-sources', type=Path,
                        help='Skip exact source versions already archived in this JSON index')
    parser.add_argument('--reuse-directory', type=Path, action='append', default=[],
                        help='Reuse matching SHA256-verified source files before downloading')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    def package_snapshot(build_root):
        raw = subprocess.check_output(['dpkg-query', '--admindir=' + str(build_root / 'var/lib/dpkg'),
            '-W', '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n'], text=True)
        return [dict(zip(('binary', 'binary_version', 'source', 'source_version'), line.split('\t')))
                for line in raw.splitlines()]

    snapshots = [dict(root=str(build_root), packages=package_snapshot(build_root))
                 for build_root in [args.root, *args.additional_root]]
    packages = snapshots[0]['packages']
    if args.additional_root:
        (args.output / 'package-roots.json').write_text(json.dumps(snapshots, indent=2) + '\n')
    (args.output / 'packages.json').write_text(json.dumps(packages, indent=2) + '\n')
    sources = {(p['source'], p['source_version']) for snapshot in snapshots for p in snapshot['packages']}
    if args.existing_sources:
        archived = json.loads(args.existing_sources.read_text(encoding='utf-8-sig'))
        sources -= {(r['package'], r['version']) for r in archived}
    sources = sorted(sources)
    records, errors = [], []
    (args.output / 'sources.json').write_text('[]\n')
    (args.output / 'errors.json').write_text('[]\n')

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
            for directory in args.reuse_directory:
                if valid():
                    break
                cached = directory / filename
                if cached.is_file() and cached.stat().st_size == int(size):
                    with cached.open('rb') as stream:
                        if hashlib.file_digest(stream, 'sha256').hexdigest() == digest:
                            shutil.copyfile(cached, target)
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
