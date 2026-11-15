"""Integration tests of MeterService against spotread stubs (via a pty)."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from projectorcal.meter import (ArgyllMeter, MeterError, OneShotMeter, RESULT_RE,  # noqa: E402
                                probe_spotread)

FAKE = os.path.join(HERE, "fake_spotread")
FAKE_EXT = os.path.join(HERE, "fake_spotread_ext")


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a zombie on Linux counts as dead
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return True


class EnvMixin:
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = dict(os.environ)
        for k in list(os.environ):
            if k.startswith("FAKE_") or k == "STUB_SEQ":
                del os.environ[k]
        seq = os.path.join(self.tmp, "seq")
        with open(seq, "w") as f:
            f.write("47.5235 50.0 54.4415\n95.1 100.2 108.9\n1.0e-02 1.1e-02 1.2e-02\n")
        os.environ["STUB_SEQ"] = seq
        self.logs = []

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)

    def meter(self, exe=FAKE_EXT, **kw):
        return ArgyllMeter(exe, ["-e"], log=self.logs.append, **kw)


class ParserTests(unittest.TestCase):
    def test_result_regex_variants(self):
        for line, exp in [
            (" Result is XYZ: 47.523500 50.000000 54.441500, D50 Lab is 50.0 0.0 0.0", (47.5235, 50.0, 54.4415)),
            (" Result is XYZ: 95.1 100.2 108.9, Yxy: 100.2 0.3127 0.3290", (95.1, 100.2, 108.9)),
            ("Result is XYZ: 1.0e-02 1.1e-02 1.2e-02\n", (0.01, 0.011, 0.012)),
            ("Result is XYZ: 0.012345, 0.013456, 0.014567\n", (0.012345, 0.013456, 0.014567)),
        ]:
            m = RESULT_RE.search(line)
            self.assertIsNotNone(m, line)
            self.assertEqual(tuple(float(g) for g in m.groups()), exp)


class ArgyllMeterTests(EnvMixin, unittest.TestCase):
    def test_original_fake_spotread(self):
        m = self.meter(FAKE)
        self.assertEqual(m.start(), "ready")
        v = m.read()
        self.assertEqual(v, (47.5235, 50.0, 54.4415))
        v = m.read()
        self.assertEqual(v, (95.1, 100.2, 108.9))
        m.close()

    def test_twenty_readings(self):
        m = self.meter()
        m.start()
        t0 = time.monotonic()
        vals = [m.read() for _ in range(20)]
        self.assertEqual(vals[-1], (0.01, 0.011, 0.012))
        self.assertLess(time.monotonic() - t0, 10)
        pid = m.proc.pid
        m.close()
        time.sleep(0.2)
        self.assertFalse(pid_alive(pid))
        self.assertTrue(any("Result is XYZ" in s for s in self.logs))

    def test_calibration_prompt(self):
        os.environ["FAKE_CALIB"] = "1"
        m = self.meter()
        self.assertEqual(m.start(), "calibration")
        self.assertEqual(m.continue_calibration(), "ready")
        self.assertEqual(m.read()[1], 50.0)
        m.close()

    def test_no_instrument(self):
        os.environ["FAKE_NOINST"] = "1"
        m = self.meter()
        with self.assertRaises(MeterError) as cm:
            m.start()
        self.assertIn("Meter not found", cm.exception.message)
        self.assertIn("Instrument Access Failed", cm.exception.detail)

    def test_failed_reading_recovers(self):
        os.environ["FAKE_FAIL_AT"] = "2"
        m = self.meter()
        m.start()
        m.read()
        with self.assertRaises(MeterError) as cm:
            m.read()
        self.assertEqual(cm.exception.kind, "failed")
        self.assertTrue(m.alive)
        self.assertEqual(m.read()[1], 0.011)  # the third reading — the third line
        m.close()

    def test_disconnect_mid_reading(self):
        os.environ["FAKE_EXIT_AT"] = "2"
        m = self.meter()
        m.start()
        m.read()
        with self.assertRaises(MeterError) as cm:
            m.read()
        self.assertEqual(cm.exception.kind, "eof")
        self.assertFalse(m.alive)
        # "Retry" after reconnecting
        del os.environ["FAKE_EXIT_AT"]
        self.assertEqual(m.start(), "ready")
        self.assertEqual(m.read()[1], 50.0)
        m.close()

    def test_hang_timeout_kills_process(self):
        os.environ["FAKE_HANG_AT"] = "1"
        m = self.meter(read_timeout=1.5)
        m.start()
        pid = m.proc.pid
        t0 = time.monotonic()
        with self.assertRaises(MeterError) as cm:
            m.read()
        self.assertEqual(cm.exception.kind, "timeout")
        self.assertLess(time.monotonic() - t0, 8)
        time.sleep(0.2)
        self.assertFalse(pid_alive(pid))

    def test_start_timeout(self):
        script = os.path.join(self.tmp, "silent")
        with open(script, "w") as f:
            f.write("#!/bin/sh\nsleep 60\n")
        os.chmod(script, 0o755)
        m = self.meter(script, start_timeout=1.0)
        with self.assertRaises(MeterError) as cm:
            m.start()
        self.assertEqual(cm.exception.kind, "timeout")

    def test_oneshot(self):
        # the stub does not know -O but prints the prompt and waits for a key: one-shot waits for a result → timeout.
        script = os.path.join(self.tmp, "oneshot")
        with open(script, "w") as f:
            f.write("#!/bin/sh\necho ' Result is XYZ: 1 2 3, Yxy: 2 0.1 0.2'\n")
        os.chmod(script, 0o755)
        m = OneShotMeter(script, ["-e"], log=self.logs.append)
        self.assertEqual(m.start(), "ready")
        self.assertEqual(m.read(), (1.0, 2.0, 3.0))
        self.assertEqual(m.read(), (1.0, 2.0, 3.0))
        m.close()

    def test_orphan_killed_when_parent_dies(self):
        """If the program crashed (SIGKILL), spotread must not keep hanging (SIGHUP from the pty)."""
        pidfile = os.path.join(self.tmp, "pid")
        code = ("import sys,time; sys.path.insert(0,%r)\n"
                "from projectorcal.meter import ArgyllMeter\n"
                "m=ArgyllMeter(%r,['-e']); m.start(); print('ok',flush=True); time.sleep(60)\n") % (ROOT, FAKE_EXT)
        env = dict(os.environ, FAKE_PIDFILE=pidfile)
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, env=env)
        self.assertEqual(p.stdout.readline().strip(), b"ok")
        with open(pidfile) as f:
            child = int(f.read())
        self.assertTrue(pid_alive(child))
        p.send_signal(signal.SIGKILL)
        p.wait()
        p.stdout.close()
        for _ in range(30):
            if not pid_alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(child))


class FakeConPty:
    """Stand-in for conpty.ConPtyProcess on Linux: the stub runs on a pty, and its output is delivered the way a
    Windows pseudo console does it — VT sequences around the text, CRLF, arbitrary chunk boundaries (even inside an
    escape sequence)."""

    def __init__(self, argv):
        import pty
        import random
        import select as sel
        self._sel, self.rng = sel, random.Random(7)
        master, slave = pty.openpty()
        self.p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        self.fd, self.pid, self.pending = master, self.p.pid, b""

    def read(self, timeout):
        if not self.pending:
            r, _, _ = self._sel.select([self.fd], [], [], timeout)
            if not r:
                return b""
            try:
                data = os.read(self.fd, 4096)
            except OSError:
                return None
            if not data:
                return None
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\x1b[K\r\n")
            self.pending = b"\x1b[?25l" + data + b"\x1b]0;spotread\x07\x1b[?25h"
        n = self.rng.randint(1, 9)
        out, self.pending = self.pending[:n], self.pending[n:]
        return out

    def write(self, data):
        os.write(self.fd, data)

    def poll(self):
        return self.p.poll()

    def wait(self, timeout):
        return self.p.wait(timeout)

    def terminate(self):
        if self.p.poll() is None:
            self.p.kill()

    def close(self):
        self.terminate()
        self.p.wait(2)
        os.close(self.fd)


class WindowsPathTests(EnvMixin, unittest.TestCase):
    """The Windows code path of ArgyllMeter (pseudo console + VT cleaning) against the stubs."""

    def setUp(self):
        super().setUp()
        from projectorcal import conpty, meter
        self._saved = (meter.IS_WIN, conpty.ConPtyProcess)
        meter.IS_WIN, conpty.ConPtyProcess = True, FakeConPty

    def tearDown(self):
        from projectorcal import conpty, meter
        meter.IS_WIN, conpty.ConPtyProcess = self._saved
        super().tearDown()

    def test_readings_calibration_failure(self):
        os.environ["FAKE_CALIB"] = "1"
        os.environ["FAKE_FAIL_AT"] = "3"
        m = self.meter()
        self.assertEqual(m.start(), "calibration")
        self.assertEqual(m.continue_calibration(), "ready")
        self.assertEqual(m.read(), (47.5235, 50.0, 54.4415))
        self.assertEqual(m.read(), (95.1, 100.2, 108.9))
        with self.assertRaises(MeterError) as cm:
            m.read()
        self.assertEqual(cm.exception.kind, "failed")
        vals = [m.read() for _ in range(10)]
        self.assertTrue(all(len(v) == 3 for v in vals))
        self.assertFalse(any("\x1b" in x for x in self.logs))
        m.close()
        self.assertFalse(m.alive)

    def test_disconnect_and_oneshot(self):
        os.environ["FAKE_EXIT_AT"] = "2"
        m = self.meter()
        m.start()
        m.read()
        with self.assertRaises(MeterError) as cm:
            m.read()
        self.assertEqual(cm.exception.kind, "eof")
        script = os.path.join(self.tmp, "oneshot")
        with open(script, "w") as f:
            f.write("#!/bin/sh\necho ' Result is XYZ: 1 2 3, Yxy: 2 0.1 0.2'\n")
        os.chmod(script, 0o755)
        o = OneShotMeter(script, ["-e"], log=self.logs.append)
        o.start()
        self.assertEqual(o.read(), (1.0, 2.0, 3.0))
        self.assertEqual(o.read(), (1.0, 2.0, 3.0))
        o.close()

    def test_find_spotread_windows_folder(self):
        from projectorcal import meter
        saved = meter.EXE
        meter.EXE = "spotread.exe"
        try:
            d = os.path.join(self.tmp, "Argyll_V3.3.0", "bin")
            os.makedirs(d)
            exe = os.path.join(d, "spotread.exe")
            with open(exe, "w") as f:
                f.write("")
            os.chmod(exe, 0o755)
            self.assertEqual(meter.find_spotread(os.path.join(self.tmp, "Argyll_V3.3.0")), exe)  # folder is enough
        finally:
            meter.EXE = saved


class ProbeTests(EnvMixin, unittest.TestCase):
    def test_probe_parses_usage(self):
        script = os.path.join(self.tmp, "sr")
        with open(script, "w") as f:
            f.write("#!/bin/sh\ncat >&2 <<'X'\nRead Print Spot values, Version 3.3.0\n"
                    "Author: Graeme W. Gill, licensed under the AGPL Version 3\n"
                    "usage: spotread [-options] [logfile]\n -v                Verbose mode\n"
                    " -c listno         Set communication port from the following list (default 1)\n"
                    "    1 = 'usb:/bus2/dev3/ (Calibrite Display Plus HL)'\n"
                    " -t                Use transmission measurement mode\nX\nexit 1\n")
        os.chmod(script, 0o755)
        info = probe_spotread(script)
        self.assertEqual(info["version"], "3.3.0")
        self.assertEqual(info["instruments"], ["usb:/bus2/dev3/ (Calibrite Display Plus HL)"])


if __name__ == "__main__":
    unittest.main()
