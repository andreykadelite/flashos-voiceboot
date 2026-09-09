# Using FlashOS

[Русский](../ru/guide.md) · [Home](../../README.en.md)

FlashOS boots from USB and reads its menu aloud. Prepare the drive with the [Windows installer](installer.md). Menu labels and speech in this release are Russian.

## Images and boot

Copy `.iso` files to `iso` on the FLASHOS partition. Subfolders work; do not extract the images. Use descriptive filenames. Connect headphones or speakers, select USB in the computer's boot menu and wait for the greeting. Firmware menus and the Secure Boot key enrollment screen are outside FlashOS and are not spoken by it.

Choose **Запустить систему** (Start a system), select an image with the arrow keys and press Enter. FlashOS records a one-time selection and restarts. Ventoy consumes that selection and starts the image. On BIOS machines you may have to choose USB again. The next normal USB boot returns to the talking menu.

The ISO list is rebuilt at every boot; F5 rescans it while the menu is open. The image check examines boot structure, not universal compatibility. Once the selected installer starts, it must provide its own speech and accessibility support.

## Keyboard

| Key | Action |
| --- | --- |
| Up / Down | Previous / next item |
| Enter | Open or activate |
| Escape | Back; request cancellation during a job |
| Home / End | First / last item |
| Page Up / Page Down | Move ten rows |
| Space | Repeat the item or job status |
| F1 | Help for the current screen |
| F3 | Section, position and selection details |
| F5 | Rescan ISO files |
| F6 | Next audio output |
| F8 | Save audio diagnostics |
| + / − | Volume |
| F10 | Power menu, after stopping an active job |

Some laptops require Fn. In text fields, Enter accepts, Escape cancels and Backspace deletes. Passwords are not spoken. Reports are read line by line, including text wider than the screen. Graphical and fallback text modes share the same keys and speech. Mouse use is optional. Navigation reads the item; F3 supplies its context.

## Tools and diagnostics

**Инструменты** (Tools) covers PC information, files, networking, reports and backups. **Диагностика** (Diagnostics) covers memory, CPU, disks, filesystems, network, sensors, audio and keyboard.

Hardware information comes from Linux and firmware interfaces: CPU, RAM modules, PCI/USB devices, disks, partitions and drivers. Virtual machines, USB bridges and firmware may hide fields. Missing information is not itself a fault.

SMART and NVMe reports include information and self-tests supported by the device. Some USB enclosures do not pass these commands through. A passing health status cannot rule out data loss. Disk read tests do not establish write reliability.

The memtester check reports progress and tests allocated, available user-space RAM. It cannot cover memory used by the kernel or FlashOS. Failure to lock memory with mlock invalidates the result. CPU checks create load and can be stopped with Escape.

Filesystem diagnostics do not repair the source. Repair is a separate action requiring confirmation. Reports can contain serial numbers, network addresses and private paths; remove those before sharing them publicly.

## Backup and recovery

Open **Инструменты** → **Резервные копии, восстановление и ремонт** (Backups, recovery and repair).

**Создать образ раздела** (Create a partition image) uses Partclone for ext2/3/4, FAT, exFAT, NTFS, XFS and Btrfs when the unmounted source can be read correctly. It saves to a new folder in `backups` on FLASHOS. Keep the entire folder: `partition.pcl`, `blocks.map` and `flashos-backup.json`. The image and source's used blocks are checked after creation. Incomplete copies without a manifest are not offered for restore.

**Проверить резервный образ** (Verify a backup image) reads the image and checks SHA256 and Partclone checksums. **Восстановить раздел из образа** (Restore a partition) **overwrites the target partition**. The target must be at least as large as the original. Read device details with F3 before confirming. The image is verified before writing, and the restored used blocks are read back afterwards.

This restores a partition, not the whole disk layout or boot structure. The restored filesystem has the original UUID; do not attach both copies to an installed OS at once. After an interrupted restore, repeat it from a verified image before using the target.

For read errors, a separate ddrescue action copies a disk to an image file with a map file. Its IMG/MAP output is not a Partclone backup and cannot be automatically restored by that menu.

PhotoRec recovers files by content into `recovery` on the USB drive without modifying the source. Names and directories are usually lost; duplicates and incomplete files are possible. Encryption without a key, overwritten data and SSD TRIM may prevent recovery. Allow enough destination space.

**Исправить файловую систему** (Repair a filesystem) modifies the selected partition. Automatic repair and a follow-up check are offered for ext2/3/4, FAT and exFAT. Back up first. Other installed utilities may have repair modes that are not exposed in the accessible menu.

## Voice, power and storage

**Настройки голоса** (Voice settings) offers Elena, Irina, Anna, Aleksandr and Yuriy RHVoice voices, eSpeak NG fallback, rate, pitch, volume, sounds and progress intervals. Settings save automatically to `flashos/settings.json` on FLASHBOOT. Save failures are spoken. F6 output selection lasts for the current session; hardware is detected again on the next PC. [Voice licenses](../../THIRD-PARTY.md).

**Питание** (Power) offers shutdown, restart, restart into the main OS, selection of an installed OS and entry into UEFI settings. Installed OS selection uses suitable UEFI entries and BootNext, leaving permanent BootOrder unchanged. It does not find every possible OS. On BIOS or when a usable UEFI entry/EFI partition is missing, use the firmware's disk selection.

FLASHOS is the main exFAT data/system partition. VTOYEFI is Ventoy's 32 MiB service partition. FLASHBOOT is about 512 MiB FAT32 and contains the kernel, initrd, boot files and settings. Do not remove service files or use Ventoy's standard Update action, which may replace FlashOS configuration.

Before reinstalling, copy your ISO files, backups, recovered files, reports and `flashos/settings.json` from the respective partitions to another drive. The EXE's Install action erases them. Remove the drive after a normal shutdown.
