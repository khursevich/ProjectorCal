"""Windows: run a console program (spotread.exe) on a pseudo console (ConPTY, Windows 10 1809+), stdlib only.

ArgyllCMS reads keys from the *console* (`_getch`), not from stdin, so a plain pipe cannot press its keys. ConPTY
gives the child a real console whose input we write to and whose output we read. The child is put in a job object
with KILL_ON_JOB_CLOSE: if ProjectorCal dies, Windows ends spotread too (the Unix version relies on SIGHUP).

ConPTY renders a screen, so its output carries VT escape sequences; `clean()` strips them (a cursor jump becomes a
line break, so a result line is never glued to the previous text).
"""
from __future__ import annotations

import queue
import re
import subprocess
import threading

_VT = re.compile(r"\x1b\[[0-9;?]*([A-Za-z@`])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[=>78DEMc]")


def clean(text):
    """VT sequences → plain text: cursor positioning / line erase become line breaks, everything else is dropped."""
    def rep(m):
        return "\n" if m.group(1) in ("H", "f", "J") else ""
    return _VT.sub(rep, text)


class VTCleaner:
    """clean() for a stream: an escape sequence split between two reads is held back until it is complete."""

    def __init__(self):
        self.pending = ""

    def feed(self, text):
        text = self.pending + text
        self.pending = ""
        k = text.rfind("\x1b")
        if k >= 0 and len(text) - k < 64 and _VT.match(text[k:]) is None:
            self.pending, text = text[k:], text[:k]          # incomplete: wait for the rest
        return clean(text)


def available():
    try:
        import ctypes
        return hasattr(ctypes, "WinDLL") and hasattr(ctypes.WinDLL("kernel32"), "CreatePseudoConsole")
    except (OSError, AttributeError, ImportError):
        return False


