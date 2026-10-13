"""Exercise the real compiled bootstrap and Qt UI without UAC or device access.

The production executable is never changed. A QA copy has only the two PE
manifest privilege attributes changed. Only the uncompressed temporary-file
Nuitka format used by this project is accepted.
"""
import argparse
import ctypes
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
ATTRIBUTE = b'level="requireAdministrator"'
REPLACEMENT = b'level="asInvoker"' + b' ' * (len(ATTRIBUTE) - len(b'level="asInvoker"'))
MAX_CORE = 256 * 1024**2


def pe_layout(data):
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('Invalid DOS header')
    pe = struct.unpack_from('<I', data, 60)[0]
    if pe + 24 > len(data) or data[pe:pe+4] != b'PE\0\0':
        raise ValueError('Invalid PE signature')
    machine, sections = struct.unpack_from('<HH', data, pe+4)
    optional_size = struct.unpack_from('<H', data, pe+20)[0]
    optional = pe + 24
    if not 1 <= sections <= 96 or optional + optional_size > len(data):
        raise ValueError('Invalid PE header bounds')
    magic = struct.unpack_from('<H', data, optional)[0]
    directory = optional + {0x20b: 112, 0x10b: 96}.get(magic, -MAX_CORE)
    if directory < optional or directory+24 > optional+optional_size:
        raise ValueError('Unsupported PE optional header')
    resource_rva, resource_size = struct.unpack_from('<II', data, directory+16)
    table = optional + optional_size
    if table+sections*40 > len(data):
        raise ValueError('Invalid section table')
    entries = []
    end = table+sections*40
    for index in range(sections):
        virtual_size, rva, raw_size, raw = struct.unpack_from('<IIII', data, table+index*40+8)
        if raw_size and (raw < table+sections*40 or raw+raw_size > MAX_CORE):
            raise ValueError('Unbounded PE section')
        entries.append((rva, max(virtual_size, raw_size), raw, raw_size))
        end = max(end, raw+raw_size)
    def offset(rva, size):
        for start, span, raw, raw_size in entries:
            relative = rva-start
            if 0 <= relative and relative+size <= raw_size:
                return raw+relative
        raise ValueError('Resource points outside a PE section')
    if not resource_rva or resource_size < 16:
        raise ValueError('Missing PE resources')
    return dict(end=end, machine=machine, resource=offset(resource_rva, resource_size),
                resource_size=resource_size, offset=offset)


def manifest_range(data):
    layout = pe_layout(data)
    base = layout['resource']
    def entries(relative):
        if relative < 0 or relative+16 > layout['resource_size']:
            raise ValueError('Resource directory outside bounds')
        named, numbered = struct.unpack_from('<HH', data, base+relative+12)
        count = named+numbered
        if count > 256 or relative+16+count*8 > layout['resource_size']:
            raise ValueError('Invalid resource directory')
        return [struct.unpack_from('<II', data, base+relative+16+index*8)
                for index in range(count)]
    directory = 0
    for resource_id in (24, 1):
        matching = [value for key, value in entries(directory) if key == resource_id]
        if len(matching) != 1 or not matching[0] & 0x80000000:
            raise ValueError('Expected exactly one ID-1 manifest resource')
        directory = matching[0] & 0x7fffffff
    languages = entries(directory)
    if len(languages) != 1 or languages[0][1] & 0x80000000:
        raise ValueError('Expected exactly one manifest language')
    relative = languages[0][1]
    if relative+16 > layout['resource_size']:
        raise ValueError('Manifest data entry outside bounds')
    rva, size = struct.unpack_from('<II', data, base+relative)
    if not 0 < size <= 64*1024:
        raise ValueError('Invalid manifest length')
    start = layout['offset'](rva, size)
    raw = bytes(data[start:start+size])
    manifest = ElementTree.fromstring(raw)
    nodes = manifest.findall('.//{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel')
    if len(nodes) != 1 or nodes[0].get('level') != 'requireAdministrator' or nodes[0].get('uiAccess') != 'false':
        raise ValueError('Production manifest must require administrator rights')
    if raw.count(ATTRIBUTE) != 1:
        raise ValueError('Expected a single standard execution-level attribute')
    position = start+raw.index(ATTRIBUTE)
    return position, len(ATTRIBUTE), start, size


def onefile_inner(core):
    marker = b'KAX' + 'FlashOS-Setup.exe\0'.encode('utf-16le')
    candidates = []
    position = core.find(marker)
    while position >= 0:
        header = position+len(marker)
        if header+12 <= len(core):
            size = struct.unpack_from('<Q', core, header)[0]
            start = header+8
            if 64 <= size <= MAX_CORE and start+size <= len(core) and core[start:start+2] == b'MZ':
                inner = core[start:start+size]
                try:
                    if pe_layout(inner)['end'] <= size:
                        manifest_range(inner)
                        candidates.append((start, size))
                except (ValueError, struct.error, ElementTree.ParseError):
                    pass
        position = core.find(marker, position+1)
    if len(candidates) != 1:
        raise ValueError('Expected exactly one validated uncompressed Nuitka inner EXE')
    return candidates[0]


