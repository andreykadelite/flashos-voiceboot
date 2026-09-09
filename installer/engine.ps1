# Windows PowerShell 5.1 is included with supported Windows versions.
param([Parameter(Mandatory)][string]$RequestPath)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Set-StrictMode -Version Latest
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$request=Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$manifest=Get-Content -LiteralPath $request.manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$cancel=Join-Path (Split-Path -Parent $RequestPath) 'cancel'
$script:changed=$false
$script:doneBytes=[long]0
$script:totalBytes=[long]1
$script:lastProgress=[datetime]::MinValue
$script:phase='preflight'
function Event([string]$kind,[string]$message,$percent=$null) {
  $data=@{kind=$kind;message=$message;phase=$script:phase;percent=$percent;changed=$script:changed;bytes=$script:doneBytes}
  [Console]::WriteLine((ConvertTo-Json -InputObject $data -Compress))
  [Console]::Out.Flush()
}
function Phase([string]$name,[string]$message) {
  $script:phase=$name
  Event 'phase' $message
}
function Check-Cancel {
  if(Test-Path -LiteralPath $cancel) { throw [OperationCanceledException]::new('Операция остановлена по запросу пользователя.') }
}
function Assert-Target {
  $target=$request.target
  $disk=Get-Disk -Number $target.number
  $win=Get-CimInstance Win32_DiskDrive | Where-Object Index -eq $target.number | Select-Object -First 1
  if($disk.IsBoot -or $disk.IsSystem -or $disk.IsReadOnly -or $disk.IsOffline -or [string]$disk.BusType -ne 'USB' -or
     $disk.Size -ne $target.size -or $disk.LogicalSectorSize -ne 512 -or
     ([string]$disk.SerialNumber).Trim() -cne [string]$target.serial -or [string]$disk.FriendlyName -cne [string]$target.model -or
     [string]$disk.Path -cne [string]$target.path -or [string]$win.PNPDeviceID -cne [string]$target.pnp -or
     -not $target.path -or -not $target.pnp) { throw 'Устройство изменилось, занято Windows или недоступно. Запись запрещена.' }
  $letters=@(Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue | Where-Object DriveLetter | ForEach-Object { [string]$_.DriveLetter })
  $protectedPaths=@($RequestPath,$request.manifest,$request.objects,$request.engine,$PSHOME,$env:SystemRoot)+@($request.protected_paths)
  $protectedPaths+=@(Get-CimInstance Win32_PageFileUsage | ForEach-Object Name)
  foreach($path in $protectedPaths) {
    if($path -match '^([A-Za-z]):' -and $Matches[1] -in $letters) { throw 'Выбранный диск содержит Windows, файл подкачки или файлы установщика.' }
    $probe=[string]$path
    while($probe -and -not (Test-Path -LiteralPath $probe)) { $probe=[IO.Path]::GetDirectoryName($probe.TrimEnd('\')) }
    if($probe) {
      $owners=@(Get-Volume -FilePath $probe | Get-Partition | ForEach-Object DiskNumber)
      if($disk.Number -in $owners) { throw 'На выбранном диске находятся файлы работающего установщика или Windows.' }
    }
  }
  return $disk
}
function Assert-Volume([string]$root,[int]$partition) {
  $null=Assert-Target
  if($root -notmatch '^[A-Z]:\\$') { throw 'Неверный путь раздела.' }
  $p=Get-Partition -DriveLetter $root.Substring(0,1)
  if($p.DiskNumber -ne $request.target.number -or $p.PartitionNumber -ne $partition) { throw 'Буква раздела теперь относится к другому устройству.' }
}
function Root-For($partition) {
  if(-not $partition.DriveLetter) {
    $partition | Add-PartitionAccessPath -AssignDriveLetter
    $partition=Get-Partition -DiskNumber $partition.DiskNumber -PartitionNumber $partition.PartitionNumber
  }
  if(-not $partition.DriveLetter) { throw 'Windows не назначила букву разделу. Закройте другие программы работы с дисками и повторите.' }
  return ([string]$partition.DriveLetter + ':\')
}
function Io-Root([string]$root,[int]$partition) {
  Assert-Volume $root $partition
  $p=Get-Partition -DiskNumber $request.target.number -PartitionNumber $partition
  $paths=@($p.AccessPaths | Where-Object { $_ -match '^\\\\\?\\Volume\{[A-Fa-f0-9-]+\}\\$' })
  if($paths.Count -ne 1) { throw 'Windows не предоставила постоянный путь тома.' }
  return $paths[0]
}
function Safe-Join([string]$root,[string]$relative) {
  if($relative -notmatch '^[A-Za-z0-9_А-Яа-яЁё. /-]+$' -or $relative.Split('/') -contains '..' -or [IO.Path]::IsPathRooted($relative)) { throw 'Недопустимый путь в комплекте.' }
  $path=[IO.Path]::GetFullPath((Join-Path $root $relative))
  $prefix=[IO.Path]::GetFullPath($root).TrimEnd('\')+'\'
  if(-not $path.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Путь выходит за пределы раздела.' }
  $probe=$path
  while($probe -and $probe.Length -ge $prefix.Length) {
    if(Test-Path -LiteralPath $probe) {
      if((Get-Item -LiteralPath $probe -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Ссылки и точки подключения внутри целевой папки не допускаются.' }
    }
    $probe=[IO.Path]::GetDirectoryName($probe)
  }
  return $path
}
function Progress-Bytes([int]$count,[string]$name) {
  $script:doneBytes+=$count
  if(([datetime]::UtcNow-$script:lastProgress).TotalMilliseconds -ge 500) {
    Event 'progress' $name ([math]::Min(100,[math]::Round(100*$script:doneBytes/$script:totalBytes,1)))
    $script:lastProgress=[datetime]::UtcNow
  }
}
function Copy-File([string]$source,[string]$destination,[string]$name) {
  $parent=[IO.Path]::GetDirectoryName($destination)
  # .NET drops the trailing separator for a volume-GUID root. Win32 requires it.
  if($parent -match '^\\\\\?\\Volume\{[A-Fa-f0-9-]+\}$') { $parent+='\' }
  $null=[IO.Directory]::CreateDirectory($parent)
  $inputFile=[IO.File]::OpenRead($source)
  try {
    $outputFile=[IO.File]::Open($destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try {
      $buffer=New-Object byte[] (4MB)
      while(($count=$inputFile.Read($buffer,0,$buffer.Length)) -gt 0) {
        Check-Cancel
        $outputFile.Write($buffer,0,$count)
        Progress-Bytes $count $name
      }
      $outputFile.Flush($true)
    } finally { $outputFile.Dispose() }
  } finally { $inputFile.Dispose() }
}
function Hash-File([string]$path,[string]$name) {
  $file=[IO.File]::OpenRead($path)
  $sha=[Security.Cryptography.SHA256]::Create()
  try {
    $buffer=New-Object byte[] (4MB)
    while(($count=$file.Read($buffer,0,$buffer.Length)) -gt 0) {
      Check-Cancel
      $null=$sha.TransformBlock($buffer,0,$count,$buffer,0)
      Progress-Bytes $count $name
    }
    $null=$sha.TransformFinalBlock($buffer,0,0)
    return ([BitConverter]::ToString($sha.Hash).Replace('-','').ToLowerInvariant())
  } finally { $sha.Dispose();$file.Dispose() }
}
function Data-Uuid([string]$root) {
  $volumes=@(Get-CimInstance Win32_Volume | Where-Object DeviceID -eq $root)
  if($volumes.Count -ne 1 -or $null -eq $volumes[0].SerialNumber) { throw 'Не удалось получить серийный номер выбранного тома.' }
  $serial=([uint32]$volumes[0].SerialNumber).ToString('X8')
  if($serial -notmatch '^[A-Fa-f0-9]{8}$') { throw 'Не удалось получить UUID основного раздела.' }
  return ($serial.Substring(0,4)+'-'+$serial.Substring(4,4)).ToUpperInvariant()
}
try {
  if(-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Для операции нужны права администратора.' }
  if($request.action -notin @('install','format','verify') -or $request.style -notin @('MBR','GPT') -or $request.filesystem -notin @('exFAT','NTFS','FAT32')) { throw 'Недопустимые параметры операции.' }
  if($request.action -ne 'verify' -and $request.erase_confirmed -ne $true) { throw 'Удаление данных не подтверждено.' }
  $disk=Assert-Target
  if($request.action -eq 'install' -and $disk.Size -lt 4000000000) { throw 'Нужна флешка объёмом не менее 4 ГБ.' }
  if($request.action -ne 'verify' -and $request.style -eq 'MBR' -and $disk.Size -ge 2TB) { throw 'Для накопителя от 2 ТиБ требуется GPT.' }
  if($request.action -eq 'format' -and $request.filesystem -eq 'FAT32' -and $disk.Size -gt 32000000000) { throw 'FAT32 доступна для накопителей до 32 ГБ.' }
  Check-Cancel
  if($request.action -eq 'format') {
    Phase 'format' 'Форматирование. Не отключайте флешку; остановка станет возможна после завершения этого этапа.'
    $null=Assert-Target
    $script:changed=$true
    $disk | Clear-Disk -RemoveData -RemoveOEM -Confirm:$false
    $disk=Assert-Target
    if([string]$disk.PartitionStyle -eq 'RAW') {
      $disk | Initialize-Disk -PartitionStyle $request.style | Out-Null
    } elseif([string]$disk.PartitionStyle -ne $request.style) {
      $disk | Set-Disk -PartitionStyle $request.style
    }
    $disk=Assert-Target
    if([string]$disk.PartitionStyle -ne $request.style) { throw 'Windows не создала выбранную разметку.' }
    $p=$disk | New-Partition -UseMaximumSize -AssignDriveLetter
    $null=Assert-Target
    $p | Format-Volume -FileSystem $request.filesystem -NewFileSystemLabel 'USB' -Confirm:$false | Out-Null
    $root=Root-For $p
    Assert-Volume $root $p.PartitionNumber
    $volume=Get-Volume -Partition $p
    $volume | Write-VolumeCache
    if($volume.FileSystem -ne $request.filesystem) { throw 'Файловая система после форматирования не совпала с выбранной.' }
    Event 'complete' ('Флешка отформатирована в '+$volume.FileSystem+'. Раздел '+$root) 100
    exit 0
  }
  if($request.action -eq 'install') {
    Phase 'partition' 'Создание загрузочных разделов. Не отключайте флешку.'
    $null=Assert-Target
    $argsList=@('VTOYCLI','/I',('/PhyDrive:'+$disk.Number),'/R:512','/FS:EXFAT')
    if($request.style -eq 'GPT') { $argsList+='/GPT' }
    $script:changed=$true
    $process=Start-Process -FilePath (Join-Path $request.engine 'Ventoy2Disk.exe') -ArgumentList $argsList -WorkingDirectory $request.engine -WindowStyle Hidden -PassThru
    $last='';$waitNotice=$false;$started=[datetime]::UtcNow
    while(-not $process.HasExited) {
      $percentPath=Join-Path $request.engine 'cli_percent.txt'
      if(Test-Path -LiteralPath $percentPath) {
        $value=([string](Get-Content -LiteralPath $percentPath -Raw -ErrorAction SilentlyContinue)).Trim()
        if($value -match '^\d+$' -and $value -ne $last) { Event 'progress' 'Создание разделов Ventoy' ([math]::Min(100,[int]$value));$last=$value }
      }
      if(-not $waitNotice -and ([datetime]::UtcNow-$started).TotalMinutes -ge 8) {
        Event 'log' 'Запись занимает больше времени, чем обычно. Ожидаем завершения Ventoy; принудительное закрытие может повредить разметку.'
        $waitNotice=$true
      }
      Start-Sleep -Milliseconds 500
      $process.Refresh()
    }
    $process.WaitForExit()
    $donePath=Join-Path $request.engine 'cli_done.txt'
    if(-not (Test-Path -LiteralPath $donePath) -or (Get-Content -LiteralPath $donePath -Raw).Trim() -ne '0' -or $process.ExitCode -ne 0) { throw 'Ventoy не смог создать разделы. Подробности сохранены в журнале Ventoy.' }
    $null=Assert-Target
    Check-Cancel
    Update-HostStorageCache
    $main=Get-Partition -DiskNumber $disk.Number -PartitionNumber 1
    $mainRoot=Root-For $main
    Assert-Volume $mainRoot 1
    $mainVolume=Get-Volume -Partition $main
    if($mainVolume.FileSystem -ne 'exFAT') { throw 'Ventoy не создал ожидаемый раздел exFAT.' }
    $mainVolume | Set-Volume -NewFileSystemLabel 'FLASHOS'
    $disk=Assert-Target
    $boot=$disk | New-Partition -UseMaximumSize -AssignDriveLetter
    if($boot.PartitionNumber -ne 3 -or $boot.Size -lt 500MB -or $boot.Size -gt 514MB) { throw 'Неожиданный размер или номер загрузочного раздела.' }
    $null=Assert-Target
    $boot | Format-Volume -FileSystem FAT32 -NewFileSystemLabel 'FLASHBOOT' -Confirm:$false | Out-Null
    $bootRoot=Root-For $boot
    $roots=@{usb=(Io-Root $mainRoot 1);boot=(Io-Root $bootRoot 3)}
    $letters=@{usb=$mainRoot;boot=$bootRoot}
    Phase 'copy' 'Запись системы FlashOS'
    $script:doneBytes=0
    $script:totalBytes=($manifest.files | Where-Object section -in @('usb','boot') | Measure-Object bytes -Sum).Sum
    foreach($entry in $manifest.files | Where-Object section -in @('usb','boot')) {
      $part=if($entry.section -eq 'usb'){1}else{3}
      Assert-Volume $letters[$entry.section] $part
      $destination=Safe-Join $roots[$entry.section] $entry.path
      Copy-File (Join-Path $request.objects $entry.sha256) $destination $entry.path
    }
    $null=[IO.Directory]::CreateDirectory((Join-Path $roots.usb 'iso'))
    $uuid=Data-Uuid $roots.usb
    foreach($relative in @('flashos/efi/grub.cfg','EFI/debian/grub.cfg')) {
      Assert-Volume $bootRoot 3
      $configPath=Safe-Join $roots.boot $relative
      $template=[IO.File]::ReadAllText($configPath)
      if([regex]::Matches($template,'@DATA_UUID@').Count -ne 1) { throw 'Ошибка шаблона загрузки.' }
      [IO.File]::WriteAllText($configPath,$template.Replace('@DATA_UUID@',$uuid),[Text.UTF8Encoding]::new($false))
    }
    Write-VolumeCache -Path $roots.usb
    Write-VolumeCache -Path $roots.boot
  } else {
    $mainRoot=Root-For (Get-Partition -DiskNumber $disk.Number -PartitionNumber 1)
    $bootRoot=Root-For (Get-Partition -DiskNumber $disk.Number -PartitionNumber 3)
    $roots=@{usb=(Io-Root $mainRoot 1);boot=(Io-Root $bootRoot 3)}
    $letters=@{usb=$mainRoot;boot=$bootRoot}
    if((Get-Volume -Path $roots.usb).FileSystemLabel -ne 'FLASHOS' -or (Get-Volume -Path $roots.boot).FileSystemLabel -ne 'FLASHBOOT') { throw 'На выбранной флешке не обнаружены разделы FlashOS.' }
    $uuid=Data-Uuid $roots.usb
  }
  Phase 'verify' 'Проверка записанных файлов по SHA256'
  $script:doneBytes=0
  $script:totalBytes=($manifest.files | Where-Object section -in @('usb','boot') | Measure-Object bytes -Sum).Sum
  foreach($entry in $manifest.files | Where-Object section -in @('usb','boot')) {
    $part=if($entry.section -eq 'usb'){1}else{3}
    Assert-Volume $letters[$entry.section] $part
    $path=Safe-Join $roots[$entry.section] $entry.path
    if($entry.section -eq 'boot' -and $entry.path -in @('flashos/efi/grub.cfg','EFI/debian/grub.cfg')) {
      $original=[IO.File]::ReadAllText((Join-Path $request.objects $entry.sha256))
      if([IO.File]::ReadAllText($path) -cne $original.Replace('@DATA_UUID@',$uuid)) { throw ('Не совпала конфигурация загрузки: '+$entry.path) }
      Progress-Bytes $entry.bytes $entry.path
    } elseif((Hash-File $path $entry.path) -cne $entry.sha256) { throw ('Не совпала контрольная сумма: '+$entry.path) }
  }
  $null=Assert-Target
  Check-Cancel
  Event 'complete' ('FlashOS проверена. Папка для образов: '+$mainRoot+'iso. Можно безопасно извлечь флешку средствами Windows.') 100
} catch {
  $kind=if($_.Exception -is [OperationCanceledException]){'cancelled'}else{'error'}
  $message=$_.Exception.Message
  $detail=[string]$_.ScriptStackTrace
  if($detail) { Event 'log' $detail }
  if($script:changed) { $message+=' Данные на выбранной флешке уже изменены. Установку или форматирование нужно повторить.' }
  Event $kind $message
  exit 1
}
