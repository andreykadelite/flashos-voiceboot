"""Keep the selected device object open, detect removal, and permit readback by handle."""
import ctypes
from ctypes import wintypes as W


class DiskLease:
    def __init__(self, target):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateFileW.restype = W.HANDLE
        self.kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        self.kernel.DeviceIoControl.argtypes = [W.HANDLE, W.DWORD, ctypes.c_void_p, W.DWORD, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p]
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.kernel.SetFilePointerEx.argtypes = [W.HANDLE, ctypes.c_int64, ctypes.c_void_p, W.DWORD]
        self.kernel.ReadFile.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p]
        # A metadata-only handle pins the device object without blocking Ventoy's
        # FSCTL_LOCK_VOLUME. GENERIC_READ here prevents Ventoy from acquiring its lock.
        self.handle = self.kernel.CreateFileW(target['path'], 0, 3, None, 3, 0, None)
        self.reader = None
        if self.handle == ctypes.c_void_p(-1).value:
            self.handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        self.target = target
        try:
            self.check()
        except Exception:
            self.close()
            raise

    def check(self):
        self._check_handle(self.handle)

    def _check_handle(self, handle):
        number = (W.DWORD * 3)()
        geometry = ctypes.create_string_buffer(1024)
        count = W.DWORD()
        for code, result in ((0x002D1080, number), (0x000700A0, geometry)):
            if not self.kernel.DeviceIoControl(handle, code, None, 0, ctypes.byref(result), ctypes.sizeof(result), ctypes.byref(count), None):
                raise OSError('Связь с выбранным USB-накопителем потеряна. Запись остановлена.')
            if count.value < (12 if code == 0x002D1080 else 32):
                raise OSError('Windows вернула неполные сведения об открытом устройстве.')
        length = ctypes.c_int64.from_buffer_copy(geometry.raw[24:32]).value
        sector = W.DWORD.from_buffer_copy(geometry.raw[20:24]).value
        if number[0] != 7 or number[1] != self.target['number'] or length != self.target['size'] or sector != self.target['sector']:
            raise OSError('Идентификатор открытого устройства не совпал. Запись остановлена.')

    def close(self):
        if self.reader:
            self.kernel.CloseHandle(self.reader)
            self.reader = None
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > self.target['size']:
            raise ValueError('Чтение выходит за пределы выбранного устройства.')
        self.check()
        if self.reader is None:
            self.reader = self.kernel.CreateFileW(self.target['path'], 0x80000000, 3, None, 3, 0, None)
            if self.reader == ctypes.c_void_p(-1).value:
                self.reader = None
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                self._check_handle(self.reader)
                self.check()
            except Exception:
                self.kernel.CloseHandle(self.reader)
                self.reader = None
                raise
        if not self.kernel.SetFilePointerEx(self.reader, offset, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(length)
        count = W.DWORD()
        if not self.kernel.ReadFile(self.reader, buffer, length, ctypes.byref(count), None) or count.value != length:
            raise OSError('Не удалось прочитать загрузочную область флешки полностью.')
        return buffer.raw
