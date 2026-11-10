"""Colorimeter link via ArgyllCMS spotread (an external process on a pty).

Rules (see docs/HOW_IT_WORKS.md, "Meter link"):
* spotread reads keys from the terminal, so it runs on a pty, and the pty becomes its
  controlling terminal: if our program dies, the kernel sends spotread SIGHUP.
* The key (space) is sent ONLY after the spotread prompt. A key press during a reading
  may be taken by Argyll as a cancel.
* All raw output goes to the log (log callback).
"""
from __future__ import annotations

import errno
import glob
import os
import re
import select
import signal
import subprocess
import sys
import time

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
RESULT_RE = re.compile(r"Result is XYZ:\s*(" + NUM + r")\s*,?\s*(" + NUM + r")\s*,?\s*(" + NUM + r")")
PROMPT_RE = re.compile(r"(?i)(take a reading|any key to measure|measure it|hit esc or q to exit)")
CALIB_RE = re.compile(r"(?i)(any key to continue|calibration position)")
FAIL_RE = re.compile(r"(?i)(spot read failed|read failed|measurement failed|failed due to|"
                     r"instrument access failed|no instrument|error)")
DIFFUSER_RE = re.compile(r"(?i)(wrong position|diffuser)")

# One-shot launch via Popen: the child makes the pty its controlling terminal and execs.
_TRAMPOLINE = (
    "import os,sys,fcntl,termios\n"
    "try:\n"
    "    fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
    "except OSError:\n"
    "    pass\n"
    "os.execv(sys.argv[1], sys.argv[1:])\n"
)


class MeterError(Exception):
    """Meter error. message — for the user, detail — raw text."""

    def __init__(self, message, detail="", kind="error"):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.kind = kind


