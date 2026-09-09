import ctypes
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@unittest.skipUnless(os.name == 'nt', 'Windows process and accessibility APIs')
class WindowsWorkerTests(unittest.TestCase):
    def test_job_close_terminates_writer_and_its_child(self):
        from installer.process_group import ProcessGroup
        group = ProcessGroup()
        code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],creationflags=0x08000000); print(p.pid,flush=True); time.sleep(60)"
        parent = group.start([sys.executable, '-u', '-c', code])
        child = int(parent.stdout.readline().strip())
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, child)
        try:
            self.assertTrue(handle)
            self.assertIsNone(parent.poll())
            group.close()
            parent.wait(timeout=5)
            self.assertEqual(0, kernel.WaitForSingleObject(handle, 5000))
        finally:
            group.close()
            parent.stdout.close()
            if handle:
                kernel.CloseHandle(handle)

    def test_compiled_engine_source_matches_reviewed_script(self):
        from installer._release import ENGINE_SCRIPT
        path = Path(__file__).resolve().parents[1] / 'installer/engine.ps1'
        self.assertEqual(path.read_text(encoding='utf-8'), ENGINE_SCRIPT)


if __name__ == '__main__':
    unittest.main()
