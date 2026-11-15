"""Auto-run analysis: timed meter readings → values of all patches in the video sequence.

Each reading is (t0, t1, XYZ): t0 — the key press (integration start), t1 — the result arrival.
1. Sync on the start mark: the white→gray fall (readings on white are short → precise time);
   if the fall happened inside a reading, the white fraction inside it is taken from its value (interpolation).
2. After the run — refinement: for every visible transition between segments the actual picture change time is
   measured; the time correction is piecewise-constant (catches player pauses/stalls), the finish mark
   confirms the video reached the end without a shift.
3. A reading goes into a segment only if it is entirely inside it with a margin (MARGIN_IN after the change,
   MARGIN_OUT before the next one). Outliers (menu, glare) are rejected by the median. The result is plausibility-checked.
"""
from __future__ import annotations

import math

from . import colorimetry as C
from .stability import y_tolerance

MARGIN_IN = 0.8
MARGIN_OUT = 0.3
JITTER = 1.0           # allowed jitter of a transition moment, s (estimated from reading boundaries)


def _mix_fraction(v, a, b):
    """If v is a mix of pictures a and b (the reading caught the transition): fraction of a (0…1), else None.
    Projection in XYZ onto the a–b segment; the residual must be small."""
    d = [a[i] - b[i] for i in range(3)]
    dd = sum(x * x for x in d)
    if dd <= 0:
        return None
    f = sum((v[i] - b[i]) * d[i] for i in range(3)) / dd
    if not (0.002 < f < 0.998):
        return None
    res = math.sqrt(sum((v[i] - b[i] - f * d[i]) ** 2 for i in range(3)))
    return f if res < 0.06 * math.sqrt(dd) + 0.02 * max(v[1], 0.01) else None


def _dist(a, b):
    """"Distance" between two readings: log luminance + chromaticity (if both are bright)."""
    if a[1] <= 0 or b[1] <= 0:
        return 99.0
    d = abs(math.log(a[1] / b[1]))
    if a[1] > 1 and b[1] > 1:
        p, q = C.xy(a), C.xy(b)
        d += 10 * math.hypot(p[0] - q[0], p[1] - q[1])
    return d


