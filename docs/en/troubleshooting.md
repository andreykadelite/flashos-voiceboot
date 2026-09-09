# Troubleshooting

[Русский](../ru/troubleshooting.md) · [Home](../../README.en.md)

## The installer cannot see a drive

Press F5, try another USB port and check Windows Disk Management. The installer accepts USB devices with 512-byte logical sectors and excludes system, protected, offline and installer-hosting disks. With multiple drives, it does not transfer a selection to an unrelated device. Do not bypass identity checks by editing disk numbers.

## Windows reports an unknown publisher

The EXE is unsigned. Get it from the [project release](https://github.com/andreykadelite/flashos-voiceboot/releases/latest) and compare `Get-FileHash .\FlashOS-Setup.exe -Algorithm SHA256` with `SHA256SUMS.txt`. Organisational Windows policies may block unsigned applications; FlashOS does not bypass them.

## A write failed or was cancelled

Save the log. Once installation starts, cancellation cannot restore the old contents. Wait for the system command to stop, then repeat installation. Check local extraction space, close applications using the USB, and try another port or drive. Do not unplug while the writer is active.

## Secure Boot requests a key

Some firmware requires Ventoy certificate enrollment through MOK. FlashOS cannot speak that screen because Linux and its speech system have not started. Sighted assistance may be needed. Independent enrollment on every PC is not guaranteed. Follow [Ventoy's official procedure](https://www.ventoy.net/en/doc_secure.html); no purchased FlashOS key is required. External boot and certificate trust are controlled by the computer owner and firmware.

## There is no sound

Wait for boot, check speakers/headphones and volume, then try F6 with a pause between presses. Audio may initially use HDMI or another connector. USB headsets can be detected after boot, though connecting before boot is preferable. F8 saves an audio report. A driver being present does not guarantee support for a particular board; new hardware may need a newer kernel, firmware or UCM profile. Bluetooth audio is not a supported first-boot method.

## An ISO is missing or fails to boot

Check the `iso` folder on the main partition and the `.iso` extension. Complete the copy and safely eject the drive in Windows. Press F5 or restart FlashOS. Verify the image against its publisher's SHA256. Its architecture and BIOS/UEFI support must match the PC. On BIOS, select USB again after the handoff restart if necessary.

FlashOS speech ends when control passes to the installer. Use that installer's own accessibility features. Working audio in FlashOS does not imply that Windows PE or another ISO includes the same driver.

## SMART is incomplete or a passing memory test conflicts with symptoms

A USB bridge/controller may hide SMART or NVMe fields and self-tests. Available fields depend on the disk. Memtester covers allocated available RAM, not all memory. These checks cannot establish that the entire PC is healthy. Save reports and describe the actual symptoms.

## Updating FlashOS

The current EXE performs a fresh installation. It has no update mode preserving ISO files and settings. Back up those files to another drive, reinstall, then restore them. Do not run Ventoy's standard Update over FlashOS; it may replace the project's boot configuration.

Contact [Issues](https://github.com/andreykadelite/flashos-voiceboot/issues), [@zotovsmol](https://t.me/zotovsmol) or [andreykadelite@ya.ru](mailto:andreykadelite@ya.ru). Review logs for private data before sending.
