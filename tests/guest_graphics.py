"""Test-only F12: crash X during a harmless detached job, verify service cleanup."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def fault(ui):
    assert Path('/sys/class/dmi/id/sys_vendor').read_text().strip() in ('QEMU', 'Bochs')
    assert getattr(ui.screen, 'graphical', False)
    marker = Path('/run/flashos-fault-pid')
    marker.unlink(missing_ok=True)
    def run(job):
        def terminate_x():
            for _ in range(100):
                if marker.exists():
                    time.sleep(2)
                    for process in Path('/proc').iterdir():
                        if process.name.isdigit():
                            try:
                                if (process / 'comm').read_text().strip() == 'Xorg':
                                    os.kill(int(process.name), signal.SIGTERM)
                                    return
                            except OSError:
                                pass
                    return
                time.sleep(.1)
        threading.Thread(target=terminate_x, daemon=True).start()
        code = "import os,time; from pathlib import Path; Path('/run/flashos-fault-pid').write_text(str(os.getpid())); time.sleep(600)"
        job.command([sys.executable, '-c', code], timeout=650, resource='storage')
    ui.run_job('Испытание отказа графики: без записи на диски', run)


def verify(ui):
    log = subprocess.run(['journalctl', '-u', 'flashos-toolkit-fixture.service', '--no-pager', '-n', '40'], capture_output=True, text=True, timeout=10)
    status = subprocess.run(['systemctl', 'show', 'flashos-toolkit-fixture.service', '-p', 'Result', '-p', 'ExecMainStatus'], capture_output=True, text=True, timeout=10)
    (ui.state_root / 'flashos/fixture-service.json').write_text(json.dumps({'status': status.stdout, 'journal': log.stdout}))
    marker = Path('/run/flashos-fault-pid')
    if marker.exists() and not getattr(ui.screen, 'graphical', False):
        pid = int(marker.read_text())
        gone = not Path('/proc', str(pid)).exists()
        result = {'detached_job_pid': pid, 'job_stopped_before_console': gone, 'console': True}
        (ui.state_root / 'flashos/graphics-fallback.json').write_text(json.dumps(result))
        with open('/dev/ttyS0', 'w') as stream:
            stream.write('GRAPHICS_FALLBACK ' + json.dumps(result) + '\n')
        assert gone, 'Detached job survived into the new menu'
