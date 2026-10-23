"""Spoken application store client; all package mutations stay in the USB root."""
import json
import os
from pathlib import Path
import uuid
from contextlib import nullcontext


WARNING = ("Не все приложения корректно работают с экранным диктором. "
           "Магазин полностью озвучивается FlashOS. Внешние приложения используют Orca; "
           "их доступность зависит от поддержки AT-SPI. Alt+F4 закрывает окно, "
           "Control+Alt+Escape завершает приложение и возвращает в FlashOS. "
           "Перед этим сохраните открытые документы.")


class StoreConnectionError(RuntimeError):
    def __init__(self, message, retry_safe=False):
        super().__init__(message)
        self.retry_safe = retry_safe


def result_json(output):
    """Progress can precede the one bounded structured result."""
    for line in reversed(output.splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get('format') == 1:
            if not data.get('ok'):
                if data.get('error_kind') == 'network':
                    raise StoreConnectionError(data.get('error') or 'Нет связи с серверами Debian.',
                                               retry_safe=data.get('retry_safe') is True)
                raise RuntimeError(data.get('error') or 'Магазин не смог выполнить действие.')
            return data.get('data', {})
    raise RuntimeError('Магазин вернул неполный результат. Откройте отчёт.')


class StoreClient:
    def __init__(self, environment, ui=None):
        self.environment = environment
        self.ui = ui

    def call(self, job, action, request=None, **options):
        job.update('Проверяю хранилище приложений на флешке.')
        self.environment.open(job)
        from network_proxy import apt_proxy, redact_proxy
        proxy = apt_proxy(self.ui) if self.ui is not None and action in ('refresh', 'apply', 'repair') else nullcontext((None, {}))
        with self.environment.lock(), proxy as (proxy_directory, proxy_environment):
            directory = self.environment.root / 'var/lib/flashos-store'
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / ('request-' + uuid.uuid4().hex + '.json')
            properties = [
                'RootDirectory=' + str(self.environment.root), 'User=0', 'Group=0',
                'AmbientCapabilities=CAP_SETUID',
                'PrivatePIDs=yes', 'PrivateDevices=yes', 'PrivateIPC=yes',
                'ProtectKernelTunables=yes', 'ProtectKernelModules=yes',
                'ProtectControlGroups=yes', 'ProtectClock=yes', 'ProtectHostname=yes',
                'NoNewPrivileges=yes', 'RestrictNamespaces=yes',
                'CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER CAP_FSETID CAP_SETGID CAP_SETUID CAP_SETFCAP',
                'KillMode=control-group', 'TimeoutStopSec=180s',
                'BindsTo=flashos.service', 'After=flashos.service',
            ]
            command = ['systemd-run', '--quiet', '--wait', '--pipe', '--collect',
                       '--service-type=exec', '--expand-environment=no',
                       '--unit=flashos-store-' + uuid.uuid4().hex]
            command += ['--property=' + value for value in properties]
            if proxy_directory is not None:
                command += ['--property=BindReadOnlyPaths=' + str(proxy_directory) + ':/run/flashos-proxy',
                            '--setenv=APT_CONFIG=/run/flashos-proxy/apt.conf']
            command += ['--setenv=LANG=C.UTF-8', '--setenv=DEBIAN_FRONTEND=noninteractive',
                        '--setenv=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                        '--', '/usr/bin/python3', '/opt/flashos-store/packages.py', action]
            if request is not None:
                with path.open('x', encoding='utf-8') as stream:
                    os.chmod(path, 0o600)
                    json.dump(request, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                command += ['--request', '/var/lib/flashos-store/' + path.name]
            for name, value in options.items():
                command += ['--' + name.replace('_', '-'), str(value)]
            mutation = action in ('apply', 'repair')
            if mutation:
                job.check()
                job.cancellation_deferred = True
                job.update('Устанавливаю пакеты. Остановка возможна после безопасного завершения записи.')
            try:
                def progress(line):
                    line = redact_proxy(line, proxy_environment)
                    job.log(line)
                    if not line.startswith('{'):
                        job.update(line[:250])
                code, output = job.command(command, timeout=7200, accepted=None,
                    output_limit=2 * 1024 * 1024, cleanup=mutation, resource='applications',
                    log_output=False, on_line=progress)
                try:
                    data = result_json(output)
                except StoreConnectionError as error:
                    error = StoreConnectionError(redact_proxy(str(error), proxy_environment), error.retry_safe)
                    job.store_connection_error = error
                    raise error from None
                except RuntimeError as error:
                    raise RuntimeError(redact_proxy(str(error), proxy_environment)) from None
                if code != 0:
                    raise RuntimeError('Служба магазина завершилась с ошибкой. Код: ' + str(code) +
                                       '. Результат не подтверждён; откройте отчёт.')
                if mutation:
                    job.cancel_event.clear()
                job.payload = data
                job.result = redact_proxy(data.get('message', 'Действие магазина завершено и проверено.'), proxy_environment)
                return data
            finally:
                if mutation:
                    job.cancellation_deferred = False
                path.unlink(missing_ok=True)
