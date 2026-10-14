"""Explicit Wi-Fi connections with NetworkManager; credentials stay in RAM."""
import json
from pathlib import Path
import re
import tempfile
import uuid

from network import request, wireless_interfaces
from network_runtime import INTERFACE, NetworkManager, _verify_addresses, rollback_temporary_connection
from wifi_hardware import recover_missing_interfaces


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
        if (not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', bssid)
                or not INTERFACE.fullmatch(device)):
            continue
        found.append({'bssid': bssid, 'ssid': ssid, 'signal': int(signal) if signal.isdigit() else 0,
                      'security': security, 'device': device})
    return sorted(found, key=lambda item: (-item['signal'], item['ssid'], item['bssid']))


def radio_blocks(sys_root='/sys'):
    """Kernel rfkill flags distinguish a disabled radio from missing hardware."""
    hard, soft = False, False
    try:
        entries = list((Path(sys_root) / 'class/rfkill').iterdir())
    except OSError:
        return hard, soft
    for entry in entries:
        try:
            if (entry / 'type').read_text().strip() != 'wlan':
                continue
            hard = hard or (entry / 'hard').read_text().strip() == '1'
            soft = soft or (entry / 'soft').read_text().strip() == '1'
        except OSError:
            continue
    return hard, soft


def recovery_report(job, report):
    """Keep discovery/load evidence even when no interface can be recovered."""
    job.wifi_recovery = report
    job.attachments['wifi-recovery.json'] = json.dumps(report, ensure_ascii=False, indent=2)
    labels = {
        'driver_already_bound': 'драйвер уже привязан к устройству',
        'module_already_loaded': 'модуль уже загружен',
        'module_loaded': 'модуль загружен при повторной проверке',
        'load_failed': 'загрузка модуля завершилась ошибкой',
        'load_not_confirmed': 'загрузка модуля не подтверждена',
        'blacklisted_or_unavailable': 'модуль исключён из загрузки или недоступен',
        'unexpected_load_plan': 'способ загрузки модуля не прошёл проверку',
        'device_changed': 'устройство изменилось во время проверки',
        'device_became_active': 'устройство стало активным во время проверки',
        'attempt_limit': 'достигнут предел попыток загрузки',
    }
    rows = []
    for item in report.get('devices', []):
        rows += [item['bus'].upper() + ' ' + item['address'] + '. Модуль: ' + item['module'] + '. ' +
                 labels.get(item.get('status'), 'Сопоставление драйвера выполнено.'),
                 'Подключённый драйвер: ' + (item.get('driver') or 'не подключён'),
                 'Идентификатор: ' + item['alias']]
        if item.get('load_error'):
            rows.append('Ошибка загрузки: ' + item['load_error'])
    rows += report.get('errors', [])
    job.sections.append(('Проверка загрузки драйверов Wi-Fi', rows or ['Подходящие устройства для повторной загрузки не найдены.']))


