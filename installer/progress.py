"""Measured per-stage transfer statistics; no guessed USB-generation speeds."""
from collections import deque
import math
import time


TRANSFER_NAMES = {'prepare': 'Подготовка на ПК', 'copy': 'Запись на USB', 'verify': 'Чтение с USB'}


def duration(seconds):
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


class ProgressState:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset()

    def reset(self):
        self.started = self.phase_started = self.last_event = self.clock()
        self.phase = ''
        self.message = 'Готов к работе. Выберите USB-накопитель.'
        self.file = ''
        self.percent = None
        self.done = self.total = 0
        self.samples = deque(maxlen=40)
        self.terminal = False
        self.stopping = False

    def accept(self, event):
        kind = event['kind']
        if kind not in ('phase', 'progress', 'complete', 'error', 'cancelled'):
            return
        now = self.clock()
        if kind in ('complete', 'error', 'cancelled'):
            # Engine completion is delayed until boot readback finishes; its phase
            # and byte counters describe an older stage, not a fresh transfer.
            self.message = event.get('message', '')
            self.last_event = now
            self.terminal = True
            if kind == 'complete':
                self.percent = 100.
            return
        phase = event.get('phase', self.phase)
        if phase != self.phase:
            self.phase, self.phase_started = phase, now
            self.samples.clear()
            self.file = ''
            self.done = self.total = 0
            self.percent = None
            self.samples.append((now, 0))
        self.last_event = now
        if kind == 'phase' or kind in ('complete', 'error', 'cancelled'):
            self.message = event.get('message', '')
        if kind == 'progress':
            self.file = event.get('message', '')
        value = event.get('percent')
        if isinstance(value, (int, float)) and math.isfinite(value):
            self.percent = max(0., min(100., value))
        elif kind == 'phase':
            self.percent = None
        if self.phase in TRANSFER_NAMES:
            self.done = max(0, event.get('bytes', self.done))
            self.total = max(0, event.get('total_bytes', self.total))
            if not self.samples or self.samples[-1][1] != self.done:
                self.samples.append((now, self.done))

    def metrics(self, now=None):
        if not self.phase and not self.terminal:
            return 'Время: —\nСкорость: —'
        now = self.clock() if now is None else now
        if self.terminal:
            now = self.last_event
        parts = [f'Время: {duration(now - self.started)}']
        if self.phase in TRANSFER_NAMES and len(self.samples) > 1:
            # Use a recent measured window; include a blocked write in its duration.
            while len(self.samples) > 2 and self.samples[1][0] < now - 10:
                self.samples.popleft()
            start, count = self.samples[0]
            speed = max(0, self.done - count) / max(.1, now - start)
            stalled = now - self.last_event >= 5 and not self.terminal
            speed_text = f'{speed / 1024**2:.1f} МиБ/с' if speed >= 102.4 * 1024 else f'{speed / 1024:.1f} КиБ/с'
            parts.append(f'{TRANSFER_NAMES[self.phase]}: ' + ('ожидание устройства' if stalled else speed_text))
            if self.total:
                parts.append(f'{self.done / 1024**2:.1f} из {self.total / 1024**2:.1f} МиБ')
                if speed > 0 and now - self.phase_started >= 3 and not stalled and not self.terminal:
                    # A short window can contain only small license/config files.
                    # Do not extrapolate that KiB/s rate to a remaining large image.
                    average = self.done / max(.1, now - self.phase_started)
                    parts.append('До конца этапа примерно: ' + duration((self.total - self.done) / max(1, average)))
        elif not self.terminal:
            parts.append('Скорость: ожидаются данные')
        if not self.terminal and now - self.last_event >= 5:
            parts.append('Без нового отчёта: ' + duration(now - self.last_event) + '. Не отключайте флешку.')
        return '\n'.join(parts)

    def summary(self):
        parts = [self.message]
        if self.stopping and not self.terminal:
            parts.insert(0, 'Остановка запрошена. Ожидаем завершения текущей системной записи.')
        if self.percent is not None:
            parts.append(f'Этап: выполнено {self.percent:.1f}%; осталось {100-self.percent:.1f}%.')
        elif not self.terminal and self.phase:
            parts.append('Процент этапа пока неизвестен.')
        if self.file:
            parts.append('Сейчас: ' + self.file)
        parts.append(self.metrics())
        return '\n'.join(parts)
