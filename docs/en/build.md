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

The fetcher verifies the pinned Ventoy, SOF and license inputs. Provisioning needs network access and root inside the builder. It installs the package list in `build/build-system.sh`, `build/install-hardware.sh`, `install-admin.sh` and `install-recovery.sh`; builds the pinned RHVoice revision with selected submodules; installs the SOF release; copies `src/` and `boot/`; removes build-host machine IDs, resolver state and logs; creates initrd/squashfs; then patches only Ventoy's GRUB configuration. Signed EFI binaries are retained.

Hardware provisioning is centralized in `build/install-hardware.sh`, called by full image builds. `build/repack-system.sh` verifies the already provisioned hardware set; apply Debian package changes through the full build before repacking. Kernel, non-free firmware and Mesa use an explicit trixie-backports install; Intel/AMD microcode, ALSA topology and additional firmware use the stable suite. `build/install-sof.py` overlays the official SOF 2026.09.2 archive after checking its pinned SHA256, preserving notices and provenance in `/usr/share/doc/flashos`. Debian's `firmware-sof-signed` remains the base package and reports its own 2025.01-1 version.

The portable live image explicitly uses `MODULES=most`. Initrd contains early microcode for both CPU vendors through `IUCODE_TOOL_INITRAMFS=early`, `IUCODE_TOOL_SCANCPUS=no` and `AMD64UCODE_INITRAMFS=early`, independent of the builder's CPU. udev loads hardware modules. No additional DKMS modules or forced driver blacklists are installed.

Repacking runs `depmod` and regenerates initrd for the installed FlashOS kernel, independently of the running Docker/WSL kernel. Hardware verification compares the exported kernel and initrd with squashfs, checks libkmod binary indexes and complete required driver load plans with `modprobe --show-depends`, without loading modules on the builder. AX211 and I219-V PCI aliases are checked separately.

The output is `dist/usb`, `dist/boot`, `dist/ventoy.disk.img.xz` and `dist/packages.tsv`. Never place personal ISOs, backups, settings or reports in this packaging tree. `tools/package-public-docs.py` copies only public documentation. Test ISOs belong in separate VM fixtures.

