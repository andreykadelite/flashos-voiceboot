"""Small Windows boundary; no disk mutation is performed by inventory."""
import base64
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys

INVENTORY_SCRIPT = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$ProgressPreference='SilentlyContinue'
$physical=@(Get-CimInstance Win32_DiskDrive)
$pageLetters=@(Get-CimInstance Win32_PageFileUsage | ForEach-Object { $_.Name.Substring(0,1) })
$protectedNumbers=@(foreach($path in $installerPaths) {
  if(Test-Path -LiteralPath $path) {
    Get-Volume -FilePath $path | Get-Partition | ForEach-Object DiskNumber
  }
})
$result=@(foreach($disk in Get-Disk) {
  $win=$physical | Where-Object Index -eq $disk.Number | Select-Object -First 1
  $volumes=@(foreach($part in @(Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue)) {
    if($part.DriveLetter -and [int]$part.DriveLetter -ne 0) {
      $v=Get-Volume -DriveLetter $part.DriveLetter -ErrorAction SilentlyContinue
      if($v) { @{letter=[string]$part.DriveLetter;label=[string]$v.FileSystemLabel;filesystem=[string]$v.FileSystem;free=[long]$v.SizeRemaining;partition=$part.PartitionNumber} }
    }
  })
  $protected=($disk.Number -in $protectedNumbers) -or [bool](@($volumes | Where-Object { $_.letter -in $pageLetters }).Count)
  @{number=$disk.Number;model=[string]$disk.FriendlyName;serial=([string]$disk.SerialNumber).Trim();size=[long]$disk.Size;
    sector=$disk.LogicalSectorSize;path=[string]$disk.Path;pnp=[string]$win.PNPDeviceID;bus=[string]$disk.BusType;
    boot=[bool]$disk.IsBoot;system=[bool]$disk.IsSystem;readonly=[bool]$disk.IsReadOnly;offline=[bool]$disk.IsOffline;
    protected=$protected;style=[string]$disk.PartitionStyle;volumes=$volumes}
})
ConvertTo-Json -InputObject $result -Depth 5 -Compress
'''


def powershell():
    return str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')


def ps_command(script):
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return [powershell(), '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded]


def inventory():
    paths = [str(Path(p).resolve()) for p in (sys.argv[0], sys.executable, __file__, os.environ.get('TEMP', ''), os.environ['SystemRoot']) if p]
    encoded = base64.b64encode(json.dumps(paths).encode('utf-8')).decode('ascii')
    prefix = "$installerPaths=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "')) | ConvertFrom-Json\n"
    result = subprocess.run(ps_command(prefix + INVENTORY_SCRIPT), capture_output=True, timeout=45,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace').strip() or 'Windows не смогла прочитать список дисков.')
    disks = json.loads(result.stdout.decode('utf-8-sig'))
    return disks


def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def elevate():
    # Relaunch only the interface. No command-line option authorizes a disk write.
    if '__compiled__' in globals() or getattr(sys, 'frozen', False):
        executable, args = str(Path(sys.argv[0]).resolve()), ''
    else:
        executable, args = sys.executable, subprocess.list2cmdline([str(Path(__file__).with_name('entry.py'))])
    value = ctypes.windll.shell32.ShellExecuteW(None, 'runas', executable, args, str(Path(executable).parent), 1)
    if value <= 32:
        raise RuntimeError('Запрос прав администратора отменён или Windows не смогла открыть установщик.')


class OperationMutex:
    def __enter__(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        self.handle = kernel.CreateMutexW(None, False, 'Global\\FlashOSInstallerDiskWriter')
        if not self.handle or ctypes.get_last_error() == 183:
            if self.handle:
                kernel.CloseHandle(ctypes.c_void_p(self.handle))
            raise RuntimeError('Другая операция Flash OS уже выполняется. Дождитесь её завершения.')
        return self

    def __exit__(self, *args):
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self.handle))
