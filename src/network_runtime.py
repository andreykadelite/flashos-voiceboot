"""Apply validated network profiles provisionally; NetworkManager owns crash rollback."""
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
import threading
import time
import uuid

from network_profiles import IPSettings, device_id, machine_id, matches, profile_from_dict


ACTIVE_STATE = Path('/run/flashos-network-active.json')
NM_SERVICE = 'org.freedesktop.NetworkManager'
NM_PATH = '/org/freedesktop/NetworkManager'
INTERFACE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,14}\Z')
DEVICE_PATH = re.compile(r'/org/freedesktop/NetworkManager/Devices/[0-9]+\Z')
CHECKPOINT_PATH = re.compile(r'/org/freedesktop/NetworkManager/Checkpoint/[0-9]+\Z')


def _uuid(value):
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None
    return parsed if parsed == value else None


class NetworkManager:
    """Bounded nmcli and D-Bus calls; command output and secrets never enter reports."""
    def __init__(self, job=None):
        self.job = job

    def command(self, argv, timeout=10, cleanup=False, required=True):
        if self.job is not None:
            code, output = self.job.command(argv, timeout=timeout, accepted=None,
                                            log_output=False, cleanup=cleanup, resource='network')
        else:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                    errors='replace', timeout=timeout,
                                    env={**os.environ, 'LC_ALL': 'C'})
            code, output = result.returncode, result.stdout
        if required and code:
            raise RuntimeError('NetworkManager не выполнил действие. Проверьте адаптер, кабель и настройки сети.')
        return code, output

    def dbus(self, method, signature, *arguments, cleanup=False, required=True):
        return self.command(['busctl', '--system', '--timeout=5s', 'call', NM_SERVICE,
                             NM_PATH, NM_SERVICE, method, signature, *map(str, arguments)],
                            timeout=8, cleanup=cleanup, required=required)

    def device_path(self, interface):
        _, output = self.dbus('GetDeviceByIpIface', 's', interface)
        fields = shlex.split(output)
        if len(fields) != 2 or fields[0] != 'o' or not DEVICE_PATH.fullmatch(fields[1]):
            raise RuntimeError('NetworkManager не подтвердил выбранный адаптер.')
        return fields[1]

    def checkpoint(self, path, seconds):
        # NM's DELETE_NEW_CONNECTIONS deletes profiles globally, even on other NICs.
        # Delete only our candidate UUID in Python; an orphan after a crash is RAM-only
        # and has autoconnect disabled. Never destroy unrelated checkpoints.
        _, output = self.dbus('CheckpointCreate', 'aouu', 1, path, seconds, 0)
        fields = shlex.split(output)
        if len(fields) != 2 or fields[0] != 'o' or not CHECKPOINT_PATH.fullmatch(fields[1]):
            raise RuntimeError('Безопасный откат сети недоступен. Настройки не применены.')
        return fields[1]

    def adjust(self, checkpoint, seconds):
        self.dbus('CheckpointAdjustRollbackTimeout', 'ou', checkpoint, seconds)

    def destroy(self, checkpoint, required=True):
        return self.dbus('CheckpointDestroy', 'o', checkpoint, cleanup=True, required=required)[0] == 0

    def rollback(self, checkpoint):
        code, output = self.dbus('CheckpointRollback', 'o', checkpoint, cleanup=True, required=False)
        if code:
            return False
        fields = shlex.split(output)
        if len(fields) < 2 or fields[0] != 'a{su}' or not fields[1].isdigit():
            return False
        count = int(fields[1])
        return count > 0 and len(fields) == 2 + count * 2 and all(
            DEVICE_PATH.fullmatch(fields[index]) and fields[index + 1] == '0'
            for index in range(2, len(fields), 2))

    def connection_status(self, interface, cleanup=False):
        _, output = self.command(['nmcli', '-g', 'GENERAL.CON-UUID,GENERAL.STATE',
                                 'device', 'show', interface], timeout=5, cleanup=cleanup)
        fields = output.splitlines()
        if len(fields) != 2:
            raise RuntimeError('Не удалось подтвердить активное соединение адаптера.')
        value = fields[0].strip()
        state = re.fullmatch(r'([0-9]+)(?:\s+\([^\r\n]*\))?', fields[1].strip())
        if not state or (value not in ('', '--') and not _uuid(value)):
            raise RuntimeError('Не удалось подтвердить активное соединение адаптера.')
        return (None if value in ('', '--') else value), int(state[1])

    def active_uuid(self, interface, cleanup=False, activated=False):
        # Keep raw UUIDs available to avoid overriding a different connection
        # that is still activating. A confirmed/proxy context requires state 100.
        value, state = self.connection_status(interface, cleanup=cleanup)
        return value if not activated or state == 100 else None

    def delete(self, identifier):
        self.command(['nmcli', 'connection', 'delete', 'uuid', identifier],
                     timeout=8, cleanup=True, required=False)

    def wait_active_uuid(self, interface, identifier, seconds=15):
        deadline = time.monotonic() + seconds
        while True:
            current, state = self.connection_status(interface, cleanup=True)
            complete = state == 100 if identifier else state in (10, 20, 30)
            if current == identifier and complete:
                return current
            if time.monotonic() >= deadline:
                if current == identifier:
                    raise RuntimeError('Предыдущее состояние сети ещё не восстановлено. '
                                       'Откройте диагностику сети.')
                return current
            time.sleep(.25)


