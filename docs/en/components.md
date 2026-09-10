# Release components

[Русский](../ru/components.md) · [Home](../../README.en.md)

FlashOS 2026.09.09.3; installer 1.0.2. Exact Linux binary versions are in [packages.tsv](../../metadata/packages.tsv); their source-package mapping is in [debian-packages.json](../../metadata/debian-packages.json).

| Component | Version | Purpose |
| --- | --- | --- |
| Debian | 13.6, trixie and backports | Live-system base |
| Booted Linux kernel | 7.1.8-1~bpo13+1 | Drivers and hardware access |
| Ventoy | 1.1.17 | BIOS/UEFI and ISO boot |
| RHVoice | 1.18.4 | Menu speech |
| eSpeak NG | 1.52.0+dfsg-5 | Fallback speech |
| SOF firmware | 2025.12.2 | Supported audio DSPs |
| smartmontools | 7.5-2~bpo13+1 | SMART and self-tests |
| nvme-cli | 2.13-2 | NVMe diagnostics |
| memtester | 4.7.1-1 | Allocated RAM tests |
| stress-ng | 0.19.02-1 | Stress tests |
| Partclone | 0.3.36+repack-1 | Partition images |
| TestDisk / PhotoRec | 7.2-0.1 | File recovery; the menu uses PhotoRec |
| Windows Python | 3.12.10 | Embedded installer runtime |
| Qt / PySide6 | 6.11.2 | Installer interface |
| Nuitka | 4.2.1 | EXE compilation |

Voices: Elena 4.3, Irina 4.1, Anna 4.1, Aleksandr 4.2 and Yuriy 4.0. RHVoice and submodule revisions are recorded in [rhvoice-sources.json](../../metadata/rhvoice-sources.json). Model licenses differ; see [THIRD-PARTY](../../THIRD-PARTY.md).

Other installed components include filesystem and partition tools, networking utilities, NetworkManager, ALSA/PipeWire, Xorg/Tk and firmware for supported Intel, AMD, Realtek, Atheros, Broadcom, MediaTek and other hardware. These are Linux drivers, not a universal Windows driver pack. Detection depends on the device ID and support in the kernel and firmware.
