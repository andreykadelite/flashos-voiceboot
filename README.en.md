# FlashOS VoiceBoot

[Русский](README.md) · English

A talking multiboot USB system and a standalone Windows installer, in one repository. FlashOS helps blind users choose an operating system installer and run PC diagnostics from a keyboard.

Copy your `.iso` files into the USB drive's `iso` folder. At each boot, FlashOS scans the folder, reads the available choices aloud and prepares the selected image for Ventoy. Adding or deleting an ISO requires no configuration changes.

**[Download the Windows EXE](https://github.com/andreykadelite/flashos-voiceboot/releases/latest/download/FlashOS-Setup.exe)** · [All release files](https://github.com/andreykadelite/flashos-voiceboot/releases/latest) · [User guide](docs/en/guide.md)

Current release: FlashOS 2026.09.12.1 and installer 1.0.4. This release preserves USB selection across administrator startup and restores missing or damaged ISO selection state. [Changes](CHANGELOG.md) · [Test coverage](docs/en/testing.md).

The application interface and spoken help are currently in Russian. English documentation explains the controls and limitations; an English UI is not included in this release.

## Two parts, one project

| Part | What it does | Where it runs |
| --- | --- | --- |
| FlashOS | Spoken ISO selection, hardware information, diagnostics, backup and recovery tools | Boots from USB on x86-64 PCs |
| Flash OS Installer | Selects a USB drive, installs or verifies FlashOS, formats a drive and shows progress and logs | Windows 10 1809+ / Windows 11 x64 |

The installer uses native Qt 6 widgets and Windows accessibility interfaces. It is compiled with Nuitka into one EXE containing the system, bootloaders, drivers, voices and tools. The destination PC needs no Python, Qt installation or internet connection. Windows and Linux installation ISOs are supplied by the user.

<details>
<summary>Screenshots: talking menu and Windows installer</summary>

![FlashOS main menu: Start a system, Tools, Diagnostics, Power, Voice settings and Help, with a highlighted keyboard selection.](docs/images/flashos.png)

![Flash OS Installer showing a fictitious USB drive, installation, verification and format controls, progress and log.](docs/images/installer.png)

</details>

## Get started

1. Download `FlashOS-Setup.exe` and check it against `SHA256SUMS.txt` from the same release.
2. Connect a USB drive of at least 4 GB; 16 GB or more leaves room for installation images. Save its existing files elsewhere.
3. Run the EXE and allow the Windows administrator prompt before the menu opens. Select the drive by model, capacity and serial number, then choose **Установить** (Install).
4. Confirm the selected drive. **Installation deletes all its partitions and files.** Wait for the write and verification to finish.
5. Copy your ISO files into `iso` on the **FLASHOS** partition. Subfolders are supported.
6. Connect headphones or speakers, boot the PC from USB and wait for speech. Open **Запустить систему** (Start a system), choose an image with the arrow keys and press Enter.

FlashOS restarts to hand the choice to Ventoy. On a BIOS machine you may need to select USB again. Speech inside the selected OS installer depends on that installer's own accessibility support.

## What is included

The main menu contains Start a system, Tools, Diagnostics, Power, Voice settings and Help. Navigation reads the selected item without repeating the section name. Space repeats the item; F3 reads its context.

- Hardware inventory: CPU, RAM modules, disks, partitions, graphics, network and connected drivers.
- Disk health: detailed SMART and NVMe information, supported self-tests and read checks.
- Diagnostics: available RAM with memtester, CPU, filesystems, network, sensors, audio and keyboard.
- Recovery: Partclone partition images with verification and restore; ddrescue disk-to-file copying; PhotoRec file recovery; supported filesystem repairs.
- Files and networking: file browsing/copying, Wi-Fi connection and report export.
- Speech: five RHVoice voices, eSpeak NG fallback, output selection and saved voice settings.

See [the guide](docs/en/guide.md) for which operations change disks and what each check can establish. Installing a command-line package does not mean every one of its modes has an accessible menu action.

## Compatibility and limits

The USB system targets x86-64 PCs with legacy BIOS or 64-bit UEFI. The installer accepts USB devices with 512-byte logical sectors. ARM, Apple Silicon and 32-bit UEFI are outside this build's supported targets.

**Secure Boot can require Ventoy key enrollment on first use. That firmware screen is not spoken by FlashOS.** Some machines reject third-party certificates or external boot. This is not a promise of independent first boot on every PC.

Linux drivers and firmware cover many devices, but coverage depends on the hardware. F6 cycles audio outputs. The memory test covers available user-space RAM, not all installed memory. FlashOS speech does not make another OS installer accessible.

The EXE is currently unsigned. [Testing and known limits](docs/en/testing.md) distinguish automated and virtual-machine checks from physical hardware testing.

## Documentation and source

- [Using FlashOS](docs/en/guide.md) · [Windows installer](docs/en/installer.md)
- [Building both parts](docs/en/build.md) · [Architecture](docs/en/architecture.md)
- [Troubleshooting](docs/en/troubleshooting.md) · [Test coverage](docs/en/testing.md)
- [Components and versions](docs/en/components.md) · [Licenses and source archives](THIRD-PARTY.md)
- [Contributing](CONTRIBUTING.md) · [Change log](CHANGELOG.md)

`src/` holds the talking system; `installer/` holds the Qt application and Windows writer; `boot/` holds boot configuration. `build/`, `tools/` and `tests/` contain the build scripts and checks. Large system files and the EXE are release assets, not Git history.

## Author and license

Development: **Andrey Anatolyevich Zotov (Зотов Андрей Анатольевич)**.

Report bugs and suggest tools or accessibility improvements through [GitHub Issues](https://github.com/andreykadelite/flashos-voiceboot/issues), [Telegram @zotovsmol](https://t.me/zotovsmol) or [andreykadelite@ya.ru](mailto:andreykadelite@ya.ru). Remove private hardware identifiers and file paths from public reports.

Original project code and documentation use [MIT](LICENSE); retain the copyright and license notice in derivatives. Third-party components keep their own licenses. **The bundled Yuriy voice has a noncommercial, no-derivatives license.** Read [NOTICE](NOTICE.md) and [THIRD-PARTY](THIRD-PARTY.md) before redistributing the complete bundle.
