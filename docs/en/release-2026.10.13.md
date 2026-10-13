# FlashOS 2026.10.13.2 and installer 1.0.8

[Русский](../ru/release-2026.10.13.md) · [Home](../../README.en.md)

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

Download `FlashOS-Setup.exe` and compare its SHA256 with `SHA256SUMS.txt` from
the [release](https://github.com/andreykadelite/flashos-voiceboot/releases/latest).
Installation erases the selected USB drive. Back up its ISO files, documents,
settings and application store first. See the [installer guide](installer.md)
and [user guide](guide.md).

## Reassemble the system build archive

The system ZIP exceeds GitHub's per-file release limit. Download both
`FlashOS-2026.10.13.2-system.zip.001` and `.002`, `system-archive-parts.json`,
and `join-system-archive.py` into one directory. With Python 3.11 or newer, run:

```sh
python join-system-archive.py system-archive-parts.json
```

The script checks every part and the complete archive against the recorded
SHA256 values. It refuses to replace an existing ZIP and removes an incomplete
output after a verification failure. Extract the verified ZIP at the root of
the source checkout for this release tag to populate `dist`. This build archive
is not a bootable ISO; the ready-made EXE is sufficient to install FlashOS.

See [verification results](testing.md) and the release's
`FlashOS-Setup.verification.json` for test scope and the exact EXE hash.
Physical USB installation, a physical PC reboot and Windows 10 were not tested
for this release. Initial Secure Boot setup may require enrolling a Ventoy key
on a firmware screen without speech. The EXE has no publisher signature yet.
