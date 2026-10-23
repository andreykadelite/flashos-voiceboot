"""Diagnostic failures must preserve useful evidence without reporting a clean check."""
import json
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import drivers
import hardware
import toolkit
from jobs import Job


class DriverReportTests(unittest.TestCase):
    def report(self, responses):
        job = Job('drivers', None)
        entry = {'bus': 'pci', 'address': '0000:00:01.0', 'name': 'Test device',
                 'driver': 'test', 'builtin': True, 'modules': [], 'alias': 'pci:test'}
        with patch.object(job, 'command', side_effect=[(0, json.dumps([entry]))] + responses) as command:
            drivers.report_job(job)
        self.assertIn('Подключённый драйвер ядра: test', dict(job.sections)['PCI 0000:00:01.0. Test device'])
        return job, command

    def test_timeout_in_auxiliary_probe_preserves_drivers_and_later_kernel_errors(self):
        error = 'test: failed to load firmware test.bin'
        job, command = self.report([TimeoutError('PCI query timed out'), (0, 'USB tree'),
                                    (0, 'hwinfo'), (0, error)])
        self.assertTrue(job.incomplete)
        self.assertTrue(job.warning)
        self.assertFalse(job.failed)
        self.assertEqual(command.call_args.args[0][0], 'journalctl')
        self.assertIn(error, dict(job.sections)['Ошибки загрузки прошивок'])
        self.assertEqual(job.attachments['kernel.txt'], error)
        self.assertIn('Недоступно', job.sections[2][0])

    def test_unreadable_kernel_journal_is_not_a_clean_firmware_check(self):
        for response in ((1, 'Permission denied'), TimeoutError('Journal query timed out')):
            with self.subTest(response=response):
                job, _ = self.report([(0, 'PCI'), (0, 'USB'), (0, 'hwinfo'), response])
                self.assertTrue(job.incomplete)
                rows = '\n'.join(dict(job.sections)['Ошибки загрузки прошивок'])
                self.assertIn('не подтверждено', rows)
                self.assertNotIn('ошибок прошивки не найдено', rows)
                self.assertIn('не завершена', job.result)

    def test_successful_empty_kernel_journal_reports_bounded_clean_check(self):
        job, _ = self.report([(0, 'PCI'), (0, 'USB'), (0, 'hwinfo'), (0, '')])
        self.assertFalse(job.incomplete or job.warning or job.failed)
        self.assertIn('В просмотренной части', dict(job.sections)['Ошибки загрузки прошивок'][0])


class SensorReportTests(unittest.TestCase):
    def report(self, code, data):
        job = Job('sensors', None)
        with patch.object(job, 'command', return_value=(code, json.dumps(data))), \
             patch('hardware.Path') as sysfs:
            sysfs.return_value.glob.return_value = []
            hardware.temperature_job(job)
        return job

    def test_partial_sensor_output_keeps_measurements_and_reports_incomplete(self):
        job = self.report(1, {'chip': {'CPU': {'temp1_input': 42.5}}})
        self.assertTrue(job.incomplete)
        self.assertFalse(job.failed)
        self.assertIn('частично', job.result)
        self.assertIn('42.5', job.sections[0][1][0])

    def test_invalid_chip_does_not_discard_other_sensor_measurements(self):
        job = self.report(0, {'unavailable': None, 'chip': {'CPU': {'temp1_input': 42.5}}})
        self.assertTrue(job.incomplete)
        self.assertIn('42.5', job.sections[0][1][0])

    def test_nonfinite_measurement_is_not_a_successful_sensor_reading(self):
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value):
                job = self.report(0, {'chip': {'CPU': {'temp1_input': value}}})
                self.assertTrue(job.incomplete)
                self.assertEqual(job.sections, [])
                self.assertNotEqual(job.result, 'Показания готовы.')

    def test_sensor_alarm_remains_a_warning_with_available_measurements(self):
        job = self.report(0, {'chip': {'CPU': {'temp1_input': 42.5, 'temp1_crit_alarm': 1}}})
        self.assertTrue(job.warning)
        self.assertFalse(job.incomplete or job.failed)
        self.assertIn('тревогу', job.result)

    def test_invalid_numeric_value_preserves_other_rows_and_reports_partial(self):
        for value in ('not-a-number', None, True, False, [], {}, 10 ** 400, -(10 ** 400)):
            with self.subTest(value=value):
                job = self.report(0, {'chip': {'CPU': {'temp1_input': 42.5, 'temp2_input': value,
                                                     'fan1_input': 1200}}})
                self.assertTrue(job.incomplete)
                self.assertFalse(job.warning or job.failed)
                self.assertIn('частично', job.result)
                rows = job.sections[0][1]
                self.assertEqual(len(rows), 2)
                self.assertIn('42.5', rows[0])
                self.assertIn('1200', rows[1])

    def test_only_invalid_values_cannot_report_ready_or_sensor_alarm(self):
        for value in ('not-a-number', True, 10 ** 400):
            with self.subTest(value=value):
                job = self.report(0, {'chip': {'CPU': {'temp1_input': value, 'temp1_crit_alarm': value}}})
                self.assertTrue(job.incomplete)
                self.assertFalse(job.warning or job.failed)
                self.assertEqual(job.sections, [])
                self.assertNotEqual(job.result, 'Показания готовы.')


class ImageChecksumTests(unittest.TestCase):
    digest = hashlib.sha256(b'ISO content').hexdigest()

    def report(self, reference):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'my image.iso'
            path.write_bytes(b'ISO content')
            path.with_name(path.name + '.sha256').write_bytes(reference)
            job = Job('checksum', None)
            def command(_args, **kwargs):
                kwargs['on_line'](json.dumps({'sha256': self.digest}))
                return 0, ''
            with patch.object(job, 'command', side_effect=command):
                toolkit.checksum_job(SimpleNamespace(path=path, size=path.stat().st_size))(job)
        return job

    def test_manifest_uses_selected_filename_instead_of_first_hash(self):
        reference = '0' * 64 + '  another.iso\n' + self.digest + ' *./my image.iso\n'
        job = self.report(reference.encode())
        self.assertFalse(job.failed or job.incomplete)
        self.assertEqual(job.result, 'Контрольная сумма совпадает.')

    def test_reference_for_another_file_cannot_mark_selected_image_corrupt(self):
        job = self.report(('0' * 64 + '  another.iso\n').encode())
        self.assertFalse(job.failed)
        self.assertTrue(job.incomplete)
        self.assertIn(self.digest, '\n'.join(job.lines))

    def test_matching_filename_with_wrong_hash_reports_mismatch(self):
        job = self.report(('0' * 64 + '  my image.iso\n').encode())
        self.assertTrue(job.failed)
        self.assertIn('НЕ совпадает', job.result)

    def test_conflicting_matching_references_do_not_choose_one_arbitrarily(self):
        reference = self.digest + '  my image.iso\n' + '0' * 64 + ' *my image.iso\n'
        job = self.report(reference.encode())
        self.assertTrue(job.incomplete)
        self.assertFalse(job.failed)

    def test_unreadable_reference_does_not_discard_computed_hash(self):
        job = self.report(b'\xff\xfe')
        self.assertTrue(job.incomplete)
        self.assertFalse(job.failed)
        self.assertIn(self.digest, '\n'.join(job.lines))


if __name__ == '__main__':
    unittest.main()
