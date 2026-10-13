"""Validated portable network profiles; passwords belong only in the encrypted vault."""
from dataclasses import asdict, dataclass, field, replace
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import unicodedata
from urllib.parse import urlsplit
import uuid

MAX_BYTES = 256 * 1024
MAX_PROFILES = 64


class ProfileError(ValueError):
    pass


class DurabilityError(OSError):
    """The file was replaced, but the directory flush did not confirm USB durability."""


def _text(value, label, maximum=128, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or
            (not empty and not value) or
            any(unicodedata.category(c) in ('Cc', 'Cs') for c in value)):
        raise ProfileError('Недопустимое поле: ' + label + '.')
    return value


def _boolean(value, label):
    if type(value) is not bool:
        raise ProfileError('Нужно логическое значение: ' + label + '.')
    return value


def _metric(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 2**32 - 1):
        raise ProfileError('Метрика маршрута должна быть от 0 до 4294967295.')
    return value


def _items(value, maximum, label):
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ProfileError('Недопустимый список: ' + label + '.')
    return tuple(value)


def _address(value, interface=False, allow_loopback=False):
    _text(value, 'IP-адрес', 64)
    if '%' in value or (interface and '/' not in value):
        raise ProfileError('Адрес требует CIDR-префикс; имя интерфейса в адресе недопустимо.')
    try:
        parsed = ipaddress.ip_interface(value) if interface else ipaddress.ip_address(value)
    except ValueError as error:
        raise ProfileError('Недопустимый IP-адрес или CIDR-префикс.') from error
    address = parsed.ip if interface else parsed
    if (address.is_unspecified or address.is_multicast or
            (address.is_loopback and not allow_loopback) or str(address) == '255.255.255.255'):
        raise ProfileError('Для сетевого профиля нужен обычный адрес узла.')
    if (interface and address.version == 4 and parsed.network.prefixlen < 31 and
            address in (parsed.network.network_address, parsed.network.broadcast_address)):
        raise ProfileError('IPv4-адрес узла не может быть адресом сети или широковещательным адресом.')
    return str(parsed)


def _domain(value):
    _text(value, 'домен поиска DNS', 253)
    try:
        normalized = value.rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError as error:
        raise ProfileError('Недопустимый домен DNS.') from error
    if (not normalized or len(normalized) > 253 or
            not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                    for part in normalized.split('.'))):
        raise ProfileError('Недопустимый домен DNS.')
    return normalized


def _uuid(value, label):
    _text(value, label, 36)
    try:
        parsed = str(uuid.UUID(value))
    except ValueError as error:
        raise ProfileError('Недопустимый идентификатор: ' + label + '.') from error
    if parsed != value or parsed == str(uuid.UUID(int=0)):
        raise ProfileError('Нужен UUID в стандартной записи: ' + label + '.')
    return value


def normalize_mac(value):
    if not isinstance(value, str):
        raise ProfileError('Не подтверждён постоянный адрес сетевого адаптера.')
    value = value.strip().lower().replace('-', ':')
    if (not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', value) or
            value in ('00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff') or int(value[:2], 16) & 1):
        raise ProfileError('Не подтверждён постоянный адрес сетевого адаптера.')
    return value


def _mapping(value, keys, label):
    if not isinstance(value, dict) or set(value) - set(keys):
        raise ProfileError('Недопустимые поля: ' + label + '.')
    return value


@dataclass(frozen=True)
class Route:
    destination: str
    gateway: str = ''
    metric: int | None = None

    def __post_init__(self):
        _text(self.destination, 'сеть маршрута', 64)
        if '%' in self.destination or '/' not in self.destination:
            raise ProfileError('Маршрут требует сеть с CIDR-префиксом.')
        try:
            destination = ipaddress.ip_network(self.destination, strict=False)
        except ValueError as error:
            raise ProfileError('Недопустимая сеть маршрута.') from error
        _text(self.gateway, 'шлюз маршрута', 64, empty=True)
        gateway = _address(self.gateway) if self.gateway else ''
        if gateway and ipaddress.ip_address(gateway).version != destination.version:
            raise ProfileError('Сеть и шлюз маршрута используют разные версии IP.')
        object.__setattr__(self, 'destination', str(destination))
        object.__setattr__(self, 'gateway', gateway)
        _metric(self.metric)


