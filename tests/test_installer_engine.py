"""Run isolated installer byte-count and local file I/O checks in PowerShell.

The production engine main block is never executed: its six local I/O helpers
are selected from the parser AST. Every written file is in TemporaryDirectory.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'installer' / 'engine.ps1'


def powershell_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def available_powershells():
    candidates = [shutil.which('powershell.exe'), shutil.which('pwsh.exe'), shutil.which('pwsh')]
    if os.name == 'nt':
        candidates.append(str(Path(os.environ.get('SystemRoot', r'C:\Windows')) /
                              'System32/WindowsPowerShell/v1.0/powershell.exe'))
        candidates.append(str(Path.home() /
                              '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'))
    return list(dict.fromkeys(str(Path(path).resolve()) for path in candidates
                             if path and Path(path).is_file()))


POWERSHELLS = available_powershells()
LOAD_HELPERS = r'''
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Set-StrictMode -Version Latest
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$allowed=@('Event','Phase','Check-Cancel','Progress-Bytes','Copy-File','Hash-File')
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(ENGINE_PATH,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count) { throw 'Installer engine syntax error.' }
$definitions=@($ast.FindAll({param($node)
  $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in $allowed
},$true))
foreach($name in $allowed) {
  $matches=@($definitions | Where-Object Name -eq $name)
  if($matches.Count -ne 1) { throw ('Expected exactly one helper: '+$name) }
  . ([scriptblock]::Create($matches[0].Extent.Text))
}
# Guard accidental storage access if a helper changes in a future release.
foreach($command in @('Get-Disk','Clear-Disk','Initialize-Disk','New-Partition',
                     'Format-Volume','Get-CimInstance','Start-Process')) {
  Set-Item -LiteralPath ('Function:'+ $command) -Value { throw 'Storage access forbidden in local I/O tests.' }
}
$script:changed=$false;$script:phase='local-test'
$script:doneBytes=[long]0;$script:totalBytes=[long]0
$script:lastProgress=[datetime]::MinValue
'''


@unittest.skipUnless(POWERSHELLS, 'PowerShell runtime is unavailable')
class InstallerEngineTests(unittest.TestCase):
    def run_helpers(self, runtime, scenario, directory=None):
        # The backend writes UTF-8 BOM before invoking PowerShell -File.
        # Match that production encoding before selecting AST definitions.
        with tempfile.TemporaryDirectory() as engine_directory:
            engine = Path(engine_directory) / 'engine.ps1'
            engine.write_text(ENGINE.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
            header = LOAD_HELPERS.replace('ENGINE_PATH', powershell_literal(engine))
            if directory is not None:
                header += '\n$cancel=' + powershell_literal(Path(directory) / 'cancel') + '\n'
            script = header + '\n' + scenario
            encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
            process = subprocess.run([runtime, '-NoLogo', '-NoProfile', '-NonInteractive',
                                      '-EncodedCommand', encoded], capture_output=True,
                                     encoding='utf-8', timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        records = [json.loads(line) for line in process.stdout.splitlines() if line.startswith('{')]
        self.assertTrue(records, 'PowerShell returned no JSON evidence')
        result = records[-1]
        self.assertTrue(result.get('qa_result'), process.stdout)
        self.assertIn(result['major'], (5, 7))
        if Path(runtime).name.lower() == 'powershell.exe':
            self.assertEqual(result['major'], 5, 'Windows PowerShell 5.1 must be tested directly')
        return result, records[:-1]

    def test_large_totals_and_completed_byte_counts(self):
        scenario = r'''
$rows=@()
foreach($total in @([long]2147483647,[long]2147483648,[long]2147483649,
                    [long]2177247376,[long]4294967296,[long]4294967297,
                    [long]8589934592,[long]8589934593)) {
  foreach($source in @('int64','manifest-sum')) {
    $script:totalBytes=if($source -eq 'int64') {$total} else {
      (@([pscustomobject]@{bytes=$total-1},[pscustomobject]@{bytes=1}) | Measure-Object bytes -Sum).Sum
    }
    $script:doneBytes=[long]0;$script:lastProgress=[datetime]::MinValue
    Progress-Bytes 65536 'first chunk'
    $script:doneBytes=[long]($total-65536)
    $script:lastProgress=[datetime]::MinValue
    Progress-Bytes 65536 'last chunk'
    $rows+=@{total=$total;source=$source;done=$script:doneBytes;done_type=$script:doneBytes.GetType().FullName}
  }
}
@{qa_result=$true;major=$PSVersionTable.PSVersion.Major;rows=$rows} | ConvertTo-Json -Depth 5 -Compress
'''
        for runtime in POWERSHELLS:
            with self.subTest(runtime=runtime):
                result, events = self.run_helpers(runtime, scenario)
                self.assertEqual(len(result['rows']), 16)
                self.assertEqual(len(events), 32)
                for row, first, last in zip(result['rows'], events[::2], events[1::2]):
                    self.assertEqual(row['done'], row['total'])
                    self.assertEqual(row['done_type'], 'System.Int64')
                    self.assertEqual(first['bytes'], 65536)
                    self.assertEqual(first['total_bytes'], row['total'])
                    self.assertEqual(last['bytes'], row['total'])
                    self.assertEqual(last['percent'], 100)

    def test_fractional_progress_zero_total_and_clamping(self):
        scenario = r'''
foreach($case in @(@(800,300),@(1000,123),@(200,101),@(0,0),@(0,1),@(100,150))) {
  $script:totalBytes=[long]$case[0];$script:doneBytes=[long]0
  $script:lastProgress=[datetime]::MinValue
  Progress-Bytes $case[1] 'fractional progress'
}
@{qa_result=$true;major=$PSVersionTable.PSVersion.Major} | ConvertTo-Json -Compress
'''
        for runtime in POWERSHELLS:
            with self.subTest(runtime=runtime):
                _, events = self.run_helpers(runtime, scenario)
                self.assertEqual([event['percent'] for event in events], [37.5, 12.3, 50.5, 0, 100, 100])
                for event in events[:3]:
                    self.assertIsInstance(event['percent'], float)

    def test_phase_resets_large_counters_without_losing_int64_type(self):
        scenario = r'''
$script:doneBytes=[long]8589934593;$script:totalBytes=[long]8589934593
Phase 'verify' 'reset verification'
@{qa_result=$true;major=$PSVersionTable.PSVersion.Major;done_type=$script:doneBytes.GetType().FullName;
  total_type=$script:totalBytes.GetType().FullName} | ConvertTo-Json -Compress
'''
        for runtime in POWERSHELLS:
            with self.subTest(runtime=runtime):
                result, events = self.run_helpers(runtime, scenario)
                self.assertEqual(result['done_type'], 'System.Int64')
                self.assertEqual(result['total_type'], 'System.Int64')
                self.assertEqual(events, [{'phase': 'verify', 'percent': None, 'kind': 'phase',
                                          'message': 'reset verification', 'changed': False,
                                          'bytes': 0, 'total_bytes': 0}])

    def test_cancellation_during_final_identity_check_never_starts_erasure(self):
        # Run only each branch's prefix through its first mutation, with every
        # device command replaced. A cancellation arriving in Assert-Target must
        # be observed before either Clear-Disk or Ventoy can start.
        for runtime in POWERSHELLS:
            for action, mutation in (('format', 'Clear-Disk'), ('install', 'Start-Process')):
                with self.subTest(runtime=runtime, action=action), tempfile.TemporaryDirectory() as directory:
                    scenario = r'''
$request=[pscustomobject]@{action=ACTION;style='GPT';engine='unused'}
$disk=[pscustomobject]@{Number=9}
$script:mutationAttempted=$false
function Assert-Target {
  [IO.File]::WriteAllText($cancel,'cancel')
  return $disk
}
function Clear-Disk { $script:mutationAttempted=$true; throw 'Unexpected erasure' }
function Start-Process { $script:mutationAttempted=$true; throw 'Unexpected writer' }
$condition='$request.action -eq '+"'"+$request.action+"'"
$branches=@($ast.FindAll({param($node)
  $node -is [Management.Automation.Language.IfStatementAst] -and
  $node.Clauses[0].Item1.Extent.Text -eq $condition
},$true))
if($branches.Count -ne 1) { throw 'Expected one production action branch' }
$prefix=@()
foreach($statement in $branches[0].Clauses[0].Item2.Statements) {
  $prefix+=$statement.Extent.Text
  if($statement.Extent.Text -match MUTATION) { break }
}
if($prefix[-1] -notmatch MUTATION) { throw 'Expected first mutation in action branch' }
$caught=$false
try { . ([scriptblock]::Create(($prefix -join "`n"))) }
catch [OperationCanceledException] { $caught=$true }
@{qa_result=$true;major=$PSVersionTable.PSVersion.Major;cancelled=$caught;
  mutation_attempted=$script:mutationAttempted;changed=$script:changed} | ConvertTo-Json -Compress
'''.replace('ACTION', powershell_literal(action)).replace('MUTATION', powershell_literal(mutation))
                    result, _ = self.run_helpers(runtime, scenario, directory)
                    self.assertTrue(result['cancelled'])
                    self.assertFalse(result['mutation_attempted'])
                    self.assertFalse(result['changed'])

    def local_file(self, directory):
        data = bytes(range(256)) * 16384 + b'final short block'
        source = Path(directory) / 'source.bin'
        source.write_bytes(data)
        return source, data

    def test_local_copy_and_hash_cross_int32_boundary(self):
        for runtime in POWERSHELLS:
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as directory:
                source, data = self.local_file(directory)
                destination = Path(directory) / 'nested' / 'copied.bin'
                scenario = f'''
$script:totalBytes=[long]2177247376;$script:doneBytes=[long]2147483600
Copy-File {powershell_literal(source)} {powershell_literal(destination)} 'copy fixture'
$afterCopy=$script:doneBytes;$copyType=$script:doneBytes.GetType().FullName
$script:doneBytes=[long]2147483600;$script:lastProgress=[datetime]::MinValue
$digest=Hash-File {powershell_literal(destination)} 'hash fixture'
@{{qa_result=$true;major=$PSVersionTable.PSVersion.Major;digest=$digest;copy_bytes=$afterCopy;
  hash_bytes=$script:doneBytes;copy_type=$copyType;hash_type=$script:doneBytes.GetType().FullName}} | ConvertTo-Json -Compress
'''
                result, events = self.run_helpers(runtime, scenario, directory)
                self.assertEqual(destination.read_bytes(), data)
                self.assertEqual(source.read_bytes(), data)
                self.assertEqual(result['digest'], hashlib.sha256(data).hexdigest())
                self.assertEqual(result['copy_bytes'], 2147483600 + len(data))
                self.assertEqual(result['hash_bytes'], result['copy_bytes'])
                self.assertEqual(result['copy_type'], 'System.Int64')
                self.assertEqual(result['hash_type'], 'System.Int64')
                self.assertTrue(events)
                self.assertTrue(all(0 <= event['percent'] <= 100 for event in events))

    def cancellation(self, operation):
        for runtime in POWERSHELLS:
            for stop_after in (0, 1):
                with self.subTest(runtime=runtime, written_chunks=stop_after), tempfile.TemporaryDirectory() as directory:
                    source, data = self.local_file(directory)
                    destination = Path(directory) / 'cancelled.bin'
                    command = (f'Copy-File {powershell_literal(source)} {powershell_literal(destination)}' if operation == 'copy'
                               else f'Hash-File {powershell_literal(source)}')
                    scenario = f'''
$script:totalBytes=[long]2177247376
$script:cancelChecks=0;$script:originalCancel=(Get-Command Check-Cancel).ScriptBlock
function Check-Cancel {{
  $script:cancelChecks++
  if($script:cancelChecks -eq {stop_after + 1}) {{ [IO.File]::WriteAllText($cancel,'cancel') }}
  & $script:originalCancel
}}
$caught=$false;$digest=$null
try {{ $digest={command} 'cancel fixture' }}
catch [OperationCanceledException] {{ $caught=$true }}
$probe=[IO.File]::Open({powershell_literal(source)},[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
$probe.Dispose()
'''
                    if operation == 'copy':
                        scenario += f'''
$probe=[IO.File]::Open({powershell_literal(destination)},[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
$probe.Dispose()
'''
                    scenario += '''
@{qa_result=$true;major=$PSVersionTable.PSVersion.Major;cancelled=$caught;
  checks=$script:cancelChecks;bytes=$script:doneBytes;digest=$digest} | ConvertTo-Json -Compress
'''
                    result, _ = self.run_helpers(runtime, scenario, directory)
                    self.assertTrue(result['cancelled'])
                    self.assertEqual(result['checks'], stop_after + 1)
                    self.assertEqual(result['bytes'], stop_after * 4 * 1024 * 1024)
                    self.assertIsNone(result['digest'])
                    self.assertEqual(source.read_bytes(), data)
                    if operation == 'copy':
                        self.assertEqual(destination.read_bytes(), data[:stop_after * 4 * 1024 * 1024])

    def test_local_copy_cancellation_releases_streams(self):
        self.cancellation('copy')

    def test_local_hash_cancellation_releases_streams(self):
        self.cancellation('hash')
