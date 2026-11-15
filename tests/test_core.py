"""Unit tests: colorimetry (vectors), catalog, stability detector, hints, patch check."""
import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from projectorcal import analysis as A  # noqa: E402
from projectorcal import colorimetry as C  # noqa: E402
from projectorcal.catalog import Catalog, pass_patches  # noqa: E402
from projectorcal.session import new_session  # noqa: E402
from projectorcal.stability import StabilityDetector  # noqa: E402

VEC = json.load(open(os.path.join(HERE, "vectors.json"), encoding="utf-8"))
CAT = Catalog(os.path.join(ROOT, "assets", "patches"))


def gray_xyz(Y):
    return C.scale(C.D65, Y)


class ColorimetryVectors(unittest.TestCase):
    def test_de2000_sharma(self):
        for v in VEC["de2000_sharma"]:
            self.assertAlmostEqual(C.de2000(v["lab1"], v["lab2"]), v["expected"], places=4)

    def test_de2000_symmetric_and_zero(self):
        a, b = (50, 2.6772, -79.7751), (50, 0, -82.7485)
        self.assertAlmostEqual(C.de2000(a, b), C.de2000(b, a), places=10)
        self.assertEqual(C.de2000(a, a), 0)

    def test_rgb_balance(self):
        for key in ("rgb_balance_neutral", "rgb_balance_tint"):
            got = C.rgb_balance(VEC[key]["XYZ"])
            for g, e in zip(got, VEC[key]["expected"]):
                self.assertAlmostEqual(g, e, delta=0.001)

    def test_cct(self):
        self.assertAlmostEqual(C.cct(VEC["cct_d65"]["XYZ"]), VEC["cct_d65"]["computed"], delta=0.1)

    def test_pq(self):
        for v in VEC["pq"]:
            self.assertAlmostEqual(C.pq_eotf(v["e"]), v["nits"], delta=0.001 * max(1, v["nits"]))

    def test_bt1886(self):
        v = VEC["bt1886"]
        self.assertAlmostEqual(C.bt1886(v["V"], v["Lw"], v["Lb"]), v["nits"], places=3)

    def test_neutral_properties(self):
        for Y in (0.05, 1, 50, 300):
            bal = C.rgb_balance(gray_xyz(Y))
            for b in bal:
                self.assertAlmostEqual(b, 100, places=3)
            self.assertAlmostEqual(C.duv_vs_d65(gray_xyz(Y)), 0, places=9)
            self.assertAlmostEqual(C.gray_de_color(gray_xyz(Y), 100), 0, places=9)

    def test_inverse_matrix(self):
        v = (0.3, 0.6, 0.1)
        back = C.matvec(C.M709_INV, C.matvec(C.M709, v))
        for a, b in zip(v, back):
            self.assertAlmostEqual(a, b, places=12)

    def test_gamma_eff(self):
        self.assertAlmostEqual(C.gamma_eff(100 * 0.5 ** 2.4, 100, 0.5), 2.4, places=9)
        self.assertIsNone(C.gamma_eff(10, 100, 1.0))

    def test_color_target_matches_catalog(self):
        for pid in CAT.sdr_colors(sweeps=True):
            p = CAT.get(pid)
            t = C.color_target_XYZ(p["rgb"], 1.0, "2.4")
            x, y = C.xy(t)
            self.assertAlmostEqual(x, p["tx"], places=6)
            self.assertAlmostEqual(y, p["ty"], places=6)
            self.assertAlmostEqual(t[1], p["tY"], places=6)

    def test_matches_reference_pcal_numpy(self):
        """Comparison with the pcal.py reference (if numpy is available)."""
        try:
            import numpy as np  # noqa: F401
        except ImportError:
            self.skipTest("no numpy")
        import importlib.util
        import types
        sys.modules.setdefault("pexpect", types.ModuleType("pexpect"))
        spec = importlib.util.spec_from_file_location("pcal", os.path.join(ROOT, "reference", "pcal.py"))
        pcal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pcal)
        import random
        rnd = random.Random(1)
        for _ in range(200):
            XYZ = tuple(rnd.uniform(0.01, 200) for _ in range(3))
            w = tuple(rnd.uniform(50, 200) * k for k in C.D65)
            arr = np.array(XYZ)
            self.assertAlmostEqual(C.cct(XYZ), pcal.cct(arr), places=6)
            self.assertAlmostEqual(C.duv_vs_d65(XYZ), pcal.duv_vs_d65(arr), places=9)
            for a, b in zip(C.rgb_balance(XYZ), pcal.rgb_balance(arr)):
                self.assertAlmostEqual(a, b, places=6)
            la, lb = C.lab(XYZ, w), pcal.lab(arr, np.array(w))
            for a, b in zip(la, lb):
                self.assertAlmostEqual(a, b, places=6)
            l2 = C.lab(tuple(c * 1.1 for c in XYZ), w)
            self.assertAlmostEqual(C.de2000(la, l2), pcal.de2000(lb, np.array(l2)), places=6)
        for e in (0.0, 0.05, 0.3, 0.5, 0.75, 1.0):
            self.assertAlmostEqual(C.pq_eotf(e), pcal.pq_eotf(e), places=6)


