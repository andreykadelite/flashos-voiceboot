"""Spoken, portable network profiles; credentials are never stored in profiles."""
from dataclasses import replace
from pathlib import Path
import uuid

from network_profiles import (NetworkProfile, ProfileScope, ProfileStore, IPSettings,
    WifiSettings, ProxySettings, Route, DurabilityError, machine_id, device_id, matches, rebind)
from network_runtime import available_devices, connect_job, startup_job
from network_secrets import Vault


def words(value):
    return tuple(value.replace(',', ' ').split())


def manager(ui):
    if not hasattr(ui, 'network_settings'):
        ui.network_settings = NetworkSettings(ui)
    return ui.network_settings


class NetworkSettings:
    def __init__(self, ui):
        self.ui = ui
        self.store = ProfileStore(ui.state_root)
        self.vault = Vault(ui.state_root)

    def unlock(self, create=False):
        if self.vault.unlocked:
            return True
        exists = self.vault.exists
        if not exists and not create:
            return False
        password = self.ui.text_input('Пароль защищённого хранилища сети' if exists else
            'Новый общий пароль хранилища — не менее 8 символов', secret=True, limit=256)
        if password is None:
            return False
        if not exists:
            repeated = self.ui.text_input('Повторите новый пароль хранилища', secret=True, limit=256)
            if password != repeated:
                self.ui.message('Хранилище паролей', 'Пароли не совпали. Хранилище не создано.')
                return False
        result = self.ui.run_job('Разблокировка паролей сети' if exists else 'Создание защищённого хранилища',
            lambda job: self.vault.unlock(password) if exists else self.vault.create(password),
            review=False, save=False)
        return not (result.failed or result.cancelled)

    def startup(self):
        profiles = self.store.load()
        machine = machine_id()
        devices = available_devices()
        eligible = [p for p in profiles if p.autoconnect and any(
            d['kind'] == p.kind and matches(p, machine, device_id(d['interface']), autoconnect=True)
            for d in devices)]
        if not eligible:
            return
        if any(p.secret_ref for p in eligible):
            self.unlock()
        self.ui.run_job('Сохранённое подключение этого компьютера', startup_job(eligible,
            secret_resolver=lambda ref: self.vault.get(ref).get('wifi_password') if self.vault.unlocked else None),
            review=False, save=False)

    def choose_device(self, kind):
        items = [(d['interface'] + ' — ' + d['state'], d['interface'])
                 for d in available_devices() if d['kind'] == kind]
        if not items:
            self.ui.message('Сетевой адаптер', 'Подходящий адаптер не найден. Проверьте подключение и драйвер.')
            return None
        return self.ui.choose('Адаптер для профиля', items, prompt=True)

    def main(self):
        if self.ui.demo:
            self.ui.message('Сетевые профили', 'Настройка доступна после загрузки настоящей FlashOS.')
            return
        while True:
            try:
                profiles = self.store.load()
                choices = [('Создать профиль проводной сети', 'wired'),
                           ('Создать профиль Wi-Fi', 'wifi'),
                           ('Защищённое хранилище паролей', 'vault'),
                           ('Отключить сетевой адаптер', 'disconnect')]
                choices += [(p.name + (' — автоподключение' if p.autoconnect else '') +
                             (' — другой компьютер' if p.scope.machine != machine_id() else ''), p) for p in profiles]
                value = self.ui.choose('Настройки и сохранённые профили сети', choices,
                    'Профили сохраняются на флешке. Чужие настройки не применяются автоматически. '
                    'Статический адрес используйте только в подходящей сети. Пароли защищены общим паролем.', prompt=True)
                if value is None:
                    return
                if isinstance(value, NetworkProfile):
                    self.profile_menu(value)
                elif value in ('wired', 'wifi'):
                    self.create(value)
                elif value == 'vault':
                    self.vault_menu()
                elif value == 'disconnect':
                    self.disconnect()
            except (OSError, ValueError, RuntimeError) as error:
                self.ui.message('Настройки сети', str(error))

    def create(self, kind):
        interface = self.choose_device(kind)
        if not interface:
            return
        machine, device = machine_id(), device_id(interface)
        if not machine or not device:
            self.ui.message('Сохранение профиля', 'Не удалось надёжно определить компьютер или адаптер. '
                            'Автоматическое применение небезопасно. Используйте временное подключение в меню сети.')
            return
        name = self.ui.text_input('Название сетевого профиля', limit=80)
        if not name:
            return
        wifi = None
        if kind == 'wifi':
            from wifi import scan_job
            scan = self.ui.run_job('Поиск сетей для профиля', scan_job, review=False, save=True)
            if scan.cancelled:
                return
            if scan.failed:
                self.ui.speech.say('Поиск сетей не завершён. Можно ввести SSID вручную.')
            points = [p for p in getattr(scan, 'payload', []) if p['device'] == interface and p['ssid']]
            point = self.ui.choose('Сеть Wi-Fi для профиля',
                [(p['ssid'] + ' — ' + p['security'], p) for p in points] + [('Ввести имя сети вручную', 'manual')], prompt=True)
            if point is None:
                return
            ssid = self.ui.text_input('Имя сети Wi-Fi — SSID', limit=32) if point == 'manual' else point['ssid']
            if not ssid:
                return
            if point != 'manual' and any(x in point['security'] for x in ('EAP', '802.1X', 'WEP')):
                self.ui.message('Wi-Fi', 'Для этой сети нужен корпоративный способ входа. '
                                'Этот редактор поддерживает открытые сети и WPA2/WPA3 Personal.')
                return
            security = self.ui.choose('Защита Wi-Fi', [('WPA2 Personal', 'wpa2'),
                ('WPA3 Personal', 'wpa3'), ('Открытая сеть', 'open')], prompt=True)
            if security is None:
                return
            wifi = WifiSettings(ssid, security, hidden=point == 'manual')
        profile = NetworkProfile(str(uuid.uuid4()), name, kind, ProfileScope(machine, device), wifi=wifi)
        self.edit(profile)

    def profile_menu(self, profile):
        while True:
            choice = self.ui.choose(profile.name, [('Подключить и проверить', 'connect'),
                ('Изменить параметры', 'edit'), ('Прочитать все параметры', 'read'),
                ('Создать копию для этого компьютера и адаптера', 'rebind'),
                ('Удалить сохранённый профиль', 'delete')], prompt=True)
            if choice is None:
                return
            if choice == 'connect':
                self.connect(profile)
            elif choice == 'edit':
                self.edit(profile)
                profile = next((p for p in self.store.load() if p.id == profile.id), profile)
            elif choice == 'read':
                self.ui.view('Параметры ' + profile.name, summary(profile))
            elif choice == 'rebind':
                interface = self.choose_device(profile.kind)
                if interface and self.ui.confirm('Создать отдельную копию профиля для этого компьютера? '
                    'Прежний профиль сохранится. Автоподключение копии выключено.'):
                    copy = rebind(profile, machine_id(), device_id(interface))
                    self.edit(replace(copy, name=profile.name[:72] + ' — копия'))
            elif choice == 'delete':
                if self.ui.confirm('Удалить профиль ' + profile.name + ' с флешки? Текущее подключение сохранится.'):
                    self.store.delete(profile.id)
                    if (profile.secret_ref and self.vault.unlocked and
                            not any(p.secret_ref == profile.secret_ref for p in self.store.load())):
                        self.vault.delete(profile.secret_ref)
                    return

    def edit(self, profile):
        draft, secrets = profile, {}
        while True:
            items = [('Имя: ' + draft.name, 'name'), ('IPv4: ' + draft.ipv4.method, 'ipv4'),
                     ('IPv6: ' + draft.ipv6.method, 'ipv6'), ('Прокси: ' + draft.proxy.mode, 'proxy'),
                     ('Автоподключение: ' + ('включено' if draft.autoconnect else 'выключено'), 'auto'),
                     ('Прочитать параметры', 'read'), ('Сохранить профиль на флешку', 'save')]
            if draft.kind == 'wifi':
                items[1:1] = [('Параметры Wi-Fi: ' + draft.wifi.ssid, 'wifi'), ('Пароль Wi-Fi', 'password'),
                    ('Использование на другом ПК: ' + ('разрешено вручную' if draft.scope.portable else 'только этот ПК'), 'portable')]
            action = self.ui.choose('Редактор профиля ' + draft.name, items,
                'Изменения вступят в силу при сохранении и отдельном подключении. Escape отменяет редактирование.', prompt=True)
            if action is None:
                return
            try:
                if action == 'name':
                    value = self.ui.text_input('Новое название профиля', limit=80)
                    if value:
                        draft = replace(draft, name=value)
                elif action in ('ipv4', 'ipv6'):
                    value = edit_ip(self.ui, getattr(draft, action), int(action[-1]))
                    draft = replace(draft, **{action: value})
                elif action == 'proxy':
                    value = edit_proxy(self.ui, draft.proxy)
                    draft = replace(draft, proxy=value)
                    if value.username:
                        password = self.ui.text_input('Пароль прокси — Escape сохранить прежний', secret=True, limit=256)
                        if password is not None:
                            secrets['proxy_password'] = password
                elif action == 'auto':
                    if not draft.autoconnect and not self.ui.confirm('Подключать этот профиль на этом компьютере при загрузке? '
                        'Для статики это разрешение действует и при смене кабельной сети: проверьте её перед загрузкой.'):
                        continue
                    draft = replace(draft, autoconnect=not draft.autoconnect)
                elif action == 'portable':
                    draft = replace(draft, scope=replace(draft.scope, portable=not draft.scope.portable))
                elif action == 'wifi':
                    ssid = self.ui.text_input('SSID Wi-Fi — Escape оставить прежний', limit=32)
                    security = self.ui.choose('Защита Wi-Fi', [('WPA2 Personal', 'wpa2'), ('WPA3 Personal', 'wpa3'),
                                                              ('Открытая сеть', 'open')], prompt=True)
                    if ssid and security:
                        hidden = self.ui.choose('Скрытая сеть', [('Нет', False), ('Да', True)], prompt=True)
                        if hidden is not None:
                            draft = replace(draft, wifi=WifiSettings(ssid, security, hidden))
                elif action == 'password':
                    value = self.ui.text_input('Пароль Wi-Fi', secret=True, limit=128)
                    if value is not None:
                        secrets['wifi_password'] = value
                elif action == 'read':
                    self.ui.view('Параметры профиля', summary(draft))
                elif action == 'save':
                    if not self.ui.confirm('Сохранить профиль ' + draft.name + ' на флешку?'):
                        continue
                    self.save(draft, secrets)
                    return
            except (OSError, ValueError, RuntimeError) as error:
                self.ui.message('Параметр не изменён', str(error))

    def save(self, profile, secrets):
        if profile.wifi and profile.wifi.security != 'open' and 'wifi_password' in secrets:
            password = secrets['wifi_password']
            if (any(c in password for c in ('\n', '\r', '\0')) or
                    (profile.wifi.security == 'wpa2' and not (8 <= len(password.encode('utf-8')) <= 63 or
                      len(password) == 64 and all(c in '0123456789abcdefABCDEF' for c in password))) or
                    (profile.wifi.security == 'wpa3' and not 1 <= len(password.encode('utf-8')) <= 63)):
                raise ValueError('Недопустимый пароль Wi-Fi. WPA2: 8–63 байта или 64 шестнадцатеричных символа; WPA3: 1–63 байта.')
        before = self.store.load()
        old_ref = profile.secret_ref
        if secrets and not self.unlock(create=True):
            raise RuntimeError('Для сохранения паролей разблокируйте защищённое хранилище.')
        ref = str(uuid.uuid4()) if secrets else old_ref
        if secrets:
            inherited = self.vault.get(old_ref) if old_ref else {}
            profile = replace(profile, secret_ref=ref)
            self.vault.set(ref, {**inherited, **secrets})
        updated = [p for p in before if p.id != profile.id]
        if profile.autoconnect:
            updated = [replace(p, autoconnect=False) if p.scope.machine == profile.scope.machine
                       and p.scope.device == profile.scope.device else p for p in updated]
        updated.append(profile)
        try:
            self.store.save(updated)
        except DurabilityError:
            # replace already committed: retain both credential generations until
            # durability is known. Deleting the new entry could orphan this profile.
            raise
        except BaseException:
            if secrets:
                try:
                    self.vault.delete(ref)
                except (OSError, RuntimeError):
                    self.ui.speech.say('Новый пароль остался в защищённом хранилище; прежний профиль сохранён.')
            raise
        if secrets and old_ref and not any(p.secret_ref == old_ref for p in updated):
            try:
                self.vault.delete(old_ref)
            except (OSError, RuntimeError):
                self.ui.speech.say('Прежняя запись пароля осталась в защищённом хранилище. Новый профиль сохранён.')
        self.ui.speech.say('Сетевой профиль сохранён. Для применения выберите подключение.')

    def connect(self, profile):
        interface = self.choose_device(profile.kind)
        if not interface:
            return
        if not matches(profile, machine_id(), device_id(interface)):
            self.ui.message('Профиль другого устройства', 'Создайте копию профиля для этого компьютера и адаптера. '
                            'Чужой статический адрес не будет применён.')
            return
        password = None
        if profile.secret_ref and self.unlock():
            password = self.vault.get(profile.secret_ref).get('wifi_password')
        if profile.kind == 'wifi' and profile.wifi.security != 'open' and not password:
            password = self.ui.text_input('Пароль Wi-Fi для этого подключения', secret=True, limit=128)
            if password is None:
                return
        result = self.ui.run_job('Проверочное подключение профиля', connect_job(profile, interface, password),
                                 review=False, save=False)
        transaction = getattr(result, 'payload', None)
        if transaction is None:
            return
        try:
            if not (result.failed or result.cancelled) and self.ui.confirm_timed(
                    'Принять подключение ' + profile.name + '?', transaction.remaining_seconds()):
                transaction.confirm()
                self.ui.speech.say('Подключение принято. Профиль сохранён; наличие IP ещё не подтверждает интернет.')
        finally:
            if transaction.pending:
                transaction.rollback()

    def vault_menu(self):
        while True:
            choice = self.ui.choose('Защищённые пароли сети', [('Разблокировать или создать хранилище', 'unlock'),
                ('Заблокировать пароли в этой сессии', 'lock'), ('Изменить общий пароль хранилища', 'change')],
                'Пароль хранилища не сохраняется. При его потере сохранённые пароли нельзя восстановить. '
                'Блокировка закрывает доступ FlashOS к сохранённым паролям; действующее подключение не разрывается.', prompt=True)
            if choice is None:
                return
            if choice == 'unlock':
                self.unlock(create=True)
            elif choice == 'lock':
                self.vault.lock()
            elif choice == 'change' and self.unlock():
                first = self.ui.text_input('Новый общий пароль хранилища', secret=True, limit=256)
                second = self.ui.text_input('Повторите новый пароль', secret=True, limit=256) if first else None
                if first and first == second:
                    self.ui.run_job('Изменение общего пароля', lambda job: self.vault.change_password(first),
                                    review=False, save=False)
                elif first:
                    self.ui.message('Пароль хранилища', 'Пароли не совпали.')

    def disconnect(self):
        devices = available_devices()
        interface = self.ui.choose('Отключить адаптер', [(d['interface'], d['interface']) for d in devices], prompt=True)
        if interface and self.ui.confirm('Отключить ' + interface + '? Сохранённые профили останутся.'):
            def run(job):
                if interface not in [d['interface'] for d in available_devices(job)]:
                    raise RuntimeError('Адаптер изменился. Выберите его заново.')
                job.command(['nmcli', '--wait', '10', 'device', 'disconnect', interface], timeout=15, log_output=False)
                job.result = 'Адаптер отключён.'
            self.ui.run_job('Отключение сети', run, review=False, save=False)


