"""Create administrator-owned work data and pin its non-reparse parent directories."""
import ctypes
from ctypes import wintypes as W
import os
from pathlib import Path
import uuid


class SecurityAttributes(ctypes.Structure):
    _fields_ = [('length', W.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', W.BOOL)]


class SecureRun:
    def __init__(self):
        self.handles = []
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        self.kernel.CreateFileW.restype = W.HANDLE
        self.kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        self.kernel.GetFileAttributesW.argtypes = [W.LPCWSTR]
        self.kernel.GetFileAttributesW.restype = W.DWORD
        self.kernel.CreateDirectoryW.argtypes = [W.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [W.LPCWSTR, W.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        folder_id = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID('f1b32785-6fba-4fcf-9d55-7b8e7f157091').bytes_le)
        folder_path = ctypes.c_wchar_p()
        shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, W.DWORD, W.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        if shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(folder_path)) != 0:
            raise RuntimeError('Windows не предоставила локальный каталог профиля.')
        try:
            base = Path(folder_path.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(folder_path, ctypes.c_void_p))
        try:
            # No ancestor may be swapped for a junction while elevated code uses it.
            for directory in reversed([base] + list(base.parents)):
                self.pin(directory)
            parent = base
            for name in ('FlashOS Installer', 'runs'):
                parent = parent / name
                if not parent.exists():
                    parent.mkdir()
                self.pin(parent)
            self.path = parent / uuid.uuid4().hex
            descriptor = ctypes.c_void_p()
            sddl = 'O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)S:(ML;OICI;NW;;;HI)'
            if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                security = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
                if not self.kernel.CreateDirectoryW(str(self.path), ctypes.byref(security)):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self.kernel.LocalFree(descriptor)
            self.pin(self.path)
        except Exception:
            self.close()
            raise

    def pin(self, path):
        handle = self.kernel.CreateFileW(str(path), 0x80, 3, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handles.append(handle)
        attrs = self.kernel.GetFileAttributesW(str(path))
        if attrs == 0xFFFFFFFF or attrs & 0x400:
            raise RuntimeError('Каталог установщика содержит ссылку или точку перенаправления. Используйте обычный локальный профиль Windows.')

    def close(self):
        for handle in reversed(self.handles):
            self.kernel.CloseHandle(handle)
        self.handles.clear()
