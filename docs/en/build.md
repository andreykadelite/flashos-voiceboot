# Building FlashOS and the installer

[Русский](../ru/build.md) · [Home](../../README.en.md)

Build the Linux system first, then package it in the Windows EXE. These are source build instructions, not a claim of byte-for-byte reproducibility. Debian repository contents, compiler metadata and timestamps can change output. Exact published package/source versions and hashes are in `metadata/` and the release archives.

## Prepare a checkout

Clone this repository and check out the release tag you need. Use a dedicated Debian 13 x86-64 VM or WSL distribution for Linux builds; the scripts provision `/opt/flashos-rootfs`. Do not point them at your running system. Allow at least 20 GB of free space for packages, source archives and intermediate files.

From the repository root, run:

```sh
python3 tools/fetch-build-inputs.py
sudo bash build/setup-builder.sh
sudo bash build/build-system.sh
```

The fetcher verifies the pinned Ventoy, SOF and license inputs. Provisioning needs network access and root inside the builder. It installs the package list in `build/build-system.sh`, `install-admin.sh` and `install-recovery.sh`; builds the pinned RHVoice revision with selected submodules; installs the SOF release; copies `src/` and `boot/`; removes build-host machine IDs, resolver state and logs; creates initrd/squashfs; then patches only Ventoy's GRUB configuration. Signed EFI binaries are retained.

The output is `dist/usb`, `dist/boot`, `dist/ventoy.disk.img.xz` and `dist/packages.tsv`. Never place personal ISOs, backups, settings or reports in this packaging tree. `tools/package-public-docs.py` copies only public documentation. Test ISOs belong in separate VM fixtures.

To build the EXE with the prepared system, download `FlashOS-2026.09.12.1-system.zip` from [this release](https://github.com/andreykadelite/flashos-voiceboot/releases/tag/v2026.09.12.1-installer-1.0.4), verify SHA256 and extract it into a source checkout of the same tag. It supplies the `dist` tree. Use the EXE to prepare USB media: the ZIP contains build files and cannot be written with Rufus or dd.

After changing FlashOS code, run `build/repack-system.sh` in the prepared Linux environment. `tools/verify-toolkit-build.py` compares the sources with files inside squashfs and records the image SHA256. Packaging stops if the system version, image or sources differ from that report. The prepared-system archive includes the report.

The full Linux build uses current trixie/backports indexes. To reconstruct older Debian package binaries, use the exact `.dsc`, original tarballs and Debian patches in the release's Debian source archive with `dpkg-source -x` and `dpkg-buildpackage` in the matching Debian environment. See `metadata/debian-sources.json`; it records every source file and SHA256. Third-party source archives include upstream build instructions. Rebuilding signed upstream EFI binaries will not reproduce the upstream signing keys; use originals or your own trust setup.

## Windows EXE

Use Windows x64 and Python **3.12.10**. Install Python into `build/installer/python312` with its standard library, development files, pip and `LICENSE.txt`. This is an ordinary CPython install, not the minimal embeddable ZIP. The script intentionally uses this dedicated interpreter.

```powershell
& .\build\installer\python312\python.exe -m pip install -r installer/requirements-build.txt
& .\build\installer\python312\python.exe tools/fetch-build-inputs.py
.\tools\Build-Installer.ps1 -Mode onefile
```

Pinned dependencies are PySide6/Qt **6.11.2**, Nuitka **4.2.1**, zstandard **0.25.0** and ordered-set **4.1.0**. Nuitka downloads its supported MinGW compiler on the first build; that step needs internet. The released EXE was built using MinGW 15.2. The build creates a deduplicated payload, pins its manifest hash in `installer/_release.py`, runs the installer unit tests and compiles the application. The output is `release/FlashOS-Setup.exe` with a SHA256 file.

`tools/build-installer-payload.py` needs the prepared `dist` tree, Windows Ventoy archive, public docs/licenses and Python license. It excludes ISO files. `installer/engine.ps1` is the canonical writer source; `_release.py` is generated and must be regenerated after changing it. Python is embedded in the EXE, so end users do not need a Python installation.

## Replace Qt/PySide6

Qt DLLs are dynamically linked. You may modify the LGPL libraries and debug those modifications; the project does not prohibit reverse engineering for that purpose. Sources for the actual Qt base and PySide/Shiboken releases are included with this release.

Build ABI-compatible Qt/PySide6 libraries using their included build instructions, install the resulting wheel/libraries into the dedicated Python environment, then run `Build-Installer.ps1 -Mode standalone`. In `build/installer/nuitka/entry.dist`, replace Qt/PySide/Shiboken libraries and platform/style plugins with a mutually compatible set. Run that directory's EXE directly. To obtain one file, run `-Mode onefile` using the same modified environment. Payload hashing covers the system data; it does not forbid replacing the Qt libraries or rebuilding the application. No FlashOS signing key is required to run a modified installer.

## Checks without physical writes

```sh
python3 -m unittest discover -s tests -v
python3 tools/check-publication.py
```

On Windows, run `python -m unittest discover -s tests -p 'test_installer*.py' -v`. With build dependencies installed and payload prepared, `python tools/check-installer-qt.py` checks keyboard/layout behaviour using fake USB devices. It writes screenshots/reports into `dist`, not to USB.

For boot tests, extract the verified Linux Ventoy archive under `tools/ventoy-1.1.17`, then run in the Linux builder:

```sh
sudo python3 tools/make-test-disk.py --size-gib 4
python3 tools/vm.py bios
python3 tools/vm.py status
python3 tools/vm.py quit
```

The disposable image is `dist/flashos-test.img`; QEMU receives that file, not host disks. Test one VM at a time. `uefi` and `secure` select the OVMF modes configured in `tools/vm.py`; Secure Boot key state is a separate setup step, not an accessibility guarantee. Guest recovery tests also require their documented fixture images. Do not turn physical-disk acceptance helpers into CI jobs.

## Publish a derivative

Keep the author/license notice and identify your changes. Review every bundled voice and firmware license, archive matching corresponding source, remove personal state from the live root and packaging tree, run the checks, then attach the EXE/system/source archives and hashes to a release. `tools/export-system.py` creates the system ZIP. Never commit the large generated files or private USB backups to Git.