def available_devices(job=None):
    """Only managed Ethernet/Wi-Fi adapters, without scanning or changing the network."""
    manager = NetworkManager(job)
    _, output = manager.command(['nmcli', '-t', '-e', 'no', '-f', 'DEVICE,TYPE,STATE',
                                 'device', 'status'], timeout=5)
    result = []
    for line in output.splitlines()[:64]:
        fields = line.split(':', 2)
        if len(fields) != 3:
            continue
        interface, kind, state = fields
        if INTERFACE.fullmatch(interface) and kind in ('ethernet', 'wifi') and state != 'unmanaged':
            result.append({'interface': interface, 'kind': 'wired' if kind == 'ethernet' else 'wifi',
                           'state': state})
    return result


def _ip_arguments(settings, family):
    prefix = 'ipv' + str(family)
    routes = []
    for route in settings.routes:
        gateway = route.gateway or ('0.0.0.0' if family == 4 else '::')
        routes.append(route.destination + ' ' + gateway +
                      ((' ' + str(route.metric)) if route.metric is not None else ''))
    values = {
        'method': settings.method, 'addresses': ','.join(settings.addresses),
        'gateway': settings.gateway, 'dns': ','.join(settings.dns),
        'dns-search': ','.join(settings.dns_search), 'routes': ','.join(routes),
        'ignore-auto-dns': 'no' if settings.dns_method == 'auto' else 'yes',
        'route-metric': str(settings.route_metric if settings.route_metric is not None else -1),
        'never-default': 'yes' if settings.never_default else 'no',
        'may-fail': 'no' if settings.method == 'manual' else 'yes',
    }
    if family == 4 and settings.method != 'disabled':
        # Detect a duplicate static/DHCP address before offering confirmation.
        values['dad-timeout'] = '3000'
    return [item for key, value in values.items() for item in (prefix + '.' + key, value)]


def candidate_arguments(profile, interface, identifier, permanent_mac):
    """Every IP/DNS/route setting is explicit, preventing old-profile inheritance."""
    args = ['nmcli', 'connection', 'add', 'save', 'no', 'type',
            'ethernet' if profile.kind == 'wired' else 'wifi', 'ifname', interface,
            'con-name', 'flashos-network-' + identifier, 'connection.uuid', identifier,
            'connection.autoconnect', 'no']
    args += ['802-3-ethernet.mac-address' if profile.kind == 'wired' else '802-11-wireless.mac-address',
             permanent_mac]
    args += _ip_arguments(profile.ipv4, 4) + _ip_arguments(profile.ipv6, 6)
    if profile.kind == 'wifi':
        args += ['ssid', profile.wifi.ssid, '802-11-wireless.hidden',
                 'yes' if profile.wifi.hidden else 'no']
        if profile.wifi.security != 'open':
            args += ['wifi-sec.key-mgmt', 'sae' if profile.wifi.security == 'wpa3' else 'wpa-psk',
                     # save no keeps the profile and PSK in NM's RAM cache. The
                     # checkpoint must be able to reconnect after nmcli exits.
                     'wifi-sec.psk-flags', '0']
    return args


