"""Runs solely from the test overlay on the serial-identified QEMU fixture."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, '/opt/flashos')
from jobs import Job
from menu import prepare_medium, live_disk
from power import installed_entries
import toolkit
import smart
import network
from interface import UI

assert subprocess.check_output(['systemd-detect-virt'], text=True).strip() == 'qemu'
assert subprocess.check_output(['lsblk', '-dn', '-o', 'SERIAL', '/dev/vda'], text=True).strip() == 'FLASHOS-TEST-FIXTURE'
assert int(Path('/sys/class/block/vda/size').read_text()) == 256 * 1024**2 // 512
root = Path('/run/live/medium')
state = prepare_medium(root)
live = live_disk(root)
assert live != '/dev/vda'
report = state / 'flashos/toolkit-fixture.json'
results = []
def record(name, function):
    print('FIXTURE_START ' + name, flush=True)
    job = Job(name, function)
    if name == 'bound-driver-audit':
        command = job.command
        def tracked(args, **kwargs):
            print('DRIVER_COMMAND ' + ' '.join(args), flush=True)
            return command(args, **kwargs)
        job.command = tracked
    job._run(function)
    results.append({'name': name, 'failed': job.failed, 'incomplete': job.incomplete, 'result': job.result, 'lines': list(job.lines),
                    'sections': job.sections, 'attachments': list(job.attachments)})
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    with report.open('rb') as stream:
        os.fsync(stream.fileno())
    print('FIXTURE ' + name + ' failed=' + str(job.failed) + ' result=' + job.result, flush=True)
    return job
def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
before = digest('/dev/vda')
from drivers import report_job as driver_report
record('bound-driver-audit', driver_report)
disk = next(d for d in toolkit.devices(live) if d.path == '/dev/vda')
part = next(d for d in toolkit.devices(live) if d.path == '/dev/vda2')
record('hardware-cpu', toolkit.report_job('cpu'))
record('read-only-filesystem', toolkit.filesystem_job(part, live))
record('sampled-read', toolkit.read_job(disk, live))
record('partition-table', toolkit.backup_table_job(disk, live))
record('smart-unsupported', toolkit.smart_job(disk, live))
assert all(item.disk != live for item in toolkit.devices(live))
if Path('/dev/nvme0n1').exists():
    assert subprocess.check_output(['lsblk', '-dn', '-o', 'SERIAL', '/dev/nvme0n1'], text=True).strip() == 'FLASHOS-AUDIT-NVME'
    scan = record('smart-autodetect', lambda job: smart.discovery(job, live))
    target = next(item for item in scan.payload if item.path.startswith('/dev/nvme'))
    health = record('smart-nvme', smart.report_job(target, live))
    assert not health.failed, health.result
    raw_health = json.loads(health.attachments['smart.json'])
    assert raw_health['smart_status']['passed'] is True
    assert 'power_on_hours' in raw_health['nvme_smart_health_information_log']
    assert raw_health['smartctl']['output'], 'Original SMART text is missing'
    ui = UI.__new__(UI)
    ui.state_root = state
    ui.save_report('SMART NVMe integration', health.result, list(health.lines), health.attachments)
    record('sensors', toolkit.temperature_job)
    interfaces = network.wired_interfaces()
    assert interfaces
    record('dhcp', network.dhcp_job(interfaces[0]))
    record('network', network.network_job)
from guest_product import run as product_tests
product_tests(record, part, live, root, state, digest)
record('ddrescue-image', toolkit.rescue_job(part, live, root))
def verify_copy(job):
    images = sorted((root / 'recovery').glob('vda2-*.img'), key=lambda p: p.stat().st_mtime)
    assert images and digest(images[-1]) == digest('/dev/vda2')
    assert before == digest('/dev/vda'), 'Source fixture changed'
    job.result = 'Source unchanged; ddrescue image SHA256 matches.'
record('source-and-copy-hashes', verify_copy)
if Path('/dev/vdb').exists():
    from guest_recovery import run as recovery_tests
    recovery_tests(record, part, live, root, digest)
if Path('/sys/firmware/efi').exists():
    existing = subprocess.check_output(['efibootmgr'], text=True)
    if 'FlashOS Fixture' not in existing:
        subprocess.run(['efibootmgr', '-C', '-d', '/dev/vda', '-p', '1', '-L', 'FlashOS Fixture',
                        '-l', '\\EFI\\fixture\\BOOTX64.EFI'], check=True)
    def verify_entry(job):
        entries = installed_entries(live)
        assert any(e.title == 'FlashOS Fixture' for e in entries)
        job.result = 'UEFI fixture loader verified read-only.'
    record('installed-loader', verify_entry)
with open('/dev/ttyS0', 'w') as serial:
    serial.write('FLASHOS_TOOLKIT_FIXTURE_DONE\n')