def scan_diagnostics(job, devices):
    """Collect bounded, read-only evidence before diagnosing absent interfaces."""
    job.sections.append(('Wi-Fi в NetworkManager',
        [name + ': ' + state for name, state in devices] or ['Wi-Fi устройства не перечислены.']))
    try:
        kernel_devices = wireless_interfaces()
        kernel_present = bool(kernel_devices)
        kernel_rows = kernel_devices or ['Беспроводные интерфейсы не зарегистрированы.']
    except OSError:
        kernel_devices = []
        kernel_present = None
        kernel_rows = ['Не удалось прочитать беспроводные интерфейсы sysfs.']
    job.sections.append(('Беспроводные интерфейсы sysfs', kernel_rows))
    driver_rows = []
    for interface in kernel_devices:
        device = Path('/sys/class/net') / interface / 'device'
        try:
            alias = (device / 'modalias').read_text().strip()
            driver = (device / 'driver').resolve(strict=True).name if (device / 'driver').is_symlink() else ''
            driver_rows += [interface + ': драйвер ' + (driver or 'не подключён'), 'modalias: ' + alias]
        except OSError:
            driver_rows.append(interface + ': сведения о драйвере недоступны.')
    job.sections.append(('Привязка драйверов Wi-Fi в sysfs', driver_rows or ['Сетевые интерфейсы Wi-Fi не перечислены.']))
    for title, filename, command in (
            ('Версия работающего ядра', 'wifi-kernel-version.txt', ['uname', '-r']),
            ('Состояние службы сети и радио', 'wifi-nm-status.txt',
             ['nmcli', '-t', '-f', 'RUNNING,STATE,WIFI-HW,WIFI', 'general', 'status']),
            ('Причины состояния устройств NetworkManager', 'wifi-nm-devices.txt',
             ['nmcli', '-f', 'GENERAL.DEVICE,GENERAL.TYPE,GENERAL.DRIVER,GENERAL.DRIVER-VERSION,'
              'GENERAL.FIRMWARE-VERSION,GENERAL.NM-MANAGED,GENERAL.STATE,GENERAL.REASON,GENERAL.FIRMWARE-MISSING',
              'device', 'show']),
            ('Беспроводные интерфейсы ядра', 'wifi-interfaces.txt', ['iw', 'dev']),
            ('Блокировки радио', 'wifi-rfkill.txt', ['rfkill', 'list']),
            ('Устройства PCI и драйверы', 'wifi-pci.txt', ['lspci', '-nnk']),
            ('Устройства USB', 'wifi-usb.txt', ['lsusb']),
            ('Службы обнаружения устройств', 'wifi-device-services.txt',
             ['systemctl', 'show', '--property=Id,LoadState,ActiveState,SubState,Result',
              'NetworkManager.service', 'systemd-udevd.service', 'systemd-udev-trigger.service']),
            ('Ошибки обнаружения устройств', 'wifi-udev.txt',
             ['journalctl', '-b', '--no-pager', '-p', 'warning', '-n', '120',
              '-u', 'systemd-udevd.service', '-u', 'systemd-udev-trigger.service'])):
        code, raw = request(job, command, timeout=8)
        job.attachments[filename] = raw
        job.sections.append((title, raw.splitlines() or ['Сведения недоступны.' if code else 'Нет записей.']))
        if command == ['iw', 'dev'] and code == 0:
            kernel_present = kernel_present or bool(re.search(r'^\s*Interface\s+\S+', raw, re.MULTILINE))
    # Firmware/probe failures can precede many unrelated warnings. Read all
    # priorities, then retain hardware lines without application/profile logs.
    code, raw = request(job, ['journalctl', '-k', '-b', '--no-pager', '-n', '4000'],
                        timeout=15, output_limit=2 * 1024**2)
    pattern = r'\b(?:iwl\w*|ath\w*|rtw\w*|rtl\w*|mt7\w*|brcm\w*|cfg80211|mac80211|rfkill|wiphy|wireless|wlan\w*|firmware|pci\w*|usb\w*)\b'
    hardware_lines = [line for line in raw.splitlines() if re.search(pattern, line, re.I)]
    job.attachments['wifi-kernel.txt'] = '\n'.join(hardware_lines) if code == 0 else raw
    job.sections.append(('Журнал драйверов и прошивок при загрузке', hardware_lines if code == 0 and hardware_lines else
                         [raw if code else 'В просмотренной части журнала нет записей о беспроводных устройствах или прошивках.']))
    hard, soft = radio_blocks()
    if hard:
        return 'Wi-Fi отключён аппаратно. Проверьте переключатель, клавишу Wi-Fi и настройки BIOS/UEFI.'
    if soft:
        return 'Wi-Fi заблокирован программно. Проверьте режим полёта и блокировки радио в подробностях.'
    if devices and all(state == 'unmanaged' for _, state in devices):
        return 'Wi-Fi адаптер обнаружен, но не управляется NetworkManager. Откройте подробности диагностики сети.'
    if devices:
        return 'Wi-Fi адаптер обнаружен, но поиск сетей не завершён. Проверьте состояние адаптера в подробностях.'
    if kernel_present:
        return 'Драйвер создал Wi-Fi интерфейс, но NetworkManager его не перечислил. Откройте подробности диагностики.'
    if kernel_present is None:
        return 'Не удалось проверить беспроводные интерфейсы ядра. Откройте сведения PCI, USB и ошибки в подробностях.'
    recovery = getattr(job, 'wifi_recovery', {})
    if recovery.get('loaded') or any(item.get('driver') or item.get('already_loaded')
                                   for item in recovery.get('devices', [])):
        return ('Ядро не зарегистрировало Wi-Fi интерфейс. Сопоставленный драйвер присутствует, '
                'но интерфейс не создан. Откройте журнал драйверов и прошивок в подробностях.')
    return ('Ядро не зарегистрировало Wi-Fi интерфейс. Возможны отсутствующий драйвер, прошивка или отключённое устройство. '
            'Откройте сведения PCI, USB и ошибки ядра в подробностях.')


