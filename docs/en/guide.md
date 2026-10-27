# Using FlashOS

[Русский](../ru/guide.md) · [Home](../../README.en.md)

FlashOS boots from USB and reads its menu aloud. This guide covers FlashOS 2026.10.27.1 and installer 1.0.11. See the [release notes](release-2026.10.27.md) for the changes. Prepare the drive with the [Windows installer](installer.md). Menu labels and speech remain Russian.

## Images and boot

Copy `.iso` files to `iso` on the FLASHOS partition. Subfolders work; do not extract the images. Use descriptive filenames. Connect headphones or speakers, select USB in the computer's boot menu and wait for the greeting. Firmware menus and the Secure Boot key enrollment screen are outside FlashOS and are not spoken by it.

Choose **Запустить систему** (Start a system), select an image with the arrow keys and press Enter. Launch does not wait for the ISO name to finish speaking. FlashOS records a one-time selection and restarts. Ventoy consumes that selection and starts the image. On BIOS machines you may have to choose USB again. The next normal USB boot returns to the talking menu.

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
| F7 | Stop current FlashOS speech |
| F8 | Save audio diagnostics |
| + / − | Volume |
| F10 | Power menu, after stopping an active job |

Some laptops require Fn. In text fields, Enter accepts, Escape cancels and Backspace deletes. Passwords are not spoken. Reports are read line by line, including text wider than the screen. Graphical and fallback text modes share the same keys and speech. Mouse use is optional. Navigation reads the item; F3 supplies its context.

F7, Enter or Space can stop a long introductory announcement, such as the store accessibility warning or a settings explanation. Enter or Space during that announcement skips only its speech: it neither activates a row in the next menu nor accepts a confirmation. Use the appropriate key once the menu appears. F7 does not cancel a running job; use Escape and wait for its safe completion point.

## Applications and Debian store

Open **Приложения** (Applications) → **Магазин приложений Debian** (Debian application store). First create persistent storage, normally 2 GiB. The file `flashos/applications/store.ext4` lives on FLASHOS and reserves its full size, so allow space beyond the system and ISO files. Existing USB files are retained.

Left/right arrows and Tab switch between Available, Installed and Updates; up/down select rows, Enter opens an action and Escape returns. F2 searches names and descriptions, F4 selects sources, F5 refreshes the catalog online and F3 reads details. Debian packages include libraries and data without a launchable window.

Sources are official Debian 13, security, updates and explicitly selected Backports, with Main, Contrib, Non-free and Firmware components. Signatures are required. Before installation, upgrade or removal, read the complete plan: versions, dependencies, download size and required free space. Confirm the whole plan, including dependent applications affected by removal. Package state is checked after writing. Escape waits for a safe completion point.

When a connection error prevents access to Debian, the store offers **Перейти в Инструменты и подключиться к сети** (open Tools and connect). Choose temporary Wi-Fi, wired DHCP or a saved profile, and check DNS or internet access if needed. Escape returns to the store's recovery choices; explicitly retry the download or return without downloading. The tab, search and source are retained. A connection entry is also available in the store. Receiving an IP address alone does not establish Debian connectivity. Temporary Wi-Fi credentials last until reboot; create a network profile to retain them.

Previously downloaded catalogs and installed applications remain available offline. Retrying an installation after a download failure rebuilds the plan and requires confirmation. If package writes have already started, replay is disabled: connect and use interrupted-installation recovery under Storage and free space. Signature, storage and repository errors remain distinct from connection failures.

Installed applications can be launched or removed. Launch does not wait for its spoken announcement to finish. Graphical programs run as a separate user with Orca; screen-reader support and settings depend on the application. Control interrupts current Orca speech. Ctrl+Alt+Escape closes the external session and returns to FlashOS. Alt+F4 normally closes the active window. Save documents before closing.

Storage and free space shows space inside the image and on USB, offers growth to 3/4/8/16 GiB and recovery from interrupted package changes. FAT32 supports a storage file up to 3 GiB; exFAT allows larger sizes. Applications and settings travel with the USB to another x86-64 PC. Back up `store.ext4` before reinstalling FlashOS. Remove USB after normal shutdown through FlashOS.

