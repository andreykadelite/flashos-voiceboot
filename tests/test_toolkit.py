from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jobs import Job, Cancelled
from power import parse_entries, select_once, reboot_once, BootEntry, installed_entries
from settings import Settings, validate, DEFAULTS, DurabilityError
from toolkit import memory_budget, memory_job, smart_summary, smart_job, filesystem_job, rescue_job, Device


class SettingsTests(unittest.TestCase):
    def test_directory_flush_failure_keeps_committed_state(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(folder)
            with patch('settings.os.fsync', side_effect=[None, OSError('removed')]):
                with self.assertRaises(DurabilityError):
                    settings.save({'voice': 'yuriy'})
            self.assertEqual(settings.data['voice'], 'yuriy')
            self.assertEqual(Settings(folder).data['voice'], 'yuriy')
            settings.save({'rate': 120})
            self.assertEqual(Settings(folder).data['voice'], 'yuriy')

    def test_save_load_and_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(folder)
            settings.save({'voice': 'yuriy', 'rate': 170, 'pitch': 120, 'volume': 60, 'signals': False})
            self.assertEqual(Settings(folder).data, settings.data)
            self.assertEqual(settings.data['voice'], 'yuriy')
            self.assertFalse(settings.data['signals'])

    def test_invalid_types_and_oversize_file_fall_back(self):
        self.assertEqual(validate({'voice': 'bad;command', 'rate': True, 'volume': '200'}), DEFAULTS)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'flashos/settings.json'
            path.parent.mkdir()
            path.write_text(' ' * 4097)
            settings = Settings(folder)
            self.assertEqual(settings.data, DEFAULTS)
            self.assertTrue(settings.error)

    def test_failed_atomic_write_does_not_claim_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(folder)
            settings.save({'voice': 'anna'})
            with patch('settings.os.replace', side_effect=OSError('read only')):
                with self.assertRaises(OSError):
                    settings.save({'voice': 'yuriy'})
            self.assertEqual(Settings(folder).data['voice'], 'anna')
            self.assertEqual(settings.data['voice'], 'anna')


class PowerTests(unittest.TestCase):
    def test_poweroff_still_works_with_readonly_state_partition(self):
        from menu import power_menu
        ui = Mock()
        ui.demo = False
        ui.choose.return_value = 'off'
        with patch('menu.cancel', side_effect=OSError('read only')), patch('menu.os.sync'), patch('menu.subprocess.run') as run:
            power_menu(ui, Path('/fake'), '/dev/usb')
        self.assertEqual(run.call_args.args[0], ['systemctl', 'poweroff'])

    def test_missing_efi_file_is_not_an_installed_system(self):
        guid = '11111111-1111-1111-1111-111111111111'
        entry = BootEntry('0001', 'Removed OS', f'HD(1,GPT,{guid},0x800,0x800)/File(\\EFI\\gone\\boot.efi)', True)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'mount'
            path.mkdir()
            listing = '{"blockdevices":[{"path":"/dev/fake","type":"disk","tran":"nvme","children":[{"path":"/dev/fake1","type":"part","partuuid":"' + guid + '","fstype":"vfat"}]}]}'
            firmware = 'Boot0001* Removed OS\t' + entry.path
            with patch('power.tempfile.mkdtemp', return_value=str(path)), patch('power.Path.exists', return_value=True), patch('power.subprocess.check_output', side_effect=[listing, firmware]), patch('power.subprocess.run', side_effect=[Mock(returncode=0), Mock(returncode=1), Mock(returncode=0)]) as run:
                self.assertEqual(installed_entries('/dev/usb'), [])
            self.assertIn('ro,nosuid,nodev,noexec', run.call_args_list[0].args[0])
            self.assertEqual(run.call_args_list[1].args[0][0], 'test')
            self.assertEqual(run.call_args_list[2].args[0][0], 'umount')

    def test_only_present_installed_loaders_excluding_live_usb(self):
        a, b = '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'
        text = f'''BootOrder: 0002,0001,0003
Boot0001* Windows Boot Manager\tHD(1,GPT,{a},0x800,0x80000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)
Boot0002* FlashOS\tHD(3,GPT,{b},0x800,0x80000)/File(\\EFI\\BOOT\\BOOTX64.EFI)
Boot0003* Linux\tPciRoot(0x0)/HD(1,GPT,{a},0x800,0x80000)/File(\\EFI\\debian\\shimx64.efi)
Boot0004* Network\tPciRoot(0x0)/MAC(112233445566)
Boot0005  Inactive\tHD(1,GPT,{a},0x800,0x80000)/File(\\EFI\\BOOT\\BOOTX64.EFI)
'''
        parts = {a: {'disk': '/dev/nvme0n1', 'transport': 'nvme'}, b: {'disk': '/dev/sda', 'transport': 'usb'}}
        self.assertEqual([e.number for e in parse_entries(text, parts, '/dev/sda')], ['0001', '0003'])

    def test_stale_boot_entry_does_not_write_nvram(self):
        entry = BootEntry('0001', 'Windows', 'path', True)
        with patch('power.installed_entries', return_value=[]), patch('power.subprocess.run') as run:
            with self.assertRaises(ValueError):
                select_once(entry, '/dev/sda')
            run.assert_not_called()

    def test_bootnext_without_bootorder_change(self):
        entry = BootEntry('0001', 'Windows', 'path', True)
        with patch('power.installed_entries', return_value=[entry]), patch('power.subprocess.run') as run:
            select_once(entry, '/dev/sda')
            self.assertEqual(run.call_args.args[0], ['efibootmgr', '-n', '0001'])

    def test_failed_reboot_clears_only_our_bootnext(self):
        entry = BootEntry('0001', 'Windows', 'path', True)
        failure = subprocess.CalledProcessError(1, ['systemctl', 'reboot'])
        with patch('power.select_once'), patch('power.subprocess.check_output', return_value='BootNext: 0001\n'), patch('power.subprocess.run', side_effect=[failure, None]) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                reboot_once(entry, '/dev/usb')
            self.assertEqual(run.call_args.args[0], ['efibootmgr', '-N'])

    def test_failed_reboot_preserves_another_bootnext(self):
        entry = BootEntry('0001', 'Windows', 'path', True)
        with patch('power.select_once'), patch('power.subprocess.check_output', return_value='BootNext: 0002\n'), patch('power.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'reboot')) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                reboot_once(entry, '/dev/usb')
            self.assertEqual(run.call_count, 1)


class MemoryTests(unittest.TestCase):
    def test_memory_reserve_and_small_system_refusal(self):
        self.assertEqual(memory_budget('quick', 'MemAvailable: 4194304 kB\n'), 128)
        self.assertEqual(memory_budget('extended', 'MemAvailable: 4194304 kB\n'), 3072)
        with self.assertRaises(ValueError):
            memory_budget('quick', 'MemAvailable: 262144 kB\n')

    def execute(self, code, lines):
        job = Job('RAM', lambda _: None)
        def command(args, **kwargs):
            self.assertNotIn('-p', args)
            for line in lines:
                kwargs['on_line'](line)
            return code, '\n'.join(lines)
        with patch('toolkit.memory_budget', return_value=128), patch.object(job, 'command', side_effect=command):
            memory_job('quick')(job)
        return job

    def test_reduced_allocation_reports_actual_size(self):
        job = self.execute(0, ['got 64MB (67108864 bytes), trying mlock ...locked.', 'Random Value: ok'])
        self.assertFalse(job.failed)
        self.assertIn('64 мегабайт', job.result)

    def test_oom_is_not_reported_as_defective_ram(self):
        job = self.execute(-9, ['trying mlock ...locked.'])
        self.assertTrue(job.failed)
        self.assertIn('не подтверждение неисправности', job.result)

    def test_memory_error_is_reported_and_counted(self):
        job = self.execute(4, ['trying mlock ...locked.', 'FAILURE: 0x123 != 0x456'])
        self.assertTrue(job.failed)
        self.assertEqual(job.error_count, 1)
        self.assertIn('Обнаружены ошибки', job.result)

    def test_unlocked_test_is_not_a_pass(self):
        self.assertTrue(self.execute(0, ['trying mlock ...failed']).failed)


class JobTests(unittest.TestCase):
    def test_cancel_after_remount_still_restores_read_only(self):
        device = Device('/dev/fake', 'disk', 1024, '', '', '', False, '8:0', '/dev/fake')
        with tempfile.TemporaryDirectory() as folder:
            medium = Path(folder)
            (medium / 'flashos').mkdir()
            (medium / 'flashos/flashos.marker').touch()
            job = Job('rescue', lambda _: None)
            def command_result(args, **kwargs):
                if 'remount,rw' in args:
                    raise Cancelled()
                return 0, 'ro' if args[0] == 'findmnt' else ''
            with patch('toolkit.check_device'), patch('toolkit.subprocess.check_output', side_effect=[
                    '{"filesystems":[{"source":"/dev/usb1","fstype":"exfat"}]}', 'usb\n']), patch('medium.medium_info', return_value=('/dev/usb1', 'exfat')), patch.object(job, 'command', side_effect=command_result) as command:
                with self.assertRaises(Cancelled):
                    rescue_job(device, '/dev/usb', medium)(job)
            mounts = [call for call in command.call_args_list if call.args[0][0] == 'mount']
            self.assertEqual(len(mounts), 2)
            self.assertEqual(mounts[-1].args[0][2], 'remount,ro')
            self.assertTrue(mounts[-1].kwargs['cleanup'])

    def test_recovery_cannot_write_to_nested_mount(self):
        device = Device('/dev/fake', 'disk', 1024, '', '', '', False, '8:0', '/dev/fake')
        with tempfile.TemporaryDirectory() as folder:
            medium = Path(folder)
            (medium / 'flashos').mkdir()
            (medium / 'flashos/flashos.marker').touch()
            job = Job('rescue', lambda _: None)
            with patch('toolkit.check_device'), patch('toolkit.subprocess.check_output', side_effect=[
                    '{"filesystems":[{"source":"/dev/usb1","fstype":"exfat"}]}', 'usb\n', '/dev/another1\n']), patch('medium.medium_info', return_value=('/dev/usb1', 'exfat')), patch.object(job, 'command', return_value=(0, 'ro')) as command:
                with self.assertRaisesRegex(ValueError, 'другому разделу'):
                    rescue_job(device, '/dev/usb', medium)(job)
            mounts = [call for call in command.call_args_list if call.args[0][0] == 'mount']
            self.assertEqual(len(mounts), 2)
            self.assertEqual(mounts[-1].args[0][2], 'remount,ro')
            self.assertTrue(mounts[-1].kwargs['cleanup'])
            self.assertEqual(list((medium / 'recovery').iterdir()), [])

    def test_cancel_kills_descendant_after_parent_exit(self):
        script = 'import os,time; p=os.fork(); time.sleep(20) if p==0 else None'
        job = Job('descendant', lambda j: j.command([sys.executable, '-c', script])).start()
        time.sleep(0.1)
        job.cancel()
        self.assertTrue(job.done.wait(3))
        self.assertTrue(job.cancelled)

    def test_cancel_reaps_child(self):
        job = Job('cancel', lambda j: j.command([sys.executable, '-c', 'import time;time.sleep(20)'])).start()
        deadline = time.monotonic() + 2
        while job.process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        process = job.process
        self.assertIsNotNone(process)
        job.cancel()
        self.assertTrue(job.done.wait(3))
        self.assertTrue(job.cancelled)
        self.assertIsNotNone(process.poll())

    def test_timeout_is_failure_and_log_is_bounded(self):
        job = Job('timeout', lambda j: j.command([sys.executable, '-c',
            'import time;print("x"*10000,flush=True);time.sleep(20)'], timeout=0.1)).start()
        self.assertTrue(job.done.wait(3))
        self.assertTrue(job.failed)
        self.assertTrue(all(len(line) <= 500 for line in job.lines))

    def test_smart_unknown_does_not_mean_healthy(self):
        self.assertIn('недоступна', smart_summary({})[0])
        self.assertIn('неисправность', smart_summary({'smart_status': {'passed': False}})[0])

    def test_failed_smart_query_does_not_emit_success(self):
        device = Device('/dev/fake', 'disk', 1024, '', '', '', False, '8:0', '/dev/fake')
        job = Job('SMART', lambda _: None)
        with patch('toolkit.check_device'), patch.object(job, 'command', return_value=(2, '{}')):
            smart_job(device, '/dev/usb')(job)
        self.assertTrue(job.failed)
        self.assertIn('не подтверждение неисправности', job.result)

    def test_ext4_check_is_read_only(self):
        device = Device('/dev/fake1', 'part', 1024, '', '', 'ext4', False, '8:1', '/dev/fake')
        job = Job('fs', lambda _: None)
        with patch('toolkit.check_device'), patch.object(job, 'command', return_value=(0, '')) as command:
            filesystem_job(device, '/dev/usb')(job)
        self.assertEqual(command.call_args.args[0], ['e2fsck', '-f', '-n', '/dev/fake1'])