class ConPtyProcess:
    """A child on a pseudo console. read(timeout) → bytes | b"" (nothing yet) | None (closed)."""

    def __init__(self, argv, cols=250, rows=50):
        import ctypes
        from ctypes import wintypes as W
        self._ct, self._W = ctypes, W
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32 = k32

        class COORD(ctypes.Structure):
            _fields_ = [("X", W.SHORT), ("Y", W.SHORT)]

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR), ("lpTitle", W.LPWSTR),
                        ("dwX", W.DWORD), ("dwY", W.DWORD), ("dwXSize", W.DWORD), ("dwYSize", W.DWORD),
                        ("dwXCountChars", W.DWORD), ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD),
                        ("dwFlags", W.DWORD), ("wShowWindow", W.WORD), ("cbReserved2", W.WORD),
                        ("lpReserved2", ctypes.c_void_p), ("hStdInput", W.HANDLE), ("hStdOutput", W.HANDLE),
                        ("hStdError", W.HANDLE)]

        class STARTUPINFOEXW(ctypes.Structure):
            _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE), ("dwProcessId", W.DWORD),
                        ("dwThreadId", W.DWORD)]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", W.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", W.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", W.DWORD), ("SchedulingClass", W.DWORD)]

        class EXT_LIMIT(ctypes.Structure):
            _fields_ = [("Basic", BASIC_LIMIT), ("Io", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        H = W.HANDLE
        k32.CreatePipe.argtypes = [ctypes.POINTER(H), ctypes.POINTER(H), ctypes.c_void_p, W.DWORD]
        k32.CreatePseudoConsole.argtypes = [COORD, H, H, W.DWORD, ctypes.POINTER(H)]
        k32.CreatePseudoConsole.restype = ctypes.c_long
        k32.ClosePseudoConsole.argtypes = [H]
        k32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, W.DWORD, W.DWORD,
                                                          ctypes.POINTER(ctypes.c_size_t)]
        k32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, W.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                                  ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        k32.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL, W.DWORD,
                                       ctypes.c_void_p, W.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
        k32.ReadFile.argtypes = [H, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p]
        k32.WriteFile.argtypes = [H, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p]
        k32.CloseHandle.argtypes = [H]
        k32.WaitForSingleObject.argtypes = [H, W.DWORD]
        k32.WaitForSingleObject.restype = W.DWORD
        k32.GetExitCodeProcess.argtypes = [H, ctypes.POINTER(W.DWORD)]
        k32.TerminateProcess.argtypes = [H, ctypes.c_uint]
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
        k32.CreateJobObjectW.restype = H
        k32.SetInformationJobObject.argtypes = [H, ctypes.c_int, ctypes.c_void_p, W.DWORD]
        k32.AssignProcessToJobObject.argtypes = [H, H]
        k32.ResumeThread.argtypes = [H]

        def check(ok, what):
            if not ok:
                raise OSError("%s failed (Windows error %d)" % (what, ctypes.get_last_error()))

        in_r, in_w, out_r, out_w = H(), H(), H(), H()
        check(k32.CreatePipe(ctypes.byref(in_r), ctypes.byref(in_w), None, 0), "CreatePipe")
        check(k32.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w), None, 0), "CreatePipe")
        hpc = H()
        hr = k32.CreatePseudoConsole(COORD(cols, rows), in_r, out_w, 0, ctypes.byref(hpc))
        if hr != 0:
            for h in (in_r, in_w, out_r, out_w):
                k32.CloseHandle(h)
            raise OSError("CreatePseudoConsole failed (0x%08x) — Windows 10 version 1809 or newer is needed" % (hr & 0xffffffff))
        size = ctypes.c_size_t(0)
        k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attrs = ctypes.create_string_buffer(size.value)
        check(k32.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)), "InitializeProcThreadAttributeList")
        PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
        check(k32.UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, hpc,
                                            ctypes.sizeof(H), None, None), "UpdateProcThreadAttribute")
        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        si.StartupInfo.dwFlags = 0x100          # STARTF_USESTDHANDLES with no handles: the child uses the pseudo
        si.lpAttributeList = ctypes.addressof(attrs)   # console even if our own stdio is redirected
        pi = PROCESS_INFORMATION()
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        EXTENDED_STARTUPINFO_PRESENT, CREATE_SUSPENDED = 0x00080000, 0x00000004
        ok = k32.CreateProcessW(None, cmdline, None, None, False, EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED,
                                None, None, ctypes.byref(si), ctypes.byref(pi))
        err = ctypes.get_last_error()
        k32.DeleteProcThreadAttributeList(attrs)
        k32.CloseHandle(in_r)                    # the pseudo console holds its own copies
        k32.CloseHandle(out_w)
        if not ok:
            k32.ClosePseudoConsole(hpc)
            k32.CloseHandle(in_w)
            k32.CloseHandle(out_r)
            raise OSError("Could not start %s (Windows error %d)" % (argv[0], err))
        # job object: the child dies with us
        job = k32.CreateJobObjectW(None, None)
        if job:
            info = EXT_LIMIT()
            info.Basic.LimitFlags = 0x2000           # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            k32.AssignProcessToJobObject(job, pi.hProcess)
        k32.ResumeThread(pi.hThread)
        k32.CloseHandle(pi.hThread)
        self.pid = pi.dwProcessId
        self._hproc, self._hpc, self._in, self._out, self._job = pi.hProcess, hpc, in_w, out_r, job
        self._q = queue.Queue()
        self._closed = False
        self._eof = False
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        ct, W = self._ct, self._W
        buf = ct.create_string_buffer(4096)
        n = W.DWORD(0)
        while True:
            ok = self._k32.ReadFile(self._out, buf, 4096, ct.byref(n), None)
            if not ok or n.value == 0:
                self._q.put(None)
                return
            self._q.put(buf.raw[:n.value])

    def read(self, timeout):
        if self._eof:
            return None
        try:
            data = self._q.get(timeout=timeout)
        except queue.Empty:
            if self.poll() is not None and self._q.empty():
                # the child is gone; ConPTY keeps the pipe open until it is closed — report the end
                self._eof = True
                return None
            return b""
        if data is None:
            self._eof = True
        return data

    def write(self, data):
        n = self._W.DWORD(0)
        if self._closed or not self._k32.WriteFile(self._in, data, len(data), self._ct.byref(n), None):
            raise OSError("write to the pseudo console failed")

    def poll(self):
        if self._hproc is None:
            return -1
        if self._k32.WaitForSingleObject(self._hproc, 0) != 0:       # WAIT_OBJECT_0
            return None
        code = self._W.DWORD(0)
        self._k32.GetExitCodeProcess(self._hproc, self._ct.byref(code))
        return code.value

    def wait(self, timeout):
        if self._hproc is not None and self._k32.WaitForSingleObject(self._hproc, int(timeout * 1000)) != 0:
            raise subprocess.TimeoutExpired("spotread", timeout)
        return self.poll()

    def terminate(self):
        if self._hproc is not None and self.poll() is None:
            self._k32.TerminateProcess(self._hproc, 1)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.terminate()
        k = self._k32
        hpc, out = self._hpc, self._out

        def shut():                 # may block until its output is drained: the reader thread keeps reading
            k.ClosePseudoConsole(hpc)
            k.CloseHandle(out)
        threading.Thread(target=shut, daemon=True).start()
        for h in (self._in, self._hproc, self._job):
            if h:
                k.CloseHandle(h)
        self._hproc = None
