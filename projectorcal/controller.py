"""Wizard controller: meter thread, live mode, patch passes, session.

Threads: HTTP handlers only change state under self.lock and wake the worker thread.
All meter I/O happens in a single worker thread (outside the lock).
"""
from __future__ import annotations

import collections
import json
import math
import os
import threading
import time
import uuid

from . import analysis as A
from . import colorimetry as C
from .catalog import pass_patches
from .meter import ArgyllMeter, MeterError, OneShotMeter, build_args, find_spotread, install_hint, platform_name
from .mock import MockMeter, MockProjector
from . import fulltest as FT
from . import profiles as PR
from . import psettings as PS
from . import sequence as SEQ
from .autorun import AutoRun
from .session import STEP_IDS, gp_fresh, new_session, now_iso
from .stability import StabilityDetector, y_tolerance

PASS_STEPS = ("black", "gray", "gpoints", "color", "verify", "hdr", "summary")
LIVE_STEPS = ("laser",)
MANUAL_DEFAULT = ("black", "summary")   # auto-capture is off by default in these steps

# What to recheck after a menu change (menuValues keys). A top-level change invalidates the lower ones.
_ALL_SDR = ("gray", "gpoints", "color", "verify")
MENU_DEPS = {
    "Black Level": _ALL_SDR, "Contrast": _ALL_SDR, "Brightness level (laser)": _ALL_SDR + ("hdr",),
    "Color Temp": _ALL_SDR, "Gain R": _ALL_SDR, "Gain G": _ALL_SDR, "Gain B": _ALL_SDR,
    "Offset R": _ALL_SDR, "Offset G": _ALL_SDR, "Offset B": _ALL_SDR,
    "Gamma": ("gray", "gpoints", "verify"), "Gamma Control Points": ("verify",),
    "Color Space": ("color", "verify"),
    "CMS Red": ("color", "verify"), "CMS Yellow": ("color", "verify"), "CMS Green": ("color", "verify"),
    "CMS Cyan": ("color", "verify"), "CMS Blue": ("color", "verify"), "CMS Magenta/Pink": ("color", "verify"),
}


