"""Explicit Wi-Fi connections with NetworkManager; credentials stay in RAM."""
import json
from pathlib import Path
import re
import tempfile
import uuid

from network import addresses


def split_escaped(line):
    parts, value, escaped = [], '', False
    for character in line:
        if escaped:
            value += character
            escaped = False
        elif character == '\\':
            escaped = True
        elif character == ':':
            parts.append(value)
            value = ''
        else:
            value += character
    parts.append(value + ('\\' if escaped else ''))
    return parts


def access_points(raw):
    found = []
    for line in raw.splitlines():
        fields = split_escaped(line)
        if len(fields) != 5:
            continue
        bssid, ssid, signal, security, device = fields
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', bssid):
            continue
        found.append({'bssid': bssid, 'ssid': ssid, 'signal': int(signal) if signal.isdigit() else 0,
                      'security': security, 'device': device})
    return sorted(found, key=lambda item: (-item['signal'], item['ssid'], item['bssid']))


def scan_job(job):
    job.command(['nmcli', 'radio', 'wifi', 'on'], timeout=10, log_output=False)
    _, raw = job.command(['nmcli', '-t', '-e', 'yes', '-f', 'DEVICE,TYPE,STATE', 'device'], timeout=10, log_output=False)
    job.interfaces = [p[0] for line in raw.splitlines() if len(p := split_escaped(line)) == 3
                      and p[1] == 'wifi' and p[2] != 'unmanaged']
    job.payload = []
    if not job.interfaces:
        job.incomplete = True
        job.result = 'Wi-Fi адаптер не найден. Проверьте аппаратный переключатель или подключите USB-адаптер.'
        return
    job.update('Поиск беспроводных сетей.')
    for interface in job.interfaces:
        # Explicit device selection also avoids an unmanaged radio swallowing a scan.
        # A freshly appeared adapter can finish its first scan after nmcli returns.
        for attempt in range(3):
            code, raw = job.command(['nmcli', '--wait', '20', '-t', '-e', 'yes', '-f', 'BSSID,SSID,SIGNAL,SECURITY,DEVICE',
                'device', 'wifi', 'list', 'ifname', interface, '--rescan', 'yes'],
                timeout=25, log_output=False, accepted=None)
            job.attachments['wifi-' + interface.replace(':', '-') + '.txt'] = raw
            points = access_points(raw)
            if code:
                job.incomplete = True
                job.log('Сканирование не удалось: ' + interface + '. Проверьте переключатель Wi-Fi.')
                break
            if points:
                job.payload.extend(points)
                break
            if attempt < 2:
                job.cancel_event.wait(2)
                job.check()
    job.payload.sort(key=lambda item: (-item['signal'], item['ssid'], item['bssid']))
    job.result = f'Найдено точек доступа: {len(job.payload)}.'


def connect_job(point, password):
    def run(job):
        name = 'flashos-wifi-' + uuid.uuid4().hex
        identifier = str(uuid.uuid4())
        interface = point['device']
        security = point['security']
        if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', interface) or not 1 <= len(point['ssid'].encode('utf-8')) <= 32:
            raise ValueError('Недопустимое имя устройства или сети.')
        if any(word in security for word in ('802.1X', 'EAP', 'WEP')):
            raise ValueError('Этот способ входа пока не поддерживается. Доступны открытые сети и WPA2/WPA3 Personal.')
        protected = 'WPA' in security
        if protected and (not password or '\n' in password or '\r' in password):
            raise ValueError('Введите пароль сети одной строкой.')
        command = ['nmcli', 'connection', 'add', 'save', 'no', 'type', 'wifi', 'ifname', interface,
            'con-name', name, 'connection.uuid', identifier, 'connection.autoconnect', 'no',
            'ssid', point['ssid'], '802-11-wireless.hidden', 'yes' if not point.get('bssid') else 'no',
            'ipv4.method', 'auto', 'ipv6.method', 'auto']
        if point.get('bssid'):
            command += ['802-11-wireless.bssid', point['bssid']]
        if protected:
            command += ['wifi-sec.key-mgmt', 'sae' if 'WPA3' in security and 'WPA2' not in security else 'wpa-psk',
                        # save no keeps the PSK in RAM so a later checkpoint can
                        # reconnect this connection after its nmcli agent exits.
                        'wifi-sec.psk-flags', '0']
        success = False
        try:
            job.command(command, timeout=15, log_output=False)
            job.update('Подключение и получение сетевого адреса.')
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='flashos-wifi-', dir='/run') as secret:
                if protected:
                    secret.write('802-11-wireless-security.psk:' + password + '\n')
                secret.flush()
                code, _ = job.command(['nmcli', '--wait', '40', 'connection', 'up', 'uuid', identifier,
                        'ifname', interface, 'passwd-file', secret.name], timeout=45, accepted=None, log_output=False)
            if code:
                raise RuntimeError('Соединение не установлено. Проверьте пароль, сигнал и блокировку Wi-Fi. Пароль не сохранён.')
            _, raw = job.command(['ip', '-j', 'address', 'show', 'dev', interface], timeout=10, log_output=False)
            assigned = [address for _, address, _ in addresses(json.loads(raw))]
            if not assigned:
                raise RuntimeError('Сеть не выдала адрес. Соединение отключено.')
            job.lines.extend(['Сеть: ' + point['ssid'], 'Устройство: ' + interface, 'Адрес: ' + ', '.join(assigned),
                              'Пароль и профиль действуют до перезагрузки. Интернет проверяется в диагностике сети.'])
            job.result = 'Wi-Fi подключён. Получен сетевой адрес.'
            success = True
        finally:
            if not success:
                job.command(['nmcli', 'connection', 'delete', 'uuid', identifier], timeout=10,
                            accepted=None, log_output=False, cleanup=True)
    return run


def wifi_menu(ui):
    while True:
        scan = ui.run_job('Поиск Wi-Fi', scan_job, review=False, save=False)
        if scan.failed or scan.cancelled or not getattr(scan, 'interfaces', []):
            return
        choices = [(f"{p['ssid'] or 'Скрытая сеть'}. Сигнал {p['signal']} процентов. {p['security'] or 'Открытая'}",
                    p, 'Адаптер ' + p['device'] + '. Точка ' + p['bssid']) for p in scan.payload if p['ssid']]
        choices += [('Повторить поиск', '__refresh__'), ('Ввести скрытую сеть', 'hidden')]
        point = ui.choose('Доступные сети Wi-Fi', choices,
            'Стрелки — выбор сети. F3 — адаптер и точка доступа. F5 — повторить поиск. Escape — назад.', refresh=True)
        if point is None:
            return
        if point != '__refresh__':
            break
    if point == 'hidden':
        interface = ui.choose('Адаптер Wi-Fi', [(name, name) for name in scan.interfaces])
        if not interface:
            return
        ssid = ui.text_input('Имя скрытой сети')
        if not ssid:
            return
        security = ui.choose('Защита сети', [('WPA2 Personal', 'WPA2'), ('WPA3 Personal', 'WPA3'), ('Открытая сеть', '')])
        if security is None:
            return
        point = {'ssid': ssid, 'security': security, 'device': interface, 'bssid': ''}
    if any(word in point['security'] for word in ('802.1X', 'EAP', 'WEP')):
        ui.message('Подключение Wi-Fi', 'Для этой сети нужен другой способ входа. Поддерживаются WPA2/WPA3 Personal и открытые сети.')
        return
    password = ui.text_input('Пароль Wi-Fi', secret=True, limit=64) if 'WPA' in point['security'] else ''
    if password is not None:
        ui.run_job('Подключение Wi-Fi', connect_job(point, password))