@dataclass(frozen=True)
class IPSettings:
    method: str = 'auto'
    addresses: tuple[str, ...] = ()
    gateway: str = ''
    dns_method: str = 'auto'
    dns: tuple[str, ...] = ()
    dns_search: tuple[str, ...] = ()
    routes: tuple[Route, ...] = ()
    route_metric: int | None = None
    never_default: bool = False

    def __post_init__(self):
        if self.method not in ('auto', 'dhcp', 'manual', 'disabled'):
            raise ProfileError('Неизвестный способ настройки IP.')
        if self.dns_method not in ('auto', 'manual', 'none'):
            raise ProfileError('Неизвестный способ настройки DNS.')
        addresses = tuple(_address(v, interface=True) for v in _items(self.addresses, 16, 'адреса'))
        _text(self.gateway, 'шлюз', 64, empty=True)
        gateway = _address(self.gateway) if self.gateway else ''
        dns = tuple(_address(v, allow_loopback=True) for v in _items(self.dns, 8, 'DNS'))
        search = tuple(_domain(v) for v in _items(self.dns_search, 8, 'домены DNS'))
        routes = _items(self.routes, 32, 'маршруты')
        if not all(isinstance(route, Route) for route in routes):
            raise ProfileError('Нужен проверенный список маршрутов.')
        _metric(self.route_metric)
        _boolean(self.never_default, 'не использовать маршрут по умолчанию')
        if self.method == 'manual' and not addresses:
            raise ProfileError('Для ручной настройки нужен хотя бы один адрес с CIDR-префиксом.')
        if self.method != 'manual' and (addresses or gateway):
            raise ProfileError('Ручные адреса и шлюз доступны только в ручном режиме IP.')
        if self.dns_method == 'manual' and not dns:
            raise ProfileError('Введите хотя бы один сервер DNS или выберите отключение DNS.')
        if self.dns_method != 'manual' and dns:
            raise ProfileError('Адреса DNS доступны только в ручном режиме DNS.')
        if self.dns_method == 'none' and search:
            raise ProfileError('При отключённом DNS домены поиска не используются.')
        if self.method == 'disabled' and (addresses or gateway or dns or search or routes or
                                          self.route_metric is not None or self.never_default or
                                          self.dns_method != 'auto'):
            raise ProfileError('У отключённой версии IP не должно быть дополнительных параметров.')
        for key, value in (('addresses', addresses), ('gateway', gateway), ('dns', dns),
                           ('dns_search', search), ('routes', routes)):
            object.__setattr__(self, key, value)

    @property
    def ignore_auto_dns(self):
        return self.dns_method != 'auto'

    def validate_family(self, family):
        if family == 4 and self.method == 'dhcp':
            raise ProfileError('Для IPv4 DHCP используется режим auto.')
        values = [ipaddress.ip_interface(value).version for value in self.addresses]
        values += [ipaddress.ip_address(value).version for value in (*self.dns, self.gateway) if value]
        values += [ipaddress.ip_network(route.destination).version for route in self.routes]
        if any(version != family for version in values):
            raise ProfileError('Параметры IPv4 и IPv6 нельзя смешивать.')
        if self.method == 'manual':
            interfaces = tuple(ipaddress.ip_interface(value) for value in self.addresses)
            assigned = {value.ip for value in interfaces}
            if len(assigned) != len(interfaces):
                raise ProfileError('Один IP-адрес не должен повторяться с разными префиксами.')
            # An off-subnet gateway is valid only after an explicit direct route;
            # IPv6 link-local routers are reachable through the selected adapter.
            direct = tuple(ipaddress.ip_network(route.destination) for route in self.routes if not route.gateway)
            reachable = tuple(value.network for value in interfaces) + direct
            gateways = (self.gateway, *(route.gateway for route in self.routes))
            for text in (value for value in gateways if value):
                gateway = ipaddress.ip_address(text)
                if gateway in assigned:
                    raise ProfileError('Шлюз не может совпадать с собственным адресом адаптера.')
                if family == 4 and any(
                        value.network.prefixlen < 31 and gateway in
                        (value.network.network_address, value.network.broadcast_address)
                        for value in interfaces):
                    raise ProfileError('Шлюз не может быть адресом сети или широковещательным адресом.')
                if not (family == 6 and gateway.is_link_local) and not any(
                        gateway in network for network in reachable):
                    raise ProfileError('Шлюз вне подсетей адаптера: добавьте прямой маршрут к нему или исправьте адрес.')


