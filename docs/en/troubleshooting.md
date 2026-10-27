# Troubleshooting

[Русский](../ru/troubleshooting.md) · [Home](../../README.en.md)

## The installer cannot see a drive

Press F5, try another USB port and check Windows Disk Management. The installer accepts USB devices with 512-byte logical sectors and excludes system, protected, offline and installer-hosting disks. With multiple drives, it does not transfer a selection to an unrelated device. Do not bypass identity checks by editing disk numbers.

## Windows reports an unknown publisher

The EXE is unsigned. Get it from the [project release](https://github.com/andreykadelite/flashos-voiceboot/releases/latest). If needed, compare `Get-FileHash .\FlashOS-Setup.exe -Algorithm SHA256` with the file's digest in GitHub Releases. Organisational Windows policies may block unsigned applications; FlashOS does not bypass them.

## A write failed or was cancelled

Save the log. Once installation starts, cancellation cannot restore the old contents. Wait for the system command to stop, then repeat installation. Check local extraction space, close applications using the USB, and try another port or drive. Do not unplug while the writer is active.

## Secure Boot requests a key

Some firmware requires Ventoy certificate enrollment through MOK. FlashOS cannot speak that screen because Linux and its speech system have not started. Sighted assistance may be needed. Independent enrollment on every PC is not guaranteed. Follow [Ventoy's official procedure](https://www.ventoy.net/en/doc_secure.html); no purchased FlashOS key is required. External boot and certificate trust are controlled by the computer owner and firmware.

`Verification failed: (0x1A) Security Violation` occurs before the voice menu starts. Ventoy's procedure requires a new key for versions 1.1.13 and later because of the UEFI CA 2023 transition. In the FlashOS 2026.09.12.1 test, fresh OVMF Microsoft-key variables stopped at this error; Debian and Win11PE booted with previously enrolled keys. Follow the firmware vendor's and Ventoy's enrollment procedure; automatic enrollment on every PC has not been established. See [test details](testing.md).

## There is no sound

Wait for boot, check speakers/headphones and volume, then try F6 with a pause between presses. Audio may initially use HDMI or another connector. USB headsets can be detected after boot, though connecting before boot is preferable. F8 saves an audio report. A driver being present does not guarantee support for a particular board; new hardware may need a newer kernel, firmware or UCM profile. Bluetooth audio is not a supported first-boot method.

## Wi-Fi hardware or networks are missing

Retry Tools → Wi-Fi: NetworkManager may discover a newly enabled radio or USB adapter asynchronously. An unmanaged device, hardware/software radio block, service error and missing kernel interface are reported separately. FlashOS does not automatically take ownership of unmanaged interfaces.

Wi-Fi scanning and profile network scanning automatically save reports and attachments under `flashos/reports` on FLASHBOOT. Details include NetworkManager state, kernel interfaces (`iw dev`), `rfkill`, PCI drivers, USB devices, discovery services and driver boot logs. Diagnostics → Drivers provides another report. The automatic `flashos/audio-last.json` snapshot and F8 now retain kernel context and PCI driver bindings, including non-audio Wi-Fi failures. Network passwords are not passed to diagnostic commands.

If no kernel Wi-Fi interface exists, FlashOS checks missed PCI/USB driver autoload once. Loading is permitted only for an unbound device with an unloaded module from the installed wireless driver tree. Module blacklists are respected. Loaded/bound drivers and active connections are retained; attempt context is saved in `wifi-recovery.json` beside the report. This does not repair faulty hardware or failed initialization of an already loaded driver.

A missing interface needs the exact PCI/USB ID, driver binding and requested firmware filename from the kernel log. Driver files being present does not establish successful initialization. The official [Linux Wireless debugging instructions](https://wireless.docs.kernel.org/en/latest/en/users/drivers/iwlwifi/debugging.html) also require a kernel log for Intel Wi-Fi failures.

When booting after Windows, check its Fast Startup setting: ordinary shutdown with this enabled can retain hardware state. For comparison, hold Shift while choosing Shut down, then boot FlashOS. The [iwlwifi documentation describes this dual-boot factor](https://wireless.docs.kernel.org/en/latest/en/users/drivers/iwlwifi.html). It is a possible cause; a missing-interface message alone cannot establish the diagnosis.

Clear hardware blocks using the switch/key or BIOS/UEFI setting. Firmware failures require that PC's kernel messages. Mention whether the issue persists after a full shutdown. A failed quick connection attempts to restore the previous connection; saved profiles and unrelated connections are retained.

## The terminal does not echo characters or repeats the prompt

Open the terminal and press Ctrl+Shift+P. Select characters or characters and words under input echo; Ctrl+Shift+E cycles the modes. Set the shell prompt to once per window. New-output reading has a separate checkbox and Ctrl+Shift+A shortcut. To avoid repeating the complete submitted command after Enter, turn off submitted-command speech. Choose «Сохранить» (Save).

Hidden password input intentionally has no echo. After Ctrl+Shift+Space stops speech, the next command permits automatic reading again if enabled. Ctrl+Shift+R reads the screen and Ctrl+Shift+L the cursor line. For complete silence, check Ctrl+Shift+F6 in the terminal and global voice settings. Preferences live in `flashos/terminal.json` on FLASHBOOT. An invalid file produces a warning and uses defaults; saving writes a validated file again. Command contents are not stored there.

If the prompt path is confusing, check it with `pwd`. This build restores missing HOME when starting from the service, and plain `cd` opens the home directory. Incorrectly abbreviating `/run` as `~/run` when HOME is empty has been fixed. If you manually changed or removed HOME inside Bash, use an explicit path or restore the intended value.

## The cursor is hidden or touchpad behaviour is unexpected

Return to the graphical FlashOS menu and press Ctrl+Alt+P. Open Utilities → Accessibility — cursor, mouse and touchpad. Hiding preserves clicks; physical devices are not disabled. All controls remain keyboard accessible. Check the primary-button preference: selecting right-primary makes the left button the secondary Back button.

«Устройства и применение» reports detected devices and errors. Allow a few seconds after connecting a USB mouse. Saving cannot enable an unsupported hardware feature: the driver must expose its XInput/libinput properties. An external program may draw its own cursor with a different colour. For hardware failures, save Diagnostics → Drivers information and provide the model, USB/PS2/I2C connection and exact symptom. Virtual-machine checks do not establish compatibility with a particular touchpad.

## An ISO is missing or fails to boot

Check the `iso` folder on the main partition and the `.iso` extension. Complete the copy and safely eject the drive in Windows. Press F5 or restart FlashOS. Verify the image against its publisher's SHA256. Its architecture and BIOS/UEFI support must match the PC. On BIOS, select USB again after the handoff restart if necessary.

FlashOS speech ends when control passes to the installer. Use that installer's own accessibility features. Working audio in FlashOS does not imply that Windows PE or another ISO includes the same driver.

## SMART is incomplete or a passing memory test conflicts with symptoms

A USB bridge/controller may hide SMART or NVMe fields and self-tests. Available fields depend on the disk. Memtester covers allocated available RAM, not all memory. These checks cannot establish that the entire PC is healthy. Save reports and describe the actual symptoms.

## Updating FlashOS

The current EXE performs a fresh installation. It has no update mode preserving ISO files and settings. Back up those files to another drive, reinstall, then restore them. Do not run Ventoy's standard Update over FlashOS; it may replace the project's boot configuration.

Contact [Issues](https://github.com/andreykadelite/flashos-voiceboot/issues), [@zotovsmol](https://t.me/zotovsmol) or [andreykadelite@ya.ru](mailto:andreykadelite@ya.ru). Review logs for private data before sending.