## Ethernet, Wi-Fi and saved profiles

These settings are included in FlashOS 2026.10.13.2. See the build's [verification report](testing.md) for results and test scope.

Open **Инструменты** (Tools) → **Подключение к сети и проверка интернета** (Network connection and internet checks) → **Настройки IP, DNS, прокси и сохранённые профили** (IP, DNS, proxy and saved profiles). The quick Wi-Fi and DHCP entries create a connection for the current session. To reuse settings, create an Ethernet or Wi-Fi profile and select its adapter. Scan for Wi-Fi or enter an SSID manually, including hidden networks. The editor supports open networks, WPA2 Personal and WPA3 Personal; it does not configure corporate 802.1X/EAP or WEP.

IPv4 and IPv6 have separate settings. IPv4 supports DHCP, static addressing and disabling. IPv6 supports automatic SLAAC/DHCPv6, DHCPv6 only, static addressing and disabling. Enter static addresses with prefixes, for example `192.168.1.20/24`; separate multiple addresses with spaces. Leave the gateway empty for a local network. DNS can be manual, obtained automatically through DHCP/SLAAC, or disabled for that IP family, with optional search domains. For a static IP family, specify DNS manually or use DNS obtained automatically by the other IP family: selecting automatic DNS alone does not start DHCP for a static address. Lower route metrics take preference. The local-network-only option suppresses this connection's default route. Extra routes use `network/prefix gateway metric`, separated by semicolons; use `-` for a directly connected route's gateway. Use values assigned for your network; examples are not ready-to-use configuration.

The editor changes a draft. **Сохранить профиль на флешку** (Save profile on USB) saves after confirmation; Escape discards unsaved editing. To apply it, open the saved profile and choose **Подключить и проверить** (Connect and check). After connection checks, FlashOS allows up to 60 seconds for confirmation. Declining is selected initially; Escape, timeout or an error restores the previous connection. NetworkManager provides the rollback timer. Confirmation does not establish access to every required resource: an assigned address alone does not establish internet access.

Profiles match the computer and the adapter's permanent MAC address. An interface rename does not bind the profile to another adapter. Autoconnect starts disabled; enabling it disables the previous automatic profile for that computer and adapter. On returning to the same PC and adapter, an enabled profile applies again. Moving that PC to a different wired network can make its old static settings unsuitable: keep them manual or use separate profiles. Another PC's profile is never applied automatically. Create a separate copy for the current PC and adapter when needed; the original remains, and the copy starts with autoconnect disabled. Use a temporary connection if stable identifiers are unavailable.

At boot, FlashOS establishes at most one saved connection automatically and stops after the first successful connection. Connect other profiles manually, including those for a second adapter.

A Wi-Fi profile with automatic IP can allow manual use on another PC. Portable profiles reject static addresses, gateways and extra routes. On an unfamiliar PC, select the connection explicitly; automatic use remains restricted to the original PC and adapter. Create a separate profile or a newly bound copy for another static setup.

Settings are in `flashos/network/profiles.json` on the FLASHBOOT service partition. IP settings, SSID, proxy username and other parameters are unencrypted; Wi-Fi and proxy passwords are separate in `flashos/network/secrets.bin`, protected by AES-256-GCM and a shared vault password. First saving a secret asks you to create and repeat a vault password of at least eight characters. It is neither spoken nor saved on USB. On booting a known PC, unlock the vault to connect to a protected network; Escape continues without saved secrets. The vault menu supports locking and changing the master password. Locking leaves an established connection active. A forgotten password prevents reading saved secrets; keep your password and back up the complete `flashos/network` directory.

Each profile can set separate HTTP and HTTPS proxies, for example `http://proxy.example:3128`. Enter the username separately and the password in its protected field; do not embed them in the URL. Address and domain exclusions are supported. The proxy applies to the APT store and newly launched applications that honor `http_proxy`, `https_proxy` and `no_proxy`; restart already open applications. Configure programs with their own proxy settings separately. The editor does not support PAC/WPAD. Network diagnostics test direct access, so they can differ from the store's access through a proxy.

