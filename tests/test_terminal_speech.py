from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from terminal_speech import OVERFLOW_NOTICE, SnapshotDiff, TerminalSpeech, normalize_text


class FakeSpeech:
    def __init__(self):
        self.completion = threading.Event()
        self.completion.set()
        self.last = ''
        self.calls = []
        self.stops = 0
        self.closed = False

    def say(self, text):
        self.completion.set()
        self.completion = threading.Event()
        self.last = text
        self.calls.append(text)
        return self.completion

    def stop(self):
        self.stops += 1
        self.completion.set()

    def close(self):
        self.closed = True


class TerminalSpeechTests(unittest.TestCase):
    def setUp(self):
        self.speech = FakeSpeech()
        self.queue = TerminalSpeech(self.speech)

    def drain(self, queue=None):
        queue = queue or self.queue
        for _ in range(200):
            self.speech.completion.set()
            queue.tick()
            if not queue.pending_count and self.speech.completion.is_set():
                return
        self.fail('Speech queue did not drain')

    def test_chunks_wait_for_previous_completion(self):
        self.queue.enqueue_output('Первый вывод')
        self.queue.tick()
        self.queue.enqueue_output('Второй вывод')
        for _ in range(5):
            self.queue.tick()
        self.assertEqual(self.speech.calls, ['Первый вывод'])
        self.assertEqual(self.speech.stops, 0)
        self.speech.completion.set()
        self.queue.tick()
        self.assertEqual(self.speech.calls, ['Первый вывод', 'Второй вывод'])

    def test_normal_manual_speech_waits_without_interrupting(self):
        self.queue.enqueue_output('Вывод')
        self.queue.tick()
        self.queue.speak('Строка под курсором')
        self.queue.tick()
        self.assertEqual(self.speech.calls, ['Вывод'])
        self.drain()
        self.assertEqual(self.speech.calls, ['Вывод', 'Строка под курсором'])
        self.assertEqual(self.speech.stops, 0)

    def test_priority_interrupts_and_preserves_order_of_queued_text(self):
        self.queue.enqueue_output('Текущий вывод')
        self.queue.tick()
        self.queue.enqueue_output('Следующий вывод')
        self.queue.speak('Команда: ls /home', priority=True)
        self.queue.tick()
        self.assertEqual(self.speech.stops, 1)
        self.assertEqual(self.speech.last, 'Команда: ls /home')
        self.drain()
        self.assertEqual(self.speech.calls, ['Текущий вывод', 'Команда: ls /home', 'Следующий вывод'])

    def test_pause_discards_automatic_and_stops_only_current_automatic(self):
        self.queue.enqueue_output('Старый текущий вывод')
        self.queue.tick()
        self.queue.enqueue_output('Старый следующий вывод')
        self.queue.speak('Ручная строка')
        self.queue.pause()
        self.assertEqual(self.speech.stops, 1)
        self.assertFalse(self.queue.enqueue_output('Пропущено при паузе'))
        self.drain()
        self.assertEqual(self.speech.calls, ['Старый текущий вывод', 'Ручная строка'])
        self.queue.resume()
        self.queue.enqueue_output('Новый вывод')
        self.drain()
        self.assertEqual(self.speech.calls[-1], 'Новый вывод')

    def test_pause_keeps_current_manual_speech(self):
        self.queue.speak('Ручная строка')
        self.queue.tick()
        current = self.speech.completion
        self.queue.enqueue_output('Следующий вывод')
        self.queue.pause()
        self.assertEqual(self.speech.stops, 0)
        self.assertFalse(current.is_set())
        self.assertEqual(self.queue.pending_count, 0)

    def test_manual_speech_does_not_clear_automatic_pause(self):
        self.queue.pause()
        self.queue.stop()
        self.queue.speak('Команда: pwd')
        self.assertTrue(self.queue.paused)
        self.assertFalse(self.queue.stopped)
        self.assertFalse(self.queue.enqueue_output('Автоматический вывод'))
        self.drain()
        self.assertEqual(self.speech.calls, ['Команда: pwd'])

    def test_stop_discards_everything_and_suppresses_future_output(self):
        self.queue.speak('Текущая речь')
        self.queue.tick()
        self.queue.speak('Ожидающая ручная речь')
        self.queue.enqueue_output('Ожидающий вывод')
        self.queue.stop()
        self.assertTrue(self.queue.stopped)
        self.assertEqual(self.queue.pending_count, 0)
        self.assertFalse(self.queue.enqueue_output('Пропущенный вывод'))
        self.queue.tick()
        self.assertEqual(self.speech.calls, ['Текущая речь'])

    def test_manual_command_restarts_after_stop(self):
        self.queue.stop()
        self.queue.speak('Команда: uname -a', priority=True)
        self.queue.enqueue_output('Linux')
        self.drain()
        self.assertEqual(self.speech.calls, ['Команда: uname -a', 'Linux'])

    def test_resume_restarts_after_stop_without_replaying_discarded_output(self):
        self.queue.stop()
        self.queue.enqueue_output('Старый вывод')
        self.queue.resume()
        self.queue.enqueue_output('Новый вывод')
        self.drain()
        self.assertEqual(self.speech.calls, ['Новый вывод'])

    def test_empty_manual_text_does_not_resume_stop(self):
        self.queue.stop()
        self.assertFalse(self.queue.speak('\x00\n\t'))
        self.assertTrue(self.queue.stopped)

    def test_close_is_terminal_and_does_not_close_shared_speech(self):
        self.queue.enqueue_output('Вывод')
        self.queue.tick()
        self.queue.close()
        self.queue.close()
        self.assertEqual(self.speech.stops, 1)
        self.assertFalse(self.queue.tick())
        self.assertFalse(self.queue.speak('Поздняя речь', priority=True))
        self.assertFalse(self.queue.enqueue_output('Поздний вывод'))
        self.queue.resume()
        self.assertFalse(self.speech.closed)
        self.assertEqual(self.queue.pending_count, 0)

    def test_shared_speech_in_progress_is_not_interrupted_by_output(self):
        self.speech.say('Внешняя ручная речь')
        self.queue.enqueue_output('Терминал')
        self.queue.tick()
        self.assertEqual(self.speech.calls, ['Внешняя ручная речь'])
        self.speech.completion.set()
        self.queue.tick()
        self.assertEqual(self.speech.calls[-1], 'Терминал')

    def test_pause_does_not_stop_external_speech_that_replaced_automatic(self):
        self.queue.enqueue_output('Вывод')
        self.queue.tick()
        self.speech.say('Внешняя ручная речь')
        self.queue.pause()
        self.assertEqual(self.speech.stops, 0)
        self.assertFalse(self.speech.completion.is_set())

    def test_long_text_is_split_without_losing_path_punctuation(self):
        queue = TerminalSpeech(self.speech, chunk_chars=80)
        text = '/mnt/FlashOS/' + 'a' * 160 + '.txt'
        queue.speak(text)
        self.drain(queue)
        self.assertTrue(all(len(text) <= 80 for text in self.speech.calls))
        self.assertEqual(''.join(self.speech.calls), text)

    def test_overflow_is_bounded_and_announced_once_per_burst(self):
        queue = TerminalSpeech(self.speech, max_messages=3, chunk_chars=80)
        for index in range(1000):
            queue.enqueue_output('Строка ' + str(index))
            self.assertLessEqual(queue.pending_count, 3)
        self.drain(queue)
        self.assertEqual(self.speech.calls.count(OVERFLOW_NOTICE), 1)
        self.assertEqual(self.speech.calls[:2], ['Строка 0', 'Строка 1'])
        for index in range(10):
            queue.enqueue_output('Новый поток ' + str(index))
        self.drain(queue)
        self.assertEqual(self.speech.calls.count(OVERFLOW_NOTICE), 2)

    def test_huge_single_message_has_bounded_chunks_and_one_notice(self):
        queue = TerminalSpeech(self.speech, max_messages=3, chunk_chars=80)
        queue.enqueue_output('x' * 1_000_000)
        self.assertLessEqual(queue.pending_count, 3)
        self.drain(queue)
        self.assertEqual(len(self.speech.calls), 3)
        self.assertEqual(self.speech.calls[-1], OVERFLOW_NOTICE)
        self.assertTrue(all(len(text) <= 80 for text in self.speech.calls))

    def test_priority_can_replace_full_backlog_and_still_reports_omissions_once(self):
        queue = TerminalSpeech(self.speech, max_messages=3, chunk_chars=80)
        for index in range(10):
            queue.enqueue_output('Вывод ' + str(index))
        queue.speak('Команда: id', priority=True)
        self.drain(queue)
        self.assertEqual(self.speech.calls[0], 'Команда: id')
        self.assertEqual(self.speech.calls.count(OVERFLOW_NOTICE), 1)

    def test_invalid_limits_fail_early(self):
        for kwargs in ({'max_messages': 0}, {'max_messages': True}, {'max_messages': 1},
                       {'chunk_chars': 1}, {'chunk_chars': 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TerminalSpeech(self.speech, **kwargs)


class TerminalTextTests(unittest.TestCase):
    def test_normalization_keeps_paths_and_punctuation_without_terminal_controls(self):
        raw = '\x1b[31mОшибка:\x1b[0m\t/mnt/Мой_диск/a.txt --flag="a:b"\x00\x07\u202e\n\n  Готово!  '
        self.assertEqual(normalize_text(raw), 'Ошибка: /mnt/Мой_диск/a.txt --flag="a:b"\nГотово!')

    def test_osc_title_and_empty_lines_are_not_spoken(self):
        self.assertEqual(normalize_text('\x1b]0;секретный заголовок\x07\n\r\t'), '')
        self.assertEqual(normalize_text('\x1b]0;заголовок\x1b\\Вывод'), 'Вывод')


class SnapshotDiffTests(unittest.TestCase):
    def test_initial_snapshot_and_identical_redraw(self):
        diff = SnapshotDiff()
        self.assertEqual(diff.update(['Первая строка', '', 'Вторая строка', '']), ['Первая строка', 'Вторая строка'])
        self.assertEqual(diff.update(['Первая строка', '', 'Вторая строка', '']), [])

    def test_reset_sets_silent_baseline(self):
        diff = SnapshotDiff()
        diff.reset(['root@flashos:~# ls'])
        self.assertEqual(diff.update(['root@flashos:~# ls', 'file.txt']), ['file.txt'])

    def test_scroll_with_overlap_only_announces_new_lines(self):
        diff = SnapshotDiff()
        diff.reset(['один', 'два', 'три', 'четыре'])
        self.assertEqual(diff.update(['три', 'четыре', 'пять', 'шесть']), ['пять', 'шесть'])

    def test_partial_line_append_only_announces_suffix(self):
        diff = SnapshotDiff()
        diff.reset(['Загрузка пакета'])
        self.assertEqual(diff.update(['Загрузка пакета: 10%']), [': 10%'])
        self.assertEqual(diff.update(['Загрузка пакета: 10% готово']), ['готово'])
        self.assertEqual(diff.update(['Загрузка пакета: 10% готово']), [])

    def test_append_before_new_line_is_not_repeated(self):
        diff = SnapshotDiff()
        diff.reset(['Начало', 'Часть'])
        self.assertEqual(diff.update(['Начало', 'Часть строки', 'Следующая']), ['строки', 'Следующая'])

    def test_rewritten_progress_line_is_announced(self):
        diff = SnapshotDiff()
        diff.reset(['Загрузка: 10%', 'Готово'])
        self.assertEqual(diff.update(['Загрузка: 20%', 'Готово']), ['Загрузка: 20%'])

    def test_clear_screen_does_not_repeat_deleted_text_and_resets_baseline(self):
        diff = SnapshotDiff()
        diff.reset(['Старый вывод', 'Другая строка'])
        self.assertEqual(diff.update(['', '', '']), [])
        self.assertEqual(diff.update(['Старый вывод']), ['Старый вывод'])

    def test_inserted_line_does_not_repeat_unchanged_suffix(self):
        diff = SnapshotDiff()
        diff.reset(['верх', 'низ'])
        self.assertEqual(diff.update(['верх', 'новая строка', 'низ']), ['новая строка'])

    def test_duplicate_lines_preserve_scroll_overlap(self):
        diff = SnapshotDiff()
        diff.reset(['a', 'repeat', 'repeat', 'b'])
        self.assertEqual(diff.update(['repeat', 'repeat', 'b', 'c']), ['c'])

    def test_string_input_is_bounded_by_lines_and_characters(self):
        diff = SnapshotDiff(max_lines=2, max_line_chars=20)
        self.assertEqual(diff.update('discard\n' + 'x' * 1_000_000 + '\nlast\n'), ['x' * 20, 'last'])
        self.assertEqual(diff.update('other discard\n' + 'x' * 1_000_000 + '\nlast\n'), [])

    def test_iterable_input_and_continuous_scroll_are_bounded(self):
        diff = SnapshotDiff(max_lines=3, max_line_chars=20)
        self.assertEqual(diff.update(str(index) for index in range(10000)), ['9997', '9998', '9999'])
        self.assertEqual(diff.update(['9998', '9999', '10000']), ['10000'])

    def test_controls_do_not_create_false_changes(self):
        diff = SnapshotDiff()
        diff.reset(['\x1b[31mОшибка\x1b[0m'])
        self.assertEqual(diff.update(['Ошибка']), [])

    def test_invalid_snapshot_limits(self):
        for kwargs in ({'max_lines': 0}, {'max_lines': True}, {'max_line_chars': -1}, {'max_line_chars': '20'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SnapshotDiff(**kwargs)

    def test_coordinates_read_identical_new_rows_without_repeating_old_rows(self):
        diff = SnapshotDiff()
        diff.reset(['repeat', 'repeat', 'repeat'], start_row=100)
        self.assertEqual(diff.update(['repeat', 'repeat', 'repeat'], start_row=101), ['repeat'])
        self.assertEqual(diff.update(['repeat', 'repeat', 'repeat'], start_row=101), [])
        self.assertEqual(diff.update(['repeat', 'repeat', 'repeat'], start_row=103), ['repeat', 'repeat'])

    def test_coordinates_continued_line_and_new_duplicate_line(self):
        diff = SnapshotDiff()
        diff.reset(['Часть'], start_row=20)
        self.assertEqual(diff.update(['Часть строки', 'Часть'], start_row=20), ['строки', 'Часть'])

    def test_coordinates_rewritten_line_and_scroll_overlap(self):
        diff = SnapshotDiff()
        diff.reset(['old', 'progress: 10%', 'tail'], start_row=30)
        self.assertEqual(diff.update(['progress: 20%', 'tail', 'new'], start_row=31), ['progress: 20%', 'new'])

    def test_coordinates_reset_is_silent_and_row_rollback_starts_new_screen(self):
        diff = SnapshotDiff()
        diff.reset(['same', 'old'], start_row=100)
        self.assertEqual(diff.update(['same', 'new'], start_row=0), ['same', 'new'])
        self.assertEqual(diff.update(['same', 'new'], start_row=0), [])
        diff.reset(['same', 'new'], start_row=0)
        self.assertEqual(diff.update(['same', 'new', 'later'], start_row=0), ['later'])

    def test_coordinates_clear_drops_old_row_text(self):
        diff = SnapshotDiff()
        diff.reset(['same'], start_row=0)
        self.assertEqual(diff.update([], start_row=0), [])
        self.assertEqual(diff.update(['same'], start_row=0), ['same'])

    def test_coordinates_bounds_adjust_ids_for_discarded_prefix(self):
        diff = SnapshotDiff(max_lines=2, max_line_chars=20)
        self.assertEqual(diff.update(['discard', 'repeat', 'repeat'], start_row=10), ['repeat', 'repeat'])
        self.assertEqual(diff.update(['repeat', 'repeat'], start_row=11), [])
        self.assertEqual(diff.update(['repeat', 'repeat'], start_row=12), ['repeat'])

    def test_coordinates_trailing_empty_rows_keep_absolute_row_alignment(self):
        diff = SnapshotDiff(max_lines=3)
        diff.reset(['old', 'same', '', ''], start_row=10)
        self.assertEqual(diff.update(['same', '', 'new'], start_row=11), ['new'])

    def test_switching_from_plain_to_coordinates_starts_explicit_row_baseline(self):
        diff = SnapshotDiff()
        diff.reset(['same'])
        self.assertEqual(diff.update(['same'], start_row=100), ['same'])
        self.assertEqual(diff.update(['same']), [])
        self.assertEqual(diff.update(['same', 'new']), ['new'])

    def test_invalid_coordinates_do_not_mutate_baseline(self):
        diff = SnapshotDiff()
        diff.reset(['same'], start_row=0)
        for start_row in (-1, True, 1.5, '0'):
            with self.subTest(start_row=start_row), self.assertRaises(ValueError):
                diff.update(['changed'], start_row=start_row)
            with self.subTest(start_row=start_row), self.assertRaises(ValueError):
                diff.reset(['changed'], start_row=start_row)
        self.assertEqual(diff.update(['same'], start_row=0), [])


if __name__ == '__main__':
    unittest.main()
