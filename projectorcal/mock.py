"""Projector and meter simulation (MockMeter) for development and demos without hardware.

Model: patch → linear RGB with the projector gamma and white-balance corrections (Gain/Offset R/G/B
in "menu steps", as on the Vidda: 0 by default) → XYZ through slightly "wide" native laser
primaries → meter noise. The controls are changed on the simulation panel in the UI.
"""
from __future__ import annotations

import random
import threading
import time

from . import colorimetry as C

# Native "laser" primaries (wider than Rec.709) — XYZ at unit channel luminance.
_NATIVE = ((0.4400, 0.3400, 0.1700),
           (0.2150, 0.7120, 0.0730),
           (0.0150, 0.1050, 0.9600))


def _mix(a, b, t):
    return tuple(tuple(a[i][j] * (1 - t) + b[i][j] * t for j in range(3)) for i in range(3))


class MockProjector:
    def __init__(self):
        self.lock = threading.Lock()
        self.white_nits = 140.0
        self.black_nits = 0.07
        self.gamma = 2.25
        self.tint = [1.05, 1.0, 0.93]          # initial bias: warm/reddish
        self.low_tint = [0.96, 1.0, 1.08]      # shadows are cooler
        self.gain = [0, 0, 0]                  # menu steps, 1 step ≈ 0.5 %
        self.offset = [0, 0, 0]                # menu steps
        self.gp = [0] * 20                     # Gamma → Control Point 1…20 → Gain (nodes 5…100 %), as on the Vidda C3S
        self.play = None                       # auto-sequence playback: {"seq", "t0", "speed"}
        self.native = 0.6                      # 0 = Rec.709, 1 = native wide gamut
        self.hdr_peak = 230.0
        self.noise = 0.003
        self.displayed = "SDR_01"              # id of the patch on screen
        self.follow = True                     # the "user" switches to the right patch by themselves

    def snapshot(self):
        with self.lock:
            return {"gain": list(self.gain), "offset": list(self.offset), "gp": list(self.gp),
                    "displayed": self.displayed,
                    "follow": self.follow, "whiteNits": self.white_nits, "gamma": self.gamma,
                    "native": self.native}

    def update(self, d):
        with self.lock:
            for k in ("gain", "offset"):
                if k in d and isinstance(d[k], list) and len(d[k]) == 3:
                    setattr(self, k, [max(-50, min(50, int(v))) for v in d[k]])
            if "gp" in d and isinstance(d["gp"], list) and len(d["gp"]) == 20:
                self.gp = [max(-50, min(50, int(v))) for v in d["gp"]]
            if "displayed" in d and d["displayed"]:
                self.displayed = str(d["displayed"])
            if "follow" in d:
                self.follow = bool(d["follow"])
            for k, attr, lo, hi in (("whiteNits", "white_nits", 5, 2000), ("gamma", "gamma", 1.8, 2.8),
                                    ("native", "native", 0, 1)):
                if k in d:
                    try:
                        setattr(self, attr, max(lo, min(hi, float(d[k]))))
                    except (TypeError, ValueError):
                        pass

    def _gp_corr(self, c):
        """Luminance multiplier from the gamma points: piecewise-linear curve over nodes 0, 5…100 %, 1 step ≈ 2.3 %."""
        nodes = [0.0] + [g * 0.0227 for g in self.gp]
        x = max(0.0, min(1.0, c)) * 20
        i = min(int(x), 19)
        t = x - i
        return pow(2.718281828, nodes[i] * (1 - t) + nodes[i + 1] * t)

    def start_playback(self, seq, delay=2.0, speed=1.0):
        """"The user pressed Play in Plex" after `delay` seconds."""
        import time
        with self.lock:
            self.play = {"seq": seq, "t0": time.monotonic() + delay / speed, "speed": speed}

    def stop_playback(self):
        with self.lock:
            self.play = None

    def playing_patch(self, t):
        """Id of the patch on screen at time t during playback (None — Plex menu before/after the video)."""
        pl = self.play
        if not pl:
            return None
        ft = (t - pl["t0"]) * pl["speed"]
        if ft < 0 or ft >= pl["seq"]["duration"]:
            return "PLEX_MENU"
        for sg in pl["seq"]["segments"]:
            if sg["start"] <= ft < sg["start"] + sg["dur"]:
                return sg["patchId"]
        return "PLEX_MENU"

    def xyz_for(self, patch):
        """Ideal (noise-free) response to a patch."""
        with self.lock:
            if patch is None:  # Plex menu between patches: dark bluish UI
                return (7.0, 7.5, 12.0)
            hdr = patch["mode"] == "hdr10"
            if patch.get("kind") == "test":
                rgb = (0.01, 0.01, 0.01)
            else:
                rgb = patch["rgb"]
            if hdr:
                tn = patch.get("tnits", 0.0)
                peak = self.hdr_peak
                knee = 0.6 * peak
                y = tn if tn <= knee else knee + (peak - knee) * (1 - pow(2.718, -(tn - knee) / (peak - knee)))
                lin = [y / self.white_nits] * 3
            else:
                lin = [max(c, 0.0) ** self.gamma * self._gp_corr(c) for c in rgb]
            out = []
            for i in range(3):
                g = (1 + 0.005 * self.gain[i]) * (self.tint[i] if lin[i] > 0.2 else
                                                  self.low_tint[i] + (self.tint[i] - self.low_tint[i]) * lin[i] / 0.2)
                o = 0.0004 * self.offset[i] * (1 - min(lin[i], 1.0))
                out.append(max(lin[i] * g + o, 0.0))
            M = _mix(C.M709, _NATIVE, self.native)
            XYZ = C.scale(C.matvec(M, out), self.white_nits)
            blk = C.scale(C.D65, self.black_nits)
            return tuple(XYZ[i] + blk[i] for i in range(3))