def patch_core(core):
    if len(core) > MAX_CORE or pe_layout(core)['end'] != len(core):
        raise ValueError('The QA patch accepts only the bounded PE image, excluding its overlay')
    if core.count(ATTRIBUTE) != 2:
        raise ValueError('Expected exactly two production privilege attributes')
    outer = manifest_range(core)
    inner_start, inner_size = onefile_inner(core)
    inner = manifest_range(core[inner_start:inner_start+inner_size])
    patches = [(outer[0], ATTRIBUTE, REPLACEMENT),
               (inner_start+inner[0], ATTRIBUTE, REPLACEMENT)]
    changed = bytearray(core)
    for start, before, after in patches:
        if bytes(changed[start:start+len(before)]) != before or len(before) != len(after):
            raise ValueError('Unsafe manifest patch')
        changed[start:start+len(before)] = after
    for shift, descriptor in ((0, outer), (inner_start, inner)):
        _, _, manifest_start, manifest_size = descriptor
        parsed = ElementTree.fromstring(bytes(changed[shift+manifest_start:shift+manifest_start+manifest_size]))
        node = parsed.find('.//{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel')
        if node.get('level') != 'asInvoker' or node.get('uiAccess') != 'false':
            raise ValueError('The QA manifest does not parse safely')
    ranges = sorted((start, start+len(before)) for start, before, after in patches)
    cursor = 0
    for start, stop in ranges:
        if core[cursor:start] != changed[cursor:start]:
            raise ValueError('The QA copy changed bytes outside the manifest ranges')
        cursor = stop
    if core[cursor:] != changed[cursor:]:
        raise ValueError('The QA copy changed bytes outside the manifest ranges')
    return bytes(changed), [dict(offset=start, bytes=len(before), kind='manifest_attribute')
                           for start, before, after in patches]


def copy_for_qa(source, destination):
    with source.open('rb') as stream:
        header = stream.read(64*1024)
        end = pe_layout(header)['end']
        if not 0 < end <= MAX_CORE or end >= source.stat().st_size:
            raise ValueError('Expected a small PE image with an appended system archive')
        stream.seek(0)
        core = stream.read(end)
        if len(core) != end:
            raise ValueError('Truncated PE image')
        changed, ranges = patch_core(core)
        production_sha = hashlib.sha256(core)
        overlay_sha = hashlib.sha256()
        with destination.open('xb') as output:
            output.write(changed)
            while block := stream.read(8*1024**2):
                production_sha.update(block)
                overlay_sha.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
    with destination.open('rb') as output:
        if output.read(end) != changed:
            raise ValueError('The QA PE image was not copied exactly')
        copied_overlay = hashlib.file_digest(output, 'sha256').hexdigest()
    if copied_overlay != overlay_sha.hexdigest() or destination.stat().st_size != source.stat().st_size:
        raise ValueError('The appended system archive changed in the QA copy')
    return dict(production_sha256=production_sha.hexdigest(), pe_bytes=end,
                overlay_sha256=overlay_sha.hexdigest(), overlay_unchanged=True,
                patched_ranges=ranges, production_unchanged=True,
                bytes=source.stat().st_size, qa_executable=str(destination))


class STARTUPINFO(ctypes.Structure):
    _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR),
                ('lpTitle', W.LPWSTR), ('dwX', W.DWORD), ('dwY', W.DWORD),
                ('dwXSize', W.DWORD), ('dwYSize', W.DWORD), ('dwXCountChars', W.DWORD),
                ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD),
                ('dwFlags', W.DWORD), ('wShowWindow', W.WORD), ('cbReserved2', W.WORD),
                ('lpReserved2', ctypes.POINTER(ctypes.c_ubyte)), ('hStdInput', W.HANDLE),
                ('hStdOutput', W.HANDLE), ('hStdError', W.HANDLE)]


class PROCESSINFO(ctypes.Structure):
    _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE),
                ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]


