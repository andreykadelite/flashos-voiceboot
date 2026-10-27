# FlashOS 2026.10.13.2 and installer 1.0.8

[Русский](../ru/release-2026.10.13.md) · [Home](../../README.en.md)

Historical release description. The system and source ZIP/7z archives mentioned below are no longer uploaded to releases. For the current version, use the [EXE](https://github.com/andreykadelite/flashos-voiceboot/releases/latest), [component source instructions](sources.md) and [build instructions](build.md).

This release replaces FlashOS 2026.09.12.1 with installer 1.0.4. It adds a
Debian application store, persistent applications on USB and Orca speech
for external applications that support AT-SPI.

The store supports search, installed packages, updates, official Debian 13
repositories and explicit Backports selection. Package changes require
confirmation of the full dependency plan. Applications, documents and
preferences persist in `flashos/applications/store.ext4`. Installed programs
and a previously downloaded catalog are available offline. Ctrl+Alt+Escape
ends the external session and returns to FlashOS.

Other additions include saved Ethernet/Wi-Fi profiles, automatic or static
IPv4/IPv6, DNS, routes, proxies, an encrypted credential vault, and timed
confirmation with connection rollback. Utilities provide Orca speech and
application appearance settings. FlashOS and Orca share five RHVoice voices
and Russian eSpeak NG. F7, Enter or Space can skip introductory announcements
without confirming the next action. Kernel, firmware, SOF, Mesa and CPU
microcode have also been updated.

Wi-Fi discovery now retries adapter detection while the driver starts and
can load a missed driver for a matching device without resetting an active
connection. Failed quick connections restore the previous network, and scan
details remain on USB. Fixes also cover browsing large folders, UTF-8/UTF-16
text previews, handing ISO selections to the bootloader and GPT handling in
the installer. System and installer version numbers remain unchanged.

Download [FlashOS-Setup.exe](https://github.com/andreykadelite/flashos-voiceboot/releases/latest/download/FlashOS-Setup.exe).
The installer includes its runtime and system files; Python, Qt and internet
are not needed for installation. The other
[release files](https://github.com/andreykadelite/flashos-voiceboot/releases/latest)
are for building from source.
Installation erases the selected USB drive. Back up its ISO files, documents,
settings and application store first. See the [installer guide](installer.md)
and [user guide](guide.md).

## System archive for building

`FlashOS-2026.10.13.2-system.7z` is for developers building the EXE from source; the
ready-made EXE is sufficient for installation. It contains the `dist` tree
and release information. Third-party sources remain in separate ZIP archives.

Project sources are available in the repository and
`FlashOS-2026.10.13.2-source.zip`.

Open the system archive in 7-Zip and extract at the root
of the source checkout for the same tag, then follow the
[build instructions](build.md). This archive is not a bootable ISO.

## Compatibility

Initial Secure Boot setup may require enrolling a Ventoy key on a firmware
screen without speech. The EXE has no publisher signature yet.