## Cursor, mouse and touchpad

![FlashOS cursor, mouse and touchpad settings with navigation controls](../images/pointer-settings.png)

Open «Утилиты → Доступность — курсор, мышь и тачпад» (Utilities → Accessibility — cursor, mouse and touchpad). No store preparation is needed. Each item is spoken and keyboard accessible. Values save automatically under `pointer` in `flashos/settings.json` on FLASHBOOT and apply when the graphical menu starts and after device connection.

| Preference | Choices |
| --- | --- |
| Pointer | Show/hide; sizes 24, 32, 48 or 64 pixels |
| Colour | White, black or yellow with a contrasting outline |
| Speed | Five levels from slow to very fast |
| Acceleration | Adaptive or flat |
| Primary button | Left or right |
| Scrolling | Traditional or natural; menu wheel steps of 1, 3 or 5 items |
| Touchpad | Tap to click, tap and drag, disable while typing |
| Touchpad scroll method | Automatic, two-finger or edge scrolling |
| Touchpad click method | Automatic, button areas or finger count |
| Middle button | Emulate by pressing both buttons |
| Item activation | Double click or single click |

By default, one primary-button click selects and reads an item; a double click opens it. Wheel/touchpad scrolling moves the selection without wrapping between list ends. The secondary button returns; with right-primary mode the left button is secondary. Bottom buttons provide Back, Open, Help and Stop speech. Single-click mode opens an item immediately; destructive-action confirmations remain required.

Hiding the cursor does not disable mouse or touchpad input. In the FlashOS menu, Ctrl+Alt+P shows the pointer and saves that value; the keyboard remains available. First close an external application or return to FlashOS using its normal shortcut. The custom cursor's size and colour apply in the menu, while child applications receive the Xcursor size; individual programs may draw their own pointers.

«Прочитать все настройки» reads all values. «Устройства и применение» reports detected pointers and application errors. XInput settings apply only where the device advertises the required property and capability. A saved preference does not establish support by a particular touchpad. Text mode can save preferences for the next graphical launch. Touch screens and three-finger window-management gestures are outside this utility. Wireless mice with USB receivers use USB HID support; this utility does not configure Bluetooth pairing.

## Utilities and application settings

**Утилиты** (Utilities) follows Applications in the main menu. Open **Настройки доступности приложений Debian — GSettings** (Debian application accessibility settings). FlashOS speaks each item and value: arrows select, Enter opens and Escape returns. Save documents and close external applications first. If persistent storage is absent, FlashOS offers to prepare it.

Settings include text size at 100/125/150/200%, high contrast, animations on/off and cursor size at 24/32/48 pixels. A separate item checks screen-reader accessibility and can restore its enabled state. **Прочитать все настройки** (Read all settings) reads the stored values. Visual preferences keep the reader enabled and preserve FlashOS voice settings and the Orca profile.

Confirmed changes are saved in the user profile inside the USB drive's `store.ext4`; they apply on the next application launch and travel with the drive to another PC. **Сбросить параметры этой утилиты** (Reset this utility's settings) requires confirmation and resets only text size, contrast, animations and cursor size, preserving documents, other keys and Orca profile files. Screen-reader accessibility remains enabled.

These settings apply to GTK applications that support them. Other programs may use their own scale, theme and accessibility settings; Orca speech requires AT-SPI support. Ctrl+Alt+Escape closes the external session and returns to FlashOS; Alt+F4 normally closes a window.

### Orca screen-reader voice and speech

Open **Утилиты** → **Настройки экранного диктора Orca — голос и речь** (Orca screen-reader voice and speech). Save documents and close the external application first. The default mode, **Как в FlashOS** (Follow FlashOS), applies current FlashOS voice and speech settings on each application launch. The initial voice is Elena RHVoice. Both environments provide the same Elena, Irina, Anna, Aleksandr and Yuriy RHVoice voices, plus Russian eSpeak NG.

