# Сторонние компоненты / Third-party components

## Русский

MIT в корне репозитория относится к собственному коду и документации FlashOS. Она не заменяет условия программ, голосов и прошивок внутри EXE и загрузочной среды. Исходные уведомления сохранены в `licenses/`, архивах исходников и `/usr/share/doc` live-системы.

В релизах загружается только установщик EXE. Исходный код FlashOS хранится в этом репозитории; [получение исходников компонентов](docs/ru/sources.md) описывает точные версии, URL и ревизии сторонних проектов. [Реестр архивов](metadata/source-archives.json) сохраняет состав и SHA256 локальных комплектов, собранных при подготовке системы. Он не является списком доступных файлов релиза. Архивы прежнего релиза сняты с публикации 27 октября 2026 года; их локальные копии сохранены.

Исторические комплекты исходников:

- `FlashOS-2026.09.09.3-debian-sources.zip`: точные исходные пакеты Debian с `.dsc`, исходными архивами и патчами; список и SHA256 — [debian-sources.json](metadata/debian-sources.json).
- `FlashOS-2026.09.09.3-upstream-sources.zip`: RHVoice с использованными подмодулями, Ventoy, Qt base, PySide/Shiboken, CPython, Nuitka и дополнительные источники/уведомления.
- `FlashOS-2026.10.12.1-hardware-sources.zip`: точные исходные пакеты новых и обновлённых компонентов Debian, включая ядро 7.2.6, firmware, Mesa и микрокод, а также оригинальный upstream-архив SOF 2026.09.2 с лицензиями и SHA256. [source-archives.json](metadata/source-archives.json) перечисляет содержимое каждого локального архива.
- `FlashOS-2026.10.12.2-applications-sources.zip`: недостающие точные исходные пакеты для live-системы и начальной Debian-среды приложений, в том числе Openbox, Orca, GNOME Terminal и их библиотек. Версии среды: [app-runtime-packages.json](metadata/app-runtime-packages.json). Пакеты, которые пользователь скачает позднее, имеют собственные лицензии и источники Debian.
- Исходники самого проекта: этот репозиторий и стандартные архивы исходников тега. [Инструкция по сборке](docs/ru/build.md) также объясняет замену Qt/PySide6.

Локальный комплект `FlashOS-2026.10.13.1-network-sources.zip` содержит точные исходники `python-cryptography` 43.0.0-3+deb13u1, `python-bcrypt` 4.2.0-2.1 и `python-cffi` 1.17.1-3. Это зависимости защищённого хранилища сетевых паролей. [Реестр архивов](metadata/source-archives.json) содержит состав, размеры и SHA256; URL файлов находятся в [debian-sources.json](metadata/debian-sources.json), исходные лицензии — в `/usr/share/doc` live-системы.

Сборка 2026.10.27.1 добавляет `xinput` 1.6.4-1. Точные исходные файлы, URL и SHA256 находятся в [debian-sources.json](metadata/debian-sources.json); локальная упаковка `FlashOS-2026.10.27.1-accessibility-sources.zip` записана в том же реестре архивов. Уведомления пакета находятся в `/usr/share/doc/xinput/copyright` live-системы. ZIP не загружается в релиз.

Перед публикацией архивы аппаратных компонентов и приложений заново упакованы из сохранённых файлов. SHA256 каждого индексированного файла совпал с прежним реестром. Содержимое исходников сохранено; контрольные суммы самих ZIP изменились. В реестре записаны новые суммы и прежние значения. Снимки пакетов внутри этих архивов относятся к моменту первоначального сбора; текущий состав системы указан в `metadata` этого тега.

Среда приложений FlashOS 2026.10.13.2 содержит 455 Debian-пакетов; к прежнему seed добавлен только `espeak-ng` версии `1.52.0+dfsg-5`, без изменения остальных версий. Его точные `.dsc`, исходный архив и патчи Debian уже перечислены в [debian-sources.json](metadata/debian-sources.json) и существующем [реестре исходных архивов](metadata/source-archives.json); новый архив этой версии не требуется. Уведомления eSpeak NG сохраняются в `/usr/share/doc/espeak-ng/copyright` среды приложений. RHVoice 1.18.4, модуль Speech Dispatcher и пять моделей копируются из live-системы без изменения их содержимого и лицензий; уведомления RHVoice также находятся в `/usr/share/doc/flashos/rhvoice` начальной среды. Применяются тот же комплект исходников RHVoice и ограничения моделей, описанные ниже.

