"""Exercise terminal/lock ordering with fake writers; never opens a disk."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from installer import backend
from installer.model import identity
from tests.test_installer import usb


class WorkerLifecycleTests(unittest.TestCase):
    def execute(self, action='verify', code=0, output=None, boot_error=None, cancelled=False, replacement=False):
        events, order = [], []
        with tempfile.TemporaryDirectory() as folder:
            secure = MagicMock(path=Path(folder))
            secure.close.side_effect = lambda: order.append('secure_close')
            lock = MagicMock()
            lock.__enter__.side_effect = lambda: order.append('lock')
            lock.__exit__.side_effect = lambda *a: order.append('unlock')
            bundle = MagicMock(manifest={'schema': 1, 'files': [], 'objects': {}})
            def extract(destination, emit, stop, required=None):
                destination.mkdir()
            bundle.extract.side_effect = extract
            process = MagicMock()
            process.stdout = io.BytesIO(output or (json.dumps(dict(kind='complete', message='done', percent=100))+'\n').encode())
            process.wait.return_value = code
            process.poll.return_value = code
            group = MagicMock()
            group.start.return_value = process
            lease = MagicMock()
            check_boot = MagicMock(side_effect=boot_error or (lambda *a: order.append('boot_verify')))
            def emit(event):
                events.append(event)
                order.append(event['kind'])
            with patch.object(backend, 'SecureRun', return_value=secure), patch.object(backend, 'OperationMutex', return_value=lock), \
                 patch.object(backend, 'is_admin', return_value=True), patch.object(backend, 'Bundle', return_value=bundle), \
                 patch.object(backend, 'inventory', return_value=[usb(serial='replacement') if replacement else usb()]), \
                 patch.object(backend, 'DiskLease', return_value=lease), patch.object(backend, 'ProcessGroup', return_value=group), \
                 patch.object(backend, 'powershell', return_value='fake.exe'), patch.object(backend, 'verify_boot', check_boot):
                operation = backend.Operation(dict(target=identity(usb()), action=action, style='MBR', filesystem='exFAT', erase_confirmed=True), emit)
                if cancelled:
                    operation.cancel()
                operation.run()
                self.assertFalse((Path(folder)/'work').exists())
            return events, order, bundle, group, check_boot

    def test_success_requires_boot_verification_and_lock_covers_cleanup(self):
        events, order, bundle, group, check_boot = self.execute()
        self.assertLess(order.index('boot_verify'), order.index('complete'))
        self.assertLess(order.index('secure_close'), order.index('unlock'))
        self.assertEqual(1, len([e for e in events if e['kind'] == 'complete']))
        self.assertNotIn('error', order)
        required = bundle.extract.call_args.kwargs['required']
        self.assertIn(('engine', 'ventoy/ventoy.disk.img.xz'), required)
        self.assertNotIn(('usb', 'flashos/filesystem.squashfs'), required)

    def test_boot_error_never_emits_complete(self):
        events, order, *_ = self.execute(boot_error=ValueError('bad boot'))
        self.assertNotIn('complete', order)
        self.assertIn('error', order)
        self.assertIn('bad boot', next(e for e in events if e['kind'] == 'error')['message'])

    def test_nonzero_exit_overrides_untrusted_success(self):
        events, order, *_ = self.execute(code=1)
        self.assertNotIn('complete', order)
        self.assertIn('error', order)

    def test_early_cancel_or_replacement_never_starts_disk_writer(self):
        for options in ({'cancelled': True}, {'replacement': True}):
            with self.subTest(options=options):
                events, order, bundle, group, check_boot = self.execute(**options)
                group.start.assert_not_called()
                check_boot.assert_not_called()
                self.assertNotIn('complete', order)
                self.assertEqual('unlock', order[-1])

    def test_format_does_not_extract_linux_image(self):
        events, order, bundle, group, check_boot = self.execute(action='format')
        bundle.extract.assert_not_called()
        check_boot.assert_not_called()
        self.assertIn('complete', order)
