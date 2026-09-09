"""Collect release evidence only after fresh VM scenarios and source checks pass."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import unittest

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / 'src'))
from help_content import VERSION

suite = unittest.defaultTestLoader.discover(str(project / 'tests'))
unit = unittest.TextTestRunner(verbosity=0).run(suite)
assert unit.wasSuccessful()
build = json.loads((project / 'dist/toolkit-build-verification.json').read_text())
for name, expected in build['sources_sha256'].items():
    assert hashlib.sha256((project / 'src' / name).read_bytes()).hexdigest() == expected
assert build['sources_match'] and build['no_test_fixture']
results = json.loads((project / 'dist/toolkit-guest-reports/toolkit-fixture.json').read_text())
names = {row['name']: row for row in results}
required = {'hardware-cpu', 'read-only-filesystem', 'sampled-read', 'partition-table', 'smart-unsupported',
    'smart-autodetect', 'smart-nvme', 'sensors', 'dhcp', 'network', 'inventory-memory', 'inventory-disks',
    'files-mount-readonly', 'files-list-and-text', 'files-copy-verified', 'files-unmount', 'reports-export',
    'network-target-local', 'wifi-scan-hwsim', 'wifi-wpa2-dhcp-hwsim', 'ddrescue-image', 'source-and-copy-hashes',
    'partclone-backup', 'partclone-verify', 'partclone-corrupt-rejected', 'partclone-restore-readback',
    'restored-files-match', 'partclone-smaller-filesystem-backup', 'partclone-corrupt-map-rejected',
    'photorec-carve', 'photorec-content-match', 'bound-driver-audit', 'installed-loader'}
required |= {f'partclone-{fs}-{step}' for fs in ('vfat', 'exfat', 'ntfs') for step in ('backup', 'restore', 'file-match')}
assert required <= names.keys(), 'Missing scenarios: ' + str(required - names.keys())
expected_failures = {'smart-unsupported', 'partclone-corrupt-rejected', 'partclone-corrupt-map-rejected'}
assert not [row for row in results if row['failed'] and row['name'] not in expected_failures]
for name in required:
    assert names[name]['failed'] == (name in expected_failures), (name, names[name]['result'])
    if name not in expected_failures:
        assert not names[name]['incomplete'], (name, names[name]['result'])
fallback = json.loads((project / 'dist/toolkit-guest-reports/graphics-fallback.json').read_text())
assert fallback['job_stopped_before_console'] and fallback['console']
service = json.loads((project / 'dist/toolkit-guest-reports/fixture-service.json').read_text())
assert 'Result=success' in service['status'] and 'ExecMainStatus=0' in service['status'], service
secure = (project / 'dist/vm-secure-recovery.serial.log').read_text(errors='replace')
bios = (project / 'dist/vm-bios-recovery.serial.log').read_text(errors='replace')
assert 'secure_boot=1 menu=main' in secure and 'FLASHOS_TOOLKIT_FIXTURE_DONE' in secure
assert 'GRAPHICS_FALLBACK ' in secure and 'job_stopped_before_console": true' in secure
assert 'secure_boot=0 menu=main' in bios
assert 'FIXTURE_START' not in bios, 'BIOS test must use only the production image'
visual = json.loads((project / 'dist/graphical-verification.json').read_text())
assert len(visual) == 4 and all(row['keys'] == 'pass' for row in visual)
deleted = json.loads((project / 'dist/photorec-deleted-verification.json').read_text())
assert deleted['deleted_file_recovered'] and deleted['sha256_matches'] and deleted['source_image_unchanged']
audio = []
for mode in ('secure', 'bios'):
    evidence = json.loads((project / f'dist/vm-{mode}-recovery.evidence.json').read_text())
    assert evidence['peak'] > 100 and evidence['nonzero_samples'] > 1000
    audio.append(evidence)
hashes = {}
for name in ('filesystem.squashfs', 'vmlinuz', 'initrd'):
    path = project / 'dist/usb/flashos' / name
    with path.open('rb') as stream:
        hashes[name] = {'bytes': path.stat().st_size, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
command_tree = ast.parse((project / 'tools/verify-runtime.py').read_text())
commands = next(ast.literal_eval(node.value) for node in command_tree.body if isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'commands')
result = {'completed_utc': datetime.now(timezone.utc).isoformat(), 'version': VERSION,
    'unit_tests': unit.testsRun, 'integration_scenarios': len(required), 'expected_rejections': sorted(expected_failures),
    'runtime_commands': len(commands), 'graphics': visual, 'graphics_failure_cleanup': fallback,
    'deleted_file_recovery': deleted,
    'secure_boot_hda': True, 'bios_ac97_2gb': True, 'physical_host_booted_from_usb': False,
    'files': hashes, 'audio': audio}
(project / 'dist/recovery-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
(project / 'docs/RECOVERY-TESTS-RU.md').write_text(f'''# Испытания FlashOS {VERSION}

Проверка завершена: {result['completed_utc']}.

- Автоматические тесты Python: {unit.testsRun}, все пройдены.
- Проверенные команды в загрузочной системе: {len(commands)}; Tkinter загружается.
- Сценарии на отдельных виртуальных устройствах QEMU: {len(required)}.
- Загрузка UEFI с Secure Boot и звуком Intel HDA; загрузка BIOS с AC97 и 2 ГБ памяти.
- Графическое меню: 640×480, 800×600, 1280×800 и 1920×1080; клавиши, выбор мышью и длинные подтверждения.
- После принудительного отказа Xorg прежняя отдельная команда завершилась до запуска текстового меню.

Созданы и восстановлены резервные копии ext4, FAT32, exFAT и NTFS. Для FAT32, exFAT и NTFS исходная тестовая файловая система после сохранения образа была переформатирована: простое отсутствие записи не могло дать успешный результат. Восстановленные блоки проверены после сброса буферов, содержимое файлов совпало по SHA256. Проверена файловая система меньшего размера внутри большего раздела.

Намеренно повреждённые образ и карта блоков отклонены до записи. Отсутствие SMART у виртуального VirtIO-диска показано как недоступность оценки; это ожидаемый результат, а не неисправность. У виртуального NVMe проверены структурированные данные SMART.

PhotoRec нашёл контрольный PNG. В отдельном испытании на новом образе ext4 файл был записан, синхронизирован и удалён; PhotoRec восстановил его с той же SHA256, не изменив исходный образ.

Также проверены сведения о памяти и дисках, файловый просмотр, копирование с проверкой содержимого, экспорт отчётов, DHCP, MTR, WPA2 и DHCP через два виртуальных радио, ddrescue и загрузочная запись UEFI.

Содержимое squashfs сверено с исходниками. Тестовые сценарии, hostapd, dnsmasq и механизм искусственного отказа графики в рабочий образ не включены. Контрольные суммы и подробные результаты: `dist/recovery-verification.json`, `dist/toolkit-build-verification.json`, `dist/toolkit-guest-reports`.

Физический ноутбук с новой сборкой в ходе этих испытаний не загружался. Виртуальные проверки не подтверждают поддержку каждого контроллера, звукового кодека или прошивки UEFI. На незнакомом ПК Secure Boot может потребовать регистрацию ключа Ventoy.

Разработка: Зотов Андрей Анатольевич. Контакты: Telegram @zotovsmol, andreykadelite@ya.ru. При изменении проекта сохраняйте указание исходного автора.
''', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
