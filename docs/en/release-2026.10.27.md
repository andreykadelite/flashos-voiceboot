# FlashOS 2026.10.27.1 and installer 1.0.11

[Русский](../ru/release-2026.10.27.md) · [Home](../../README.en.md)

FlashOS is a bootable USB system with a spoken menu for OS installers,
PC diagnostics and recovery. This release fixes terminal speech and adds
mouse and touchpad preferences. The interface and spoken help remain Russian.

**[Download FlashOS-Setup.exe](https://github.com/andreykadelite/flashos-voiceboot/releases/download/v2026.10.27.1-installer-1.0.11/FlashOS-Setup.exe)** · [Release](https://github.com/andreykadelite/flashos-voiceboot/releases/tag/v2026.10.27.1-installer-1.0.11) · [Source code and documentation](https://github.com/andreykadelite/flashos-voiceboot/tree/v2026.10.27.1-installer-1.0.11)

## Terminal

Typed characters are spoken again. The Bash prompt is read once per window
by default; subsequent automatic speech reads new output. Stop speech keeps
character echo available, and Enter resumes output reading. Starting the
terminal from a service now restores the home directory and bare `cd`.

The toolbar's «Настройки терминала» or Ctrl+Shift+P opens 22 preferences:
character/word echo, command and history reading, punctuation, speech behaviour,
font, scrollback and text cursor. Preferences persist on FLASHBOOT in
`flashos/terminal.json`. Hidden input is not spoken, and commands and screen
contents are not saved in that file.

## Mouse and touchpad

Utilities → Accessibility — cursor, mouse and touchpad now offers 15 preferences.
Hide the pointer, choose its size and colour, or change speed, primary button
and scrolling. Supported touchpads also offer tapping, dragging and
protection while typing. Values persist under `pointer` in
`flashos/settings.json` on FLASHBOOT and apply when devices are connected,
within driver capabilities.

A click selects and reads a menu item; a double click opens it. Wheel and
touchpad scrolling move the selection. Back, Open, Help and Stop speech buttons
have been added. Ctrl+Alt+P in the menu restores pointer visibility.
Keyboard operation remains available.

[Keys and all preferences](guide.md) · [USB installation](installer.md)

The release contains only the EXE. Source code and documentation are available
at tag `v2026.10.27.1-installer-1.0.11`. Before reinstalling, copy the USB
drive's data and preferences elsewhere: installation deletes its contents.
