# Изменения / Change log

## Установщик / Installer 1.0.2 — 2026-09-10

Исправлены чтение журнала и справки стрелками через настоящий текстовый курсор Qt,
Enter/Space, сохранение фокуса при обновлении устройств и окончании записи.
Добавлены оглавление справки, снимок состояния по F6, оставшийся процент этапа,
отдельные скорости подготовки/записи/чтения и индикация ожидания сброса кеша.
Список EXE разделяет его Windows-библиотеки и встроенный Linux-образ.
Проверка USB использует выборочную распаковку эталонов; запись маленьких файлов
не повторяет тяжёлый опрос Windows и аппаратный сброс кеша для каждого файла.
Повторные операции блокируются до завершения потока и очистки.

Fix keyboard text/caret accessibility, Enter/Space activation and focus stability.
Add help contents, F6 status snapshots, remaining stage percentage and measured
preparation/write/read speeds. Reduce repeated storage queries and per-file
hardware flushes; keep a volume flush before verification. Verify only extracts
the needed boot references. The embedded Linux system remains 2026.09.09.3.

## 2026.09.09.3 — первый публичный выпуск / first public release

FlashOS 2026.09.09.3, установщик / installer 1.0.1.

Говорящая система с поиском ISO при загрузке, графическим и текстовым меню, сохранением голоса, диагностикой, Partclone, ddrescue и PhotoRec. Установщик Qt 6/Nuitka поставляется одним EXE с выбором USB, установкой, проверкой, форматированием, прогрессом и журналом.

Для публикации подготовлены русская и английская документация, MIT для собственного кода, отдельные сторонние лицензии, версии пакетов и архивы исходников. Установщик 1.0.1 обновляет упаковку документации и лицензий после локальной версии 1.0.0; модуль записи, ядро и инструменты сохранены. В системе .3 удалены идентификатор, сетевой файл и журналы сборочного компьютера. Частные аппаратные отчёты и тестовые ISO в релиз не входят.

Talking ISO selection with a boot-time rescan, graphical/text menus, persistent voice settings, diagnostics, Partclone, ddrescue and PhotoRec. The Qt 6/Nuitka installer is one EXE with USB selection, installation, verification, formatting, progress and logs.

Publication adds Russian/English documentation, MIT for original code, separate third-party notices, package versions and source archives. Installer 1.0.1 refreshes documentation/license packaging after the local 1.0.0 build; the disk writer, kernel and tools are retained. System .3 removes build-host machine IDs, resolver configuration and logs. Private hardware reports and test ISOs are excluded.

Known limits / Ограничения: [русский](docs/ru/troubleshooting.md), [English](docs/en/troubleshooting.md).
