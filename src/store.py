"""Accessible APT store and the portable applications menu."""
import curses
from pathlib import Path

from store_client import StoreClient, WARNING
from store_storage import StoreEnvironment
from store_launch import StoreLauncher
from network import network_menu


TABS = ('Доступные', 'Установленные', 'Обновления')
SOURCE_LABELS = {
    'all': 'Все разделы Debian 13',
    'stable': 'Debian 13 и обновления безопасности',
    'backports': 'Debian 13 Backports',
    'main': 'Debian Main — свободное ПО',
    'contrib': 'Debian Contrib',
    'non-free': 'Debian Non-free',
    'non-free-firmware': 'Debian Firmware',
}


def amount(value):
    value = max(0, int(value or 0))
    if value >= 1024**3:
        return f'{value / 1024**3:.2f} ГиБ'
    return f'{value / 1024**2:.1f} МиБ'


def rows(data):
    if isinstance(data, list):
        return data
    return data.get('entries', data.get('items', data.get('packages', [])))


class ApplicationStore:
    def __init__(self, ui, medium, live_disk):
        self.ui = ui
        self.environment = StoreEnvironment(medium, live_disk)
        self.client = StoreClient(self.environment, ui)
        self.launcher = StoreLauncher(self.environment, ui)
        self.source, self.query, self.offset, self.tab = 'all', '', 0, 0
        self.refresh_prompted = False

    def task(self, title, action, request=None, review=False, **options):
        self.replan_requested = False
        while True:
            if action in ('refresh', 'apply', 'repair'):
                from network_proxy import ensure_credentials
                ensure_credentials(self.ui)
            result = self.ui.run_job(title,
                lambda job: self.client.call(job, action, request, **options),
                review=review, save=action in ('apply', 'repair'))
            cancellation = getattr(result, 'cancel_event', None)
            if result.cancelled or result.incomplete or (cancellation is not None and cancellation.is_set()):
                return None
            if not result.failed:
                data = getattr(result, 'payload', None)
                return None if data and data.get('cancelled') else data
            error = getattr(result, 'store_connection_error', None)
            if error is None:
                return None
            if not self.connection_recovery(action, error.retry_safe):
                return None
            if action == 'apply':
                self.replan_requested = True
                return None

    def connection_recovery(self, action, retry_safe):
        items = [('Перейти в Инструменты и подключиться к сети', 'network')]
        if retry_safe:
            label = ('Заново проверить и подтвердить план установки' if action == 'apply'
                     else 'Повторить загрузку через интернет')
            items.append((label, 'retry'))
        items.append(('Вернуться без загрузки — сохранить текущий каталог', None))
        help_text = ('Не удалось связаться с серверами Debian. В Инструментах выберите Wi-Fi, '
                     'проводную сеть по DHCP или сохранённый профиль IP, DNS и прокси. Наличие сетевого адреса ещё не означает доступ в интернет. '
                     'Escape возвращает сюда; после подключения выберите повтор. '
                     'Сохранённый каталог и установленные приложения доступны без загрузки.')
        if not retry_safe:
            help_text += (' Изменения пакетов могли начаться. После подключения откройте '
                          '«Хранилище и свободное место» и проверку прерванной установки.')
        title = 'Магазин — требуется подключение к сети'
        self.ui.focus[title] = 0
        while True:
            choice = self.ui.choose(title, items, help_text, prompt=True)
            if choice == 'network':
                network_menu(self.ui)
            else:
                return choice == 'retry'

    def ready(self):
        if self.ui.demo:
            self.ui.message('Магазин приложений', 'Установка доступна после загрузки настоящей флешки FlashOS.')
            return False
        if not self.environment.image.is_file():
            pending = self.environment.image.with_suffix('.ext4.creating')
            if pending.exists() or pending.is_symlink():
                recovery = self.ui.choose('Незавершённая подготовка хранилища',
                    [('Отмена', None), ('Проверить и завершить подготовку', 'recover'),
                     ('Сохранить незавершённый образ в архив и создать новый', 'archive')],
                    'Существующий файл не удаляется. Архив на этой флешке продолжает занимать место.',
                    prompt=True)
                if recovery is None:
                    return False
                if recovery == 'recover':
                    result = self.ui.run_job('Завершение подготовки хранилища',
                        self.environment.recover_preparation, review=True)
                    return not (result.failed or result.cancelled)
                if not self.ui.confirm('Сохранить весь незавершённый образ в папку incomplete? Он продолжит занимать место на флешке.'):
                    return False
                result = self.ui.run_job('Сохранение незавершённого хранилища',
                    self.environment.archive_preparation, review=True)
                if result.failed or result.cancelled:
                    return False
            choice = self.ui.choose('Постоянное хранилище приложений',
                [('Отмена', None),
                 ('Создать хранилище 2 ГиБ на этой флешке', 2),
                 ('Создать хранилище 4 ГиБ', 4),
                 ('Создать хранилище 8 ГиБ', 8)],
                'Программы и настройки останутся на флешке. Место выделяется полностью; '
                'на FAT32 поддерживается хранилище до 3 ГиБ. Существующие файлы сохраняются.',
                prompt=True)
            if choice is None:
                return False
            result = self.ui.run_job('Создание хранилища приложений',
                lambda job: self.environment.initialize(job, size_gib=choice), review=False)
            if result.failed or result.cancelled:
                return False
        return True

    def close(self):
        self.launcher.close()
        if not self.environment.status()['mounted']:
            return
        result = self.ui.run_job('Сохранение и отключение приложений',
            self.environment.close, review=False, save=False)
        if result.failed or result.cancelled:
            raise RuntimeError('Хранилище приложений не отключено. Проверьте отчёт перед перезапуском.')

    def main(self):
        while True:
            items = [('Магазин приложений Debian', 'store'),
                     ('Установленные приложения — запуск и удаление', 'installed'),
                     ('Хранилище и свободное место', 'space'),
                     ('Доступность и закрытие приложений', 'help')]
            if not self.ui.demo and self.environment.image.is_file():
                installed = self.task('Чтение установленных приложений', 'installed')
                if installed is not None:
                    items += [(entry.get('package', entry.get('name', '')), ('package', entry))
                              for entry in rows(installed)]
            selection = self.ui.choose('Приложения', items, prompt=True)
            if selection is None:
                return
            if isinstance(selection, tuple) and selection[0] == 'package':
                self.package(selection[1])
                continue
            if selection == 'help':
                self.ui.message('Доступность приложений', WARNING +
                    ' Данные пользователя хранятся внутри образа flashos/applications/store.ext4. '
                    'Выключайте компьютер через меню FlashOS перед извлечением флешки.')
                continue
            if not self.ready():
                continue
            if selection == 'store':
                self.store()
            elif selection == 'installed':
                self.installed()
            elif selection == 'space':
                self.storage()

    def storage(self):
        while True:
            data = self.environment.status()
            lines = ['Флешка: ' + str(self.environment.medium),
                     'Хранилище: ' + str(self.environment.image),
                     'Внешняя флешка: свободно ' + amount(data.get('outer_free_bytes', 0)),
                     'Внутри хранилища: свободно ' + amount(data.get('free_bytes', 0)),
                     'Установка и настройки сохраняются после перезапуска и переноса на другой ПК x86-64.']
            selection = self.ui.choose('Хранилище приложений',
                [('Прочитать состояние и свободное место', 'status'),
                 ('Увеличить хранилище', 'grow'),
                 ('Очистить кэш загрузок — сохранить программы и настройки', 'clean'),
                 ('Проверить и восстановить завершение установки', 'repair'),
                 ('Восстановить файловую систему хранилища после сбоя', 'repair-image'),
                 ('Сохранить и отключить хранилище', 'close')])
            if selection is None:
                return
            if selection == 'status':
                result = self.ui.run_job('Проверка хранилища', self.environment.open, review=False, save=False)
                if result.failed or result.cancelled:
                    continue
                data = self.environment.status()
                lines[3] = 'Внутри хранилища: свободно ' + amount(data.get('free_bytes', 0))
                self.ui.view('Место для приложений', lines)
            elif selection == 'grow':
                size = self.ui.choose('Новый общий размер хранилища',
                    [('Отмена', None), ('3 ГиБ — максимум для FAT32', 3),
                     ('4 ГиБ', 4), ('8 ГиБ', 8), ('16 ГиБ', 16)])
                if size:
                    self.launcher.close()
                    self.ui.run_job('Увеличение хранилища', lambda job: self.environment.grow(job, size_gib=size))
            elif selection == 'clean':
                if self.ui.confirm('Удалить только скачанные пакеты из кэша APT? Установленные приложения и личные файлы сохраняются.'):
                    self.task('Очистка загрузок магазина', 'clean', review=True)
            elif selection == 'repair':
                if self.ui.confirm('Проверить и завершить ранее прерванные изменения пакетов?'):
                    self.task('Восстановление пакетов', 'repair', review=True)
            elif selection == 'repair-image':
                if self.ui.confirm('Восстановить журнал и безопасно исправимые ошибки ext4-хранилища? Файл приложений сохраняется.'):
                    self.launcher.close()
                    self.ui.run_job('Восстановление файловой системы приложений', self.environment.repair_image)
            elif selection == 'close':
                self.close()
                return

    def select_source(self):
        value = self.ui.choose('Источник приложений',
            [(label, key) for key, label in SOURCE_LABELS.items()],
            'Main, Contrib, Non-free и Firmware — разделы официального Debian. '
            'Backports устанавливаются только при явном выборе. Проверка подписей обязательна.',
            prompt=True)
        if value:
            self.source, self.offset = value, 0

    def store(self):
        self.ui.speak_wait('Магазин Debian. ' + WARNING, 'Доступность приложений')
        while True:
            action = ('catalog', 'installed', 'updates')[self.tab]
            options = {}
            if self.tab == 0:
                options = {'query': self.query, 'source': self.source, 'offset': self.offset, 'limit': 50}
            elif self.tab == 2:
                options = {'source': self.source}
            data = self.task('Чтение магазина', action, **options)
            if data is None:
                choice = self.ui.choose('Магазин недоступен',
                    [('Обновить список пакетов через интернет', 'refresh'),
                     ('Проверка и восстановление пакетов', 'repair')])
                if choice is None:
                    return
                self.task('Обновление каталога' if choice == 'refresh' else 'Восстановление пакетов', choice, review=True)
                continue
            if self.tab == 0 and data.get('needs_refresh') and not self.refresh_prompted:
                self.refresh_prompted = True
                if self.ui.confirm('Каталог ещё не загружен. Загрузить подписанные списки Debian через интернет?'):
                    self.task('Первое обновление каталога Debian', 'refresh', review=True)
                    continue
            entries = rows(data)
            items = [('Поиск: ' + (self.query or 'все пакеты') + ' — F2', ('search', None)),
                     ('Источник: ' + SOURCE_LABELS[self.source] + ' — F4', ('source', None)),
                     ('Обновить каталог через интернет — F5', ('refresh', None)),
                     ('Подключиться к сети — Инструменты', ('network', None))]
            if self.tab == 2 and entries:
                items.append(('Обновить все приложения и их зависимости', ('upgrade-all', None)))
            for entry in entries:
                name = entry.get('name', entry.get('package', ''))
                summary = (entry.get('summary', entry.get('description', '')).splitlines() or [''])[0]
                version = entry.get('version', entry.get('installed_version', ''))
                label = name + (' — ' + summary[:160] if summary else '') + ('; ' + version if version else '')
                items.append((label, ('package', entry), entry.get('description', summary)))
            if not entries:
                empty = ('Обновления не найдены. F5 — обновить каталог через интернет.' if self.tab == 2 else
                         'Через магазин пока не установлено приложений.' if self.tab == 1 else
                         'Приложения не найдены. Обновите каталог или измените поиск.')
                items.append((empty, ('empty', None)))
            if self.tab == 0:
                total = data.get('total', len(entries))
                if self.offset:
                    items.append(('Предыдущие 50 пакетов', ('previous', None)))
                if self.offset + len(entries) < total:
                    items.append((f'Следующие 50 пакетов. Всего {total}', ('next', None)))
            items.append(('Предупреждение о доступности приложений', ('help', None)))
            title = 'Магазин Debian — ' + TABS[self.tab]
            self.ui.speech.say(title)
            choice = self.ui.choose(title, items, tabs=TABS, tab_index=self.tab,
                shortcuts={curses.KEY_F2: ('search', None), curses.KEY_F4: ('source', None),
                           curses.KEY_F5: ('refresh', None)},
                help_text='Стрелки влево и вправо и Tab выбирают вкладки Доступные, Установленные, Обновления. '
                          'Вверх и вниз — строка. Enter — действие. F2 — поиск, F4 — источник, F5 — обновить каталог. '
                          'F3 — описание. Escape — назад. ' + WARNING)
            if choice is None:
                return
            command, value = choice
            if command == '__tab__':
                self.tab, self.offset = value, 0
                self.ui.speech.say('Вкладка ' + TABS[self.tab])
            elif command == 'search':
                value = self.ui.text_input('Поиск по названию и описанию пакетов', limit=150)
                if value is not None:
                    self.query, self.offset, self.tab = value.strip(), 0, 0
            elif command == 'source':
                self.select_source()
            elif command == 'refresh':
                self.task('Обновление подписанных каталогов Debian', 'refresh', review=True)
            elif command == 'network':
                network_menu(self.ui)
            elif command in ('previous', 'next'):
                self.offset = max(0, self.offset + (-50 if command == 'previous' else 50))
            elif command == 'help':
                self.ui.message('Доступность приложений', WARNING)
            elif command == 'package':
                self.package(value)
            elif command == 'upgrade-all':
                self.transaction('upgrade', [])

    def installed(self):
        while True:
            data = self.task('Установленные приложения', 'installed')
            if data is None:
                return
            entries = rows(data)
            selected = self.ui.choose('Установленные приложения',
                [(entry.get('name', entry.get('package', '')), entry) for entry in entries] +
                [('Открыть магазин приложений', 'store')], prompt=True)
            if selected is None:
                return
            if selected == 'store':
                self.store()
            else:
                self.package(selected)

    def package(self, entry):
        name = entry.get('name', entry.get('package'))
        while True:
            data = self.task('Проверка установленного приложения', 'installed')
            if data is None:
                return
            managed = next((item for item in rows(data)
                            if item.get('package', item.get('name')) == name), None)
            installed_version = (managed or entry).get('installed_version')
            items = []
            if managed is not None:
                items += [('Запустить', 'launch'), ('Проверить обновления', 'upgrade'),
                          ('Удалить установленное приложение', 'remove')]
                state = 'Установлено через магазин. Версия: ' + str(installed_version)
            elif installed_version:
                state = 'Компонент среды или зависимость приложения. Управляется вместе с приложениями.'
            else:
                items.append(('Установить', 'install'))
                state = 'Приложение ещё не установлено.'
            items.append(('Описание, версия и источник', 'details'))
            items = [(label, action, state) for label, action in items]
            selected = self.ui.choose('Приложение ' + name, items,
                state + ' Стрелки — выбор. Enter — открыть. Escape — назад. F3 — прочитать состояние.',
                prompt=True)
            if selected is None:
                return
            if selected == 'details':
                self.ui.view(name, [entry.get('description', entry.get('summary', 'Описание не предоставлено.')),
                    'Доступная версия: ' + str(entry.get('version') or installed_version or 'не указана'),
                    'Установленная версия: ' + str(installed_version or 'не установлено'), state,
                    'Источник: ' + ', '.join(row.get('suite', '') + '/' + row.get('component', '') for row in entry.get('sources', [])), WARNING])
            elif selected == 'launch':
                data = self.task('Поиск ярлыков ' + name, 'launchers', package=name)
                if data is None:
                    continue
                choices = rows(data)
                if not choices:
                    self.ui.message(name, 'Пакет не предоставляет запускаемое приложение. '
                        'Библиотеки и данные используются другими приложениями автоматически.')
                    continue
                chosen = self.ui.choose('Запустить ' + name,
                    [(row.get('name', name), row) for row in choices], prompt=True)
                if chosen:
                    self.launcher.launch(chosen)
            else:
                if self.transaction(selected, [name]) and selected == 'remove':
                    return

    def transaction(self, operation, packages):
        while True:
            self.replan_requested = False
            changed = self.confirm_transaction(operation, packages)
            if not self.replan_requested:
                return changed

    def confirm_transaction(self, operation, packages):
        plan = self.task('Проверка зависимостей и места',
            'plan', {'operation': operation, 'packages': packages, 'source': self.source})
        if plan is None:
            return False
        changes = plan.get('changes', [])
        if not changes:
            self.ui.message('Пакеты', 'Изменения не требуются.')
            return False
        lines = ['Действие: ' + operation,
                 'Загрузка: ' + amount(plan.get('download_bytes')),
                 'Необходимое свободное место с запасом: ' + amount(plan.get('required_bytes')),
                 'Изменений пакетов: ' + str(len(changes)),
                 'Зависимости устанавливаются вместе с приложением после подтверждения.']
        lines += [f"{row['package']}: {row.get('action', '')}; {row.get('from_version') or 'не установлен'} → {row.get('to_version') or 'удаление'}"
                  for row in changes]
        if operation == 'remove':
            lines.append('Зависимые приложения, указанные в плане, также будут удалены. '
                         'Личные настройки сохраняются. Ненужные зависимости не удаляются без отдельного плана.')
        title = ('Удалить' if operation == 'remove' else 'Установить обновления для' if operation == 'upgrade' else 'Установить') + ' ' + ', '.join(packages[:3])
        if operation == 'upgrade' and not packages:
            title = 'Обновить все приложения и зависимости'
        self.ui.focus[title] = 0
        while True:
            confirmation = self.ui.choose(title,
                [('Отмена', False), ('Прочитать полный план и зависимости', 'plan'),
                 ('Подтвердить ' + str(len(changes)) + ' изменений. Место: ' + amount(plan.get('required_bytes')), True)],
                'Перед записью повторно проверяются версии, зависимости, подписи и свободное место. '
                'Escape во время установки ожидает безопасного завершения записи пакетов.', prompt=True)
            if confirmation == 'plan':
                self.ui.view('План изменений пакетов', lines)
            elif confirmation is True:
                result = self.task('Изменение приложений на флешке', 'apply', plan, review=True)
                return result is not None
            else:
                return False