При дальнейшем распространении сохраняйте соответствующие исходники, лицензии и уведомления. Некоторые прошивки разрешают распространение только неизменённых бинарных файлов; наличие их в системе не означает разрешения менять их или лицензировать по MIT.

### Основные компоненты

| Компонент | Условия и источник |
| --- | --- |
| Debian и ядро Linux | Лицензии отдельных пакетов; ядро GPL-2.0-only с применимыми исключениями. Версии: [packages.tsv](metadata/packages.tsv), соответствие исходникам: [debian-packages.json](metadata/debian-packages.json). Авторские уведомления — `/usr/share/doc/*/copyright` |
| Ventoy 1.1.17 | Проект longpanda, GPL v3; встроенные сторонние части сохраняют собственные лицензии. [Исходники](https://github.com/ventoy/Ventoy/tree/v1.1.17). FlashOS меняет GRUB-конфигурацию, не подписанные EFI-файлы |
| Qt / PySide6 / Shiboken 6.11.2 | The Qt Company и участники, используемые библиотеки LGPL-3.0. [Полный текст](licenses/LGPL-3.0-only.txt), [GPL v3](licenses/GPL-3.0-only.txt), [обязанности LGPL](https://www.qt.io/faq/qt-open-source-licensing). Библиотеки динамические; разрешены замена, пересборка и исследование для отладки таких изменений |
| Python 3.12.10 | Python Software Foundation и участники; [лицензии Python и встроенных компонентов](licenses/Python-3.12.10.txt) |
| Nuitka 4.2.1 | Kay Hayen и участники; AGPL v3 с Nuitka Runtime Library Exception. Исключение разрешает независимому приложению использовать другую лицензию; исходники и тексты приложены |
| SOF firmware 2026.09.2 | [Официальный выпуск](https://github.com/thesofproject/sof-bin/releases/tag/v2026.09.2); условия файлов `LICENCE.*` сохранены в архиве и live-системе |
| Microsoft Visual C++ Runtime | Неизменённые DLL, поставленные с CPython/PySide6; отдельные [условия Microsoft](https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/) и [перечень распространяемых файлов](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution). Они не лицензируются по MIT или GPL |

### RHVoice и голоса

RHVoice создан Ольгой Яковлевой и участниками проекта. Используется тег 1.18.4, commit `fa9dd196fd2dac3b0bf089a2d80fc8477c2380e3`. Основная библиотека — LGPL 2.1 или новее; сборка с MAGE — GPL 3 или новее. [Исходные условия RHVoice](licenses/rhvoice/License.md), [ревизии подмодулей](metadata/rhvoice-sources.json).

| Данные | Версия | Лицензия и происхождение сведений |
| --- | --- | --- |
| Елена / Elena | 4.3 | GPL v3 по [README модели](licenses/rhvoice/Elena-README.md); голос Елены Соболевой |
| Анна / Anna | 4.1 | GPL-2.0-or-later согласно разделу `data/*` copyright пакета Debian RHVoice; см. замечание ниже |
| Ирина / Irina | 4.1 | То же основание Debian `data/*` |
| Александр / Aleksandr | 4.2 | То же основание Debian `data/*` |
| Russian / English | 2.17 | То же основание Debian `data/*` |
| Юрий / Yuriy | 4.0 | **CC BY-NC-ND 4.0**; [README модели](licenses/rhvoice/Yuriy-README.md) и [полный текст](licenses/rhvoice/by-nc-nd-4.0.txt) |

В отдельных репозиториях Анны, Ирины, Александра и языков отсутствует собственный файл лицензии. Здесь приведена атрибуция из [copyright Debian RHVoice](licenses/rhvoice/Debian-copyright-1.14.0-2.txt), а не утверждение, что каждый upstream-репозиторий содержит явное уведомление. Неоднозначность оформления обсуждается в [RHVoice #885](https://github.com/RHVoice/RHVoice/issues/885). Модели включены без изменений; опубликованы их точные ревизии и доступная исходная форма. Записи дикторов и отсутствующие у upstream материалы обучения в состав не входят. Перед изменением или отдельным переизданием моделей следует проверить их происхождение и актуальные условия правообладателей.

Юрий создан RHVoice Lab в память о дикторе Юрии Николаевиче Заборовском. В README указаны Артём Плаксин — руководство, Сергей Паршаков — монтаж записей, Денис Шишкин — обработка звука, Бека Гозалишвили — обучение. Модель скопирована без изменений. CC BY-NC-ND разрешает предусмотренное лицензией некоммерческое распространение с атрибуцией, но не коммерческое использование и не распространение изменённой модели. Настройка скорости воспроизведения не означает изменения файла модели. Для иных прав обращайтесь к правообладателю: [условия CC](https://creativecommons.org/licenses/by-nc-nd/4.0/).

**Полный комплект с Юрием нельзя объявлять свободным от ограничений коммерческого использования.** MIT для FlashOS не снимает ограничений модели. Милена и Катя Nuance не включены; Юрий RHVoice не является Yuri Nuance.

## Происхождение старых бинарников Ventoy

В архиве есть исходники BusyBox 1.27.1, 1.30.0, 1.31.0 и 1.32.0, опубликованные конфигурации и дополнительные зависимости из списка Ventoy. Для старых статических апплетов BusyBox.net конфигурация указывает компилятор `i486-linux-uclibc-`, но не точную ревизию встроенной uClibc. Приложенная uClibc 0.9.30.1 относится к инструментарию mini-native, указанному Ventoy; её соответствие каждому старому апплету отдельно не подтверждено. Это ограничение проверки происхождения, а не обещание полной воспроизводимости сторонних бинарников. [Список источников](metadata/ventoy-extra-sources.json) содержит ссылки и контрольные суммы.

Терминал live-системы использует неизменённые пакеты Debian GTK, VTE,
PyGObject/GI и ATK. Их уведомления находятся в `/usr/share/doc/*/copyright`,
точные версии и исходники — в `metadata/debian-packages.json`,
`metadata/debian-sources.json` и `metadata/source-archives.json`.

## English

The live terminal uses unmodified Debian GTK, VTE, PyGObject/GI and ATK
packages. Their notices remain in `/usr/share/doc/*/copyright`; exact binary
and source versions are recorded in `metadata/debian-packages.json`,
`metadata/debian-sources.json` and `metadata/source-archives.json`.

The root MIT license covers FlashOS's original code and documentation. Bundled operating-system packages, bootloaders, libraries, voices and firmware retain their own terms. Notices are preserved in `licenses/`, source archives and the live system's `/usr/share/doc`.

Only the installer EXE is uploaded to releases. FlashOS source code is in this repository; [obtaining component sources](docs/en/sources.md) lists exact versions, URLs and upstream revisions. [Archive inventory](metadata/source-archives.json) records the contents and SHA256 of local source bundles; it is not a list of downloadable release assets. The previous release's uploaded archives were removed on 27 October 2026; local copies were retained.

The historical local bundles contain exact Debian source packages (`FlashOS-2026.09.09.3-debian-sources.zip`), upstream source trees (`FlashOS-2026.09.09.3-upstream-sources.zip`), updated hardware packages and SOF 2026.09.2 (`FlashOS-2026.10.12.1-hardware-sources.zip`), and additional live/application-runtime sources (`FlashOS-2026.10.12.2-applications-sources.zip`). [Runtime source mapping](metadata/app-runtime-packages.json) lists versions. Packages downloaded later retain their own licenses and Debian source availability. Keep matching source, licenses and notices available when redistributing. See [build instructions](docs/en/build.md) for building and replacing Qt/PySide6.

Debian package licenses and copyrights are available in `/usr/share/doc/*/copyright`; [metadata](metadata/debian-packages.json) maps binaries to exact sources. Linux uses GPL-2.0-only with applicable exceptions. Ventoy 1.1.17 is longpanda's GPL v3 project with separately licensed embedded components. FlashOS modifies configuration while preserving signed EFI files. Qt/PySide6/Shiboken 6.11.2 use LGPL v3 for the included libraries; they are dynamically linked and may be replaced or rebuilt. Reverse engineering for debugging such library changes is permitted. Python uses PSF and its included component terms. Nuitka 4.2.1 uses AGPL v3 with the Runtime Library Exception for independent compiled applications. SOF and other firmware retain their original `LICENCE.*` terms. Unmodified Microsoft runtime DLLs retain Microsoft's terms; they are not relicensed under MIT/GPL.

The local bundle `FlashOS-2026.10.13.1-network-sources.zip` contains exact sources for `python-cryptography` 43.0.0-3+deb13u1, `python-bcrypt` 4.2.0-2.1 and `python-cffi` 1.17.1-3, the encrypted network-vault dependencies. The [archive inventory](metadata/source-archives.json) records contents, sizes and SHA256; file URLs are in [debian-sources.json](metadata/debian-sources.json). Original notices remain in the live system's `/usr/share/doc`.

Build 2026.10.27.1 adds `xinput` 1.6.4-1. Exact source files, URLs and SHA256 are in [debian-sources.json](metadata/debian-sources.json); the local `FlashOS-2026.10.27.1-accessibility-sources.zip` packaging is recorded in the archive inventory. Package notices remain in the live system's `/usr/share/doc/xinput/copyright`. The ZIP is not uploaded to the release.

The hardware and application source ZIPs were reassembled from retained files before publication. Every indexed member matched its previous SHA256. Source contents are unchanged; the ZIP container hashes changed. The inventory records the current hashes and their previous values. Package snapshots inside these archives describe the original collection stage; this tag's `metadata` records the current installed package set.

The FlashOS 2026.10.13.2 application runtime contains 455 Debian packages; its only addition to the previous seed is `espeak-ng` version `1.52.0+dfsg-5`, with other versions unchanged. Its exact `.dsc`, original source archive and Debian patches are already recorded in [debian-sources.json](metadata/debian-sources.json) and the existing [source-archive inventory](metadata/source-archives.json); this version needs no additional archive. eSpeak NG notices remain in the runtime's `/usr/share/doc/espeak-ng/copyright`. RHVoice 1.18.4, its Speech Dispatcher module and five voice models are copied from the live system without changing their contents or licenses. RHVoice notices also reside in the seed's `/usr/share/doc/flashos/rhvoice`. The same RHVoice source bundle and model restrictions described below apply.

RHVoice 1.18.4 is by Olga Yakovleva and contributors. Its library is LGPL 2.1 or later; the build with MAGE is GPL 3 or later. Elena 4.3's model README states GPL v3. Debian's RHVoice copyright assigns `data/*` GPL-2.0-or-later, covering Anna 4.1, Irina 4.1, Aleksandr 4.2 and Russian/English 2.17 unless separately overridden. Those split upstream repositories lack their own license files; this is Debian's provenance, not a claim of explicit notices in each repository. [Issue #885](https://github.com/RHVoice/RHVoice/issues/885) discusses that ambiguity. Exact unmodified model revisions and the available upstream form are supplied, not unpublished recordings or training materials.

**Yuriy 4.0 uses CC BY-NC-ND 4.0.** The model remains unmodified and credits Yuri Nikolaevich Zaborovsky, RHVoice Lab, Artem Plaksin, Sergey Parshakov, Denis Shishkin and Beqa Gozalishvili as described in its README. Noncommercial sharing with attribution is allowed under that license; commercial use and distribution of modified models are not. Obtain appropriate permission for uses outside its terms. The full bundle must not be advertised as MIT-only or unrestricted for commercial use. Nuance Milena and Katya are not included; RHVoice Yuriy is not Nuance Yuri.

The source archive includes BusyBox 1.27.1, 1.30.0, 1.31.0 and 1.32.0, published build configurations and Ventoy's listed dependencies. The old BusyBox.net static applet configurations name `i486-linux-uclibc-` but do not record the exact linked uClibc revision. The included uClibc 0.9.30.1 belongs to Ventoy's documented mini-native toolchain; its correspondence to every older applet has not been independently established. This is a provenance-verification limitation, not a claim that every third-party binary is fully reproducible. [Source inventory](metadata/ventoy-extra-sources.json).

Project author: Andrey Anatolyevich Zotov / Зотов Андрей Анатольевич. This credit does not replace the authorship of any third-party component.