@dataclass(frozen=True)
class WifiSettings:
    ssid: str
    security: str = 'open'
    hidden: bool = False

    def __post_init__(self):
        _text(self.ssid, 'SSID', 32)
        if len(self.ssid.encode('utf-8')) > 32:
            raise ProfileError('SSID должен занимать не более 32 байт UTF-8.')
        if self.security not in ('open', 'wpa2', 'wpa3'):
            raise ProfileError('Доступны открытая сеть, WPA2 Personal и WPA3 Personal.')
        _boolean(self.hidden, 'скрытая сеть')


def _proxy_url(value):
    _text(value, 'адрес прокси', 2048, empty=True)
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in ('http', 'https', 'socks5h') or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.path not in ('', '/') or parsed.query or parsed.fragment or
                (port is not None and not 1 <= port <= 65535)):
            raise ValueError
        host = parsed.hostname
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            host = _domain(host)
        else:
            if '%' in host or address.is_unspecified or address.is_multicast:
                raise ValueError
            host = '[' + str(address) + ']' if address.version == 6 else str(address)
        return parsed.scheme + '://' + host + (':' + str(port) if port is not None else '')
    except (ValueError, UnicodeError) as error:
        raise ProfileError('Нужен URL прокси без пароля, пути и параметров запроса.') from error


def _bypass(value):
    _text(value, 'исключение прокси', 253)
    if value == '*':
        return value
    if '%' in value:
        raise ProfileError('Имена интерфейсов в исключениях прокси недопустимы.')
    try:
        return str(ipaddress.ip_network(value, strict=False) if '/' in value else ipaddress.ip_address(value))
    except ValueError:
        prefix = '*.' if value.startswith('*.') else '.' if value.startswith('.') else ''
        return prefix + _domain(value[len(prefix):])


@dataclass(frozen=True)
class ProxySettings:
    mode: str = 'none'
    http_url: str = ''
    https_url: str = ''
    bypass: tuple[str, ...] = ()
    username: str = ''

    def __post_init__(self):
        if self.mode not in ('none', 'manual'):
            raise ProfileError('Неизвестный режим прокси.')
        http, https = _proxy_url(self.http_url), _proxy_url(self.https_url)
        bypass = tuple(_bypass(value) for value in _items(self.bypass, 32, 'исключения прокси'))
        _text(self.username, 'имя пользователя прокси', 128, empty=True)
        if self.mode == 'none' and (http or https or bypass or self.username):
            raise ProfileError('Для отключённого прокси дополнительные параметры не используются.')
        if self.mode == 'manual' and not (http or https):
            raise ProfileError('Введите URL прокси для HTTP или HTTPS.')
        for key, value in (('http_url', http), ('https_url', https), ('bypass', bypass)):
            object.__setattr__(self, key, value)


@dataclass(frozen=True)
class ProfileScope:
    machine: str
    device: str
    portable: bool = False

    def __post_init__(self):
        if not isinstance(self.machine, str) or not re.fullmatch(r'[0-9a-f]{64}', self.machine):
            raise ProfileError('Не подтверждён идентификатор компьютера.')
        object.__setattr__(self, 'device', normalize_mac(self.device))
        _boolean(self.portable, 'переносимый профиль')


@dataclass(frozen=True)
class NetworkProfile:
    id: str
    name: str
    kind: str
    scope: ProfileScope
    ipv4: IPSettings = field(default_factory=IPSettings)
    ipv6: IPSettings = field(default_factory=IPSettings)
    wifi: WifiSettings | None = None
    proxy: ProxySettings = field(default_factory=ProxySettings)
    secret_ref: str = ''
    autoconnect: bool = False

    def __post_init__(self):
        _uuid(self.id, 'профиль')
        _text(self.name, 'название профиля', 80)
        if self.kind not in ('wired', 'wifi') or not isinstance(self.scope, ProfileScope):
            raise ProfileError('Нужны тип подключения и область действия профиля.')
        if not all(isinstance(value, IPSettings) for value in (self.ipv4, self.ipv6)):
            raise ProfileError('Нужны проверенные настройки IPv4 и IPv6.')
        self.ipv4.validate_family(4)
        self.ipv6.validate_family(6)
        if (self.kind == 'wifi' and not isinstance(self.wifi, WifiSettings)) or (self.kind == 'wired' and self.wifi is not None):
            raise ProfileError('Параметры Wi-Fi требуются только для беспроводного профиля.')
        if not isinstance(self.proxy, ProxySettings):
            raise ProfileError('Нужны проверенные настройки прокси.')
        _text(self.secret_ref, 'ссылка на зашифрованный секрет', 36, empty=True)
        if self.secret_ref:
            _uuid(self.secret_ref, 'секрет')
        _boolean(self.autoconnect, 'автоподключение')
        if self.scope.portable and (self.kind != 'wifi' or self.static):
            raise ProfileError('Переносимым может быть только профиль Wi-Fi с автоматической настройкой IP.')
        if self.scope.portable and not any(value.method in ('auto', 'dhcp') for value in (self.ipv4, self.ipv6)):
            raise ProfileError('Переносимому профилю Wi-Fi нужна автоматическая настройка IP.')

    @property
    def static(self):
        return any(value.method == 'manual' or value.addresses or value.gateway or value.routes
                   for value in (self.ipv4, self.ipv6))

    def to_dict(self):
        return json.loads(json.dumps(asdict(self), ensure_ascii=False))


