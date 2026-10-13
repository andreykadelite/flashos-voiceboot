"""Layered network checks; a missing ping reply alone is not a diagnosis."""
import json
import ipaddress
from pathlib import Path
import re


def network_tool(ui, action):
    """Use the same explicit connection actions from Tools and the store."""
    if ui.demo:
        ui.message('Демонстрация', 'Этот инструмент доступен после загрузки с флешки.')
        return
    if action == 'wifi':
        from wifi import wifi_menu
        wifi_menu(ui)
    elif action == 'dhcp':
        interfaces = wired_interfaces()
        if not interfaces:
            ui.message('Проводная сеть', 'Проводной сетевой адаптер не найден. '
                       'Подключите сетевой кабель и проверьте адаптер в сведениях о компьютере.')
            return
        name = ui.choose('Проводное сетевое устройство', [(name, name) for name in interfaces],
                         'Выберите адаптер для получения адреса по DHCP. Escape — отменить.', prompt=True)
        if name:
            ui.run_job('Подключение сети', dhcp_job(name))
    elif action == 'network':
        ui.run_job('Проверка сети', network_job)
    elif action == 'target':
        target = ui.text_input('Имя компьютера, сайта или IP-адрес')
        if target:
            ui.run_job('Проверка узла', target_job(target))
    elif action == 'profiles':
        from network_settings import manager
        manager(ui).main()
    else:
        raise ValueError('Неизвестный сетевой инструмент.')


def network_menu(ui):
    """Return to the caller after connecting; it decides which operation to retry."""
    title = 'Инструменты — подключение к сети'
    items = [
        ('Подключить Wi-Fi', 'wifi',
         'Выберите сеть и введите пароль. Поддерживаются открытые сети и WPA2/WPA3 Personal.'),
        ('Подключить проводную сеть по DHCP', 'dhcp',
         'Подключите сетевой кабель, затем выберите адаптер. Адрес выдаёт ваша сеть.'),
        ('Настройки IP, DNS, прокси и сохранённые профили', 'profiles',
         'IPv4 и IPv6, DHCP и статика, маршруты, Wi-Fi, зашифрованные пароли и привязка к компьютеру.'),
        ('Проверить соединение, DNS и доступ в интернет', 'network',
         'Проверка адресов, маршрутов, DNS и HTTPS. Получение адреса ещё не подтверждает доступ в интернет.'),
        ('Проверить адрес, DNS и маршрут к узлу', 'target',
         'Введите имя сайта или IP-адрес для подробной проверки.'),
        ('Вернуться к предыдущему экрану', 'back',
         'Также можно нажать Escape. В магазине после возвращения можно повторить загрузку.'),
    ]
    while True:
        action = ui.choose(title, items,
            'Быстрое подключение действует до перезагрузки. Сохранённые параметры доступны в сетевых профилях. '
            'При необходимости проверьте DNS и доступ в интернет. '
            'Escape — вернуться к предыдущему экрану.', prompt=True)
        if action is None or action == 'back':
            return
        try:
            network_tool(ui, action)
        except (OSError, ValueError, RuntimeError) as error:
            ui.message('Подключение к сети', str(error))


def request(job, command, timeout=15):
    try:
        return job.command(command, timeout=timeout, accepted=None, log_output=False)
    except (OSError, RuntimeError) as error:
        return 1, str(error)


def addresses(data):
    return [(nic['ifname'], addr['local'], addr.get('family')) for nic in data if nic.get('ifname') != 'lo'
            for addr in nic.get('addr_info', []) if addr.get('scope') == 'global'
            and not ipaddress.ip_address(addr['local']).is_link_local]


def wired_interfaces():
    return [path.name for path in sorted(Path('/sys/class/net').iterdir())
            if (path / 'device').exists() and not (path / 'wireless').exists()]


def dhcp_job(name):
    from network_runtime import temporary_dhcp_job
    return temporary_dhcp_job(name)


