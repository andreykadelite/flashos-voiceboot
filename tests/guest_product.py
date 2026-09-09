"""Integration tests on disposable virtual devices; never copied to production."""
import json
from pathlib import Path
import subprocess
import time

import files
import inventory
import wifi
import network


def run(record, part, live, medium, state, digest):
    record('inventory-memory', inventory.report_job('ram'))
    record('inventory-disks', inventory.report_job('disks'))
    volume = files.Volume(part, live)
    try:
        opened = record('files-mount-readonly', volume.open)
        assert not opened.failed, opened.result
        def listing(job):
            data = files.worker(job, 'list', volume.root, 'Documents')
            assert {item['name'] for item in data['items']} == {'Пример.txt', 'data.bin', 'recovery.png'}
            text = files.worker(job, 'text', volume.root, 'Documents/Пример.txt')
            assert text['lines'] == ['Проверка копирования файлов FlashOS.']
            job.result = 'Russian text and listing verified; symlink hidden.'
        record('files-list-and-text', listing)
        copied = record('files-copy-verified', files.copy_job(volume, Path('Documents'), medium))
        assert not copied.failed, copied.result
        assert copied.warning, 'Skipped symlink was not reported'
        output = Path(copied.payload)
        assert digest(output / 'data.bin') == digest(volume.root / 'Documents/data.bin')
        assert not (output / 'skip-link').exists()
    finally:
        record('files-unmount', volume.close)
    ui_report = state / 'flashos/reports/product-test.txt'
    ui_report.parent.mkdir(exist_ok=True)
    ui_report.write_text('Integration export fixture\n')
    exported = record('reports-export', files.export_job(state, medium, live))
    assert not exported.failed, exported.result
    record('network-target-local', network.target_job('127.0.0.1'))
    try:
        wifi_test(record)
    except Exception as error:
        def failed(job):
            raise RuntimeError(str(error))
        record('wifi-fixture-error', failed)


def wifi_test(record):
    def command(args):
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=20)
    assert Path('/usr/sbin/hostapd').exists(), 'Test overlay dependencies missing'
    command(['modprobe', 'mac80211_hwsim', 'radios=2'])
    devices = []
    for _ in range(20):
        devices = sorted(p.name for p in Path('/sys/class/net').iterdir() if (p / 'wireless').exists())
        if len(devices) >= 2:
            break
        time.sleep(1)
    assert len(devices) == 2, devices
    ap, station = devices
    command(['udevadm', 'settle'])
    command(['nmcli', 'radio', 'wifi', 'on'])
    command(['nmcli', 'device', 'set', ap, 'managed', 'no'])
    for _ in range(30):
        if command(['nmcli', '-g', 'GENERAL.STATE', 'device', 'show', ap]).strip().startswith('10 '):
            break
        time.sleep(.2)
    command(['ip', 'link', 'set', ap, 'down'])
    for _ in range(10):
        mode = subprocess.run(['iw', 'dev', ap, 'set', 'type', '__ap'], capture_output=True, text=True, timeout=5)
        if mode.returncode == 0:
            break
        time.sleep(.5)
    assert mode.returncode == 0, mode.stderr
    command(['ip', 'address', 'add', '192.168.77.1/24', 'dev', ap])
    config = Path('/run/flashos-test-hostapd.conf')
    config.write_text(f'''interface={ap}
driver=nl80211
ssid=FlashOS: Test
channel=1
hw_mode=g
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase=FixturePass-42
''')
    with open('/run/flashos-test-wifi.log', 'w') as log:
        hostapd = subprocess.Popen(['hostapd', str(config)], stdout=log, stderr=log)
        dhcp = subprocess.Popen(['dnsmasq', '--keep-in-foreground', '--conf-file=/dev/null', '--port=0',
            '--interface=' + ap, '--bind-interfaces', '--dhcp-range=192.168.77.10,192.168.77.20,255.255.255.0,1h',
            '--dhcp-option=3,192.168.77.1', '--dhcp-leasefile=/run/flashos-test-leases', '--user=root', '--no-resolv'],
            stdout=log, stderr=log)
        try:
            time.sleep(3)
            assert hostapd.poll() is None and dhcp.poll() is None, Path(log.name).read_text()
            scan = record('wifi-scan-hwsim', wifi.scan_job)
            assert not scan.failed, scan.result
            matches = [p for p in scan.payload if p['ssid'] == 'FlashOS: Test' and p['device'] == station]
            assert matches, str(scan.payload) + str(scan.attachments) + '\n' + command(['nmcli', '-t', '-e', 'yes', '-f',
                'BSSID,SSID,SIGNAL,SECURITY,DEVICE', 'device', 'wifi', 'list', 'ifname', station, '--rescan', 'yes'])
            point = matches[0]
            connected = record('wifi-wpa2-dhcp-hwsim', wifi.connect_job(point, 'FixturePass-42'))
            assert not connected.failed, connected.result + '\n' + Path(log.name).read_text()
            assert '192.168.77.' in str(connected.lines)
            # Both radios share one IP namespace: an interface-bound ping to an
            # address owned locally is not a valid over-the-air reachability test.
            # Association plus DHCP above checks the client path used by the product.
            link = command(['iw', 'dev', station, 'link'])
            assert 'Connected to ' in link and 'SSID: FlashOS: Test' in link, link
            assert 'FixturePass-42' not in json.dumps(connected.attachments) + str(connected.lines)
            command(['nmcli', 'device', 'disconnect', station])
        finally:
            for process in (hostapd, dhcp):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            command(['modprobe', '-r', 'mac80211_hwsim'])
