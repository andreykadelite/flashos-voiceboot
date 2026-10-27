# Flash OS Installer for Windows

[Русский](../ru/installer.md) · [Home](../../README.en.md)

Installer 1.0.11 packages FlashOS 2026.10.27.1. See the [release notes](release-2026.10.27.md). It runs on Windows 10 1809+ and Windows 11 x64. It contains its runtime and system files; no Python, Qt installation or network connection is required. Allow several gigabytes of free local space for extraction.

## Install a USB drive

1. Download [FlashOS-Setup.exe](https://github.com/andreykadelite/flashos-voiceboot/releases/latest/download/FlashOS-Setup.exe). The release contains only the EXE. [Source code and documentation](https://github.com/andreykadelite/flashos-voiceboot/tree/v2026.10.27.1-installer-1.0.11) are in the repository; developers can follow the [build instructions](build.md).
2. Back up the USB drive. Connect a drive of at least 4 GB, preferably 16 GB or more for your ISOs.
3. Run the EXE and allow the Windows administrator prompt before the menu opens. Select the drive by its model, size, disk number, volume letters and serial. If several drives are connected, choose one yourself.
4. Choose **Установить** (Install). The erase confirmation opens immediately for the selected drive. The drive selection and options stay in the same window.
5. Read the confirmation. **Installation deletes every partition and file on that USB drive, including old ISO images and voice settings.** Confirm only after checking its identity.
6. Wait for a successful verification message. The progress area and log show stages and errors. Keep the USB connected.
7. Copy your own `.iso` files into `iso` on FLASHOS. OS installation images are not included.

## Buttons and options

| Russian label | Meaning |
| --- | --- |
| Установить | Create a fresh FlashOS USB and verify the copied files and boot structures |
| Проверить | Compare an existing USB with the system bundled in this EXE, including BIOS/UEFI structures |
| Форматировать | Erase FlashOS and other data; create one ordinary exFAT, NTFS or supported FAT32 partition |
| Открыть iso | Open the selected drive's ISO folder |
| Сохранить журнал | Save the operation log |
| Состав EXE | View the bundled Windows runtime, embedded Linux package list and licenses |
| Справка | Open help |
| Остановить | Request cancellation |

Verify leaves user ISO files, settings and reports untouched. A different FlashOS version may legitimately differ from the embedded files. Format performs a quick format, not secure erasure. FAT32 formatting is offered up to 32 GB and cannot store individual files of 4 GiB or larger.

MBR is the default for BIOS/UEFI compatibility. Select GPT for drives of 2 TiB or more. Some old BIOS systems cannot boot GPT. Devices must report 512-byte logical sectors. System disks, disks used by Windows or a pagefile, and the disk containing the running installer are excluded from destructive operations.

## Accessibility

Tab / Shift+Tab move between controls; arrows select list items; Enter or Space activates buttons. F1 opens help, F5 refreshes devices, F6 focuses status, Ctrl+L focuses the log and Ctrl+S saves it.

In the log, help and read-only fields, arrow keys move the actual text caret so screen readers can follow lines and characters. Home/End move within a line; Ctrl+Home/Ctrl+End move to the start/end of the document. Shift+arrows select text and Ctrl+C copies it. Incoming log entries preserve the reading position. Help has a section list: choose with arrows, then Tab into its text; Escape closes it.

F6 refreshes a readable status snapshot containing the stage, current file, completed and remaining stage percentage, measured speed, elapsed time and any available stage ETA. The snapshot is held while focused; press F6 again to refresh and announce it. The adjacent visual indicators continue updating. Completion and errors leave focus in place. Removing a selected drive never selects a different USB automatically.

## USB speed and waiting

Local preparation, USB writing and USB verification have separate measured speeds. Percent and ETA refer to the current stage. Partitioning and cache flushes can take an unknown amount of time, so the installer does not invent an overall completion percentage or a USB generation from the drive's name. USB 2.0/3.x ports, hubs and device controllers affect actual speed. Slow transfers are allowed to finish; a waiting indicator shows how long there has been no new report. Cancellation waits for a pending system write to finish.

Verify extracts only small reference files and boot images on the PC, then checks the installed USB; it no longer unpacks the entire system image. Installation still validates the full embedded payload before altering the USB and flushes the volume caches before readback.

Native Qt widgets have accessible names. Progress stages are sent through Qt's accessibility interface; speech depends on the running screen reader. The log remains readable while work continues. The window follows Windows scaling, supports small windows with scrolling and uses system colours in high-contrast mode. The installer UI is Russian; it does not include its own speech synthesizer.

## Cancellation and errors

Cancellation waits for active partition/format commands to finish. Do not kill the writer in Task Manager. Once writing starts, old data is gone and cancellation can leave the new system incomplete. Repeat installation before using the drive.

A changed device identity stops the operation. Reconnect and refresh. If a disk is busy, close files and other disk-writing programs. For checksum errors, retry with another port or drive. Logs are saved locally under `LocalAppData/FlashOS Installer/runs`; remove private details before sharing them.

The EXE is unsigned and Windows may identify an unknown publisher. Obtain it from the project release and verify its hash. Secure Boot may require an unspoken key-enrollment screen. See [troubleshooting](troubleshooting.md), [licenses](../../THIRD-PARTY.md) and [test coverage](testing.md).
