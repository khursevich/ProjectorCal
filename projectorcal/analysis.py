"""Measurement analysis and hints (HintEngine). Rules and thresholds — docs/HOW_IT_WORKS.md."""
from __future__ import annotations

import math

from . import colorimetry as C
from .catalog import HUES, HUE_MENU, HUE_NAME, HUE_TO

BAL_GOOD, BAL_OK = 2.0, 5.0          # 98–102 / 95–105
DUV_OK = 0.003
DRIFT = 0.05                          # warning about a white luminance change


def _r(v, n=4):
    return None if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else round(v, n)


def derive(XYZ):
    """Derived values of one reading for the "Meter" panel."""
    x, y = C.xy(XYZ)
    bal = C.rgb_balance(XYZ)
    return {"X": _r(XYZ[0], 5), "Y": _r(XYZ[1], 5), "Z": _r(XYZ[2], 5), "x": _r(x), "y": _r(y),
            "cct": _r(C.cct(XYZ), 0) if XYZ[1] > 0 else None, "duv": _r(C.duv_vs_d65(XYZ), 5),
            "bal": [_r(b, 2) for b in bal]}


def level(value, good, ok):
    """'good' | 'ok' | 'bad' by absolute value."""
    if value is None:
        return None
    a = abs(value)
    return "good" if a <= good else ("ok" if a <= ok else "bad")


def balance_level(bal):
    return level(max(abs(b - 100) for b in bal), BAL_GOOD, BAL_OK)


def balance_hints(bal, control="Gain", tol=BAL_GOOD):
    """White balance hints. Green is left alone: R and B are matched to G.

    Returns a list of {channel, action:'up'|'down', pct, text}.
    """
    r, g, b = bal
    out = []
    if g <= 0:
        return out
    for name, v in (("R", r), ("B", b)):
        rel = v / g * 100
        if rel < 100 - tol:
            out.append({"channel": name, "action": "up", "pct": _r(100 - rel, 1),
                        "text": "Raise %s %s (%.1f%% short)" % (name, control, 100 - rel)})
        elif rel > 100 + tol:
            out.append({"channel": name, "action": "down", "pct": _r(rel - 100, 1),
                        "text": "Lower %s %s (%.1f%% too much)" % (name, control, rel - 100)})
    if abs(g - 100) > BAL_OK and not out:
        out.append({"channel": "G", "action": "up" if g < 100 else "down", "pct": _r(abs(g - 100), 1),
                    "text": "Green is %.1f%% %s, while R and B match it: move R and B together in the same direction"
                            % (abs(g - 100), "low" if g < 100 else "high")})
    return out


# ------------------------------------------------------------------ selections
def meas_xyz(m):
    return (m["X"], m["Y"], m["Z"])


def valid(m):
    """A reading is usable: not flagged as suspect/rejected (manually accepted ones are usable)."""
    f = m.get("flags") or []
    return "accepted" in f or not ("suspect" in f or "rejected" in f)


def step_measurements(session, step_id, pass_no=None):
    ms = [m for m in session["measurements"] if m["stepId"] == step_id and valid(m)]
    if pass_no is not None:
        ms = [m for m in ms if m.get("pass", 1) == pass_no]
    return ms


def latest_by_patch(ms):
    out = {}
    for m in ms:
        out[m["patchId"]] = m
    return out


def previous_by_patch(session, step_id, pass_no):
    """The last reading of each patch from earlier passes (for before/after comparison)."""
    return latest_by_patch([m for m in session["measurements"]
                            if m["stepId"] == step_id and m.get("pass", 1) < pass_no and valid(m)])


def white_y(session):
    """The fixed white; if not fixed — the last SDR_01 reading in the session."""
    if session.get("whiteY"):
        return session["whiteY"]
    for m in reversed(session.get("measurements", [])):
        if m["patchId"] == "SDR_01" and m["Y"] > 0 and valid(m):
            return m["Y"]
    return None


