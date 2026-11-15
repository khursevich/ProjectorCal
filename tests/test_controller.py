"""Controller integration tests: simulation and the spotread stub (acceptance criteria of the original spec)."""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from projectorcal.catalog import Catalog  # noqa: E402
from projectorcal.controller import Controller  # noqa: E402
from projectorcal.session import Store  # noqa: E402
from test_meter import pid_alive  # noqa: E402

CAT = Catalog(os.path.join(ROOT, "assets", "patches"))
FAKE_EXT = os.path.join(HERE, "fake_spotread_ext")


def wait(cond, timeout=20.0, step=0.05):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(step)
    return False


class Base(unittest.TestCase):
    mode = "mock"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = dict(os.environ)
        for k in list(os.environ):
            if k.startswith("FAKE_") or k == "STUB_SEQ":
                del os.environ[k]
        self.store = Store(os.path.join(self.tmp, "data"))
        st = self.store.load_settings()
        st["meterMode"] = self.mode
        st["spotreadPath"] = FAKE_EXT
        self.store.save_settings(st)
        self.ctl = None

    def start(self):
        self.ctl = Controller(self.store, CAT, mock_delay=0.01, min_span=0.0)
        return self.ctl

    def tearDown(self):
        if self.ctl:
            self.ctl.shutdown()
        os.environ.clear()
        os.environ.update(self.saved)