To use independent speech, choose the voice/synthesizer, rate, pitch or volume and confirm the displayed values. Orca then uses its own preferences without changing FlashOS menu speech. Orca scales run from 0 to 100, with rate and pitch 50 at the midpoint; these differ from the FlashOS scales. **Вернуть речь как в FlashOS** (Restore speech following FlashOS) restores following the current menu settings after confirmation.

**Прослушать пример выбранным голосом Orca** (Hear an example using the selected Orca voice) uses the application's actual synthesizer. Escape stops the example and returns to settings; F7 stops built-in FlashOS speech. In a running application, Control interrupts current Orca speech and Caps Lock plus Space opens Orca's own preferences window.

Preferences are stored in Orca's native `/home/flashos-app/.local/share/orca/user-settings.conf` inside `store.ext4`. They persist on USB and apply at the next application launch on any PC. Resetting speech preserves other profiles, pronunciations, braille preferences and key bindings. Existing user-customized Orca speech is retained and uses independent settings. Only the exact old default profile created by FlashOS is automatically migrated. Editing speech through Orca's own window while following FlashOS retains those edits and ends automatic following.

An older `store.ext4` uses the shared voices from the current system. No storage recreation, application reinstall or replacement of user libraries is required. The speech settings utility keeps the screen reader enabled.

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

**Настройки голоса** (Voice settings) offers Elena, Irina, Anna, Aleksandr and Yuriy RHVoice voices, eSpeak NG fallback, rate, pitch, volume, sounds and progress intervals. Elena RHVoice is selected by default. Settings save automatically to `flashos/settings.json` on FLASHBOOT. Save failures are spoken. F6 output selection lasts for the current session; hardware is detected again on the next PC. [Voice licenses](../../THIRD-PARTY.md).

**Питание** (Power) offers shutdown, restart, restart into the main OS, selection of an installed OS and entry into UEFI settings. Installed OS selection uses suitable UEFI entries and BootNext, leaving permanent BootOrder unchanged. It does not find every possible OS. On BIOS or when a usable UEFI entry/EFI partition is missing, use the firmware's disk selection.

FLASHOS is the main exFAT data/system partition. VTOYEFI is Ventoy's 32 MiB service partition. FLASHBOOT is about 512 MiB FAT32 and contains the kernel, initrd, boot files and settings. Do not remove service files or use Ventoy's standard Update action, which may replace FlashOS configuration.

Before reinstalling, copy your ISO files, backups, recovered files, reports, `flashos/settings.json`, `flashos/terminal.json`, the complete `flashos/network` directory and `flashos/applications/store.ext4` from the respective partitions to another drive. The EXE's Install action erases them. Remove the drive after a normal shutdown.


### Interrupted storage preparation

Preparing 2, 4 or 8 GiB includes allocating space, creating ext4, extracting and
verifying the runtime, and flushing writes. A slow USB drive can take several
minutes. Keep it connected and follow the current stage. Failure reports are
saved to `flashos/reports` on FLASHBOOT.

After interruption, the store offers verification or archiving of the unfinished
image. Verification completes only a fully written runtime. If the file is
incomplete or does not contain ext4, choose to archive the unfinished image and
create a new one, confirm preservation, then choose the size again. The old file
remains under `flashos/applications/incomplete` and still uses space. A completed
store containing installed applications is never automatically replaced.


### Application window controls

Alt+Tab switches forward between windows; Alt+Shift+Tab switches backward.
Alt+F10 maximizes or restores a window. Alt+F7 starts moving with the arrow keys;
Alt+F8 starts resizing. Enter confirms, Escape cancels the change. Alt+F4 closes
the active window; respond to the application’s save prompt if necessary.
Ctrl+Alt+Escape ends the external session and returns to FlashOS. The spoken help
also lists these shortcuts.

## Spoken system terminal

![Bash terminal with speech controls](../images/terminal.png)

Open Tools → the spoken terminal. It runs full Bash in the live system as root, with history, completion and diagnostic utilities. It needs neither internet nor application-store preparation. The graphical display is required; console fallback retains the existing spoken tools.

Typed characters are spoken by default. The shell prompt — the text before a command — is read once when the window opens. Automatic reading then speaks new output; the submitted command is announced separately after Enter if enabled. Character echo, submitted-command speech and output reading have independent controls. Editing a command should not repeat the entire prompt.

