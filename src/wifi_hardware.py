"""Bounded recovery of missed Wi-Fi coldplug without resetting an existing driver."""
from pathlib import Path
import platform
import re
import stat
import subprocess
import time

from network import wireless_interfaces


MODULE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,63}\Z')
PCI_ALIAS = re.compile(r'pci:v[0-9A-Fa-f]{8}d[0-9A-Fa-f]{8}sv[0-9A-Fa-f]{8}'
                       r'sd[0-9A-Fa-f]{8}bc[0-9A-Fa-f]{2}sc[0-9A-Fa-f]{2}i[0-9A-Fa-f]{2}\Z')
USB_ALIAS = re.compile(r'usb:v[0-9A-Fa-f]{4}p[0-9A-Fa-f]{4}d[0-9A-Fa-f]{4}'
                       r'dc[0-9A-Fa-f]{2}dsc[0-9A-Fa-f]{2}dp[0-9A-Fa-f]{2}'
                       r'ic[0-9A-Fa-f]{2}isc[0-9A-Fa-f]{2}ip[0-9A-Fa-f]{2}in[0-9A-Fa-f]{2}\Z')


def _driver(device):
    path = device / 'driver'
    try:
        information = path.lstat()
    except FileNotFoundError:
        return ''
    return path.resolve().name if stat.S_ISLNK(information.st_mode) else 'unknown_binding'


def _command(job, arguments, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError('Истекло время проверки загрузки драйверов Wi-Fi.')
    return job.command(arguments, timeout=min(4, remaining), accepted=None,
                       log_output=False, resource='network', output_limit=16384)


def _module_file(filename, module_root, version):
    path = Path(filename)
    if not path.is_absolute() or not re.search(r'\.ko(?:\.(?:xz|zst|gz))?$', path.name):
        return False
    try:
        return path.is_file() and path.resolve(strict=True).is_relative_to(
            (module_root / version / 'kernel/drivers/net/wireless').resolve(strict=True))
    except OSError:
        return False


def _load_plan_matches(lines, filename, module_root, version):
    try:
        expected = Path(filename).resolve(strict=True)
        base = (module_root / version).resolve(strict=True)
        found = False
        for fields in lines:
            if len(fields) < 2 or fields[0] not in ('insmod', 'builtin'):
                return False
            if fields[0] == 'builtin':
                if len(fields) != 2 or not MODULE.fullmatch(fields[1]):
                    return False
                continue
            path = Path(fields[1])
            if not path.is_file() or not path.resolve(strict=True).is_relative_to(base):
                return False
            found = found or path.resolve(strict=True) == expected
        return found
    except OSError:
        return False


def _inventory(job, sys_root, module_root, version, deadline, errors):
    result = []
    for bus, pattern in (('pci', PCI_ALIAS), ('usb', USB_ALIAS)):
        for device in sorted((sys_root / 'bus' / bus / 'devices').glob('*'))[:128]:
            job.check()
            try:
                if bus == 'pci':
                    if (device / 'class').read_text().strip().lower() != '0x028000':
                        continue
                elif ':' not in device.name:
                    continue
                with (device / 'modalias').open() as stream:
                    alias = stream.read(256).strip()
                if not pattern.fullmatch(alias):
                    errors.append('Недопустимый идентификатор ' + bus.upper() + ' ' + device.name)
                    continue
                code, output = _command(job, ['modprobe', '-S', version, '--resolve-alias', alias], deadline)
                if code:
                    errors.append('Не удалось сопоставить драйвер: ' + bus.upper() + ' ' + device.name)
                    continue
                for name in output.splitlines()[:8]:
                    name = name.strip().replace('-', '_')
                    if not MODULE.fullmatch(name):
                        errors.append('Недопустимое имя модуля для ' + device.name)
                        continue
                    code, filename = _command(job, ['modinfo', '-k', version, '-F', 'filename', name], deadline)
                    if code or not _module_file(filename.strip(), module_root, version):
                        continue
                    result.append({'bus': bus, 'address': device.name, 'alias': alias,
                                   'module': name, 'filename': filename.strip(),
                                   'driver': _driver(device),
                                   'already_loaded': (sys_root / 'module' / name).is_dir()})
                    if len(result) >= 16:
                        return result
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                errors.append(str(error)[:300])
                if time.monotonic() >= deadline:
                    return result
    return result


def recover_missing_interfaces(job, sys_root=Path('/sys'), modules_root=Path('/lib/modules'),
                               kernel_version=None):
    """Load an unloaded matching wireless module once; never unload, reset or rebind."""
    report = {'attempted': False, 'loaded': [], 'devices': [], 'errors': []}
    try:
        if wireless_interfaces():
            report['skipped'] = 'wireless_interface_exists'
            return report
    except OSError:
        report['skipped'] = 'wireless_inventory_unavailable'
        return report
    version = kernel_version or platform.release()
    if not re.fullmatch(r'[A-Za-z0-9_.+~-]{1,128}', version):
        report['skipped'] = 'invalid_kernel_version'
        return report
    sys_root, modules_root = Path(sys_root), Path(modules_root)
    if not (sys_root / 'module').is_dir():
        report['skipped'] = 'module_inventory_unavailable'
        return report
    deadline = time.monotonic() + 12
    report['devices'] = _inventory(job, sys_root, modules_root, version, deadline, report['errors'])
    attempted = set()
    for item in report['devices']:
        job.check()
        device = sys_root / 'bus' / item['bus'] / 'devices' / item['address']
        name = item['module']
        try:
            # Discovery can race with udev or a newly attached adapter. Recheck
            # all guards immediately before loading, including the live alias.
            if wireless_interfaces():
                report['skipped'] = 'wireless_interface_appeared'
                break
            if _driver(device):
                item['status'] = 'driver_already_bound'
                continue
            if (sys_root / 'module' / name).is_dir():
                item['status'] = 'module_already_loaded'
                continue
            if (device / 'modalias').read_text().strip() != item['alias']:
                item['status'] = 'device_changed'
                continue
            if name in attempted or len(attempted) >= 4:
                item['status'] = 'attempt_limit'
                continue
            code, plan = _command(job, ['modprobe', '-S', version, '--use-blacklist',
                                       '--show-depends', name], deadline)
            lines = [line.split() for line in plan.splitlines() if line.strip()]
            if code or not lines:
                item['status'] = 'blacklisted_or_unavailable'
                continue
            if not _load_plan_matches(lines, item['filename'], modules_root, version):
                item['status'] = 'unexpected_load_plan'
                continue
            # A dry run is only evidence; recheck state after it as well.
            if (wireless_interfaces() or _driver(device) or (sys_root / 'module' / name).is_dir()
                    or (device / 'modalias').read_text().strip() != item['alias']):
                item['status'] = 'device_became_active'
                continue
            job.check()
            attempted.add(name)
            report['attempted'] = True
            code, output = _command(job, ['modprobe', '-S', version, '--use-blacklist', name], deadline)
            if code:
                item['status'] = 'load_failed'
                item['exit_code'] = code
                item['load_error'] = output.strip()[:1000]
            elif (sys_root / 'module' / name).is_dir():
                report['loaded'].append(name)
                item['status'] = 'module_loaded'
            else:
                item['status'] = 'load_not_confirmed'
            for _ in range(4):
                if wireless_interfaces() or time.monotonic() >= deadline:
                    break
                job.cancel_event.wait(.25)
                job.check()
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            report['errors'].append(str(error)[:300])
            if time.monotonic() >= deadline:
                break
    return report
