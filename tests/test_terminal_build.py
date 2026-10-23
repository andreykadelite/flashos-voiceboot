"""Offline terminal certification must reject incomplete and altered images."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    'terminal_build', Path(__file__).resolve().parents[1] / 'tools/verify-terminal-build.py')
terminal_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terminal_build)

LIB = 'usr/lib/x86_64-linux-gnu/'
FILES = {
    'bash': ['usr/bin/bash'],
    'python3-gi': ['usr/lib/python3/dist-packages/gi/__init__.py',
                   'usr/lib/python3/dist-packages/gi/_gi.cpython-314-x86_64-linux-gnu.so'],
    'gir1.2-gtk-3.0': [LIB + 'girepository-1.0/Gtk-3.0.typelib'],
    'gir1.2-vte-2.91': [LIB + 'girepository-1.0/Vte-2.91.typelib'],
    'libatk-adaptor': [LIB + 'gtk-2.0/modules/libatk-bridge.so'],
    'libvte-2.91-0': [LIB + 'libvte-2.91.so.0.9000.2'],
    'libgtk-3-0t64': [LIB + 'libgtk-3.so.0.2600.1'],
}
MULTIARCH = {'gir1.2-gtk-3.0', 'gir1.2-vte-2.91', 'libatk-adaptor', 'libvte-2.91-0', 'libgtk-3-0t64'}


class TerminalBuildTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.image = self.root / 'filesystem.squashfs'
        self.image.write_bytes(b'fixture image bytes')
        self.contents = self.root / 'image-files'
        self.status = '\n\n'.join('Package: ' + package + '\nStatus: install ok installed\n'
            'Version: 9.10-2\nArchitecture: amd64\n' +
            ('Multi-Arch: same\n' if package in MULTIARCH else '') for package in FILES)
        self.write('var/lib/dpkg/status', self.status.encode())
        for package, paths in FILES.items():
            manifest = []
            for path in paths:
                payload = ('packaged file: ' + path).encode()
                self.write(path, payload)
                manifest.append(hashlib.md5(payload, usedforsecurity=False).hexdigest() + '  ' + path)
            self.write(self.manifest_path(package), ('\n'.join(manifest) + '\n').encode())
        self.links = {LIB + 'libvte-2.91.so.0': 'libvte-2.91.so.0.9000.2',
                      LIB + 'libgtk-3.so.0': 'libgtk-3.so.0.2600.1'}
        self.commands = []
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(terminal_build.subprocess, 'run', side_effect=self.unsquashfs).start()

    def write(self, name, payload):
        path = self.contents / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def manifest_path(self, package):
        return 'var/lib/dpkg/info/' + package + (':amd64' if package in MULTIARCH else '') + '.md5sums'

    def unsquashfs(self, arguments, **kwargs):
        self.assertEqual(arguments[0], 'unsquashfs')
        self.assertEqual(arguments[2], str(self.image))
        self.assertEqual(len(arguments), 4)
        self.commands.append(arguments)
        option, name = arguments[1], arguments[3]
        if option == '-cat' and (self.contents / name).is_file():
            content = (self.contents / name).read_bytes()
        elif option == '-ll' and name in self.links:
            content = ('lrwxrwxrwx root/root 20 2026-10-22 12:00 squashfs-root/' +
                       name + ' -> ' + self.links[name] + '\n').encode()
        else:
            raise subprocess.CalledProcessError(1, arguments, stderr=b'File not found')
        return subprocess.CompletedProcess(arguments, 0, stdout=content, stderr=b'')

    def test_complete_runtime_records_real_versions_image_hash_and_packaged_file_hashes(self):
        report = terminal_build.verify(self.image)
        self.assertTrue(report['verified'])
        self.assertEqual(report['filesystem_sha256'], hashlib.sha256(self.image.read_bytes()).hexdigest())
        self.assertEqual(report['required_package_versions'], {name: '9.10-2' for name in FILES})
        self.assertEqual(set(report['packaged_files_sha256']), {path for paths in FILES.values() for path in paths})
        for path, digest in report['packaged_files_sha256'].items():
            self.assertEqual(digest, hashlib.sha256((self.contents / path).read_bytes()).hexdigest())
        self.assertEqual(report['library_links'], self.links)
        self.assertTrue(all(command[1] in ('-cat', '-ll') for command in self.commands))

    def test_missing_required_package_is_not_certified(self):
        self.write('var/lib/dpkg/status', self.status.split('\n\n', 1)[1].encode())
        with self.assertRaisesRegex(RuntimeError, 'Missing installed.*bash'):
            terminal_build.verify(self.image)

    def test_unconfigured_removed_and_half_installed_packages_are_not_certified(self):
        for state in ('install ok unpacked', 'install ok half-configured', 'install reinstreq installed',
                      'deinstall ok config-files'):
            with self.subTest(state=state):
                self.write('var/lib/dpkg/status', self.status.replace('install ok installed', state, 1).encode())
                with self.assertRaisesRegex(RuntimeError, 'not configured and installed: bash'):
                    terminal_build.verify(self.image)

    def test_held_configured_package_is_still_installed(self):
        self.write('var/lib/dpkg/status', self.status.replace('install ok installed', 'hold ok installed', 1).encode())
        self.assertTrue(terminal_build.verify(self.image)['verified'])

    def test_missing_version_or_duplicate_package_prevents_certification(self):
        for status in (self.status.replace('Version: 9.10-2', 'Version: ', 1),
                       self.status + '\n\n' + self.status.split('\n\n')[0]):
            with self.subTest(status=status):
                self.write('var/lib/dpkg/status', status.encode())
                with self.assertRaises(RuntimeError):
                    terminal_build.verify(self.image)

    def test_each_required_runtime_file_must_exist(self):
        for path in (path for paths in FILES.values() for path in paths):
            with self.subTest(path=path):
                content = (self.contents / path).read_bytes()
                (self.contents / path).unlink()
                with self.assertRaisesRegex(RuntimeError, 'Cannot read terminal component'):
                    terminal_build.verify(self.image)
                self.write(path, content)

    def test_altered_typelibs_python_loader_and_libraries_are_rejected(self):
        for path in (path for paths in FILES.values() for path in paths):
            with self.subTest(path=path):
                original = (self.contents / path).read_bytes()
                self.write(path, original + b'altered')
                with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                    terminal_build.verify(self.image)
                self.write(path, original)

    def test_missing_or_incomplete_package_manifest_is_rejected(self):
        manifest = self.contents / self.manifest_path('gir1.2-vte-2.91')
        manifest.unlink()
        with self.assertRaisesRegex(RuntimeError, 'Cannot read terminal component'):
            terminal_build.verify(self.image)
        self.write(self.manifest_path('gir1.2-vte-2.91'), b'')
        with self.assertRaisesRegex(RuntimeError, 'absent from package manifest'):
            terminal_build.verify(self.image)

    def test_library_soname_cannot_point_to_a_missing_or_different_file(self):
        for target in ('missing.so', '/outside/libvte.so', '../libvte-2.91.so.0.9000.2'):
            with self.subTest(target=target):
                self.links[LIB + 'libvte-2.91.so.0'] = target
                with self.assertRaisesRegex(RuntimeError, 'Invalid terminal library link'):
                    terminal_build.verify(self.image)

    def test_ambiguous_versioned_library_and_unsafe_manifest_are_rejected(self):
        manifest_name = self.manifest_path('libvte-2.91-0')
        original = (self.contents / manifest_name).read_bytes()
        for extra in (b'0' * 32 + b'  ' + LIB.encode() + b'libvte-2.91.so.0.8000.1\n',
                      b'0' * 32 + b'  ../outside\n'):
            with self.subTest(extra=extra):
                self.write(manifest_name, original + extra)
                with self.assertRaises(RuntimeError):
                    terminal_build.verify(self.image)

    def test_failed_cli_replaces_existing_passing_report(self):
        report = self.root / 'verification.json'
        report.write_text(json.dumps({'verified': True}))
        self.write(FILES['gir1.2-vte-2.91'][0], b'corrupt')
        with self.assertRaises(SystemExit) as error:
            terminal_build.main(['--image', str(self.image), '--output', str(report)])
        self.assertEqual(error.exception.code, 1)
        payload = json.loads(report.read_text())
        self.assertFalse(payload['verified'])
        self.assertIn('checksum mismatch', payload['error'])

    def test_cli_success_writes_requested_report(self):
        report = self.root / 'out/verification.json'
        self.assertEqual(terminal_build.main(['--image', str(self.image), '--output', str(report)]), 0)
        self.assertTrue(json.loads(report.read_text())['verified'])


if __name__ == '__main__':
    unittest.main()
