# Project architecture

[Русский](../ru/architecture.md) · [Home](../../README.en.md)

FlashOS has two independent runtimes in one repository: a bootable Linux environment and a Windows USB installer.

On USB boot, Ventoy and Debian boot components start Linux. The live system reads `filesystem.squashfs` with a temporary RAM overlay. Drivers, ALSA, PipeWire/WirePlumber and UCM profiles provide audio access. The Python menu uses RHVoice or eSpeak NG. Its graphical frontend is Tk/Xorg, with a talking text fallback.

The catalog is built from `iso`. A choice is stored as a one-time job on FLASHBOOT. After restart, Ventoy's configuration reads and clears it, then boots the ISO. This accounts for the restart and the need to reselect USB on some BIOS machines. Signed EFI files are separate from the modified GRUB configuration.

Tools share job handling for device selection, preconditions, speech, logs, cancellation and result parsing. A successful command is not treated as proof of complete hardware health. Recovery writes are separate from read-only diagnostics.

The installer uses PySide6/Qt Widgets. Discovery and writing run separately from the UI. Device properties are checked again before a destructive operation. SHA256 validates the embedded payload, ACLs protect the working directory and a Windows Job Object contains writer subprocesses. Completion includes file, partition and boot-structure verification. See `installer/model.py`, `backend.py`, `secure_run.py`, `disk_lease.py`, `process_group.py`, `engine.ps1` and `boot_verify.py`.

| Directory | Contents |
| --- | --- |
| `src` | Menu, speech, ISO catalog, diagnostics and recovery |
| `installer` | Qt GUI, USB discovery, writing and verification |
| `boot` | systemd, audio and boot configuration |
| `build` | Debian provisioning and system image build |
| `tools` | Packaging, virtual machines and checks |
| `tests` | Unit and guest-system tests |
| `docs/ru`, `docs/en` | Documentation |
| `metadata`, `licenses` | Versions, checksums and third-party notices |

Generated `dist` files, EXEs, private reports, backups and downloads stay outside Git history. User artifacts and third-party source archives are assets in the same GitHub Release.
