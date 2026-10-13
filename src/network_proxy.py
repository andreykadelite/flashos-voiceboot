"""Per-connection proxy handoff, with credentials confined to RAM files."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit, quote, unquote
import ipaddress
import re
import stat

PROXY_VARIABLES = ('http_proxy', 'https_proxy', 'no_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY')


def bypasses(host, entries):
    host = host.lower().rstrip('.')
    for value in entries:
        if value == '*':
            return True
        if '/' in value:
            try:
                if ipaddress.ip_address(host) in ipaddress.ip_network(value):
                    return True
            except ValueError:
                pass
        else:
            domain = value.lower().lstrip('*.').rstrip('.')
            if host == domain or host.endswith('.' + domain):
                return True
    return False


def ensure_credentials(ui):
    from network_runtime import active_profile
    profile = active_profile()
    if profile is not None and profile.proxy.username:
        from network_settings import manager
        settings = manager(ui)
        if not settings.unlock():
            raise RuntimeError('Для прокси разблокируйте сохранённые пароли в настройках сети.')


def proxy_environment(ui):
    from network_runtime import active_profile
    profile = active_profile()
    if profile is None or profile.proxy.mode == 'none':
        return {}
    proxy = profile.proxy
    password = ''
    if proxy.username:
        settings = getattr(ui, 'network_settings', None)
        if settings is None or not settings.vault.unlocked:
            raise RuntimeError('Пароль прокси заблокирован. Откройте защищённое хранилище в настройках сети.')
        record = settings.vault.get(profile.secret_ref)
        if 'proxy_password' not in record:
            raise RuntimeError('Для прокси не сохранён пароль. Измените профиль сети.')
        password = record['proxy_password']
    def authenticated(url):
        if not url or not proxy.username:
            return url
        parsed = urlsplit(url)
        return parsed.scheme + '://' + quote(proxy.username, safe='') + ':' + quote(password, safe='') + '@' + parsed.netloc
    http, https = authenticated(proxy.http_url), authenticated(proxy.https_url)
    bypass = ','.join(proxy.bypass)
    result = {'http_proxy': http, 'https_proxy': https, 'no_proxy': bypass,
              'HTTP_PROXY': http, 'HTTPS_PROXY': https, 'NO_PROXY': bypass}
    return result


def apt_configuration(environment):
    def literal(value):
        # URI and bypass validation happen in the profile model; still escape APT syntax.
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    lines = []
    for protocol in ('http', 'https'):
        value = environment.get(protocol + '_proxy', '') or 'DIRECT'
        lines.append(f'Acquire::{protocol}::Proxy {literal(value)};')
        for host in ('deb.debian.org', 'security.debian.org'):
            if bypasses(host, environment.get('no_proxy', '').split(',')):
                lines.append(f'Acquire::{protocol}::Proxy::{host} "DIRECT";')
    return '\n'.join(lines) + '\n'


@contextmanager
def apt_proxy(ui):
    environment = proxy_environment(ui)
    directory = Path(tempfile.mkdtemp(prefix='flashos-proxy-', dir='/run'))
    try:
        path = directory / 'apt.conf'
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(apt_configuration(environment))
        # No credentials in systemd-run argv: the worker reads a RAM-only bind mount.
        yield directory, environment
    finally:
        shutil.rmtree(directory)


def write_session_proxy(ui, runtime):
    environment = proxy_environment(ui)
    path = runtime / 'network-proxy.json'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(environment, stream)
    os.chown(path, 1000, 1000)


def read_session_proxy(runtime):
    path = runtime / 'network-proxy.json'
    raw = read_private_file(path, 1000, 128 * 1024)
    if raw is None:
        return {}
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict) or any(key not in PROXY_VARIABLES or not isinstance(value, str) or
            any(c in value for c in ('\n', '\r', '\0')) for key, value in data.items()):
        raise RuntimeError('Недопустимые параметры прокси сессии.')
    return data


def read_private_file(path, owner, limit):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeError('Недопустимый файл параметров прокси.') from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit or info.st_mode & 0o077 or info.st_uid != owner:
            raise RuntimeError('Недопустимый файл параметров прокси.')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise RuntimeError('Слишком большой файл параметров прокси.')
        return raw
    finally:
        os.close(descriptor)


def read_apt_proxy(path=Path('/run/flashos-proxy/apt.conf')):
    raw = read_private_file(path, 0, 128 * 1024)
    if raw is None:
        raise RuntimeError('Параметры прокси магазина недоступны.')
    result = {}
    for protocol, url in re.findall(r'Acquire::(http|https)::Proxy "([^"\n]*)";', raw.decode('utf-8')):
        if url != 'DIRECT':
            result[protocol + '_proxy'] = url
    return result


def redact_proxy(text, environment):
    for protocol in ('http', 'https'):
        url = environment.get(protocol + '_proxy', '')
        password = urlsplit(url).password
        if password:
            for value in sorted({password, unquote(password)}, key=len, reverse=True):
                if value:
                    text = text.replace(value, '[скрыто]')
    return re.sub(r'((?:https?|socks5h)://)[^\s/@]+@', r'\1[скрыто]@', text)
