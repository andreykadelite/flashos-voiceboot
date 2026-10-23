"""Exercise the payload writer without running the release packaging script."""
import ast
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from tests.test_installer_engine import POWERSHELLS, powershell_literal


ROOT = Path(__file__).resolve().parents[1]


class PayloadBuildTests(unittest.TestCase):
    def writer(self, archive):
        source = ROOT / 'tools/build-installer-payload.py'
        tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
        definitions = [node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name == 'include']
        self.assertEqual(1, len(definitions))
        namespace = {'hashlib': hashlib, 'archive': archive, 'records': [], 'objects': {}}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), namespace)
        return namespace

    def test_file_payload_is_streamed_and_deduplicated_with_byte_payloads(self):
        data = bytes(range(256)) * 32769
        digest = hashlib.sha256(data).hexdigest()
        result = io.BytesIO()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'filesystem.squashfs'
            source.write_bytes(data)
            with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_STORED) as archive:
                writer = self.writer(archive)
                with patch.object(Path, 'read_bytes', side_effect=AssertionError('File must be streamed')):
                    writer['include']('usb', 'flashos/filesystem.squashfs', source)
                writer['include']('boot', 'duplicate', data)
                writer['include']('info', 'notice', b'notice')
        with zipfile.ZipFile(result) as archive:
            self.assertEqual(data, archive.read('objects/' + digest))
            self.assertEqual(2, len(archive.namelist()))
        self.assertEqual(3, len(writer['records']))
        self.assertEqual(writer['records'][0]['sha256'], writer['records'][1]['sha256'])
        self.assertEqual(len(data), writer['records'][0]['bytes'])
        self.assertEqual(len(data), writer['objects'][digest])

    def test_empty_file_and_empty_bytes_use_one_valid_object(self):
        result = io.BytesIO()
        digest = hashlib.sha256(b'').hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'empty'
            source.touch()
            with zipfile.ZipFile(result, 'w') as archive:
                writer = self.writer(archive)
                writer['include']('usb', 'empty', source)
                writer['include']('boot', 'empty', b'')
        with zipfile.ZipFile(result) as archive:
            self.assertEqual(['objects/' + digest], archive.namelist())
            self.assertEqual(b'', archive.read('objects/' + digest))
        self.assertEqual({digest: 0}, writer['objects'])
        self.assertEqual([0, 0], [record['bytes'] for record in writer['records']])


class SystemExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / 'tools'))
        specification = importlib.util.spec_from_file_location('system_export_atomic', ROOT / 'tools/export-system.py')
        cls.export = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(cls.export)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('usb/file', 'ventoy.disk.img.xz', 'ventoy-efi.img', 'packages.tsv',
                     'toolkit-build-verification.json'):
            path = self.root / 'dist' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('payload ' + name).encode())
        (self.root / 'release').mkdir()
        self.output = self.root / 'release/FlashOS-test-system.zip'
        self.output.write_bytes(b'previous valid archive')
        for target in (patch.object(self.export, 'ROOT', self.root),
                       patch.object(self.export, 'SYSTEM_VERSION', 'test'),
                       patch.object(self.export, 'verified_system'),
                       patch.object(self.export, 'payload_files',
                                    side_effect=lambda root, section: [('file', root / 'dist/usb/file')]
                                    if section == 'usb' else [])):
            target.start()
            self.addCleanup(target.stop)

    def assert_preserved(self):
        self.assertEqual(b'previous valid archive', self.output.read_bytes())
        self.assertEqual([self.output], list(self.output.parent.iterdir()))

    def test_failed_export_keeps_previous_archive_and_removes_temporary(self):
        # Archive writing starts successfully, then a later missing input fails.
        (self.root / 'dist/ventoy-efi.img').unlink()
        with self.assertRaises(FileNotFoundError):
            self.export.main()
        self.assert_preserved()

    def test_failed_replacement_keeps_previous_archive_and_removes_temporary(self):
        with patch.object(self.export.os, 'replace', side_effect=OSError('destination locked')):
            with self.assertRaisesRegex(OSError, 'destination locked'):
                self.export.main()
        self.assert_preserved()

    def test_failed_flush_keeps_previous_archive_and_removes_temporary(self):
        with patch.object(self.export.os, 'fsync', side_effect=OSError('flush failed')):
            with self.assertRaisesRegex(OSError, 'flush failed'):
                self.export.main()
        self.assert_preserved()

    def test_successful_export_replaces_archive_with_verified_file_sums(self):
        with patch('builtins.print'):
            self.export.main()
        with zipfile.ZipFile(self.output) as archive:
            self.assertIsNone(archive.testzip())
            data = archive.read('dist/usb/file')
            sums = archive.read('SYSTEM-SHA256SUMS.txt').decode()
            self.assertIn(hashlib.sha256(data).hexdigest() + '  dist/usb/file', sums)
        self.assertEqual([self.output], list(self.output.parent.iterdir()))