def _read_state(path):
    try:
        if path.is_symlink():
            return None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                             getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            information = os.fstat(stream.fileno())
            if not stat.S_ISREG(information.st_mode) or information.st_size > 65536:
                return None
            raw = stream.read(65537)
        if len(raw) > 65536:
            return None
        value = json.loads(raw.decode('utf-8'))
        if not isinstance(value, dict) or type(value.get('format')) is not int or value['format'] != 1:
            return None
        return value
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None


def _write_state(path, value):
    if path.is_symlink():
        raise OSError('Недопустимый файл состояния сети.')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '-', delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _clear_state(path, identifier):
    value = _read_state(path)
    if value and value.get('connection_uuid') == identifier:
        path.unlink(missing_ok=True)


def active_profile(job=None, state_path=ACTIVE_STATE):
    """A proxy context is valid only while its exact confirmed connection is active."""
    path = Path(state_path)
    value = _read_state(path)
    if not value:
        return None
    try:
        interface = value.get('interface', '')
        identifier = value.get('connection_uuid')
        if not isinstance(interface, str) or not INTERFACE.fullmatch(interface) or not _uuid(identifier):
            raise ValueError('Неверное состояние сетевого адаптера.')
        profile = profile_from_dict(value['profile'])
        current, state = NetworkManager(job).connection_status(interface)
        valid = (current == identifier and
                 device_id(interface, job=job) == value.get('device') and
                 machine_id() == value.get('machine'))
        if valid:
            # Association/renewal may briefly leave state 100 while keeping the
            # same UUID. Preserve its confirmed context for the next ready state.
            return profile if state == 100 else None
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError):
        pass
    _clear_state(path, value.get('connection_uuid'))
    return None


