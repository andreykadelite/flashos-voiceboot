"""APT application backend. Run only inside FlashOS's marked Debian application root."""
import argparse
from collections import deque
import configparser
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

STATE = Path('/var/lib/flashos-store')
MARKER = Path('/etc/flashos-app-root')
STATUS = Path('/var/lib/dpkg/status')
LISTS = Path('/var/lib/apt/lists')
SOURCES = ('all', 'stable', 'backports', 'main', 'contrib', 'non-free', 'non-free-firmware')
SUITES = {'trixie', 'trixie-updates', 'trixie-security', 'trixie-backports'}
RESERVE = 256 * 1024 * 1024
REMOVAL_RESERVE = 16 * 1024 * 1024
PACKAGE = re.compile(r'^[a-z0-9][a-z0-9+.-]*(?::amd64|:all)?$')
VERSION = re.compile(r'^[A-Za-z0-9.+:~\-]+$')
FROZEN = {'apt', 'dpkg', 'bash', 'dash', 'coreutils', 'python3', 'python3-minimal',
          'python3-apt', 'debianutils', 'libc-bin', 'util-linux', 'mount', 'login'}
APT_OPTIONS = ['-o', 'Acquire::AllowInsecureRepositories=false',
               '-o', 'Acquire::AllowDowngradeToInsecureRepositories=false',
               '-o', 'Acquire::AllowWeakRepositories=false',
               '-o', 'APT::Get::AllowUnauthenticated=false',
               '-o', 'Dpkg::Options::=--force-confold',
               '-o', 'Dpkg::Options::=--force-confdef',
               '-o', 'DPkg::Lock::Timeout=30']
CANCELLED = False


class StoreError(RuntimeError):
    pass


class StoreNetworkError(StoreError):
    """APT transport failed; only pre-install downloads may be repeated safely."""
    def __init__(self, message, retry_safe=False):
        super().__init__(message)
        self.retry_safe = bool(retry_safe)


def network_failure(output):
    """Recognize explicit C-locale transport errors, keeping integrity failures separate."""
    # A mixed failure must still expose signature, storage or repository problems.
    # Connecting to a network cannot fix them, so do not offer a network retry.
    operation_errors = (
        'gpg error', 'no_pubkey', 'expkeysig', 'badsig', 'revkeysig',
        'signature verification', 'signatures couldn\'t be verified', 'signatures were invalid', 'is not signed',
        'does not have a release file', 'clearsigned file isn\'t valid',
        'certificate verification failed', 'certificate is not trusted',
        'certificate issuer is unknown', 'certificate has expired', 'hash sum mismatch',
        '404 not found', '403 forbidden', '401 unauthorized',
        'no space left on device', 'read-only file system', 'permission denied',
        'could not get lock', 'unable to acquire', 'has no installation candidate',
        'unable to locate package', 'unmet dependencies', 'held broken packages',
    )
    lowered = output.casefold()
    if (any(message in lowered for message in operation_errors) or
            re.search(r'\bversion .+ was not found\b', lowered)):
        return False
    return any(message in lowered for message in (
        'temporary failure resolving', 'could not resolve', 'could not connect to',
        'unable to connect to', 'failed to connect to', 'connection timed out',
        'connection timeout', 'connection refused', 'connection reset by peer',
        'network is unreachable', 'network unreachable', 'no route to host',
        'connection closed prematurely', 'remote end closed connection',
    ))


