"""Cancellable jobs with bounded logs and progress independent of speech."""
from collections import deque
import os
import re
import selectors
import signal
import subprocess
import threading
import time


class Cancelled(Exception):
    pass


class Job:
    lingering = []
    lingering_lock = threading.Lock()

    def __init__(self, title, function):
        self.title = title
        self.status = 'Подготовка'
        self.lines = deque(maxlen=2000)
        self.result = ''
        self.failed = False
        self.incomplete = False
        self.warning = False
        self.sections = []
        self.attachments = {}
        self.error_count = 0
        self.cancelled = False
        self.cancel_event = threading.Event()
        self.done = threading.Event()
        self.started = time.monotonic()
        self.process = None
        self.thread = threading.Thread(target=self._run, args=(function,), daemon=True)

    def start(self):
        self.thread.start()
        return self

    def update(self, text):
        self.status = str(text)[:350]

    def log(self, text):
        text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(text))
        if '\b' in text:
            visible = []
            for character in text:
                if character == '\b':
                    if visible:
                        visible.pop()
                else:
                    visible.append(character)
            text = ''.join(visible)
        text = ''.join(c for c in text if c.isprintable() or c == '\t')[:500]
        if text.strip():
            self.lines.append(text)

    def check(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def cancel(self):
        self.cancel_event.set()

    def _run(self, function):
        try:
            function(self)
            self.check()
            if not self.result:
                self.result = 'Завершено.'
        except Cancelled:
            self.cancelled = True
            self.result = getattr(self, 'cancel_result', 'Остановлено пользователем. Проверка не завершена.')
        except Exception as error:
            self.failed = True
            self.result = 'Не удалось завершить: ' + str(error)
            self.log(self.result)
        finally:
            self.done.set()

    def _stop(self, process, resource):
        # SIGINT lets ddrescue flush its map. Kill the group even if its leader exited.
        try:
            os.killpg(process.pid, signal.SIGINT)
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with self.lingering_lock:
                self.lingering.append((process, resource))
            def reap():
                process.wait()
                with self.lingering_lock:
                    self.lingering.remove((process, resource))
            threading.Thread(target=reap, daemon=True).start()
            raise RuntimeError('Устройство не отвечает. Проверки этого типа временно недоступны; остальные инструменты работают.')

    def command(self, command, timeout=60, on_line=None, accepted=(0,), cleanup=False,
                log_output=True, output_limit=262144, resource=None, cwd=None, capture_output=True):
        """Bounded process cleanup, with no implicit Popen.__exit__ wait."""
        if not cleanup:
            self.check()
        storage = {'smartctl', 'nvme', 'lsblk', 'blkid', 'dd', 'ddrescue', 'ddrescuelog',
                   'e2fsck', 'fsck.fat', 'fsck.exfat', 'ntfsfix', 'xfs_repair', 'btrfs',
                   'sfdisk', 'mount', 'umount', 'lsscsi', 'hdparm', 'rsync'}
        resource = resource or ('storage' if command[0] in storage else command[0])
        with self.lingering_lock:
            if not cleanup and any(group == resource for _, group in self.lingering):
                raise RuntimeError('Предыдущая команда ещё ожидает это устройство. Другие инструменты доступны.')
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, start_new_session=True, cwd=cwd, env={**os.environ, 'LC_ALL': 'C'})
        selector = selectors.DefaultSelector()
        complete = False
        try:
            self.process = process
            output = bytearray()
            truncated = False
            pending = b''
            tail = deque(maxlen=40)
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            def consume(line):
                decoded = line.decode('utf-8', errors='replace')
                tail.append(decoded[-500:])
                if log_output:
                    self.log(decoded)
                if on_line:
                    on_line(decoded)
            try:
                while selector.get_map():
                    if not cleanup:
                        self.check()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Превышено время ожидания команды')
                    for key, _ in selector.select(0.2):
                        chunk = os.read(key.fileobj.fileno(), 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if capture_output:
                            remaining = max(0, output_limit - len(output))
                            if len(chunk) > remaining:
                                truncated = True
                            output.extend(chunk[:remaining])
                        else:
                            output.extend(chunk)
                            del output[:-min(output_limit, 8192)]
                        pending += chunk
                        lines = re.split(rb'[\r\n]', pending)
                        pending = lines.pop()[-8192:]
                        for line in lines:
                            consume(line)
                if pending:
                    consume(pending)
                code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
                complete = True
                if not cleanup:
                    self.check()
                if truncated:
                    raise RuntimeError('Вывод команды превысил допустимый объём. Отчёт неполный.')
                if accepted is not None and code not in accepted:
                    if not log_output:
                        for line in tail:
                            self.log(line)
                    raise RuntimeError(f'{command[0]}: код завершения {code}. Подробности в отчёте.')
                return code, output.decode('utf-8', errors='replace')
            finally:
                selector.close()
                if not complete:
                    self._stop(process, resource)
        finally:
            selector.close()
            process.stdout.close()
            self.process = None
