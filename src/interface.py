"""Keyboard-first console menus: every focused row and job state has speech."""
import curses
from collections import deque
import os
from pathlib import Path
import time
import uuid

from audio_diagnostics import save_async
from jobs import Job
from settings import validate, DurabilityError


class OpenPower(Exception):
    pass


def clean(text):
    return ' '.join(''.join(c if c.isprintable() else ' ' for c in str(text)).split())


class UI:
    def __init__(self, screen, speech, settings, state_root, demo=False):
        self.screen, self.speech, self.settings = screen, speech, settings
        self.state_root, self.demo = Path(state_root), demo
        self.focus = {}
        self.pending_keys = deque()
        if not getattr(screen, 'graphical', False):
            curses.curs_set(0)
        screen.keypad(True)
        screen.timeout(150)

    def draw(self, title, rows, selected=0, footer='', tabs=None, tab_index=0, detail=''):
        if getattr(self.screen, 'graphical', False):
            status = footer or self.speech.error or f'Громкость {self.speech.volume}%.'
            options = {'detail': clean(detail)} if detail else {}
            if tabs:
                options.update(tabs=tabs, tab_index=tab_index)
            self.screen.render(title, rows, selected, status, **options)
            return
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        capacity = max(1, height - 6)
        start = max(0, min(selected - capacity // 2, len(rows) - capacity))
        lines = [(title, False), ('Стрелки Выбор  Enter Открыть  Escape Назад  Пробел Повтор', False),
                 ('F1 Помощь  F3 Где я  F6 Звук  F7 Стоп речи  F8 Отчёт звука  F10 Питание  +/- Громкость', False), ('', False)]
        if tabs:
            lines[1] = ('  '.join(('[' + tab + ']') if i == tab_index else tab for i, tab in enumerate(tabs)), False)
            lines[2] = ('Left/Right и Tab — вкладки; F2 — поиск; F4 — источник; F1 — помощь.', False)
        lines += [(row, start + i == selected) for i, row in enumerate(rows[start:start + capacity])]
        lines += [(footer or self.speech.error or f'Громкость {self.speech.volume}%.', False)]
        for row, (line, selected_line) in enumerate(lines[:height]):
            try:
                self.screen.addstr(row, 0, clean(line)[:max(0, width - 1)],
                                   curses.A_REVERSE if selected_line else 0)
            except curses.error:
                pass
        self.screen.refresh()

    def announce(self, title, items, index):
        self.speech.say(clean(items[index][0]))

    def read_key(self, wide=False):
        """Keys that interrupted an explanation still reach the next menu."""
        if self.pending_keys:
            value = self.pending_keys.popleft()
            return chr(value) if wide and isinstance(value, int) and value < 256 else value
        return self.screen.get_wch() if wide else self.screen.getch()

    def discard_skip_repeats(self, skipped):
        """Discard already queued repeats, without delaying the next key press."""
        self.screen.timeout(0)
        try:
            for _ in range(64):
                key = self.screen.getch()
                if key == -1:
                    break
                if key != skipped:
                    if key == curses.KEY_F10:
                        raise OpenPower()
                    self.pending_keys.append(key)
                    break
        finally:
            self.screen.timeout(150)

    def speak_wait(self, text, title='Сообщение'):
        """Read an optional explanation while keeping Tk and curses responsive.

        Enter, Space and F7 skip speech only; they never consent to an action.
        Navigation is returned to the next menu, and F10 keeps its normal meaning.
        """
        import textwrap
        rows = textwrap.wrap(clean(text), width=350) or ['Нет данных.']
        done = self.speech.say(text)
        deadline = time.monotonic() + 100
        try:
            while not done.is_set():
                if time.monotonic() >= deadline:
                    return False
                self.draw(title, rows, footer='Enter, Пробел или F7 — пропустить озвучку. F10 — питание.')
                key = self.screen.getch()
                if key in (10, 13, curses.KEY_ENTER, ord(' '), curses.KEY_F7):
                    self.discard_skip_repeats(key)
                    return False
                if key in (27, curses.KEY_BACKSPACE, 8, 127, 9, curses.KEY_BTAB,
                           curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT, curses.KEY_RIGHT,
                           curses.KEY_HOME, curses.KEY_END, curses.KEY_NPAGE, curses.KEY_PPAGE,
                           curses.KEY_F2, curses.KEY_F4, curses.KEY_F5) or isinstance(key, tuple):
                    self.pending_keys.append(key)
                    return False
                if key == curses.KEY_F3:
                    done = self.speech.say(text)
                elif key == curses.KEY_F1:
                    done = self.speech.say('Enter, Пробел или F7 останавливают только озвучку. '
                                           'Подтверждение действия выполняется отдельно. ' + text)
                else:
                    self.global_key(key)
            return True
        finally:
            if not done.is_set():
                self.speech.stop()

    def change_settings(self, changes, message, announce=True):
        temporary = validate({**self.settings.data, **changes})
        try:
            self.settings.save(changes)
        except DurabilityError as error:
            self.speech.configure(self.settings.data)
            self.speak_wait(message + '. ' + str(error), 'Сохранение настроек')
        except OSError:
            self.settings.data = temporary
            self.speech.configure(temporary)
            self.speak_wait(message + '. Настройки действуют до перезагрузки: запись на флешку не удалась.', 'Сохранение настроек')
        else:
            self.speech.configure(self.settings.data)
            if announce:
                self.speech.say(message)

    def global_key(self, key):
        if key == curses.KEY_F7:
            self.speech.stop()
        elif key == curses.KEY_F6:
            self.speech.next_output()
        elif key == curses.KEY_F8 and not self.demo:
            self.speech.say('Сохраняю сведения о звуке.')
            save_async(self.state_root, self.speech.say_if_idle)
        elif key in (ord('+'), ord('='), ord('-')):
            volume = max(10, min(100, self.speech.volume + (-10 if key == ord('-') else 10)))
            self.change_settings({'volume': volume}, f'Громкость {volume} процентов')
        elif key == curses.KEY_F10:
            raise OpenPower()
        else:
            return False
        return True

    def choose(self, title, items, help_text='Стрелки — выбор. Enter — открыть. Escape — назад. F3 — положение в меню.', refresh=False, prompt=False, tabs=None, tab_index=0, shortcuts=None):
        if not items:
            self.message(title, 'Доступные пункты не найдены.')
            return None
        index = min(self.focus.get(title, 0), len(items) - 1)
        if prompt:
            self.speech.say(clean(title) + '. ' + clean(items[index][0]))
        else:
            self.announce(title, items, index)
        while True:
            self.draw(title, [f'{i + 1}. {clean(item[0])}' for i, item in enumerate(items)], index,
                      tabs=tabs, tab_index=tab_index, detail=items[index][2] if len(items[index]) > 2 else '')
            key = self.read_key()
            if tabs:
                if isinstance(key, tuple) and len(key) == 2 and key[0] == 'tab':
                    return ('__tab__', max(0, min(len(tabs) - 1, key[1])))
                if key in (curses.KEY_LEFT, curses.KEY_RIGHT, 9, curses.KEY_BTAB):
                    next_tab = (tab_index + (-1 if key in (curses.KEY_LEFT, curses.KEY_BTAB) else 1)) % len(tabs)
                    return ('__tab__', next_tab)
            if shortcuts and key in shortcuts:
                return shortcuts[key]
            if key in (27, curses.KEY_BACKSPACE, 8, 127):
                return None
            if key in (10, 13, curses.KEY_ENTER):
                return items[index][1]
            if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_HOME, curses.KEY_END,
                       curses.KEY_NPAGE, curses.KEY_PPAGE):
                if key == curses.KEY_HOME:
                    index = 0
                elif key == curses.KEY_END:
                    index = len(items) - 1
                elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
                    index = max(0, min(len(items) - 1, index + (10 if key == curses.KEY_NPAGE else -10)))
                else:
                    amount = 10 if key in (curses.KEY_NPAGE, curses.KEY_PPAGE) else 1
                    index = (index + (-amount if key in (curses.KEY_UP, curses.KEY_PPAGE) else amount)) % len(items)
                self.focus[title] = index
                self.announce(title, items, index)
            elif key == ord(' '):
                self.announce(title, items, index)
            elif key == curses.KEY_F1:
                self.speech.say(help_text)
            elif key == curses.KEY_F3:
                details = '. ' + clean(items[index][2]) if len(items[index]) > 2 else ''
                self.speech.say(f'{title}. {index + 1} из {len(items)}. {clean(items[index][0])}' + details)
            elif key == curses.KEY_F5 and refresh:
                return '__refresh__'
            else:
                self.global_key(key)

    def view(self, title, lines):
        import textwrap
        rows = [part for line in lines if clean(line)
                for part in textwrap.wrap(clean(line), width=350, break_long_words=True)] or ['Нет данных.']
        title = title + ' — строки'
        self.focus[title] = 0
        while self.choose(title, [(line, i) for i, line in enumerate(rows)],
                'Отчёт читается построчно стрелками. Page Down и Page Up — по десять строк. '
                'Home и End — начало и конец. Пробел повторяет строку. F3 — положение. Escape закрывает отчёт.') is not None:
            pass

    def message(self, title, text):
        self.view(title, str(text).splitlines())

    def confirm(self, text):
        self.focus[text] = 0
        return self.choose(text, [('Отмена', False), ('Продолжить', True)], prompt=True) is True

    def confirm_timed(self, text, seconds=60):
        """A pending NetworkManager checkpoint must not outlive its consent screen."""
        deadline = time.monotonic() + max(0, seconds)
        selected = 0
        items = ['Отменить и вернуть прежнее подключение', 'Принять параметры подключения']
        self.speech.say(text + '. ' + items[0] + '. На подтверждение ' + str(int(seconds)) + ' секунд.')
        while True:
            remaining = max(0, int(deadline - time.monotonic()))
            if remaining <= 0:
                self.speech.say('Время подтверждения истекло. Возвращаю прежнее подключение.')
                return False
            self.draw(text, items, selected, f'Осталось {remaining} секунд. Escape — откат.')
            key = self.read_key()
            if key in (27, curses.KEY_BACKSPACE, 8, 127):
                return False
            if key in (10, 13, curses.KEY_ENTER):
                return selected == 1
            if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_HOME, curses.KEY_END):
                selected = 0 if key == curses.KEY_HOME else 1 if key == curses.KEY_END else 1 - selected
                self.speech.say(items[selected])
            elif key == ord(' '):
                self.speech.say(items[selected] + f'. Осталось {remaining} секунд.')
            elif key == curses.KEY_F3:
                self.speech.say(text + f'. {selected + 1} из 2. ' + items[selected] + f'. Осталось {remaining} секунд.')
            elif key == curses.KEY_F1:
                self.speech.say('Проверьте подключение. Enter принимает выбранное действие. '
                                'Escape или истечение времени возвращает прежние параметры.')
            else:
                self.global_key(key)

    def text_input(self, title, secret=False, limit=255):
        value = ''
        self.speech.say(title + '. Enter — принять. Escape — отменить. Backspace — удалить. '
                        + ('Символы пароля не произносятся. F3 — число символов.' if secret else 'F3 — прочитать введённое.'))
        while True:
            self.draw(title, [('•' * len(value)) if secret else value,
                             'F3 — число символов' if secret else 'F3 — прочитать введённое'])
            try:
                key = self.read_key(wide=True)
            except curses.error:
                continue
            if key == '\x1b':
                return None
            if key in ('\n', '\r', curses.KEY_ENTER):
                return value
            if key in ('\x08', '\x7f', curses.KEY_BACKSPACE):
                if value:
                    value = value[:-1]
                    self.speech.say('Удалено')
            elif key == curses.KEY_F3:
                self.speech.say(f'Символов: {len(value)}' if secret else value or 'Пусто')
            elif key == curses.KEY_F1:
                self.speech.say('Введите текст. Enter — принять. Escape — отменить. F3 — проверить ввод.')
            elif isinstance(key, str) and key.isprintable() and len(value) < limit:
                value += key
                self.speech.say('Символ' if secret else 'Пробел' if key == ' ' else key)
            elif isinstance(key, int):
                self.global_key(key)

    def save_report(self, title, result, lines, attachments=None):
        folder = self.state_root / 'flashos/reports'
        folder.mkdir(exist_ok=True)
        path = folder / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.txt')
        with path.open('x', encoding='utf-8') as stream:
            stream.write(title + '\n' + result + '\n\n' + '\n'.join(lines))
            stream.flush()
            os.fsync(stream.fileno())
        for name, content in (attachments or {}).items():
            if not name.replace('-', '').replace('.', '').isalnum() or '..' in name:
                raise ValueError('Недопустимое имя вложения отчёта')
            with path.with_name(path.stem + '-' + name).open('x', encoding='utf-8') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return path

    def run_job(self, title, function, review=True, save=True):
        self.active_job = None
        try:
            return self._run_job(title, function, review, save)
        except BaseException:
            if self.active_job is not None and not self.active_job.done.is_set():
                self.active_job.cancel()
                self.active_job.thread.join(timeout=10)
            raise
        finally:
            self.active_job = None

    def _run_job(self, title, function, review=True, save=True):
        job = Job(title, function).start()
        self.active_job = job
        self.speech.say(title + '. Начато.')
        last_voice = last_signal = time.monotonic()
        errors = 0
        stopping = False
        power_requested = False
        def request_stop(power=False):
            nonlocal stopping, power_requested
            power_requested = power_requested or power
            if stopping:
                return
            job.cancel()
            stopping = True
            if getattr(job, 'cancellation_deferred', False):
                self.speech.say('Дождусь безопасного завершения записи пакетов. Затем откроется питание.' if power_requested else 'Дождусь безопасного завершения записи пакетов.')
            else:
                self.speech.say('Останавливаю. Затем откроется питание.' if power_requested else 'Останавливаю.')
        while not job.done.is_set():
            now = time.monotonic()
            elapsed = int(now - job.started)
            state = f'{job.status} Прошло {elapsed // 60} минут {elapsed % 60} секунд.'
            self.draw(title, [state, f'Сообщений об ошибке: {job.error_count}',
                             'Остановка запрошена. Ожидаю завершения.' if stopping else 'Escape — остановить проверку.'])
            if job.error_count > errors:
                errors = job.error_count
                self.speech.signal('error')
                self.speech.say('Обнаружена ошибка. ' + state)
                last_voice = now
            elif now - last_voice >= self.settings.data['progress_seconds']:
                self.speech.say_if_idle(state)
                last_voice = now
            if now - last_signal >= 10:
                self.speech.signal()
                last_signal = now
            key = self.screen.getch()
            if key in (27, curses.KEY_F10):
                request_stop(key == curses.KEY_F10)
            elif key == ord(' '):
                self.speech.say(state)
            elif key == curses.KEY_F1:
                self.speech.say('Короткий сигнал означает, что проверка продолжается. '
                                'Результат будет произнесён. Пробел — текущее состояние, Escape — остановить.')
            elif key != curses.KEY_F10:
                try:
                    self.global_key(key)
                except OpenPower:
                    # A settings error can open speak_wait inside this job loop.
                    # Its F10 must wait for the same safe completion as direct F10.
                    request_stop(power=True)
        lines = list(job.lines)
        saved = ''
        if save:
            try:
                report_lines = lines + [line for heading, rows in job.sections for line in [heading, *rows]]
                path = self.save_report(title, job.result, report_lines, job.attachments)
                saved = 'Отчёт: ' + path.name
            except OSError as error:
                saved = 'Отчёт не сохранён: ' + str(error)
                job.warning = True
                job.result += ' Отчёт не сохранён.'
        self.speech.signal('error' if job.failed else 'progress' if job.cancelled or job.incomplete or job.warning else 'success')
        if power_requested:
            self.speech.say(job.result)
            raise OpenPower()
        if review or job.failed or job.cancelled or job.incomplete or job.warning:
            if job.sections:
                while True:
                    selected = self.choose(title, [(job.result, (title, [job.result, saved, *lines])),
                        *((heading, (heading, rows)) for heading, rows in job.sections)])
                    if selected is None:
                        break
                    self.view(*selected)
            else:
                self.view(title, [job.result, saved, *lines])
        return job

    def keyboard_test(self):
        self.speech.say('Проверка клавиатуры. Нажимайте клавиши. Escape — закончить.')
        names = {curses.KEY_UP: 'вверх', curses.KEY_DOWN: 'вниз', curses.KEY_LEFT: 'влево',
                 curses.KEY_RIGHT: 'вправо', 10: 'Enter', 32: 'пробел', 9: 'Tab',
                 curses.KEY_BACKSPACE: 'Backspace', curses.KEY_HOME: 'Home', curses.KEY_END: 'End'}
        last = 'Ожидание клавиши'
        while True:
            self.draw('Проверка клавиатуры', [last, 'Escape — выход'])
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if key == '\x1b':
                return
            if key == curses.KEY_F7:
                self.speech.stop()
                last = 'F7 — речь остановлена'
                continue
            if isinstance(key, str):
                last = names.get(ord(key), key if key.isprintable() else 'Код ' + str(ord(key)))
            else:
                names.update({curses.KEY_F0 + i: f'F{i}' for i in range(1, 13)})
                names.update({curses.KEY_IC: 'Insert', curses.KEY_DC: 'Delete',
                    curses.KEY_NPAGE: 'Page Down', curses.KEY_PPAGE: 'Page Up'})
                last = names.get(key, 'Код ' + str(key))
            self.speech.say(last)
