# Проверка зависимостей, 22 октября 2026

Production-библиотеки установщика и Debian live не изменены. В CI обновлена только тестовая зависимость `cryptography==43.0.0` → `50.0.2` после чтения официального changelog и отдельной Linux-проверки. Известный путь эксплуатации перечисленных Qt/cryptography CVE через собственный код FlashOS не подтверждён. Это оценка достижимости конкретных API, а не утверждение об отсутствии уязвимостей во всём дистрибутиве и сторонних программах.

## Метод и свидетельства

`pip-audit 2.10.1` установлен в отдельную `build/audit-dependencies` venv, без установки в `build/installer/python312`. Сканирование использовало актуальный сервис PyPI. Сырые JSON могут содержать повторные записи одного advisory; ниже они не считаются отдельными CVE.

- `dependency-installer-pinned.json`: четыре закреплённые build-зависимости, **0 findings**.
- `dependency-installer-installed.json`: также проверены PySide6 Essentials/Addons/Shiboken 6.11.2, **0 PyPI findings**. Это не покрывает все native Qt CVE.
- Полный builder содержит Pillow 12.3.0: 24 advisory и pip 25.0.1: 6 уникальных advisory. Их нет в production imports и списке модулей существующего Nuitka report; это риски среды разработки при обработке недоверенных изображений/установке пакетов. Обновление builder вне production chain — отдельная работа. Пример: [Pillow GIF DoS](https://github.com/python-pillow/Pillow/security/advisories/GHSA-74r2-7ff9-mjj2), [PyPA pip traversal](https://github.com/pypa/advisory-database/blob/main/vulns/pip/PYSEC-2026-196.yaml).
- `dependency-ci-cryptography.json`: 7 уникальных advisory групп для PyPI 43.0.0; `dependency-cryptography-50.0.2.json`: **0 findings**.
- `cryptography-50.0.2-tests.log`: отдельная Linux venv `/tmp/flashos-crypto-audit-20261022`, Python 3.13.5, cryptography 50.0.2, **23/23 network_secrets tests passed**, без пропусков.
- `cryptography-vault-compatibility.json`: vault, созданный Debian 43.0.0, успешно прочитан 50.0.2; обратное направление также успешно. Использовались только временные тестовые данные.
- Финальный полный прогон с cryptography 50.0.2: **693 теста, OK, 32 ожидаемых пропуска Windows/Qt/PowerShell**. Локальный журнал: `build/audit-20261022/crypto50-linux-final.log`.

## Cryptography: достижимость

Собственный код импортирует только AESGCM/InvalidTag (`src/network_secrets.py:61`), передаёт обычные bytes, использует 12-байтовый nonce, проверяет ограничение файла около 1 МиБ. Производная ключа вычисляется `hashlib.scrypt`. Чтение/запись ciphertext: строки 288 и 359. Ни X.509, ни RSA/SECT, ни PKCS7/CMS через cryptography FlashOS не вызывает.

| Advisory для PyPI 43.0.0 | Условие | Достижимость в FlashOS |
|---|---|---|
| [CVE-2024-6119](https://github.com/pyca/cryptography/security/advisories/GHSA-h4gh-qq45-vh27), fixed 43.0.1 | OpenSSL certificate name checks, malformed otherName | Vault не проверяет сертификаты. Debian не использует OpenSSL из wheel. |
| [CVE-2024-12797](https://github.com/pyca/cryptography/security/advisories/GHSA-79v4-65xg-pq4g), fixed 44.0.1 | TLS/DTLS с явно включёнными Raw Public Keys | Vault не создаёт TLS/RPK-соединений. |
| [CVE-2026-34180](https://github.com/pyca/cryptography/security/advisories/GHSA-537c-gmf6-5ccf), fixed 48.0.1 | DER ASN.1 primitive >2 ГиБ на 64-bit Unix | Нет ASN.1 parser; vault ограничен около 1 МиБ. |
| [CVE-2026-26007](https://github.com/pyca/cryptography/security/advisories/GHSA-r6ph-v2qm-q3c2), fixed 46.0.5 | SECT ECDH/ECDSA и недоверенный публичный ключ | Нет асимметричной криптографии; Debian 43.0.0-3+deb13u1 уже исправлен. |
| [CVE-2026-34073](https://github.com/pyca/cryptography/security/advisories/GHSA-m959-cc7f-wv43), fixed 46.0.6 | X.509 wildcard peer-name constraints | X.509 verification не вызывается. |
| [CVE-2026-69248](https://github.com/pyca/cryptography/security/advisories/GHSA-m2h6-j472-rp4c), fixed 49.0.0 | X.509 wildcard permittedSubtrees | X.509 verification не вызывается. |
| [CVE-2026-69249](https://github.com/pyca/cryptography/security/advisories/GHSA-jwv3-5hgf-82ww), fixed 49.0.0 | Повторные self-signed certificates в chain builder | Certificate chain builder не вызывается. |

Live package manifest содержит `python3-cryptography 43.0.0-3+deb13u1`, связанный с системным OpenSSL; PyPI wheel findings автоматически переносить на Debian нельзя. [Debian tracker](https://security-tracker.debian.org/tracker/source-package/python-cryptography) показывает три оставшихся X.509 issue как minor/no-DSA. Исходный Docker Python также использует Debian 43.0.0-3+deb13u1; его пакет не менялся.

CVE-2026-39892 введён в 45.0.0, а CVE-2026-69247 — в 44.0.0, поэтому 43.0.0 ими не затронут. Кандидат 49.0.0 всё ещё имеет последний PKCS7 advisory (`dependency-cryptography-49.0.0.json`); выбран актуальный patch 50.0.2 с нулевым результатом сканера. [Официальный changelog](https://cryptography.io/en/50.0.2/changelog/) сохраняет используемые AESGCM encrypt/decrypt/InvalidTag API; новые encrypt_into/decrypt_into не используются. Удаления старых asymmetric/cipher API и изменения платформ не затрагивают Linux Python 3.13 CI. Production Debian обновлять этим wheel не предлагается.

## Native Qt 6.11.2: достижимость

Существующий Nuitka report содержит QtCore, QtGui, QtNetwork и QtWidgets; QtXml, QtQml/QtQuick, Qt5Compat и Qt VNC не включены. `installer/*.py` не вызывает QJsonDocument/QDom/QNetworkAccessManager/QML/VNC. JSON обрабатывается Python `json`; QTextBrowser получает справку из payload с проверкой SHA256 и не открывает внешние ссылки. QtNetwork присутствует как зависимость Qt, но сетевых запросов приложения через него нет.

| CVE | Условие | Достижимость |
|---|---|---|
| [CVE-2026-96432](https://www.qt.io/blog/security-advisory-cve-2026-96432) | Ответ HTTP/2 в QNetworkAccessManager; fixed 6.11.3 | Нет QNetworkAccessManager и HTTP-загрузок через Qt. |
| [CVE-2026-101201](https://www.qt.io/blog/security-advisory-cve-2026-101201) | Специальный JSON/binary JSON в Qt JSON APIs; fixed 6.11.3 | Входной JSON разбирает Python; Qt5Compat отсутствует. Нет подтверждённого маршрута недоверенных данных к Qt JSON API. |
| [CVE-2026-105923](https://wiki.qt.io/List_of_known_vulnerabilities_in_Qt_products#CVE-2026-105923:) | SVG через QML VectorImage; fixed 6.11.3/6.12.0 | QML/VectorImage отсутствуют. |
| [CVE-2026-79680](https://wiki.qt.io/List_of_known_vulnerabilities_in_Qt_products#CVE-2026-79680:) | Аутентификация Qt VNC Server; fixed 6.11.3 | Qt VNC Server отсутствует. |
| [CVE-2026-15037](https://wiki.qt.io/List_of_known_vulnerabilities_in_Qt_products#CVE-2026-15037) | QDom serialization недоверенных строк; fixed 6.12.0 | QtXml/QDom отсутствуют. |
| [CVE-2026-82292 vendor patch](https://download.qt.io/official_releases/qt/6.11/CVE-2026-82292-qtbase-6.11.diff) | NULL QDom nodes при разборе XML | QDom/QtXml отсутствуют. Точный affected range отдельно не устанавливался. |
| [CVE-2026-16762 vendor patch](https://download.qt.io/official_releases/qt/6.11/CVE-2026-16762-qtbase-6.11-0001.diff) | Квадратичная обработка XML attributes в QXmlStreamReader | Внешний XML в этот API не передаётся. Точный affected range отдельно не устанавливался. |

Другие проверенные недавние Qt advisories 8168/78253/79616/76151/19248/13326 исправлены уже в 6.11.2; 9499/6210 — в 6.11.1. Это не основания объявлять 6.11.2 свободным от прочих native/third-party рисков.

## Наличие Qt update и исходников

Публичного exact PySide6 6.11.3 не найдено: PyPI JSON/project дают HTTP404, pip index показывает 6.11.2 → 6.12.0; [Qt 6.11 index](https://download.qt.io/official_releases/qt/6.11/) и [QtForPython index](https://download.qt.io/official_releases/QtForPython/pyside6/) не содержат 6.11.3. Сырые проверки: `dependency-availability.json`. Утверждение ограничено проверенными публичными каналами.

Доступный 6.12.0 потребовал бы отдельного обновления бинарников, совместимой пары Qt/PySide, лицензий, source archives и metadata. Скачивание исходников/установка Qt не выполнялись. По официальным mirrorlist:

- [PySide/Shiboken source 6.12.0](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz.mirrorlist): 18 211 772 байта, SHA256 `099c1a597cf33c1b000e11cf68a0b82d216fb802bf81726efc720394f10c7751`.
- [Qtbase source 6.12.0](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qtbase-everywhere-src-6.12.0.tar.xz.mirrorlist): 51 220 268 байт, SHA256 `a951bd163c7b80fc6b8c88d7668fb56abf91c152373e13c10666763238131307`.
- Суммарно два соответствующих source archives: 69 432 040 байт (~66.2 МиБ); проверенные Windows wheels перечислены в `qt612-available-wheels.json`.

Решение выпуска: сохранить проверенную Qt 6.11.2 chain с этим reachability record; обновление Qt подготовить отдельно. Единственная зависимость, изменённая этой проверкой, — CI cryptography pin после успешных тестов. Windows production Python, Qt/PySide, Nuitka и live Debian packages сохранены.
