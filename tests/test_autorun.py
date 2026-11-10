"""Auto run: synthetic file playback + meter model → the analyzer must recover every patch."""
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from projectorcal import sequence as Q  # noqa: E402
from projectorcal.autorun import AutoRun  # noqa: E402
from projectorcal.catalog import Catalog  # noqa: E402
from projectorcal.mock import MockProjector  # noqa: E402

CAT = Catalog(os.path.join(ROOT, "assets", "patches"))


class Sim:
    """Playback: Play `start` s after "Start"; a pause of stall_len s at stall_at (file seconds)."""

    def __init__(self, seq, start=3.7, stall_at=None, stall_len=0.0, osd_rate=0.0, seed=1, white=40.0,
                 before=None, jitter=0.0, stop_at=None):
        self.seq, self.start = seq, start
        self.stall_at, self.stall_len = stall_at, stall_len
        self.rng = random.Random(seed)
        self.osd_rate = osd_rate
        self.proj = MockProjector()
        self.proj.white_nits = white
        self.cache = {}
        self.before = before or "PLEX_MENU"
        self.jitter = jitter
        self.stop_at = stop_at

    def file_time(self, t):
        ft = t - self.start
        if self.stall_at is not None and ft > self.stall_at:
            ft = max(self.stall_at, ft - self.stall_len)
        return ft

    def xyz_at(self, t):
        ft = self.file_time(t)
        pid = self.before
        if 0 <= ft < self.seq["duration"]:
            for s in self.seq["segments"]:
                if s["start"] <= ft < s["start"] + s["dur"]:
                    pid = s["patchId"]
                    break
        elif ft >= self.seq["duration"]:
            pid = "PLEX_MENU"
        if self.stop_at is not None and ft >= self.stop_at:
            pid = "PLEX_MENU"
        if pid not in self.cache:
            self.cache[pid] = self.proj.xyz_for(CAT.by_id.get(pid))
        return self.cache[pid]

    def readings(self, until):
        t = 0.0
        out = []
        while t < until:
            # realistic reading time (from runs on a Vidda): decided by the picture at the reading start
            dur = Q.read_time_xyz(self.xyz_at(t)) * (1 + self.jitter * self.rng.uniform(-1, 1))
            samples = [self.xyz_at(t + dur * (k + 0.5) / 20) for k in range(20)]
            v = tuple(sum(s[i] for s in samples) / 20 for i in range(3))
            k = 1 + self.rng.gauss(0, 0.003)
            v = tuple(c * k for c in v)
            if self.rng.random() < self.osd_rate:
                v = tuple(c * 0.6 + 2.0 for c in v)       # the projector menu reached the meter
            out.append((t, t + dur, v))
            t += dur + 0.05
        return out


def run(sim):
    ar = AutoRun(sim.seq, expected=Q.expected_rel(CAT, sim.seq), expected_xy=Q.expected_xy(CAT, sim.seq))
    for r in sim.readings(min(sim.seq["duration"] + sim.start + (sim.stall_len or 0) + 12, 900)):
        ar.feed(*r)
    return ar, ar.analyze()