To build the EXE with a prepared system, use `FlashOS-2026.10.22.2-system.7z` from its matching [release](https://github.com/andreykadelite/flashos-voiceboot/releases). Open it in 7-Zip and extract at the root of a source checkout of the same tag to populate `dist`. Project sources are also available in `FlashOS-2026.10.22.2-source.zip`. The system archive contains the system certificate, metadata and `SYSTEM-SHA256SUMS.txt` for checking its contents. Component and source information is also included in the project source archive. The release page provides the ready-to-use EXE and archives. Use the EXE to prepare USB media: the archive contains build files and cannot be written with Rufus or dd.

After changing FlashOS code, run `build/repack-system.sh` in the prepared Linux environment. `tools/verify-toolkit-build.py` compares the sources with files inside squashfs and records the image SHA256. Packaging stops if the system version, image or sources differ from that report. The prepared-system archive includes the report.

The full Linux build uses current trixie/backports indexes. To reconstruct older Debian package binaries, use the exact `.dsc`, original tarballs and Debian patches in the release's Debian source archive with `dpkg-source -x` and `dpkg-buildpackage` in the matching Debian environment. See `metadata/debian-sources.json` for source files and SHA256, and `metadata/source-archives.json` for archive membership. Release 2026.10.12.1 retains the 2026.09.09.3 source archives and adds new or changed Debian sources plus the original SOF 2026.09.2 asset in `FlashOS-2026.10.12.1-hardware-sources.zip`. Debian downloads are checked against signed deb-src indexes; SOF is checked against its pinned `metadata/build-inputs.json` record. Third-party source archives include upstream build instructions. Rebuilding signed upstream EFI binaries will not reproduce the upstream signing keys; use originals or your own trust setup.

## Repacking and terminal dependencies

Before repacking a prepared live root for 2026.10.22.2, install the new
terminal dependencies:

```sh
sudo unshare --mount --propagation private bash -eu -c '
root=/opt/flashos-rootfs
mountpoint -q "$root/proc" || mount -t proc proc "$root/proc"
mountpoint -q "$root/sys" || mount --rbind /sys "$root/sys"
mountpoint -q "$root/dev" || mount --rbind /dev "$root/dev"
mount --make-rslave "$root/sys"
mount --make-rslave "$root/dev"
if [ "$(readlink "$root/etc/resolv.conf" || true)" = /run/NetworkManager/resolv.conf ]; then
    mkdir -p "$root/run/NetworkManager"
    touch "$root/run/NetworkManager/resolv.conf"
    mount --bind /etc/resolv.conf "$root/run/NetworkManager/resolv.conf"
    mount -o remount,bind,ro "$root/run/NetworkManager/resolv.conf"
fi
bash build/install-terminal.sh
bash build/repack-system.sh
'
```

Virtual filesystems need mounting again after restarting the builder. This
example uses a private mount namespace; its mounts are released when the
process exits. For subsequent Python-only changes, repeat the example without
the `install-terminal.sh` stage. `repack-system.sh` checks the mounts before
changing the image and stops with an actionable error for an unprepared chroot.
The read-only DNS bind is needed for package installation in a sanitized live
root and stays outside the image (`run/*` is excluded).

Full builds invoke this stage automatically. It adds GTK 3, VTE, PyGObject
and the ATK bridge; Bash must already be installed. After package changes,
refresh `metadata/debian-packages.json` and `metadata/packages.tsv` and check
corresponding-source coverage for the exact versions. `verify-terminal-build.py`
checks actual packages, libraries and typelibs inside squashfs; the main image
certificate includes its result.

## Application runtime and corresponding source

`build/install-app-store.sh` creates a separate amd64 Debian root at `/opt/flashos-app-runtime` and installs Python/APT, Openbox, Orca, GNOME Terminal, eSpeak NG and the audio session. `build/install-rhvoice.py` must finish first: the application runtime uses the verified RHVoice from the live root. Full builds call these stages in that order. A user's actual `store.ext4` is excluded from the build.

`tools/provision-shared-speech.py` copies the same RHVoice 1.18.4 binaries, libraries, `sd_rhvoice` module, five unmodified voice datasets and license notices from the live root. It compares copies by SHA256, checks both synthesizers' dependencies through `ldd`, and verifies voice enumeration through the Speech Dispatcher protocol. Its manifest is `var/lib/flashos-store/speech-runtime.json` inside the runtime. No separate Debian RHVoice package or different model versions are installed for applications.

`tools/repack-app-runtime.py` writes the sanitized seed to `/opt/flashos-rootfs/opt/flashos/app-runtime.tar.xz`; `app-runtime.json` records size, SHA256 and UID 1000. The packer checks package versions and corresponding-source coverage, excluding machine identifiers, builder DNS state, logs and temporary directories. In build 2026.10.22.1 the runtime contains 455 Debian packages: only `espeak-ng` version `1.52.0+dfsg-5` was added to the previous seed, without upgrading or removing existing packages. Its source is already covered by the existing source archives. Copying RHVoice from the live root does not add dpkg packages.

After preparing the packages, refresh shared speech and the seed without another APT operation:

```sh
sudo python3 tools/provision-shared-speech.py --live /opt/flashos-rootfs \
  --runtime /opt/flashos-app-runtime
sudo python3 tools/repack-app-runtime.py --runtime /opt/flashos-app-runtime \
  --live /opt/flashos-rootfs
sudo bash build/repack-system.sh
```

Changes to other package versions require updating and checking the matching lists and source archives first. Seed repacking does not replace user stores: on opening them, `store_storage.py` supplies shared voices read-only and libraries through RAM, preserving applications, Orca profiles and user libraries on USB.

Application-runtime package lists are `metadata/app-runtime-packages.tsv` and `metadata/app-runtime-packages.json`. Collect corresponding source for both prepared roots using exact source-pair union:

```sh
sudo python3 tools/collect-debian-sources.py --root /opt/flashos-rootfs \
  --additional-root /opt/flashos-app-runtime --existing-sources metadata/debian-sources.json \
  --output build/publication/applications-sources
```

Enable matching signed deb-src indexes for trixie, updates, security and backports in the builder. `--reuse-directory` reuses downloaded files only after SHA256 verification. `FlashOS-2026.10.12.2-applications-sources.zip` supplements existing source archives; retain the complete matching set. Package licenses remain under `/usr/share/doc/*/copyright` inside each environment.

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

Docker boot tests require loop/mount permissions and QEMU in the builder. In a minimal container without a running udev daemon, `make-test-disk.py` creates missing partition nodes only after checking the loop backing file and sysfs device numbers. Do not pass physical disks to the container. mac80211_hwsim testing does not establish compatibility with a physical PCI/USB Wi-Fi adapter.

## Extract the release build archive

Open `FlashOS-2026.10.22.2-system.7z` in 7-Zip and extract at the root of
the matching source checkout. This is a single archive; no separate parts or
Python join script are required. See the
[release overview](release-2026.10.22.md#installation-and-release-files).

## Publish a derivative

Keep the author/license notice and identify your changes. Review every bundled voice and firmware license, archive matching corresponding source, remove personal state from the live root and packaging tree, run the checks, then attach the EXE/system/source archives and hashes to a release. `tools/export-system.py` creates the system ZIP. Never commit the large generated files or private USB backups to Git.