# ------------------------------------------------------------------ SDR gray
def analyze_gray(session, catalog, step_id):
    st = session["steps"][step_id]
    pass_no = st.get("pass", 1)
    cur = latest_by_patch(step_measurements(session, step_id, pass_no))
    prev = previous_by_patch(session, step_id, pass_no)
    gamma = session.get("gamma", "2.4")
    gval = C.gamma_value(gamma)
    white_m = cur.get(catalog.sdr_gray_by_stim(100))
    Yw = white_m["Y"] if white_m else white_y(session)
    black_m = cur.get(catalog.sdr_gray_by_stim(0))
    Yb = black_m["Y"] if black_m else 0.0
    rows, hints = [], []
    des, gms, bals = [], [], []
    gzones, bzones = [], []
    for pid in catalog.sdr_gray():
        m = cur.get(pid)
        p = catalog.get(pid)
        s = p["stim"]
        k = round(s * 100)
        row = {"patchId": pid, "stim": k, "measured": m is not None}
        if m is None or not Yw:
            if m is not None:
                row.update({"Y": _r(m["Y"], 4)})
            rows.append(row)
            continue
        XYZ = meas_xyz(m)
        Y = XYZ[1]
        Yt = C.sdr_target_Y(s, Yw, gamma, Yb) if s > 0 else None
        bal = C.rgb_balance(XYZ)
        de_c = C.gray_de_color(XYZ, Yw) if Y > 0.02 else None
        de_f = C.gray_de_full(XYZ, Yw, Yt) if Yt else None
        ge = C.gamma_eff(Y, Yw, s)
        dev = (Y / Yt - 1) * 100 if Yt else None
        x, y = C.xy(XYZ)
        row.update({"mid": m["id"], "Y": _r(Y, 4), "Yt": _r(Yt, 4), "dev": _r(dev, 2), "gEff": _r(ge, 3),
                    "deFull": _r(de_f, 2), "deColor": _r(de_c, 2), "bal": [_r(b, 1) for b in bal],
                    "x": _r(x), "y": _r(y), "cct": _r(C.cct(XYZ), 0), "sd": m.get("sdYpercent")})
        if gamma != "bt1886" and ge is not None and 0.1 <= s <= 0.9:
            # γeff on bright steps is very sensitive (ln s is small): 1 % of Y at 90 % is Δγ 0.1.
            # So the step level is by Δγ, but a small Y deviation is not counted as an error.
            lv = level(ge - gval, 0.05, 0.1)
            if abs(dev) <= 1.5:
                lv = "good"
            elif abs(dev) <= 3 and lv == "bad":
                lv = "ok"
            row["gLevel"] = lv
        if de_c is not None:
            row["deLevel"] = level(de_c, 1.5, 3.0)
        if s >= 0.1:
            row["balLevel"] = balance_level(bal)
        pm = prev.get(pid)
        if pm and pm["Y"] > 0:
            row["prevY"] = _r(pm["Y"], 4)
            row["chgPrev"] = _r((Y / pm["Y"] - 1) * 100, 2)
        if 10 <= k <= 90:
            if de_c is not None:
                des.append(de_c)
            if ge is not None:
                gms.append(ge)
        if k >= 20:   # balance criterion: 20–100 % (below that — HL sensor noise)
            bals.append(max(abs(b - 100) for b in bal))
        # gamma hint candidates (grouped below by neighboring steps)
        if 0.1 <= s <= 0.9 and dev is not None:
            off = (abs(dev) > 8) if gamma == "bt1886" else (ge is not None and abs(ge - gval) > 0.1 and abs(dev) > 3)
            gzones.append((k, ("up" if dev > 0 else "down") if off else None, dev))
        if s >= 0.1:
            bh = balance_hints(bal, "Gain" if s >= 0.5 else "Offset") if max(abs(b - 100) for b in bal) > BAL_OK else []
            bzones.append((k, tuple((h["channel"], h["action"]) for h in bh) or None, bh))
        rows.append(row)
    summary = {"whiteY": _r(Yw, 3), "blackY": _r(Yb, 4) if black_m else None,
               "contrast": _r(Yw / Yb, 0) if (Yw and black_m and Yb > 0.0005) else None,
               "deMean": _r(sum(des) / len(des), 2) if des else None, "deMax": _r(max(des), 2) if des else None,
               "gMean": _r(sum(gms) / len(gms), 3) if gms else None, "gamma": gamma,
               "balMax": _r(max(bals), 2) if bals else None, "pass": pass_no,
               "measuredCount": len(cur)}
    # 95/100 ratio (clipping check, step 1)
    m95 = cur.get(catalog.sdr_gray_by_stim(95))
    if m95 and white_m and white_m["Y"] > 0:
        summary["ratio95"] = _r(m95["Y"] / white_m["Y"], 3)
    if gms and gamma != "bt1886":
        gm = sum(gms) / len(gms)
        if gm < gval - 0.05:
            summary["gText"] = "mids brighter than target (γ lower)"
        elif gm > gval + 0.05:
            summary["gText"] = "mids darker than target (γ higher)"
        else:
            summary["gText"] = "on target"
    summary["verdict"] = gray_verdict(summary)
    amb = session.get("ambientY")
    if amb is not None:
        summary["ambientY"] = _r(amb, 4)
    if summary.get("contrast") and summary["contrast"] < 900:
        hints.append({"level": "ok", "patchId": None,
                      "text": "Black is high: %.3f nits, contrast %d:1 (a Vidda C3S in a dark room reaches ~0.03 nits / 1300:1). "
                              "Check the room light (measurement in the “Preparation” step) and Black Level with PLUGE. While black "
                              "is high, 5–15%% shadows cannot honestly reach a clean γ 2.4 — calibrate shadows at night."
                              % (Yb, summary["contrast"])})
    if gms and gamma != "bt1886" and abs(sum(gms) / len(gms) - gval) > 0.1:
        gm = sum(gms) / len(gms)
        hints.append({"level": "bad", "patchId": None,
                      "text": "Overall γ ≈ %.2f vs target %s (picture is %s). If the Gamma menu has presets (2.2/2.4/…), "
                              "first pick the closest to the target, then fine-tune the points." % (
                                  gm, gamma, "brighter" if gm < gval else "darker")})
    for run in _runs(gzones):
        hi, lo = run[0][0], run[-1][0]
        devs = [abs(z[2]) for z in run]
        worst = max(run, key=lambda z: abs(z[2]))[0]
        zone = ("%d%%" % hi) if hi == lo else ("%d–%d%%" % (lo, hi))
        if run[0][1] == "up":
            act = "lower the Gamma → Adjust → Control Point points responsible for this zone"
        else:
            act = "raise the Gamma → Adjust → Control Point points responsible for this zone"
        hints.append({"level": "bad", "patchId": None,
                      "text": "%s: %s than target by %s → %s (worst at %d%%; verify the direction with one reading)" % (
                          zone, "brighter" if run[0][1] == "up" else "darker",
                          ("%.0f%%" % devs[0]) if len(devs) == 1 else ("%.0f–%.0f%%" % (min(devs), max(devs))),
                          act, worst)})
    for run in _runs(bzones):
        hi, lo = run[0][0], run[-1][0]
        zone = ("%d%%" % hi) if hi == lo else ("%d–%d%%" % (lo, hi))
        parts = []
        for ch, action in run[0][1]:
            pct = max(h["pct"] for z in run for h in z[2] if h["channel"] == ch)
            ctl = "Gain" if hi >= 50 and lo >= 50 else ("Offset" if hi < 50 else "Gain/Offset")
            parts.append("%s %s %s (up to %.1f%%)" % ("Raise" if action == "up" else "Lower", ch, ctl, pct))
        hints.append({"level": "ok", "patchId": None, "text": "%s: balance drifts → %s" % (zone, "; ".join(parts))})
    return {"rows": rows, "summary": summary, "hints": hints}