class MockFlowTests(Base):
    def test_auto_gamma_pass_and_resume(self):
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.set_fields({"whiteY": 140.0})
        c.goto("gray")
        c.set_step("gray", {"full": True})
        self.assertTrue(wait(lambda: c.state()["pass"]["patchId"] is None, timeout=120), c.state()["pass"])
        ms = [m for m in c.session["measurements"] if m["stepId"] == "gray"]
        self.assertEqual([m["patchId"] for m in ms], CAT.sdr_gray())
        an = c.session_view()["analysis"]["gray"]["summary"]
        self.assertAlmostEqual(an["gMean"], 2.25, delta=0.05)  # simulator gamma
        sid = c.session["id"]
        c.shutdown()
        # program restart → the same session, the same step, readings in place (criterion 4)
        self.ctl = Controller(self.store, CAT, mock_delay=0.01, min_span=0.0)
        self.assertEqual(self.ctl.session["id"], sid)
        self.assertEqual(self.ctl.session["currentStep"], "gray")
        self.assertEqual(len([m for m in self.ctl.session["measurements"] if m["stepId"] == "gray"]), 21)

    def test_wrong_patch_then_accept(self):
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.set_fields({"whiteY": 140.0})
        c.projector.update({"follow": False, "displayed": "SDR_01"})
        c.goto("verify")
        c.set_step("verify", {"quick": True})
        # white recorded, next we wait for 70 %, but 30 % is on screen
        self.assertTrue(wait(lambda: c.session["steps"]["verify"].get("idx") == 1))
        c.projector.update({"displayed": "SDR_15"})
        self.assertTrue(wait(lambda: c.state()["pass"]["wrong"] is not None), c.state()["pass"])
        w = c.state()["pass"]["wrong"]
        self.assertIn("SDR_07", w["text"])
        self.assertEqual(w["guess"], "SDR_15")
        self.assertEqual(len([m for m in c.session["measurements"] if m["stepId"] == "verify"]), 1)
        c.pass_action("verify", "accept")
        last = c.session["measurements"][-1]
        self.assertEqual((last["patchId"], last["flags"]), ("SDR_07", ["accepted"]))

    def test_brightness_drift_warning(self):
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.set_fields({"whiteY": 140.0})
        c.projector.update({"follow": False, "displayed": "SDR_01", "whiteNits": 125})
        c.goto("verify")
        self.assertTrue(wait(lambda: c.state()["drift"] is not None, timeout=30), c.state())
        self.assertLess(c.state()["drift"]["pct"], -5)
        c.dismiss("acceptWhite")
        self.assertAlmostEqual(c.session["whiteY"], 125, delta=3)

    def test_fix_white_and_gray_live(self):
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.goto("laser")
        c.measure("fixwhite", {"laserLevel": "9"})   # the simulator is still "switching" the patch — the Plex menu is on screen
        self.assertTrue(wait(lambda: any("not white" in n["text"] for n in c.notices)))
        self.assertIsNone(c.session.get("whiteY"))
        time.sleep(1.6)
        c.measure("fixwhite", {"laserLevel": "9"})
        self.assertTrue(wait(lambda: c.session.get("whiteY") is not None))
        self.assertEqual(c.session["laserLevel"], "9")
        self.assertEqual(c.session["steps"]["laser"]["status"], "done")
        c.goto("gray")
        self.assertTrue(wait(lambda: c.state()["pass"]["patchId"] is None, timeout=60))
        plan = c.session_view()["analysis"]["grayPlan"]
        self.assertIn("Gain", plan["title"])          # the simulator is warm → Gain at 100 % first
        c.gray_live(plan["patchId"])
        st = c.state()
        self.assertEqual((st["activity"]["type"], st["activity"]["patchId"]), ("live", "SDR_01"))
        self.assertTrue(wait(lambda: c.state().get("live", {}).get("hints")))
        c.gray_live(None)
        self.assertEqual(c.state()["activity"]["type"], "pass")

    def test_manual_measure_rejects_patch_switch(self):
        """Patch switched during a manual reading → the mix is not recorded."""
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.set_fields({"whiteY": 140.0})
        c.projector.update({"follow": False, "displayed": "SDR_01"})
        c.goto("verify")
        c.pass_action("verify", "auto", False)
        c.mock_delay = 0.3
        c.meter.delay = 0.3
        c.pass_action("verify", "measure")
        # switch exactly in the middle of the reading: after the first sample
        self.assertTrue(wait(lambda: (c.job_running or {}).get("i", 0) >= 1, timeout=10, step=0.01))
        c.projector.update({"displayed": "SDR_11"})   # the "user" switched in the middle of the reading
        self.assertTrue(wait(lambda: c.state()["meter"]["job"] is None and not c.jobs, timeout=10))
        self.assertEqual([m for m in c.session["measurements"] if m["stepId"] == "verify"], [])
        self.assertIn("changed during the reading", c.state()["pass"]["state"]["text"])

    def test_manual_wrong_patch_not_recorded_silently(self):
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.set_fields({"whiteY": 140.0})
        c.projector.update({"follow": False, "displayed": "SDR_11"})
        c.goto("verify")
        c.pass_action("verify", "auto", False)
        c.pass_action("verify", "measure")       # waiting for white, 50 % is on screen
        self.assertTrue(wait(lambda: c.state()["pass"]["wrong"] is not None, timeout=10))
        self.assertEqual([m for m in c.session["measurements"] if m["stepId"] == "verify"], [])
        c.pass_action("verify", "accept")
        self.assertEqual(c.session["measurements"][-1]["flags"], ["accepted"])

    def _gp_pass(self, c):
        self.assertTrue(wait(lambda: c._gp_pass_done(), timeout=120), c.state()["pass"])

    def test_gamma_points_batch_rounds(self):
        """Gamma by points: pass → corrections for all points → set → check → refinement → done."""
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.goto("gpoints")
        c.gp_action("start", {"preset": "dark"})
        st = c.session["steps"]["gpoints"]
        self.assertEqual((st["stage"], set(st["values"].values()), len(st["values"])), ("base", {0}, 20))
        p = c.projector
        for rnd in range(4):
            self._gp_pass(c)
            c.gp_action("plan")
            self.assertEqual(st["stage"], "plan")
            if not st["queue"]:
                break
            gp = list(p.gp)
            for r in st["plan"]:
                gp[r["point"] - 1] = r["new"]
            p.update({"gp": gp})          # "set in the menu and left"
            c.gp_action("applied")
            self.assertEqual(st["stage"], "check")
        self.assertEqual(st["queue"], [])
        self.assertLessEqual(rnd, 2)                        # 2–3 full passes
        self.assertAlmostEqual(st["perStep"], 2.3, delta=0.3)   # the simulator sensitivity is learned
        c.gp_action("finish")
        self.assertEqual(st["stage"], "done")
        self.assertEqual(c.session["menuValues"]["sdr"]["Gamma Control Points"],
                         ", ".join("%d:%+d" % (k, v) for k, v in enumerate(p.gp, 1) if v))

    def test_gamma_continue_and_probe(self):
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.projector.update({"gp": [-20, -10, -8, -6, -4, -3, -2, -1] + [0] * 12})
        c.goto("gpoints")
        c.gp_action("continue", {"values": {"1": -20, "2": -10, "3": -8, "4": -6, "5": -4, "6": -3, "7": -2,
                                            "8": -1}})
        st = c.session["steps"]["gpoints"]
        self.assertEqual(st["values"]["1"], -20)
        self._gp_pass(c)
        c.gp_action("plan")
        gp = list(c.projector.gp)
        gp[9] -= 5                                           # probe point 10 (50 %) by −5
        c.projector.update({"gp": gp})
        c.gp_action("probe", {"point": 10, "delta": -5})
        self.assertEqual((st["stage"], st["values"]["10"]), ("check", -5))
        self._gp_pass(c)
        c.gp_action("plan")
        res = st["probe"]["result"]
        self.assertEqual(res["peak"], 50)
        self.assertEqual((res["from"], res["to"]), (50, 50))       # nodes at 5 %: neighboring steps untouched
        c.gp_action("probeRestore")
        self.assertEqual((st["values"]["10"], st["probe"]), (0, None))

    def test_full_check_sdr_and_learning(self):
        """Full check: auto run of the video (simulation, ×10) → all patches → analysis, plan; a second run after
        a Gain change → the program learns "% per step" and the plan for the third run gives the exact value."""
        os.environ["PROJECTORCAL_MOCK_SPEED"] = "10"
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.set_psettings({"sdr.gain.R": 0, "sdr.gain.B": 0, "sdr.blackLevel": 53})
        c.goto("full")
        c.full_start("sdr")
        self.assertEqual(c.state()["auto"]["state"], "arming")
        self.assertTrue(wait(lambda: len(c.session["runs"]) == 1, timeout=120), c.state().get("auto"))
        run = c.session["runs"][0]
        self.assertEqual(run["used"], 51)
        self.assertTrue(run["sync"]["endMarker"])
        fv = c.session_view()["analysis"]["full"]
        lat = fv["latest"]["sdr"]
        self.assertAlmostEqual(lat["analysis"]["metrics"]["gMean"], 2.25, delta=0.05)   # simulator gamma
        gain_item = [i for i in lat["plan"] if i["group"] in ("wbGain", "wb")][0]
        self.assertTrue(any(x["path"] == "sdr.gain.R" and x["to"] < 0 for x in gain_item["set"]))
        # "turn" Gain R by −6 in the projector and in the settings → second run
        c.projector.update({"gain": [-6, 0, 0]})
        c.set_psettings({"sdr.gain.R": -6})
        c.full_start("sdr")
        self.assertTrue(wait(lambda: len(c.session["runs"]) == 2, timeout=120))
        fv = c.session_view()["analysis"]["full"]
        k = [e for e in fv["knowledge"] if e["single"]]
        self.assertTrue(k and "Gain R" in k[0]["perStep"], fv["knowledge"])
        per = k[0]["perStep"]["Gain R"]
        self.assertLess(abs(per - 0.5), 0.15)             # simulator: 0.5 % per step
        run2 = fv["runs"][1]
        self.assertEqual([x["path"] for x in run2["changes"]], ["sdr.gain.R"])
        self.assertTrue(any(x["key"] == "R/G@100" and x["sig"] for x in run2["compare"]))
        item = [i for i in fv["latest"]["sdr"]["plan"] if i["group"] in ("wbGain", "wb")][0]
        r_set = [x for x in item["set"] if x["path"] == "sdr.gain.R"][0]
        self.assertNotIn("Gain ≈", item["text"].split("R/G")[1].split(";")[0])  # already learned
        c.projector.update({"gain": [r_set["to"], 0, 0]})
        c.set_psettings({"sdr.gain.R": r_set["to"]})
        c.full_start("sdr")
        self.assertTrue(wait(lambda: len(c.session["runs"]) == 3, timeout=120))
        m3 = c.session_view()["analysis"]["full"]["latest"]["sdr"]["analysis"]["metrics"]
        self.assertLess(abs(m3["R/G@100"]), 1.0)          # hit in one step

    def test_full_check_hdr_and_navigation_lock(self):
        os.environ["PROJECTORCAL_MOCK_SPEED"] = "10"
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.goto("full")
        c.full_start("hdr")
        c.goto("gray")                                    # cannot leave in the middle of a check
        self.assertEqual(c.session["currentStep"], "full")
        self.assertTrue(wait(lambda: len(c.session["runs"]) == 1, timeout=90))
        self.assertEqual(c.session["runs"][0]["used"], 21)
        h = c.session_view()["analysis"]["full"]["latest"]["hdr"]["analysis"]["hdr"]["summary"]
        self.assertIn("refWhite", h)

    def test_slow_meter_profile_and_10_point_layout(self):
        """Meter profile → slow auto-run video (the mock plays it, the analyzer recovers all patches);
        projector profile with 10 gamma points → points at 10 % steps in the plan."""
        os.environ["PROJECTORCAL_MOCK_SPEED"] = "20"
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.update_settings({"meterProfile": "spyder"})
        self.assertEqual(c.auto_speed(), "slow")
        c.set_fields({"projectorProfile": {"model": "hisense-px", "gpCount": 10}})
        from projectorcal import analysis as A
        self.assertEqual((A.GP_COUNT, A.GP_STEP), (10, 10))
        c.goto("full")
        c.full_start("hdr")
        self.assertTrue(wait(lambda: len(c.session["runs"]) == 1, timeout=120), c.state().get("auto"))
        run = c.session["runs"][0]
        self.assertEqual(run["seq"]["speed"], "slow")
        self.assertTrue(run["file"].endswith("_slow.mp4"))
        self.assertEqual(run["used"], 21)
        c.update_settings({"autoSpeed": "std"})
        self.assertEqual(c.auto_speed(), "std")
        c.set_fields({"projectorProfile": {"model": "vidda-c3s", "gpCount": 20}})
        self.assertEqual((A.GP_COUNT, A.GP_STEP), (20, 5))

    def test_ambient(self):
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.projector.update({"follow": False, "displayed": "SDR_21"})
        c.measure("ambient", {})
        self.assertTrue(wait(lambda: c.session.get("ambientY") is not None))
        self.assertTrue(any("Ambient light" in n["text"] for n in c.notices))

    def test_menu_change_marks_recheck(self):
        c = self.start()
        for sid in ("gray", "gpoints", "color", "verify", "hdr"):
            c.session["steps"][sid]["status"] = "done"
        c.set_fields({"menuValues": {"sdr": {"CMS Red": "1,0,0"}}})
        st = {k: c.session["steps"][k]["status"] for k in ("gray", "color", "verify", "hdr")}
        self.assertEqual(st, {"gray": "done", "color": "recheck", "verify": "recheck", "hdr": "done"})
        c.set_fields({"menuValues": {"sdr": {"Gain R": "3"}}})
        self.assertEqual(c.session["steps"]["gray"]["status"], "recheck")
        self.assertIn("Gain R", c.session["events"][-1]["text"])


