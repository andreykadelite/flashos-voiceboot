param([ValidateSet('standalone','onefile')][string]$Mode='onefile')
$ErrorActionPreference='Stop'
$project=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python=Join-Path $project 'build/installer/python312/python.exe'
Push-Location $project
try {
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
    '--file-version=1.0.8.0','--product-version=1.0.8.0',
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
    Copy-Item -LiteralPath build/installer/nuitka/FlashOS-Setup.exe -Destination release/FlashOS-Setup.exe -Force
    $hash=Get-FileHash -LiteralPath release/FlashOS-Setup.exe -Algorithm SHA256
    @{Algorithm=$hash.Algorithm; Hash=$hash.Hash; File='FlashOS-Setup.exe'} | ConvertTo-Json | Set-Content -LiteralPath release/FlashOS-Setup.sha256.json -Encoding UTF8
    $otherSums=@()
    if(Test-Path -LiteralPath release/SHA256SUMS.txt) {
      $otherSums=@(Get-Content -LiteralPath release/SHA256SUMS.txt -Encoding UTF8 | Where-Object { $_ -notmatch ' [*]?FlashOS-Setup\.exe$' })
    }
    $checksumLines=[string[]](@($hash.Hash.ToLowerInvariant()+' *FlashOS-Setup.exe')+$otherSums)
    [IO.File]::WriteAllLines((Join-Path $project 'release/SHA256SUMS.txt'),$checksumLines,[Text.UTF8Encoding]::new($false))
    if(Test-Path -LiteralPath release/release-manifest.json) {
      $releaseInfo=Get-Content -LiteralPath release/release-manifest.json -Raw | ConvertFrom-Json
      $releaseInfo.installer='1.0.8'
      $releaseInfo.system='2026.10.13.2'
      foreach($entry in $releaseInfo.files) {
        if($entry.file -eq 'FlashOS-Setup.exe') {
          $entry.bytes=(Get-Item -LiteralPath release/FlashOS-Setup.exe).Length
          $entry.sha256=$hash.Hash.ToLowerInvariant()
        }
      }
      $releaseInfo | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath release/release-manifest.json -Encoding UTF8
    }
  }
} finally { Pop-Location }
