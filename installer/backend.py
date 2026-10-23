"""Run one verified operation, retaining logs and waiting for disk writers."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import threading
from installer.bundle import Bundle
from installer.model import validate_request
from installer.windows import inventory, is_admin, OperationMutex, powershell
from installer._release import ENGINE_SCRIPT
from installer.process_group import ProcessGroup
from installer.secure_run import SecureRun
from installer.disk_lease import DiskLease
from installer.boot_verify import verify_boot


class Operation:
    def __init__(self, request, emit):
        self.request = request
        self.callback = emit
        self.stop = threading.Event()
        self.secure = SecureRun()
        self.root = self.secure.path
        self.work = self.root / 'work'
        self.terminal = False
        self.changed = False
        self.log_warning = False

    def cancel(self):
        self.stop.set()
        (self.root / 'cancel').touch(exist_ok=True)

    def emit(self, event):
        event['time'] = datetime.now(timezone.utc).isoformat()
        self.changed |= bool(event.get('changed'))
        try:
            with (self.root / 'events.jsonl').open('a', encoding='utf-8') as file:
                file.write(json.dumps(event, ensure_ascii=False) + '\n')
        except OSError as error:
            if not self.log_warning:
                self.log_warning = True
                self.callback(dict(kind='log', message=f'Не удалось сохранить журнал на компьютере: {error}', phase='logging'))
        if event['kind'] in ('complete', 'error', 'cancelled'):
            self.terminal = True
            try:
                (self.root / 'result.json').write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError:
                self.log_warning = True
        self.callback(event)

    def run(self):
        # Hold the cross-process writer lock through readback AND final cleanup.
        try:
            with OperationMutex():
                self._run_locked()
        except Exception as error:
            if not self.terminal:
                self.emit(dict(kind='error', message=str(error), changed=self.changed))
            self.secure.close()

    def _run_locked(self):
        process = None
        group = None
        lease = None
        watchdog = None
        watch_stop = threading.Event()
        device_lost = threading.Event()
        pending_complete = None
        try:
            if not is_admin():
                raise RuntimeError('Для операции нужны права администратора.')
            self.emit(dict(kind='phase', phase='preflight', message='Проверка выбранного USB-накопителя и условий операции', percent=None))
            current = next((d for d in inventory() if d['number'] == self.request['target']['number']), None)
            if current is None:
                raise ValueError('Выбранный накопитель отключён.')
            validate_request(self.request, current)
            bundle = Bundle()
            if self.request['action'] == 'format':
                self.work.mkdir()
                (self.work / 'manifest.json').write_text(json.dumps(bundle.manifest, ensure_ascii=False), encoding='utf-8')
            else:
                required = None
                if self.request['action'] == 'verify':
                    required = {('engine', 'boot/boot.img'), ('engine', 'boot/core.img.xz'),
                                ('engine', 'ventoy/ventoy.disk.img.xz'),
                                ('boot', 'flashos/efi/grub.cfg'), ('boot', 'EFI/debian/grub.cfg')}
                bundle.extract(self.work, self.emit, self.stop.is_set, required=required)
            current = next((d for d in inventory() if d['number'] == self.request['target']['number']), None)
            if current is None:
                raise ValueError('Накопитель отключён во время подготовки.')
            validate_request(self.request, current)
            if self.stop.is_set():
                raise InterruptedError('Подготовка отменена. Флешка не изменена.')
            lease = DiskLease(self.request['target'])
            request = dict(self.request, manifest=str(self.work / 'manifest.json'), objects=str(self.work / 'objects'),
                           engine=str(self.work / 'engine'), protected_paths=[str(Path(sys.argv[0]).resolve()), sys.executable])
            (self.root / 'request.json').write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
            script = self.root / 'engine.ps1'
            # BOM is necessary for Russian strings in Windows PowerShell 5.1.
            script.write_text(ENGINE_SCRIPT, encoding='utf-8-sig')
            group = ProcessGroup()
            lease.check()
            process = group.start([powershell(), '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                        '-File', str(script), '-RequestPath', str(self.root / 'request.json')],
                                       )
            def watch_device():
                while not watch_stop.wait(.1):
                    try:
                        lease.check()
                    except OSError:
                        device_lost.set()
                        group.close()
                        return
            watchdog = threading.Thread(target=watch_device, daemon=True)
            watchdog.start()
            for raw in iter(process.stdout.readline, b''):
                line = raw.decode('utf-8-sig', errors='replace').strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict) or 'kind' not in event:
                        raise ValueError()
                except (ValueError, TypeError):
                    event = dict(kind='log', message=line, phase='system')
                if event['kind'] == 'complete':
                    pending_complete = event
                else:
                    self.emit(event)
            code = process.wait()
            if device_lost.is_set():
                self.terminal = False
                raise RuntimeError('Связь с USB-накопителем потеряна. Все процессы записи остановлены. Подключите флешку и повторите операцию.')
            if pending_complete is not None and code == 0 and not self.terminal:
                if self.request['action'] != 'format':
                    verify_boot(lease, self.work, bundle.manifest, self.emit, self.stop.is_set,
                                self.request['style'] if self.request['action'] == 'install' else None)
                self.emit(pending_complete)
            elif pending_complete is not None and not self.terminal:
                raise RuntimeError(f'Системная команда сообщила завершение, но вернула ошибку (код {code}).')
            if not self.terminal:
                raise RuntimeError(f'Системная команда завершилась без итогового отчёта (код {code}). Проверьте журнал.')
        except Exception as error:
            # A Python/UI error must never leave an independent disk writer behind.
            if process is not None and process.poll() is None:
                self.emit(dict(kind='log', message='Ожидание завершения системной записи после ошибки.', phase='waiting'))
                process.communicate()
            if not self.terminal:
                message = str(error)
                if self.changed:
                    message += ' Данные на флешке уже изменены; повторите установку.'
                self.emit(dict(kind='cancelled' if isinstance(error, InterruptedError) else 'error', message=message, changed=self.changed))
        finally:
            watch_stop.set()
            if watchdog is not None:
                watchdog.join(timeout=5)
            if group is not None:
                group.close()
            if lease is not None:
                lease.close()
            for name in ('cli_log.txt', 'cli_percent.txt', 'cli_done.txt'):
                try:
                    source = self.work / 'engine' / name
                    if source.is_file():
                        shutil.copyfile(source, self.root / name)
                except OSError as error:
                    self.callback(dict(kind='log', message=f'Не удалось сохранить {name}: {error}', phase='cleanup'))
            # Only this operation's resolved work folder is disposable; logs remain.
            resolved_root, resolved_work = self.root.resolve(), self.work.resolve()
            if self.work.exists() and not self.work.is_symlink() and resolved_work.parent == resolved_root:
                try:
                    shutil.rmtree(resolved_work)
                except OSError as error:
                    self.callback(dict(kind='log', message=f'Временные файлы остались в {self.work}: {error}', phase='cleanup'))
            self.callback(dict(kind='finished', message=f'Журнал: {self.root}', phase='finished'))
            self.secure.close()
