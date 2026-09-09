# Testing and limits of the evidence

[Русский](../ru/testing.md) · [Home](../../README.en.md)

Public release: FlashOS 2026.09.09.3, installer 1.0.1. These checks establish specific behaviour, not universal PC compatibility.

## Automated checks

Debian ran `python3 -m unittest discover -s tests -q`: **139 tests, no failures, 2 Windows-only checks skipped**. Windows separately passed **20 installer tests**, including writer-child termination, device identity, prohibited targets, damaged payloads and MBR/GPT/EFI verification.

The live-image check compares every `src/*.py` with squashfs and rejects guest test services/scripts in the user image. The prepared system was checked for 70 runtime commands, Tk 8.6 and smartctl argument handling. Tests cover build-host state removal, refusal to sanitize an unrelated root and escaping parent symlinks.

The Qt test uses fake drives. It checks keyboard interaction, deletion confirmation, multiple devices, progress/errors and layouts at 640×480, 800×600, 1000×800 and 1280×900. UI Automation checks established named-control exposure, not how every screen reader announces them. They do not replace user testing with NVDA, JAWS or Narrator.

Publication checks cover documentation links, tracked-file boundaries, generated images and credential-like strings in Git, archive hashes and Debian source-package completeness. GitHub Actions repeats safe unit checks and never writes physical disks.

## Boot and recovery

Before public packaging, system 2026.09.09.2 passed 108 Linux unit tests and 42 QEMU tool/recovery scenarios. Separate checks covered BIOS, UEFI, Secure Boot with prepared key state, virtual audio, and backup image creation, verification and restore. These are historical functional tests, not a fresh full rerun of every scenario on .3.

Version .3 retains the tools and booted kernel while changing the version and removing build machine IDs, resolver configuration and logs. The new image reached the QEMU menu in BIOS with HDA audio and UEFI Secure Boot with USB audio and previously prepared keys. Final-EXE launch results are recorded in the release notes. A VM audio recording establishes signal presence, not speech intelligibility on its own.

## Physical USB test

The local 1.0.0 installer performed real format, installation and verification on one 64 GB Kingston DataTraveler 3.0 drive. Checks covered files, BIOS/UEFI structures and preservation of restored user data. The computer's system disk was unchanged.

Public installer 1.0.1 refreshes system/documentation packaging and retains the writer. This is not presented as repeated physical testing across many drives. A cold boot of the public release on the physical laptop was not performed as part of publication. Private logs, device serials and that USB backup are not published.

## Hardware-dependent checks

First-use Secure Boot enrollment, firmware-menu accessibility, the PC's actual audio, USB bridge SMART support, firmware/controller/disk combinations and third-party OS installer accessibility still depend on the user's equipment. Kernel/FlashOS memory remains outside memtester. Incomplete SMART or a test without errors does not prove hardware health.

See [contributing](../../CONTRIBUTING.md) for a useful bug report, including the version, equipment, keys and expected result.