@unittest.skipUnless(POWERSHELLS, 'PowerShell runtime is unavailable')
class AtomicInstallerPublicationTests(unittest.TestCase):
    def publish(self, runtime, *, failure='', existing=True):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / 'compiled.exe'
            target = folder / 'published.exe'
            content, previous = b'MZ compiled fixture\x00' * 100, b'MZ previous working installer'
            source.write_bytes(content)
            if existing:
                target.write_bytes(previous)
            scenario = r'''
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(SCRIPT,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count) { throw 'Installer build syntax error' }
$definitions=@($ast.FindAll({param($node)
  $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
  $node.Name -eq 'Publish-InstallerAtomically'
},$true))
if($definitions.Count -ne 1) { throw 'Expected exactly one atomic publisher' }
$definition=$definitions[0]
$functionText=$definition.Extent.Text
$failure=FAILURE
if($failure) {
  $method=switch($failure) { 'copy' {'CopyTo'} 'flush' {'Flush'} 'replace' {'Replace'} 'move' {'Move'} 'corrupt' {'CopyTo'} }
  $calls=@($definition.FindAll({param($node)
    $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
    $node.Member.Value -eq $method -and $node.Static -eq ($method -in @('Replace','Move'))
  },$true))
  if($calls.Count -ne 1) { throw 'Expected exactly one method at fault boundary' }
  $replacement="throw [IO.IOException]::new('injected "+$failure+" failure')"
  if($failure -eq 'copy') { $replacement='$outputStream.WriteByte(7); '+$replacement }
  if($failure -eq 'corrupt') { $replacement=$calls[0].Extent.Text+'; $outputStream.WriteByte(7)' }
  $offset=$calls[0].Extent.StartOffset-$definition.Extent.StartOffset
  $length=$calls[0].Extent.EndOffset-$calls[0].Extent.StartOffset
  $functionText=$functionText.Remove($offset,$length).Insert($offset,$replacement)
}
# Only this one function is evaluated. No compilation, elevation or disk worker.
. ([scriptblock]::Create($functionText))
$caught='';$result=$null
try { $result=Publish-InstallerAtomically SOURCE TARGET }
catch { $caught=$_.Exception.Message }
@{error=$caught;result=$result} | ConvertTo-Json -Compress -Depth 5
'''
            for marker, value in (('SCRIPT', ROOT / 'tools/Build-Installer.ps1'),
                                  ('FAILURE', failure), ('SOURCE', source), ('TARGET', target)):
                scenario = scenario.replace(marker, powershell_literal(value))
            encoded = base64.b64encode(scenario.encode('utf-16le')).decode('ascii')
            process = subprocess.run([runtime, '-NoLogo', '-NoProfile', '-NonInteractive',
                                      '-EncodedCommand', encoded], capture_output=True,
                                     encoding='utf-8', timeout=30)
            self.assertEqual(0, process.returncode, process.stderr + process.stdout)
            result = json.loads(process.stdout)
            self.assertEqual(content, source.read_bytes())
            self.assertFalse(list(folder.glob('*.tmp')))
            if failure:
                expected = 'SHA256 verification' if failure == 'corrupt' else 'injected ' + failure + ' failure'
                self.assertIn(expected, result['error'])
                self.assertIsNone(result['result'])
                if existing:
                    self.assertEqual(previous, target.read_bytes())
                else:
                    self.assertFalse(target.exists())
            else:
                self.assertEqual('', result['error'])
                self.assertEqual(content, target.read_bytes())
                self.assertEqual('SHA256', result['result']['Algorithm'])
                self.assertEqual(hashlib.sha256(content).hexdigest(), result['result']['Hash'].lower())

    def test_successfully_publishes_new_or_replaces_existing_installer(self):
        for runtime in POWERSHELLS:
            for existing in (False, True):
                with self.subTest(runtime=runtime, existing=existing):
                    self.publish(runtime, existing=existing)

    def test_copy_flush_and_replace_failures_preserve_working_installer(self):
        for runtime in POWERSHELLS:
            for failure in ('copy', 'flush', 'replace'):
                with self.subTest(runtime=runtime, failure=failure):
                    self.publish(runtime, failure=failure)

    def test_hash_mismatch_never_replaces_working_installer(self):
        for runtime in POWERSHELLS:
            with self.subTest(runtime=runtime):
                self.publish(runtime, failure='corrupt')

    def test_failed_first_publication_leaves_no_partial_installer(self):
        for runtime in POWERSHELLS:
            with self.subTest(runtime=runtime):
                self.publish(runtime, failure='move', existing=False)
