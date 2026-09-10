# Компоненты выпуска

[English](../en/components.md) · [Главная](../../README.md)

FlashOS 2026.09.09.3; установщик 1.0.2. Точные версии всех Linux-пакетов находятся в [packages.tsv](../../metadata/packages.tsv), соответствие исходным пакетам — в [debian-packages.json](../../metadata/debian-packages.json).

| Компонент | Версия | Назначение |
| --- | --- | --- |
| Debian | 13.6, trixie и backports | Основа live-системы |
| Загружаемое ядро Linux | 7.1.8-1~bpo13+1 | Драйверы и работа оборудования |
| Ventoy | 1.1.17 | BIOS/UEFI и запуск ISO |
| RHVoice | 1.18.4 | Речь меню |
| eSpeak NG | 1.52.0+dfsg-5 | Резервная речь |
| SOF firmware | 2025.12.2 | Поддерживаемые звуковые DSP |
| smartmontools | 7.5-2~bpo13+1 | SMART и самотесты |
| nvme-cli | 2.13-2 | Диагностика NVMe |
| memtester | 4.7.1-1 | Проверка выделенной RAM |
| stress-ng | 0.19.02-1 | Нагрузочные проверки |
| Partclone | 0.3.36+repack-1 | Образы разделов |
| TestDisk / PhotoRec | 7.2-0.1 | Поиск файлов; меню использует PhotoRec |
| Python Windows | 3.12.10 | Встроенная среда установщика |
| Qt / PySide6 | 6.11.2 | Интерфейс установщика |
| Nuitka | 4.2.1 | Сборка EXE |

Голоса: Елена 4.3, Ирина 4.1, Анна 4.1, Александр 4.2, Юрий 4.0. Ревизии RHVoice и подмодулей записаны в [rhvoice-sources.json](../../metadata/rhvoice-sources.json). Лицензии моделей отличаются; см. [THIRD-PARTY](../../THIRD-PARTY.md).

В Linux также установлены инструменты работы с файлами и разделами, сетевые утилиты, NetworkManager, ALSA/PipeWire, Xorg/Tk и прошивки для ряда Intel, AMD, Realtek, Atheros, Broadcom, MediaTek и других устройств. Это набор драйверов Linux, а не универсальный пакет драйверов для Windows. Работа конкретного устройства определяется его идентификатором и поддержкой в ядре и firmware.
