"""Validate x64 installer PE layout and Windows image mapping without executing it."""
import ctypes
from ctypes import wintypes as W
import json
from pathlib import Path
import struct
import sys

MAX_IMAGE_BYTES = 256 * 1024**2


def image_layout(path):
    path = Path(path).resolve(strict=True)
    size = path.stat().st_size
    with path.open('rb') as stream:
        dos = stream.read(64)
        if len(dos) != 64 or dos[:2] != b'MZ':
            raise ValueError('Missing DOS executable header')
        offset = struct.unpack_from('<I', dos, 60)[0]
        if offset < 64 or offset + 24 > size:
            raise ValueError('Invalid PE header offset')
        stream.seek(offset)
        header = stream.read(24)
        if header[:4] != b'PE\0\0':
            raise ValueError('Missing PE executable header')
        machine, count, _, _, _, optional_size, flags = struct.unpack_from('<HHIIIHH', header, 4)
        if machine != 0x8664 or not flags & 2 or flags & 0x2000:
            raise ValueError('Installer must be a Windows x64 executable')
        if not 1 <= count <= 96 or optional_size < 112:
            raise ValueError('Invalid PE optional header or section count')
        optional = stream.read(optional_size)
        if len(optional) != optional_size or struct.unpack_from('<H', optional)[0] != 0x20b:
            raise ValueError('Installer must use PE32+ format')
        image_bytes, header_bytes = struct.unpack_from('<II', optional, 56)
        section_alignment, file_alignment = struct.unpack_from('<II', optional, 32)
        if (not 0 < image_bytes <= MAX_IMAGE_BYTES or not 0 < header_bytes <= image_bytes
                or not file_alignment or not section_alignment
                or offset + 24 + optional_size + count * 40 > header_bytes):
            raise ValueError('Invalid or oversized mapped image; system data must stay outside PE sections')
        sections = []
        for _ in range(count):
            raw = stream.read(40)
            if len(raw) != 40:
                raise ValueError('Truncated PE section table')
            virtual_size, rva, raw_size, raw_offset = struct.unpack_from('<IIII', raw, 8)
            if (rva % section_alignment or rva + max(virtual_size, raw_size) > image_bytes
                    or (raw_size and (raw_offset < header_bytes or raw_offset % file_alignment
                                     or raw_offset + raw_size > size))):
                raise ValueError('Invalid PE section extent or alignment')
            sections.append({'name': raw[:8].rstrip(b'\0').decode('ascii', 'replace'),
                             'raw_end': raw_offset + raw_size, 'virtual_end': rva + virtual_size})
    return {'path': str(path), 'machine': 'amd64', 'format': 'PE32+',
            'file_bytes': size, 'image_bytes': image_bytes,
            'mapped_image_limit': MAX_IMAGE_BYTES,
            'pe_file_end': max(header_bytes, *(row['raw_end'] for row in sections)),
            'sections': len(sections)}


def windows_image_mapping(path):
    if sys.platform != 'win32':
        raise RuntimeError('The installer image gate must run on Windows')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
    kernel.CreateFileW.restype = W.HANDLE
    kernel.CreateFileMappingW.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR]
    kernel.CreateFileMappingW.restype = W.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [W.HANDLE], W.BOOL
    handle = kernel.CreateFileW(str(Path(path).resolve(strict=True)), 0x80000000, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # SEC_IMAGE_NO_EXECUTE + PAGE_READONLY: validate the image; never run code or UAC.
        section = kernel.CreateFileMappingW(handle, None, 0x11000002, 0, 0, None)
        if not section:
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(section)
    finally:
        kernel.CloseHandle(handle)
    return {'windows_image_mapping': True, 'executable_code_run': False}


def check(path):
    result = image_layout(path)
    result.update(windows_image_mapping(path))
    return result


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('Pass the built EXE path(s)')
    print(json.dumps([check(path) for path in sys.argv[1:]]))
