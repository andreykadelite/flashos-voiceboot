# Wi-Fi after the update — user confirmation, 14 October 2026

After updating the USB, the user booted FlashOS and confirmed that Wi-Fi works on the PC where the preceding image did not create a wireless interface. This confirmation applies to image `e6eef74bbf7cd6b813bb5e3c7b37145a5b2fc702203b89e987f7473f0e98c736` and that PC. It neither establishes the initial root cause nor extends to other adapter models.

The technical verification history is retained below. Statements about no physical USB write and unconfirmed physical Wi-Fi describe the state before this update and the user's reply. Current metadata records this confirmation separately from virtual test results.

The new release includes `SHA256SUMS.txt`, `FlashOS-Setup.verification.json` and other technical information inside the system archive. Historical references to these files beside the EXE describe the previous release asset layout.

---

# FlashOS Wi-Fi continuation audit — 14 October 2026

Versions remain system `2026.10.13.2`, installer `1.0.8`. New squashfs SHA256: `e6eef74bbf7cd6b813bb5e3c7b37145a5b2fc702203b89e987f7473f0e98c736`. The complete first audit metadata is retained under `historical_previous_build_before_20261014_wifi_fix`; its text and preceding tests remain below.

The user rebooted the preceding corrected image `80fbc0907a72bc455385cdde98a5c4c5a3e7ee8f8ce8e1b946a5f0d88b2b6902` and reported that the kernel had not registered a Wi-Fi interface. PCI Wi-Fi is visible, but the previous automatic report omitted driver binding and unfiltered probe/module errors. **The physical root cause remains unestablished; virtual checks do not prove repair of Wi-Fi on the user's PC.**

Both Wi-Fi scan entrypoints now persist reports on USB. Attachments accept a safe underscore in legal interface names. Diagnostics retain the actual kernel version, NetworkManager/udev state, PCI/USB, driver binding, modalias and bounded hardware logs at all priorities. The automatic boot report also retains a bounded full kernel log and `lspci -nnk`.

After repeated successful empty NetworkManager and known-empty sysfs inventories, recovery attempts normal loading of **only an unloaded, unbound** wireless driver matched by the exact PCI/USB alias. It validates the running-kernel module file, dependencies and blacklist. Loaded drivers and active interfaces are preserved; no NetworkManager restart, module unload, forced reset or rebind occurs. This handles missed autoload, not an unknown probe failure of a loaded driver.