def ip_settings_from_dict(value):
    keys = IPSettings.__dataclass_fields__
    value = dict(_mapping(value, keys, 'настройки IP'))
    try:
        value['routes'] = tuple(Route(**_mapping(route, Route.__dataclass_fields__, 'маршрут'))
                                for route in _items(value.get('routes', ()), 32, 'маршруты'))
        return IPSettings(**value)
    except TypeError as error:
        raise ProfileError('Недопустимые параметры IP или маршрута.') from error


def profile_from_dict(value):
    value = dict(_mapping(value, NetworkProfile.__dataclass_fields__, 'профиль'))
    try:
        value['scope'] = ProfileScope(**_mapping(value.get('scope'), ProfileScope.__dataclass_fields__, 'область действия'))
        for name in ('ipv4', 'ipv6'):
            value[name] = ip_settings_from_dict(value.get(name, {}))
        wifi = value.get('wifi')
        value['wifi'] = WifiSettings(**_mapping(wifi, WifiSettings.__dataclass_fields__, 'Wi-Fi')) if wifi is not None else None
        value['proxy'] = ProxySettings(**_mapping(value.get('proxy', {}), ProxySettings.__dataclass_fields__, 'прокси'))
        return NetworkProfile(**value)
    except TypeError as error:
        raise ProfileError('Профиль содержит неполные или недопустимые параметры.') from error


def matches(profile, machine, device, autoconnect=False):
    if (not isinstance(profile, NetworkProfile) or not isinstance(machine, str) or
            not re.fullmatch(r'[0-9a-f]{64}', machine) or not device or type(autoconnect) is not bool):
        return False
    try:
        device = normalize_mac(device)
    except ProfileError:
        return False
    exact = profile.scope.machine == machine and profile.scope.device == device
    return exact and profile.autoconnect if autoconnect else exact or profile.scope.portable


def rebind(profile, machine, device):
    """Explicit manual use on another PC creates a copy and never inherits autoconnect."""
    return replace(profile, id=str(uuid.uuid4()), scope=ProfileScope(machine, device, profile.scope.portable),
                   autoconnect=False)


def machine_id(sys_root='/sys'):
    """A stable hash of DMI identity; never use a cloned live machine-id or NIC name."""
    directory = Path(sys_root) / 'class/dmi/id'
    try:
        identifier = uuid.UUID((directory / 'product_uuid').read_text().strip())
        if identifier.int not in (0, 2**128 - 1):
            return hashlib.sha256(('flashos-pc-v1:uuid:' + str(identifier)).encode()).hexdigest()
    except (OSError, ValueError):
        pass
    try:
        serial = (directory / 'board_serial').read_text().strip()
        invalid = {'', 'none', 'unknown', 'default string', 'to be filled by o.e.m.', 'not specified',
                   'system serial number', '123456789', '0123456789', 'oem'}
        if serial.casefold() in invalid or len(serial) > 256 or len(set(serial)) == 1:
            return None
        _text(serial, 'серийный номер платы', 256)
        parts = []
        for name in ('board_vendor', 'board_name'):
            try:
                parts.append((directory / name).read_text().strip()[:256])
            except OSError:
                parts.append('')
        return hashlib.sha256(('flashos-pc-v1:board:' + json.dumps([*parts, serial])).encode()).hexdigest()
    except (OSError, ProfileError):
        return None