# Response of Gamma → Control Point 1 → Gain on the Vidda C3S, measured 2026-11-01 (pass 23:28 vs 23:43):
# −14 steps gave ΔY −30 % at 10 %, −15 % at 20 %, −9 % at 30 %, −4 % at 50 %, −1 % at 70 %, 0 at 90–100 %
# and did not touch black. The shape fits ΔlnY = K·steps·(ln s)², K ≈ 0.0047 (error ~10 % at 10–60 %).
CP1_K = 0.0047


def cp1_steps(rows):
    """How many Control Point 1 steps remove the shadow deviation (negative — darker), or None.

    Only when the deviation is concentrated in the shadows (that is how point 1 works)."""
    pts = [(r["stim"] / 100, math.log(1 + r["dev"] / 100)) for r in rows
           if r.get("measured") and r.get("dev") is not None and 10 <= r["stim"] <= 60]
    if len(pts) < 3:
        return None
    f = [(CP1_K * math.log(s) ** 2, d) for s, d in pts]
    n = -sum(a * d for a, d in f) / sum(a * a for a, _ in f)
    low = [d for s, d in pts if s <= 0.3]
    high = [d for s, d in pts if s >= 0.5]
    if not low or abs(sum(low) / len(low)) < 0.04:
        return None
    if high and abs(sum(high) / len(high)) > 0.6 * abs(sum(low) / len(low)):
        return None  # the mids moved just as much — it is not the shadows but the overall gamma/preset
    return round(n)


# "Gamma by points" wizard. The Vidda C3S has 20 points: point k is the curve node at k·5 % (1 → 5 %, 2 → 10 %, …, 20 → 100 %).
# Verified 2026-11-03: with the values entered from the menu (1:−20 … 12:−3), the "20 points at 5 %" model explained
# 98 % of the luminance changes at 20–90 % vs a run with all points = 0; the "10 at 10 %" model — 75 %.
# Point 20 = white 100 %: it is left alone, white is the reference for all targets.
GP_COUNT = 20
GP_STEP = 5                       # % stimulus between neighboring points
GP_POINTS = tuple(range(1, GP_COUNT))


def gp_stim(k):
    return GP_STEP * int(k)


def gp_point(stim):
    """Step (5…95 %) → point number, or None."""
    return stim // GP_STEP if stim % GP_STEP == 0 and 0 < stim < 100 else None


def gp_tol(stim):
    return (1.5, 3.0) if stim >= 30 else (3.0, 6.0)


# Point response (2026-11-03): ≈2.3 % of its step's luminance per step, linear between neighboring nodes ("tent"
# ±5 %). Refined from actual results (gp_learn) after each run where only the points changed.
GP_PER_STEP = 2.3


def _tent(stim, node, width=float(GP_STEP)):
    return max(0.0, 1.0 - abs(stim - node) / width)