Windows Fast Startup was observed enabled. This is a **possible factor**, not an established cause: [iwlwifi documentation](https://wireless.docs.kernel.org/en/latest/en/users/drivers/iwlwifi.html#about-dual-boot-with-windows-and-fast-boot-enabled) describes dual-boot initialization failures, and [Microsoft](https://learn.microsoft.com/en-us/windows/win32/power/system-power-states) explains retained kernel state during hybrid shutdown. Windows settings were not changed automatically.

- Docker/Debian with Xvfb: **652 tests, 27 skips, no errors**. Windows: **97 installer tests**.
- The new clean production image passed BIOS, UEFI and Secure Boot with previously enrolled disposable OVMF keys; the visible menu was checked separately. First enrollment was not tested.
- A separate QA layer checked no-adapter diagnostics, missed autoload through a synthetic PCI alias and real `mac80211_hwsim`, scanning beside an unmanaged AP, WPA2/DHCP and wrong-password rollback. This is virtual testing; QEMU does not emulate AX211. Guest source/boot files matched production, and the test layer was removed.
- Debian/firmware package versions remain unchanged. An inventory of 90 wireless modules and potentially missing declared firmware does not establish universal support. Legacy b43/b43legacy devices require separate appropriate firmware with redistribution permission; speculative firmware was not added.

At this metadata snapshot the physical USB update to the new image remains pending; its results will be recorded separately in private local evidence. User applications, ISOs, settings and reports are outside the system repair payload. Raw hardware reports and USB identifiers stay in `build/wifi-fix-20261014`, outside public documentation. New EXE evidence belongs in `release/FlashOS-Setup.verification.json`.

---

# FlashOS 2026.10.13.2 audit — 14 October 2026

System `2026.10.13.2` and installer `1.0.8` retain their version numbers. New squashfs SHA256: `80fbc0907a72bc455385cdde98a5c4c5a3e7ee8f8ce8e1b946a5f0d88b2b6902`. The checks below this audit belong to the preceding image; their complete metadata is retained under `historical_previous_build_before_20261014_audit`.

FlashOS uses Debian 13, Linux 7.2.6, live-boot, Ventoy/Debian GRUB and Python. Tk/Xorg or curses provides the menu; RHVoice/eSpeak NG provides speech. NetworkManager and wpa_supplicant manage networking with drivers and firmware in the live image. The application store uses a separate persistent Debian root on USB with the shared host kernel/network. Nuitka compiles the Python 3.12.10/PySide6 installer and attaches the certified system payload.

The connected USB's original squashfs matched the project artifact. The exact AX211 `8086:7e40` PCI alias, iwlwifi/iwlmvm dependencies, MA API89 firmware/PNVM, kernel configuration and pinned Linux sources were verified. Removal of these drivers was not established. The user reports failure after a full shutdown. **The physical failure remains undiagnosed without that PC's Linux boot log; virtual tests do not establish recovery of the physical AX211.**

Fixes cover unmanaged Wi-Fi falsely appearing absent, asynchronous discovery, phy80211 classification, quick connection rollback and tentative IPv6; long file browser replies, truncated UTF-8/UTF-16, short ISO request writes and GPT usable bounds. Repacking now refreshes binary module indexes/initrd; verification checks dependency plans and boot files against squashfs.

- Docker/Debian with Xvfb: 624 tests, 27 skips, no errors. Windows: 97 installer tests and six Qt verification groups.
- Clean production QEMU boots produced the menu readiness marker in BIOS, UEFI and Secure Boot with previously enrolled disposable OVMF keys. The visible menu after greeting was checked separately; first enrollment was not tested.
- A separate mac80211_hwsim QA layer verified scanning beside an unmanaged AP, real WPA2 association/DHCP, restoration of the previous UUID/address after an incorrect password and candidate-only cleanup. Guest sources matched production; job reports contained no passwords.
- Debian package versions and the application seed were retained. No test hostapd/dnsmasq/services are in production. No physical USB writes or host reboot occurred.

Exact compiled EXE evidence and SHA256 are in `release/FlashOS-Setup.verification.json`. Local logs, screenshots, source evidence and original artifact backups are in `build/audit-20261014`. Distinguish this rebuild by SHA256 because version numbers were retained. See [Wi-Fi troubleshooting](troubleshooting.md#wi-fi-hardware-or-networks-are-missing).

---

# FlashOS 2026.10.13.2 verification — 13 October 2026

The Linux image was repacked with skippable informational speech and Orca preferences. `filesystem.squashfs` SHA256: `6c20d8ad5794b47e34be483a324cda8f6a2c84a714c50c263589cd04a0ee95c9`. Installer 1.0.8 packages this same certified image, including the preceding network profiles and encrypted credential vault.

- Docker Linux: 582 tests, 30 skips, no failures. Actual Tk/Xvfb and curses checks covered F7, Enter and Space, retained navigation and absence of consent forwarding after a skipped explanation. A nested power request during package writes waits for actual job completion.
- Independent speech and Orca reviews have no open findings. The voice-selection regression uses the real `UI.choose` and keyboard navigation for all six choices. It reproduced the original error, then verified spoken Russian labels and correct identifiers after the fix.
- Certificates compared all 40 Python sources with squashfs, 75 live runtime commands, the hardware set and a separate 455-package application runtime. Both synthesizers, five RHVoice voices and all 276 speech assets were verified. The sole added application package is eSpeak NG; other versions remain unchanged and its source version is already covered by the archives.

QEMU inside Docker checked skipped store/settings explanations, ISO handoff without waiting for speech, upgrading existing application storage, migration of the former default Orca profile and preservation of custom preferences. All five RHVoice voices and Russian eSpeak completed real preview speech through Speech Dispatcher. UID 1000 application sessions checked Orca's actual module and voice, speech completion and process cleanup.

The visible-menu check exercised RHVoice and eSpeak selection, confirmation, saving, a rate change, preview, an application session and restoration of FlashOS-follow mode. Checks covered graceful shutdown/reboot with retained preferences, fresh application storage using the current seed and proxy handoff. QEMU audio was recorded; intelligibility was not independently rated. The exact final production image separately reached the main menu without the test observer.

Windows installer sources passed 91 tests and actual Qt interface checks with mocked disks. Verification of all payload objects and the final compiled EXE startup is recorded in `FlashOS-Setup.verification.json` beside the installer, bound to the EXE's SHA256. No physical USB writes or host reboot occurred; this update did not test Windows 10, physical Wi-Fi or physical audio devices.

Current evidence is in `metadata/testing.json` under `speech_orca_update`; local logs and screenshots are in `build/speech-orca-20261013`. Earlier metadata is retained in full under `historical_previous_release`; the archived checks below apply to the older images named there.

## Publication checks

The full suite in Debian WSL with a real Tk display through Xvfb and
cryptography installed ran 587 tests, with 27 skipped and no errors.
This includes five new split-archive reconstruction tests. All 91 installer
tests passed on Windows. Linux CI now installs cryptography and uses Xvfb
to exercise the encrypted vault and graphical interface.

Every indexed member in the reassembled hardware/application source ZIPs
matched its previous SHA256. Their new container hashes are recorded in
`metadata/source-archives.json`; source contents are unchanged. The EXE
and system archive are repackaged after documentation changes, using the
same certified Linux image. Final EXE checks cover payload hashes, Windows
PE acceptance and compiled interface startup with mocked disks. Results
are published beside the EXE.

## Archive: network settings verification — 13 October 2026

The local FlashOS 2026.10.13.1 Linux image was repacked with Ethernet and Wi-Fi settings, saved profiles, an encrypted credential vault, and proxy handoff to the store and applications. `filesystem.squashfs` SHA256: `de07eed2afd7a3626209a0c296ef1db618cd579a924ff8bd8cfa31d3e88466d2`. The Windows EXE was not rebuilt and contains the system from before this update.

- Linux in Docker: 537 tests, 29 skips, no failures.
- Windows: all 91 installer source tests passed without skips. This does not mean the new Linux image is included in the existing EXE.
- A real Tk display under Xvfb: all 6 graphical tests passed without skips. Another 15 screens were checked at 1366×768, 1024×768 and 640×480, with screenshots visually reviewed. Checks cover keyboard navigation, spoken focused action and position, protected password entry, editing cancellation, and timed connection confirmation.
- Two independent reviews completed with no open findings: 72 checks covering the profile model, vault, settings and proxy, plus 93 checks covering network execution and menus. These checks are part of the overall suite and are not added to the 537-test total.
- A disposable 64-MiB FAT32 image retained a static profile with DNS and its encrypted vault after unmounting and remounting. Neither the master password nor the network password appeared as plaintext in the raw image.
- Production verification compared all 39 Python files against squashfs and checked 75 runtime commands, 490 live-system packages and 454 baseline application-runtime packages. Nine Debian source files cover the new cryptographic dependencies with verified checksums.

QEMU inside Docker used a disposable virtual USB drive and a controlled test observer. Checks exercised real DHCP and static IP, gateway, DNS, extra routes and metrics; cancellation, timeout and a process crash restored the prior connection. Reboot, interface renaming with the same permanent MAC, boot on a different virtual PC and adapter, and return to the original PC were checked. Foreign static settings were skipped; profiles and the encrypted vault remained intact.

Linux mac80211_hwsim and a separate hostapd access point exercised hidden WPA2, WPA3 SAE, actual IPv4/IPv6 packet traffic through simulated radios, an IPv6 address conflict and restoration of the previous Wi-Fi connection. Real APT requests used an authenticated local proxy with a private RAM configuration file; passwords were absent from process arguments and persistent logs. An application launched as UID 1000 received the proxy environment. After a network failure and fresh DHCP, the store refreshed the signed Debian catalogue. A separate boot of the final production image without the test layer reached the main menu.

Repeated forced power-offs damaged FAT only in the disposable virtual USB copy. fsck repaired it; profiles survived, the vault authenticated with AES-GCM, and subsequent writes and graceful reboot passed. Atomic file replacement does not guarantee whole-FAT integrity after power loss. No physical USB devices were written; physical Wi-Fi and boot on real hardware were not tested for this update.

This update is recorded under `historical_previous_release.network_configuration_update` in `metadata/testing.json`; local logs, independent reviews, FAT32 checks and screenshots are in `build/network-config-20261013`. Earlier verification remains below as an archive and applies to the specific images named there.

## Archive: store connection verification before saved network profiles — 13 October 2026

The local FlashOS 2026.10.13.1 Linux image was repacked without compiling the EXE. Squashfs SHA256: `0ea882a44279b2a8390a0baf22b87418c19a4cee729b17483b1a4a210179a60c`. The earlier image and certificates were backed up separately.

- Linux in Docker: 405 tests, 29 skips, no failures. All 6 graphical tests also passed with a real Tk display. New connection screens were checked at 640×480 and 1024×768, and screenshots were reviewed.
- Windows: 91 installer source tests passed. The EXE was not rebuilt and still contains the earlier system.
- QEMU inside Docker, disposable virtual USB and the current image with a test observer: cached catalog reading offline, actual APT connection failure, the network-tools offer, Escape without retrying, Ethernet DHCP connection and a successful signed Debian catalog refresh after an explicit retry. The tab, source and search query were retained.
- All 16 network-scenario checks passed. Escape during an actual APT download returned the cached catalog without a connection offer. After removing the QA layer, a separate clean-image BIOS boot reached the graphical main menu.
- Tests distinguish signature, storage and repository errors, suppress network recovery after cancellation, and require a new installation plan and confirmation after a download failure. Package writes that already started require recovery rather than automatic replay.
- Certificates compare all 34 Python files against squashfs and verify 75 runtime commands, the hardware set and 454 baseline application packages. The test observer was not added to the distributable squashfs. System file checksums were verified.

The real network scenario uses virtual Ethernet. A physical Wi-Fi access point was not used in this run; Wi-Fi and DHCP menu routes have unit coverage. Physical USB devices were not rewritten. This update's details are recorded under `historical_previous_release.network_store_update` in `metadata/testing.json`; local logs and screenshots are in `build/network-store-20261013`.

## FlashOS 2026.10.13.1 verification before the store connection update

Built image SHA256: `5e64c73040e371401bc5fad61f60e68cf9f146c5b7aaf15c75cedf250a048252`.

- Linux: 367 tests, 27 skips, no failures.
- Windows: 91 installer tests passed; layout, keyboard operation and accessible labels checked.
- Docker: real file-backed exFAT/ext4 creation at 2, 4 and 8 GiB, growth, reopen, portable profile, cancellation, completed preparation recovery and preservation of incomplete images.
- QEMU: 8-GiB preparation inside FlashOS, dependency installation, launch, reboot, saved document/profile retention and uninstall. GUI, Orca and window controls checked.
- Separate production boots without the QA layer: BIOS, UEFI and Secure Boot with previously enrolled test keys.
- Source, boot file, runtime and hardware component checks bind the actual new image. Kernel, initrd and package runtime remain unchanged.

After compilation, the installer undergoes separate Windows image and actual GUI startup checks without disk operations. Final evidence and checksums accompany the EXE in `FlashOS-Setup.verification.json` and `SHA256SUMS.txt`.

Kingston logs were read: formatting its 8-GiB internal image timed out. That physical failure was not reproduced in Docker. The physical USB was neither formatted nor modified. Windows 10 was not booted; Windows checks ran on Windows 11. Third-party accessibility depends on each application.

`metadata/testing.json` separates current verification from historical evidence. Reinstallation with the EXE deletes the selected USB partitions: first back up required ISOs, documents, settings and completed application storage.

## Historical verification of version 2026.10.12.2

The following section refers to the previous image and is retained for history.

# Testing and limits of the evidence

[Русский](../ru/testing.md) · [Home](../../README.en.md)

## FlashOS 2026.10.12.2 and installer 1.0.6

The production image with the Debian store and Utilities was verified: squashfs SHA256 `d57b6ab77157f5da22b5eded22532e093784d4a2d98e537f71588a2b87114c96`, application seed `c294bd24ec0e5bff9f631dca17078ad9daeaac5eaa37db570e3ff54c46cf6d76`. Certificates compared all 34 Python files with squashfs, checked the hardware set and the separate 454-package application root with UID 1000 and official signed sources. The production image contains no guest test services.

Linux passed 297 unittests with no errors and 20 Windows/Qt skips. Windows separately passed 54 installer tests. Source Qt checks with fictitious drives confirmed six groups: layout, keyboard, confirmation, progress, log selection and error handling, at 640×480, 800×600, 1000×800 and 1280×900.

This image passed 26 application and Utilities checks in QEMU/UEFI with a separate test overlay. Evidence covers the Utilities menu and six registered packages, persisted 150% scale and enabled screen reader, Mousepad and Nano running as UID 1000 without capabilities, applied GTK scale, and a persisted 39-byte document exposed in full through AT-SPI, with real Orca reporting the entire-document selection in a Russian voice family. Short speech received BEGIN and END events. Private audio used a virtual USB sink. Alt+F4 returned both programs to FlashOS and removed their processes and Orca; Nano completed successfully with its separate terminal-session completion record. Menu and return screenshots were reviewed separately. Direct AT-SPI read the full 39-character text. Document-content speech is separately evidenced on the earlier4973 image; current logs confirm the Russian full-selection announcement.

Three saved acceptance phases completed (recorded checks: 1: 23, 2: 8, 3: 15): installation and dependencies, reopening and persistence, and application removal preserving user files. Phase 1 belongs to an earlier iteration; final phases 2 and 3 are separately bound to this image. Earlier build results and direct helper tests do not replace final GUI verification.

After disabling the test overlay, this exact production image cold-booted in BIOS, UEFI and UEFI Secure Boot to `FLASHOS_READY … menu=main`. Secure Boot used previously enrolled OVMF keys; first-use enrollment with clean keys remains untested.

The backend passed 20 real Debian checks, including signed catalog refresh, dependencies, Backports, interrupted-operation repair and cache cleanup. Preferences passed nine separate real Gio/dconf checks as UID 1000 with independent D-Bus sessions; that helper test alone does not verify the visible menu or systemd client. Archives cover all 453 exact source-package versions across both roots; SHA256 and size of all 1517 indexed members in four archives were verified. The earlier seed snapshot inside applications-sources.zip remains historical; metadata and certification record the current 454 packages and seed hash.

The compiled EXE and payload audit is stored separately in `release/FlashOS-Setup.verification.json` beside the EXE. These bundled documents record system checks before packaging and do not certify the later executable build. Compiled GUI startup was not tested: the Windows process had no administrator token, and no UAC prompt was requested.

The physical PC was not rebooted into this image, and the rebuilt EXE was not written to physical USB. Physical Wi-Fi, audio, GPUs, speech intelligibility on speakers, a braille display and power loss during writes remain untested. Previous-release results below retain their original verification scope.

## FlashOS 2026.10.12.1 and installer 1.0.5

The new image uses Linux 7.2.6, Debian firmware 20260810, SOF 2026.09.2 and Mesa 26.1.6. Docker/QEMU reached the main menu with BIOS/HDA, BIOS/AC97, UEFI/USB audio and UEFI Secure Boot/HDA. Secure Boot used a separate copy of previously enrolled OVMF keys; first boot with clean keys was not tested.

All four modes produced an audio signal. Unplugging USB audio showed the missing-output message; reconnecting restored speech. Screenshots confirmed the graphical menu for HDA, UEFI/USB and Secure Boot. The AC97 screenshot was captured during the synchronous greeting; serial readiness and recorded audio establish that mode's result. A VM recording does not establish speech intelligibility on physical speakers.

The Linux suite passed 192 tests with 20 Windows/Qt skips. Windows passed 54 installer tests. New negative cases reject missing microcode, wrong PCI subsystem IDs, mismatched kernel/initrd, stale hardware evidence and swapped boot files during packaging.

The shipped kernel resolves the Lenovo 21MS's Intel AX211 8086:7e40 to iwlwifi and Intel I219-V 8086:550b to e1000e. AX211 uses the iwlwifi-ma-b0-gf-a0 API 89 firmware and PNVM, both present. These drivers existed before; the kernel/firmware were refreshed and exact matching is now checked. The initrd carries early Intel and AMD microcode regardless of the builder CPU.

Image composition/hashes are recorded in metadata/testing.json; source certification compares application files to squashfs. FlashOS-Setup.verification.json beside the final EXE records its SHA256 and later compiled-installer/guest checks. Historical results below belong to their respective releases.

QEMU received no physical host disks. The new EXE was not written to a physical USB drive and the notebook was not rebooted into the new system. Physical Wi-Fi, audio and GPUs remain untested.

## FlashOS 2026.09.12.1 and installer 1.0.4

Copies of two user-provided ISOs, Debian 13.6.0 amd64 netinst and Win11PE, were tested in Docker with QEMU 10.0.13. Each was launched from the `iso` directory through the FlashOS menu with Secure Boot disabled and enabled using previously enrolled OVMF keys. Debian reached its graphical installer; Win11PE reached its desktop. Returning from Debian reached the main menu without automatically launching the ISO again. No guest operating system was installed to a disk, and no physical host disk was exposed to the VM.

System 2026.09.09.3 failed to open the menu when `ventoy/flashos.env` was removed. The updated system recreated it, listed both ISOs and launched Debian. Unit tests also cover empty, malformed and oversized files, preserving a healthy file's allocation and rejecting links. A forced VM reset during another FAT write left filesystem damage and a read-only mount: launching stopped with an error. Recovering the selection file does not repair general filesystem damage after power loss.

Fresh OVMF Microsoft-key variables without Ventoy enrollment produced `Verification failed: (0x1A) Security Violation`. Automatic first boot with unprepared keys has not been established. Enrolled keys and compatible firmware remain conditions of the successful Secure Boot checks; see [troubleshooting](troubleshooting.md). Two tested images do not establish universal ISO or PC compatibility.

Linux passed 181 tests with 20 Windows/Qt checks skipped. Windows passed 54 installer tests. Packaging verifies the sources, live version and squashfs SHA256 together; changed sources, an image change or merely relabeling external version markers invalidates old evidence. The prepared-system export requires the same verification.

Physical acceptance of the exact final EXE is recorded after packaging in `FlashOS-Setup.verification.json` beside the EXE, bound to its SHA256. The physical PC was not rebooted, and NVDA speech was not independently listened to.

## Previous release history

Historical release: FlashOS 2026.09.09.3, installer 1.0.2. These checks establish specific behaviour, not universal PC compatibility.

### Automated checks

Debian ran `python3 -m unittest discover -s tests -q`: **167 tests, no failures, 14 Windows/Qt checks skipped**. Windows separately passed **48 installer tests**, including writer-child termination, device identity, prohibited targets, damaged payloads, MBR/GPT/EFI verification, locking through cleanup, text caret behaviour, focus and progress metrics.

The live-image check compares every `src/*.py` with squashfs and rejects guest test services/scripts in the user image. The prepared system was checked for 70 runtime commands, Tk 8.6 and smartctl argument handling. Tests cover build-host state removal, refusal to sanitize an unrelated root and escaping parent symlinks.

The Qt test uses fake drives. It checks keyboard interaction, deletion confirmation, multiple devices, progress/errors and layouts at 640×480, 800×600, 1000×800 and 1280×900, with an additional 200% scaling check. The final EXE was checked for selecting the first log entry with arrow keys through TextPattern, F6 status focus, help-section changes with arrows, Tab into help text and movement through the EXE component table. A single-file launch from a Unicode directory was also checked. These checks do not replace user testing with NVDA, JAWS or Narrator; NVDA speech was not independently listened to.

Publication checks cover documentation links, tracked-file boundaries, generated images and credential-like strings in Git, archive hashes and Debian source-package completeness. GitHub Actions repeats safe unit checks and never writes physical disks.

### Boot and recovery

Before public packaging, system 2026.09.09.2 passed 108 Linux unit tests and 42 QEMU tool/recovery scenarios. Separate checks covered BIOS, UEFI, Secure Boot with prepared key state, virtual audio, and backup image creation, verification and restore. These are historical functional tests, not a fresh full rerun of every scenario on .3.

Version .3 retains the tools and booted kernel while changing the version and removing build machine IDs, resolver configuration and logs. The new image reached the QEMU menu in BIOS with HDA audio and UEFI Secure Boot with USB audio and previously prepared keys. Final-EXE launch results are recorded in the release notes. A VM audio recording establishes signal presence, not speech intelligibility on its own.

### Physical USB test

Installer 1.0.2 was tested on one 64 GB Kingston DataTraveler 3.0: exFAT formatting took 20.56 s, installation with verification took 238.56 s, and a separate verification took 37.30 s. A local acceptance script exercised the same writer module embedded in the EXE. Checks covered file SHA256 hashes, partition layout and BIOS/UEFI structures. The internal NVMe partition layout was unchanged.

Only GUI display/navigation refinements followed that physical test; the writer, its dependencies and embedded payload remained identical to the tested versions. The final EXE was checked interactively. Other PCs and physical USB 2.0 were not tested, and these timings describe only this run. A cold boot on the physical PC was not performed. Private logs, device serials and the USB backup are not published. See the [original installer audit in Russian](../ru/installer-audit-2026-09-10.md).

### Hardware-dependent checks

First-use Secure Boot enrollment, firmware-menu accessibility, the PC's actual audio, USB bridge SMART support, firmware/controller/disk combinations and third-party OS installer accessibility still depend on the user's equipment. Kernel/FlashOS memory remains outside memtester. Incomplete SMART or a test without errors does not prove hardware health.

See [contributing](../../CONTRIBUTING.md) for a useful bug report, including the version, equipment, keys and expected result.