def device_id(interface, job=None, sys_root='/sys'):
    """Prefer the NIC's permanent address; randomized current Wi-Fi addresses are not IDs."""
    if not isinstance(interface, str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', interface):
        return None
    try:
        if job is not None:
            code, output = job.command(['ethtool', '-P', interface], timeout=5, accepted=None, log_output=False)
        else:
            result = subprocess.run(['ethtool', '-P', interface], capture_output=True, text=True,
                                    timeout=5, check=False, env={**os.environ, 'LC_ALL': 'C'})
            code, output = result.returncode, result.stdout
        if code == 0:
            match = re.search(r'Permanent address:\s*((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})', output)
            if match:
                return normalize_mac(match.group(1))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        pass
    directory = Path(sys_root) / 'class/net' / interface
    try:
        return normalize_mac((directory / 'perm_address').read_text().strip())
    except (OSError, ProfileError):
        pass
    try:
        if (directory / 'addr_assign_type').read_text().strip() == '0':
            return normalize_mac((directory / 'address').read_text().strip())
    except (OSError, ProfileError):
        pass
    return None


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def secure_path(state_root, relative='flashos/network/profiles.json', create_parent=False):
    """Reject symlinks in both the supplied root and every component below it."""
    root = Path(os.path.abspath(state_root))
    def linked(path):
        return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())
    if any(linked(path) for path in (root, *root.parents)) or not root.is_dir():
        raise ProfileError('Раздел настроек не подтверждён.')
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ProfileError('Недопустимый путь сетевых настроек.')
    target = root / path
    current = root
    for index, name in enumerate(path.parts):
        current = current / name
        if linked(current):
            raise ProfileError('Ссылки в сетевых настройках не допускаются.')
        if index < len(path.parts) - 1:
            if current.exists() and not current.is_dir():
                raise ProfileError('Вместо папки сетевых настроек найден файл.')
            if create_parent:
                current.mkdir(exist_ok=True)
        elif current.exists() and not current.is_file():
            raise ProfileError('Недопустимый файл сетевых настроек.')
    return target


class ProfileStore:
    def __init__(self, state_root):
        self.state_root = Path(state_root)
        self.path = self.state_root / 'flashos/network/profiles.json'

    def load(self):
        path = secure_path(self.state_root)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        except FileNotFoundError:
            return ()
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise ProfileError('Файл сетевых настроек слишком большой или имеет неверный тип.')
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ProfileError('Файл сетевых настроек слишком большой.')
        try:
            def unique_object(pairs):
                result = {}
                for name, value in pairs:
                    if name in result:
                        raise ValueError('Повторяющееся поле сетевых настроек.')
                    result[name] = value
                return result
            data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ProfileError('Файл сетевых настроек повреждён.') from error
        _mapping(data, ('format', 'profiles'), 'база сетевых настроек')
        if type(data.get('format')) is not int or data['format'] != 1:
            raise ProfileError('Неизвестная версия сетевых настроек.')
        profiles = tuple(profile_from_dict(value) for value in _items(data.get('profiles'), MAX_PROFILES, 'профили'))
        if len({profile.id for profile in profiles}) != len(profiles):
            raise ProfileError('В базе повторяются идентификаторы профилей.')
        return profiles

    def save(self, profiles):
        profiles = _items(profiles, MAX_PROFILES, 'профили')
        if not all(isinstance(profile, NetworkProfile) for profile in profiles):
            raise ProfileError('Нужен список проверенных сетевых профилей.')
        if len({profile.id for profile in profiles}) != len(profiles):
            raise ProfileError('Идентификаторы профилей не должны повторяться.')
        raw = (json.dumps({'format': 1, 'profiles': [profile.to_dict() for profile in profiles]},
                          ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if len(raw) > MAX_BYTES:
            raise ProfileError('Слишком большой набор сетевых настроек.')
        path = secure_path(self.state_root, create_parent=True)
        temporary = path.with_name('.profiles-' + uuid.uuid4().hex + '.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        replaced = False
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            secure_path(self.state_root)
            if temporary.is_symlink():
                raise ProfileError('Временный файл сетевых настроек изменился.')
            os.replace(temporary, path)
            replaced = True
            try:
                fsync_directory(path.parent)
            except OSError as error:
                raise DurabilityError('Профили записаны, но сохранение на носителе не подтверждено.') from error
        finally:
            if not replaced:
                temporary.unlink(missing_ok=True)

    def upsert(self, profile):
        if not isinstance(profile, NetworkProfile):
            raise ProfileError('Нужен проверенный сетевой профиль.')
        profiles = list(self.load())
        for index, current in enumerate(profiles):
            if current.id == profile.id:
                profiles[index] = profile
                break
        else:
            profiles.append(profile)
        self.save(profiles)

    def delete(self, profile_id):
        _uuid(profile_id, 'профиль')
        profiles = self.load()
        remaining = [profile for profile in profiles if profile.id != profile_id]
        if len(remaining) == len(profiles):
            return False
        self.save(remaining)
        return True