def scan_job(job):
    job.interfaces, job.payload = [], []
    radio_code, radio_raw = request(job, ['nmcli', 'radio', 'wifi', 'on'], timeout=10)
    # Radio enablement and hotplug discovery are asynchronous. Retry inventory
    # briefly before diagnosing an absent interface or an unready NM service.
    for attempt in range(3):
        code, raw = request(job, ['nmcli', '-t', '-e', 'yes', '-f', 'DEVICE,TYPE,STATE', 'device'], timeout=10)
        devices = [(p[0], p[2]) for line in raw.splitlines() if len(p := split_escaped(line)) == 3
                   and p[1] == 'wifi' and INTERFACE.fullmatch(p[0])] if code == 0 else []
        if devices:
            break
        if attempt == 1 and code == 0:
            try:
                missing = not wireless_interfaces()
            except OSError:
                missing = False
            if missing:
                job.update('Проверка автоматической загрузки драйверов Wi-Fi.')
                recovery_report(job, recover_missing_interfaces(job))
        if attempt < 2:
            job.cancel_event.wait(1)
            job.check()
    job.attachments['wifi-devices.txt'] = raw
    job.interfaces = [name for name, state in devices if state != 'unmanaged']
    if code:
        job.failed = True
        job.sections.append(('Ошибка NetworkManager', raw.splitlines() or ['Нет ответа.']))
        scan_diagnostics(job, devices)
        job.result = 'Не удалось получить устройства NetworkManager. Проверьте службу в подробностях диагностики сети.'
        return
    if not job.interfaces:
        job.incomplete = True
        job.result = scan_diagnostics(job, devices)
        return
    if radio_code:
        job.incomplete = True
        job.attachments['wifi-radio.txt'] = radio_raw
        job.sections.append(('Включение Wi-Fi', radio_raw.splitlines() or ['Команда не выполнена.']))
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
                if attempt < 2:
                    job.cancel_event.wait(2)
                    job.check()
                    continue
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
    if job.incomplete:
        diagnosis = scan_diagnostics(job, devices)
        job.result = f'Найдено точек доступа: {len(job.payload)}. ' + diagnosis
    else:
        job.result = (f'Найдено точек доступа: {len(job.payload)}.' if job.payload else
                      'Wi-Fi адаптер обнаружен, но сети не найдены. Повторите поиск или введите скрытую сеть.')


def connect_job(point, password):
    def run(job):
        name = 'flashos-wifi-' + uuid.uuid4().hex
        identifier = str(uuid.uuid4())
        interface = point['device']
        security = point['security']
        if not INTERFACE.fullmatch(interface) or not 1 <= len(point['ssid'].encode('utf-8')) <= 32:
            raise ValueError('Недопустимое имя устройства или сети.')
        if any(word in security for word in ('802.1X', 'EAP', 'WEP')):
            raise ValueError('Этот способ входа пока не поддерживается. Доступны открытые сети и WPA2/WPA3 Personal.')
        protected = 'WPA' in security
        if protected and (not isinstance(password, str) or not password or len(password) > 128
                          or any(character in password for character in ('\n', '\r', '\0'))):
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
        manager = NetworkManager(job)
        previous = manager.active_uuid(interface)
        checkpoint = manager.checkpoint(manager.device_path(interface), 90)
        try:
            job.command(command, timeout=15, log_output=False, resource='network')
            job.update('Подключение и получение сетевого адреса.')
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='flashos-wifi-', dir='/run') as secret:
                if protected:
                    secret.write('802-11-wireless-security.psk:' + password + '\n')
                secret.flush()
                code, _ = job.command(['nmcli', '--wait', '40', 'connection', 'up', 'uuid', identifier,
                        'ifname', interface, 'passwd-file', secret.name], timeout=45, accepted=None,
                        log_output=False, resource='network')
            if code:
                raise RuntimeError('Соединение не установлено. Проверьте пароль, сигнал и блокировку Wi-Fi. Пароль не сохранён.')
            assigned = _verify_addresses(manager, None, interface)
            job.check()
            manager.destroy(checkpoint)
            job.lines.extend(['Сеть: ' + point['ssid'], 'Устройство: ' + interface, 'Адрес: ' + ', '.join(assigned),
                              'Пароль и профиль действуют до перезагрузки. Интернет проверяется в диагностике сети.'])
            job.result = 'Wi-Fi подключён. Получен сетевой адрес.'
        except BaseException:
            if not rollback_temporary_connection(manager, interface, identifier, checkpoint, previous):
                job.log('Восстановление предыдущей сети не подтверждено. Откройте диагностику сети.')
            raise
    return run


def wifi_menu(ui):
    while True:
        scan = ui.run_job('Поиск Wi-Fi', scan_job, review=False, save=True)
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