class ArgyllControllerTests(Base):
    mode = "argyll"

    def test_disconnect_retry_session_kept(self):
        """Criterion 3: USB disconnect → a clear message → “Retry” works, the session is intact."""
        os.environ["FAKE_EXIT_AT"] = "5"
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        c.measure("test", {})
        self.assertTrue(wait(lambda: c.session["steps"]["prep"]["checklist"].get("_testOk")))
        c.free_live(True)
        self.assertTrue(wait(lambda: c.meter_state == "error"))
        err = c.state()["meter"]["error"]["message"]
        self.assertIn("disconnected", err)
        n = len(c.session["measurements"])
        del os.environ["FAKE_EXIT_AT"]
        c.meter_retry()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        self.assertTrue(wait(lambda: c.state()["reading"] is not None))
        self.assertEqual(len(self.store.load(c.session["id"])["measurements"]), n)

    def test_calibration_dialog(self):
        os.environ["FAKE_CALIB"] = "1"
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "calibration"))
        c.meter_calibration_continue()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))

    def test_no_instrument_message_and_auto_retry(self):
        os.environ["FAKE_NOINST"] = "1"
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "error"))
        self.assertIn("Meter not found", c.state()["meter"]["error"]["message"])
        del os.environ["FAKE_NOINST"]
        c.retry_at = 0  # speed up the auto-retry (normally after 10 s)
        with c.lock:
            c.cond.notify_all()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))

    def test_no_orphan_after_shutdown(self):
        pidfile = os.path.join(self.tmp, "pid")
        os.environ["FAKE_PIDFILE"] = pidfile
        c = self.start()
        self.assertTrue(wait(lambda: c.meter_state == "ready"))
        with open(pidfile) as f:
            pid = int(f.read())
        self.assertTrue(pid_alive(pid))
        c.shutdown()
        self.ctl = None
        self.assertTrue(wait(lambda: not pid_alive(pid), timeout=5))

    def test_spotread_not_found(self):
        st = self.store.load_settings()
        st["spotreadPath"] = "/nonexistent/spotread"
        self.store.save_settings(st)
        os.environ["PATH"] = "/nonexistent"
        c = self.start()
        if c.meter_exe is None or "spotread" not in str(c.meter_exe):
            self.assertTrue(wait(lambda: c.meter_state == "error"))
            from projectorcal.meter import install_hint
        self.assertIn(install_hint(), c.state()["meter"]["error"]["message"])   # the hint for this system


if __name__ == "__main__":
    unittest.main()