`pwd` shows the current directory; plain `cd` returns to the home directory. If a system service supplies no HOME, startup restores it from the account. The prompt uses `~` only for that home directory; with the standard account environment, `/run` remains `/run`.

Tab completes names; Up/Down selects command history. Ctrl+C interrupts a program and Ctrl+D sends end of input. Application keys keep their normal meaning.

| Shortcut | Action |
| --- | --- |
| Ctrl+Shift+P | Terminal preferences |
| Ctrl+Shift+E | Cycle character, word, both and disabled input echo |
| Ctrl+Shift+A | Toggle new-output reading |
| Ctrl+Shift+Space or Ctrl+Shift+F7 | Stop speech and clear its queue |
| Ctrl+Shift+R / Ctrl+Shift+L | Read visible screen / cursor line |
| Ctrl+Shift+Up/Down | Previous / next line of the output-history snapshot |
| Ctrl+Shift+Home / Ctrl+Shift+End | First snapshot line / refresh and read the last line |
| Shift+PageUp / Shift+PageDown | Scroll the terminal |
| Ctrl+Tab, then Tab | Focus and navigate toolbar buttons |
| Escape | Return focus to the terminal or cancel a dialog |
| Ctrl+Shift+C / Ctrl+Shift+V | Copy / paste |
| Shift+Insert | Paste the selection |
| Ctrl+plus / Ctrl+minus | Change font size |
| Ctrl+Shift+F6 | Next audio output |
| Ctrl+Shift+F1 | Help |
| Ctrl+Shift+Q | Request closure with confirmation |

After Stop speech, automatic output stays stopped. Character echo continues, but typing alone does not clear the stopped state. Submitting a command with Enter, explicitly reading or resuming permits output again if automatic reading is enabled. Paused output reading discards the stale backlog. Reviewing a history snapshot does not move Bash's editing cursor.

### Terminal preferences

![Terminal preferences showing character echo, prompt, punctuation, speech queue, font and reading checkboxes](../images/terminal-settings.png)

Choose «Настройки терминала» or press Ctrl+Shift+P. Tab/Shift+Tab move among spoken fields, arrows change selections and Space toggles checkboxes. «Сохранить» applies and saves; «Отмена» retains previous values. «Стандартные» fills the dialog with defaults, which must still be saved.

| Preferences | Choices |
| --- | --- |
| Input echo | Characters, words, both, off |
| Shell prompt | Once per window, after each command, off |
| Command/output punctuation | Voice default, main symbols, all symbols |
| Editing speech | Separate command-history, cursor character, deletion and Tab-completion controls |
| Speech while typing | Interrupt the current utterance; announce capital letters |
| Output and command reading | Separate new-output and submitted-command controls |
| Speech queue | 20, 80 or 200 chunks; 200, 800 or 1600 characters per chunk |
| Text | Font size from 9 to 28; 1000, 5000 or 20000 scrollback rows |
| Text cursor | Block, I-beam or underline; blinking |
| Other controls | Scroll on output, audible bell, middle-button paste, opening hint and shell-exit announcement |

Preferences persist in `flashos/terminal.json` on FLASHBOOT, separately from `flashos/settings.json`; no application store is required. The voice, rate and volume come from FlashOS speech settings when opening the window. Input-echo, automatic-reading and quick font-size shortcuts also save their values. Save failures are spoken. Keep the USB connected until normal shutdown.

Direct key echo applies while editing a Bash command. Input inside external programs is not intercepted; their text is available through output and screen review. Hidden program input is not spoken. Secrets entered as visible command text remain visible: disable input echo, submitted-command speech and automatic reading before entering them. Bash history and session metadata stay in RAM and are absent from the settings file; applications may save their own files. A bounded queue announces overflow; retained terminal text remains available for review. Use `less` or redirect to a chosen file for large logs.

Multiline paste and closing an active session require confirmation. Closing the production session stops child processes. Store applications still use a separate user; configure their Orca speech in Utilities.
