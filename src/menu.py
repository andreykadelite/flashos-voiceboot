#!/usr/bin/env python3
"""Speaking administration environment for the FlashOS USB."""
import argparse
import curses
import json
import os
from pathlib import Path
import re
import subprocess
import time

from audio_diagnostics import save_async
from catalog import discover
from interface import UI, OpenPower
from help_content import help_menu
from power import installed_entries, reboot_once
from selection import arm, cancel, initialize
from settings import Settings, VOICES, DEFAULTS
from speech import Speech
import toolkit
import smart
from network import network_menu, network_tool

def return_to_usb():
    """Request the current UEFI boot entry once; never change BootOrder."""
    if not Path('/sys/firmware/efi').exists():
        return False
    try:
        result = subprocess.run(['efibootmgr'], capture_output=True, text=True, check=True, timeout=5)
        current = re.search(r'^BootCurrent: ([0-9A-Fa-f]{4})$', result.stdout, re.M)
        if not current:
            return False
        subprocess.run(['efibootmgr', '-n', current[1]], check=True, capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def prepare_medium(root):
    if not (root / 'flashos/flashos.marker').is_file():
        raise RuntimeError('Не найден системный раздел флешки')
    mounted = json.loads(subprocess.check_output(['findmnt', '-J', '-M', str(root), '-o', 'SOURCE,FSTYPE,OPTIONS'], timeout=5))
    fs = mounted['filesystems'][0]
    if fs['fstype'] not in ('exfat', 'vfat', 'ntfs3', 'ntfs', 'fuseblk'):
        raise RuntimeError('Неожиданная файловая система загрузочного раздела')
    source = str(Path(fs['source']).resolve())
    parent = subprocess.check_output(['lsblk', '-ndo', 'PKNAME', source], text=True, timeout=5).strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', parent):
        raise RuntimeError('Не удалось определить загрузочную флешку')
    listing = json.loads(subprocess.check_output(['lsblk', '--tree', '-J', '-o', 'PATH,FSTYPE', '/dev/' + parent], timeout=5))
    candidates = []
    for item in listing['blockdevices'][0].get('children', []):
        part = Path('/sys/class/block', Path(item['path']).name, 'partition')
        if part.is_file() and part.read_text().strip() == '3' and item['fstype'] == 'vfat':
            candidates.append(item['path'])
    if len(candidates) != 1:
        raise RuntimeError('Не найден третий служебный раздел флешки')
    state_root = Path('/run/flashos-boot')
    state_root.mkdir(exist_ok=True)
    existing = subprocess.run(['findmnt', '-J', '-M', str(state_root), '-o', 'SOURCE,FSTYPE'], capture_output=True, text=True, timeout=5)
    if existing.returncode == 0:
        state_fs = json.loads(existing.stdout)['filesystems'][0]
        if str(Path(state_fs['source']).resolve()) != str(Path(candidates[0]).resolve()) or state_fs['fstype'] != 'vfat':
            raise RuntimeError('Служебная папка занята другим накопителем')
    else:
        subprocess.run(['mount', '-t', 'vfat', '-o', 'rw,nosuid,nodev,noexec', candidates[0], str(state_root)], check=True, timeout=15)
    if not (state_root / 'flashos/flashos.marker').is_file():
        raise RuntimeError('Служебный раздел не принадлежит FlashOS')
    initialize(state_root)
    return state_root



def live_disk(root):
    source = json.loads(subprocess.check_output(
        ['findmnt', '-J', '-M', str(root), '-o', 'SOURCE'], timeout=5))['filesystems'][0]['source']
    parent = subprocess.check_output(['lsblk', '-ndo', 'PKNAME', str(Path(source).resolve())], text=True, timeout=5).strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', parent):
        raise ValueError('Не удалось определить загрузочный диск')
    return '/dev/' + parent


def choose_device(ui, disk, kind):
    choices = toolkit.devices(disk, kind)
    return ui.choose('Выбор накопителя', [(item.title, item, item.details) for item in choices],
                     'Стрелки — выбор. F3 — полные идентификаторы устройства. Enter — продолжить. Escape — назад.')


def iso_menu(ui, root, checksums=False):
    while True:
        images = discover(root, 'uefi' if Path('/sys/firmware/efi').exists() else 'bios')
        selected = ui.choose('Контрольная сумма ISO' if checksums else 'Запустить систему',
            [(image.title + ('. ' + image.reason if not image.bootable else ''), image) for image in images],
            'Стрелки — выбор образа. Enter — ' + ('проверка суммы. ' if checksums else 'запуск. ') +
            'F5 перечитывает папку ISO. Escape — главное меню.', refresh=True)
        if selected is None:
            return
        if selected == '__refresh__':
            continue
        if checksums:
            ui.run_job('SHA256 образа', toolkit.checksum_job(selected))
        elif not selected.bootable:
            ui.message('Запуск невозможен', selected.reason)
        elif ui.demo:
            ui.message('Демонстрация', 'Выбрана система ' + selected.title)
        else:
            try:
                arm(root, selected, ui.state_root)
                next_boot = return_to_usb()
                tip = 'Дождитесь установщика.' if next_boot else 'Если потребуется, снова выберите USB в меню компьютера.'
                ui.speech.say('Запускаю ' + selected.title + '. Компьютер перезагрузится. ' + tip)
                subprocess.run(['systemctl', 'reboot'], check=True, timeout=15)
                return
            except (OSError, ValueError, subprocess.SubprocessError):
                cancel(ui.state_root)
                raise


def hardware_menu(ui):
    while True:
        key = ui.choose('Информация о компьютере',
            [(value[0], key) for key, value in toolkit.REPORTS.items()] +
            [('Батарея и температуры', 'temperature')])
        if key is None:
            return
        ui.run_job('Батарея и температуры' if key == 'temperature' else toolkit.REPORTS[key][0],
                   toolkit.temperature_job if key == 'temperature' else toolkit.report_job(key))


def tools_menu(ui, root, disk):
    while True:
        key = ui.choose('Инструменты', [
            ('Полная информация о компьютере', 'hardware'),
            ('Терминал с озвучкой команд и вывода', 'terminal'),
            ('Файлы: просмотр и копирование на флешку', 'files'),
            ('Контрольная сумма ISO', 'checksum'),
            ('Сохранить таблицу разделов', 'table'),
            ('Скопировать накопитель в файл образа', 'rescue'),
            ('Резервные копии, восстановление и ремонт', 'recovery'),
            ('Проверить драйверы и прошивки устройств', 'drivers'),
            ('Подключение к сети и проверка интернета', 'connection'),
            ('Подключить проводную сеть по DHCP', 'dhcp'),
            ('Подключить Wi-Fi', 'wifi'),
            ('Проверить адрес, DNS и маршрут', 'target'),
            ('Просмотреть сохранённые отчёты', 'reports'),
            ('Выгрузить отчёты на основной раздел флешки', 'export'),
        ])
        if key is None:
            return
        if key == 'hardware':
            hardware_menu(ui)
        elif key == 'terminal':
            from terminal import launch
            launch(ui, root)
        elif key == 'recovery':
            from recovery import recovery_menu
            recovery_menu(ui, root, disk)
        elif key == 'drivers':
            from drivers import report_job
            if not ui.demo:
                ui.run_job('Драйверы и прошивки устройств', report_job)
        elif key == 'connection':
            network_menu(ui)
        elif key in ('dhcp', 'wifi', 'target'):
            network_tool(ui, key)
        elif key in ('files', 'export'):
            if ui.demo:
                ui.message('Демонстрация', 'Этот инструмент доступен после загрузки с флешки.')
                continue
            if key == 'files':
                from files import browser_menu
                browser_menu(ui, root, disk)
            else:
                from files import export_job
                ui.run_job('Выгрузка отчётов', export_job(ui.state_root, root, disk))
        elif key == 'checksum':
            iso_menu(ui, root, checksums=True)
        elif key == 'reports':
            folder = ui.state_root / 'flashos/reports'
            files = [path for path in sorted(folder.glob('*.txt'), reverse=True)
                     if re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{6}\.txt', path.name) and not path.is_symlink()]
            choices = []
            for path in files:
                try:
                    with path.open(encoding='utf-8', errors='replace') as stream:
                        title = stream.readline(400).strip()
                    stamp = path.name
                    choices.append((f'{title}. {stamp[6:8]}.{stamp[4:6]}. {stamp[9:11]}:{stamp[11:13]}', path))
                except OSError:
                    continue
            selected = ui.choose('Сохранённые отчёты', choices)
            if selected is not None:
                if selected.is_symlink() or selected.stat().st_size > 2 * 1024**2:
                    raise ValueError('Недопустимый файл отчёта')
                ui.view(selected.name, selected.read_text(encoding='utf-8', errors='replace').splitlines())
        else:
            if ui.demo:
                ui.message('Демонстрация', 'Действия с устройствами доступны после загрузки с флешки.')
                continue
            device = choose_device(ui, disk, 'disk' if key == 'table' else None)
            if device is None:
                continue
            if key == 'table':
                ui.run_job('Копия таблицы разделов', toolkit.backup_table_job(device, disk))
            elif ui.confirm('Скопировать ' + device.title + '. ' + device.details + ' в папку recovery на флешке?'):
                ui.run_job('Копирование накопителя', toolkit.rescue_job(device, disk, root))


def diagnostics_menu(ui, disk):
    while True:
        key = ui.choose('Диагностика', [
            ('Проверка оперативной памяти', 'memory'),
            ('Нагрузочная проверка процессора', 'cpu'),
            ('Состояние диска SMART и NVMe', 'smart'),
            ('Выборочная проверка чтения диска', 'read'),
            ('Проверка файловой системы без исправления', 'filesystem'),
            ('Проверка сети и DNS', 'network'),
            ('Батарея и температуры', 'temperature'),
            ('Проверка звука: левый и правый каналы', 'sound'),
            ('Проверка клавиатуры', 'keyboard'),
            ('Ошибки ядра и устройств', 'kernel'),
            ('Подключённые драйверы и ошибки прошивок', 'drivers'),
        ])
        if key is None:
            return
        if key == 'keyboard':
            ui.keyboard_test()
            continue
        if key == 'sound':
            ui.speak_wait('Сейчас сигнал в левом канале.', 'Проверка звука')
            left = ui.speech.signal('success', 'left', force=True, wait=True)
            ui.speak_wait('Теперь в правом канале.', 'Проверка звука')
            right = ui.speech.signal('success', 'right', force=True, wait=True)
            ui.message('Проверка звука', 'Сигналы воспроизведены. F6 меняет выход.' if left and right else
                       'Не удалось воспроизвести один или оба сигнала. F6 меняет выход.')
            continue
        if ui.demo:
            ui.message('Демонстрация', 'Проверки оборудования запускаются после загрузки с флешки.')
            continue
        if key == 'memory':
            mode = ui.choose('Проверка памяти', [
                ('Быстрая: до 128 мегабайт, один проход', 'quick'),
                ('Обычная: до 1 гигабайта, один проход', 'normal'),
                ('Расширенная: до 75 процентов доступной памяти, три прохода', 'extended'),
                ('Что проверяет этот тест', 'help')])
            if mode == 'help':
                ui.message('Проверка памяти', 'Тестирует выделенную и закреплённую область свободной памяти. '
                    'Память ядра, прошивки и работающего меню не проверяется. Система продолжает говорить. '
                    'Сигнал раз в десять секунд означает работу, пробел читает состояние, Escape останавливает. '
                    'Отсутствие найденных ошибок не гарантирует исправность всей оперативной памяти.')
            elif mode:
                ui.run_job('Проверка памяти', toolkit.memory_job(mode))
        elif key == 'cpu':
            seconds = ui.choose('Нагрузка процессора', [('Одна минута', 60), ('Пять минут', 300)])
            if seconds and ui.confirm('Процессор будет работать под полной нагрузкой. Обеспечьте охлаждение.'):
                ui.run_job('Нагрузка процессора', toolkit.cpu_job(seconds))
        elif key == 'drivers':
            from drivers import report_job
            ui.run_job('Драйверы и прошивки устройств', report_job)
        elif key == 'smart':
            scan = ui.run_job('Поиск накопителей', lambda job: smart.discovery(job, disk), review=False, save=False)
            if not scan.failed and not scan.cancelled:
                device = ui.choose('SMART: выбор накопителя', [(item.title, item) for item in scan.payload])
                if device:
                    ui.run_job('SMART', smart.report_job(device, disk))
        elif key in ('read', 'filesystem'):
            device = choose_device(ui, disk, 'filesystem' if key == 'filesystem' else 'disk')
            if device:
                function = {'read': toolkit.read_job,
                            'filesystem': toolkit.filesystem_job}[key]
                ui.run_job('Диагностика: ' + device.title, function(device, disk))
        else:
            function = {'network': toolkit.network_job, 'temperature': toolkit.temperature_job,
                        'kernel': toolkit.report_job('kernel')}[key]
            ui.run_job({'network': 'Проверка сети', 'temperature': 'Батарея и температуры',
                        'kernel': 'Ошибки ядра'}[key], function)


def voice_menu(ui):
    while True:
        data = ui.settings.data
        voice_title = dict(VOICES).get(data['voice'], data['voice'])
        key = ui.choose('Настройки голоса', [
            ('Голос: ' + voice_title, 'voice'),
            (f"Скорость: {data['rate']} процентов", 'rate'),
            (f"Высота: {data['pitch']} процентов", 'pitch'),
            (f"Громкость: {data['volume']} процентов", 'volume'),
            ('Звуковые сигналы: ' + ('включены' if data['signals'] else 'выключены'), 'signals'),
            (f"Сообщать ход проверки каждые {data['progress_seconds']} секунд", 'progress_seconds'),
            ('Прослушать пример', 'sample'),
            ('Следующее звуковое устройство', 'output'),
            ('Сбросить настройки голоса', 'reset'),
            ('О голосах и лицензиях', 'licenses'),
        ])
        if key is None:
            return
        if key == 'voice':
            value = ui.choose('Выбор голоса', [(title, name) for name, title in VOICES])
            if value:
                ui.change_settings({'voice': value}, 'Выбран голос ' + dict(VOICES)[value], announce=False)
        elif key in ('rate', 'pitch', 'volume', 'progress_seconds'):
            values = {'rate': range(50, 251, 10), 'pitch': range(50, 151, 10),
                      'volume': range(10, 101, 10), 'progress_seconds': [15, 30, 60, 120]}[key]
            value = ui.choose('Новое значение', [(str(value), value) for value in values])
            if value is not None:
                ui.change_settings({key: value}, 'Установлено значение ' + str(value), announce=False)
        elif key == 'signals':
            value = not data['signals']
            ui.change_settings({'signals': value}, 'Сигналы ' + ('включены' if value else 'выключены'), announce=False)
        elif key == 'sample':
            ui.speak_wait('Здравствуйте. Это Флэш ОС. Стрелки выбирают пункт, Enter открывает его. '
                          'Настройки голоса сохраняются на вашей флешке.', 'Пример голоса')
        elif key == 'output':
            ui.speech.next_output()
        elif key == 'reset' and ui.confirm('Вернуть стандартные настройки голоса?'):
            ui.change_settings({key: value for key, value in DEFAULTS.items() if key != 'pointer'},
                               'Стандартные настройки голоса восстановлены')
        elif key == 'licenses':
            ui.message('Голоса', 'Елена, Ирина, Анна, Александр и Юрий — голоса RHVoice. '
                'Юрий RHVoice отличается от одноимённого Nuance. Для Юрия действуют ограничения '
                'некоммерческого использования. Милена и Катя Nuance в сборку не включены: '
                'нужна подходящая лицензия и Linux-компонент производителя. '
                'Лицензии и источники сохранены с документацией. Резервный голос — eSpeak.')


def power_menu(ui, root, disk):
    key = ui.choose('Питание', [('Выключение', 'off'), ('Перезапуск с флешки', 'reboot'),
        ('Перезапуск в основную систему', 'main'), ('Выбрать установленную систему', 'installed'),
        ('Перезапуск в настройки UEFI', 'firmware')])
    if key is None:
        return
    if ui.demo:
        ui.message('Демонстрация', 'Выбрано действие питания: ' + key)
        return
    entry = None
    if key in ('main', 'installed'):
        def scan(job):
            job.payload = installed_entries(disk, job=job)
            job.result = f'Найдено систем: {len(job.payload)}.'
        result = ui.run_job('Поиск установленных систем', scan, review=False, save=False)
        if result.failed or result.cancelled:
            return
        entries = result.payload
        if key == 'main':
            entries = [item for item in entries if item.internal]
        if not entries:
            ui.message('Установленные системы', 'Не найдены подходящие активные записи UEFI на других дисках. '
                'В режиме BIOS или при отсутствии записей выберите внутренний диск в меню загрузки компьютера. '
                'Порядок загрузки не изменён.')
            return
        entry = entries[0] if key == 'main' else ui.choose('Установленные системы',
            [(item.title + '. Запись ' + item.number, item) for item in entries])
        if entry is None:
            return
    if key == 'firmware' and not Path('/sys/firmware/efi').exists():
        ui.message('Настройки прошивки', 'Компьютер загружен в режиме BIOS. Автоматический вход в UEFI недоступен.')
        return
    try:
        cancel(ui.state_root)
    except OSError:
        if key != 'off':
            raise
        ui.speak_wait('Не удалось записать служебный раздел. Компьютер будет выключен.', 'Питание')
    message = 'Выключение компьютера.' if key == 'off' else 'Перезапуск. ' + (
        'Запускается ' + entry.title if entry else 'Настройки UEFI.' if key == 'firmware' else 'Загрузка с флешки.')
    ui.speech.say(message)
    if entry:
        reboot_once(entry, disk)
        return
    if key == 'reboot':
        if not return_to_usb():
            ui.speech.say('Если потребуется, снова выберите USB в меню загрузки компьютера.')
    command = ['systemctl', 'poweroff' if key == 'off' else 'reboot']
    if key == 'firmware':
        command.append('--firmware-setup')
    subprocess.run(command, check=True, timeout=15)


def run_menu(screen, root, speech, demo, state_root, settings):
    ui = UI(screen, speech, settings, state_root, demo)
    from store import ApplicationStore
    applications = ApplicationStore(ui, root, live_disk(root) if not demo else '/dev/flashos-demo')
    from utilities import Utilities
    utilities = Utilities(ui, applications)
    disk = live_disk(root) if not demo else '/dev/flashos-demo'
    images = discover(root, 'uefi' if Path('/sys/firmware/efi').exists() else 'bios')
    try:
        secure_var = Path('/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c')
        secure = secure_var.read_bytes()[4] if secure_var.is_file() else 0
        with open('/dev/ttyS0', 'w') as serial:
            serial.write(f'FLASHOS_READY images={len(images)} audio={len(speech.outputs)} secure_boot={secure} menu=main\n')
    except OSError:
        pass
    def show_error(error):
        try:
            ui.message('Действие не выполнено', str(error))
        except OpenPower:
            pass
    network_startup = not demo
    welcome = True
    while True:
        try:
            if welcome:
                welcome = False
                fallback = ('Графическое меню остановилось. Работаю в текстовом режиме. '
                            'Если выполнялось восстановление или исправление раздела, '
                            'оно могло остаться незавершённым. '
                            if os.environ.get('FLASHOS_GRAPHICS_FAILED') else '')
                ui.speak_wait(fallback + 'Флэш ОС. F1 — помощь. F7 — остановить речь.' +
                              (' ' + settings.error if settings.error else ''), 'FlashOS')
            if network_startup:
                network_startup = False
                try:
                    from network_settings import manager
                    manager(ui).startup()
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                    ui.speech.say('Сохранённая сеть не подключена. Откройте Инструменты и настройки сети. ' + str(error))
            choice = ui.choose('Главное меню', [
                ('Запустить систему', 'iso', 'Выберите загрузочный образ на флешке и способ запуска.'),
                ('Инструменты', 'tools', 'Работа с файлами, накопителями и восстановлением данных.'),
                ('Диагностика', 'diagnostics', 'Сведения о компьютере, проверка оборудования и отчёты.'),
                ('Приложения', 'apps', 'Магазин Debian, установленные программы и место на флешке.'),
                ('Утилиты', 'utilities', 'Размер текста, контрастность и доступность приложений.'),
                ('Питание', 'power', 'Сохранение данных, безопасное выключение и перезапуск.'),
                ('Настройки голоса', 'voice', 'Голос, громкость, скорость речи и звуковое устройство.'),
                ('Справка', 'help', 'Управление с клавиатуры и инструкции по работе с FlashOS.')])
            if choice is None:
                continue
            if choice in ('iso', 'tools', 'power'):
                applications.close()
            {'apps': applications.main, 'utilities': utilities.main, 'iso': lambda: iso_menu(ui, root), 'tools': lambda: tools_menu(ui, root, disk),
             'diagnostics': lambda: diagnostics_menu(ui, disk),
             'power': lambda: power_menu(ui, root, disk), 'voice': lambda: voice_menu(ui),
             'help': lambda: help_menu(ui)}[choice]()
        except OpenPower:
            try:
                applications.close()
                power_menu(ui, root, disk)
            except OpenPower:
                continue
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                show_error(error)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            show_error(error)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--graphical', action='store_true')
    args = parser.parse_args()
    speech = Speech()
    try:
        state_root = prepare_medium(args.root) if not args.demo else args.root
        settings = Settings(state_root)
        speech.configure(settings.data)
        if not args.demo:
            save_async(state_root, delay=8)
        if args.graphical:
            from graphical import GraphicalScreen
            screen = GraphicalScreen(windowed=args.demo)
            if not args.demo:
                Path('/run/flashos-gui-ready').write_text('ready\n')
            try:
                run_menu(screen, args.root, speech, args.demo, state_root, settings)
            finally:
                screen.close()
        else:
            curses.wrapper(run_menu, args.root, speech, args.demo, state_root, settings)
    except Exception as error:
        print('FlashOS: ' + str(error), flush=True)
        speech.say('Ошибка Флэш ОС. ' + str(error))
        time.sleep(5)
        raise
    finally:
        speech.close()


if __name__ == '__main__':
    main()