def read_json(path, default=None):
    path = Path(path)
    if not path.exists() and default is not None:
        return default
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise StoreError('Служебный JSON слишком большой.')
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise StoreError('Невозможно прочитать служебные данные: ' + str(error)) from error


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(data, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == 'posix':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def names(values):
    if not isinstance(values, list) or len(values) > 512:
        raise StoreError('Нужен список не более 512 имён пакетов.')
    result = []
    for value in values:
        if not isinstance(value, str) or len(value) > 128 or not PACKAGE.fullmatch(value):
            raise StoreError('Некорректное имя пакета.')
        value = value.split(':')[0]
        if value not in result:
            result.append(value)
    return result


def protected_name(name):
    name = name.split(':')[0]
    return name.startswith(('linux-image', 'linux-modules', 'linux-headers',
                            'grub-', 'shim-', 'flashos'))


def baseline():
    data = read_json(STATE / 'base-packages.json')
    if isinstance(data, dict):
        data = data.get('packages', data)
    return set(names(list(data) if isinstance(data, dict) else data))


def ensure_root():
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise StoreError('Обработчик запускается администратором внутри контейнера приложений.')
    marker = read_json(MARKER)
    if marker != {'format': 1, 'suite': 'trixie', 'role': 'applications'}:
        raise StoreError('Корень приложений не подтверждён. Изменять основную систему запрещено.')
    baseline()
    policy = Path('/usr/sbin/policy-rc.d')
    if not policy.is_file() or 'exit 101' not in policy.read_text(encoding='utf-8'):
        raise StoreError('В контейнере не запрещён автоматический запуск системных служб.')


def validate_repository(uri, suite, components, fields=None):
    parsed = urlsplit(uri)
    paths = {'deb.debian.org': {'/debian', '/debian-security'},
             'security.debian.org': {'/debian-security'}}
    if (parsed.scheme != 'https' or parsed.hostname not in paths or
            parsed.path.rstrip('/') not in paths[parsed.hostname] or
            parsed.username or parsed.password or parsed.port not in (None, 443) or
            parsed.query or parsed.fragment or suite not in SUITES):
        raise StoreError('Разрешены только подписанные официальные репозитории Debian trixie.')
    if not components or not set(components).issubset(set(SOURCES[3:])):
        raise StoreError('Неизвестный раздел репозитория Debian.')
    fields = fields or {}
    if any(fields.get(key, '').lower() in {'yes', 'true', '1'} for key in
           ('trusted', 'allow-insecure', 'allow-weak', 'allow-downgrade-to-insecure')):
        raise StoreError('Нельзя отключать проверку подписи репозитория.')
    key = fields.get('signed-by', '/usr/share/keyrings/debian-archive-keyring.gpg')
    if key != '/usr/share/keyrings/debian-archive-keyring.gpg':
        raise StoreError('Источник должен использовать официальный ключ архива Debian.')


def repositories():
    entries = []
    directory = Path('/etc/apt/sources.list.d')
    files = [Path('/etc/apt/sources.list')]
    files += sorted(directory.glob('*.list')) + sorted(directory.glob('*.sources'))
    for path in files:
        if not path.is_file():
            continue
        content = path.read_text(encoding='utf-8')
        if path.suffix != '.sources':
            for line in content.splitlines():
                line = line.split('#', 1)[0].strip()
                if not line:
                    continue
                match = re.fullmatch(r'deb(?:\s+\[([^\]]+)\])?\s+(\S+)\s+(\S+)\s+(.+)', line)
                if not match:
                    raise StoreError('Не поддерживается строка источника: ' + path.name)
                fields = dict(item.split('=', 1) for item in (match[1] or '').split())
                validate_repository(match[2], match[3], match[4].split(), fields)
                entries.append({'uri': match[2], 'suite': match[3], 'components': match[4].split()})
        else:
            for paragraph in re.split(r'\n\s*\n', content):
                fields = {}
                for line in paragraph.splitlines():
                    if not line.strip() or line.lstrip().startswith('#'):
                        continue
                    if line[:1].isspace() or ':' not in line:
                        raise StoreError('Некорректный формат источника: ' + path.name)
                    key, value = line.split(':', 1)
                    fields[key.lower()] = value.strip()
                if not fields or fields.get('enabled', 'yes').lower() == 'no':
                    continue
                if fields.get('types') != 'deb':
                    raise StoreError('Магазин поддерживает только двоичные пакеты Debian.')
                components = fields.get('components', '').split()
                for uri in fields.get('uris', '').split():
                    for suite in fields.get('suites', '').split():
                        validate_repository(uri, suite, components, fields)
                        entries.append({'uri': uri, 'suite': suite, 'components': components})
    if not entries:
        raise StoreError('Не настроены источники Debian. Восстановите контейнер приложений.')
    return entries


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def snapshot():
    value = hashlib.sha256()
    for path in sorted(LISTS.glob('*')):
        if path.is_file() and path.name not in {'lock'} and not path.name.startswith('.'):
            value.update(path.name.encode())
            value.update(bytes.fromhex(digest(path)))
    return {'status': digest(STATUS), 'lists': value.hexdigest()}


def require_snapshot(expected):
    if snapshot() != expected:
        raise StoreError('Список пакетов изменился. Заново откройте и подтвердите план.')


def registry():
    data = read_json(STATE / 'state.json', {'format': 1, 'packages': []})
    if not isinstance(data, dict) or data.get('format') != 1:
        raise StoreError('Неизвестный формат списка установленных приложений.')
    return set(names(data.get('packages', []))) - baseline()


def registry_sources():
    data = read_json(STATE / 'state.json', {'format': 1, 'packages': []})
    sources = data.get('sources', {})
    if not isinstance(sources, dict) or any(value not in SOURCES for value in sources.values()):
        raise StoreError('Некорректные сохранённые источники приложений.')
    return sources


def effective_source(package, source):
    if source == 'all' and package.is_installed:
        if registry_sources().get(package.name) == 'backports' or any(
                origin.codename == 'trixie-backports' for origin in package.installed.origins):
            return 'backports'
    return source


def save_registry(old, plan):
    current = updated_registry(old, plan)
    # Dependencies need their backports origin even after the installed version
    # disappears from current APT indexes. They remain outside the app registry.
    sources = dict(registry_sources())
    for item in plan['changes']:
        if item['action'] == 'remove':
            sources.pop(item['package'], None)
        elif item.get('source'):
            sources[item['package']] = item['source']
    for name in current:
        if name in plan['requested'] and plan['operation'] == 'install':
            sources.setdefault(name, plan['source'])
    atomic_json(STATE / 'state.json', {'format': 1, 'packages': sorted(current), 'sources': sources})


def pending():
    data = read_json(STATE / 'journal.json', {})
    return data if data.get('phase') not in (None, 'complete', 'cancelled') else None


def require_healthy():
    if pending():
        raise StoreError('Установка была прервана. Выполните «Восстановить зависимости».')
    code, output = command(['dpkg', '--audit'], progress=False, accepted=None)
    if code or output.strip():
        raise StoreError('Есть недонастроенные пакеты. Выполните «Восстановить зависимости».')


def cancellation(signum, frame):
    global CANCELLED
    CANCELLED = True
    print('Запрошена остановка. Текущая запись пакетов завершится безопасно.', file=sys.stderr, flush=True)


def command(argv, progress=True, accepted=(0,), cancellable=False, retry_safe=False):
    environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root',
                   'LANG': 'C.UTF-8', 'LC_ALL': 'C',
                   'DEBIAN_FRONTEND': 'noninteractive', 'UCF_FORCE_CONFFOLD': '1'}
    from network_proxy import read_apt_proxy, redact_proxy
    proxy_environment = {}
    if os.environ.get('APT_CONFIG') == '/run/flashos-proxy/apt.conf':
        environment['APT_CONFIG'] = '/run/flashos-proxy/apt.conf'
        proxy_environment = read_apt_proxy()
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                             errors='replace', env=environment, start_new_session=True)
    tail = deque(maxlen=40)
    classification = deque(maxlen=40)
    captured = []
    count = 0
    try:
        for line in child.stdout:
            tail.append(redact_proxy(line.rstrip(), proxy_environment)[:1000])
            # Classification sees original transport/integrity words, but this
            # bounded RAM-only text is never included in messages or reports.
            classification.append(line[:1000] + line[-1000:])
            count += len(line)
            if count <= 8 * 1024 * 1024:
                captured.append(line)
            if progress:
                print(redact_proxy(line.rstrip(), proxy_environment)[:1000], file=sys.stderr, flush=True)
            if cancellable and CANCELLED:
                os.killpg(child.pid, signal.SIGTERM)
        code = child.wait()
    finally:
        child.stdout.close()
        # A dpkg child deliberately has its own session; never kill it on UI cancellation.
    output = ''.join(captured)
    if accepted is not None and code not in accepted:
        detail = '\n'.join(tail)
        message = 'Команда ' + argv[0] + ' завершилась с ошибкой: ' + redact_proxy(detail, proxy_environment)[:40000]
        if not CANCELLED and Path(argv[0]).name == 'apt-get' and network_failure('\n'.join(classification)):
            raise StoreNetworkError(message, retry_safe=retry_safe)
        raise StoreError(message)
    if count > 8 * 1024 * 1024:
        raise StoreError('Вывод пакетной команды превышает допустимый объём.')
    return code, output