class AutoRunTests(unittest.TestCase):
    def check(self, sim, ar, res, tol=0.01, allow_missing=0):
        self.assertTrue(res["ok"], res)
        bad, missing = [], []
        for sg in res["segments"]:
            if sg["role"] != "patch":
                continue
            true = sim.proj.xyz_for(CAT.get(sg["patchId"]))
            if not sg["XYZ"] or "implausible" in sg["flags"]:
                missing.append((sg["patchId"], "none", sg["flags"]))
                continue
            err = abs(sg["XYZ"][1] / true[1] - 1)
            if err > tol:
                bad.append((sg["patchId"], round(err * 100, 1), sg["n"]))
        self.assertEqual(bad, [])
        self.assertLessEqual(len(missing), allow_missing, missing)
        return res

    def test_sdr_normal(self):
        sim = Sim(Q.build(CAT, "sdr"), start=3.7)
        ar, res = run(sim)
        self.check(sim, ar, res)
        self.assertTrue(res["sync"]["endMarker"])
        self.assertLess(abs(res["sync"]["maxShift"]), 1.0)     # transition estimate jitter — up to half a reading
        self.assertTrue(all(sg["n"] >= 1 for sg in res["segments"] if sg["role"] == "patch"))
        self.assertGreaterEqual(len(res["white"]), 4)            # white drift checks

    def test_sdr_start_late_with_plex_ui(self):
        sim = Sim(Q.build(CAT, "sdr"), start=9.3)                # the Plex menu is on screen before Play
        ar, res = run(sim)
        self.check(sim, ar, res)

    def test_sdr_stall_in_middle(self):
        """Plex stalled for 2.5 s in the middle of the file — correction by transitions."""
        sim = Sim(Q.build(CAT, "sdr"), start=2.0, stall_at=150.3, stall_len=2.5)
        ar, res = run(sim)
        self.check(sim, ar, res)
        self.assertGreater(res["sync"]["maxShift"], 2.0)
        self.assertTrue(res["sync"]["endMarker"])

    def test_osd_spikes_rejected(self):
        sim = Sim(Q.build(CAT, "sdr"), start=4.0, osd_rate=0.04, seed=3)
        ar, res = run(sim)
        self.check(sim, ar, res, tol=0.015, allow_missing=2)   # misses are flagged, no wrong values

    def test_hdr(self):
        sim = Sim(Q.build(CAT, "hdr"), start=5.0)
        ar, res = run(sim)
        self.check(sim, ar, res)

    def test_play_pressed_before_start(self):
        """Play pressed 5 s before "Start" in the program — the intro is still playing, the mark is caught."""
        sim = Sim(Q.build(CAT, "sdr"), start=-5.0)
        ar, res = run(sim)
        self.check(sim, ar, res)

    def test_long_stall_and_jitter(self):
        sim = Sim(Q.build(CAT, "sdr"), start=3.0, stall_at=97.6, stall_len=5.0, jitter=0.4, seed=7)
        ar, res = run(sim)
        self.check(sim, ar, res)
        self.assertGreater(res["sync"]["maxShift"], 4.5)

    def test_stopped_in_middle(self):
        """Video stopped in the middle: what was captured is correct, the rest is flagged "no readings"."""
        sim = Sim(Q.build(CAT, "sdr"), start=2.0, stop_at=200)
        ar, res = run(sim)
        self.assertTrue(res["ok"])
        self.assertFalse(res["sync"]["endMarker"])
        done = [sg for sg in res["segments"] if sg["role"] == "patch" and sg["start"] + sg["dur"] < 199]
        late = [sg for sg in res["segments"] if sg["role"] == "patch" and sg["start"] > 202]
        for sg in done:
            true = sim.proj.xyz_for(CAT.get(sg["patchId"]))
            self.assertLess(abs(sg["XYZ"][1] / true[1] - 1), 0.01, sg)
        wrong = [sg["patchId"] for sg in late if sg["XYZ"] and "implausible" not in sg["flags"] and
                 abs(sg["XYZ"][1] / sim.proj.xyz_for(CAT.get(sg["patchId"]))[1] - 1) > 0.01]
        self.assertEqual(wrong, [])

    def test_no_video(self):
        sim = Sim(Q.build(CAT, "sdr"), start=10 ** 6)
        ar, res = run(sim)
        self.assertFalse(res["ok"])
        self.assertIn("mark", res["error"])

    def test_bright_projector(self):
        sim = Sim(Q.build(CAT, "sdr"), start=1.0, white=140.0)
        ar, res = run(sim)
        self.check(sim, ar, res)


class SequenceFiles(unittest.TestCase):
    def test_video_files_match_schedule(self):
        """The built videos (assets/patches/AUTO) must match the schedule in the program."""
        import json
        for mode in ("sdr", "hdr"):
            path = os.path.join(CAT.dir, "AUTO", "sequence_%s.json" % mode)
            self.assertTrue(os.path.exists(path), "no %s — run tools/make_sequence.py" % path)
            with open(path, encoding="utf-8") as f:
                built = json.load(f)
            self.assertEqual(built, json.loads(json.dumps(Q.build(CAT, mode))),
                             "video is outdated — rebuild with tools/make_sequence.py")
            self.assertTrue(os.path.exists(os.path.join(CAT.dir, "AUTO", built["file"])))


class AutoRunRandomized(unittest.TestCase):
    def test_no_silent_errors_harsh(self):
        """Random harsh conditions: Plex pauses, menu in the meter, reading spread, different brightness.
        Flagged misses are allowed; a wrong value without a flag is not."""
        from projectorcal.autorun import reliable
        import random as R
        silent, total = 0, 0
        for seed in range(100, 130):
            r = R.Random(seed)
            mode = r.choice(["sdr", "sdr", "hdr"])
            sim = Sim(Q.build(CAT, mode), start=r.uniform(-5, 8), stall_at=r.choice([None, r.uniform(30, 150)]),
                      stall_len=r.uniform(0.5, 6), osd_rate=r.choice([0, 0.02, 0.05]), jitter=r.uniform(0, 0.5),
                      seed=seed, white=r.choice([25, 40, 80, 150]))
            ar, res = run(sim)
            if not res["ok"]:
                continue
            for sg in res["segments"]:
                if sg["role"] == "patch" and reliable(sg):
                    total += 1
                    t = sim.proj.xyz_for(CAT.get(sg["patchId"]))[1]
                    if abs(sg["XYZ"][1] / t - 1) > 0.015:
                        silent += 1
        self.assertGreater(total, 1000)
        self.assertEqual(silent, 0)


if __name__ == "__main__":
    unittest.main()