class CatalogTests(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(len(CAT.patches), 74)
        self.assertEqual(len(CAT.sdr_gray()), 21)
        self.assertEqual(len(CAT.sdr_colors()), 6)
        self.assertEqual(len(CAT.sdr_colors(sweeps=True)), 30)
        self.assertEqual(len(CAT.hdr_gray()), 21)

    def test_files_exist(self):
        if not os.path.isdir(os.path.join(CAT.dir, "SDR")):
            self.skipTest("video patches not installed (they are distributed via Releases, not git)")
        for p in CAT.patches:
            self.assertTrue(os.path.exists(os.path.join(CAT.dir, p["file"])), p["file"])

    def test_pass_sets(self):
        self.assertEqual(pass_patches(CAT, "verify", {"quick": True}),
                         ["SDR_01", "SDR_07", "SDR_11", "SDR_15", "SDR_17", "SDR_19"])
        self.assertEqual(pass_patches(CAT, "gray", {}), ["SDR_01", "SDR_05", "SDR_09", "SDR_13", "SDR_17", "SDR_19"])
        self.assertEqual(len(pass_patches(CAT, "gray", {"full": True})), 21)
        self.assertEqual(pass_patches(CAT, "hdr", {}),
                         ["HDR_06", "HDR_08", "HDR_09", "HDR_10", "HDR_11", "HDR_12", "HDR_13", "HDR_15", "HDR_17"])
        self.assertEqual(CAT.sdr_gray_by_stim(50), "SDR_11")
        self.assertEqual(CAT.sdr_gray_by_stim(20), "SDR_17")
        self.assertEqual(CAT.sdr_gray_by_stim(10), "SDR_19")


class StabilityTests(unittest.TestCase):
    def test_stable_and_unstable(self):
        d = StabilityDetector()
        for y in (100, 100.3, 99.8):
            d.add(gray_xyz(y))
        self.assertTrue(d.is_stable())
        d.add(gray_xyz(102))
        self.assertFalse(d.is_stable())

    def test_needs_window(self):
        d = StabilityDetector()
        d.add(gray_xyz(100))
        d.add(gray_xyz(100))
        self.assertFalse(d.is_stable())

    def test_low_light_tolerance(self):
        d = StabilityDetector()
        for y in (0.100, 0.104, 0.097):
            d.add(gray_xyz(y))
        self.assertTrue(d.is_stable())

    def test_xy_instability(self):
        d = StabilityDetector()
        d.add(gray_xyz(100))
        d.add(gray_xyz(100))
        d.add((95.047 * 1.01, 100, 108.883 * 0.99))
        self.assertFalse(d.is_stable())

    def test_min_span(self):
        d = StabilityDetector(min_span=1.5)
        for i in range(5):
            d.add(gray_xyz(100), t=i * 0.1)        # 0.4 s — too early
        self.assertFalse(d.is_stable())
        for i in range(5, 20):
            d.add(gray_xyz(100), t=i * 0.1)        # 1.9 s — stable
        self.assertTrue(d.is_stable())
        d.add(gray_xyz(103), t=2.0)                # a jump inside the 1.5 s window
        self.assertFalse(d.is_stable())
        d2 = StabilityDetector(min_span=1.5)
        for i in range(3):
            d2.add(gray_xyz(100), t=i * 2.0)       # slow readings: 3 in 4 s
        self.assertTrue(d2.is_stable())

    def test_change(self):
        d = StabilityDetector()
        ref = gray_xyz(100)
        self.assertFalse(d.changed_from(ref, gray_xyz(104)))
        self.assertTrue(d.changed_from(ref, gray_xyz(100 * 0.95 ** 2.4)))  # neighboring step 95 %
        self.assertTrue(d.changed_from(ref, (100, 100, 100)))              # same Y, different color


class HintTests(unittest.TestCase):
    def test_balance_hints(self):
        self.assertEqual(A.balance_hints((100, 100, 100)), [])
        h = A.balance_hints((105, 100, 96), "Gain")
        self.assertEqual([(x["channel"], x["action"]) for x in h], [("R", "down"), ("B", "up")])
        self.assertIn("Gain", h[0]["text"])
        # G low, R and B equal to each other → both channels are "too much" relative to G
        h = A.balance_hints((103, 97, 103), "Offset")
        self.assertEqual([(x["channel"], x["action"]) for x in h], [("R", "down"), ("B", "down")])

    def _session(self, white=100.0, gamma=2.4, tint=(1, 1, 1), step="gray", stims=None):
        s = new_session({})
        s["whiteY"] = white
        for pid in CAT.sdr_gray():
            p = CAT.get(pid)
            if stims is not None and round(p["stim"] * 100) not in stims:
                continue
            Y = white * p["stim"] ** gamma + 0.02
            X, Yy, Z = gray_xyz(Y)
            s["measurements"].append({"id": pid, "patchId": pid, "stepId": step, "pass": 1, "timestamp": "",
                                      "X": X * tint[0], "Y": Yy, "Z": Z * tint[2], "samples": 3,
                                      "sdYpercent": 0.1})
        return s

    def test_gray_perfect(self):
        an = A.analyze_gray(self._session(), CAT, "gray")
        sm = an["summary"]
        self.assertLess(sm["deMean"], 0.01)
        self.assertAlmostEqual(sm["gMean"], 2.4, delta=0.02)
        self.assertEqual(sm["verdict"], "good")
        self.assertEqual([h for h in an["hints"] if h["level"] == "bad"], [])
        self.assertIsNotNone(sm["contrast"])

    def test_gray_too_bright_mid(self):
        an = A.analyze_gray(self._session(gamma=2.2), CAT, "gray")
        self.assertEqual(an["summary"]["gText"], "mids brighter than target (γ lower)")
        self.assertTrue(any("brighter than target" in h["text"] for h in an["hints"]))
        self.assertEqual(an["summary"]["verdict"], "bad")
        # neighboring steps are grouped: one zone hint + a general one about the gamma preset
        self.assertEqual(len(an["hints"]), 2, an["hints"])
        self.assertIn("Overall γ ≈ 2.20", an["hints"][0]["text"])

    def test_gray_tint_hints(self):
        an = A.analyze_gray(self._session(tint=(1.06, 1, 0.94)), CAT, "gray")
        self.assertTrue(any("balance" in h["text"] for h in an["hints"]))
        self.assertIn(an["summary"]["verdict"], ("ok", "bad"))

    def test_ratio95(self):
        an = A.analyze_gray(self._session(step="black", stims=[100, 95]), CAT, "black")
        self.assertAlmostEqual(an["summary"]["ratio95"], 0.884, delta=0.005)

    def test_color_analysis(self):
        s = new_session({})
        s["whiteY"] = 100.0
        for pid in CAT.sdr_colors():
            p = CAT.get(pid)
            t = C.color_target_XYZ(p["rgb"], 100.0, "2.4")
            k = 1.1 if p["hue"] == "red" else 1.0
            s["measurements"].append({"id": pid, "patchId": pid, "stepId": "color", "pass": 1, "timestamp": "",
                                      "X": t[0] * k, "Y": t[1] * k, "Z": t[2] * k, "samples": 3, "sdYpercent": 0})
        an = A.analyze_color(s, CAT)
        rows = {r["patchId"]: r for r in an["rows"] if r.get("measured")}
        self.assertLess(rows["SDR_23"]["de"], 0.01)  # yellow is exact
        self.assertGreater(rows["SDR_22"]["dY"], 9)  # red is 10 % brighter
        self.assertIn("Bright decrease", rows["SDR_22"]["hint"])
        self.assertEqual(an["summary"]["count"], 6)

    def test_hue_direction(self):
        """Red shifted toward yellow (Δh > 0) → hint "toward yellow", move toward magenta."""
        s = new_session({})
        s["whiteY"] = 100.0
        t = C.color_target_XYZ([1, 0, 0], 100.0, "2.4")
        L = C.lab(t, C.scale(C.D65, 100))
        Lc, Cc, h = C.lch(L)
        h2 = math.radians(h + 6)
        L2 = (Lc, Cc * math.cos(h2), Cc * math.sin(h2))
        # Lab → XYZ
        fy = (L2[0] + 16) / 116
        fx, fz = fy + L2[1] / 500, fy - L2[2] / 200
        finv = lambda f: f ** 3 if f ** 3 > 216 / 24389 else (116 * f - 16) / (24389 / 27)  # noqa: E731
        XYZ = tuple(finv(f) * w for f, w in zip((fx, fy, fz), C.scale(C.D65, 100)))
        s["measurements"].append({"id": "a", "patchId": "SDR_22", "stepId": "color", "pass": 1, "timestamp": "",
                                  "X": XYZ[0], "Y": XYZ[1], "Z": XYZ[2], "samples": 3, "sdYpercent": 0})
        row = [r for r in A.analyze_color(s, CAT)["rows"] if r["patchId"] == "SDR_22"][0]
        self.assertAlmostEqual(row["dh"], 6, delta=0.2)
        self.assertIn("toward yellow", row["hint"])
        self.assertIn("move Color tone toward magenta", row["hint"])

    def test_hdr_rolloff(self):
        s = new_session({})
        for pid in CAT.hdr_gray([75, 65, 60, 55, 50, 45, 40, 30, 20]):
            p = CAT.get(pid)
            Y = min(p["tnits"], 180.0)
            X, Yy, Z = gray_xyz(Y)
            s["measurements"].append({"id": pid, "patchId": pid, "stepId": "hdr", "pass": 1, "timestamp": "",
                                      "X": X, "Y": Yy, "Z": Z, "samples": 3, "sdYpercent": 0})
        sm = A.analyze_hdr(s, CAT)["summary"]
        self.assertAlmostEqual(sm["peak"], 180, delta=0.1)
        self.assertEqual(sm["rolloff"]["stim"], 60)   # 55 % ≈ 153 nits still on target, 60 % ≈ 244 nits clipped to 180
        self.assertEqual(sm["verdict"], "good")


class SchemeTests(unittest.TestCase):
    def test_gray_plan_order(self):
        s = new_session({})
        s["whiteY"] = 100.0

        def add(stim, tint=(1, 1, 1), gamma=2.4):
            pid = CAT.sdr_gray_by_stim(stim)
            X, Y, Z = gray_xyz(100 * (stim / 100) ** gamma)
            s["measurements"].append({"id": pid + str(len(s["measurements"])), "patchId": pid, "stepId": "gray",
                                      "pass": 1, "timestamp": "2026-11-02T01:00:00", "X": X * tint[0], "Y": Y,
                                      "Z": Z * tint[2], "samples": 3, "sdYpercent": 0.1, "flags": []})
        for k in (100, 80, 60, 40, 20, 10):
            add(k, tint=(1.03, 1, 1) if k == 100 else (1, 1, 1))
        plan = A.gray_plan(A.analyze_gray(s, CAT, "gray"), s, CAT)
        self.assertIn("Gain", plan["title"])
        add(100)
        add(20, tint=(1, 1, 1.04))
        plan = A.gray_plan(A.analyze_gray(s, CAT, "gray"), s, CAT)
        self.assertIn("Offset", plan["title"])
        add(20)
        plan = A.gray_plan(A.analyze_gray(s, CAT, "gray"), s, CAT)
        self.assertEqual(plan["level"], "good")

    def test_cp1_model_reproduces_measured_change(self):
        """Point 1 model on real 2026-11-01 data: shadows after the change was reset (+17 % at 10 %, +12 % at 20 %)."""
        rows = [{"measured": True, "stim": k, "dev": d} for k, d in
                ((70, 1.3), (60, 2.0), (50, 3.0), (40, 5.2), (30, 6.0), (20, 9.6), (10, 19.6))]
        n = A.cp1_steps(rows)
        self.assertTrue(-11 <= n <= -6, n)
        # original measurement: −14 steps → −30 % at 10 %, −4 % at 50 %
        self.assertAlmostEqual(math.exp(A.CP1_K * -14 * math.log(0.1) ** 2) - 1, -0.30, delta=0.03)
        self.assertAlmostEqual(math.exp(A.CP1_K * -14 * math.log(0.5) ** 2) - 1, -0.040, delta=0.01)
        # flat mids and shadows — no hint; a general gamma shift is not about point 1 either
        self.assertIsNone(A.cp1_steps([{"measured": True, "stim": k, "dev": 0.5} for k in (10, 20, 30, 50)]))
        self.assertIsNone(A.cp1_steps([{"measured": True, "stim": k, "dev": 9} for k in (10, 20, 30, 50, 60)]))

    def test_gp_plan_on_real_data(self):
        """Run of 2026-11-03 (points 1:−20 … 12:−3, 20 points at 5 %): shadows 15–50 % darker than target → raise 3–10."""
        rows = [{"measured": True, "stim": k, "dev": d, "Y": 1.0} for k, d in (
            (95, -0.4), (90, -1.2), (85, -0.7), (70, 1.0), (65, -1.2), (60, -0.8), (55, 0.5), (50, -3.4),
            (45, -7.3), (40, -10.1), (35, -12.5), (30, -17.0), (25, -14.4), (20, -11.6), (15, -15.0), (10, -9.0))]
        vals = {"1": -20, "2": -16, "3": -16, "4": -15, "5": -13, "6": -11, "7": -10, "8": -9, "9": -6, "10": -2,
                "11": -3, "12": -3}
        plan = {r["point"]: r for r in A.gp_plan({"rows": rows}, vals)}
        self.assertEqual(sorted(plan), list(range(1, 20)))
        self.assertTrue(6 <= plan[6]["delta"] <= 9, plan[6])         # 30 %: −17 % → ≈ +8 steps
        self.assertTrue(3 <= plan[8]["delta"] <= 6, plan[8])         # 40 %: −10 % → ≈ +5
        self.assertTrue(-1 <= plan[11]["delta"] <= 1, plan[11])      # 55 %: fine
        self.assertEqual(plan[15]["delta"], 0)                       # 75 % not measured — left alone
        self.assertEqual(plan[1]["delta"], 0)                        # 5 % not measured
        self.assertEqual(A.gp_queue({"rows": rows})[:3], [2, 3, 4])

    def test_gp_learn_reproduces_fit(self):
        """Measurement 2026-11-03: "all 0" → values from the menu. 20 points at 5 % model — ≈ 2.3–2.5 % per step."""
        def rows(eff):
            return {"rows": [{"measured": True, "stim": k, "Y": 100 * (k / 100) ** 2.4 * (1 + eff.get(k, 0) / 100)}
                             for k in range(10, 101, 5)]}
        eff = {20: -29.2, 25: -28.2, 30: -27.2, 35: -21.1, 40: -17.0, 45: -13.2, 50: -8.4, 55: -3.8, 60: -4.1}
        vals = {"1": -20, "2": -16, "3": -16, "4": -15, "5": -13, "6": -11, "7": -10, "8": -9, "9": -6, "10": -2,
                "11": -3, "12": -3}
        lr = A.gp_learn(rows({}), {"rows": [r for r in rows(eff)["rows"] if r["stim"] >= 20]}, vals)
        self.assertTrue(lr["used"])
        self.assertAlmostEqual(lr["perStep"], 2.4, delta=0.3)
        self.assertGreater(lr["fit"], 0.9)

    def test_drift_model_warmup(self):
        """Warm-up 2026-11-05: white 37.9 → 39.9 nits, τ ≈ 26 s — the model recovers the curve, correction ≤ 0.3 %."""
        from projectorcal import fulltest as FT
        import math as M
        W = lambda t: 39.9 - 3.3 * M.exp(-t / 26.0)  # noqa: E731
        segs = [{"patchId": "SDR_01", "role": role, "start": t - 4, "dur": 8, "XYZ": (1, W(t), 1), "n": 5}
                for role, t in (("mark", 14), ("patch", 28), ("drift", 362), ("drift", 473))]
        m = FT.drift_model({"mode": "sdr", "segments": segs})
        self.assertEqual(m["kind"], "exp")
        for t in (45, 100, 240, 600):
            self.assertAlmostEqual(FT.drift_white(m, t) / W(t), 1.0, delta=0.003)
        self.assertIsNone(FT.drift_model({"mode": "sdr", "segments": [dict(x, XYZ=(1, 39.0, 1)) for x in segs]}))

    def test_flag_before_failure(self):
        from projectorcal.autorun import AutoRun, reliable
        segs = [{"i": i, "patchId": "SDR_%02d" % (30 + i), "role": "patch", "start": 10 * i, "dur": 10,
                 "XYZ": (1, 1, 1), "n": 3, "flags": []} for i in range(8)]
        for x in segs[5:]:
            x["XYZ"], x["flags"] = None, ["no readings"]
        AutoRun._flag_before_failure(segs)
        self.assertEqual([reliable(x) for x in segs], [True] * 3 + [False] * 5)   # 15 s window — two 10 s patches
        self.assertIn("before failure", segs[4]["flags"])

    def test_cms_weighted_plan(self):
        """Blue mid saturations brighter than target, 100 % darker: lower Bright, since mid tones weigh more."""
        from projectorcal import fulltest as FT, colorimetry as C2
        def row(sat, amp, dy):
            t = (30.0, 10.0, -60.0 * sat)
            yr = ((t[0] + 16) / 116.0) ** 3 * (1 + dy / 100.0)
            L = 116 * yr ** (1 / 3.0) - 16
            k = (1 + dy / 100.0) ** (1 / 3.0)
            lab = (L, t[1] * k, t[2] * k)
            return {"measured": True, "hue": "blue", "sat": sat, "amp": amp, "lab": list(lab), "tlab": list(t),
                    "de": C2.de2000(lab, t)}
        rows = [row(0.25, 0.75, 6), row(0.5, 0.75, 18), row(0.75, 0.75, 22), row(1.0, 0.75, -5), row(1.0, 1.0, -8)]
        sdr = {"cms": {"blue": {"tone": 0, "sat": 0, "bright": 3}}}
        it = FT._plan_cms({"color": {"rows": rows}}, sdr, {("cms:blue", "bright → dY"): 11.0}, "blue")
        self.assertIsNotNone(it)
        b = [x for x in it["set"] if x["path"].endswith(".bright")][0]
        self.assertLess(b["to"], 3)

    def test_hdr_reference_white(self):
        s = new_session({})
        s["whiteY"] = 39.1
        meas = {75: 40.8, 65: 30.6, 60: 25.2, 55: 19.7, 50: 13.8, 45: 8.2, 40: 4.35, 30: 1.23, 20: 0.30}
        for pid in CAT.hdr_gray(list(meas)):
            Y = meas[round(CAT.get(pid)["stim"] * 100)]
            X, Yy, Z = gray_xyz(Y)
            s["measurements"].append({"id": pid, "patchId": pid, "stepId": "hdr", "pass": 1, "timestamp": "",
                                      "X": X, "Y": Yy, "Z": Z, "samples": 3, "sdYpercent": 0, "flags": []})
        sm = A.analyze_hdr(s, CAT)["summary"]
        self.assertAlmostEqual(sm["refWhite"], 22.9, delta=0.5)
        self.assertEqual(sm["refWhitePct"], 56)

    def test_suspect_excluded(self):
        s = new_session({})
        s["whiteY"] = 100.0
        X, Y, Z = gray_xyz(100)
        s["measurements"] += [
            {"id": "a", "patchId": "SDR_01", "stepId": "gray", "pass": 1, "timestamp": "", "X": X, "Y": Y, "Z": Z,
             "samples": 3, "sdYpercent": 0, "flags": []},
            {"id": "b", "patchId": "SDR_01", "stepId": "gray", "pass": 1, "timestamp": "", "X": 5, "Y": 9, "Z": 1,
             "samples": 3, "sdYpercent": 70, "flags": ["suspect"]}]
        row = [r for r in A.analyze_gray(s, CAT, "gray")["rows"] if r["stim"] == 100][0]
        self.assertEqual(row["mid"], "a")

    def test_old_session_migrates(self):
        from projectorcal.session import migrate
        s = new_session({})
        s["currentStep"] = "wb"
        del s["steps"]["gray"]
        m = migrate(s)
        self.assertEqual(m["currentStep"], "gray")
        self.assertIn("gray", m["steps"])

    def test_hdr_brighter_than_previous_rejected(self):
        s = new_session({})
        prev = {"patchId": "HDR_08", "Y": 23.5}
        self.assertIsNotNone(A.plausibility(CAT.get("HDR_09"), gray_xyz(40.4), s, CAT, prev))
        self.assertIsNone(A.plausibility(CAT.get("HDR_09"), gray_xyz(23.4), s, CAT, prev))   # equal near the peak — allowed

    def test_history_check(self):
        s = new_session({})
        s["whiteY"] = 39.0
        X, Y, Z = C.color_target_XYZ([0, 1, 1], 39.0, "2.4")
        s["measurements"].append({"id": "c", "patchId": "SDR_25", "stepId": "color", "pass": 1,
                                  "timestamp": "2026-11-02T02:08:00", "X": X, "Y": Y, "Z": Z, "samples": 3,
                                  "sdYpercent": 0, "flags": []})
        # the same cyan but half as bright (player bar/dimming) → wrong patch
        self.assertIsNotNone(A.plausibility(CAT.get("SDR_25"), (X * 0.45, Y * 0.45, Z * 0.45), s, CAT))
        self.assertIsNone(A.plausibility(CAT.get("SDR_25"), (X * 1.05, Y * 1.05, Z * 1.05), s, CAT))


class PlausibilityTests(unittest.TestCase):
    def setUp(self):
        self.s = new_session({})
        self.s["whiteY"] = 100.0

    def test_gray_ok_and_wrong(self):
        p = CAT.get("SDR_11")
        self.assertIsNone(A.plausibility(p, gray_xyz(100 * 0.5 ** 2.4), self.s, CAT))
        bad = A.plausibility(p, gray_xyz(100 * 0.8 ** 2.4), self.s, CAT)
        self.assertIsNotNone(bad)
        self.assertEqual(bad["guess"], "SDR_05")

    def test_white_checked_against_fixed(self):
        p = CAT.get("SDR_01")
        self.assertIsNone(A.plausibility(p, gray_xyz(103), self.s, CAT))
        bad = A.plausibility(p, gray_xyz(100 * 0.2 ** 2.4), self.s, CAT)
        self.assertEqual(bad["guess"], "SDR_17")
        s2 = new_session({})
        self.assertIsNone(A.plausibility(p, gray_xyz(5), s2, CAT))  # white is not fixed yet

    def test_gray_colored(self):
        p = CAT.get("SDR_11")
        t = C.color_target_XYZ([0, 0, 1], 100, "2.4")
        self.assertIn("color patch", A.plausibility(p, t, self.s, CAT)["text"])

    def test_previous_not_dropped(self):
        p = CAT.get("SDR_02")
        prev = {"patchId": "SDR_01", "Y": 100.0}
        self.assertIsNotNone(A.plausibility(p, gray_xyz(100.0), self.s, CAT, prev))

    def test_color(self):
        p = CAT.get("SDR_22")
        red = C.color_target_XYZ([1, 0, 0], 100, "2.4")
        self.assertIsNone(A.plausibility(p, red, self.s, CAT))
        green = C.color_target_XYZ([0, 1, 0], 100, "2.4")
        self.assertEqual(A.plausibility(p, green, self.s, CAT)["guess"], "SDR_24")
        self.assertIn("Gray", A.plausibility(p, gray_xyz(100), self.s, CAT)["text"])

    def test_drift(self):
        self.assertIsNone(A.brightness_drift(self.s, 103))
        self.assertAlmostEqual(A.brightness_drift(self.s, 94), -0.06)


class ProfileTests(unittest.TestCase):
    """Projector / meter profiles (v1.7): gamma layouts, meter detection, slow auto-run schedule."""

    def tearDown(self):
        A.set_gp_layout(20)

    def test_gp_layout_10_points(self):
        A.set_gp_layout(10)
        self.assertEqual(A.gp_stim(3), 30)
        self.assertEqual(A.GP_POINTS, tuple(range(1, 10)))
        # mids too dark by 10 % around 40 %: the 10-point plan lifts point 4 (40 %) and nothing at the top
        rows = [{"measured": True, "stim": k, "dev": (-10.0 if 30 <= k <= 50 else 0.0), "Y": 1.0}
                for k in range(10, 100, 5)]
        plan = {r["point"]: r for r in A.gp_plan({"rows": rows}, {})}
        self.assertEqual(sorted(plan), list(range(1, 10)))
        self.assertEqual(plan[4]["stim"], 40)
        self.assertGreater(plan[4]["delta"], 2)
        self.assertEqual(plan[9]["delta"], 0)
        self.assertIn(4, A.gp_queue({"rows": rows}))

    def test_no_gamma_points_suggests_preset(self):
        from projectorcal import fulltest as FT
        A.set_gp_layout(0)
        rows = [{"measured": True, "stim": k, "dev": 8.0, "Y": 1.0} for k in range(10, 100, 5)]
        self.assertEqual(A.gp_plan({"rows": rows}, {}), [])
        self.assertEqual(A.gp_queue({"rows": rows}), [])
        it = FT._plan_gamma_preset({"rows": rows}, {"gammaPreset": "2.2"})
        self.assertEqual(it["level"], "bad")
        self.assertIn("darker (higher)", it["text"])
        A.set_gp_layout(20)
        self.assertEqual(A.gp_stim(3), 15)

    def test_meter_detection(self):
        from projectorcal import profiles as P
        self.assertEqual(P.detect_meter(["/dev/bus/usb/001/005 (GretagMacbeth i1 DisplayPro, ColorMunki Display)"]),
                         ("i1d3", True))
        self.assertEqual(P.detect_meter(["usb (Datacolor SpyderX)"])[0], "spyder")
        self.assertEqual(P.detect_meter(["usb (X-Rite i1 Pro 2)"])[0], "i1pro")
        self.assertEqual(P.detect_meter(["usb (Klein K-10)"])[0], "k10")
        self.assertEqual(P.detect_meter(["usb (Hughski ColorHug2)"])[0], "other-slow")
        self.assertEqual(P.detect_meter([]), (None, False))
        raw = (" -y X                 Display type - instrument specific list to choose from:\n"
               "    l                    LCD (Generic)\n    1                    Factory Default [Default] (Generic)\n"
               "    u                    WLED Family Display (DisplayCAL)\n -I illum             Set illumination\n")
        self.assertEqual([k for k, _ in P.parse_display_types(raw)], ["l", "1", "u"])
        for m in P.METERS:
            self.assertIn(m["speed"], ("std", "slow"))
        for p in P.PROJECTORS:
            self.assertIn(p["gpCount"], P.GP_LAYOUTS)

    def test_slow_schedule(self):
        from projectorcal import sequence as Q
        std, slow = Q.build(CAT, "sdr"), Q.build(CAT, "sdr", "slow")
        self.assertNotIn("speed", std)             # the v3 standard videos stay valid
        self.assertEqual(slow["file"], "ProjectorCal_AUTO_SDR_v3_slow.mp4")
        self.assertEqual([s["patchId"] for s in std["segments"]], [s["patchId"] for s in slow["segments"]])
        for a, b in zip(std["segments"], slow["segments"]):
            self.assertGreaterEqual(b["dur"], a["dur"])
        self.assertGreater(slow["duration"], 1.8 * std["duration"])

    def test_meter_args_display_type(self):
        from projectorcal.meter import build_args
        self.assertEqual(build_args({"flag": "-e", "displayType": "u"}), ["-e", "-y", "u"])
        self.assertEqual(build_args({"flag": "-e", "displayType": "u; rm"}), ["-e"])

    def test_old_session_gets_profile(self):
        from projectorcal.session import migrate
        s = new_session({})
        del s["projectorProfile"]["model"], s["projectorProfile"]["gpCount"]
        s = migrate(s)
        self.assertEqual(s["projectorProfile"]["model"], "vidda-c3s")
        self.assertEqual(s["projectorProfile"]["gpCount"], 20)


if __name__ == "__main__":
    unittest.main()
