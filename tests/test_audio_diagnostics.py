from pathlib import Path
import sys
import threading
import json
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import audio_diagnostics as diagnostics


class DiagnosticTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Linux directory fsync')
    def test_boot_snapshot_keeps_wifi_probe_errors_and_bound_pci_drivers(self):
        error = 'iwlwifi 0000:00:14.3: probe with driver iwlwifi failed with error -110\n'
        calls = []
        def command(argv, **kwargs):
            calls.append(argv)
            output = (error if argv == ['dmesg'] else
                      'Kernel driver in use: iwlwifi\n' if argv == ['lspci', '-nnk'] else '')
            return SimpleNamespace(stdout=output, stderr='', returncode=0)
        with tempfile.TemporaryDirectory() as directory, patch.object(diagnostics.subprocess, 'run', side_effect=command):
            destination = Path(directory) / 'audio-last.json'
            diagnostics.collect(destination)
            report = json.loads(destination.read_text())
        self.assertIn(error.strip(), report['kernel_log'])
        self.assertNotIn(error.strip(), report['audio_kernel'])
        self.assertIn('Kernel driver in use: iwlwifi', report['pci'])
        self.assertFalse(report['kernel_log_truncated'])
        self.assertIn(['lspci', '-nnk'], calls)

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