class MockMeter:
    """Compatible with ArgyllMeter: start(), continue_calibration(), read(), close()."""

    name = "mock"

    def __init__(self, projector, catalog, delay=0.35, log=None):
        self.projector = projector
        self.catalog = catalog
        self.delay = delay
        self.log = log or (lambda s: None)
        self.ready = False

    def start(self):
        time.sleep(min(self.delay, 0.2))
        self.ready = True
        self.log("[simulation] meter ready\n")
        return "ready"

    def continue_calibration(self):
        return self.start()

    def read(self):
        pl = self.projector.play
        if pl:
            # like the real meter: a dark reading takes longer, a reading on a transition captures both pictures
            t0 = time.monotonic()
            pid = self.projector.playing_patch(t0)
            from .sequence import read_time_xyz
            dur = read_time_xyz(self.projector.xyz_for(self.catalog.by_id.get(pid))) / pl["speed"]
            time.sleep(dur)
            samples = [self.projector.xyz_for(self.catalog.by_id.get(self.projector.playing_patch(t0 + dur * (k + 0.5) / 10)))
                       for k in range(10)]
            XYZ = tuple(sum(x[i] for x in samples) / 10 for i in range(3))
            n = self.projector.noise * (4 if XYZ[1] < 1 else 1)
            k = 1 + random.gauss(0, n)
            v = tuple(c * k for c in XYZ)
            self.log(" Result is XYZ: %.6f %.6f %.6f\n" % v)
            return v
        time.sleep(self.delay)
        pid = self.projector.displayed
        patch = self.catalog.by_id.get(pid)
        XYZ = self.projector.xyz_for(patch)
        n = self.projector.noise * (4 if XYZ[1] < 1 else 1)
        k = 1 + random.gauss(0, n)
        v = tuple(c * k * (1 + random.gauss(0, n / 3)) for c in XYZ)
        self.log(" Result is XYZ: %.6f %.6f %.6f\n" % v)
        return v

    @property
    def alive(self):
        return self.ready

    def close(self):
        self.ready = False