class ConnectionTransaction:
    def __init__(self, manager, profile, interface, identifier, checkpoint, previous_uuid,
                 device, machine, state_path=ACTIVE_STATE):
        self.manager, self.profile, self.interface = manager, profile, interface
        self.connection_uuid, self.checkpoint = identifier, checkpoint
        self.previous_uuid, self.device, self.machine = previous_uuid, device, machine
        self.state_path = Path(state_path)
        self.previous_state = _read_state(self.state_path)
        self.deadline = 0
        self._state = 'pending'
        self._lock = threading.RLock()
        self._timer = None

    @property
    def pending(self):
        with self._lock:
            return self._state == 'pending'

    def remaining_seconds(self):
        return max(0, math.ceil(self.deadline - time.monotonic()))

    def await_confirmation(self, seconds, cancel_event):
        # NM still rolls back if FlashOS crashes or its Python threads stop running.
        self.manager.adjust(self.checkpoint, seconds + 5)
        self.deadline = time.monotonic() + seconds
        self._timer = threading.Timer(seconds, self._automatic_rollback)
        self._timer.daemon = True
        self._timer.start()
        def cancellation_watch():
            if cancel_event.wait(seconds):
                self._automatic_rollback()
        threading.Thread(target=cancellation_watch, daemon=True).start()

    def _automatic_rollback(self):
        try:
            self.rollback()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            # Keep the NM checkpoint alive; its timeout remains the final backstop.
            pass

    def confirm(self):
        with self._lock:
            if self._state == 'confirmed':
                return
            if self._state != 'pending' or self.remaining_seconds() == 0:
                self.rollback()
                raise RuntimeError('Время подтверждения истекло. Предыдущая сеть восстановлена.')
            if self.manager.active_uuid(self.interface, cleanup=True, activated=True) != self.connection_uuid:
                self.rollback()
                raise RuntimeError('Соединение изменилось. Заново выберите и примените профиль.')
            if device_id(self.interface) != self.device or machine_id() != self.machine:
                self.rollback()
                raise RuntimeError('Компьютер или адаптер изменился. Заново выберите сетевой профиль.')
            if self.remaining_seconds() == 0:
                self.rollback()
                raise RuntimeError('Время подтверждения истекло. Предыдущая сеть восстановлена.')
            record = {'format': 1, 'connection_uuid': self.connection_uuid,
                      'interface': self.interface, 'device': self.device, 'machine': self.machine,
                      'profile': self.profile.to_dict()}
            try:
                _write_state(self.state_path, record)
                self.manager.destroy(self.checkpoint)
            except BaseException:
                self.rollback()
                raise
            self._state = 'confirmed'
            if self._timer:
                self._timer.cancel()
            old = (self.previous_state or {}).get('connection_uuid')
            if (old and old != self.connection_uuid and _uuid(old) and
                    self.previous_state.get('interface') == self.interface):
                self.manager.delete(old)

    def rollback(self):
        with self._lock:
            if self._state != 'pending':
                return
            restored, current, failure = False, None, None
            try:
                native = self.manager.rollback(self.checkpoint)
                current = (self.manager.wait_active_uuid(self.interface, self.previous_uuid) if native else
                           self.manager.active_uuid(self.interface, cleanup=True))
                if current != self.previous_uuid and current in (None, self.connection_uuid):
                    if current == self.connection_uuid:
                        self.manager.command(['nmcli', 'connection', 'down', 'uuid', self.connection_uuid],
                                             timeout=8, cleanup=True, required=False)
                    if self.previous_uuid:
                        self.manager.command(['nmcli', '--wait', '15', 'connection', 'up', 'uuid',
                                              self.previous_uuid, 'ifname', self.interface],
                                             timeout=20, cleanup=True, required=False)
                    current = self.manager.wait_active_uuid(self.interface, self.previous_uuid)
                if not native and current == self.previous_uuid:
                    current = self.manager.wait_active_uuid(self.interface, self.previous_uuid)
                restored = current == self.previous_uuid
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                failure = error
                try:
                    current = self.manager.active_uuid(self.interface, cleanup=True)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    current = None
            if restored:
                try:
                    self.manager.destroy(self.checkpoint, required=False)
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    failure = failure or error
            try:
                self.manager.delete(self.connection_uuid)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                failure = failure or error
            try:
                _clear_state(self.state_path, self.connection_uuid)
                if self.previous_state and (
                        self.previous_state.get('interface') != self.interface or
                        self.previous_state.get('connection_uuid') == current == self.previous_uuid):
                    # Keep the other NIC's context, or the old UUID's context
                    # during reassociation; active_profile validates state 100.
                    _write_state(self.state_path, self.previous_state)
            except (OSError, ValueError) as error:
                failure = failure or error
            self._state = 'rolled_back'
            if self._timer:
                self._timer.cancel()
            if not restored:
                raise RuntimeError('Не удалось подтвердить возврат прежнего соединения. '
                                   'Откройте диагностику сети; автоматический откат NetworkManager остаётся включён.') from failure
            if failure:
                raise RuntimeError('Прежняя сеть восстановлена, но очистка временного состояния завершилась с ошибкой. '
                                   'Откройте диагностику сети.') from failure


def _verify_addresses(manager, profile, interface, dad_seconds=8):
    deadline = time.monotonic() + dad_seconds
    manual_link_local = {str(ipaddress.ip_interface(address).ip)
                         for settings in ((profile.ipv4, profile.ipv6) if profile else ())
                         if settings.method == 'manual' for address in settings.addresses
                         if ipaddress.ip_interface(address).ip.is_link_local}
    while True:
        _, output = manager.command(['ip', '-j', 'address', 'show', 'dev', interface], timeout=5)
        assigned, tentative = set(), set()
        try:
            data = json.loads(output)
            if not isinstance(data, list):
                raise ValueError('Список адаптеров не получен.')
            for nic in data:
                if nic.get('ifname') != interface:
                    continue
                for address in nic.get('addr_info', []):
                    value = ipaddress.ip_address(address['local'])
                    # A deliberately configured local-only network is valid. An
                    # automatic link-local fallback still does not prove DHCP worked.
                    explicit_local = value.is_link_local and str(value) in manual_link_local
                    if not explicit_local and (address.get('scope') != 'global' or value.is_link_local):
                        continue
                    flags = address.get('flags', [])
                    if address.get('dadfailed') or 'dadfailed' in flags:
                        raise RuntimeError('Обнаружен конфликт IP-адреса. Предыдущее соединение будет восстановлено.')
                    if address.get('tentative') or 'tentative' in flags:
                        tentative.add(str(value))
                    else:
                        assigned.add(str(value))
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise RuntimeError('Не удалось проверить полученные сетевые адреса.') from error
        if not tentative:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Проверка уникальности IP-адреса не завершилась. Предыдущее соединение будет восстановлено.')
        time.sleep(.25)
    if not assigned:
        raise RuntimeError('Сетевой адрес не получен. Предыдущее соединение будет восстановлено.')
    for settings in ((profile.ipv4, profile.ipv6) if profile else ()):
        if settings.method == 'manual' and not all(str(ipaddress.ip_interface(address).ip) in assigned
                                                    for address in settings.addresses):
            raise RuntimeError('Заданные статические адреса не применены. Предыдущее соединение будет восстановлено.')
    return sorted(assigned)


