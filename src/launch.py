"""Start the native display and fall back to the speaking console if X fails."""
import os
from pathlib import Path
import signal
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
            graphical = subprocess.Popen(['xinit', sys.executable, menu, *arguments, '--graphical',
                '--', '/usr/bin/Xorg', ':0', 'vt1', '-keeptty', '-nolisten', 'tcp'],
                stdout=log, stderr=log, start_new_session=True)
            deadline = time.monotonic() + 35
            while graphical.poll() is None and not ready.exists() and time.monotonic() < deadline:
                time.sleep(.2)
            if ready.exists():
                graphical.wait()
                failed.write_text('Use console after service cleanup.\n')
                # Let systemd stop the whole service cgroup, including detached storage
                # commands, before it restarts this launcher in console mode.
                sys.exit(1)
            elif graphical.poll() is None:
                os.killpg(graphical.pid, signal.SIGTERM)
                try:
                    graphical.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(graphical.pid, signal.SIGKILL)
                    graphical.wait(timeout=5)
        print('Графическое меню недоступно. Запускаю говорящий текстовый режим.', flush=True)
    os.execv(sys.executable, [sys.executable, menu, *arguments])


if __name__ == '__main__':
    main()
