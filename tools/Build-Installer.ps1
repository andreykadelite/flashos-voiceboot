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
  & $python -m unittest tests.test_installer tests.test_installer_windows tests.test_installer_boot
  if($LASTEXITCODE -ne 0) { throw 'Installer tests failed.' }
  $arguments=@('-m','nuitka',('--mode='+$Mode),'--mingw64','--assume-yes-for-downloads','--jobs=4',
    '--enable-plugin=pyside6','--include-qt-plugins=platforms,styles,imageformats',
    '--noinclude-qt-plugins=imageformats,iconengines,tls',
    '--include-data-files=build/installer/payload.zip=installer/payload.zip',
    '--windows-console-mode=disable','--windows-file-description=Установщик Flash OS',
    '--company-name=Зотов Андрей Анатольевич','--product-name=Установщик Flash OS',
    '--file-version=1.0.1.0','--product-version=1.0.1.0',
    '--output-filename=FlashOS-Setup.exe','--output-dir=build/installer/nuitka',
    '--report=build/installer/nuitka-report.xml','--python-flag=isolated','--python-flag=safe_path')
  if($Mode -eq 'onefile') { $arguments+=@('--onefile-no-compression','--onefile-no-dll') }
  $arguments+='installer/entry.py'
  & $python @arguments
  if($LASTEXITCODE -ne 0) { throw 'Nuitka compilation failed.' }
  if($Mode -eq 'onefile') {
    $null=New-Item -ItemType Directory -Path release -Force
    Copy-Item -LiteralPath build/installer/nuitka/FlashOS-Setup.exe -Destination release/FlashOS-Setup.exe -Force
    $hash=Get-FileHash -LiteralPath release/FlashOS-Setup.exe -Algorithm SHA256
    $hash | ConvertTo-Json | Set-Content -LiteralPath release/FlashOS-Setup.sha256.json -Encoding UTF8
    ($hash.Hash.ToLowerInvariant()+' *FlashOS-Setup.exe') | Set-Content -LiteralPath release/SHA256SUMS.txt -Encoding ASCII
  }
} finally { Pop-Location }
