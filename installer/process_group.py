"""Windows job: the writer starts suspended and inherits crash cleanup for children."""
import ctypes
from ctypes import wintypes as W
import subprocess
import threading


class BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64), ('flags', W.DWORD),
                ('minimum_working_set', ctypes.c_size_t), ('maximum_working_set', ctypes.c_size_t),
                ('active_processes', W.DWORD), ('affinity', ctypes.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]


class IOCounts(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', BasicLimits), ('io', IOCounts), ('process_memory', ctypes.c_size_t),
                ('job_memory', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


class ThreadEntry(ctypes.Structure):
    _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('thread_id', W.DWORD), ('owner_pid', W.DWORD),
                ('base_priority', W.LONG), ('delta_priority', W.LONG), ('flags', W.DWORD)]


class ProcessGroup:
    def __init__(self):
        self.lock = threading.Lock()
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.restype = W.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
        self.kernel.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
        self.kernel.Thread32First.argtypes = [W.HANDLE, ctypes.POINTER(ThreadEntry)]
        self.kernel.Thread32Next.argtypes = [W.HANDLE, ctypes.POINTER(ThreadEntry)]
        self.kernel.OpenThread.restype = W.HANDLE
        self.kernel.OpenThread.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.kernel.ResumeThread.argtypes = [W.HANDLE]
        self.kernel.ResumeThread.restype = W.DWORD
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no child breakaway.
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def start(self, command):
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW | 0x00000004)  # CREATE_SUSPENDED
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, W.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
            snapshot = self.kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
            if snapshot == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                entry = ThreadEntry()
                entry.size = ctypes.sizeof(entry)
                found = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
                while found:
                    if entry.owner_pid == process.pid:
                        thread = self.kernel.OpenThread(2, False, entry.thread_id)  # THREAD_SUSPEND_RESUME
                        if not thread:
                            raise ctypes.WinError(ctypes.get_last_error())
                        try:
                            if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
                                raise ctypes.WinError(ctypes.get_last_error())
                        finally:
                            self.kernel.CloseHandle(thread)
                        return process
                    found = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
                raise RuntimeError('Не удалось запустить защищённый процесс записи.')
            finally:
                self.kernel.CloseHandle(snapshot)
        except Exception:
            process.kill()
            process.wait()
            raise

    def close(self):
        with self.lock:
            if self.handle:
                self.kernel.CloseHandle(self.handle)
                self.handle = None