def _solve(Mx, v):
    """Gaussian elimination for a square system (the matrix is small, 19×19)."""
    n = len(v)
    M = [list(Mx[i]) + [v[i]] for i in range(n)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        if abs(M[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * b for a, b in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0 for i in range(n)]


def gp_plan(an, values, per_step=GP_PER_STEP, points=GP_POINTS):
    """Corrections for all points at once from a full pass.

    ΔlnY(s) = g · Σ Δk · tent(s, 5k); g = ln(1 + per_step %). Least squares over steps 10–95 % (5 % — with
    a small weight: meter noise and black dominate there) with light regularization and smoothing (neighboring points
    must not zigzag). A point without a nearby reading is left alone. Returns per point: now, delta, new, dev at the node."""
    g = math.log(1 + per_step / 100.0)
    rows = {r["stim"]: r for r in an["rows"] if r.get("measured") and r.get("dev") is not None}
    # a step whose target is below twice the black level is defined by black, not by gamma: "pulling" it with a point
    # would crush near-black into black. Without a black reading, 5 % is not used at all.
    Yb = (an.get("summary") or {}).get("blackY")
    obs = [(st, -math.log(1 + rows[st]["dev"] / 100.0), 0.3 if st < 10 else 1.0)
           for st in range(GP_STEP, 100, GP_STEP) if st in rows and _gp_usable(rows[st], st, Yb)]
    if len(obs) < 5:
        return []
    ks = list(points)
    nk = len(ks)
    AtA = [[0.0] * nk for _ in ks]
    Aty = [0.0] * nk
    seen = [0.0] * nk
    for st, y, w in obs:
        a = [g * _tent(st, gp_stim(k)) for k in ks]
        for i in range(nk):
            seen[i] += a[i]
            Aty[i] += w * a[i] * y
            for j in range(nk):
                AtA[i][j] += w * a[i] * a[j]
    lam, smooth = 0.02 * g * g, 0.05 * g * g
    for i in range(nk):
        AtA[i][i] += lam
        if i + 1 < nk and seen[i] and seen[i + 1]:
            AtA[i][i] += smooth
            AtA[i + 1][i + 1] += smooth
            AtA[i][i + 1] -= smooth
            AtA[i + 1][i] -= smooth
    d = _solve(AtA, Aty)
    out = []
    for i, (k, dk) in enumerate(zip(ks, d)):
        now = int(values.get(str(k), 0) or 0)
        delta = int(round(dk)) if seen[i] else 0
        r0 = rows.get(gp_stim(k))
        if r0 is not None and abs(r0["dev"]) <= gp_tol(gp_stim(k))[0] and abs(delta) <= 1:
            delta = 0                     # the step is already well within tolerance — ±1 step here is meter noise
        r = rows.get(gp_stim(k))
        out.append({"point": k, "stim": gp_stim(k), "now": now, "delta": delta,
                    "new": max(-50, min(50, now + delta)), "dev": r["dev"] if r else None})
    return out


def _gp_usable(row, st, Yb):
    if not Yb:
        return st >= 10
    Yt = row.get("Yt")
    return Yt is None or Yt > 2 * Yb


def gp_learn(prev_an, cur_an, applied):
    """Actual sensitivity (% per step) from two full passes and the applied corrections {k: Δ}."""
    p = {r["stim"]: r["Y"] for r in prev_an["rows"] if r.get("measured") and r.get("Y")}
    c = {r["stim"]: r["Y"] for r in cur_an["rows"] if r.get("measured") and r.get("Y")}
    if 100 not in p or 100 not in c or sum(abs(v) for v in applied.values()) < 3:
        return None
    xs, ys = [], []
    for st in range(10, 100, GP_STEP):
        if st in p and st in c and p[st] > 0:
            x = sum(int(v) * _tent(st, gp_stim(k)) for k, v in applied.items())
            if x:
                xs.append(x)
                ys.append(math.log((c[st] / c[100]) / (p[st] / p[100])))
    if len(xs) < 3:
        return None
    g = sum(a * b for a, b in zip(xs, ys)) / sum(a * a for a in xs)
    res = sum((y - g * x) ** 2 for x, y in zip(xs, ys))
    tot = sum(y * y for y in ys) or 1e-9
    fit = 1 - res / tot
    per = (math.exp(g) - 1) * 100
    if fit < 0.6 or not (0.5 <= per <= 5.0):
        return {"perStep": round(per, 2), "fit": round(fit, 2), "used": False}
    return {"perStep": round(per, 2), "fit": round(fit, 2), "used": True}


def gp_probe(prev_an, cur_an, delta):
    """Probe of an unknown point: where its effect peaks and how many % per step."""
    p = {r["stim"]: r["Y"] for r in prev_an["rows"] if r.get("measured") and r.get("Y")}
    c = {r["stim"]: r["Y"] for r in cur_an["rows"] if r.get("measured") and r.get("Y")}
    if 100 not in p or 100 not in c or not delta:
        return None
    eff = {st: ((c[st] / c[100]) / (p[st] / p[100]) - 1) * 100 for st in range(5, 100, 5) if st in p and st in c}
    if not eff:
        return None
    peak = max(eff, key=lambda st: abs(eff[st]))
    zone = [st for st in eff if abs(eff[st]) >= 0.4 * abs(eff[peak])]
    per = (math.exp(math.log(1 + eff[peak] / 100) / delta) - 1) * 100
    return {"peak": peak, "from": min(zone), "to": max(zone), "perStep": round(per, 2),
            "effect": {k: round(v, 1) for k, v in eff.items()}}


def gp_queue(an, points=GP_POINTS):
    """Points to adjust (bottom-up) according to a full pass (10–95 %)."""
    rows = {r["stim"]: r for r in an["rows"] if r.get("measured") and r.get("dev") is not None}
    out = []
    for k in points:
        st = gp_stim(k)
        r = rows.get(st)
        if st >= 10 and r is not None and abs(r["dev"]) > gp_tol(st)[1]:
            out.append(k)
    return out


def gray_plan(an, session, catalog):
    """One next action for the gray circle. Order matters: Gain (100 %) → Offset (20 %) → gamma →
    compromise in the mids. Change one thing at a time and do a new circle."""
    rows = {r["stim"]: r for r in an["rows"] if r.get("measured") and r.get("bal")}
    if 100 not in rows:
        return {"level": None, "title": "Do a circle of readings", "text": "Play the patches one by one — the program records them.",
                "patchId": None}

    def dev(r):
        return max(abs(b - 100) for b in r["bal"])

    r100 = rows[100]
    if dev(r100) > BAL_GOOD:
        h = balance_hints(r100["bal"], "Gain")
        return {"level": "bad", "title": "1. White balance at 100% → White Balance Gain",
                "text": "; ".join(x["text"] for x in h) or "Balance R and B to G", "patchId": r100["patchId"],
                "control": "Gain"}
    low = rows.get(20) or rows.get(30)
    if low and dev(low) > 2.5:
        h = balance_hints(low["bal"], "Offset")
        return {"level": "bad", "title": "2. Shadow balance (%d%%) → White Balance Offset" % low["stim"],
                "text": "; ".join(x["text"] for x in h) or "Balance R and B to G", "patchId": low["patchId"],
                "control": "Offset"}
    gh = [h for h in an["hints"] if h["level"] == "bad"]
    if gh:
        worst = max((r for r in rows.values() if r.get("gEff") and 10 <= r["stim"] <= 90),
                    key=lambda r: abs(r.get("dev") or 0), default=None)
        text = gh[0]["text"]
        gp = session["steps"].get("gpoints", {})
        if gp.get("stage") != "done":
            return {"level": "bad", "title": "3. Balance done → gamma in the “Gamma by points” step",
                    "text": "Gamma is set in the next step: all points = 0, base pass, then "
                            "corrections for all points at once. " + text, "patchId": None, "control": "Gamma",
                    "goto": "gpoints"}
        n = cp1_steps(an["rows"])
        if n:
            text = ("Shadows are %s than target → Gamma → Control Point 1 → Gain: %+d steps from the current value (estimate: "
                    "−14 steps = −30%% at 10%%, −15%% at 20%%, −4%% at 50%%). Then a new circle." % (
                        "brighter" if n < 0 else "darker", n))
        return {"level": "bad", "title": "3. Gamma → Gamma / Control Point",
                "text": text, "patchId": worst["patchId"] if worst else None, "control": "Gamma"}
    mids = [r for r in rows.values() if 30 <= r["stim"] <= 90 and dev(r) > 3]
    if mids:
        w = max(mids, key=dev)
        h = balance_hints(w["bal"], "Gain" if w["stim"] >= 50 else "Offset")
        return {"level": "ok", "title": "4. Mids (%d%%) drift by %.1f%%" % (w["stim"], dev(w)),
                "text": "Compromise: %s — but keep 100%% and 20%% within 98–102." % (
                    "; ".join(x["text"] for x in h) or "move Gain/Offset by 1 step"),
                "patchId": w["patchId"], "control": "Gain" if w["stim"] >= 50 else "Offset"}
    return {"level": "good", "title": "Gray scale within tolerance ✓",
            "text": "Next — colors. If you change balance or gamma later, do one more circle.",
            "patchId": None}


def pass_history(session, catalog, step_id):
    """Summary of each pass of a step (gray circles): white, ΔE, γ, balance, verdict."""
    import copy
    st = session["steps"][step_id]
    out = []
    for p in range(1, st.get("pass", 1) + 1):
        if not step_measurements(session, step_id, p):
            continue
        s2 = dict(session)
        s2["steps"] = copy.deepcopy(session["steps"])
        s2["steps"][step_id]["pass"] = p
        sm = analyze_gray(s2, catalog, step_id)["summary"]
        ms = step_measurements(session, step_id, p)
        out.append({"pass": p, "t": ms[-1]["timestamp"][11:16], "n": len(latest_by_patch(ms)),
                    "deMean": sm["deMean"], "gMean": sm["gMean"], "balMax": sm["balMax"], "verdict": sm["verdict"]})
    return out


def _runs(zones):
    """Groups of neighboring steps with the same non-empty tag: [(k, key, …), …] → [[…], …]."""
    runs, cur = [], []
    for z in zones:
        if z[1] is not None and cur and cur[-1][1] == z[1]:
            cur.append(z)
            continue
        if cur:
            runs.append(cur)
        cur = [z] if z[1] is not None else []
    if cur:
        runs.append(cur)
    return runs


def gray_verdict(s):
    """'good' (OK) / 'ok' (within tolerance) / 'bad' (needs work) / None (not enough data)."""
    if s.get("deMean") is None:
        return None
    lv = []
    if s["deMean"] < 1.5 and s["deMax"] < 2.5:
        lv.append("good")
    elif s["deMean"] < 3 and s["deMax"] < 4:
        lv.append("ok")
    else:
        lv.append("bad")
    if s.get("gMean") is not None and s.get("gamma") != "bt1886":
        lv.append(level(s["gMean"] - C.gamma_value(s["gamma"]), 0.05, 0.1))
    if s.get("balMax") is not None and s["deMax"] >= 2.5:
        # balance in % is stricter than the eye (−5 % R at 20 % is ΔE ≈ 1): ΔE decides, % only when visible
        lv.append(level(s["balMax"], BAL_GOOD, BAL_OK))
    for v in ("bad", "ok"):
        if v in lv:
            return v
    return "good"


# ------------------------------------------------------------------ SDR colors
def analyze_color(session, catalog, step_id="color"):
    st = session["steps"][step_id]
    pass_no = st.get("pass", 1)
    cur = latest_by_patch(step_measurements(session, step_id, pass_no))
    prev = previous_by_patch(session, step_id, pass_no)
    Yw = white_y(session)
    gamma = session.get("gamma", "2.4")
    rows, hints, des = [], [], []
    if not Yw:
        return {"rows": [], "summary": {"error": "No white: fix the white luminance in the “Laser level” step."},
                "hints": []}
    wref = C.scale(C.D65, Yw)
    for pid in catalog.sdr_colors(sweeps=True):
        p = catalog.get(pid)
        tXYZ = C.color_target_XYZ(p["rgb"], Yw, gamma)
        tx, ty = C.xy(tXYZ)
        row = {"patchId": pid, "hue": p["hue"], "sat": p["sat"], "amp": p["amp"], "tx": _r(tx), "ty": _r(ty),
               "tY": _r(tXYZ[1], 3), "measured": pid in cur}
        m = cur.get(pid)
        if m:
            XYZ = meas_xyz(m)
            lm, lt = C.lab(XYZ, wref), C.lab(tXYZ, wref)
            de = C.de2000(lm, lt)
            _, Cm, hm = C.lch(lm)
            _, Ct, ht = C.lch(lt)
            dh = C.hue_diff(hm, ht)
            dC = (Cm / Ct - 1) * 100 if Ct > 1 else None
            dY = (XYZ[1] / tXYZ[1] - 1) * 100 if tXYZ[1] > 0 else None
            x, y = C.xy(XYZ)
            row.update({"lab": [_r(v, 3) for v in lm], "tlab": [_r(v, 3) for v in lt]})
            row.update({"mid": m["id"], "x": _r(x), "y": _r(y), "Y": _r(XYZ[1], 3), "de": _r(de, 2), "dh": _r(dh, 1),
                        "dC": _r(dC, 1), "dY": _r(dY, 1), "deLevel": level(de, 3, 5)})
            pm = prev.get(pid)
            if pm:
                pde = C.de2000(C.lab(meas_xyz(pm), wref), lt)
                row["prevDe"] = _r(pde, 2)
            if p["amp"] == 1.0:
                des.append(de)
            msg = []
            i = HUES.index(p["hue"])
            if abs(dh) > 2 and p["sat"] >= 0.5:
                toward = HUES[(i + 1) % 6] if dh > 0 else HUES[(i - 1) % 6]
                back = HUES[(i - 1) % 6] if dh > 0 else HUES[(i + 1) % 6]
                msg.append("hue shifted by %.0f° toward %s → move Color tone toward %s" % (
                    abs(dh), HUE_TO[toward], HUE_TO[back]))
            if dC is not None and abs(dC) > 5:
                msg.append("saturation %s target by %.0f%% → Saturation %s" % (
                    "above" if dC > 0 else "below", abs(dC), "decrease" if dC > 0 else "increase"))
            if dY is not None and abs(dY) > 5:
                msg.append("brightness %s target by %.0f%% → Bright %s" % (
                    "above" if dY > 0 else "below", abs(dY), "decrease" if dY > 0 else "increase"))
            row["hint"] = "; ".join(msg) if msg else "within tolerance"
            if msg and p["amp"] == 1.0:
                hints.append({"level": row["deLevel"], "patchId": pid,
                              "text": "%s (%s): %s" % (HUE_NAME[p["hue"]].capitalize(), HUE_MENU[p["hue"]],
                                                       "; ".join(msg))})
        rows.append(row)
    summary = {"whiteY": _r(Yw, 3), "deMean": _r(sum(des) / len(des), 2) if des else None,
               "deMax": _r(max(des), 2) if des else None, "count": len(des), "pass": pass_no}
    inner = [r for r in rows if r.get("measured") and r["sat"] < 1 and r.get("dC") is not None]
    if len(inner) >= 6:
        dc = sum(r["dC"] for r in inner) / len(inner)
        dy = sum(r["dY"] for r in inner) / len(inner)
        summary["innerDC"], summary["innerDY"] = _r(dc, 1), _r(dy, 1)
        if dc < -8 or dy > 8:
            hints.append({"level": "ok", "patchId": None,
                          "text": "Intermediate saturations (25–75%%) are on average %s by %.0f%% and %s by %.0f%%, while "
                                  "100%% colors are accurate. This is projector processing, not CMS of the primaries: check "
                                  "that “color enhancers” are off (Color Enhancement / BrilliantColor / Vivid / "
                                  "Wide Color), Saturation = default, Color Space = BT.709 (not Auto). Compare "
                                  "with CMS reset to 0." % (
                                      "undersaturated" if dc < 0 else "oversaturated", abs(dc),
                                      "brighter" if dy > 0 else "darker", abs(dy))})
    if des:
        dm = sum(des) / len(des)
        summary["verdict"] = "good" if dm < 3 else ("ok" if dm < 5 else "bad")
    return {"rows": rows, "summary": summary, "hints": hints}


# ------------------------------------------------------------------ HDR10
def analyze_hdr(session, catalog, step_id="hdr"):
    st = session["steps"][step_id]
    pass_no = st.get("pass", 1)
    cur = latest_by_patch(step_measurements(session, step_id, pass_no))
    rows, hints, tracked = [], [], []
    for pid in catalog.hdr_gray():
        p = catalog.get(pid)
        tn = p["tnits"]
        row = {"patchId": pid, "stim": round(p["stim"] * 100), "tnits": _r(tn, 3), "measured": pid in cur,
               "inPass": round(p["stim"] * 100) in (75, 65, 60, 55, 50, 45, 40, 30, 20)}
        m = cur.get(pid)
        if m:
            XYZ = meas_xyz(m)
            bal = C.rgb_balance(XYZ)
            de_c = C.gray_de_color(XYZ, XYZ[1]) if XYZ[1] > 0.02 else None
            row.update({"mid": m["id"], "Y": _r(XYZ[1], 3), "ratio": _r(XYZ[1] / tn * 100, 1) if tn > 0 else None,
                        "bal": [_r(b, 1) for b in bal], "deColor": _r(de_c, 2), "balLevel": balance_level(bal),
                        "cct": _r(C.cct(XYZ), 0)})
            tracked.append((p["stim"], tn, XYZ[1], pid, bal))
            if max(abs(b - 100) for b in bal) > BAL_GOOD:
                ctl = "Gain" if p["stim"] >= 0.5 else "Offset"
                bh = balance_hints(bal, ctl)
                if bh:
                    hints.append({"level": balance_level(bal), "patchId": pid,
                                  "text": "PQ %d%%: %s" % (round(p["stim"] * 100), "; ".join(h["text"] for h in bh))})
        rows.append(row)
    summary = {"pass": pass_no, "count": len(tracked)}
    if tracked:
        tracked.sort()
        peak = max(t[2] for t in tracked)
        summary["peak"] = _r(peak, 1)
        # A projector with a peak of tens of nits cannot show PQ in absolute nits: look at what fraction
        # of the target it shows for shadows and mids (scale), and where additional compression starts.
        low = [y / tn for s, tn, y, _, _ in tracked if tn > 1 and tn <= 0.5 * peak]
        if not low:
            low = [y / tn for s, tn, y, _, _ in tracked if tn > 1][:2]
        if low:
            low.sort()
            scale = low[len(low) // 2]
            summary["scale"] = _r(scale * 100, 1)
            for s_, tn, y, pid, _ in tracked:
                if tn > 1 and y / tn < 0.85 * scale:
                    summary["rolloff"] = {"stim": round(s_ * 100), "target": _r(tn, 1), "Y": _r(y, 1)}
                    break
            m50 = [y for s_, tn, y, _, _ in tracked if round(s_ * 100) == 50]
            sdr_w = session.get("whiteY")
            if m50 and sdr_w:
                summary["pq50vsSdr"] = _r(m50[0] / sdr_w * 100, 0)
        # HDR reference (diffuse) white is 203 nits (BT.2408), PQ ≈ 58 %. For a screen with a peak of tens of nits
        # it is sensible to keep it at 50–70 % of the peak: faces and white walls look like SDR, with headroom for highlights.
        pts = sorted((tn, y) for s_, tn, y, _, _ in tracked if tn > 0 and y > 0)
        lo = [q for q in pts if q[0] <= 203]
        hi = [q for q in pts if q[0] >= 203]
        if lo and hi:
            (t1, y1), (t2, y2) = lo[-1], hi[0]
            rw = y1 if t2 == t1 else math.exp(math.log(y1) + (math.log(203) - math.log(t1)) /
                                             (math.log(t2) - math.log(t1)) * (math.log(y2) - math.log(y1)))
            summary["refWhite"] = _r(rw, 1)
            summary["refWhitePct"] = _r(rw / peak * 100, 0)
            if session.get("whiteY"):
                summary["refWhiteVsSdr"] = _r(rw / session["whiteY"] * 100, 0)
            if rw < 0.4 * peak:
                hints.append({"level": "ok", "patchId": None,
                              "text": "HDR is dark: reference white (203 nits in the master) is shown at %.1f nits — "
                                      "%d%% of the peak. Raise the HDR tone mapping brightness, target 50–70%% of the peak."
                                      % (rw, rw / peak * 100)})
            elif rw > 0.8 * peak:
                hints.append({"level": "ok", "patchId": None,
                              "text": "HDR has almost no highlight headroom: reference white %.1f nits = %d%% of the peak — bright "
                                      "details will merge. Lower the tone mapping, target 50–70%% of the peak."
                                      % (rw, rw / peak * 100)})
        mids = [(y / tn - 1) for s_, tn, y, _, _ in tracked if 0.40 <= s_ <= 0.55 and tn > 0]
        if mids and max(mids) > 0.30:
            summary["midHigh"] = True
            hints.append({"level": "bad", "patchId": None,
                          "text": "Mid steps (PQ 40–55%) are more than 30% brighter than target: check that Dynamic "
                                  "Contrast, AI contrast and dynamic tone mapping are off."})
        worst = max(max(abs(b - 100) for b in t[4]) for t in tracked)
        summary["balMax"] = _r(worst, 2)
        summary["verdict"] = level(worst, BAL_GOOD, BAL_OK)
    return {"rows": rows, "summary": summary, "hints": hints}


# ------------------------------------------------------------------ "is it the right patch" check
def expected_Y(patch, session, catalog):
    """Expected patch luminance by the model (for hints and checks)."""
    Yw = white_y(session)
    if patch["mode"] == "hdr10" or not Yw:
        return None
    if patch["kind"] == "gray":
        return Yw * patch["stim"] ** 2.4
    if patch["kind"] == "color":
        return Yw * patch["tY"]
    return None


def _neutral(XYZ):
    x, y = C.xy(XYZ)
    return math.hypot(x - C.D65_XY[0], y - C.D65_XY[1])


def guess_gray(XYZ, session, catalog, mode="sdr"):
    Yw = white_y(session)
    if not Yw or XYZ[1] <= 0 or mode != "sdr":
        return None
    s = (XYZ[1] / Yw) ** (1 / 2.4)
    best = min(catalog.sdr_gray(), key=lambda pid: abs(catalog.get(pid)["stim"] - s))
    return best


def last_valid(session, pid):
    """The last good reading of this patch taken after the last settings change (otherwise None).

    After a menu change (gamma, CMS, balance) old readings are no longer a reference for "is it the right patch"."""
    since = session.get("lastChangeAt") or ""
    for m in reversed(session.get("measurements", [])):
        if m["patchId"] == pid and valid(m):
            return m if m["timestamp"] > since else None     # in the same second as the change — no longer a reference
    return None


def _history_check(patch, XYZ, session):
    """Comparison with the previous good reading of the same patch: menu changes rarely move luminance by more than 25 %."""
    h = last_valid(session, patch["id"])
    if not h or h["Y"] <= 0 or XYZ[1] <= 0:
        return None
    ratio = XYZ[1] / h["Y"]
    lo, hi = (0.75, 1.33) if h["Y"] >= 0.3 else (0.5, 2.0)
    if not (lo <= ratio <= hi):
        return "luminance %.3g nits, but last time this patch gave %.3g nits (%s)" % (
            XYZ[1], h["Y"], h["timestamp"][11:16])
    if XYZ[1] >= 1.0 and h["Y"] >= 1.0:
        a, b = C.xy(XYZ), C.xy((h["X"], h["Y"], h["Z"]))
        if math.hypot(a[0] - b[0], a[1] - b[1]) > 0.03:
            return "chromaticity differs a lot from the previous reading of this patch"
    return None


def plausibility(patch, XYZ, session, catalog, prev_in_pass=None):
    """None — looks like the right patch; otherwise {text, guess} — "looks like the wrong file is playing"."""
    Y = XYZ[1]
    name = "%s (%s)" % (patch["id"], patch["name"])
    if patch["kind"] == "gray":
        # gray must be neutral (chromaticity is reliable on bright steps)
        if Y >= 1.0 and _neutral(XYZ) > 0.03:
            return {"text": "A color patch is on screen, but gray %s was expected." % name, "guess": None}
        if prev_in_pass is not None:
            pp = catalog.get(prev_in_pass["patchId"])
            if pp["kind"] == "gray" and pp["mode"] == patch["mode"] and pp["stim"] > patch["stim"] \
                    and prev_in_pass["Y"] > 0.05:
                # SDR: the next step must be darker; HDR: near the peak steps may be equal, but not brighter
                limit = 0.98 if patch["mode"] == "sdr" and prev_in_pass["Y"] > 1.0 else 1.03
                if Y > prev_in_pass["Y"] * limit:
                    return {"text": "Luminance did not drop relative to the previous step (%.3g → %.3g nits) — the previous "
                                    "file seems to be still playing or the picture is changing. Expected %s." % (
                                        prev_in_pass["Y"], Y, name), "guess": prev_in_pass["patchId"]}
        if patch["mode"] == "sdr":
            Yw = white_y(session)
            s = patch["stim"]
            if s == 1 and session.get("whiteY") and not (0.8 <= Y / session["whiteY"] <= 1.25):
                g = guess_gray(XYZ, session, catalog)
                return {"text": "Luminance (%.3g nits) does not look like white %s (fixed at %.3g nits)%s. If the laser "
                                "level changed — “Record anyway” and then re-fix the white." % (
                                    Y, name, session["whiteY"],
                                    ("; looks like %s" % g) if g and g != patch["id"] else ""), "guess": g}
            if Yw and 0 < s < 1:
                ratio = Y / (Yw * s ** 2.4)
                lo, hi = (0.6, 1.6) if s >= 0.2 else ((0.45, 2.2) if s >= 0.1 else (0.2, 5.0))
                if not (lo <= ratio <= hi):
                    g = guess_gray(XYZ, session, catalog)
                    gp = catalog.get(g) if g else None
                    return {"text": "Luminance does not look like %s (%.3g nits, expected ≈%.3g)%s" % (
                        name, Y, Yw * s ** 2.4,
                        ("; looks like %s (%s)" % (gp["id"], gp["name"])) if gp and g != patch["id"] else ""),
                        "guess": g}
            if s == 0 and Yw and Y > 0.05 * Yw:
                return {"text": "Too bright for black %s." % name, "guess": guess_gray(XYZ, session, catalog)}
        h = _history_check(patch, XYZ, session)
        if h:
            return {"text": "Does not look like %s: %s. If you just changed settings — “Record anyway”." % (
                name, h), "guess": None}
        return None
    if patch["kind"] == "color":
        x, y = C.xy(XYZ)
        cands = [("white", C.D65_XY[0], C.D65_XY[1])]
        for pid in catalog.sdr_colors(sweeps=True):
            p = catalog.get(pid)
            if p["sat"] == patch["sat"] and p["amp"] == patch["amp"]:
                cands.append((pid, p["tx"], p["ty"]))
        best = min(cands, key=lambda c: math.hypot(c[1] - x, c[2] - y))
        if best[0] != patch["id"] and Y > 0.3:
            if best[0] == "white":
                txt = "Gray/white is on screen, but %s was expected." % name
                g = None
            else:
                gp = catalog.get(best[0])
                txt = "The color does not look like %s; looks like %s (%s)." % (name, gp["id"], gp["name"])
                g = best[0]
            return {"text": txt, "guess": g}
        Yw = white_y(session)
        if Yw:
            ratio = Y / (Yw * patch["tY"])
            if not (0.7 <= ratio <= 1.45):
                return {"text": "Luminance does not look like %s (%.3g nits, expected ≈%.3g) — the patch has not started yet "
                                "or the player bar is over the picture." % (name, Y, Yw * patch["tY"]), "guess": None}
        h = _history_check(patch, XYZ, session)
        if h:
            return {"text": "Does not look like %s: %s. If you just changed CMS — “Record anyway”." % (name, h),
                    "guess": None}
        return None
    return None


def brightness_drift(session, Y):
    """Deviation of the 100% white Y from the fixed one (fraction) or None."""
    Yw = session.get("whiteY")
    if not Yw or Y <= 0:
        return None
    d = Y / Yw - 1
    return d if abs(d) > DRIFT else None
