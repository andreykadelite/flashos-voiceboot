# Изменения / Change log

Выпуск 2026.10.13.2 с установщиком 1.0.8 заменяет опубликованный
2026.09.12.1 / 1.0.4. В него вошли магазин Debian с сохранением приложений
на флешке, озвучка внешних окон через Orca, сетевые профили, настройки
доступности и обновления аппаратной поддержки. Ниже сохранены подробности
каждого этапа разработки. [Обзор выпуска](docs/ru/release-2026.10.13.md).

Release 2026.10.13.2 with installer 1.0.8 replaces published 2026.09.12.1 / 1.0.4.
It includes the persistent Debian store, Orca for external windows, saved
network profiles, accessibility settings and hardware support updates.
The development stages below retain the detailed history.
[Release overview](docs/en/release-2026.10.13.md).

## FlashOS 2026.10.13.2 · Установщик / Installer 1.0.8 — 2026-10-13

Длинные вводные сообщения магазина, настроек и других разделов можно
прервать F7, Enter или пробелом. Пропуск речи не выбирает действие и не
принимает подтверждение. F7 останавливает текущую речь FlashOS; навигация
и управление заданиями продолжают работать. Запуск ISO и приложения
больше не ждёт окончания произнесения его названия.

Добавлены настройки экранного диктора Orca в «Утилитах»: следование речи
FlashOS или независимый голос, скорость, высота, громкость, прослушивание
настоящим синтезатором и сброс только речи. Стандартный голос — Елена RHVoice;
меню и приложения используют те же пять голосов RHVoice и русский eSpeak NG.
Настройки сохраняются в штатном профиле Orca на флешке. Точный прежний
стандартный профиль мигрирует; пользовательские изменения, остальные
профили, произношения, брайлевские настройки и клавиши сохраняются.
Прежние хранилища получают общие голоса только для чтения и библиотеки
в RAM, без замены пользовательских библиотек на USB. Escape останавливает
пробный пример, Control — текущую речь Orca в приложении.
Учтена медленная подготовка RHVoice при первом запуске. Возврат по
Ctrl Alt Escape работает и во время подготовки речевой среды.
Исправлено завершение Speech Dispatcher по SIGPIPE при подготовке
модулей: настройка сигнала применяется только к речевому серверу.

Сборка включает сохраняемые настройки сети и восстановление подключения
магазина, описанные в предыдущем этапе ниже. Номера системы и установщика
обновлены до 2026.10.13.2 и 1.0.8; результаты сборки и испытаний публикуются
в отдельных отчётах о проверках.

Long introductory announcements in the store, settings and other sections
can be interrupted with F7, Enter or Space. Skipping speech neither activates
an action nor accepts a confirmation. F7 stops current FlashOS speech while
navigation and job controls remain available. ISO and application launch no
longer wait for the spoken name to finish.

Add Utilities settings for Orca: follow FlashOS speech or use an independent
voice, rate, pitch and volume, hear an actual synthesizer preview, and reset
speech only. Elena RHVoice is the default; the menu and applications use the
same five RHVoice voices and Russian eSpeak NG. Preferences persist in the
native Orca profile on USB. Migrate only the exact old standard profile;
retain user changes, other profiles, pronunciations, braille preferences and
key bindings. Older stores use shared voices read-only and libraries in RAM,
preserving user libraries on USB. Escape stops the preview; Control interrupts
current Orca speech in an application.
Allow for slow RHVoice preparation on first launch. Ctrl Alt Escape also
returns to FlashOS while the speech environment is still starting.
Fix Speech Dispatcher exiting on SIGPIPE during module preparation;
the signal setting is scoped to the speech server.

Include the persistent network settings and store connection recovery
described in the preceding development stage below. Update system and
installer versions to 2026.10.13.2 and 1.0.8; build and test results are recorded
in separate verification reports.

## Предыдущий этап разработки / Previous development stage — настройки сети, 2026-10-13

Добавлены сохраняемые на FLASHBOOT профили Ethernet и Wi-Fi: IPv4 DHCP,
IPv6 SLAAC/DHCPv6, статические адреса, шлюзы, отдельные DNS, домены поиска,
метрики и дополнительные маршруты. Профили привязаны к компьютеру и
постоянному MAC адаптера; чужая статика автоматически не применяется.
Автоподключение включается явно, для другого ПК доступны отдельная копия
и ручной переносимый Wi-Fi только с автоматическим IP.

Пароли Wi-Fi и прокси сохраняются в AES-256-GCM-хранилище с общим паролем
и scrypt. Ручное применение профиля использует checkpoint NetworkManager
и подтверждение до 60 секунд с возвратом прежней сети при отказе или сбое.
Прокси передаётся APT и новым приложениям, поддерживающим переменные среды,
через файлы RAM; PAC/WPAD и редактор корпоративного 802.1X не включены.
Wi-Fi поддерживает открытые сети и WPA2/WPA3 Personal.

При ошибке соединения магазин предлагает перейти в сетевые инструменты;
возвращение сохраняет вкладку, поиск и источник, а повтор загрузки выполняется
явно. Установка после ошибки загрузки заново проверяет план. На этом этапе
Windows EXE и пользовательский ZIP для этих изменений не пересобирались.

