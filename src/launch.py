"""Start the native display and fall back to the speaking console if X fails."""
import os
from pathlib import Path
import subprocess
import sys
import time


def previous_service_processes():
    """A task stuck in kernel I/O may survive even systemd's final SIGKILL."""
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        hierarchy, controllers, group = line.split(':', 2)
        if hierarchy == '0' and group.endswith('/flashos.service'):
            members = Path('/sys/fs/cgroup') / group.lstrip('/') / 'cgroup.procs'
            return [int(pid) for pid in members.read_text().split() if int(pid) != os.getpid()]
    return []


def main():
    if previous_service_processes():
        print('Ожидаю остановки прежних процессов. Новые операции с дисками не запускаются.', flush=True)
        sys.exit(1)
    menu = str(Path(__file__).with_name('menu.py'))
    arguments = sys.argv[1:]
    ready = Path('/run/flashos-gui-ready')
    failed = Path('/run/flashos-graphics-failed')
    if failed.exists():
        os.environ['FLASHOS_GRAPHICS_FAILED'] = '1'
    if not failed.exists() and 'flashos.text=1' not in Path('/proc/cmdline').read_text().split():
        ready.unlink(missing_ok=True)
        with open('/run/flashos-graphics.log', 'a') as log:
            try:
                graphical = subprocess.Popen(['xinit', sys.executable, menu, *arguments, '--graphical',
                    '--', '/usr/bin/Xorg', ':0', 'vt1', '-keeptty', '-nolisten', 'tcp'],
                    stdout=log, stderr=log, start_new_session=True)
            except OSError as error:
                reason = 'Не удалось запустить графическое меню: ' + str(error)
            else:
                deadline = time.monotonic() + 35
                while graphical.poll() is None and not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.2)
                if ready.exists():
                    reason = 'Графическое меню завершилось. Код: ' + str(graphical.wait())
                elif graphical.poll() is None:
                    reason = 'Графическое меню не подтвердило готовность за 35 секунд.'
                else:
                    reason = 'Графическое меню завершилось до готовности. Код: ' + str(graphical.returncode)
            print(reason, file=log, flush=True)
        failed.write_text(reason + '\n')
        # Xinit puts Xorg and its client in separate process groups. Only systemd
        # can clean the entire service cgroup and reset the VT before console use.
        print('Графическое меню недоступно. Ожидаю очистки службы перед текстовым режимом.', flush=True)
        sys.exit(1)
    os.execv(sys.executable, [sys.executable, menu, *arguments])


if __name__ == '__main__':
    main()
