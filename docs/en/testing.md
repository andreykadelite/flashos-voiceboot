# Testing and limits of the evidence

[Русский](../ru/testing.md) · [Home](../../README.en.md)

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
