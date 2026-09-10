import unittest
from installer.progress import ProgressState


class TransferProgressTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.state = ProgressState(lambda: self.now)

    def test_separate_usb_write_read_and_local_preparation(self):
        for phase, name in [('prepare', 'Подготовка на ПК'), ('copy', 'Запись на USB'), ('verify', 'Чтение с USB')]:
            self.state.accept(dict(kind='phase', phase=phase, message=name, bytes=0, total_bytes=100 * 1024**2))
            self.now += 5
            self.state.accept(dict(kind='progress', phase=phase, message='system.bin', bytes=50 * 1024**2, total_bytes=100 * 1024**2, percent=50))
            self.assertIn(name + ': 10.0 МиБ/с', self.state.metrics())
            self.assertIn('осталось 50.0%', self.state.summary())
            self.assertIn('system.bin', self.state.summary())
            self.assertIn('примерно: 00:05', self.state.summary())

    def test_blocked_usb_does_not_show_old_speed_or_timeout(self):
        self.state.accept(dict(kind='phase', phase='copy', message='Запись'))
        self.now = 5
        self.state.accept(dict(kind='progress', phase='copy', bytes=1024**2, total_bytes=10 * 1024**2, percent=10))
        self.now = 20
        self.assertIn('ожидание устройства', self.state.metrics())
        self.assertIn('Без нового отчёта: 00:15', self.state.metrics())
        self.assertNotIn('примерно', self.state.metrics())
        self.assertFalse(self.state.terminal)

    def test_indeterminate_stage_discards_previous_bytes_and_percent(self):
        self.state.accept(dict(kind='progress', phase='copy', bytes=1024, percent=99))
        self.state.accept(dict(kind='phase', phase='flush', message='Сброс кеша'))
        self.assertIsNone(self.state.percent)
        self.assertEqual(0, self.state.done)
        self.assertNotIn('99.0%', self.state.summary())

    def test_failure_keeps_actual_progress_and_elapsed_time(self):
        self.now = 10
        self.state.accept(dict(kind='progress', phase='copy', percent=37.5))
        self.state.accept(dict(kind='error', message='Накопитель отключён'))
        self.now = 90
        self.assertEqual(37.5, self.state.percent)
        self.assertIn('00:10', self.state.metrics())
        self.assertIn('Накопитель отключён', self.state.summary())

    def test_unknown_progress_is_not_invented(self):
        for percent in [None, float('nan'), float('inf'), '50']:
            self.state.accept(dict(kind='phase', phase='partition', message='Разметка', percent=percent))
            self.assertIsNone(self.state.percent)

    def test_stop_message_survives_new_worker_phase(self):
        self.state.stopping = True
        self.state.accept(dict(kind='phase', phase='flush', message='Сброс кеша'))
        self.assertIn('Остановка запрошена', self.state.summary())

    def test_idle_state_never_claims_waiting_for_usb(self):
        self.now = 3600
        self.assertNotIn('Без нового отчёта', self.state.summary())
        self.assertNotIn('Процент этапа', self.state.summary())
        self.assertEqual('Время: —\nСкорость: —', self.state.metrics())

    def test_delayed_completion_does_not_create_fictional_transfer_speed(self):
        self.state.accept(dict(kind='phase', phase='boot', message='Проверка загрузчика'))
        self.now = 30
        self.state.accept(dict(kind='complete', phase='verify', message='Готово', bytes=1600000000, percent=100))
        self.assertEqual('boot', self.state.phase)
        self.assertNotIn('МиБ/с', self.state.summary())

    def test_small_files_do_not_predict_hours_for_next_large_image(self):
        self.state.accept(dict(kind='phase', phase='copy', message='Запись'))
        for second, done in [(100, 1000*1024**2), (110, 1100*1024**2), (120, 1100*1024**2+1024), (121, 1100*1024**2+2048)]:
            self.now = second
            self.state.accept(dict(kind='progress', phase='copy', bytes=done, total_bytes=1300*1024**2, percent=85))
        self.assertIn('КиБ/с', self.state.metrics())
        self.assertIn('примерно: 00:21', self.state.metrics())