class AutoRun:
    def __init__(self, seq, speed=1.0, expected=None, expected_xy=None, white_hint=None):
        """expected: {patchId: expected luminance relative to the mark white}; expected_xy: {patchId: (x, y)} —
        chromaticity targets of color patches; white_hint: known white (nits) — not to confuse the mark with a dark plateau."""
        self.seq = seq
        self.expected = expected or {}
        self.expected_xy = expected_xy or {}
        self.white_hint = white_hint
        self.speed = speed               # for tests: the video "plays" faster
        from .sequence import SPEEDS
        self.slow = SPEEDS.get(seq.get("speed", "std"), 1.0)   # slow-meter video: every reading may take ×slow longer
        self.segs = seq["segments"]
        self.readings = []               # (t0, t1, XYZ)
        self.state = "arming"            # arming | running | done
        self.T0 = None                   # file start time (program clock)
        self.sync_info = {}
        self.result = None

    # ------------------------------------------------------------------ live
    def file_time(self, t):
        return (t - self.T0) * self.speed if self.T0 is not None else None

    def feed(self, t0, t1, XYZ):
        self.readings.append((t0, t1, tuple(XYZ)))
        if self.state == "arming":
            self._try_sync()
        elif self.state == "running":
            ft = self.file_time(t1)
            if ft is not None and ft > self.seq["endAnchor"] + 3:
                self.state = "done"

    def current_segment(self, t):
        ft = self.file_time(t)
        if ft is None:
            return None
        for s in self.segs:
            if s["start"] <= ft < s["start"] + s["dur"]:
                return s
        return None

    def progress(self, t):
        ft = self.file_time(t)
        if ft is None:
            return None
        return max(0.0, min(1.0, ft / self.seq["duration"]))

    # ------------------------------------------------------------------ sync on the mark
    def _find_falling_edge(self, readings, min_bright_span, bright_min=1.0, start_idx=0):
        """Finds the mark: darker → stable bright plateau → darker (mark gray). On transitions a reading may catch both
        pictures (mixed) — the change moment inside it is estimated from the luminance fraction.
        Returns (t_fall, Y_white, t_rise, index) or None."""
        r = readings
        n = len(r)
        i = start_idx
        while i < n - 1:
            Yi = r[i][2][1]
            if Yi < bright_min or abs(r[i + 1][2][1] / Yi - 1) >= 0.08:
                i += 1
                continue
            if i + 2 < n and r[i + 1][2][1] > 1.03 * Yi and abs(r[i + 2][2][1] / r[i + 1][2][1] - 1) < 0.03:
                i += 1                            # r[i] caught the rise (a mix): the plateau starts at the next reading
                continue
            j = i + 1
            while True:
                if j + 1 < n and abs(r[j + 1][2][1] / Yi - 1) < 0.08:
                    j += 1
                elif j + 2 < n and abs(r[j + 2][2][1] / Yi - 1) < 0.08:
                    j += 2                        # a single outlier (menu, glare) inside the plateau
                else:
                    break
            if j + 1 >= n:
                return None                       # the plateau is still going
            plat = r[i:j + 1]
            Yb = sorted(x[2][1] for x in plat)[len(plat) // 2]
            if self.white_hint and Yb < 0.5 * self.white_hint:
                i = j + 1
                continue                          # plateau darker than white — not the mark
            # rise: before the plateau — clearly darker (< 35 % of white: the 50 % gray intro or black) no further than
            # 3 readings; the reading right after may catch the transition (fraction by luminance), an outlier may sit between
            kd = next((q for q in range(i - 1, max(-1, i - 4), -1) if r[q][2][1] < 0.35 * Yb), None)
            if kd is None:
                i = j + 1
                continue
            if kd == i - 1:
                t_rise = (r[kd][1] + r[i][0]) / 2
            else:
                mx = r[kd + 1]
                dk = r[kd][2][1]
                f = max(0.0, min(1.0, (mx[2][1] - dk) / (Yb - dk)))
                t_rise = mx[1] - f * (mx[1] - mx[0])
            # fall: after the plateau — the mark gray (< 35 % of white) no further than 3 readings
            qd = next((q for q in range(j + 1, min(n, j + 4)) if r[q][2][1] < 0.35 * Yb), None)
            if qd is None:
                if j + 3 >= n:
                    return None           # not visible yet, wait for more readings
                i = j + 1
                continue
            if qd == j + 1:
                t_edge = (plat[-1][1] + r[qd][0]) / 2
            else:
                mx = r[qd - 1]
                dk = r[qd][2][1]
                f = max(0.0, min(1.0, (mx[2][1] - dk) / (Yb - dk)))
                t_edge = mx[0] + f * (mx[1] - mx[0])
            if (t_edge - t_rise) * self.speed >= min_bright_span:
                self._mark_plateau = (t_rise, t_edge)
                return t_edge, Yb, t_rise, j + 1
            i = j + 1
        return None

    def _marks(self):
        """All start-mark candidates in the readings: [(t_fall, Y_white, t_rise, span)]."""
        mark_w = [sg for sg in self.segs if sg["role"] == "mark"][0]["dur"]
        out, i = [], 0
        while True:
            tol = 1.5 * self.slow                 # a slow meter's readings are longer: the edges are less precise
            res = self._find_falling_edge(self.readings, min_bright_span=mark_w - tol, start_idx=i)
            if not res:
                return out
            t_edge, Yb, t_rise, nxt = res
            span = (t_edge - t_rise) * self.speed
            if abs(span - mark_w) <= tol:
                out.append((t_edge, Yb, t_rise, span))
            i = max(nxt, i + 1)

    def _set_sync(self, mk):
        t_edge, Yb, t_rise, span = mk
        self.T0 = t_edge - self.seq["anchor"] / self.speed
        self.sync_info = {"edge": t_edge, "white": Yb, "markSpan": round(span, 2), "rise": t_rise}

    def _try_sync(self):
        mk = self._marks()
        if mk:
            self._set_sync(mk[0])
            self.state = "running"

    # ------------------------------------------------------------------ analysis after the run
    def _runs(self, edge0):
        """Readings after the start mark → runs of constant picture with the start moment of each.
        A mixed reading on a transition gives the exact transition time; a single reading after which the picture
        returned is an outlier (menu, glare)."""
        TH = 0.08
        rr = [x for x in self.readings if x[0] >= edge0 - 0.02 / self.speed]   # without the reading that caught the fall
        runs = []
        cur = None
        i = 0
        Yw = self.sync_info["white"]
        while i < len(rr):
            t0, t1, v = rr[i]
            if cur is None:
                cur = {"edge": edge0, "r": [rr[i]], "ref": v}     # the first run — the gray after the mark
                i += 1
                continue
            if _dist(v, cur["ref"]) <= TH:
                cur["r"].append(rr[i])
                i += 1
                continue
            nxt = rr[i + 1] if i + 1 < len(rr) else None
            if nxt is not None and _dist(nxt[2], cur["ref"]) <= TH and nxt[0] - cur["r"][-1][1] < 2.5 * self.slow / self.speed:
                cur.setdefault("spikes", 0)
                cur["spikes"] += 1
                i += 1                    # outlier: the picture returned
                continue
            # mixed: a mix of the current picture and the next SETTLED one (an outlier may sit between them)
            js = None
            for q in range(i + 1, min(len(rr), i + 4)):
                if q + 1 >= len(rr) or _dist(rr[q][2], rr[q + 1][2]) <= TH:
                    js = q
                    break
            f = _mix_fraction(v, cur["ref"], rr[js][2]) if js is not None else None
            if f is not None:
                edge = t0 + f * (t1 - t0)            # the reading caught the transition
                runs.append(cur)
                # the fraction from a mixed reading is precise on bright; on dark (long reading, noise) — rough
                cur = {"edge": edge, "r": [rr[js]], "ref": rr[js][2],
                       "unc": 0.3 * (t1 - t0) if min(v[1], rr[js][2][1]) < 1.0 else 0.0}
                i = js + 1
                continue
            edge = (cur["r"][-1][1] + t0) / 2
            runs.append(cur)
            # the transition may be inside the last reading of the previous run or the first of this one (not detected
            # as mixed — dark readings take 7 s): the uncertainty is the length of such a reading
            pl = cur["r"][-1]
            unc = max(0.0, t0 - pl[1]) + max(pl[1] - pl[0], t1 - t0)
            cur = {"edge": edge, "r": [rr[i]], "ref": v, "unc": unc}
            i += 1
        if cur is not None:
            runs.append(cur)
        # a short insert between identical runs — an outlier of several readings
        out = []
        k = 0
        while k < len(runs):
            if (out and k + 1 < len(runs) and _dist(out[-1]["ref"], runs[k + 1]["ref"]) <= TH and
                    runs[k + 1]["edge"] - runs[k]["edge"] < 2.5 * self.slow / self.speed):
                out[-1]["r"].extend(runs[k + 1]["r"])
                k += 2
                continue
            out.append(runs[k])
            k += 1
        for k, r in enumerate(out):
            ys = sorted(x[2][1] for x in r["r"])
            med = ys[len(ys) // 2]
            r["ref"] = min((x[2] for x in r["r"]), key=lambda v: abs(v[1] - med))
            r["end"] = out[k + 1]["edge"] if k + 1 < len(out) else r["r"][-1][1] + 1.0
        return out

    def _plausible(self, Y, seg, XYZ=None):
        """Does the run luminance (and chromaticity of a color patch) look like the segment. Unknown — assume yes."""
        exp = self.expected.get(seg["patchId"])
        Yw = self.sync_info.get("white")
        if XYZ is not None and not self._chroma_ok(XYZ, seg["i"]):
            return False
        if not exp or not Yw or Y <= 0:
            return True
        r = Y / (Yw * exp)
        if seg["patchId"] in self.expected_xy and exp >= 0.05:
            return 0.7 <= r <= 1.8          # colors: −15…+52 % on a real projector; menu over the picture — −40 %
        if exp >= 0.01:
            return 0.4 <= r <= 2.5
        return 0.1 <= r <= 10.0

    def _chroma_ok(self, XYZ, j, reach=3):
        """Color patch: the measured chromaticity is closer to its own target than to the targets of neighboring
        color patches in the schedule (and within 0.12 of its own). Catches a "one patch shift" invisible in luminance."""
        pid = self.segs[j]["patchId"]
        tgt = self.expected_xy.get(pid)
        if tgt is None or XYZ[1] <= 0.05:
            return True
        x, y = C.xy(XYZ)
        own = math.hypot(x - tgt[0], y - tgt[1])
        if own > 0.12:
            return False
        for q in range(max(0, j - reach), min(len(self.segs), j + reach + 1)):
            other = self.segs[q]["patchId"]
            o = self.expected_xy.get(other)
            if q == j or o is None or other == pid:
                continue
            if math.hypot(x - o[0], y - o[1]) + 0.004 < own and own > 0.015:
                return False
        return True

    def _match(self, runs):
        """Matching runs to schedule segments (dynamic programming).
        Run 0 = segment 2 (the gray after the mark). Cost: time shift (jitter free, player pause
        cheap, going back in time not allowed), extra/missing transitions, luminance mismatch."""
        segs = self.segs
        m, n = len(runs), len(segs)
        SPUR, MISS, BONUS = 1.0, 1.5, 0.3
        INF = float("inf")
        ft = [self.file_time(r["edge"]) for r in runs]
        best = {(0, 2): (0.0, None, 0.0)}           # (k, j) → (cost, previous, shift)
        order = [(0, 2)]
        for k in range(m):
            for j in range(2, n):
                if (k, j) not in best:
                    continue
                cost, _, d = best[(k, j)]
                # segments that run k covers entirely with the same picture (identical neighboring patches) —
                # skipped for free; other skips cost MISS
                run_end = self.file_time(runs[k]["end"]) - d
                for k2 in range(k + 1, min(m, k + 6)):
                    skipped = 0.0
                    for j2 in range(j + 1, min(n, j + 16)):
                        if j2 > j + 1:
                            q = j2 - 1
                            covered = (segs[q]["start"] + segs[q]["dur"] <= run_end + 0.5 and
                                       run_end - (self.file_time(runs[k]["edge"]) - d) >=
                                       0.8 * (segs[q]["start"] + segs[q]["dur"] - segs[j]["start"]) and
                                       self._plausible(runs[k]["ref"][1], segs[q], runs[k]["ref"]) and
                                       segs[q]["role"] == segs[j]["role"])
                            skipped += 0.0 if covered else MISS
                            if skipped > 3 * MISS:
                                break
                        # short extra runs before k2 (mixed readings on a transition, menu) — the picture change may
                        # have happened at the first of them (if they take no more than half of the segment)
                        cand = [k2] + [q for q in range(k + 1, k2)
                                       if (runs[k2]["edge"] - runs[q]["edge"]) * self.speed <= 0.5 * segs[j2]["dur"]]
                        d2 = min((ft[q] - segs[j2]["start"] for q in cand), key=lambda x: abs(x - d))
                        dd = d2 - d
                        # the transition moment is known to half a reading (seconds on dark patches)
                        # (both for this run and the previous one: its shift d may have the same error)
                        jit = JITTER + 0.5 * (runs[k2].get("unc", 0.0) + runs[k].get("unc", 0.0)) * self.speed
                        if -jit <= dd <= jit:
                            c = 0.5 * abs(dd) / jit
                        elif jit < dd <= 8.0:
                            c = 0.6 + 0.05 * dd          # pause/stall
                        else:
                            continue
                        if not self._plausible(runs[k2]["ref"][1], segs[j2], runs[k2]["ref"]):
                            c += 1.5
                        if (runs[k2]["end"] - runs[k2]["edge"]) * self.speed < 0.4 * segs[j2]["dur"]:
                            c += 0.8             # run too short for this segment (transition/outlier)
                        # skipping a run costs more the more readings it has (a menu gives 1–2, a patch more)
                        c += sum(SPUR + 0.3 * min(len(runs[w]["r"]) - 1, 5) for w in range(k + 1, k2)) + skipped - BONUS
                        tot = cost + c
                        if (k2, j2) not in best or tot < best[(k2, j2)][0]:
                            best[(k2, j2)] = (tot, (k, j), d2)
                            order.append((k2, j2))
        # end: trailing extra runs cost SPUR, trailing skips (video stopped) — 0
        fin = min(best, key=lambda kj: best[kj][0] + SPUR * (m - 1 - kj[0]))
        path = []
        node = fin
        while node is not None:
            path.append((node, best[node][2]))
            node = best[node][1]
        path.reverse()
        return path, best[fin][0]

    def raw(self):
        """Raw readings to store with the run: [[t0, t1, X, Y, Z]] in file seconds (for re-analysis)."""
        if self.T0 is None:
            return []
        return [[round(self.file_time(a), 3), round(self.file_time(b), 3)] + [round(c, 6) for c in v]
                for a, b, v in self.readings]

    @classmethod
    def from_raw(cls, seq, raw, expected=None, expected_xy=None, white_hint=None):
        """Re-analysis of a stored run: readings in file time (T0 = 0)."""
        ar = cls(seq, expected=expected, expected_xy=expected_xy, white_hint=white_hint)
        for row in raw:
            ar.feed(row[0], row[1], row[2:5])
        return ar

    def _contested(self, w, mean):
        """Readings lying well inside the segment disagree with the accepted value and are not clearly outnumbered:
        e.g. a menu that stayed on screen for two long readings after one correct one. Which of the two plateaus is
        the patch cannot be told — the patch is flagged instead of guessed."""
        a, b = w
        inside = [v for (t0, t1, v) in self.readings if t0 >= a + 0.15 / self.speed and t1 <= b - 0.1 / self.speed]
        dis = sum(1 for v in inside if _dist(v, mean) > 0.08)
        return dis > 0 and len(inside) - dis < 2 * dis + 1

    def analyze(self):
        """Result: patch values + quality. Also works for an unfinished run (whatever was captured)."""
        if self.T0 is None:
            mark_w = [sg for sg in self.segs if sg["role"] == "mark"][0]["dur"]
            self.result = {"ok": False, "error": "Start mark not found (%d s of white between grays). " % mark_w +
                                                 "The video did not start or the meter is not looking at the screen."}
            return self.result
        segs = self.segs
        # the mark may have been "found" on a frozen frame or a similar place in the file: try all candidates and take
        # the one the schedule fits best (lowest matching cost per segment)
        best = None
        for mk in self._marks()[:6] or [None]:
            if mk is not None:
                self._set_sync(mk)
            runs = self._runs(self.sync_info["edge"])
            path, cost = self._match(runs) if runs else ([], 0.0)
            score = cost - 0.5 * len(path)
            if best is None or score < best[0]:
                best = (score, dict(self.sync_info), self.T0, runs, path, cost)
        _, self.sync_info, self.T0, runs, path, cost = best
        edge0 = self.sync_info["edge"]
        per = {sg["i"]: [] for sg in segs}
        pdur = {sg["i"]: [] for sg in segs}          # durations of accepted readings (for the "single reading" flag)
        max_shift, shifts = 0.0, 0
        prev_d = 0.0
        matched = {j for (k, j), d in path}
        win = {}                                      # matched segment → its time window (for the consistency check)
        for n_p, ((k, j), d) in enumerate(path):
            r = runs[k]
            win[j] = (self.T0 + (segs[j]["start"] + d) / self.speed,
                      self.T0 + (segs[j]["start"] + segs[j]["dur"] + d) / self.speed)
            if abs(d - prev_d) > 0.5:
                shifts += 1
            prev_d = d
            max_shift = max(max_shift, abs(d))
            # following segments that the run covers entirely in time and that are not matched themselves:
            # neighboring patches with the same picture (HDR above the peak, crushed shadows) — split readings by time
            cover = [j]
            q = j + 1
            while q < len(segs) and q not in matched and segs[q]["role"] == segs[j]["role"]:
                q_end = self.T0 + (segs[q]["start"] + segs[q]["dur"] + d) / self.speed
                if q_end > r["end"] + 0.3 / self.speed or not self._plausible(r["ref"][1], segs[q], r["ref"]):
                    break
                cover.append(q)
                q += 1
            # run length no more than the segment + pause: beyond that it is no longer the video (Plex menu after stop)
            last = segs[cover[-1]]
            limit = r["edge"] + (last["start"] + last["dur"] - segs[j]["start"] + 8) / self.speed
            for (t0, t1, v) in r["r"]:
                if not (t0 >= r["edge"] + 0.15 / self.speed and t1 <= min(r["end"], limit) - 0.1 / self.speed):
                    continue
                if len(cover) == 1:
                    # also by schedule: the reading ends no later than the segment end (+jitter). A long dark reading
                    # started before a change to a similar-luminance picture would otherwise pull the value to the neighbor
                    seg_end = self.T0 + (segs[j]["start"] + segs[j]["dur"] + d + 0.3) / self.speed
                    if t1 <= seg_end or v[1] > 1.0:
                        per[j].append(v)
                        pdur[j].append(t1 - t0)
                    continue
                for jj in cover:                  # the reading is entirely inside its segment (file time)
                    a = self.T0 + (segs[jj]["start"] + d) / self.speed
                    b = a + segs[jj]["dur"] / self.speed
                    if t0 >= a + (0.15 if jj != j else 0) / self.speed and t1 <= b - 0.1 / self.speed:
                        per[jj].append(v)
                        pdur[jj].append(t1 - t0)
                        break
        # mark white: plateau readings (the sync was based on them)
        rise = self.sync_info.get("rise")
        if rise is not None and 1 in per and not per[1]:
            per[1] = [v for (t0, t1, v) in self.readings
                      if t0 >= rise + 0.15 / self.speed and t1 <= edge0 - 0.1 / self.speed]
        segs_out = []
        for sg in segs:
            mean, cnt, sd, rej = self._robust(per[sg["i"]])
            self._pdur = pdur
            segs_out.append({"i": sg["i"], "patchId": sg["patchId"], "role": sg["role"], "start": sg["start"],
                             "dur": sg["dur"], "XYZ": mean, "n": cnt, "sd": round(sd, 3) if sd is not None else None,
                             "rejected": rej, "flags": []})
            if sg["role"] == "patch" and mean and sg["i"] in win and self._contested(win[sg["i"]], mean):
                segs_out[-1]["flags"].append("unstable")
        white_pid = segs[1]["patchId"]
        end_white = [x for x in segs_out if x["role"] == "end" and x["patchId"] == white_pid]
        end_ok = bool(end_white and end_white[0]["XYZ"] and
                      abs(end_white[0]["XYZ"][1] / self.sync_info["white"] - 1) < 0.15)
        self._validate(segs_out)
        self._flag_before_failure(segs_out)
        drift = [x for x in segs_out if x["role"] in ("drift", "mark", "end") and x["patchId"] == white_pid
                 and x["XYZ"]]
        self.result = {"ok": True,
                       "sync": dict(self.sync_info, edgesMeasured=len(path), shifts=shifts,
                                    maxShift=round(max_shift, 2), endMarker=end_ok, runs=len(runs),
                                    matchCost=round(cost, 2)),
                       "segments": segs_out,
                       "white": [{"t": x["start"], "Y": x["XYZ"][1]} for x in drift]}
        return self.result

    def _edge_between(self, prev, cur, real_prev=None):
        """Picture change time between plateaus prev and cur. If prev is a mixed reading on a transition, the change
        moment inside it = fraction of the old picture by luminance."""
        if prev.get("mixed") and len(prev["r"]) == 1:
            t0, t1, v = prev["r"][0]
            if real_prev is not None:
                fa = _mix_fraction(v, real_prev["ref"], cur["ref"])
                if fa is not None:
                    return t0 + fa * (t1 - t0)
            return (t0 + t1) / 2
        return (prev["r"][-1][1] + cur["r"][0][0]) / 2

    @staticmethod
    def _robust(vals):
        """Median by Y and outlier rejection (projector menu, glare). → (XYZ, n, sd%, rejected)."""
        if not vals:
            return None, 0, None, 0
        ys = sorted(v[1] for v in vals)
        med = ys[len(ys) // 2]
        tol = max(0.03, 2 * y_tolerance(med))
        keep = [v for v in vals if med > 0 and abs(v[1] / med - 1) <= tol] or vals
        n = len(keep)
        mean = tuple(sum(v[i] for v in keep) / n for i in range(3))
        sd = 0.0
        if n > 1 and mean[1] > 0:
            sd = math.sqrt(sum((v[1] - mean[1]) ** 2 for v in keep) / n) / mean[1] * 100
        return mean, n, sd, len(vals) - n

    @staticmethod
    def _flag_before_failure(segs_out):
        """If the picture disappeared at the end of the run (room light, blocked, switched off) — a tail without good
        segments. The patch right before it (ended ≤ 15 s before the failure) may already be spoiled — flag it."""
        tail = 0
        for x in reversed(segs_out):
            if x["role"] in ("pre", "mark"):
                break
            if x.get("XYZ") and not any(f in UNRELIABLE for f in x.get("flags", [])):
                break
            tail += 1
        if tail < 3:
            return
        start_fail = segs_out[len(segs_out) - tail]["start"]
        for x in segs_out[:len(segs_out) - tail][::-1]:
            if x["start"] + x["dur"] < start_fail - 15:
                break
            if x["role"] == "patch" and x.get("XYZ"):
                x["flags"].append("before failure")
        if tail >= 3:
            for x in segs_out[len(segs_out) - tail:]:
                if x["role"] == "patch" and "failure at end" not in x["flags"]:
                    x["flags"].append("failure at end")

    def _validate(self, segs_out):
        """Plausibility: segment luminance looks like the patch (relative to the mark white), a color patch is closer
        to its own target than to its neighbors, the gray scale is monotonic, readings are consistent. Others are excluded."""
        Yw = self.sync_info.get("white")
        for x in segs_out:
            exp = self.expected.get(x["patchId"]) if getattr(self, "expected", None) else None
            if x["XYZ"] and exp and Yw:
                ratio = x["XYZ"][1] / (Yw * exp)
                lo, hi = ((0.7, 1.8) if (x["patchId"] in self.expected_xy and exp >= 0.05) else
                          (0.45, 2.2) if exp >= 0.01 else (0.15, 8.0))
                if not (lo <= ratio <= hi):
                    x["flags"].append("implausible")
            if x["XYZ"] and "implausible" not in x["flags"] and not self._chroma_ok(x["XYZ"], x["i"]):
                x["flags"].append("implausible")
        gray = sorted([x for x in segs_out if x["role"] == "patch" and x["XYZ"]
                       and x["patchId"].split("_")[1] in ("%02d" % k for k in range(1, 22))
                       and _is_gray(x["patchId"])], key=lambda x: _stim(x["patchId"]))
        for a, b in zip(gray, gray[1:]):
            if _stim(b["patchId"]) > _stim(a["patchId"]) and b["XYZ"][1] < a["XYZ"][1] * 0.98 and a["XYZ"][1] > 0.2:
                a["flags"].append("non-monotonic")
                b["flags"].append("non-monotonic")
        for x in segs_out:
            if x["role"] == "patch" and not x["XYZ"]:
                x["flags"].append("no readings")
            elif x["XYZ"] and x["n"] == 1 and x["XYZ"][1] > 1.0 and \
                    min((getattr(self, "_pdur", {}).get(x["i"]) or [0.0]) + [99.0]) * self.speed < 2.5 * self.slow:
                x["flags"].append("single reading")      # bright readings are fast — a single one means something interfered
            elif x["XYZ"] and x["sd"] is not None and x["sd"] > 100 * max(0.01, 2 * y_tolerance(x["XYZ"][1])):
                x["flags"].append("unstable")


def _is_gray(pid):
    n = int(pid.split("_")[1])
    return n <= 21


def _stim(pid):
    return (21 - int(pid.split("_")[1])) * 5


UNRELIABLE = ("no readings", "implausible", "unstable", "non-monotonic", "single reading", "before failure")


def reliable(seg):
    """A segment is usable for analysis: has a value and no warning flags."""
    return bool(seg.get("XYZ")) and not any(f in UNRELIABLE for f in seg.get("flags", []))
