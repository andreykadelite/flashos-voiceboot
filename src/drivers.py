"""Distinguish actual bound kernel drivers from merely available aliases."""
import json
from pathlib import Path
import re
import subprocess
import sys


def collect(root=Path('/sys/bus')):
    rows = []
    for bus in ('pci', 'usb'):
        for device in sorted((root / bus / 'devices').glob('*')):
            alias = device / 'modalias'
            if not alias.is_file():
                continue
            try:
                value = alias.read_text().strip()
                driver = (device / 'driver').resolve().name if (device / 'driver').is_symlink() else ''
                module = device / 'driver/module'
                builtin = bool(driver and not module.is_symlink())
                if driver:
                    modules = [module.resolve().name] if module.is_symlink() else []
                else:
                    resolved = subprocess.run(['modprobe', '--resolve-alias', value], capture_output=True,
                                              text=True, timeout=3)
                    modules = resolved.stdout.splitlines() if resolved.returncode == 0 else []
                rows.append({'bus': bus, 'address': device.name, 'alias': value, 'driver': driver,
                    'modules': modules, 'builtin': builtin,
                    'name': (device / 'product').read_text().strip() if (device / 'product').is_file() else ''})
            except (OSError, subprocess.SubprocessError) as error:
                rows.append({'bus': bus, 'address': device.name, 'error': str(error)})
    return rows


def report_job(job):
    job.update('Сопоставляю устройства с реально подключёнными драйверами.')
    _, raw = job.command([sys.executable, str(Path(__file__))], timeout=180, log_output=False)
    entries = json.loads(raw)
    bound, unbound, failures = 0, 0, 0
    for item in entries:
        title = item['bus'].upper() + ' ' + item['address']
        if 'error' in item:
            failures += 1
            job.sections.append((title, ['Не удалось прочитать сведения: ' + item['error']]))
            continue
        if item['name']:
            title += '. ' + item['name']
        active = item['driver']
        bound += bool(active)
        unbound += not bool(active)
        job.sections.append((title, ['Подключённый драйвер ядра: ' + (active or 'не подключён'),
            ('Драйвер встроен в ядро; отдельный модуль не требуется.' if item['builtin'] else
             'Модули для устройства в этой сборке: ' + (', '.join(item['modules']) or 'не найдены по идентификатору')),
            'Идентификатор устройства: ' + item['alias']]))
    job.attachments['drivers.json'] = raw
    for command, title in ((['lspci', '-nnk'], 'Названия PCI и драйверы'),
                           (['lsusb', '-t'], 'Дерево USB и драйверы'),
                           (['hwinfo', '--short', '--pci', '--usb', '--cpu', '--memory'], 'Сводка hwinfo')):
        try:
            code, output = job.command(command, timeout=45, accepted=None, log_output=False)
        except (OSError, RuntimeError) as error:
            code, output = 1, str(error)
        if code:
            failures += 1
        job.sections.append((title + ('. Недоступно' if code else ''),
                             output.splitlines() or ['Сведения недоступны.']))
        job.attachments[command[0] + '.txt'] = output
    try:
        code, output = job.command(['journalctl', '-k', '-b', '--no-pager', '-n', '4000'],
                                timeout=15, accepted=None, log_output=False, output_limit=2*1024**2)
    except (OSError, RuntimeError) as error:
        code, output = 1, str(error)
    firmware_errors = [line for line in output.splitlines() if re.search(r'firmware|sof[- _]|ucode', line, re.I)
                       and re.search(r'fail|error|not found|missing|timed out', line, re.I)]
    failures += bool(code)
    firmware_rows = list(firmware_errors)
    if code:
        firmware_rows.append('Не удалось прочитать журнал ядра. Отсутствие ошибок загрузки прошивок не подтверждено.')
    elif not firmware_rows:
        firmware_rows.append('В просмотренной части журнала ошибок прошивки не найдено. Это не проверка всех функций устройств.')
    job.sections.insert(0, ('Ошибки загрузки прошивок', firmware_rows))
    job.attachments['kernel.txt'] = output
    job.incomplete = bool(failures)
    job.warning = bool(firmware_errors or unbound)
    job.result = (f'Устройств с подключённым драйвером: {bound}. Без подключённого драйвера: {unbound}. '
        + ('Проверка журнала загрузки прошивок не завершена. ' if code else
           f'Записей об ошибках прошивки: {len(firmware_errors)}. ') +
        'Наличие драйвера не подтверждает исправность устройства. Некоторые служебные устройства не требуют отдельного драйвера.')


if __name__ == '__main__':
    print(json.dumps(collect(), ensure_ascii=False))
