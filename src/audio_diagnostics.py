"""Bounded local sound diagnostics, stored on the USB service partition."""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

_running = threading.Lock()


def collect(destination):
    commands = {
        'kernel': ['uname', '-r'],
        'pci': ['lspci', '-nn'],
        'usb': ['lsusb'],
        'alsa_cards': ['aplay', '-l'],
        'pipewire': ['pw-dump'],
        'routing': ['wpctl', 'status'],
        'services': ['journalctl', '-b', '--no-pager', '-n', '100',
                     '-u', 'pipewire.service', '-u', 'wireplumber.service', '-u', 'flashos.service'],
    }
    report = {'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    for key, command in commands.items():
        try:
            result = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=4)
            report[key] = (result.stdout + result.stderr)[:131072]
        except (OSError, subprocess.SubprocessError) as error:
            report[key] = str(error)
    try:
        kernel = subprocess.run(['dmesg'], capture_output=True, text=True, errors='replace', timeout=4)
        report['audio_kernel'] = '\n'.join(line for line in kernel.stdout.splitlines()
            if re.search(r'snd|sof|hda|audio|soundwire|firmware|cs35|tas27|acp', line, re.I))[-65536:]
    except (OSError, subprocess.SubprocessError) as error:
        report['audio_kernel'] = str(error)
    destination = Path(destination)
    temporary = destination.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
    directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def save_async(state_root, callback=None, delay=0):
    def worker():
        if delay:
            time.sleep(delay)
        if not _running.acquire(blocking=False):
            if callback:
                callback('Сведения о звуке уже сохраняются. Подождите.')
            return
        try:
            collect(Path(state_root) / 'flashos/audio-last.json')
            if callback:
                callback('Сведения о звуке сохранены на разделе FLASHBOOT, в папке flashos.')
        except OSError:
            if callback:
                callback('Не удалось сохранить сведения о звуке.')
        finally:
            _running.release()
    threading.Thread(target=worker, daemon=True).start()
