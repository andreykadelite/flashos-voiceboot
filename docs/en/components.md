# Release components

[Русский](../ru/components.md) · [Home](../../README.en.md)

FlashOS 2026.10.27.1; installer 1.0.11. Build and verification results are recorded in the [report](testing.md). Exact Linux binary versions are in [packages.tsv](../../metadata/packages.tsv); their source-package mapping is in [debian-packages.json](../../metadata/debian-packages.json).

| Component | Version | Purpose |
| --- | --- | --- |
| Debian | 13.6, trixie and backports | Live-system base |
| Booted Linux kernel | 7.2.6-1~bpo13+1 | Drivers and hardware access |
| Ventoy | 1.1.17 | BIOS/UEFI and ISO boot |
| GTK / VTE / Bash | 3.24.49 / 0.80.1 / 5.2.37 | Full spoken terminal in the live system |
| XInput / libinput / Xorg input driver | 1.6.4-1 / 1.28.1-1+deb13u1 / 1.5.0-1 | Supported mouse and touchpad preferences |
| RHVoice | 1.18.4 | Menu and Orca speech; Elena by default |
| eSpeak NG | 1.52.0+dfsg-5 | Menu fallback and available Orca synthesizer |
| Orca | 48.1-1+deb13u2 | External application screen reader |
| Speech Dispatcher | 0.12.0-5 | RHVoice and eSpeak NG modules for Orca |
| SOF firmware | 2026.09.2 | Supported audio DSPs |
| Debian non-free firmware | 20260810-1~bpo13+1 | Audio, Ethernet, Wi-Fi, graphics and other devices |
| Mesa OpenGL / Vulkan | 26.1.6-1~bpo13+1 | Intel, AMD and supported NVIDIA graphics |
| Intel microcode | 3.20251111.1~deb13u1 | Early Intel CPU microcode |
| AMD microcode | 3.20250311.1 | Early AMD CPU microcode |
| ALSA topology | 1.2.5.1-3 | Additional audio DSP configurations |
| smartmontools | 7.5-2~bpo13+1 | SMART and self-tests |
| nvme-cli | 2.13-2 | NVMe diagnostics |
| memtester | 4.7.1-1 | Allocated RAM tests |
| stress-ng | 0.19.02-1 | Stress tests |
| Partclone | 0.3.36+repack-1 | Partition images |
| TestDisk / PhotoRec | 7.2-0.1 | File recovery; the menu uses PhotoRec |
| NetworkManager | 1.52.1-1 | Ethernet, Wi-Fi, IPv4/IPv6, DNS and connection rollback |
| python3-cryptography | 43.0.0-3+deb13u1 | AES-256-GCM for the network-password vault |
| python3-bcrypt / python3-cffi-backend | 4.2.0-2.1+b1 / 1.17.1-3 | Debian cryptography package dependencies |
| Windows Python | 3.12.10 | Embedded installer runtime |
| Qt / PySide6 | 6.11.2 | Installer interface |
| Nuitka | 4.2.1 | EXE compilation |

Application runtime: separate Debian 13 amd64 with APT, Openbox, Orca, GNOME Terminal and PipeWire/PulseAudio. Exact versions: [app-runtime-packages.tsv](../../metadata/app-runtime-packages.tsv); source mapping: [app-runtime-packages.json](../../metadata/app-runtime-packages.json). Applications share the live kernel and drivers, with their own files and package database. The runtime includes `gsettings` from `libglib2.0-bin`, Gio/dconf and GSettings schemas for the USB user profile. Main-menu Utilities offers spoken text scaling from 100–200%, contrast, animations, cursor size, restoration of enabled reader accessibility and confirmed reset of these settings. Preference support depends on GTK and the application; Orca speech depends on AT-SPI.

Utilities also offers Orca speech, following FlashOS or choosing independent voices, rate, pitch, volume and an actual synthesizer preview. Preferences persist in Orca's native profile inside `store.ext4`; user pronunciations, key bindings, braille preferences and other profiles are preserved. The runtime contains the same RHVoice binaries and voice datasets as FlashOS. Older stores use shared data and the module read-only, with libraries supplied through RAM rather than replacing user libraries on USB. F7 stops FlashOS speech; Escape stops the preview and Control interrupts Orca speech in applications.

The seed contains 455 Debian packages. The sole addition to the previous seed is `espeak-ng` version `1.52.0+dfsg-5`; all other package versions are retained. Shared RHVoice is copied from the live system with its licenses and verified manifest rather than installed as a separate Debian package. [Runtime build and verification](build.md#application-runtime-and-corresponding-source).

Menu and Orca voices: Elena 4.3, Irina 4.1, Anna 4.1, Aleksandr 4.2, Yuriy 4.0 and Russian eSpeak NG. Orca follows FlashOS by default, initially using Elena RHVoice. RHVoice and submodule revisions are recorded in [rhvoice-sources.json](../../metadata/rhvoice-sources.json). Model licenses differ; see [THIRD-PARTY](../../THIRD-PARTY.md).

Other installed components include filesystem and partition tools, networking utilities, NetworkManager, ALSA/PipeWire, Xorg/Tk and firmware for supported Intel, AMD, Realtek, Atheros, Broadcom, MediaTek and other hardware. These are Linux drivers, not a universal Windows driver pack. Detection depends on the device ID and support in the kernel and firmware.

`e1000e` and `iwlwifi` were already present in the previous image; this update refreshes their kernel and firmware. Matching PCI aliases are checked in the packaged kernel for Intel AX211 `8086:7e40` and Intel I219 `8086:550b`. Physical notebook boot and its radio/audio operation have not been tested. Nouveau/NVK use open NVIDIA drivers and firmware; the proprietary NVIDIA module is not installed.