def temporary_dhcp_job(interface):
    """Quick Ethernet DHCP never lets device-connect pick a saved static profile."""
    def run(job):
        if not INTERFACE.fullmatch(interface):
            raise ValueError('Сетевое устройство изменилось. Выберите его заново.')
        if not any(device['interface'] == interface and device['kind'] == 'wired'
                   for device in available_devices(job)):
            raise ValueError('Проводной адаптер отсутствует или не управляется NetworkManager.')
        manager = NetworkManager(job)
        previous = manager.active_uuid(interface)
        checkpoint = manager.checkpoint(manager.device_path(interface), 75)
        identifier = str(uuid.uuid4())
        args = ['nmcli', 'connection', 'add', 'save', 'no', 'type', 'ethernet',
                'ifname', interface, 'con-name', 'flashos-dhcp-' + identifier,
                'connection.uuid', identifier, 'connection.autoconnect', 'no']
        args += _ip_arguments(IPSettings(), 4) + _ip_arguments(IPSettings(), 6)
        args += ['ipv4.may-fail', 'no']
        try:
            job.update('Получение адреса DHCP.')
            manager.command(args, timeout=12)
            manager.command(['nmcli', '--wait', '35', 'connection', 'up', 'uuid', identifier,
                             'ifname', interface], timeout=40)
            found = [address for address in _verify_addresses(manager, None, interface)
                     if ipaddress.ip_address(address).version == 4]
            if not found:
                raise RuntimeError('Адрес IPv4 по DHCP не получен. Предыдущая сеть будет восстановлена.')
            job.check()
            manager.destroy(checkpoint)
        except BaseException:
            restored = manager.rollback(checkpoint)
            current = manager.wait_active_uuid(interface, previous) if restored else manager.active_uuid(interface, cleanup=True)
            if current != previous and current in (None, identifier):
                if current == identifier:
                    manager.command(['nmcli', 'connection', 'down', 'uuid', identifier],
                                    timeout=8, cleanup=True, required=False)
                if previous:
                    manager.command(['nmcli', '--wait', '15', 'connection', 'up', 'uuid', previous,
                                     'ifname', interface], timeout=20, cleanup=True, required=False)
            manager.delete(identifier)
            if manager.wait_active_uuid(interface, previous) == previous:
                manager.destroy(checkpoint, required=False)
            raise
        value = _read_state(ACTIVE_STATE)
        if value and value.get('interface') == interface and value.get('connection_uuid') == previous:
            _clear_state(ACTIVE_STATE, previous)
            if previous and _uuid(previous):
                manager.delete(previous)
        job.result = 'Получен адрес по DHCP: ' + ', '.join(found) + '. Подключение действует до перезагрузки.'
    return run