def find_spotread(custom=None):
    cands = []
    if custom:
        cands.append(os.path.expanduser(custom))
    cands += ["/opt/homebrew/bin/spotread", "/usr/local/bin/spotread"]
    for pat in ("~/Argyll*/bin/spotread", "/Applications/Argyll*/bin/spotread", "/usr/bin/spotread"):
        cands += sorted(glob.glob(os.path.expanduser(pat)), reverse=True)
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d:
            cands.append(os.path.join(d, "spotread"))
    for c in cands:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def probe_spotread(exe, timeout=25):
    """`spotread -?`: Argyll version and the list of found instruments (port lines of -c)."""
    info = {"exe": exe, "version": None, "instruments": [], "raw": "", "ok": False}
    try:
        r = subprocess.run([exe, "-?"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        out = r.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        info["raw"] = "spotread -? did not answer within %d s" % timeout
        return info
    except OSError as e:
        info["raw"] = str(e)
        return info
    info["raw"] = out[-4000:]
    m = re.search(r"Version\s+([0-9][\w.]*)", out)
    if m:
        info["version"] = m.group(1)
    in_ports = False
    for line in out.splitlines():
        if re.search(r"-c\s+listno", line):
            in_ports = True
            continue
        if in_ports:
            m = re.match(r"\s+(\d+)\s*=\s*'(.*)'", line)
            if m:
                info["instruments"].append(m.group(2))
            elif re.match(r"\s+-", line) or not line.strip():
                in_ports = False
    info["ok"] = True
    return info


def friendly_error(text):
    t = text.lower()
    if DIFFUSER_RE.search(text):
        return "The meter reports a wrong diffuser position. Move the diffuser lever away from the optics and retry."
    if "no instrument" in t or "instrument access failed" in t or "failed to find" in t or "no ports" in t \
            or "open instrument" in t or "usb" in t:
        return ("Meter not found or busy. Check USB (preferably direct, no hub), close Calibrite PROFILER "
                "and DisplayCAL. If the meter is new, run Calibrite PROFILER once (unlocks the HL).")
    return "spotread reported an error. Details are in the meter log."


class ArgyllMeter:
    """Interactive spotread on a pty: one process, a reading per space key."""

    name = "argyll"

    def __init__(self, exe, args, log=None, start_timeout=30.0, read_timeout=60.0):
        self.exe = exe
        self.args = list(args)
        self.log = log or (lambda s: None)
        self.start_timeout = start_timeout
        self.read_timeout = read_timeout
        self.proc = None
        self.fd = None
        self.buf = ""
        self.ready = False

    # ------------------------------------------------------------- process
    def _spawn(self):
        import pty
        import termios
        master, slave = pty.openpty()
        try:
            attrs = termios.tcgetattr(slave)
            attrs[3] &= ~termios.ECHO  # do not echo our key presses
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
        except termios.error:
            pass
        cmd = [sys.executable, "-I", "-c", _TRAMPOLINE, self.exe] + self.args
        self.log("$ " + " ".join([self.exe] + self.args) + "\n")
        try:
            self.proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave,
                                         start_new_session=True, close_fds=True)
        except OSError as e:
            os.close(master)
            os.close(slave)
            raise MeterError("Could not start spotread: %s" % e, kind="spawn")
        os.close(slave)
        self.fd = master
        self.buf = ""

    def _read_chunk(self, timeout):
        """Reads available output. Returns False if the process closed the terminal."""
        if self.fd is None:
            return False
        try:
            r, _, _ = select.select([self.fd], [], [], timeout)
        except (OSError, ValueError):
            return False
        if not r:
            return True
        try:
            data = os.read(self.fd, 4096)
        except OSError as e:
            if e.errno in (errno.EIO, errno.EBADF):
                return False
            raise
        if not data:
            return False
        text = data.decode("utf-8", "replace").replace("\r", "")
        self.buf += text
        self.log(text)
        return True

    def _wait_for(self, patterns, timeout):
        """Waits for the first of the patterns in self.buf. Returns (index, match) or ('eof'|'timeout', None)."""
        deadline = time.monotonic() + timeout
        while True:
            for i, pat in enumerate(patterns):
                m = pat.search(self.buf)
                if m:
                    return i, m
            left = deadline - time.monotonic()
            if left <= 0:
                return "timeout", None
            if not self._read_chunk(min(left, 0.25)):
                # read the remaining tail, if any
                for i, pat in enumerate(patterns):
                    m = pat.search(self.buf)
                    if m:
                        return i, m
                return "eof", None

    def _send(self, s):
        try:
            os.write(self.fd, s.encode())
        except OSError as e:
            raise MeterError("Lost connection to spotread.", str(e), kind="eof")

    # ------------------------------------------------------------- API
    def start(self):
        """Start. Returns 'ready' or 'calibration' (calibration position needed)."""
        self.close()
        self._spawn()
        return self._wait_start()

    def continue_calibration(self):
        self.buf = ""
        self._send(" ")
        return self._wait_start()

    def _wait_start(self):
        i, _ = self._wait_for([CALIB_RE, PROMPT_RE], self.start_timeout)
        if i == 0:
            return "calibration"
        if i == 1:
            self.buf = ""
            self.ready = True
            return "ready"
        tail = self.buf[-1500:]
        self.close()
        if i == "timeout":
            raise MeterError("spotread did not answer within %d s. Reconnect the meter and press “Retry”."
                             % self.start_timeout, tail, kind="timeout")
        raise MeterError(friendly_error(tail), tail, kind="eof")

    def read(self):
        """One reading. Returns (X, Y, Z)."""
        if not self.ready or self.fd is None:
            raise MeterError("Meter is not ready.", kind="notready")
        self.ready = False
        self.buf = ""
        self._send(" ")
        i, m = self._wait_for([RESULT_RE, PROMPT_RE, CALIB_RE], self.read_timeout)
        if i == 0:
            vals = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
            rest = self.buf[m.end():]
            self.buf = rest
            # wait for the next prompt (it comes right after the result)
            j, _ = self._wait_for([PROMPT_RE], 5.0)
            if j == "eof":
                self.close()
            else:
                self.ready = True  # on a prompt timeout we still keep going
            self.buf = ""
            return vals
        if i == 1:
            # a prompt without a result — the reading failed, spotread waits again
            text = self.buf
            self.buf = ""
            self.ready = True
            raise MeterError(friendly_error(text) if FAIL_RE.search(text) else
                             "The reading failed. Retry.", text[-1500:], kind="failed")
        tail = self.buf[-1500:]
        if i == 2:
            self.close()
            raise MeterError("spotread unexpectedly asked for meter calibration. Press “Retry”.",
                             tail, kind="calibration")
        self.close()
        if i == "timeout":
            raise MeterError("The reading did not finish within %d s. The meter is disconnected or hung — check USB "
                             "and press “Retry”." % self.read_timeout, tail, kind="timeout")
        raise MeterError("spotread exited during a reading (meter disconnected?). " + friendly_error(tail),
                         tail, kind="eof")

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def close(self):
        proc, fd = self.proc, self.fd
        self.proc, self.ready = None, False
        if proc is not None:
            if proc.poll() is None and fd is not None:
                try:
                    os.write(fd, b"q")
                except OSError:
                    pass
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        pass
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            self.fd = None


class OneShotMeter(ArgyllMeter):
    """Fallback mode: `spotread -O` per reading (slower, but no pty dialog)."""

    name = "argyll-oneshot"

    def start(self):
        self.ready = True
        return "ready"

    def continue_calibration(self):
        return "ready"

    def read(self):
        saved = self.args
        self.args = saved + ["-O"]
        try:
            self._spawn()
        finally:
            self.args = saved
        i, m = self._wait_for([RESULT_RE, CALIB_RE], self.read_timeout)
        tail = self.buf[-1500:]
        if i == 0:
            vals = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
            super().close()
            self.ready = True
            return vals
        super().close()
        self.ready = True
        if i == 1:
            raise MeterError("The meter asks for calibration — not supported in “one reading per launch” mode. "
                             "Switch the link to “interactive”.", tail, kind="calibration")
        if i == "timeout":
            raise MeterError("The reading did not finish within %d s." % self.read_timeout, tail, kind="timeout")
        raise MeterError(friendly_error(tail), tail, kind="eof")

    def close(self):
        super().close()
        self.ready = False


def build_args(settings):
    """spotread arguments from the settings."""
    args = [settings.get("flag") or "-e"]
    if settings.get("refresh") in ("n", "r"):
        args += ["-Y", settings["refresh"]]
    if settings.get("ccmx"):
        args += ["-X", os.path.expanduser(settings["ccmx"])]
    extra = (settings.get("extraArgs") or "").split()
    return args + extra