def target_job(target):
    target = target.strip()
    try:
        target = str(ipaddress.ip_address(target))
        literal = True
    except ValueError:
        target = target.rstrip('.').encode('idna').decode('ascii')
        literal = False
        if len(target) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',
                                                    label) for label in target.split('.')):
            raise ValueError('Введите имя или IP-адрес без протокола, пути и номера порта.')
    def run(job):
        job.log('Проверяемый узел: ' + target)
        commands = [('Ответ на ping', ['ping', '-n', '-c', '3', '-W', '2', '--', target], 12),
                    ('Маршрут к узлу', ['traceroute', '-n', '-m', '12', '-q', '1', '-w', '1', '--', target], 20)]
        if not literal:
            commands = [('Адреса IPv4 в DNS', ['dig', '+time=3', '+tries=1', target, 'A'], 8),
                        ('Адреса IPv6 в DNS', ['dig', '+time=3', '+tries=1', target, 'AAAA'], 8)] + commands
        for index, (title, command, timeout) in enumerate(commands):
            job.update(title)
            code, raw = request(job, command, timeout)
            job.sections.append((title, raw.splitlines() or ['Нет ответа.']))
            job.attachments[f'network-{index}.txt'] = raw
            if code or 'status: NXDOMAIN' in raw or 'status: SERVFAIL' in raw:
                job.incomplete = True
        job.result = ('Часть запросов не дала ответа. Откройте подробности.' if job.incomplete else
                      'Запросы выполнены. Ответы доступны по разделам.')
        job.log('Отсутствие ответа ping или промежуточного узла может означать фильтрацию. Это не доказывает неисправность узла.')
        job.update('Измерение потерь и задержек по маршруту, десять запросов.')
        code, raw = request(job, ['mtr', '--json', '--no-dns', '--report-cycles', '10', '--max-ttl', '12', '--', target], 35)
        job.attachments['mtr.json'] = raw
        rows = []
        try:
            for hop in json.loads(raw)['report']['hubs']:
                rows.append(f"Узел {hop.get('count', '?')}: {hop.get('host', '?')}. "
                    f"Потери: {hop.get('Loss%', '?')} процентов. Отправлено: {hop.get('Snt', '?')}. "
                    f"Средняя задержка: {hop.get('Avg', '?')} миллисекунд. Максимальная: {hop.get('Wrst', '?')}.")
        except (ValueError, KeyError, TypeError):
            code = 1
        job.sections.append(('Потери и задержки MTR', rows or ['Сведения недоступны.']))
        if code:
            job.incomplete = True
            job.result = 'Часть запросов не дала результата. Откройте подробности.'
    return run


def network_job(job):
    job.update('Интерфейсы и адреса.')
    code, raw = request(job, ['ip', '-j', 'address'])
    try:
        data = json.loads(raw) if code == 0 else []
        assigned = addresses(data)
    except (ValueError, KeyError, TypeError):
        data, assigned = [], []
    local = []
    for nic in data:
        if nic.get('ifname') == 'lo':
            continue
        flags = nic.get('flags', [])
        local.append(nic['ifname'] + ': ' + ('соединение обнаружено' if 'LOWER_UP' in flags else 'нет подтверждения соединения'))
    local += [f'{interface}: {address}' for interface, address, family in assigned]
    routes = []
    default = False
    for flag in ('-4', '-6'):
        code, raw = request(job, ['ip', flag, '-j', 'route'])
        try:
            entries = json.loads(raw) if code == 0 else []
            default = default or any(entry.get('dst') == 'default' for entry in entries)
            routes += [f"{flag[1:]}: {entry.get('dst', '')}, через {entry.get('gateway', 'напрямую')}, {entry.get('dev', '')}"
                       for entry in entries]
        except (ValueError, TypeError):
            pass
    job.sections = [('Адреса и состояние интерфейсов', local or ['Сетевые устройства не найдены.']),
                    ('Маршруты IPv4 и IPv6', routes or ['Маршруты отсутствуют.'])]
    physical = []
    inventory_missing = False
    try:
        interfaces = wired_interfaces()
    except OSError:
        interfaces = []
        inventory_missing = True
        physical.append('Не удалось прочитать список физических сетевых устройств.')
    for name in interfaces:
        for options in ([name], ['-i', name], ['-S', name]):
            code, raw = request(job, ['ethtool', *options], timeout=8)
            physical += [name, *raw.splitlines()]
    for command in (['iw', 'dev'], ['rfkill', 'list']):
        _, raw = request(job, command, timeout=8)
        physical += raw.splitlines()
    job.sections.append(('Драйверы, физическая сеть и Wi-Fi', physical or ['Нет данных.']))
    if not assigned or not default:
        job.failed = True
        job.result = 'Нет сетевого адреса.' if not assigned else 'Нет маршрута по умолчанию.'
        return
    job.update('Доступ по IP, DNS и HTTPS.')
    ping4, _ = request(job, ['ping', '-4', '-n', '-c', '1', '-W', '2', '1.1.1.1'], timeout=5)
    ping6, _ = request(job, ['ping', '-6', '-n', '-c', '1', '-W', '2', '2606:4700:4700::1111'], timeout=5)
    dns, _ = request(job, ['getent', 'ahosts', 'example.com'])
    https, detail = request(job, ['curl', '-I', '--fail', '--silent', '--show-error', '--connect-timeout', '5',
                                  '--max-time', '10', 'https://example.com/'])
    job.lines.extend(['Ответ ICMP IPv4: ' + ('получен' if ping4 == 0 else 'не получен'),
                      'Ответ ICMP IPv6: ' + ('получен' if ping6 == 0 else 'не получен'),
                      'DNS: ' + ('имя разрешено' if dns == 0 else 'нет ответа'),
                      'HTTPS example.com: ' + ('соединение установлено' if https == 0 else 'не удалось'),
                      'ICMP может блокироваться. Результат относится к указанным адресам на момент проверки.'])
    job.sections.append(('Подробности HTTPS', detail.splitlines()))
    job.failed = https != 0 and dns != 0 and ping4 != 0 and ping6 != 0
    job.incomplete = https != 0 or dns != 0 or inventory_missing
    job.result = ('Сеть: DNS и HTTPS работают.' if https == 0 and dns == 0 else
                  'Внешнее соединение не подтверждено.' if job.failed else 'Сеть работает частично. Откройте подробности.')