def cache(source='all'):
    if source not in SOURCES:
        raise StoreError('Неизвестный источник пакетов.')
    import apt
    import apt_pkg
    apt_pkg.init()
    if source == 'backports':
        apt_pkg.config.set('APT::Default-Release', 'trixie-backports')
    else:
        # Stable/security/updates remain equally eligible; backports retain priority 100.
        apt_pkg.config.clear('APT::Default-Release')
    apt_pkg.config.clear('Acquire::Languages')
    apt_pkg.config.set('Acquire::Languages::0', 'ru')
    apt_pkg.config.set('Acquire::Languages::1', 'en')
    apt_pkg.config.set('APT::Install-Recommends', 'true')
    apt_pkg.config.set('APT::Install-Suggests', 'false')
    return apt.Cache()


def origin_matches(version, source):
    for origin in version.origins:
        suite = origin.codename
        if suite not in SUITES:
            continue
        if source == 'backports' and suite != 'trixie-backports':
            continue
        if source != 'backports' and suite == 'trixie-backports':
            continue
        if source in SOURCES[3:] and origin.component != source:
            continue
        return True
    return False


def selected_version(package, source):
    import apt_pkg
    candidates = [version for version in package.versions if origin_matches(version, source)]
    if not candidates:
        return None
    best = candidates[0]
    for version in candidates[1:]:
        if apt_pkg.version_compare(version.version, best.version) > 0:
            best = version
    return best


def package_info(package, version):
    origins = [{'suite': origin.codename, 'component': origin.component}
               for origin in version.origins if origin.codename in SUITES]
    return {'package': package.name, 'version': version.version,
            'installed_version': package.installed.version if package.is_installed else None,
            'summary': version.summary or '', 'description': (version.description or '')[:12000],
            'section': version.section, 'download_bytes': int(version.size),
            'installed_bytes': int(version.installed_size), 'sources': origins}