def summary(profile):
    lines = [profile.name, 'Тип: ' + profile.kind,
             'Автоподключение: ' + ('да' if profile.autoconnect else 'нет'),
             'Привязка: компьютер и постоянный адрес адаптера. Имена интерфейсов не сохраняются.']
    if profile.wifi:
        lines += ['Wi-Fi: ' + profile.wifi.ssid + '; ' + profile.wifi.security,
                  'Скрытая сеть: ' + str(profile.wifi.hidden)]
    for family, settings in ((4, profile.ipv4), (6, profile.ipv6)):
        lines += [f'IPv{family}: {settings.method}', 'Адреса: ' + ', '.join(settings.addresses),
                  'Шлюз: ' + (settings.gateway or 'автоматический или отсутствует'),
                  'DNS: ' + settings.dns_method + '; ' + ', '.join(settings.dns),
                  'Домены поиска: ' + ', '.join(settings.dns_search),
                  'Метрика: ' + str(settings.route_metric), 'Без маршрута по умолчанию: ' + str(settings.never_default)]
        lines += [f'Маршрут: {r.destination}; через {r.gateway or "напрямую"}; метрика {r.metric}' for r in settings.routes]
    lines += ['Прокси: ' + profile.proxy.mode, 'HTTP: ' + profile.proxy.http_url,
              'HTTPS: ' + profile.proxy.https_url, 'Исключения: ' + ', '.join(profile.proxy.bypass),
              'Пароли здесь не показываются.']
    return lines