class Controller:
    def __init__(self, store, catalog, mock_delay=0.35, min_span=1.5):
        self.store = store
        self.catalog = catalog
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.settings = store.load_settings()
        self.session = store.latest()
        if self.session is None:
            self.session = new_session(self.settings)
            store.save(self.session)
        self._apply_profile()
        self.projector = MockProjector()
        self.mock_delay = mock_delay
        self.meter = None
        self.meter_state = "off"            # off | starting | calibration | ready | error
        self.meter_error = None
        self.meter_busy = False
        self.meter_exe = None
        self.want_meter = True
        self.retry_at = 0.0
        self.retry_count = 0
        self.calib_continue = False
        self.fail_streak = 0
        self.jobs = collections.deque()
        self.activity = None                # {"type": "live"|"pass"|"free", "stepId", ...}
        self.detector = StabilityDetector(window=self._window(), y_pct=self.settings.get("stabilityPct", 0.7),
                                          min_span=min_span)
        self.job_running = None             # {"purpose", "patchId", "i", "n"} — a manual reading is running
        self.autorun = None                 # the running auto run (AutoRun)
        self.auto_meta = None
        self.last_interval = None
        self.full_cache = None              # (rev, full-check data) — run analysis is expensive
        self.rolling = []
        self.last = None
        self.last_t = None
        self.pass_ref = None
        self.pass_state = {"state": "idle", "text": ""}
        self.waiting_since = 0.0
        self.wrong = None
        self.drift = None
        self.notices = collections.deque(maxlen=6)
        self.log_tail = collections.deque(maxlen=300)
        self.rev = 1
        self.mock_target = None
        self.mock_target_t = 0.0
        self.last_request = time.monotonic()
        self.stop = False
        self._restart_meter = False
        self.thread = threading.Thread(target=self._run, name="meter", daemon=True)
        with self.lock:
            self._enter_step(self.session.get("currentStep", "prep"), initial=True)
        self.thread.start()

    # ================================================================ internals
    def _window(self):
        return max(3, int(self.settings.get("avg", 3) or 3))

    def _touch(self):
        """The session changed: save it and bump the revision."""
        self.store.save(self.session)
        self.rev += 1
        self.cond.notify_all()

    def notice(self, level, text):
        self.notices.append({"id": uuid.uuid4().hex[:8], "level": level, "text": text, "t": time.time()})

    def _log(self, text):
        for line in text.splitlines():
            if line.strip():
                self.log_tail.append(line)
        try:
            with open(self.store.log_path(self.session["id"]), "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass

    def _event(self, text):
        self.session["events"].append({"t": now_iso(), "text": text})
        self.session["events"] = self.session["events"][-200:]

    # ================================================================ meter
    def _make_meter(self):
        mode = self.settings.get("meterMode", "argyll")
        if mode == "mock":
            self.meter_exe = "simulation"
            return MockMeter(self.projector, self.catalog, delay=self.mock_delay, log=self._log)
        exe = find_spotread(self.settings.get("spotreadPath"))
        self.meter_exe = exe
        if not exe:
            raise MeterError("spotread not found. " + install_hint() + " (or set the path in Settings).",
                             kind="notfound")
        args = build_args(self.settings)
        cls = OneShotMeter if mode == "oneshot" else ArgyllMeter
        return cls(exe, args, log=self._log)

    def _do_start(self):
        with self.lock:
            self.meter_state = "starting"
            self.meter_error = None
            old, self.meter = self.meter, None
        if old is not None:
            old.close()
        try:
            m = self._make_meter()
            res = m.start()
        except MeterError as e:
            self._meter_failed(e)
            return
        except Exception as e:  # noqa: BLE001 — no failure may crash the thread
            self._meter_failed(MeterError("Could not start the meter: %s" % e, kind="spawn"))
            return
        with self.lock:
            self.meter = m
            self.fail_streak = 0
            if res == "calibration":
                self.meter_state = "calibration"
                self.calib_continue = False
            else:
                self.meter_state = "ready"
                self.retry_count = 0
                self.notice("ok", "Meter connected.")
            self.cond.notify_all()

    def _do_calib_continue(self):
        with self.lock:
            self.calib_continue = False
            m = self.meter
        try:
            res = m.continue_calibration()
        except MeterError as e:
            self._meter_failed(e)
            return
        with self.lock:
            self.meter_state = "calibration" if res == "calibration" else "ready"
            if res == "ready":
                self.notice("ok", "Meter calibration done. Put the meter back in the measuring position.")

    def _meter_failed(self, e):
        with self.lock:
            if self.meter is not None:
                try:
                    self.meter.close()
                except Exception:  # noqa: BLE001
                    pass
                self.meter = None
            self.meter_state = "error"
            self.meter_error = {"message": e.message, "detail": e.detail, "kind": e.kind}
            self.retry_count += 1
            delay = 10 if self.retry_count < 6 else 30
            self.retry_at = time.monotonic() + delay
            if self.jobs:
                self.jobs.clear()
                self.notice("bad", "Reading cancelled: " + e.message)
            self._log("\n[ProjectorCal] error: %s\n" % e.message)
            self.cond.notify_all()

    def _read_one(self):
        """One reading with error handling. Returns XYZ or None."""
        self._mock_follow()
        m = self.meter
        t_start = time.monotonic()
        try:
            v = m.read()
            self.last_interval = (t_start, time.monotonic())   # for the auto run: when integration happened
        except MeterError as e:
            if e.kind == "failed" and m.alive:
                with self.lock:
                    self.fail_streak += 1
                    self.notice("warn", e.message)
                    if self.fail_streak >= 3:
                        self._meter_failed(MeterError("Three failed readings in a row. Check the meter and press "
                                                      "“Retry”.", e.detail, kind="failed"))
                return None
            self._meter_failed(e)
            return None
        except Exception as e:  # noqa: BLE001
            self._meter_failed(MeterError("Meter communication failure: %s" % e, kind="io"))
            return None
        if not all(isinstance(c, float) and math.isfinite(c) for c in v):
            with self.lock:
                self.notice("warn", "The meter returned an invalid value, reading skipped.")
            return None
        with self.lock:
            self.fail_streak = 0
            self.last = v
            self.last_t = time.time()
        return v

    # ================================================================ worker loop
    def _continuous(self):
        a = self.activity
        if not a:
            return False
        if a["type"] in ("live", "free"):
            return True
        if a["type"] == "pass":
            return a.get("auto") and self._pass_index() < len(self._pass_list())
        if a["type"] == "auto":
            return True
        return False

    def _next_action(self):
        if self._restart_meter:
            self._restart_meter = False
            return "start"
        st = self.meter_state
        if st == "off" and self.want_meter:
            return "start"
        if st == "error" and self.want_meter and time.monotonic() >= self.retry_at:
            return "start"
        if st == "calibration" and self.calib_continue:
            return "calib"
        if st == "ready":
            if self.meter is not None and not self.meter.alive and self.meter.name != "argyll-oneshot":
                self._meter_failed(MeterError("spotread exited (meter disconnected?). Plug the meter back in — "
                                              "reconnection is automatic — or press “Retry”.",
                                              kind="eof"))
                return None
            if self.jobs:
                return ("job", self.jobs.popleft())
            if self._continuous():
                return "read"
        return None

    def _run(self):
        while True:
            with self.lock:
                if self.stop:
                    break
                action = self._next_action()
                if action is None:
                    self.cond.wait(0.5)
                    continue
                self.meter_busy = action != "start"
            try:
                if action == "start":
                    self._do_start()
                elif action == "calib":
                    self._do_calib_continue()
                elif action == "read":
                    v = self._read_one()
                    if v is not None:
                        self._on_continuous(v)
                else:
                    self._do_job(action[1])
            except Exception as e:  # noqa: BLE001 — the meter thread must not die
                self._meter_failed(MeterError("Internal error: %s" % e, kind="internal"))
            finally:
                with self.lock:
                    self.meter_busy = False
        if self.meter is not None:
            self.meter.close()
            self.meter = None

    # ================================================================ continuous readings
    def _push_rolling(self, v):
        if self.rolling:
            n = len(self.rolling)
            mean = tuple(sum(r[i] for r in self.rolling) / n for i in range(3))
            if self.detector.changed_from(mean, v):
                self.rolling = []
        self.rolling.append(v)
        self.rolling = self.rolling[-self._window():]

    def _rolling_mean(self):
        if not self.rolling:
            return None
        n = len(self.rolling)
        return tuple(sum(r[i] for r in self.rolling) / n for i in range(3))

    def _on_continuous(self, v):
        with self.lock:
            self._push_rolling(v)
            a = self.activity
            if not a:
                return
            if a["type"] == "live":
                self._live_checks()
            elif a["type"] == "pass":
                self._pass_on_reading(v)
            elif a["type"] == "auto":
                self._auto_on_reading(v)

    def _live_checks(self):
        a = self.activity
        pid = a.get("patchId")
        if a["stepId"] != "gray" or not pid or len(self.rolling) < self._window():
            return
        if pid == self.catalog.sdr_gray_by_stim(100) and self.session.get("whiteY"):
            mean = self._rolling_mean()
            # only if white is plausibly on screen: otherwise it is just another patch, not drift
            if 0.75 <= mean[1] / self.session["whiteY"] <= 1.33:
                self._check_drift(mean[1])

    def _check_drift(self, Y):
        d = A.brightness_drift(self.session, Y)
        if d is not None:
            if self.drift is None:
                self._event("White luminance changed by %+.1f%%" % (d * 100))
            self.drift = {"Y": round(Y, 3), "whiteY": self.session.get("whiteY"), "pct": round(d * 100, 1)}
        elif self.drift is not None and abs(Y / self.session["whiteY"] - 1) < 0.03:
            self.drift = None

    # ================================================================ patch pass
    def _pass_list(self):
        a = self.activity
        if not a or a["type"] != "pass":
            return []
        return pass_patches(self.catalog, a["stepId"], self.session["steps"][a["stepId"]])

    def _pass_index(self):
        a = self.activity
        if not a or a["type"] != "pass":
            return 0
        return self.session["steps"][a["stepId"]].get("idx", 0)

    def _pass_current(self):
        lst = self._pass_list()
        i = self._pass_index()
        return lst[i] if 0 <= i < len(lst) else None

    def _prev_in_pass(self, step_id):
        st = self.session["steps"][step_id]
        ms = [m for m in self.session["measurements"] if m["stepId"] == step_id and m.get("pass", 1) == st["pass"]]
        return ms[-1] if ms else None

    def _pass_on_reading(self, v):
        a = self.activity
        pid = self._pass_current()
        if pid is None or not a.get("auto") or self.jobs or self.job_running:
            return  # during a manual reading the auto detector stays silent so as not to record a patch twice
        det = self.detector
        det.add(v)
        if self.pass_ref is not None and not det.changed_from(self.pass_ref, v):
            det.reset()
            self.wrong = None
            now = time.monotonic()
            if self.pass_state.get("state") != "waiting":
                self.waiting_since = now
            text = "Waiting for the patch to change: play %s." % pid
            if now - self.waiting_since > 10:
                text = ("The readings have not changed for %d s. If %s is already on screen (e.g. steps in the "
                        "clipping zone are identical), press “Measure now”." % (now - self.waiting_since, pid))
            self.pass_state = {"state": "waiting", "text": text}
            return
        if not det.is_stable():
            self.pass_state = {"state": "settling", "text": "Waiting for stability…"}
            return
        mean, sd = det.mean(), det.sd_pct()
        patch = self.catalog.get(pid)
        bad = A.plausibility(patch, mean, self.session, self.catalog, self._prev_in_pass(a["stepId"]))
        if bad:
            self.wrong = {"patchId": pid, "XYZ": mean, "sd": sd, "n": len(det.last()), "text": bad["text"],
                          "guess": bad.get("guess")}
            self.pass_state = {"state": "wrong", "text": bad["text"]}
            return
        self._record_pass(pid, mean, sd, len(det.last()), auto=True)

    def _record_pass(self, pid, XYZ, sd, n, auto, flags=None):
        a = self.activity
        step_id = a["stepId"]
        m = self._add_measurement(step_id, pid, XYZ, sd, n, auto, flags)
        self.pass_ref = XYZ
        self.wrong = None
        self.detector.reset()
        st = self.session["steps"][step_id]
        st["idx"] = st.get("idx", 0) + 1
        nxt = self._pass_current()
        self.pass_state = {"state": "recorded",
                           "text": "Recorded %s: %.4g nits. %s" % (
                               pid, m["Y"], ("Next: " + nxt) if nxt else "Pass complete.")}
        if pid == self.catalog.sdr_gray_by_stim(100) and step_id != "laser":
            self._check_drift(XYZ[1])
        self._touch()

    def _add_measurement(self, step_id, pid, XYZ, sd, n, auto, flags=None):
        st = self.session["steps"][step_id]
        m = {"id": uuid.uuid4().hex[:10], "patchId": pid, "stepId": step_id, "pass": st.get("pass", 1),
             "timestamp": now_iso(), "X": XYZ[0], "Y": XYZ[1], "Z": XYZ[2], "samples": n,
             "sdYpercent": round(sd, 3), "auto": bool(auto), "flags": list(flags or []),
             "meter": self.settings.get("meterMode")}
        self.session["measurements"].append(m)
        if st["status"] == "todo":
            st["status"] = "active"
        return m

    # ================================================================ jobs (manual readings)
    def _do_job(self, job):
        n = job.get("n") or self._window()
        vals = []
        with self.lock:
            self.job_running = {"purpose": job["purpose"], "patchId": job.get("patchId"), "i": 0, "n": n}
        try:
            for i in range(n):
                v = self._read_one()
                with self.lock:
                    if self.meter_state != "ready":
                        return  # the meter dropped out — _meter_failed has already reported it
                    if v is not None:
                        vals.append(v)
                        self._push_rolling(v)
                    self.job_running["i"] = i + 1
        finally:
            with self.lock:
                self.job_running = None
        if not vals:
            with self.lock:
                self.notice("bad", "The reading failed.")
            return
        k = len(vals)
        mean = tuple(sum(v[i] for v in vals) / k for i in range(3))
        Ys = [v[1] for v in vals]
        sd = (math.sqrt(sum((y - mean[1]) ** 2 for y in Ys) / k) / mean[1] * 100) if k > 1 and mean[1] > 0 else 0.0
        with self.lock:
            if job["purpose"] != "test" and not self._job_consistent(vals, mean):
                spread = (max(Ys) - min(Ys)) / mean[1] * 100 if mean[1] > 0 else 0
                txt = ("The picture changed during the reading (spread %.0f%%) — reading NOT recorded. Wait until the "
                       "patch is steady and do not switch it until “Recorded” appears." % spread)
                self.notice("bad", txt)
                if job["purpose"] == "pass":
                    self.pass_state = {"state": "wrong", "text": txt}
                self._touch()
                return
            self._apply_job(job, mean, sd, k)

    def _job_consistent(self, vals, mean):
        """All samples of one reading must come from one patch (otherwise it is a mix of two pictures)."""
        if len(vals) < 2 or mean[1] <= 0:
            return True
        tol = max(0.02, 3 * y_tolerance(mean[1], self.settings.get("stabilityPct", 0.7)))
        if max(abs(v[1] / mean[1] - 1) for v in vals) > tol:
            return False
        if mean[1] >= 1.0:
            xs = [C.xy(v) for v in vals]
            if max(p[0] for p in xs) - min(p[0] for p in xs) > 0.004 or \
                    max(p[1] for p in xs) - min(p[1] for p in xs) > 0.004:
                return False
        return True

    def _apply_job(self, job, mean, sd, n):
        purpose = job["purpose"]
        if purpose == "test":
            d = A.derive(mean)
            self.notice("ok", "Test reading: Y = %.4g nits, x = %.4f, y = %.4f, spread ±%.2f%%"
                        % (mean[1], d["x"], d["y"], sd))
            self.session["steps"]["prep"]["checklist"]["_testOk"] = True
            self._touch()
            return
        if purpose == "ambient":
            self.session["ambientY"] = mean[1]
            self.session["ambientAt"] = now_iso()
            black = None
            for m in reversed(self.session["measurements"]):
                if m["patchId"] == "SDR_21" and A.valid(m):
                    black = m["Y"]
                    break
            if mean[1] < 0.003:
                txt = "Ambient light %.4f nits — the room is dark, shadows can be measured." % mean[1]
            else:
                txt = ("Ambient light %.4f nits%s. Shadows and black will be measured with this addition — close the "
                       "curtains, switch off lights, calibrate shadows at night." % (mean[1], (" — that is %d%% of the projector black" %
                                                                 (mean[1] / black * 100)) if black else ""))
            self._event("Room ambient light: %.4f nits" % mean[1])
            self.notice("ok" if mean[1] < 0.003 else "warn", txt)
            self._touch()
            return
        if purpose == "fixwhite":
            x, y = C.xy(mean)
            if mean[1] < 1.0 or math.hypot(x - C.D65_XY[0], y - C.D65_XY[1]) > 0.04:
                self.notice("bad", "Not fixed: the screen is not white (Y %.3g nits, x %.4f, y %.4f). Play "
                                   "SDR_01 and wait for the player UI to disappear." % (mean[1], x, y))
                return
            self.session["whiteY"] = mean[1]
            self.session["whiteFixedAt"] = now_iso()
            if job.get("laserLevel") not in (None, ""):
                self.session["laserLevel"] = str(job["laserLevel"])
            self._add_measurement("laser", self.catalog.sdr_gray_by_stim(100), mean, sd, n, False)
            self.session["steps"]["laser"]["status"] = "done"
            self._event("White fixed at %.2f nits, laser level %s" % (mean[1], self.session["laserLevel"]))
            self.drift = None
            self.notice("ok", "Fixed: white %.1f nits (spread ±%.2f%%)." % (mean[1], sd))
            self._touch()
            return
        if purpose == "pass":
            a = self.activity
            pid = self._pass_current()
            # the patch was fixed at the key press: if the step/patch changed since, record nowhere
            if not a or a["type"] != "pass" or a["stepId"] != job["stepId"] or pid != job["patchId"]:
                self.notice("warn", "The step or patch changed during the reading — reading not recorded.")
                return
            bad = A.plausibility(self.catalog.get(pid), mean, self.session, self.catalog,
                                 self._prev_in_pass(a["stepId"]))
            if bad:
                # as in auto mode: do not record silently, ask
                self.wrong = {"patchId": pid, "XYZ": mean, "sd": sd, "n": n, "text": bad["text"],
                              "guess": bad.get("guess"), "manual": True}
                self.pass_state = {"state": "wrong", "text": bad["text"]}
                self._touch()
                return
            self._record_pass(pid, mean, sd, n, auto=False)

    # ================================================================ simulation
    def _mock_follow(self):
        if self.settings.get("meterMode") != "mock":
            return
        with self.lock:
            p = self.projector
            if not p.follow:
                return
            a = self.activity
            want = None
            if a and a["type"] == "live":
                want = a.get("patchId")
            elif a and a["type"] == "pass":
                want = self._pass_current()
            if want is None:
                return
            now = time.monotonic()
            if want != self.mock_target:
                self.mock_target = want
                self.mock_target_t = now
            # the "user" switches the file in Plex in ~1.5 s, with the Plex menu between patches
            p.displayed = want if now - self.mock_target_t > 1.5 else "PLEX_MENU"

    # ================================================================ navigation
    def _enter_step(self, step_id, initial=False):
        if step_id not in STEP_IDS:
            step_id = "prep"
        self.session["currentStep"] = step_id
        st = self.session["steps"][step_id]
        if st["status"] == "todo" and step_id != "prep":
            st["status"] = "active"
        self.wrong = None
        self.detector.reset()
        self.rolling = []
        if step_id in LIVE_STEPS:
            self.activity = {"type": "live", "stepId": step_id, "patchId": self.catalog.sdr_gray_by_stim(100)}
        elif step_id == "gpoints" and st.get("stage") not in ("base", "check"):
            self.activity = None    # gamma wizard: preparation/recommendations/result — no readings
        elif step_id == "gray" and st.get("livePatch") in self.catalog.by_id:
            # live adjustment inside the "Gray scale" step (tweaking Gain/Offset/gamma on one patch)
            self.activity = {"type": "live", "stepId": step_id, "patchId": st["livePatch"]}
        elif step_id in PASS_STEPS:
            auto = st.get("auto")
            if auto is None:
                auto = bool(self.settings.get("autoDefault", True)) and step_id not in MANUAL_DEFAULT
                st["auto"] = auto
            self.activity = {"type": "pass", "stepId": step_id, "auto": auto}
            self._reset_pass_ref()
        else:
            self.activity = None
        self.pass_state = {"state": "idle", "text": ""}
        if not initial:
            self._touch()
        else:
            self.store.save(self.session)

    def _reset_pass_ref(self):
        """Reference for "patch change" — the last recorded reading of the current pass."""
        a = self.activity
        prev = self._prev_in_pass(a["stepId"])
        self.pass_ref = (prev["X"], prev["Y"], prev["Z"]) if prev else None
        self.detector.reset()

    # ================================================================ public actions (from HTTP)
    def goto(self, step_id):
        with self.lock:
            if self.autorun is not None:
                if step_id != "full":
                    self.notice("warn", "A full check is running — wait for it to finish or press “Stop”.")
                return
            self.jobs.clear()
            self._enter_step(step_id)

    def set_step(self, step_id, d):
        with self.lock:
            st = self.session["steps"][step_id]
            if "status" in d and d["status"] in ("todo", "active", "done", "skipped", "recheck"):
                st["status"] = d["status"]
            if "notes" in d:
                st["notes"] = str(d["notes"])[:5000]
            if "checklist" in d and isinstance(d["checklist"], dict):
                st["checklist"].update({str(k): bool(v) for k, v in d["checklist"].items()})
            restart_pass = False
            for k in ("quick", "sweeps", "full"):
                if k in d and bool(d[k]) != bool(st.get(k)):
                    st[k] = bool(d[k])
                    restart_pass = True
            if restart_pass:
                # patch set changed: the current position is the first unmeasured patch of the new set
                self._seek_first_unmeasured(step_id)
            self._touch()

    def _seek_first_unmeasured(self, step_id):
        st = self.session["steps"][step_id]
        lst = pass_patches(self.catalog, step_id, st)
        done = {m["patchId"] for m in self.session["measurements"]
                if m["stepId"] == step_id and m.get("pass", 1) == st["pass"]}
        st["idx"] = next((i for i, p in enumerate(lst) if p not in done), len(lst))
        if self.activity and self.activity.get("stepId") == step_id:
            self.wrong = None
            self.detector.reset()

    def set_fields(self, d):
        with self.lock:
            s = self.session
            if "gamma" in d and d["gamma"] in ("2.2", "2.3", "2.4", "bt1886"):
                s["gamma"] = d["gamma"]
            if "laserLevel" in d:
                s["laserLevel"] = (str(d["laserLevel"]).strip() or None) if d["laserLevel"] is not None else None
            if "name" in d and str(d["name"]).strip():
                s["name"] = str(d["name"]).strip()[:200]
            if "projectorProfile" in d and isinstance(d["projectorProfile"], dict):
                s["projectorProfile"].update({k: d["projectorProfile"][k] for k in
                                              ("name", "screenSizeInches", "screenGain", "notes", "model", "gpCount")
                                              if k in d["projectorProfile"]})
                if s["projectorProfile"].get("model") not in PR.PROJECTOR_BY_ID:
                    s["projectorProfile"]["model"] = "vidda-other"
                s["projectorProfile"]["gpCount"] = PR.gp_count(s["projectorProfile"])
                self._apply_profile()
            if "menuValues" in d and isinstance(d["menuValues"], dict):
                for mode, vals in d["menuValues"].items():
                    if mode in s["menuValues"] and isinstance(vals, dict):
                        for k, v in vals.items():
                            k, v = str(k), str(v)[:200]
                            old = s["menuValues"][mode].get(k, "")
                            s["menuValues"][mode][k] = v
                            if old != v:
                                self._menu_changed(mode, k, old, v)
            if "whiteY" in d:
                try:
                    w = float(d["whiteY"])
                    if w > 0:
                        s["whiteY"] = w
                        s["whiteFixedAt"] = now_iso()
                        self._event("White set manually: %.2f nits" % w)
                except (TypeError, ValueError):
                    pass
            self._touch()

    def _menu_changed(self, mode, key, old, new):
        """A menu change makes dependent steps outdated: mark them "recheck"."""
        deps = ("hdr",) if mode == "hdr10" else MENU_DEPS.get(key, ())
        marked = []
        for sid in deps:
            st = self.session["steps"].get(sid)
            if st and st["status"] == "done":
                st["status"] = "recheck"
                marked.append(sid)
        self._event("Menu %s: %s %s → %s" % (mode.upper(), key, old or "—", new or "—"))
        self.session["lastChangeAt"] = now_iso()
        if marked:
            names = {"gray": "gray scale", "gpoints": "gamma by points", "color": "colors", "verify": "SDR verification", "hdr": "HDR10"}
            self.notice("warn", "Changed “%s” — recheck: %s." % (key, ", ".join(names[m] for m in marked)))

    # ================================================================ "Gamma by points" wizard
    # The projector applies a gamma point change only after leaving the menu, and the menu spills light into the meter,
    # so points are not tuned live: full pass → the program computes corrections for all points at once →
    # set them, leave the menu → check pass → refinement. The program keeps the point values.
    def _gp_points_text(self, st):
        ch = ["%s:%+d" % (k, v) for k, v in sorted(st["values"].items(), key=lambda kv: int(kv[0])) if v]
        return ", ".join(ch) if ch else "all 0"

    def _gp_an(self, pass_no):
        import copy
        s2 = dict(self.session)
        s2["steps"] = copy.deepcopy(self.session["steps"])
        s2["steps"]["gpoints"]["pass"] = pass_no
        return A.analyze_gray(s2, self.catalog, "gpoints")

    def _gp_finish(self, st):
        st["stage"] = "done"
        st["status"] = "done"
        self.session["menuValues"]["sdr"]["Gamma Control Points"] = self._gp_points_text(st)
        self._event("Gamma by points done: %s" % self._gp_points_text(st))
        self.notice("ok", "Gamma done. Points: %s — saved to the menu values." % self._gp_points_text(st))

    def _gp_pass_done(self, st=None):
        st = st or self.session["steps"]["gpoints"]
        done = {m["patchId"] for m in A.step_measurements(self.session, "gpoints", st["pass"])}
        need = [p for p in pass_patches(self.catalog, "gpoints", st) if p not in ("SDR_20", "SDR_21")]
        return all(p in done for p in need)

    def _gp_new_pass(self, st, stage):
        st["pass"] = st.get("pass", 1) + (1 if A.step_measurements(self.session, "gpoints", st.get("pass", 1)) else 0)
        st["idx"] = 0
        st["stage"] = stage

    def _gp_make_plan(self, st):
        an = A.analyze_gray(self.session, self.catalog, "gpoints")
        # refine the sensitivity from the actual result of the previous round
        ap = st.get("applied")
        if ap and ap.get("pass") and ap["pass"] != st["pass"] and ap.get("deltas"):
            lr = A.gp_learn(self._gp_an(ap["pass"]), an, ap["deltas"])
            if lr:
                st["learn"] = lr
                if lr["used"]:
                    st["perStep"] = lr["perStep"]
        pr = st.get("probe")
        if pr and pr.get("basePass") and not pr.get("result"):
            pr["result"] = A.gp_probe(self._gp_an(pr["basePass"]), an, pr["delta"])
            self._event("Point probe %s (%+d): %s" % (pr["point"], pr["delta"], pr["result"]))
        st["plan"] = A.gp_plan(an, st["values"], st.get("perStep") or A.GP_PER_STEP)
        st["planPass"] = st["pass"]
        st["queue"] = A.gp_queue(an)
        st["stage"] = "plan"

    def gp_action(self, action, d=None):
        d = d or {}
        with self.lock:
            st = self.session["steps"]["gpoints"]
            if st.get("stage") == "point":          # v1.2 sessions: live point mode is no longer used
                st["stage"] = "plan" if st.get("plan") else "prep"
            mv = self.session["menuValues"]["sdr"]
            if action in ("start", "continue"):
                if action == "start":
                    st["values"] = {str(k): 0 for k in range(1, 21)}
                elif isinstance(d.get("values"), dict):
                    for k, v in d["values"].items():
                        try:
                            st["values"][str(int(k))] = max(-50, min(50, int(v)))
                        except (TypeError, ValueError):
                            pass
                for k in range(1, 21):
                    st["values"].setdefault(str(k), 0)
                st["started"] = True
                st["preset"] = str(d.get("preset", st.get("preset", "")))[:60]
                st["round"] = 1
                st["applied"] = None
                st["probe"] = None
                st["plan"] = []
                if st["preset"]:
                    mv["Gamma"] = st["preset"]
                mv["Gamma Control Points"] = self._gp_points_text(st)
                self._gp_new_pass(st, "base")
                self._event("Gamma: %s, preset “%s”, points: %s" % (
                    "from scratch" if action == "start" else "fine-tuning current", st["preset"] or "—",
                    self._gp_points_text(st)))
                self.session["lastChangeAt"] = now_iso()
            elif action == "plan":
                if not self._gp_pass_done(st):
                    self.notice("warn", "First finish the full pass (all steps 10–100 %).")
                    return
                self._gp_make_plan(st)
            elif action == "value":
                k = str(int(d["point"]))
                v = max(-50, min(50, int(d["value"])))
                old = st["values"].get(k, 0)
                if old != v:
                    st["values"][k] = v
                    self._event("Gamma: point %s = %+d (was %+d), manually" % (k, v, old))
                    self.session["lastChangeAt"] = now_iso()
                    mv["Gamma Control Points"] = self._gp_points_text(st)
            elif action == "applied":
                # the user set the recommended values and left the menu
                deltas = {}
                for r in st.get("plan", []):
                    if r["delta"]:
                        deltas[str(r["point"])] = r["new"] - r["now"]
                        st["values"][str(r["point"])] = r["new"]
                if not deltas:
                    self.notice("warn", "No corrections — everything is within tolerance.")
                    return
                st["applied"] = {"pass": st["pass"], "deltas": deltas}
                st["round"] = st.get("round", 1) + 1
                mv["Gamma Control Points"] = self._gp_points_text(st)
                self._event("Gamma, round %d: %s → %s" % (st["round"] - 1, ", ".join(
                    "%s:%+d" % kv for kv in sorted(deltas.items(), key=lambda kv: int(kv[0]))),
                    self._gp_points_text(st)))
                self.session["lastChangeAt"] = now_iso()
                self._gp_new_pass(st, "check")
            elif action == "probe":
                k, delta = int(d["point"]), int(d.get("delta", -10))
                if not (1 <= k <= 20) or not delta:
                    return
                st["probe"] = {"point": k, "delta": delta, "basePass": st["pass"], "result": None,
                               "restore": st["values"].get(str(k), 0)}
                st["values"][str(k)] = st["values"].get(str(k), 0) + delta
                st["applied"] = None
                self._event("Point probe %d: %+d" % (k, delta))
                self.session["lastChangeAt"] = now_iso()
                self._gp_new_pass(st, "check")
            elif action == "probeRestore":
                pr = st.get("probe")
                if pr:
                    st["values"][str(pr["point"])] = pr["restore"]
                    self._event("Point probe %s finished, value restored to %+d" % (pr["point"], pr["restore"]))
                    st["probe"] = None
                    self.session["lastChangeAt"] = now_iso()
                    self._gp_new_pass(st, "check")
            elif action == "finish":
                self._gp_finish(st)
            elif action == "reset":
                st.update({"stage": "prep", "started": False, "plan": [], "probe": None, "applied": None})
            self._enter_step("gpoints")

    # ================================================================ full check (auto run)
    def _speed(self):
        if self.settings.get("meterMode") == "mock":
            return float(os.environ.get("PROJECTORCAL_MOCK_SPEED", "1"))
        return 1.0

    def _apply_profile(self):
        """Gamma point layout of the session's projector (20 × 5 %, 10 × 10 % or none)."""
        A.set_gp_layout(PR.gp_count(self.session.get("projectorProfile")))
        self.full_cache = None

    def auto_speed(self):
        """Which auto-run video to play: "std" (i1Display Pro/Plus timing) or "slow" (twice the time per reading)."""
        sp = self.settings.get("autoSpeed") or ""
        if sp in SEQ.SPEEDS:
            return sp
        mid = self.settings.get("meterProfile") or ""
        return PR.METER_BY_ID[mid]["speed"] if mid in PR.METER_BY_ID else "std"

    def full_start(self, mode):
        with self.lock:
            if mode not in ("sdr", "hdr"):
                return
            if self.meter_state != "ready":
                self.notice("bad", "Meter is not ready.")
                return
            seq = SEQ.build(self.catalog, mode, self.auto_speed())
            self.autorun = self._make_autorun(seq, speed=self._speed())
            self.auto_meta = {"mode": mode, "startedAt": now_iso(), "t": time.monotonic(), "file": seq["file"],
                              "duration": seq["duration"], "settings": json.loads(json.dumps(self.session["psettings"]))}
            self.jobs.clear()
            self.activity = {"type": "auto", "stepId": "full", "mode": mode}
            self.detector.reset()
            self.rolling = []
            if self.settings.get("meterMode") == "mock" and self.projector.follow:
                self.projector.start_playback(seq, delay=3.0, speed=self._speed())
            self._event("Full check %s: start" % mode.upper())
            self.cond.notify_all()
            self._touch()

    def _make_autorun(self, seq, speed=1.0, raw=None):
        wy = self.session.get("whiteY") if seq["mode"] == "sdr" else None
        kw = dict(expected=SEQ.expected_rel(self.catalog, seq), expected_xy=SEQ.expected_xy(self.catalog, seq),
                  white_hint=wy)
        if raw is not None:
            return AutoRun.from_raw(seq, raw, **kw)
        return AutoRun(seq, speed=speed, **kw)

    def _auto_on_reading(self, v):
        ar = self.autorun
        if ar is None or self.last_interval is None:
            return
        t0, t1 = self.last_interval
        ar.feed(t0, t1, v)
        waited = time.monotonic() - self.auto_meta["t"]
        if ar.state == "arming" and waited > 180:
            self.notice("bad", "No start mark found within 3 minutes. The video did not start or the meter is not "
                               "looking at the screen. Check stopped.")
            self._full_finish(save=False)
        elif ar.state == "done":
            self._full_finish(save=True)
        elif ar.state == "running" and ar.file_time(t1) > ar.seq["duration"] + 20:
            self._full_finish(save=True)

    def full_stop(self, save=True):
        with self.lock:
            if self.autorun is not None:
                self._full_finish(save=save)

    def _full_finish(self, save):
        ar, meta = self.autorun, self.auto_meta
        self.autorun, self.auto_meta = None, None
        self.activity = None
        if self.settings.get("meterMode") == "mock":
            self.projector.stop_playback()
        if not save or ar is None:
            self._touch()
            return
        res = ar.analyze()
        if not res["ok"]:
            self.notice("bad", res["error"])
            self._touch()
            return
        step = "auto" if meta["mode"] == "sdr" else "autohdr"
        st = self.session["steps"][step]
        prev = [r for r in self.session["runs"] if r["mode"] == meta["mode"]]
        st["pass"] = (max(r["pass"] for r in prev) + 1) if prev else 1
        run_id = "%s-%d" % (meta["mode"].upper(), st["pass"])
        run = {"id": run_id, "mode": meta["mode"], "pass": st["pass"], "startedAt": meta["startedAt"],
               "finishedAt": now_iso(), "file": meta["file"], "settings": meta["settings"],
               # schedule and raw readings (file time) — the run can be re-analyzed by a newer program version
               "seq": {k: ar.seq[k] for k in ("mode", "version", "speed", "segments", "anchor", "endAnchor", "duration",
                                               "file") if k in ar.seq},
               "raw": ar.raw()}
        used = self._store_run_result(run, res, step)
        self.session["runs"].append(run)
        segs = run["segments"]
        total = len([x for x in segs if x["role"] == "patch"])
        bad = total - used
        self._event("Full check %s: %d of %d patches%s" % (run_id, used, total,
                                                                 (", %d unreliable" % bad) if bad else ""))
        self.notice("ok" if not bad else "warn", "Check %s done: %d of %d patches%s." % (
            run_id, used, total, (" — %d not counted, see the list" % bad) if bad else ""))
        self.full_cache = None
        self._touch()

    def _store_run_result(self, run, res, step):
        """Analysis result → run segments and step measurements. White and black, if their patch failed, are taken from
        service segments with the same picture (mark, drift check)."""
        from .autorun import reliable
        run_id = run["id"]
        st = self.session["steps"][step]
        saved_pass, st["pass"] = st.get("pass", 1), run["pass"]
        try:
            return self._store_run_result_inner(run, res, step, reliable, run_id)
        finally:
            st["pass"] = saved_pass

    def _store_run_result_inner(self, run, res, step, reliable, run_id):
        self.session["measurements"] = [m for m in self.session["measurements"]
                                        if not ("run:" + run_id) in (m.get("flags") or [])]
        used = 0
        got = set()
        for sg in res["segments"]:
            if sg["role"] == "patch" and reliable(sg):
                self._add_measurement(step, sg["patchId"], sg["XYZ"], sg["sd"] or 0.0, sg["n"], True,
                                      ["run:" + run_id])
                got.add(sg["patchId"])
                used += 1
        for sg in res["segments"]:
            pid = sg["patchId"]
            if pid in got or sg["role"] == "patch" or not sg.get("XYZ") or sg["n"] < 2:
                continue
            if any(x["patchId"] == pid and x["role"] == "patch" for x in res["segments"]) and \
                    "implausible" not in sg.get("flags", []):
                self._add_measurement(step, pid, sg["XYZ"], sg["sd"] or 0.0, sg["n"], True,
                                      ["run:" + run_id, "from " + {"mark": "mark", "drift": "drift check",
                                                                  "end": "finish"}.get(sg["role"], sg["role"])])
                got.add(pid)
                for x in res["segments"]:
                    if x["patchId"] == pid and x["role"] == "patch":
                        x["flags"] = [f for f in x["flags"] if f != "no readings"] + ["taken from %s" % (
                            {"mark": "mark", "drift": "drift check", "end": "finish"}.get(sg["role"], sg["role"]))]
                        x["XYZ"], x["n"], x["sd"] = sg["XYZ"], sg["n"], sg["sd"]
                used += 1
        run["segments"] = [{k: sg[k] for k in ("i", "patchId", "role", "start", "dur", "XYZ", "n", "sd", "flags")}
                           for sg in res["segments"]]
        run["sync"], run["white"], run["used"] = res["sync"], res["white"], used
        return used

    def full_reanalyze(self, run_id):
        """Re-analyze a stored run from its raw readings (after a program update)."""
        with self.lock:
            run = next((r for r in self.session["runs"] if r["id"] == run_id), None)
            if not run or not run.get("raw") or not run.get("seq"):
                self.notice("warn", "Run %s has no raw readings (recorded by an older version) — cannot re-analyze, "
                                    "make a new run." % run_id)
                return
            ar = self._make_autorun(run["seq"], raw=run["raw"])
            res = ar.analyze()
            if not res["ok"]:
                self.notice("bad", res["error"])
                return
            used = self._store_run_result(run, res, "auto" if run["mode"] == "sdr" else "autohdr")
            self._event("Run %s re-analyzed: %d patches" % (run_id, used))
            self.notice("ok", "Run %s re-analyzed: %d counted." % (run_id, used))
            self.full_cache = None
            self._touch()

    def set_psettings(self, changes):
        """Projector settings changes {path: value} → log, "recheck" marks, gamma point sync."""
        with self.lock:
            ps = self.session["psettings"]
            groups = set()
            for path, val in changes.items():
                if path not in PS.FIELD_BY_PATH:
                    continue
                old, new = PS.set_(ps, path, val)
                if (old or 0) != (new or 0) if PS.FIELD_BY_PATH[path][3] == "int" else (old or "") != (new or ""):
                    groups.add(PS.FIELD_BY_PATH[path][2])
                    self._event("Settings: %s %s → %s" % (PS.FIELD_BY_PATH[path][1], "—" if old in (None, "") else old,
                                                           "—" if new in (None, "") else new))
                    if path.startswith("sdr.gammaPoints."):
                        self.session["steps"]["gpoints"]["values"][path.split(".")[-1]] = new or 0
            if groups:
                self.session["lastChangeAt"] = now_iso()
                deps = {"black": _ALL_SDR, "contrast": _ALL_SDR, "laser": _ALL_SDR + ("hdr",), "wbPreset": _ALL_SDR,
                        "wbGain": _ALL_SDR, "wbOffset": _ALL_SDR, "gammaPreset": ("gray", "gpoints", "verify"),
                        "gamma": ("gpoints", "verify"), "colorSpace": ("color", "verify"),
                        "hdrWb": ("hdr",), "hdrTone": ("hdr",), "hdrColor": ("hdr",),
                        "saturation": ("color", "verify"), "processing": _ALL_SDR, "extra": _ALL_SDR}
                marked = []
                for g in groups:
                    for sid in deps.get(g, ("color", "verify") if g.startswith("cms:") else ()):
                        if self.session["steps"][sid]["status"] == "done":
                            self.session["steps"][sid]["status"] = "recheck"
                            marked.append(sid)
                self._sync_menu_values()
                self.full_cache = None
            self._touch()

    def _sync_menu_values(self):
        """Legacy free-text menu values (for compatibility) — from the structured settings."""
        ps, mv = self.session["psettings"], self.session["menuValues"]
        sdr, hdr = ps["sdr"], ps["hdr"]
        f = lambda v: "" if v is None else str(v)  # noqa: E731
        mv["sdr"].update({"Image Mode": sdr["imageMode"], "Brightness level (laser)": f(sdr["laser"]),
                          "Black Level": f(sdr["blackLevel"]), "Contrast": f(sdr["contrast"]),
                          "Color Temp": sdr["colorTemp"], "Gamma": sdr["gammaPreset"],
                          "Gamma Control Points": PS.gp_text(ps), "Color Space": sdr["colorSpace"],
                          "Sharpness": f(sdr["sharpness"])})
        for c in "RGB":
            mv["sdr"]["Gain " + c] = f(sdr["gain"][c])
            mv["sdr"]["Offset " + c] = f(sdr["offset"][c])
            mv["hdr10"]["Gain " + c] = f(hdr["gain"][c])
            mv["hdr10"]["Offset " + c] = f(hdr["offset"][c])
        for col, key in (("red", "CMS Red"), ("yellow", "CMS Yellow"), ("green", "CMS Green"), ("cyan", "CMS Cyan"),
                         ("blue", "CMS Blue"), ("magenta", "CMS Magenta/Pink")):
            c = sdr["cms"][col]
            mv["sdr"][key] = "%d,%d,%d" % (c["tone"] or 0, c["sat"] or 0, c["bright"] or 0)
        mv["hdr10"]["Image Mode"] = hdr["imageMode"]
        mv["hdr10"]["Brightness level (laser)"] = f(hdr["laser"])
        mv["hdr10"]["Dynamic tone mapping"] = hdr["toneMapping"]

    def full_view(self):
        """Data for the "Full check" screen and the report (cached by revision)."""
        if self.full_cache and self.full_cache[0] == self.rev:
            return self.full_cache[1]
        s = self.session
        runs_an = []
        for r in s["runs"]:
            try:
                an = FT.run_analysis(s, self.catalog, r)
            except Exception as e:  # noqa: BLE001 — one broken run must not break the screen
                an = {"error": str(e), "mode": r["mode"], "metrics": {}}
            runs_an.append((r, an))
        learn_in = [{"id": r["id"], "mode": r["mode"], "settings": r.get("settings"), "metrics": an.get("metrics"),
                     "gray": an.get("gray")} for r, an in runs_an if not an.get("error")]
        know = FT.learn(learn_in)
        sens = FT.sensitivities(know)
        latest = {}
        for r, an in runs_an:
            latest[r["mode"]] = (r, an)
        out = {"runs": [], "latest": {}, "knowledge": FT.explain_knowledge(know),
               "sens": [{"group": PS.group_name(g), "key": k, "value": v} for (g, k), v in sens.items()],
               "speed": self.auto_speed(),
               "sequences": {m: {"file": q["file"], "duration": q["duration"]}
                             for m, q in ((m, SEQ.build(self.catalog, m, self.auto_speed())) for m in ("sdr", "hdr"))}}
        prev_by_mode = {}
        for r, an in runs_an:
            p = prev_by_mode.get(r["mode"])
            out["runs"].append({"id": r["id"], "mode": r["mode"], "startedAt": r["startedAt"], "used": r.get("used"),
                                "sync": r.get("sync"), "metrics": an.get("metrics", {}),
                                "changes": PS.diff(p[0].get("settings"), r.get("settings")) if p else [],
                                "compare": FT.compare(p[1].get("metrics", {}), an.get("metrics", {})) if p else []})
            prev_by_mode[r["mode"]] = (r, an)
        for mode, (r, an) in latest.items():
            if an.get("error"):
                continue
            pl = FT.plan(an, s["psettings"], sens)
            out["latest"][mode] = {"run": {"id": r["id"], "startedAt": r["startedAt"], "sync": r.get("sync"),
                                           "settings": r.get("settings"), "white": r.get("white"),
                                           "segments": r.get("segments"), "hasRaw": bool(r.get("raw"))},
                                   "analysis": an, "plan": pl, "verdict": FT.verdict(an, pl, s["psettings"]),
                                   "settingsChanged": PS.diff(r.get("settings"), s["psettings"])}
        self.full_cache = (self.rev, out)
        return out

    def gray_live(self, pid):
        """Enter/leave live adjustment in the "Gray scale" step (pid=None — back to the circle)."""
        with self.lock:
            st = self.session["steps"]["gray"]
            st["livePatch"] = pid if pid in self.catalog.by_id else None
            self.jobs.clear()
            self._enter_step("gray")

    def pass_action(self, step_id, action, arg=None):
        with self.lock:
            a = self.activity
            if not a or a["type"] != "pass" or a["stepId"] != step_id:
                self._enter_step(step_id)
                a = self.activity
            st = self.session["steps"][step_id]
            lst = pass_patches(self.catalog, step_id, st)
            if action == "auto":
                st["auto"] = bool(arg)
                a["auto"] = bool(arg)
                self._reset_pass_ref()
                self.wrong = None
                self.pass_state = {"state": "idle", "text": ""}
            elif action == "select":
                i = int(arg)
                if 0 <= i <= len(lst):
                    st["idx"] = i
                    self.wrong = None
                    self.detector.reset()
                    self.pass_state = {"state": "idle", "text": ""}
            elif action == "skip":
                st["idx"] = min(st.get("idx", 0) + 1, len(lst))
                self.wrong = None
                self.detector.reset()
            elif action == "back":
                st["idx"] = max(st.get("idx", 0) - 1, 0)
                self.wrong = None
                self.detector.reset()
            elif action == "new":
                st["pass"] = st.get("pass", 1) + 1
                st["idx"] = 0
                self.pass_ref = None
                self.wrong = None
                self.detector.reset()
                self.pass_state = {"state": "idle", "text": "New pass #%d" % st["pass"]}
                self.notice("ok", "Pass #%d started. Previous readings are kept for comparison." % st["pass"])
            elif action == "accept":
                w = self.wrong
                if w and w["patchId"] == self._pass_current():
                    self._record_pass(w["patchId"], w["XYZ"], w["sd"], w["n"], auto=not w.get("manual"),
                                      flags=["accepted"])
                    return
            elif action == "measure":
                pid = self._pass_current()
                if pid is None:
                    self.notice("warn", "Pass complete. Pick a patch in the list or start a new pass.")
                    return
                if self.meter_state != "ready":
                    self.notice("bad", "Meter is not ready.")
                    return
                if self.jobs or self.job_running:
                    return  # a reading is already running — ignore the repeated press
                self.wrong = None
                self.pass_state = {"state": "measuring", "text": "Measuring %s — do not switch the patch." % pid}
                self.jobs.append({"purpose": "pass", "stepId": step_id, "patchId": pid})
            self._touch()

    def measure(self, purpose, d):
        with self.lock:
            if self.meter_state != "ready":
                self.notice("bad", "Meter is not ready: " + ((self.meter_error or {}).get("message") or
                                                          "connect the meter."))
                return False
            if any(j["purpose"] == purpose for j in self.jobs):
                return True
            job = {"purpose": purpose}
            if purpose not in ("test", "fixwhite", "ambient"):
                return False
            if purpose == "fixwhite":
                job["laserLevel"] = d.get("laserLevel")
            self.jobs.append(job)
            self.cond.notify_all()
            return True

    def free_live(self, on):
        """Live outside the steps (e.g. during preparation)."""
        with self.lock:
            if on:
                if not self.activity:
                    self.activity = {"type": "free", "stepId": self.session["currentStep"]}
            elif self.activity and self.activity["type"] == "free":
                self.activity = None
            self.cond.notify_all()

    def meter_retry(self):
        with self.lock:
            self.want_meter = True
            self.retry_count = 0
            self._restart_meter = True
            self.meter_state = "off" if self.meter_state == "error" else self.meter_state
            self.cond.notify_all()

    def meter_calibration_continue(self):
        with self.lock:
            if self.meter_state == "calibration":
                self.calib_continue = True
                self.cond.notify_all()

    def dismiss(self, what, nid=None):
        with self.lock:
            if what == "drift":
                self.drift = None
            elif what == "notice":
                self.notices = collections.deque((n for n in self.notices if n["id"] != nid), maxlen=6)
            elif what == "acceptWhite" and self.drift:
                self.session["whiteY"] = self.drift["Y"]
                self.session["whiteFixedAt"] = now_iso()
                self._event("New white accepted: %.2f nits" % self.drift["Y"])
                for sid in ("gray", "verify"):
                    if self.session["steps"][sid]["status"] == "done":
                        self.session["steps"][sid]["status"] = "recheck"
                self.drift = None
                self._touch()

    def update_settings(self, d):
        with self.lock:
            restart = False
            for k in ("meterMode", "spotreadPath", "flag", "refresh", "ccmx", "extraArgs", "displayType"):
                if k in d and d[k] != self.settings.get(k):
                    self.settings[k] = d[k] if d[k] is not None else ""
                    restart = True
            if "avg" in d:
                self.settings["avg"] = max(1, min(10, int(d["avg"])))
                self.detector.window = self._window()
            if "stabilityPct" in d:
                self.settings["stabilityPct"] = max(0.2, min(5.0, float(d["stabilityPct"])))
                self.detector.y_pct = self.settings["stabilityPct"]
            for k in ("autoDefault",):
                if k in d:
                    self.settings[k] = bool(d[k])
            if "meterProfile" in d and (d["meterProfile"] or "") in list(PR.METER_BY_ID) + [""]:
                self.settings["meterProfile"] = d["meterProfile"] or ""
                self.full_cache = None
            if "autoSpeed" in d and (d["autoSpeed"] or "") in list(SEQ.SPEEDS) + [""]:
                self.settings["autoSpeed"] = d["autoSpeed"] or ""
                self.full_cache = None
            if "theme" in d and d["theme"] in ("dark", "light", "auto"):
                self.settings["theme"] = d["theme"]
            self.store.save_settings(self.settings)
            self.session["meter"] = {"flag": self.settings.get("flag", "-e"),
                                     "refresh": self.settings.get("refresh") or None,
                                     "ccmx": self.settings.get("ccmx") or None,
                                     "displayType": self.settings.get("displayType") or None,
                                     "profile": self.settings.get("meterProfile") or None}
            if restart:
                self.jobs.clear()
                self.want_meter = True
                self.retry_count = 0
                self._restart_meter = True
            self._touch()

    def mock_update(self, d):
        self.projector.update(d)
        with self.lock:
            self.cond.notify_all()

    def new_session(self, name=None):
        with self.lock:
            self.jobs.clear()
            self.session = new_session(self.settings, name)
            self._apply_profile()
            self.drift = None
            self.store.save(self.session)
            self._enter_step("prep")
            self.notice("ok", "New session created.")

    def open_session(self, sid):
        with self.lock:
            s = self.store.load(sid)
            self.jobs.clear()
            self.session = s
            self._apply_profile()
            self.drift = None
            self._enter_step(s.get("currentStep", "prep"))

    def delete_measurement(self, mid):
        with self.lock:
            before = len(self.session["measurements"])
            self.session["measurements"] = [m for m in self.session["measurements"] if m["id"] != mid]
            if len(self.session["measurements"]) != before:
                if self.activity and self.activity["type"] == "pass":
                    self._reset_pass_ref()
                self._touch()

    def shutdown(self):
        with self.lock:
            self.stop = True
            self.cond.notify_all()
        self.thread.join(timeout=8)
        if self.meter is not None:
            self.meter.close()
            self.meter = None
        try:
            self.store.save(self.session)
        except OSError:
            pass

    # ================================================================ view for the UI
    def state(self):
        self._mock_follow()
        with self.lock:
            self.last_request = time.monotonic()
            a = self.activity
            roll = self._rolling_mean()
            out = {
                "rev": self.rev,
                "meter": {"state": self.meter_state, "error": self.meter_error, "busy": self.meter_busy,
                          "mode": self.settings.get("meterMode"), "exe": self.meter_exe,
                          "retryIn": max(0, round(self.retry_at - time.monotonic()))
                          if self.meter_state == "error" else None,
                          "jobs": len(self.jobs) + (1 if self.job_running else 0),
                          "job": dict(self.job_running) if self.job_running else
                          ({"purpose": self.jobs[0]["purpose"], "patchId": self.jobs[0].get("patchId"), "i": 0,
                            "n": self._window()} if self.jobs else None)},
                "reading": None,
                "activity": dict(a) if a else None,
                "currentStep": self.session["currentStep"],
                "drift": self.drift,
                "notices": [n for n in self.notices
                            if time.time() - n["t"] < (20 if n["level"] == "bad" else 9)],
                "header": {"laserLevel": self.session.get("laserLevel"), "whiteY": self.session.get("whiteY"),
                           "gamma": self.session.get("gamma")},
            }
            if self.last is not None:
                r = {"last": A.derive(self.last), "age": round(time.time() - self.last_t, 1)}
                if roll is not None:
                    r["mean"] = A.derive(roll)
                    r["n"] = len(self.rolling)
                    if len(self.rolling) > 1 and roll[1] > 0:
                        ys = [v[1] for v in self.rolling]
                        r["sd"] = round(math.sqrt(sum((y - roll[1]) ** 2 for y in ys) / len(ys)) / roll[1] * 100, 2)
                    r["stable"] = len(self.rolling) >= self._window() and \
                        r.get("sd", 0) <= 100 * y_tolerance(roll[1], self.settings.get("stabilityPct", 0.7))
                out["reading"] = r
            if a and a["type"] == "live" and a.get("patchId"):
                p = self.catalog.get(a["patchId"])
                exp = A.expected_Y(p, self.session, self.catalog)
                out["live"] = {"patchId": p["id"], "expectedY": exp,
                               "control": "Gain" if p["stim"] >= 0.5 else "Offset"}
                if roll is not None and roll[1] > 0:
                    out["live"]["hints"] = A.balance_hints(C.rgb_balance(roll), out["live"]["control"])
                if out.get("reading", {}) and (out["reading"] or {}).get("sd", 0) > 3:
                    out["live"]["osd"] = ("Readings jump (±%.0f%%) — the projector menu seems to reach the meter. "
                                          "Close the menu or move it away from the center and wait." % out["reading"]["sd"])
                if roll is not None and exp and p["stim"] < 1 and len(self.rolling) >= 2:
                    ratio = roll[1] / exp
                    if not (0.5 <= ratio <= 2.0):
                        g = A.guess_gray(roll, self.session, self.catalog)
                        out["live"]["suspect"] = "Luminance does not look like %s (%.3g nits, expected ≈%.3g)%s" % (
                            p["id"], roll[1], exp, ("; looks like " + g) if g and g != p["id"] else "")
            if a and a["type"] == "pass":
                lst = self._pass_list()
                i = self._pass_index()
                pid = lst[i] if i < len(lst) else None
                st = self.session["steps"][a["stepId"]]
                out["pass"] = {"stepId": a["stepId"], "list": lst, "idx": i, "patchId": pid, "auto": a.get("auto"),
                               "pass": st.get("pass", 1), "state": self.pass_state,
                               "wrong": {k: v for k, v in self.wrong.items() if k != "XYZ"} if self.wrong else None,
                               "expectedY": A.expected_Y(self.catalog.get(pid), self.session, self.catalog)
                               if pid else None}
            if a and a["type"] == "auto" and self.autorun is not None:
                ar = self.autorun
                now = time.monotonic()
                seg = ar.current_segment(now) if ar.state == "running" else None
                out["auto"] = {"mode": a["mode"], "state": ar.state, "file": self.auto_meta["file"],
                               "waited": round(now - self.auto_meta["t"]), "readings": len(ar.readings),
                               "progress": ar.progress(now), "duration": ar.seq["duration"],
                               "fileTime": round(ar.file_time(now), 1) if ar.T0 is not None else None,
                               "patchId": seg["patchId"] if seg else None, "role": seg["role"] if seg else None}
            if self.settings.get("meterMode") == "mock":
                out["mock"] = self.projector.snapshot()
            return out

    def session_view(self):
        with self.lock:
            s = self.session
            an = {
                "black": A.analyze_gray(s, self.catalog, "black"),
                "gray": A.analyze_gray(s, self.catalog, "gray"),
                "verify": A.analyze_gray(s, self.catalog, "verify"),
                "summary": A.analyze_gray(s, self.catalog, "summary"),
                "color": A.analyze_color(s, self.catalog),
                "hdr": A.analyze_hdr(s, self.catalog),
            }
            an["grayPlan"] = A.gray_plan(an["gray"], s, self.catalog)
            an["grayHistory"] = A.pass_history(s, self.catalog, "gray")
            an["gpoints"] = A.analyze_gray(s, self.catalog, "gpoints")
            an["gpointsHistory"] = A.pass_history(s, self.catalog, "gpoints")
            an["gpQueue"] = A.gp_queue(an["gpoints"])
            an["gpPassDone"] = self._gp_pass_done()
            an["full"] = self.full_view()
            an["verifyHistory"] = A.pass_history(s, self.catalog, "verify")
            return {"rev": self.rev, "session": s, "analysis": an, "settings": self.settings,
                    "psFields": PS.fields(), "profiles": PR.public(), "autoSpeed": self.auto_speed(),
                    "platform": platform_name(), "dataDir": self.store.root,
                    "sessions": self.store.list()}

    def log_lines(self):
        with self.lock:
            return list(self.log_tail)


def meter_check_info(settings):
    """For the "Preparation" step: whether spotread is found and what it sees."""
    from .meter import probe_spotread
    exe = find_spotread(settings.get("spotreadPath"))
    if not exe:
        return {"found": False}
    info = probe_spotread(exe)
    info["found"] = True
    info["suggested"], info["ambiguous"] = PR.detect_meter(info.get("instruments"))
    info["displayTypes"] = PR.parse_display_types(info.get("raw"))
    return info

