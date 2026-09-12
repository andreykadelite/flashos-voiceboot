"""Verify the built PE's startup privilege request without executing the EXE."""
import ctypes
from ctypes import wintypes as W
import json
from pathlib import Path
import sys
from xml.etree import ElementTree


def read_manifest(path):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, args, result in (
        ('LoadLibraryExW', [W.LPCWSTR, W.HANDLE, W.DWORD], W.HMODULE),
        ('FindResourceW', [W.HMODULE, ctypes.c_void_p, ctypes.c_void_p], W.HANDLE),
        ('LoadResource', [W.HMODULE, W.HANDLE], W.HANDLE),
        ('SizeofResource', [W.HMODULE, W.HANDLE], W.DWORD),
        ('LockResource', [W.HANDLE], ctypes.c_void_p),
        ('FreeLibrary', [W.HMODULE], W.BOOL),
    ):
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    module = kernel.LoadLibraryExW(str(path), None, 2)  # Data only; never run the EXE.
    if not module:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        resource = kernel.FindResourceW(module, 1, 24)
        if not resource:
            raise ctypes.WinError(ctypes.get_last_error())
        size = kernel.SizeofResource(module, resource)
        loaded = kernel.LoadResource(module, resource)
        pointer = kernel.LockResource(loaded) if loaded else None
        if not size or not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        return ElementTree.fromstring(ctypes.string_at(pointer, size))
    finally:
        kernel.FreeLibrary(module)


def check(path):
    path = Path(path).resolve(strict=True)
    manifest = read_manifest(path)
    nodes = manifest.findall('.//{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel')
    if len(nodes) != 1 or nodes[0].get('level') != 'requireAdministrator' or nodes[0].get('uiAccess') != 'false':
        raise ValueError(f'{path}: the EXE must request administrator rights before opening the menu')
    return dict(path=str(path), level=nodes[0].get('level'), uiAccess=nodes[0].get('uiAccess'))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('Pass the built EXE path(s)')
    print(json.dumps([check(path) for path in sys.argv[1:]], ensure_ascii=False))
