"""Safety checks for removal of build-host state from an offline root."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('sanitize_live', Path(__file__).resolve().parents[1] / 'tools/sanitize-live-root.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LiveRootSanitizing(unittest.TestCase):
    def test_unprepared_directory_is_refused_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keep = root / 'keep'
            keep.write_text('unrelated')
            with self.assertRaises(ValueError):
                module.sanitize(root)
            self.assertEqual(keep.read_text(), 'unrelated')

    @unittest.skipIf(__import__('os').name == 'nt', 'Linux live-root symlinks')
    def test_host_state_removed_without_following_final_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'root'
            for folder in ('opt/flashos', 'etc', 'var/lib/dbus', 'var/log', 'var/lib/systemd', 'var/lib/NetworkManager'):
                (root / folder).mkdir(parents=True, exist_ok=True)
            (root / 'opt/flashos/menu.py').write_text('# prepared')
            outside = Path(directory) / 'outside'
            outside.write_text('must remain')
            (root / 'etc/machine-id').symlink_to(outside)
            (root / 'etc/resolv.conf').write_text('nameserver 192.0.2.1')
            (root / 'var/log/private.log').write_text('private')
            (root / 'var/lib/systemd/random-seed').write_bytes(b'seed')
            (root / 'var/lib/NetworkManager/internal-private.lease').write_text('private DHCP data')
            module.sanitize(root)
            self.assertEqual(outside.read_text(), 'must remain')
            self.assertEqual((root / 'etc/machine-id').read_text(), '')
            self.assertEqual((root / 'etc/resolv.conf').readlink(), Path('/run/NetworkManager/resolv.conf'))
            self.assertFalse((root / 'var/log/private.log').exists())
            self.assertFalse((root / 'var/lib/systemd/random-seed').exists())
            self.assertEqual(list((root / 'var/lib/NetworkManager').iterdir()), [])

    def test_bind_mount_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'opt/flashos').mkdir(parents=True)
            (root / 'opt/flashos/menu.py').write_text('# prepared')
            (root / 'etc').mkdir()
            identity = root / 'etc/machine-id'
            identity.write_text('must remain')
            for mount in ('etc', 'var', 'var/log/private'):
                with self.subTest(mount=mount), mock.patch.object(module, 'mount_points', return_value=[root / mount]):
                    with self.assertRaises(ValueError):
                        module.sanitize(root)
                    self.assertEqual(identity.read_text(), 'must remain')

    @unittest.skipIf(__import__('os').name == 'nt', 'Linux live-root symlinks')
    def test_escaping_parent_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'root'
            (root / 'opt/flashos').mkdir(parents=True)
            (root / 'opt/flashos/menu.py').write_text('# prepared')
            (root / 'etc').symlink_to(directory, target_is_directory=True)
            with self.assertRaises(ValueError):
                module.sanitize(root)
