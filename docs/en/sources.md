# Component sources

[Русский](../ru/sources.md) · [Building FlashOS](build.md) · [Licenses](../../THIRD-PARTY.md)

The release has only one uploaded asset, `FlashOS-Setup.exe`. FlashOS code and documentation are in the repository at the corresponding tag. Obtain third-party sources from the pinned URLs and revisions below.

| Material | URLs, versions and SHA256 |
| --- | --- |
| Debian source packages, including Linux, GTK/VTE, Bash, xinput and smartmontools | [debian-sources.json](../../metadata/debian-sources.json); installed-package mapping: [debian-packages.json](../../metadata/debian-packages.json), [app-runtime-packages.json](../../metadata/app-runtime-packages.json) |
| Ventoy 1.1.17, Qt base / PySide 6.11.2, Python 3.12.10, earlier SOF 2025.12.2 | [upstream-sources.json](../../metadata/upstream-sources.json) |
| Nuitka 4.2.1, OpenSSL 3.0.16 and libffi 3.4.4 | [additional-sources.json](../../metadata/additional-sources.json) |
| Dependencies and build configurations for embedded Ventoy components | [ventoy-extra-sources.json](../../metadata/ventoy-extra-sources.json) |
| RHVoice 1.18.4 with the selected languages, voices and libraries | [rhvoice-sources.json](../../metadata/rhvoice-sources.json), command below |
| Current SOF 2026.09.2, original firmware package with notices | [build-inputs.json](../../metadata/build-inputs.json) |

## Downloading manifest files

Run these commands at the source tag's repository root in Bash with `jq`, `curl` and `sha256sum`. They download several gigabytes and verify every file. Old Debian versions may leave the ordinary mirror: if that happens, locate the same version on [snapshot.debian.org](https://snapshot.debian.org/) and check its manifest SHA256 before using it.

```bash
set -eu
mkdir -p sources/files
jq -r '.[] | (.files // [.])[] | [.url, .file, .sha256] | @tsv' \
  metadata/debian-sources.json metadata/upstream-sources.json \
  metadata/additional-sources.json metadata/ventoy-extra-sources.json \
  > sources/downloads.tsv
jq -r '.[] | select(.file == "sof-bin-2026.09.2.tar.gz") | [.url, .file, .sha256] | @tsv' \
  metadata/build-inputs.json >> sources/downloads.tsv
while IFS=$'\t' read -r url file sha; do
  [ "$file" != "musl-1.2.1.tar.gz" ] || url="https://www.musl-libc.org/releases/$file"
  curl --fail --location --retry 3 "$url" -o "sources/files/$file"
  printf '%s  %s\n' "$sha" "sources/files/$file" | sha256sum --check -
done < sources/downloads.tsv
```

The musl command uses its official `www` hostname because the original hostname produced a TLS error during verification. The manifest retains the original URL; a downloaded file must pass the SHA256 check either way.

Extract a Debian package together with its original archive and patches, for example:

```bash
cd sources/files
dpkg-source -x xinput_1.6.4-1.dsc
cd xinput-1.6.4
dpkg-buildpackage -us -uc -b
```

Building requires that package's build dependencies and an appropriate Debian environment. `tools/collect-debian-sources.py` collects exact versions for a prepared rootfs using signed `deb-src` indexes. Downloading an existing tag's source files does not require rebuilding that rootfs.

## RHVoice and selected submodules

A standard GitHub archive does not contain submodule contents. This command fetches the pinned revision and all 50 selected submodules, then checks their commits against the manifest. It requires Git and Python 3.

```bash
python3 - <<'PY'
import json, subprocess
from pathlib import Path
m = json.loads(Path('metadata/rhvoice-sources.json').read_text())
dest = Path('sources/RHVoice')
subprocess.run(['git', 'clone', '--depth', '1', '--branch', m['tag'], m['repository'], str(dest)], check=True)
def git(*args):
    return subprocess.check_output(['git', '-C', str(dest), *args], text=True).strip()
assert git('rev-parse', 'HEAD') == m['commit'], 'RHVoice commit mismatch'
entries = [line.split()[:2] for line in m['submodules']]
subprocess.run(['git', '-C', str(dest), 'submodule', 'update', '--init', '--depth', '1', '--jobs', '4', '--', *[p for _, p in entries]], check=True)
for commit, path in entries:
    assert git('-C', path, 'rev-parse', 'HEAD') == commit, path
print('RHVoice and selected submodules match the manifest')
PY
```

The destination must not exist on the first run. These are the same submodules selected by [build/install-rhvoice.py](../../build/install-rhvoice.py); other voices are not needed.

## Exact smartmontools drive database

The smartmontools package is covered by the Debian manifest. Its updated `drivedb.h` comes from [official commit 5e6cb183947e13bbf5eefb8f450c5b0415edee40](https://github.com/smartmontools/smartmontools/commit/5e6cb183947e13bbf5eefb8f450c5b0415edee40). The standard updater adds the revision, date and commit to the `VERSION` string. Recreate the installed file with:

```bash
python3 - <<'PY'
import hashlib, urllib.request
from pathlib import Path
url = 'https://raw.githubusercontent.com/smartmontools/smartmontools/5e6cb183947e13bbf5eefb8f450c5b0415edee40/drivedb/drivedb.h'
with urllib.request.urlopen(url) as response:
    data = response.read()
data = data.replace(b'  { "VERSION: 7.5",', b'  { "VERSION: 7.5/6277 2026-09-01 16:20:03 +0000 5e6cb183947e",', 1)
assert hashlib.sha256(data).hexdigest() == '55036ae43423822aee906e9d72630a715998e57326c1d2eb22eea46d53630154'
Path('sources/drivedb.h').write_bytes(data)
PY
```

## Reading the archive inventory

[source-archives.json](../../metadata/source-archives.json) records the builder's local archives and their member hashes. It is a historical inventory, not a list of downloadable release assets.

The earlier upstream archive contained 44 files: 37 downloads from the three manifests above, the RHVoice tree, the drive database, its README, three JSON manifests, and a `usr/share/doc` snapshot named `Debian-documentation-and-licenses.tar.gz`. The README and JSON files describe provenance; the snapshot contains the installed Debian system's documentation and notices. Current notices remain in the repository's `licenses/` and the running FlashOS system's `/usr/share/doc`. To create a snapshot for your own build, run `tar -C /opt/flashos-rootfs -czf sources/Debian-documentation-and-licenses.tar.gz usr/share/doc`.

These commands recover source files and selected trees. They do not promise byte-identical regenerated TAR/ZIP containers or EXEs. Known provenance limits for older Ventoy binaries are recorded in [THIRD-PARTY.md](../../THIRD-PARTY.md).
