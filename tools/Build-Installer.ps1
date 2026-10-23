param([ValidateSet('standalone','onefile')][string]$Mode='onefile')
$ErrorActionPreference='Stop'
function Publish-InstallerAtomically([string]$Source,[string]$Destination) {
  $sourcePath=[IO.Path]::GetFullPath($Source)
  $destinationPath=[IO.Path]::GetFullPath($Destination)
  if($sourcePath.Equals($destinationPath,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Installer source and release destination must be different files.'
  }
  $temporary=$destinationPath+'.pending-'+[Guid]::NewGuid().ToString('N')+'.tmp'
  $createdTemporary=$false
  $sha=[Security.Cryptography.SHA256]::Create()
  try {
    $inputStream=[IO.File]::Open($sourcePath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
      $expected=[BitConverter]::ToString($sha.ComputeHash($inputStream)).Replace('-','')
      $inputStream.Position=0
      $outputStream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
      $createdTemporary=$true
      try {
        $inputStream.CopyTo($outputStream)
        $outputStream.Flush($true)
      } finally { $outputStream.Dispose() }
    } finally { $inputStream.Dispose() }
    $checkStream=[IO.File]::Open($temporary,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try { $verified=[BitConverter]::ToString($sha.ComputeHash($checkStream)).Replace('-','') }
    finally { $checkStream.Dispose() }
    if($verified -cne $expected) { throw 'The copied installer failed SHA256 verification.' }
    if([IO.File]::Exists($destinationPath)) {
      [IO.File]::Replace($temporary,$destinationPath,[NullString]::Value)
    } else {
      [IO.File]::Move($temporary,$destinationPath)
    }
    return [pscustomobject]@{Algorithm='SHA256';Hash=$verified;Path=$destinationPath}
  } finally {
    $sha.Dispose()
    if($createdTemporary -and [IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
  }
}
$project=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python=Join-Path $project 'build/installer/python312/python.exe'
Push-Location $project
try {
  $toolchainCheck=@'
import importlib.metadata
from pathlib import Path
import sys
if sys.version_info[:3] != (3, 12, 10):
    raise SystemExit('Installer build requires CPython 3.12.10.')
for requirement in Path('installer/requirements-build.txt').read_text(encoding='utf-8').splitlines():
    if not requirement.strip() or requirement.startswith('#'):
        continue
    name, expected = requirement.split('==')
    actual = importlib.metadata.version(name)
    if actual != expected:
        raise SystemExit(f'{name}: expected {expected}, found {actual}.')
'@
  & $python -c $toolchainCheck
  if($LASTEXITCODE -ne 0) { throw 'Pinned installer toolchain verification failed.' }
  $versionJson=& $python -c 'import json; from installer.model import VERSION, SYSTEM_VERSION; print(json.dumps(dict(installer=VERSION, system=SYSTEM_VERSION)))'
  if($LASTEXITCODE -ne 0) { throw 'Cannot read release versions.' }
  $versions=$versionJson | ConvertFrom-Json
  if($versions.installer -notmatch '^\d+\.\d+\.\d+$' -or $versions.system -notmatch '^\d{4}\.\d{2}\.\d{2}\.\d+$') { throw 'Invalid release versions.' }
  $windowsVersion=$versions.installer+'.0'
  & $python tools/package-public-docs.py
  if($LASTEXITCODE -ne 0) { throw 'Public documentation preparation failed.' }
  & $python tools/build-installer-payload.py
  if($LASTEXITCODE -ne 0) { throw 'Payload preparation failed.' }
  & $python -m unittest discover -s tests -p 'test_installer*.py'
  if($LASTEXITCODE -ne 0) { throw 'Installer tests failed.' }
  & $python tools/check-installer-qt.py
  if($LASTEXITCODE -ne 0) { throw 'Qt keyboard and layout checks failed.' }
  $arguments=@('-m','nuitka',('--mode='+$Mode),'--mingw64','--assume-yes-for-downloads','--jobs=4',
    '--enable-plugin=pyside6','--include-qt-plugins=platforms,styles,imageformats',
    '--noinclude-qt-plugins=imageformats,iconengines,tls',
    '--include-data-files=build/installer/payload.zip=installer/payload.zip',
    '--windows-console-mode=disable','--windows-uac-admin','--windows-file-description=Установщик Flash OS',
    '--company-name=Зотов Андрей Анатольевич','--product-name=Установщик Flash OS',
    ('--file-version='+$windowsVersion),('--product-version='+$windowsVersion),
    '--output-filename=FlashOS-Setup.exe','--output-dir=build/installer/nuitka',
    '--report=build/installer/nuitka-report.xml','--python-flag=isolated','--python-flag=safe_path')
  if($Mode -eq 'onefile') { $arguments+=@('--onefile-no-compression','--onefile-no-dll','--include-onefile-external-data=installer/payload.zip') }
  $arguments+='installer/entry.py'
  & $python @arguments
  if($LASTEXITCODE -ne 0) { throw 'Nuitka compilation failed.' }
  $manifestTargets=@('build/installer/nuitka/entry.dist/FlashOS-Setup.exe')
  if($Mode -eq 'onefile') { $manifestTargets+='build/installer/nuitka/FlashOS-Setup.exe' }
  & $python tools/check-installer-manifest.py @manifestTargets
  if($LASTEXITCODE -ne 0) { throw 'Installer startup manifest check failed.' }
  & $python tools/check-installer-image.py @manifestTargets
  if($LASTEXITCODE -ne 0) { throw 'Windows rejected the compiled installer image.' }
  if($Mode -eq 'onefile') {
    $overlayEvidence=& $python tools/append-installer-payload.py --exe build/installer/nuitka/FlashOS-Setup.exe --payload build/installer/payload.zip
    if($LASTEXITCODE -ne 0) { throw 'Verified system archive attachment failed.' }
    $overlayEvidence | Set-Content -LiteralPath build/installer/overlay-verification.json -Encoding ASCII
    $imageEvidence=& $python tools/check-installer-image.py build/installer/nuitka/FlashOS-Setup.exe
    if($LASTEXITCODE -ne 0) { throw 'Windows rejected the final installer image.' }
    $imageEvidence | Set-Content -LiteralPath build/installer/image-verification.json -Encoding ASCII
    $qaDirectory=Join-Path 'build' ('installer-compat-'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff'))
    $null=New-Item -ItemType Directory -Path $qaDirectory
    $startupReport=Join-Path $qaDirectory 'compiled-startup-verification.json'
    & $python tools/check-installer-startup.py --exe build/installer/nuitka/FlashOS-Setup.exe --report $startupReport
    if($LASTEXITCODE -ne 0) { throw 'Compiled installer startup verification failed.' }
    Copy-Item -LiteralPath $startupReport -Destination build/installer/startup-verification.json -Force
  }
  if($Mode -eq 'onefile') {
    $null=New-Item -ItemType Directory -Path release -Force
    $hash=Publish-InstallerAtomically 'build/installer/nuitka/FlashOS-Setup.exe' 'release/FlashOS-Setup.exe'
    @{Algorithm=$hash.Algorithm; Hash=$hash.Hash; File='FlashOS-Setup.exe'} | ConvertTo-Json | Set-Content -LiteralPath release/FlashOS-Setup.sha256.json -Encoding UTF8
    # A new installer must never inherit another release's archive or QA claims.
    $buildInfo=[ordered]@{
      format=2
      installer=$versions.installer
      system=$versions.system
      completed_utc=[DateTime]::UtcNow.ToString('o')
      scope='local-installer-build'
      published=$false
      verification_scope='compiled-startup-with-mocked-devices'
      files=@(@{file='FlashOS-Setup.exe';bytes=(Get-Item -LiteralPath release/FlashOS-Setup.exe).Length;sha256=$hash.Hash.ToLowerInvariant()})
    }
    $buildInfo | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath release/FlashOS-Setup.build.json -Encoding UTF8
    $buildInfo | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath release/release-manifest.json -Encoding UTF8
    $checksumLines=@($hash.Hash.ToLowerInvariant()+' *FlashOS-Setup.exe')
    foreach($name in @('FlashOS-Setup.sha256.json','FlashOS-Setup.build.json','release-manifest.json')) {
      $fileHash=Get-FileHash -LiteralPath (Join-Path release $name) -Algorithm SHA256
      $checksumLines+=($fileHash.Hash.ToLowerInvariant()+' *'+$name)
    }
    [IO.File]::WriteAllLines((Join-Path $project 'release/SHA256SUMS.txt'),[string[]]$checksumLines,[Text.UTF8Encoding]::new($false))
  }
} finally { Pop-Location }
