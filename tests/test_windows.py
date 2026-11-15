"""Windows only: the real ConPTY link (conpty.py) against a console program that reads keys like ArgyllCMS."""
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


@unittest.skipUnless(os.name == "nt", "Windows only")
class ConPtyTests(unittest.TestCase):
    def test_available(self):
        from projectorcal import conpty
        self.assertTrue(conpty.available(), "ConPTY needs Windows 10 1809+")

    def test_readings_through_pseudo_console(self):
        from projectorcal.meter import ArgyllMeter
        logs = []
        m = ArgyllMeter(sys.executable, [os.path.join(HERE, "fake_spotread_win.py")], log=logs.append,
                        start_timeout=20, read_timeout=20)
        self.assertEqual(m.start(), "ready")
        vals = [m.read() for _ in range(6)]
        self.assertEqual(vals[0], (47.5235, 50.0, 54.4415))
        self.assertEqual(vals[4], (95.1, 100.2, 108.9))
        self.assertFalse(any("\x1b" in s for s in logs))
        con = m.con
        m.close()
        time.sleep(0.5)
        self.assertIsNotNone(con.poll(), "spotread stand-in still running after close")

    def test_find_spotread_and_paths(self):
        from projectorcal import meter, session
        self.assertEqual(meter.EXE, "spotread.exe")
        self.assertIn("ProjectorCal", session.data_dir())
        self.assertIn("argyllcms.com", meter.install_hint())


if __name__ == "__main__":
    unittest.main()
