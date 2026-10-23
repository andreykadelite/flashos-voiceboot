# Сообщения об уязвимостях / Security reports

Если ошибка может привести к записи на неверный диск, выполнению постороннего кода или раскрытию данных, сообщите приватно на [andreykadelite@ya.ru](mailto:andreykadelite@ya.ru). Укажите версию, условия и минимальные шаги воспроизведения. Не присылайте пароли, ключи и образ личного диска. До исправления не публикуйте подробности эксплуатации в открытом Issue.

Проект пока не объявляет сроков поддержки или SLA. Отчёты принимаются для последнего релиза; исправление может потребовать новой сборки. Контрольная сумма выявляет отличие файла, но сама по себе не заменяет подпись издателя. Текущий EXE не подписан.

Для локальной сборки 2026.10.22.1 опубликован [аудит зависимостей и достижимости известных advisories](docs/ru/dependency-audit-2026-10-22.md). Он отдельно рассматривает native Qt и Debian cryptography; нулевой результат PyPI-сканера не означает отсутствие уязвимостей во всём комплекте.

For wrong-disk writes, code execution or data-exposure vulnerabilities, contact [andreykadelite@ya.ru](mailto:andreykadelite@ya.ru) privately with the version, conditions and minimal reproduction. Do not send passwords, keys or personal disk images. Avoid publishing exploit details in a public issue before a fix.

No support lifetime or SLA is promised. Reports are accepted for the latest release and fixes may require rebuilding. A checksum detects file differences; it does not replace publisher authentication. The current EXE is unsigned.