def connect_job(profile, interface, password=None, confirmation_seconds=60,
                activation_seconds=40, state_path=ACTIVE_STATE):
    """Return a pending transaction in job.payload; the UI must confirm or rollback it."""
    def run(job):
        selected = profile_from_dict(profile.to_dict())
        if not INTERFACE.fullmatch(interface):
            raise ValueError('Недопустимое имя сетевого адаптера.')
        if not 5 <= confirmation_seconds <= 120 or not 5 <= activation_seconds <= 45:
            raise ValueError('Недопустимое время подключения или подтверждения.')
        machine, device = machine_id(), device_id(interface, job=job)
        if not device or not matches(selected, machine, device):
            raise ValueError('Профиль принадлежит другому компьютеру или адаптеру. '
                             'Сначала явно привяжите его к выбранному устройству.')
        available = {item['interface']: item['kind'] for item in available_devices(job)}
        if available.get(interface) != selected.kind:
            raise ValueError('Выбранный адаптер отсутствует, не управляется или не подходит профилю.')
        protected = selected.kind == 'wifi' and selected.wifi.security != 'open'
        if protected and (not isinstance(password, str) or not password or
                          len(password) > 128 or any(c in password for c in ('\n', '\r', '\0'))):
            raise ValueError('Введите пароль Wi-Fi одной строкой. Пароль не записывается в журнал.')
        manager = NetworkManager(job)
        previous_uuid = manager.active_uuid(interface)
        path = manager.device_path(interface)
        job.update('Сохраняю состояние сети для автоматического отката.')
        checkpoint = manager.checkpoint(path, activation_seconds + confirmation_seconds + 30)
        identifier = str(uuid.uuid4())
        transaction = ConnectionTransaction(manager, selected, interface, identifier, checkpoint,
                                            previous_uuid, device, machine, state_path)
        try:
            manager.command(candidate_arguments(selected, interface, identifier, device), timeout=12)
            job.update('Подключаю выбранный профиль и проверяю сетевые адреса.')
            args = ['nmcli', '--wait', str(activation_seconds), 'connection', 'up',
                    'uuid', identifier, 'ifname', interface]
            if protected:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='flashos-network-',
                                                 dir='/run') as secret:
                    os.chmod(secret.name, 0o600)
                    secret.write('802-11-wireless-security.psk:' + password + '\n')
                    secret.flush()
                    manager.command([*args, 'passwd-file', secret.name], timeout=activation_seconds + 5)
            else:
                manager.command(args, timeout=activation_seconds + 5)
            addresses = _verify_addresses(manager, selected, interface)
            job.check()
            transaction.await_confirmation(confirmation_seconds, job.cancel_event)
            job.check()
            job.payload = transaction
            job.lines.extend(['Адаптер: ' + interface, 'Адреса: ' + ', '.join(addresses),
                              'Адрес получен. Доступ в интернет проверяется отдельно.',
                              'Без подтверждения прежняя сеть восстановится автоматически.'])
            job.result = 'Профиль применён временно. Подтвердите соединение в течение ' + str(confirmation_seconds) + ' секунд.'
        except BaseException:
            transaction.rollback()
            raise
    return run


def startup_job(profiles, secret_resolver=None, state_path=ACTIVE_STATE):
    """At most one previously approved exact-PC/NIC profile, with a short activation bound."""
    def run(job):
        candidates = [profile_from_dict(profile.to_dict()) for profile in profiles if profile.autoconnect]
        job.result = 'Автоматическое подключение: подходящих сохранённых профилей нет.'
        if not candidates:
            return
        machine = machine_id()
        if not machine:
            return
        attempts = 0
        for device in available_devices(job)[:16]:
            if device.get('state') == 'unavailable':
                continue
            identity = device_id(device['interface'], job=job)
            matching = [profile for profile in candidates if profile.kind == device['kind'] and
                        matches(profile, machine, identity, autoconnect=True)]
            if len(matching) > 1:
                job.result = 'Несколько профилей претендуют на один адаптер. Выберите нужный профиль вручную.'
                continue
            for profile in matching:
                password = None
                if profile.kind == 'wifi' and profile.wifi.security != 'open':
                    if not profile.secret_ref or secret_resolver is None:
                        continue
                    try:
                        password = secret_resolver(profile.secret_ref)
                    except (OSError, RuntimeError, ValueError):
                        continue
                    if not password:
                        continue
                try:
                    attempts += 1
                    connect_job(profile, device['interface'], password, confirmation_seconds=10,
                                activation_seconds=10, state_path=state_path)(job)
                    job.payload.confirm()
                    job.result = 'Подключён сохранённый профиль: ' + profile.name + '.'
                    return
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                    job.result = 'Сохранённый профиль не подключён. Доступны ручное подключение и работа без сети.'
                    if attempts >= 3:
                        return
    return run