Add persistent FLASHBOOT Ethernet and Wi-Fi profiles for DHCP/SLAAC, static
IPv4/IPv6, gateways, DNS/search domains, route metrics and extra routes.
Match profiles to machine identity and permanent adapter MAC; require explicit
autoconnect and separate rebinding for another PC. Portable automatic-IP Wi-Fi
can be used manually. Encrypt Wi-Fi/proxy secrets using AES-256-GCM with a
shared vault password and scrypt. Use NetworkManager checkpoints with up to
60 seconds for manual connection confirmation and rollback. Pass proxies to
APT and new applications honoring proxy environment variables through RAM
files. Support open and WPA2/WPA3 Personal Wi-Fi; exclude PAC/WPAD and corporate
802.1X editing. Offer network tools after store connection failure, preserve
navigation context and require an explicit retry; rebuild an installation plan
after download failure. At this stage, the Windows EXE and user system ZIP were not rebuilt.

## FlashOS 2026.10.13.1 · Установщик / Installer 1.0.7 — 2026-10-13

Исправлена подготовка постоянного хранилища приложений на медленных USB:
появились этапы и проценты выделения места и распаковки, увеличено время
ожидания записи, ошибки носителя больше не скрываются повторной попыткой записи.
Перед завершением проверяются записанные файлы среды; незавершённые файлы
сохраняются для проверки или переноса в архив. Добавлена подробная диагностика этапа сбоя.

Обновлены оформление меню, вкладок и отчётов, перенос длинных строк и подсказки
клавиш. Вкладка обновлений учитывает выбранный источник Debian. Действия в
карточке приложения соответствуют его установленному состоянию.
Сохранены проверки установщика для больших объёмов данных и запуск EXE с
небольшой загрузочной частью Windows и проверяемым встроенным архивом системы.

Improve persistent application storage preparation on slow USB media with staged
progress, longer write timeouts, unmasked storage errors and verification of the
extracted runtime before completion. Preserve interrupted preparation files and
report the failing stage. Refresh menu, tab and report layout, wrapping and key
hints. Apply the selected Debian source to updates and show package actions for
the installed state. Retain 64-bit installer byte counters and the small Windows
executable image with its verified appended system archive.

## FlashOS 2026.10.12.2 · Установщик / Installer 1.0.6 — 2026-10-12

Добавлен говорящий магазин Debian: вкладки доступных, установленных пакетов
и обновлений, поиск, выбор официального источника, чтение полного плана
зависимостей и места перед подтверждением. Приложения и настройки сохраняются
в отдельном ext4-файле на флешке. Внешняя сессия работает с Orca от UID 1000;
Ctrl+Alt+Escape закрывает её и возвращает в FlashOS. Доступность зависит от приложения. Добавлен пункт «Утилиты» с говорящими настройками GSettings: масштаб текста, контрастность, анимация, указатель и восстановление доступности для диктора. Настройки сохраняются на USB; подтверждаемый сброс сохраняет другие ключи и файлы Orca. В среду включён `gsettings`.

Add a talking Debian store with available, installed and update tabs, search,
official source selection and dependency/space plan review before confirmation.
Applications and settings persist in a separate USB ext4 file. External sessions
run with Orca as UID 1000; Ctrl+Alt+Escape closes them and returns to FlashOS.
Application screen-reader support determines accessibility. Add main-menu Utilities with spoken GSettings controls for text scale, contrast, animations, cursor size and restoring reader accessibility. Preferences persist on USB; confirmed reset preserves other keys and Orca files. Include the `gsettings` command in the application runtime.

## FlashOS 2026.10.12.1 · Установщик / Installer 1.0.5 — 2026-10-12

Расширена аппаратная поддержка загрузочной системы: прошивки для звука,
Ethernet, Wi-Fi и графики, а также микрокод процессоров. Добавлены проверки
прошивок и микрокода при сборке образа. Установщик содержит обновлённую систему.

Expand live-system hardware support with audio, Ethernet, Wi-Fi and graphics
firmware and CPU microcode. Add firmware and microcode checks during image
builds. The installer embeds the updated system.

## FlashOS 2026.09.12.1 · Установщик / Installer 1.0.4 — 2026-09-12

Если служебный файл выбора ISO потерян или повреждён, FlashOS восстанавливает
его и открывает меню. Перед этим система проверяет, что раздел принадлежит
загрузочной флешке. Исправный файл очищается без замены; сами ISO не меняются.
При сборке EXE проверяются версия и SHA256 системы, а исходники сравниваются
с содержимым squashfs. Это не позволяет упаковать старую систему после
исправления кода. Проверены Debian и Win11PE с Secure Boot и без него;
для проверки Secure Boot использовались заранее зарегистрированные ключи.

Recover missing or damaged one-shot ISO selection state after validating the USB
boot partition. Preserve the healthy file allocation and strict action-time
checks. User ISO files remain untouched. Packaging verifies the live image hash,
version and source hashes against the squashfs verification report.

## Установщик / Installer 1.0.3 — 2026-09-12

Запрос прав администратора Windows перенесён к запуску EXE, до открытия меню.
Нажатие «Установить» сразу открывает подтверждение для выбранной флешки;
окно не перезапускается и сохраняет параметры. Запуск из исходников также
получает права до создания интерфейса. Добавлены регрессионные тесты запуска,
отмены, первого нажатия и проверка манифестов обоих EXE при сборке.

Request administrator rights before opening the menu. Install opens the erase
confirmation in the same window and preserves the selected drive and options.
Source launches follow the same startup order. Add startup and action regression
tests and verify the manifests of the launcher and embedded executable at build time.
The disk writer and embedded FlashOS system remain unchanged.

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