def catalog_scan_order(selected):
    """Read compressed description records in storage order, with no new index.

    Alphabetical package order repeatedly seeks backwards in compressed APT
    lists. The public low-level cache exposes each translated record's key; it
    only changes the read order, never which version or description is searched.
    Fall back to the original order if a binding/record cannot supply the key.
    """
    if not selected:
        return selected
    try:
        import apt_pkg
        from apt.progress.base import OpProgress
        records = apt_pkg.Cache(OpProgress())
        keyed = []
        for package, version in selected:
            primitive = next(item for item in records[package.name].version_list
                             if item.ver_str == version.version and item.arch == version.architecture)
            package_file, index = primitive.translated_description.file_list[0]
            keyed.append((package_file.id, index, package, version))
        return [(package, version) for _file, _index, package, version in
                sorted(keyed, key=lambda item: item[:2])]
    except (ImportError, AttributeError, IndexError, KeyError, StopIteration,
            TypeError, ValueError, OSError, SystemError):
        return selected


def catalog(query, source, offset, limit):
    query = query.strip().casefold()
    if len(query) > 250 or offset < 0 or offset > 200000 or not 1 <= limit <= 100:
        raise StoreError('Некорректные параметры поиска или страницы.')
    packages = cache(source)
    selected = []
    for package in sorted(packages, key=lambda item: (item.name != query, not item.name.startswith(query), item.name)):
        if protected_name(package.name):
            continue
        version = selected_version(package, source)
        if version is None:
            continue
        selected.append((package, version))
    matches = []
    for package, version in catalog_scan_order(selected) if query else selected:
        if query and query not in (package.name + ' ' + (version.summary or '') + ' ' + (version.description or '')).casefold():
            continue
        matches.append((package, version))
    matches.sort(key=lambda item: (item[0].name != query, not item[0].name.startswith(query), item[0].name))
    total = len(matches)
    found = [package_info(package, version) for package, version in matches[offset:offset + limit]]
    return {'entries': found, 'total': total, 'offset': offset, 'limit': limit,
            'next_offset': offset + limit if offset + limit < total else None,
            'needs_refresh': not any(path.is_file() for path in LISTS.glob('*_Packages*'))}


def installed():
    packages = cache()
    entries = []
    for name in sorted(registry()):
        if name in packages and packages[name].is_installed:
            entries.append(package_info(packages[name], packages[name].installed))
    return {'entries': entries, 'repair_required': bool(pending())}


def allowed_changes(changes, seed):
    for item in changes:
        name = item['package'].split(':')[0]
        if protected_name(name):
            raise StoreError('Системные загрузчики и драйверы не устанавливаются в контейнер приложений.')
        if item['action'] == 'remove' and (name in seed or name in FROZEN or item.get('essential')):
            raise StoreError('Пакет ' + name + ' необходим контейнеру и не может быть удалён.')
        if item['action'] == 'upgrade' and (name in FROZEN or item.get('essential')):
            raise StoreError('Обновление защищённого пакета ' + name + ' требует новой версии контейнера.')


def changes_from_cache(packages):
    result = []
    for package in sorted(packages.get_changes(), key=lambda item: item.name):
        old = package.installed.version if package.is_installed else None
        new = None if package.marked_delete else package.candidate.version
        if old == new:
            continue
        if old and new:
            import apt_pkg
            if apt_pkg.version_compare(new, old) < 0:
                raise StoreError('Выбранная версия пакета ' + package.name + ' старее установленной. Понижение версии запрещено.')
        result.append({'package': package.name, 'from_version': old, 'to_version': new,
                       'action': 'remove' if new is None else ('upgrade' if old else 'install'),
                       'download_bytes': int(package.candidate.size) if new else 0,
                       'installed_size_delta': (int(package.candidate.installed_size) if new else 0) -
                           (int(package.installed.installed_size) if old else 0),
                       'essential': bool(package.essential),
                       'source': ('backports' if new and any(origin.codename == 'trixie-backports'
                                  for origin in package.candidate.origins) else 'stable')})
    return result