def edit_ip(ui, original, family):
    methods = [('Автоматически — DHCP' if family == 4 else 'Автоматически — SLAAC и DHCPv6', 'auto'),
               ('Статический адрес', 'manual'), ('Отключить IPv' + str(family), 'disabled')]
    if family == 6:
        methods.insert(1, ('Только DHCPv6', 'dhcp'))
    method = ui.choose('Способ настройки IPv' + str(family), methods, prompt=True)
    if method is None:
        return original
    if method == 'disabled':
        return IPSettings(method='disabled')
    addresses, gateway = (), ''
    if method == 'manual':
        raw = ui.text_input('IP-адреса с префиксом через пробел, например ' +
                            ('192.168.1.20/24' if family == 4 else '2001:db8::20/64'), limit=400)
        if raw is None:
            return original
        addresses = words(raw)
        gateway = ui.text_input('Шлюз — Enter без шлюза', limit=80)
        if gateway is None:
            return original
    dns_items = [('Автоматически из сети', 'auto'), ('Указать вручную', 'manual'),
                 ('Не использовать DNS этой версии IP', 'none')]
    if method == 'manual':
        dns_items = [('Указать вручную', 'manual'), ('DNS этой версии не задан — возможен DNS другой версии IP', 'auto'),
                     ('Не использовать DNS этой версии IP', 'none')]
    dns_method = ui.choose('Серверы DNS', dns_items, prompt=True)
    if dns_method is None:
        return original
    dns, search = (), ()
    if dns_method == 'manual':
        raw = ui.text_input('Адреса серверов DNS через пробел', limit=400)
        if raw is None:
            return original
        dns = words(raw)
    if dns_method != 'none':
        raw = ui.text_input('Домены поиска DNS через пробел — Enter без доменов', limit=400)
        if raw is None:
            return original
        search = words(raw)
    metric = ui.text_input('Метрика маршрутов — меньше означает предпочтительнее; Enter автоматически', limit=10)
    if metric is None:
        return original
    never = ui.choose('Маршрут по умолчанию', [('Использовать это соединение для интернета', False),
        ('Только локальная сеть — без маршрута по умолчанию', True)], prompt=True)
    if never is None:
        return original
    raw = ui.text_input('Дополнительные маршруты: сеть/префикс шлюз метрика; разделитель точка с запятой. Enter без маршрутов', limit=1500)
    if raw is None:
        return original
    routes = []
    for entry in raw.split(';'):
        if not entry.strip():
            continue
        parts = entry.split()
        if not 1 <= len(parts) <= 3:
            raise ValueError('Маршрут: сеть/префикс, необязательный шлюз и метрика. Для прямого маршрута шлюз — дефис.')
        routes.append(Route(parts[0], '' if len(parts) < 2 or parts[1] == '-' else parts[1],
                            int(parts[2]) if len(parts) == 3 else None))
    value = IPSettings(method, addresses, gateway, dns_method, dns, search, tuple(routes),
                       int(metric) if metric else None, never)
    value.validate_family(family)
    return value


def edit_proxy(ui, original):
    mode = ui.choose('Прокси для этого соединения', [('Без прокси', 'none'), ('Ручной прокси', 'manual')],
        'Прокси применяется к магазину Debian и новым приложениям, учитывающим переменные прокси. '
        'Приложения с собственными настройками прокси настраиваются отдельно.', prompt=True)
    if mode is None:
        return original
    if mode == 'none':
        return ProxySettings()
    values = []
    for title in ('HTTP URL прокси без пароля, например http://proxy.example:3128 — Enter не использовать',
                  'HTTPS URL прокси без пароля — Enter не использовать',
                  'Адреса и домены без прокси через пробел — Enter без исключений',
                  'Имя пользователя прокси — Enter без авторизации'):
        value = ui.text_input(title, limit=500)
        if value is None:
            return original
        values.append(value)
    return ProxySettings('manual', values[0], values[1], words(values[2]), values[3])
