# Исходники компонентов

[English](../en/sources.md) · [Сборка FlashOS](build.md) · [Лицензии](../../THIRD-PARTY.md)

В выпуске загружен только `FlashOS-Setup.exe`. Код FlashOS и документация находятся в репозитории соответствующего тега. Сторонние исходники можно получить по закреплённым адресам и ревизиям ниже.

| Что получить | Адреса, версии и SHA256 |
| --- | --- |
| Исходные пакеты Debian, включая ядро, GTK/VTE, Bash, xinput и smartmontools | [debian-sources.json](../../metadata/debian-sources.json); соответствие установленным пакетам — [debian-packages.json](../../metadata/debian-packages.json) и [app-runtime-packages.json](../../metadata/app-runtime-packages.json) |
| Ventoy 1.1.17, Qt base / PySide 6.11.2, Python 3.12.10, прежний SOF 2025.12.2 | [upstream-sources.json](../../metadata/upstream-sources.json) |
| Nuitka 4.2.1, OpenSSL 3.0.16 и libffi 3.4.4 | [additional-sources.json](../../metadata/additional-sources.json) |
| Зависимости и конфигурации встроенных компонентов Ventoy | [ventoy-extra-sources.json](../../metadata/ventoy-extra-sources.json) |
| RHVoice 1.18.4 и использованные языки, голоса и библиотеки | [rhvoice-sources.json](../../metadata/rhvoice-sources.json), команда ниже |
| Используемый SOF 2026.09.2, исходный пакет прошивок с уведомлениями | [build-inputs.json](../../metadata/build-inputs.json) |

## Загрузка файлов по манифестам

Команды выполняются из корня исходников тега в Bash с `jq`, `curl` и `sha256sum`. Они скачивают несколько гигабайт и сверяют каждый файл. Старые версии Debian могут исчезнуть с обычного зеркала: тогда найдите эту же версию на [snapshot.debian.org](https://snapshot.debian.org/) и сверьте SHA256 из манифеста, прежде чем использовать файл.

```bash
set -eu
mkdir -p sources/files
jq -r '.[] | (.files // [.])[] | [.url, .file, .sha256] | @tsv' \
  metadata/debian-sources.json metadata/upstream-sources.json \
  metadata/additional-sources.json metadata/ventoy-extra-sources.json \
  > sources/downloads.tsv
jq -r '.[] | select(.file == "sof-bin-2026.09.2.tar.gz") | [.url, .file, .sha256] | @tsv' \
  metadata/build-inputs.json >> sources/downloads.tsv
while IFS=$'\t' read -r url file sha; do
  [ "$file" != "musl-1.2.1.tar.gz" ] || url="https://www.musl-libc.org/releases/$file"
  curl --fail --location --retry 3 "$url" -o "sources/files/$file"
  printf '%s  %s\n' "$sha" "sources/files/$file" | sha256sum --check -
done < sources/downloads.tsv
```

Для musl указан также официальный адрес с `www`: на основном имени при проверке возникла ошибка TLS. В манифесте сохранён первоначальный адрес; загруженный файл в любом случае должен пройти проверку SHA256.

Пакет Debian распаковывается вместе с исходным архивом и патчами, например:

```bash
cd sources/files
dpkg-source -x xinput_1.6.4-1.dsc
cd xinput-1.6.4
dpkg-buildpackage -us -uc -b
```

Для сборки нужны зависимости соответствующего пакета и подходящая Debian-среда. `tools/collect-debian-sources.py` собирает источники из подготовленного rootfs, используя точные версии и подписанные индексы `deb-src`; получение файлов уже опубликованного тега не требует повторной сборки rootfs.

## RHVoice с выбранными подмодулями

Обычный архив GitHub не включает содержимое подмодулей. Следующая команда получает закреплённую ревизию и все 50 использованных подмодулей, затем сверяет их commits с манифестом. Нужны Git и Python 3.

```bash
python3 - <<'PY'
import json, subprocess
from pathlib import Path
m = json.loads(Path('metadata/rhvoice-sources.json').read_text())
dest = Path('sources/RHVoice')
subprocess.run(['git', 'clone', '--depth', '1', '--branch', m['tag'], m['repository'], str(dest)], check=True)
def git(*args):
    return subprocess.check_output(['git', '-C', str(dest), *args], text=True).strip()
assert git('rev-parse', 'HEAD') == m['commit'], 'RHVoice commit mismatch'
entries = [line.split()[:2] for line in m['submodules']]
subprocess.run(['git', '-C', str(dest), 'submodule', 'update', '--init', '--depth', '1', '--jobs', '4', '--', *[p for _, p in entries]], check=True)
for commit, path in entries:
    assert git('-C', path, 'rev-parse', 'HEAD') == commit, path
print('RHVoice and selected submodules match the manifest')
PY
```

Каталог назначения должен отсутствовать перед первым запуском. Используются те же подмодули, которые выбирает [build/install-rhvoice.py](../../build/install-rhvoice.py); остальные голоса не требуются.

## Точная база дисков smartmontools

Пакет smartmontools указан в Debian-манифесте. Обновлённая база `drivedb.h` получена из [официального commit 5e6cb183947e13bbf5eefb8f450c5b0415edee40](https://github.com/smartmontools/smartmontools/commit/5e6cb183947e13bbf5eefb8f450c5b0415edee40). Штатный обновлятор добавляет в строку `VERSION` номер, дату и commit. Восстановить установленный файл можно так:

```bash
python3 - <<'PY'
import hashlib, urllib.request
from pathlib import Path
url = 'https://raw.githubusercontent.com/smartmontools/smartmontools/5e6cb183947e13bbf5eefb8f450c5b0415edee40/drivedb/drivedb.h'
with urllib.request.urlopen(url) as response:
    data = response.read()
data = data.replace(b'  { "VERSION: 7.5",', b'  { "VERSION: 7.5/6277 2026-09-01 16:20:03 +0000 5e6cb183947e",', 1)
assert hashlib.sha256(data).hexdigest() == '55036ae43423822aee906e9d72630a715998e57326c1d2eb22eea46d53630154'
Path('sources/drivedb.h').write_bytes(data)
PY
```

## Что означает реестр архивов

[source-archives.json](../../metadata/source-archives.json) описывает локальные архивы сборщика и хеши их содержимого. Это исторический инвентарь, а не список доступных вложений выпуска.

У прежнего upstream-архива было 44 файла: 37 загрузок из трёх манифестов выше, дерево RHVoice, база дисков, её README, три JSON-манифеста и снимок `usr/share/doc` под именем `Debian-documentation-and-licenses.tar.gz`. README и JSON описывают происхождение файлов; снимок содержит документацию и уведомления установленной Debian-системы. Текущие уведомления сохраняются в `licenses/` репозитория и `/usr/share/doc` загруженной FlashOS. Снимок для своей сборки можно создать командой `tar -C /opt/flashos-rootfs -czf sources/Debian-documentation-and-licenses.tar.gz usr/share/doc`.

Эти команды восстанавливают исходные файлы и выбранные деревья. Они не обещают побайтового совпадения заново созданных TAR/ZIP или готового EXE. Известные ограничения происхождения старых бинарников Ventoy указаны в [THIRD-PARTY.md](../../THIRD-PARTY.md).