def check_space(required, location=STATE, minimum_inodes=2048):
    usage = shutil.disk_usage(location)
    stat = os.statvfs(location) if hasattr(os, 'statvfs') else None
    if usage.free < required:
        raise StoreError('Недостаточно места в хранилище приложений: необходимо ' +
                         str((required + 1048575) // 1048576) + ' МБ.')
    if stat and stat.f_favail < minimum_inodes:
        raise StoreError('Недостаточно свободных файловых записей. Освободите хранилище приложений.')
    return usage.free


def removal_only(changes):
    return bool(changes) and all(item['action'] == 'remove' and item['to_version'] is None
                                 for item in changes)


def check_transaction_space(required, changes):
    # Removing files needs a bounded journal budget, including on a nearly full image.
    return check_space(required, minimum_inodes=64 if removal_only(changes) else 2048)


def required_space(changes, packages):
    # Upgrades keep the old files while unpacking complete replacements.
    download = sum(item['download_bytes'] for item in changes)
    unpacked = sum(int(packages[item['package']].candidate.installed_size)
                   for item in changes if item['to_version'])
    return download + unpacked + (REMOVAL_RESERVE if removal_only(changes) else RESERVE)


def make_plan(request):
    require_healthy()
    if not isinstance(request, dict):
        raise StoreError('Запрос плана должен быть объектом JSON.')
    operation = request.get('action', request.get('operation'))
    source = request.get('source', 'all')
    requested = names(request.get('packages', []))
    if operation not in {'install', 'remove', 'upgrade'} or (operation != 'upgrade' and not requested):
        raise StoreError('Укажите установку, удаление или обновление и имена пакетов.')
    target = 'backports' if source == 'all' and any(
        registry_sources().get(name) == 'backports' for name in requested) else source
    packages = cache(target)
    before = snapshot()
    if operation == 'upgrade':
        if requested:
            for name in requested:
                if name not in packages or not packages[name].is_installed:
                    raise StoreError('Пакет не установлен: ' + name)
                version = selected_version(packages[name], effective_source(packages[name], source))
                if version:
                    import apt_pkg
                    if apt_pkg.version_compare(version.version, packages[name].installed.version) > 0:
                        packages[name].candidate = version
                        packages[name].mark_install(auto_fix=True, from_user=False)
        else:
            # A full app update must not silently unfreeze the seed runtime.
            for package in packages:
                if package.is_installed and package.name not in FROZEN and not package.essential:
                    version = selected_version(package, effective_source(package, source))
                    if version:
                        import apt_pkg
                        if apt_pkg.version_compare(version.version, package.installed.version) > 0:
                            package.candidate = version
                            package.mark_install(auto_fix=True, from_user=False)
    else:
        for name in requested:
            if name not in packages:
                raise StoreError('Пакет не найден: ' + name + '. Обновите каталог.')
            package = packages[name]
            if operation == 'remove':
                if not package.is_installed:
                    raise StoreError('Пакет не установлен: ' + name)
                package.mark_delete(auto_fix=True, purge=False)
            else:
                version = selected_version(package, effective_source(package, source))
                if version is None:
                    raise StoreError('Пакет отсутствует в выбранном источнике: ' + name)
                if not version.downloadable:
                    raise StoreError('Для пакета нет доступного подписанного архива: ' + name)
                package.candidate = version
                package.mark_install(auto_fix=True, from_user=True)
    if packages.broken_count:
        raise StoreError('Зависимости не разрешены. Выберите стабильный источник или восстановите зависимости.')
    changes = changes_from_cache(packages)
    allowed_changes(changes, baseline())
    download = sum(item['download_bytes'] for item in changes)
    required = required_space(changes, packages)
    check_transaction_space(required, changes)
    require_snapshot(before)
    plan = {'format': 1, 'operation': operation, 'source': source, 'requested': requested,
            'changes': changes, 'snapshot': before, 'download_bytes': download,
            'required_bytes': required, 'transaction_id': uuid.uuid4().hex}
    simulate(plan)
    return plan


def apt_arguments(plan, download=False, simulation=False, hook=None):
    args = ['apt-get', *APT_OPTIONS, '--no-install-suggests', '--no-auto-remove']
    if plan['source'] == 'backports':
        args += ['-t', 'trixie-backports']
    if simulation:
        args += ['--simulate']
    else:
        args += ['--assume-yes']
    if download:
        args += ['--download-only']
    if hook:
        args += ['-o', 'DPkg::Pre-Invoke::=' + hook]
    specs = []
    for item in plan['changes']:
        name = names([item['package']])[0]
        version = item['to_version']
        if version is not None and (not isinstance(version, str) or not VERSION.fullmatch(version)):
            raise StoreError('Некорректная версия пакета в плане.')
        specs.append(name + ('=' + version if version else '-'))
    return args + ['install', '--', *specs]


def simulated_changes(output):
    result = {}
    for line in output.splitlines():
        if line.startswith('Inst '):
            match = re.match(r'^Inst (\S+)(?: \[[^\]]+\])? \((\S+)', line)
            if not match:
                raise StoreError('Не удалось проверить полный план APT.')
            result[match[1]] = match[2]
        elif line.startswith('Remv '):
            match = re.match(r'^Remv (\S+)', line)
            if match:
                result[match[1]] = None
    return result


def simulate(plan):
    if not plan['changes']:
        return
    _, output = command(apt_arguments(plan, simulation=True), progress=False)
    actual = simulated_changes(output)
    expected = {item['package']: item['to_version'] for item in plan['changes']}
    if actual != expected:
        raise StoreError('План зависимостей APT отличается от просмотра. Обновите каталог и повторите.')


def verify_versions(changes, packages):
    for item in changes:
        package = packages.get(item['package'])
        actual = package.installed.version if package and package.is_installed else None
        if actual != item['to_version']:
            raise StoreError('Фактическая версия пакета ' + item['package'] +
                             ' отличается от подтверждённого плана.')


def verify_packages(changes):
    code, audit = command(['dpkg', '--audit'], progress=False, accepted=None)
    if code or audit.strip():
        raise StoreError('Установка не завершена: ' + audit.strip())
    command(['apt-get', *APT_OPTIONS, 'check'])
    verify_versions(changes, cache())
    check_names = [item['package'] for item in changes if item.get('to_version')]
    if check_names:
        code, output = command(['dpkg', '--verify', *check_names], progress=False, accepted=None)
        # Locally changed configuration is preserved by force-confold and intentionally excluded.
        invalid = [line for line in output.splitlines()
                   if line.strip() and not re.match(r'^\S+\s+c\s', line)]
        if code or invalid:
            raise StoreError('Проверка файлов установленных пакетов не пройдена: ' + '\n'.join(invalid[:20]))


def updated_registry(old, plan):
    result = set(old)
    if plan['operation'] == 'install':
        result.update(plan['requested'])
    if plan['operation'] == 'remove':
        result.difference_update(plan['requested'])
    result.difference_update(item['package'] for item in plan['changes'] if item['action'] == 'remove')
    return result - baseline()


def unwrap_plan(data):
    if isinstance(data, dict) and data.get('ok') is True and isinstance(data.get('data'), dict):
        data = data['data']
    if isinstance(data, dict) and isinstance(data.get('plan'), dict):
        data = data['plan']
    if not isinstance(data, dict) or data.get('format') != 1:
        raise StoreError('Неизвестный формат подтверждённого плана.')
    if not isinstance(data.get('transaction_id'), str) or not re.fullmatch('[a-f0-9]{32}', data['transaction_id']):
        raise StoreError('Некорректный номер плана.')
    return data


def apply(data):
    plan = unwrap_plan(data)
    require_healthy()
    require_snapshot(plan.get('snapshot'))
    fresh = make_plan({'action': plan.get('operation'), 'packages': plan.get('requested'), 'source': plan.get('source')})
    for key in ('operation', 'source', 'requested', 'changes', 'snapshot', 'required_bytes', 'download_bytes'):
        if fresh.get(key) != plan.get(key):
            raise StoreError('Подтверждённый план изменён. Заново просмотрите изменения.')
    if not plan['changes']:
        save_registry(registry(), plan)
        return {'changed': False, 'message': 'Пакеты уже находятся в нужном состоянии.'}
    check_transaction_space(plan['required_bytes'], plan['changes'])
    print('Загрузка проверенных пакетов.', file=sys.stderr, flush=True)
    command(apt_arguments(plan, download=True), cancellable=True, retry_safe=True)
    if CANCELLED:
        return {'changed': False, 'cancelled': True, 'message': 'Загрузка остановлена до изменения пакетов.'}
    require_snapshot(plan['snapshot'])
    check_transaction_space(plan['required_bytes'] - plan['download_bytes'], plan['changes'])
    simulate(plan)
    _, manual = command(['apt-mark', 'showmanual'], progress=False)
    old = registry()
    journal = {'format': 1, 'phase': 'applying', 'plan': plan, 'previous_packages': sorted(old),
               'previous_manual': manual.split(), 'started': int(time.time())}
    reviewed = STATE / 'reviewed-plan.json'
    atomic_json(reviewed, plan)
    atomic_json(STATE / 'journal.json', journal)
    # Runs with the APT/dpkg transaction lock held, immediately before dpkg writes.
    hook = '/usr/bin/python3 ' + shlex.quote(str(Path(__file__).resolve())) + ' verify --request ' + shlex.quote(str(reviewed))
    command(apt_arguments(plan, hook=hook))
    automatic = [item['package'] for item in plan['changes'] if item['to_version'] and
                 item['package'] not in set(manual.split()) | old | set(plan['requested']) | baseline()]
    if automatic:
        command(['apt-mark', 'auto', *automatic], progress=False)
    verify_packages(plan['changes'])
    journal['phase'] = 'verified'
    atomic_json(STATE / 'journal.json', journal)
    save_registry(old, plan)
    journal['phase'] = 'complete'
    atomic_json(STATE / 'journal.json', journal)
    return {'changed': True, 'cancel_requested': CANCELLED, 'changes': plan['changes'],
            'message': 'Пакеты установлены и проверены.'}


def repair():
    journal = pending()
    command(['dpkg', '--configure', '-a'])
    packages = cache()
    packages.fix_broken()
    fixes = changes_from_cache(packages)
    allowed_changes(fixes, baseline())
    if fixes:
        fix_plan = {'source': 'all', 'changes': fixes}
        simulate(fix_plan)
        check_transaction_space(required_space(fixes, packages), fixes)
        command(apt_arguments(fix_plan))
    changes = journal.get('plan', {}).get('changes', []) if journal else []
    if journal:
        allowed_changes(changes, baseline())
        current = cache(journal['plan']['source'])
        unfinished = False
        for item in changes:
            package = current.get(item['package'])
            actual = package.installed.version if package and package.is_installed else None
            if actual != item['to_version']:
                unfinished = True
                break
        # A crash after dpkg/verification can leave only the registry commit.
        # Do not require the original installation budget after it was consumed.
        if unfinished:
            check_transaction_space(journal['plan']['required_bytes'], changes)
            command(apt_arguments(journal['plan']))
    if journal and 'previous_manual' in journal:
        automatic = [item['package'] for item in changes if item['to_version'] and
                     item['package'] not in set(journal['previous_manual']) |
                     set(journal['previous_packages']) | set(journal['plan']['requested']) | baseline()]
        if automatic:
            command(['apt-mark', 'auto', *automatic], progress=False)
    verified_changes = {item['package']: item for item in [*fixes, *changes]}
    verify_packages(list(verified_changes.values()))
    if journal:
        plan = journal['plan']
        packages = cache(plan['source'])
        for item in plan['changes']:
            package = packages.get(item['package'])
            actual = package.installed.version if package and package.is_installed else None
            if actual != item['to_version']:
                raise StoreError('Зависимости исправлены, но исходный план не завершён. Просмотрите журнал установки.')
        save_registry(journal['previous_packages'], plan)
        journal['phase'] = 'complete'
        atomic_json(STATE / 'journal.json', journal)
    return {'message': 'Зависимости и файлы пакетов проверены.', 'cancel_requested': CANCELLED}


def executable(value):
    if '\x00' in value or '\n' in value or '\r' in value:
        raise StoreError('Некорректная команда запуска.')
    path = Path(value) if value.startswith('/') else Path(shutil.which(value) or '/nonexistent')
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise StoreError('Исполняемый файл приложения не найден.')
    resolved = path.resolve()
    if not str(resolved).startswith(('/usr/', '/opt/')) or '..' in path.parts:
        raise StoreError('Недопустимое расположение исполняемого файла.')
    return str(path)


def desktop_argv(exec_value, name, icon, desktop):
    try:
        tokens = shlex.split(exec_value, posix=True)
    except ValueError as error:
        raise StoreError('Некорректная строка запуска приложения.') from error
    output = []
    for token in tokens:
        if token in {'%f', '%F', '%u', '%U', '%d', '%D', '%n', '%N', '%v', '%m'}:
            continue
        if token == '%i':
            if icon:
                output += ['--icon', icon]
            continue
        replacements = {'c': name, 'k': str(desktop), '%': '%'}
        def replace(match):
            if match[1] not in replacements:
                raise StoreError('Не поддерживается параметр команды запуска.')
            return replacements[match[1]]
        token = re.sub(r'%(.)|%$', replace, token)
        output.append(token)
    if not output:
        raise StoreError('Приложение не содержит команды запуска.')
    output[0] = executable(output[0])
    return output


def parse_desktop(path, package):
    path = Path(path)
    if not path.is_absolute() or path.parent != Path('/usr/share/applications') or path.suffix != '.desktop':
        raise StoreError('Недопустимый файл запуска.')
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise StoreError('Файл запуска отсутствует или небезопасен.')
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read(path, encoding='utf-8')
    if 'Desktop Entry' not in parser:
        return None
    section = parser['Desktop Entry']
    if section.get('Type') != 'Application' or any(section.get(key, '').lower() == 'true' for key in ('Hidden', 'NoDisplay')):
        return None
    name = section.get('Name[ru]', section.get('Name', package))
    name = ''.join(char for char in name if char.isprintable())[:200]
    if section.get('TryExec'):
        executable(section['TryExec'])
    return {'id': path.name, 'package': package, 'name': name,
            'argv': desktop_argv(section.get('Exec', ''), name, section.get('Icon', ''), path),
            'terminal': section.get('Terminal', 'false').lower() == 'true',
            'desktop_file': str(path)}


def terminal_entries(files, package):
    result = []
    candidates = sorted(set(files), key=lambda line: (Path(line).name != package, Path(line).name))
    for line in candidates:
        path = Path(line)
        if path.parent not in {Path('/usr/bin'), Path('/bin')}:
            continue
        try:
            argv = [executable(line)]
        except StoreError:
            continue
        if not any(entry['argv'] == argv for entry in result):
            result.append({'id': package + '-command-' + path.name, 'package': package,
                           'name': path.name + ' — терминал', 'argv': argv, 'terminal': True})
        if len(result) == 32:
            break
    return result


def desktop_entries(files, package):
    entries = []
    for line in files:
        if not line.startswith('/usr/share/applications/') or not line.endswith('.desktop'):
            continue
        try:
            entry = parse_desktop(line, package)
            if entry:
                entries.append(entry)
        except (StoreError, configparser.Error, OSError, UnicodeError) as error:
            print('Пропущен файл запуска: ' + str(error), file=sys.stderr)
    return entries


def metapackage_entries(package):
    packages = cache()
    installed = packages[package].installed if package in packages else None
    if installed is None or installed.section.rsplit('/', 1)[-1] != 'metapackages':
        return []
    # Suites such as LibreOffice own only documentation; their direct dependencies own GUI launchers.
    owners = {version.package.name for dependency in installed.dependencies
              for version in dependency.installed_target_versions} - baseline() - {package}
    if len(owners) > 64:
        raise StoreError('У метапакета слишком много компонентов. Установите нужное приложение отдельно.')
    entries, seen = [], set()
    for owner in sorted(owners):
        owner = names([owner])[0]
        _, output = command(['dpkg-query', '-L', owner], progress=False)
        for entry in desktop_entries(output.splitlines(), package):
            if entry['id'] not in seen:
                entry['owner_package'] = owner
                entries.append(entry)
                seen.add(entry['id'])
        if len(entries) > 128:
            raise StoreError('У метапакета слишком много вариантов запуска. Выберите отдельный компонент.')
    return entries


def launchers(package):
    package = names([package])[0]
    require_healthy()
    if package not in registry():
        raise StoreError('Пакет не установлен через магазин приложений.')
    _, output = command(['dpkg-query', '-L', package], progress=False)
    files = output.splitlines()
    desktops = [line for line in files if line.startswith('/usr/share/applications/') and line.endswith('.desktop')]
    entries = desktop_entries(files, package)
    if not desktops:
        entries = terminal_entries(files, package)
        if not entries:
            entries = metapackage_entries(package)
    return {'entries': entries}


def clean_cache():
    ensure_root()
    require_healthy()
    before = shutil.disk_usage(STATE).free
    command(['apt-get', *APT_OPTIONS, 'clean'])
    after = shutil.disk_usage(STATE).free
    return {'message': 'Кэш загруженных пакетов очищен. Приложения и настройки сохранены.',
            'freed_bytes': max(0, after - before), 'free_bytes': after}


def upgrades(source):
    packages = cache(source)
    entries = []
    import apt_pkg
    for package in packages:
        if (not package.is_installed or package.name in FROZEN or package.essential
                or protected_name(package.name)):
            continue
        version = selected_version(package, effective_source(package, source))
        if version and apt_pkg.version_compare(version.version, package.installed.version) > 0:
            entries.append(package_info(package, version))
    return {'entries': sorted(entries, key=lambda item: item['package'])}


@contextlib.contextmanager
def store_lock():
    import fcntl
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'operation.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise StoreError('Другая операция магазина ещё выполняется.') from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('catalog', 'installed', 'updates', 'sources', 'plan', 'apply',
                                          'refresh', 'repair', 'clean', 'launchers', 'verify'))
    parser.add_argument('--request', type=Path)
    parser.add_argument('--query', default='')
    parser.add_argument('--source', choices=SOURCES, default='all')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--limit', type=int, default=30)
    parser.add_argument('--package')
    arguments = parser.parse_args(argv)
    signal.signal(signal.SIGINT, cancellation)
    signal.signal(signal.SIGTERM, cancellation)
    try:
        ensure_root()
        repos = repositories()
        request = read_json(arguments.request) if arguments.request else {}
        if arguments.action == 'verify':
            require_snapshot(unwrap_plan(request)['snapshot'])
            return 0
        with store_lock():
            action = arguments.action
            if action == 'catalog':
                data = catalog(arguments.query, arguments.source, arguments.offset, arguments.limit)
            elif action == 'installed':
                data = installed()
            elif action == 'updates':
                data = upgrades(arguments.source)
            elif action == 'sources':
                data = {'entries': repos, 'choices': list(SOURCES)}
            elif action == 'plan':
                data = make_plan(request)
            elif action == 'apply':
                data = apply(request)
            elif action == 'repair':
                data = repair()
            elif action == 'clean':
                data = clean_cache()
            elif action == 'launchers':
                data = launchers(arguments.package)
            else:
                require_healthy()
                command(['apt-get', *APT_OPTIONS, '-o', 'APT::Update::Error-Mode=any',
                         '-o', 'Acquire::Languages::=ru', '-o', 'Acquire::Languages::=en', 'update'],
                        cancellable=True, retry_safe=True)
                data = {'message': 'Каталог обновлён.', 'cancelled': CANCELLED}
        print(json.dumps({'format': 1, 'ok': True, 'action': arguments.action, 'data': data}, ensure_ascii=False), flush=True)
        return 0
    except Exception as error:
        network_error = isinstance(error, StoreNetworkError) and not CANCELLED
        result = {'format': 1, 'ok': False, 'action': arguments.action,
                  'error': str(error) if isinstance(error, StoreError) else 'Ошибка обработки пакетов: ' + str(error),
                  'error_kind': 'network' if network_error else 'operation'}
        if network_error:
            result['retry_safe'] = error.retry_safe
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
