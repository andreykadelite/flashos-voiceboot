# Участие в разработке / Contributing

## Русский

Сообщайте об ошибках через [Issues](https://github.com/andreykadelite/flashos-voiceboot/issues). Укажите версию релиза, режим BIOS/UEFI и Secure Boot, модель оборудования, шаги воспроизведения и ожидаемый результат. Для проблем доступности нужны также скринридер, его версия, нажатые клавиши и то, что было озвучено. Не публикуйте серийные номера, ключи, пароли, личные пути и полный аппаратный отчёт без проверки.

Предлагая новый инструмент, опишите задачу пользователя и способ работы без зрения. Укажите лицензию, поддерживаемые устройства, какие данные он изменяет, как остановить операцию и как проверить результат. Простого добавления пакета недостаточно: действие должно иметь доступное управление, понятный прогресс и ошибки.

Для изменений создайте ветку и pull request. Объясните проблему, новое поведение и проверки. Изменения записи на диск проверяйте сначала на файлах-образах или виртуальных дисках. Обычные тесты и CI не должны форматировать физические устройства. Инструкции находятся в [документе о сборке](docs/ru/build.md).

Сохраняйте авторское уведомление и LICENSE в производных версиях. Просьба также сохранять имя Зотова Андрея Анатольевича в справке и документации, отмечать собственные изменения и не выдавать изменённую сборку за официальный выпуск. Не добавляйте коммерческие голоса или прошивки без права распространения. Изменения собственного кода предлагаются на условиях MIT; сторонние лицензии сохраняются отдельно.

Контакты: [@zotovsmol](https://t.me/zotovsmol), [andreykadelite@ya.ru](mailto:andreykadelite@ya.ru).

## English

Use [Issues](https://github.com/andreykadelite/flashos-voiceboot/issues) for bugs and feature requests. Include the release version, BIOS/UEFI and Secure Boot state, hardware model, reproduction steps and expected result. Accessibility reports should include the screen reader/version, keys pressed and spoken output. Review logs for serial numbers, keys, passwords and private paths before posting.

For a new tool, explain the user's task and how it can be performed without sight. State its license, hardware support, writes, cancellation behaviour and result verification. Adding a package alone is insufficient: the operation needs accessible controls, useful progress and clear errors.

Submit a branch and pull request describing the problem, changed behaviour and checks. Test disk writers on image files or virtual disks first. Routine tests and CI must never format physical devices. See [build instructions](docs/en/build.md).

Keep the copyright and LICENSE notice in derivatives. Please retain Andrey Anatolyevich Zotov's credit in help and documentation, mark your changes and distinguish forks from official releases. Do not submit proprietary voices or firmware without redistribution permission. Original-code contributions use MIT; third-party licenses remain separate.
