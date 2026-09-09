from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import audio_diagnostics as diagnostics


class DiagnosticTests(unittest.TestCase):
    def test_delayed_startup_does_not_block_manual_request(self):
        sleeping, release, saved, finished = (threading.Event() for _ in range(4))
        calls = []
        def collect(_):
            calls.append(True)
            if len(calls) == 2:
                finished.set()
        def delay(_):
            sleeping.set()
            release.wait(2)
        with patch.object(diagnostics.time, 'sleep', side_effect=delay), patch.object(diagnostics, 'collect', side_effect=collect):
            diagnostics.save_async('/test', delay=8)
            self.assertTrue(sleeping.wait(1))
            diagnostics.save_async('/test', lambda _: saved.set())
            self.assertTrue(saved.wait(1))
            release.set()
            self.assertTrue(finished.wait(1))

    def test_busy_manual_request_is_acknowledged(self):
        acknowledged = threading.Event()
        with diagnostics._running:
            diagnostics.save_async('/test', lambda _: acknowledged.set())
            self.assertTrue(acknowledged.wait(1))