class BASICLIMIT(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                ('PerJobUserTimeLimit', ctypes.c_longlong), ('LimitFlags', W.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', W.DWORD), ('Affinity', ctypes.c_size_t),
                ('PriorityClass', W.DWORD), ('SchedulingClass', W.DWORD)]


class IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ('ReadOperationCount', 'WriteOperationCount',
                'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class EXTENDEDLIMIT(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', BASICLIMIT), ('IoInfo', IOCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


def kernel_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    definitions = [
        ('CreateProcessW', [W.LPCWSTR, W.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL,
                           W.DWORD, ctypes.c_void_p, W.LPCWSTR, ctypes.POINTER(STARTUPINFO),
                           ctypes.POINTER(PROCESSINFO)], W.BOOL),
        ('TerminateProcess', [W.HANDLE, W.UINT], W.BOOL),
        ('CloseHandle', [W.HANDLE], W.BOOL), ('ResumeThread', [W.HANDLE], W.DWORD),
        ('CreateJobObjectW', [ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
        ('SetInformationJobObject', [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD], W.BOOL),
        ('AssignProcessToJobObject', [W.HANDLE, W.HANDLE], W.BOOL),
        ('WaitForSingleObject', [W.HANDLE, W.DWORD], W.DWORD),
        ('GetExitCodeProcess', [W.HANDLE, ctypes.POINTER(W.DWORD)], W.BOOL),
    ]
    for name, args, result in definitions:
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    return kernel


def create_suspended(kernel, executable, args):
    info = STARTUPINFO()
    info.cb = ctypes.sizeof(info)
    info.dwFlags = 1  # STARTF_USESHOWWINDOW
    info.wShowWindow = 0  # SW_HIDE
    process = PROCESSINFO()
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(executable), *args]))
    accepted = kernel.CreateProcessW(str(executable), command, None, None, False,
                                    4 | 0x08000000, None, str(executable.parent),
                                    ctypes.byref(info), ctypes.byref(process))
    return process if accepted else None, 0 if accepted else ctypes.get_last_error()


def production_probe(executable):
    if ctypes.windll.shell32.IsUserAnAdmin():
        return dict(skipped=True, reason='Elevated host token; the production app was not launched')
    kernel = kernel_api()
    process, error = create_suspended(kernel, executable, [])
    if process is not None:
        try:
            kernel.TerminateProcess(process.hProcess, 1)
        finally:
            kernel.CloseHandle(process.hThread)
            kernel.CloseHandle(process.hProcess)
        raise ValueError('The production EXE unexpectedly started without its UAC request')
    if error != 740:
        raise OSError(error, 'Production CreateProcessW must return ERROR_ELEVATION_REQUIRED (740)')
    return dict(ok=True, windows_error=error, process_created=False,
                image_accepted=True, administrator_manifest_preserved=True)


def run_qa(executable, report):
    kernel = kernel_api()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    process = None
    try:
        limits = EXTENDEDLIMIT()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        process, error = create_suspended(kernel, executable, ['--self-test', '--report', str(report)])
        if process is None:
            raise ctypes.WinError(error)
        if not kernel.AssignProcessToJobObject(job, process.hProcess):
            kernel.TerminateProcess(process.hProcess, 1)
            raise ctypes.WinError(ctypes.get_last_error())
        if kernel.ResumeThread(process.hThread) == 0xffffffff:
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(process.hThread)
        process.hThread = None
        status = kernel.WaitForSingleObject(process.hProcess, 90_000)
        if status == 258:
            raise TimeoutError('Compiled bootstrap/startup diagnostic exceeded 90 seconds')
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = W.DWORD()
        if not kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if code.value != 0:
            raise RuntimeError(f'Compiled startup diagnostic exited with {code.value}')
    finally:
        kernel.CloseHandle(job)  # Terminates only this diagnostic's descendants.
        if process is not None:
            if process.hThread:
                kernel.CloseHandle(process.hThread)
            kernel.CloseHandle(process.hProcess)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=ROOT/'release/FlashOS-Setup.exe')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('The real compiled startup check requires Windows')
    executable = args.exe.resolve(strict=True)
    output = args.report.resolve()
    qa = output.parent.resolve(strict=True)
    if (qa.parent != ROOT/'build' or not qa.name.startswith('installer-compat-') or
            output.name != 'compiled-startup-verification.json' or not qa.is_dir()):
        raise ValueError('Use an existing build/installer-compat-* QA directory')
    target = qa/'FlashOS-Setup-startup-test.exe'
    report = qa/'compiled-startup.json'
    if target.exists() or report.exists() or output.exists():
        raise ValueError('Preserve earlier QA files; choose a fresh compatibility directory')
    probe = production_probe(executable)
    result = copy_for_qa(executable, target)
    run_qa(target, report)
    inner = json.loads(report.read_text(encoding='utf-8'))
    sys.path.insert(0, str(ROOT))
    from installer._release import MANIFEST_SHA256, ENGINE_SCRIPT
    from installer.model import VERSION, SYSTEM_VERSION
    if not (inner.get('ok') is True and inner.get('compiled') is True and
            Path(inner.get('bundle_path', '')).resolve() == target and
            inner.get('manifest_sha256') == MANIFEST_SHA256 and
            inner.get('engine_script_sha256') == hashlib.sha256(ENGINE_SCRIPT.encode('utf-8')).hexdigest() and
            inner.get('installer_version') == VERSION and inner.get('system_version') == SYSTEM_VERSION and
            inner.get('window_visible') is True and inner.get('keyboard_focus') is True and
            inner.get('disk_enumeration') is False and inner.get('disk_writes') is False and
            inner.get('elevation') is False and not inner.get('forbidden_action_attempts') and
            inner.get('inventory_mock_calls', 0) >= 1 and inner.get('tiny_info', {}).get('verified') is True):
        raise ValueError('The actual compiled GUI startup report did not pass')
    with executable.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != result['production_sha256']:
            raise ValueError('Production executable changed during QA')
    result.update(ok=True, production_probe=probe, compiled_startup=inner,
                  production_uac_unchanged=True, physical_disks_accessed=False,
                  windows_version=sys.getwindowsversion().build,
                  limitations=['Windows 10 was not booted; this run uses the current Windows host'])
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
