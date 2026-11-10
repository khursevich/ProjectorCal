"""Full check: run analysis, run comparison, learning "setting → effect" links, adjustment plan.

A run = all patches of the auto sequence + a snapshot of the projector settings (what was set in the menu).
Links are learned from pairs of consecutive runs: if one group of settings changed between them, the metric change
is attributed to it and converted "per step". The more runs, the more precise the plan.
"""
from __future__ import annotations

import copy
import math

from . import analysis as A
from . import colorimetry as C
from . import psettings as PS
from .autorun import UNRELIABLE
from .catalog import HUE_NAME

# Per-step estimates until there is own data (refined after the first run with a change)
PRIOR = {"wbGain": 0.5, "wbOffset": 1.0}
# Offset on the Vidda C3S is coarse: 1 step of Offset R changes R/G by roughly OFFSET_K/s % at stimulus s (0…1):
# +4.7 % at 100 %, +9 % at 50 %, +23 % at 20 % (measured 2026-11-04, Offset R −1 → 0).
OFFSET_K = 4.7
WB_STIMS = (100, 80, 60, 50, 40, 30, 20)
# Vidda CMS: how much 1 step moves (rough estimate until the first run where only this color changed)
CMS_PRIOR = {"bright": 10.0, "sat": 2.0, "tone": 1.5}   # Bright: red/blue/cyan 9.6–12 % per step (2026-11-04/05)
# Saturation weights in the CMS objective: in movies almost all colors (skin, sky, foliage, interiors) are 25–75 %,
# full saturation is rare (neon, graphics). So mid tones matter more, but 100 % is not ignored.
CMS_WEIGHT = {0.25: 1.0, 0.5: 1.0, 0.75: 0.8, 1.0: 0.5}
NOISE = {"bal": 0.6, "Y": 0.6, "de": 0.3, "g": 0.02, "dev": 1.0}


def _sess_for(session, step, pass_no):
    s2 = dict(session)
    s2["steps"] = copy.deepcopy(session["steps"])
    s2["steps"][step]["pass"] = pass_no
    return s2


def run_analysis(session, catalog, run):
    """Full analysis of one run (SDR or HDR)."""
    step = "auto" if run["mode"] == "sdr" else "autohdr"
    s2 = _sess_for(session, step, run["pass"])
    out = {"mode": run["mode"], "runId": run["id"], "pass": run["pass"]}
    corr = drift_model(run)
    if corr:
        s2 = _apply_drift(s2, step, run, corr)
        out["driftCorr"] = {k: corr[k] for k in ("tau", "maxPct", "points")}
    if run["mode"] == "sdr":
        s2 = dict(s2)
        gray = A.analyze_gray(s2, catalog, step)
        # white for colors — from the same run
        w = [r for r in gray["rows"] if r["stim"] == 100 and r.get("Y")]
        if w:
            s2["whiteY"] = w[0]["Y"]
        col = A.analyze_color(s2, catalog, step)
        out.update({"gray": gray, "color": col})
    else:
        out["hdr"] = A.analyze_hdr(s2, catalog, step)
    segs = run.get("segments", [])
    out["unreliable"] = [{"patchId": x["patchId"], "flags": x["flags"]} for x in segs
                         if x["role"] == "patch" and (not x.get("XYZ") or any(
                             f in UNRELIABLE
                             for f in x.get("flags", [])))]
    wl = [w["Y"] for w in run.get("white", []) if w.get("Y")]
    if len(wl) >= 2:
        out["drift"] = {"min": min(wl), "max": max(wl), "pct": round((max(wl) / min(wl) - 1) * 100, 2),
                        "points": run.get("white")}
    out["metrics"] = metrics(out)
    return out


def drift_model(run):
    """Laser warm-up/drift during the run from the control whites (mark, 100 % patch, drift checks, finish):
    W(t) = A − B·e^(−t/τ) (warm-up) — τ by search, A and B by least squares; if the fit is poor —
    piecewise-linear. None if white changed by less than 1 % during the run."""
    segs = run.get("segments") or []
    wid = "SDR_01" if run["mode"] == "sdr" else "HDR_06"
    pts = sorted((x["start"] + x["dur"] / 2.0, x["XYZ"][1]) for x in segs
                 if x["patchId"] == wid and x.get("XYZ") and (x.get("n") or 0) >= 2
                 and x["role"] in ("mark", "patch", "drift", "end"))
    if len(pts) < 2:
        return None
    med = sorted(y for _, y in pts)[len(pts) // 2]
    pts = [(t, y) for t, y in pts if 0.8 < y / med < 1.2]        # a white "eaten" by a failure is not a reference
    if len(pts) < 2 or max(y for _, y in pts) / min(y for _, y in pts) < 1.01:
        return None
    best = None
    if len(pts) >= 3:
        for tau in [5 * 1.15 ** i for i in range(40)]:
            e = [math.exp(-t / tau) for t, _ in pts]
            n = len(pts)
            se, see = sum(e), sum(v * v for v in e)
            sy, sey = sum(y for _, y in pts), sum(v * y for v, (_, y) in zip(e, pts))
            det = n * see - se * se
            if abs(det) < 1e-12:
                continue
            Aa = (sy * see - se * sey) / det
            Bb = -(n * sey - se * sy) / det
            res = sum((Aa - Bb * v - y) ** 2 for v, (_, y) in zip(e, pts))
            if best is None or res < best[0]:
                best = (res, tau, Aa, Bb)
    kind = "exp" if best and math.sqrt(best[0] / len(pts)) < 0.004 * med else "lin"
    ref = max(y for _, y in pts) if kind == "lin" else best[2]
    m = {"kind": kind, "pts": pts, "ref": ref, "tau": round(best[1], 1) if kind == "exp" else None,
         "A": best[2] if kind == "exp" else None, "B": best[3] if kind == "exp" else None}
    m["maxPct"] = round((ref / min(drift_white(m, t) for t, _ in pts) - 1) * 100, 2)
    m["points"] = [{"t": round(t), "Y": round(y, 3)} for t, y in pts]
    return m


def drift_white(m, t):
    if m["kind"] == "exp":
        return m["A"] - m["B"] * math.exp(-t / m["tau"])
    pts = m["pts"]
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if t <= t1:
            return y0 + (y1 - y0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def _apply_drift(s2, step, run, m):
    """Run readings are normalized to one laser brightness: XYZ × W_ref / W(t of the segment middle)."""
    tmid = {}
    for x in run.get("segments") or []:
        if x["role"] == "patch":
            tmid.setdefault(x["patchId"], x["start"] + x["dur"] / 2.0)
    tag = "run:" + run["id"]
    ms = []
    for mm in s2["measurements"]:
        if mm.get("stepId") == step and tag in (mm.get("flags") or []) and mm["patchId"] in tmid:
            k = m["ref"] / drift_white(m, tmid[mm["patchId"]])
            mm = dict(mm, X=mm["X"] * k, Y=mm["Y"] * k, Z=mm["Z"] * k)
        ms.append(mm)
    return dict(s2, measurements=ms)


def metrics(an):
    """Flat set of key run metrics (for comparison and learning)."""
    m = {}
    if an["mode"] == "sdr":
        rows = {r["stim"]: r for r in an["gray"]["rows"] if r.get("measured") and r.get("bal")}
        sm = an["gray"]["summary"]
        if 100 in rows:
            r = rows[100]
            m["whiteY"] = r["Y"]
            m["whiteCCT"] = r.get("cct")
            m["white_x"], m["white_y"] = r.get("x"), r.get("y")
        for st in (100, 80, 60, 50, 40, 30, 20, 10):
            if st in rows:
                b = rows[st]["bal"]
                m["R/G@%d" % st] = round((b[0] / b[1] - 1) * 100, 2)
                m["B/G@%d" % st] = round((b[2] / b[1] - 1) * 100, 2)
                if rows[st].get("dev") is not None and st < 100:
                    m["dev@%d" % st] = rows[st]["dev"]
        for k in ("gMean", "deMean", "deMax", "blackY", "contrast", "ratio95"):
            if sm.get(k) is not None:
                m[k] = sm[k]
        cs = an["color"]["summary"]
        if cs.get("deMean") is not None:
            m["colorDeMean"] = cs["deMean"]
        wde = cms_weighted_de(an["color"]["rows"])
        if wde is not None:
            m["colorDeW"] = round(wde, 2)
        for r in an["color"]["rows"]:
            if r.get("measured") and r["amp"] == 1.0:
                for k in ("de", "dh", "dC", "dY"):
                    if r.get(k) is not None:
                        m["%s:%s" % (r["hue"], k)] = r[k]
        if cs.get("innerDC") is not None:
            m["innerDC"], m["innerDY"] = cs["innerDC"], cs["innerDY"]
    else:
        sm = an["hdr"]["summary"]
        for k in ("peak", "refWhite", "refWhitePct", "scale", "balMax"):
            if sm.get(k) is not None:
                m[k] = sm[k]
        for r in an["hdr"]["rows"]:
            if r.get("measured") and r.get("bal"):
                b = r["bal"]
                m["R/G@PQ%d" % r["stim"]] = round((b[0] / b[1] - 1) * 100, 2)
                m["B/G@PQ%d" % r["stim"]] = round((b[2] / b[1] - 1) * 100, 2)
    if an.get("drift"):
        m["drift"] = an["drift"]["pct"]
    return m


METRIC_NAME = {"whiteY": "white, nits", "whiteCCT": "white, K", "gMean": "γ mean", "deMean": "gray ΔE mean",
             "deMax": "gray ΔE max", "blackY": "black, nits", "contrast": "contrast", "ratio95": "95/100 %",
             "colorDeMean": "color ΔE mean", "colorDeW": "color ΔE, weighted (mid tones ×2)", "innerDC": "sat 25–75 %, ΔC %", "innerDY": "sat 25–75 %, ΔY %",
             "peak": "HDR peak, nits", "refWhite": "HDR ref. white, nits", "refWhitePct": "ref. white, % of peak",
             "scale": "HDR shadows/mids, % of target", "balMax": "HDR balance max, %", "drift": "white drift during the run, %"}


def metric_name(k):
    if k in METRIC_NAME:
        return METRIC_NAME[k]
    if k.startswith("R/G@") or k.startswith("B/G@"):
        return "%s at %s %%" % (k[:3], k[4:]) if "PQ" not in k else "%s at %s %%" % (k[:3], k[4:])
    if k.startswith("dev@"):
        return "luminance at %s %% vs target, %%" % k[4:]
    if ":" in k:
        hue, f = k.split(":")
        return "%s %s" % (HUE_NAME.get(hue, hue), {"de": "ΔE", "dh": "Δh°", "dC": "ΔC %", "dY": "ΔY %"}[f])
    return k


def compare(m0, m1):
    """Metric changes between runs (only noticeable ones — above meter noise)."""
    out = []
    for k in m1:
        if k not in m0 or m0[k] is None or m1[k] is None:
            continue
        d = m1[k] - m0[k]
        if k in ("whiteY", "blackY", "peak", "refWhite"):
            rel = (m1[k] / m0[k] - 1) * 100 if m0[k] else 0
            sig = abs(rel) > (3 if k == "blackY" else NOISE["Y"])
            out.append({"key": k, "label": metric_name(k), "old": m0[k], "new": m1[k], "delta": d, "rel": rel,
                        "sig": sig})
            continue
        thr = (NOISE["bal"] if "/G@" in k else NOISE["dev"] if k.startswith("dev@") else NOISE["g"]
               if k == "gMean" else 30 if k == "whiteCCT" else 20 if k == "contrast" else NOISE["de"])
        out.append({"key": k, "label": metric_name(k), "old": m0[k], "new": m1[k], "delta": d, "sig": abs(d) > thr})
    return out


# ------------------------------------------------------------------ learning links
def learn(runs):
    """Pairs of consecutive runs of one mode → effects of settings groups.
    runs: [{"id", "mode", "settings", "metrics", "gray"(opt.)}] in chronological order."""
    know = []
    for mode in ("sdr", "hdr"):
        rr = [r for r in runs if r["mode"] == mode and r.get("metrics")]
        for a, b in zip(rr, rr[1:]):
            ch = PS.diff(a.get("settings"), b.get("settings"))
            if not ch:
                continue
            groups = sorted({c["group"] for c in ch})
            sep = separable(groups)
            entry = {"from": a["id"], "to": b["id"], "mode": mode, "changes": ch, "groups": groups,
                     "single": len(groups) == 1, "separable": sep, "effects": [], "perStep": {}}
            for c in compare(a["metrics"], b["metrics"]):
                if c["sig"]:
                    entry["effects"].append(c)
            if sep:
                entry["perGroup"] = {}
                for g in groups:
                    sub = {"perStep": {}, "_offsetToo": "wbOffset" in groups}
                    _per_step(sub, a, b, [c for c in ch if c["group"] == g], g)
                    if sub.get("fit") is not None:
                        entry["fit"] = sub["fit"]
                    entry["perGroup"][g] = sub["perStep"]
                    entry["perStep"].update(sub["perStep"])
            know.append(entry)
    return know


def separable(groups):
    """Whether the effects of several groups in one run can be separated: each moves its own metrics.
    Gain (R/G, B/G ratios) and gamma points (step luminance relative to white) barely interfere;
    different CMS colors are measured on different patches."""
    g = set(groups)
    if len(g) <= 1:
        return True
    # "families": balance (Gain or Offset — one of them), gamma, any CMS colors — each moves its own metrics
    wb = g & {"wbGain", "wbOffset"}
    rest = g - wb - {"gamma"}
    return len(wb) <= 1 and all(x.startswith("cms:") for x in rest)


def _steps(ch, path):
    for c in ch:
        if c["path"] == path:
            return (c["new"] or 0) - (c["old"] or 0)
    return 0


def _per_step(entry, a, b, ch, group):
    ma, mb = a["metrics"], b["metrics"]
    ps = entry["perStep"]
    if group == "wbOffset":
        for c in "RB":
            d = _steps(ch, "sdr.offset.%s" % c) - _steps(ch, "sdr.offset.G")
            ks = [(mb["%s/G@%d" % (c, st)] - ma["%s/G@%d" % (c, st)]) * st / 100.0 / d
                  for st in (50, 40, 30, 20) if d and "%s/G@%d" % (c, st) in ma and "%s/G@%d" % (c, st) in mb]
            if ks:
                ps["Offset %s" % c] = round(sorted(ks)[len(ks) // 2], 3)    # % at 100 % stimulus; at s — k/s
    elif group in ("wbGain", "hdrWb"):
        side = "sdr" if group != "hdrWb" else "hdr"
        kind = "gain" if group in ("wbGain",) else "offset"
        at = "@100" if group == "wbGain" else "@20"
        for ctl in (("gain", "offset") if group == "hdrWb" else (kind,)):
            for c in "RB":
                d = _steps(ch, "%s.%s.%s" % (side, ctl, c)) - _steps(ch, "%s.%s.G" % (side, ctl))
                key = "%s/G%s" % (c, at if group != "hdrWb" else ("@PQ75" if ctl == "gain" else "@PQ30"))
                if d and key in ma and key in mb:
                    ps["%s %s" % (ctl.capitalize(), c)] = round((mb[key] - ma[key]) / d, 3)
    elif group == "gamma" and a.get("gray") and b.get("gray") and not entry.get("_offsetToo"):
        deltas = {c["path"].split(".")[-1]: (c["new"] or 0) - (c["old"] or 0) for c in ch}
        lr = A.gp_learn(a["gray"], b["gray"], deltas)
        if lr:
            ps["point, % per step"] = lr["perStep"]
            entry["fit"] = lr["fit"]
    elif group.startswith("cms:"):
        col = group[4:]
        if col != "skin" and len(ch) == 1:
            f = ch[0]["path"].split(".")[-1]
            d = (ch[0]["new"] or 0) - (ch[0]["old"] or 0)
            for k in ("dY", "dC", "dh", "de"):
                key = "%s:%s" % (col, k)
                if d and key in ma and key in mb:
                    ps["%s → %s" % (f, k)] = round((mb[key] - ma[key]) / d, 3)
    elif group == "black":
        d = _steps(ch, "sdr.blackLevel")
        if d and "blackY" in ma and "blackY" in mb and ma["blackY"]:
            ps["black, % per step"] = round((mb["blackY"] / ma["blackY"] - 1) * 100 / d, 2)
    elif group == "contrast":
        d = _steps(ch, "sdr.contrast")
        if d and "ratio95" in ma and "ratio95" in mb:
            ps["95/100 per step"] = round((mb["ratio95"] - ma["ratio95"]) / d, 4)


def sensitivities(know):
    """Median of the learned "per step" values by key."""
    acc = {}
    for e in know:
        if not (e["single"] or e.get("separable")):
            continue
        for g, ps in (e.get("perGroup") or {e["groups"][0]: e["perStep"]}).items():
            for k, v in ps.items():
                acc.setdefault((g, k), []).append(v)
    return {k: sorted(v)[len(v) // 2] for k, v in acc.items()}


# ------------------------------------------------------------------ plan
def plan(an, settings, sens):
    """Prioritized list of actions for a run. Each: {group, title, text, set: [{path, label, from, to}]}."""
    items = []
    if an.get("unreliable"):
        items.append({"group": "reliability", "level": "ok", "title": "Re-measure unreliable patches",
                      "text": "Not counted: %s. Repeat the run (or measure these patches manually in the regular steps)." % ", ".join(
                          "%s (%s)" % (u["patchId"], ", ".join(u["flags"])) for u in an["unreliable"][:8])})
    if an["mode"] == "hdr":
        return items + _plan_hdr(an, settings, sens)
    gray, sm = an["gray"], an["gray"]["summary"]
    m = an["metrics"]
    if sm.get("contrast") and sm["contrast"] < 900:
        items.append({"group": "black", "level": "ok", "title": "Black is high (contrast %d:1)" % sm["contrast"],
                      "text": "Check the room light and Black Level with PLUGE (a Vidda C3S in a dark room reaches ~0.03 nits, "
                              "1300:1). While black is high, shadows cannot reach a clean γ."})
    if an.get("drift") and an["drift"]["pct"] > 2:
        items.append({"group": "drift", "level": "ok", "title": "White drifted by %.1f%% during the run" % an["drift"]["pct"],
                      "text": "The laser has not stabilized yet — warm the projector up for 45–60 minutes before a run." + (
                          " The program corrected the readings for the drift (%s, up to %.1f%%), but it is more precise without it." % (
                              "warm-up, τ ≈ %d s" % an["driftCorr"]["tau"] if an.get("driftCorr", {}).get("tau")
                              else "by the control whites", an["driftCorr"]["maxPct"]) if an.get("driftCorr") else "")})
    sdr = settings["sdr"]
    wb = _plan_wb(m, sdr, sens)
    if wb:
        items.append(wb)
        gray = _gray_after_offset(gray, wb, sdr, sens)
    gp_vals = {k: v or 0 for k, v in sdr["gammaPoints"].items()}
    per = sens.get(("gamma", "point, % per step"))
    pl = A.gp_plan(gray, gp_vals, per or A.GP_PER_STEP)
    q = A.gp_queue(gray)
    if q:
        after = " taking into account the Offset change above (it also shifts the shadow luminance)" if gray.get("predicted") else ""
        sets = [{"path": "sdr.gammaPoints.%d" % r["point"], "label": "Point %d" % r["point"], "from": r["now"],
                 "to": r["new"]} for r in pl if r["delta"]]
        items.append({"group": "gamma", "level": "bad", "title": "Gamma → Gamma / Control Point",
                      "text": "Steps out of tolerance: %s. Corrections computed for all points at once (%.1f%%/step%s)%s." % (
                          ", ".join("%d%%" % A.gp_stim(k) for k in q), per or A.GP_PER_STEP,
                          ", learned" if per else "", after), "set": sets})
    for col in ("red", "yellow", "green", "cyan", "blue", "magenta"):
        it = _plan_cms(an, sdr, sens, col)
        if it:
            items.append(it)
    for h in an["color"]["hints"]:
        if "Intermediate saturations" in h["text"]:
            items.append({"group": "inner", "level": "ok", "title": "Intermediate saturations", "text": h["text"]})
    if not [i for i in items if i["level"] == "bad"]:
        items.append({"group": "ok", "level": "good", "title": "SDR within tolerance ✓",
                      "text": "Gray, gamma and colors are fine. Next — optional small tweaks or HDR."})
    return items


def verdict(an, items, settings=None):
    """Overall run verdict: level, one line, what to set (all values at once) and what to do next."""
    total = len([x for x in an.get("segments_all", [])]) or None
    unrel = an.get("unreliable") or []
    bad = [i for i in items if i["level"] == "bad"]
    oks = [i for i in items if i["level"] == "ok" and i["group"] not in ("reliability",)]
    sets = []
    for i in items:
        for x in i.get("set") or []:
            if x["from"] != x["to"]:
                sets.append(dict(x, group=i["group"], title=i["title"]))
    lines = []
    m = an.get("metrics", {})
    if an["mode"] == "sdr":
        if m.get("whiteY"):
            lines.append("White %.1f nits%s" % (m["whiteY"], (", %d K" % m["whiteCCT"]) if m.get("whiteCCT") else ""))
        if m.get("gMean"):
            lines.append("γ %.2f (target %s)" % (m["gMean"], an["gray"]["summary"].get("gamma")))
        if m.get("deMean") is not None:
            lines.append("gray ΔE %.1f / max %.1f" % (m["deMean"], m["deMax"]))
        if m.get("colorDeW") is not None:
            lines.append("color ΔE %.1f (weighted by saturation; 100 %% — %.1f)" % (m["colorDeW"], m.get("colorDeMean") or 0))
        elif m.get("colorDeMean") is not None:
            lines.append("color ΔE %.1f" % m["colorDeMean"])
        if m.get("contrast"):
            lines.append("contrast %d:1" % m["contrast"])
    part = len(unrel) > 8
    if bad:
        level = "bad"
        head = "Needs fixing: " + ", ".join(i["title"].split(" →")[0].split(" (")[0] for i in bad)
    elif oks:
        level = "ok"
        head = "Within tolerance, can be improved: " + ", ".join(i["title"].split(" →")[0].split(" (")[0] for i in oks[:4])
    else:
        level = "good"
        head = "Everything within tolerance — nothing to fix"
    if part:
        head += " · incomplete run (%d patches not counted — repeat the run)" % len(unrel)
    groups = []
    for x in sets:
        if x["group"] not in groups:
            groups.append(x["group"])
    if sets:
        nxt = ("Set the values from the table in the projector menu, press “Set it → save to settings” and make a new "
               "full run — the program will show what changed and learn how much each setting step does. "
               "Safe to change together: Gain balance + gamma points; several CMS colors. Everything else — "
               "in a separate run. Order: %s." % " → ".join(PS.group_name(g) for g in groups))
    elif level == "good":
        nxt = "You can move on to HDR or finish. In 1–2 months — a check run (the laser ages)."
    else:
        nxt = "See the plan items below: where there is no exact value, one trial step and a new run are needed."
    return {"level": level, "headline": head, "summary": " · ".join(lines), "set": sets, "next": nxt,
            "partial": part}


def cms_weight(r):
    return CMS_WEIGHT.get(r["sat"], 0.5) if r["amp"] != 1.0 else 0.5


def _cms_reach(sat):
    """Share of a CMS step's effect at saturation sat (2026-11-04/05: 25 % — ≈0.27, 50 % — ≈0.6, from 75 % — full)."""
    return min(1.0, (sat / 0.75) ** 1.2)


def _cms_sim(r, db, dsat, kb, ks):
    """ΔE2000 of a patch if Bright is moved by db and Saturation by dsat steps (model: luminance ×(1+kb·f), chroma ×(1+ks·f))."""
    L, a, b = r["lab"]
    f = _cms_reach(r["sat"])
    fy = max(0.05, 1 + kb * db * f / 100.0)
    yr = ((L + 16) / 116.0) ** 3 if L > 8 else L / 903.3
    yr2 = yr * fy
    L2 = 116 * yr2 ** (1 / 3.0) - 16 if yr2 > 0.008856 else 903.3 * yr2
    k = fy ** (1 / 3.0) * max(0.05, 1 + ks * dsat * f / 100.0)
    return C.de2000((L2, a * k, b * k), tuple(r["tlab"]))


def cms_weighted_de(rows):
    w = [(cms_weight(r), r["de"]) for r in rows if r.get("measured") and r.get("de") is not None]
    return sum(x * d for x, d in w) / sum(x for x, _ in w) if w else None


def _plan_cms(an, sdr, sens, col):
    """Bright and Saturation of a hue — chosen by the lowest weighted ΔE over all its saturations (25–100 %).
    Color tone — only if its effect is already learned (the step direction on the Vidda is unknown)."""
    rows = [r for r in an["color"]["rows"] if r.get("measured") and r["hue"] == col and r.get("lab")]
    if len(rows) < 2:
        return None
    kb_l = sens.get(("cms:" + col, "bright → dY"))
    ks_l = sens.get(("cms:" + col, "sat → dC"))
    kb = kb_l if kb_l and abs(kb_l) > 0.5 else CMS_PRIOR["bright"]
    ks = ks_l if ks_l and abs(ks_l) > 0.3 else CMS_PRIOR["sat"]
    W = sum(cms_weight(r) for r in rows)

    def score(db, dsat):
        return sum(cms_weight(r) * _cms_sim(r, db, dsat, kb, ks) for r in rows) / W

    base = score(0, 0)
    rng_b = range(-3, 4) if kb_l else range(-2, 3)
    rng_s = range(-4, 5) if ks_l else range(-3, 4)
    best = min(((db, dsat) for db in rng_b for dsat in rng_s),
               key=lambda x: score(*x) + 0.04 * (abs(x[0]) + abs(x[1])))
    after = score(*best)
    if best == (0, 0) or base - after < 0.2 or base < 1.0:
        if base >= 2.5:      # a visible error that Bright/Saturation cannot remove — projector processing
            return {"group": "cms:" + col, "level": "ok", "title": "%s: hue ΔE %.1f — CMS does not help" % (
                PS.CMS_NAME[col], base), "text": "Trying Bright/Saturation improves by less than 0.2 ΔE: the error "
                "in mid saturations comes from projector processing. Try Color Space BT.709 instead of Auto in a separate "
                "run."}
        return None
    cur = sdr["cms"][col]
    sets, parts = [], []
    for f, d, learned in (("bright", best[0], kb_l), ("sat", best[1], ks_l)):
        if d:
            sets.append({"path": "sdr.cms.%s.%s" % (col, f), "label": "%s · %s" % (PS.CMS_NAME[col], dict(PS.CMS_FIELDS)[f]),
                         "from": cur[f] or 0, "to": (cur[f] or 0) + d})
            parts.append("%s %+d%s" % (dict(PS.CMS_FIELDS)[f], d, "" if learned else " (step is an estimate)"))
    by = ", ".join("%s%% %.1f" % (int(r["sat"] * 100) if r["amp"] != 1.0 else "100/100", r["de"])
                   for r in sorted(rows, key=lambda r: (r["amp"] == 1.0, r["sat"])))
    return {"group": "cms:" + col, "level": "bad" if base >= 3 else "ok",
            "title": "%s: hue ΔE %.1f → %.1f" % (PS.CMS_NAME[col], base, after),
            "text": "%s. Chosen over all saturations (mid tones weigh twice as much as 100 %%: in movies colors are almost never "
                    "at the limit). Current ΔE by saturation: %s." % ("; ".join(parts), by),
            "set": sets}


def _plan_wb(m, sdr, sens):
    """Gain and Offset together — by the shape of the R/G and B/G curve at 20–100 %: Gain shifts the whole scale equally,
    Offset acts like k/s (strong in shadows, slightly at white). Search over whole steps, least squares of deviations.
    Suggested only if noticeably better and something is out of tolerance (2 % on bright, 3–6 % on darker steps)."""
    sets, parts = [], []
    learned_any = False
    for c in "RB":
        pts = [(st / 100.0, m["%s/G@%d" % (c, st)]) for st in WB_STIMS if "%s/G@%d" % (c, st) in m]
        if len(pts) < 3:
            continue
        g_l = sens.get(("wbGain", "Gain %s" % c))
        k_l = sens.get(("wbOffset", "Offset %s" % c))
        g = g_l if g_l and abs(g_l) > 0.05 else PRIOR["wbGain"]
        k = k_l if k_l and abs(k_l) > 0.3 else OFFSET_K
        learned_any = learned_any or bool(g_l or k_l)
        gmax = 8 if g_l else 2          # first Gain trial — no more than 2 steps: the run will show "% per step"

        def resid(dg, do):
            return [v + g * dg + k * do / s for s, v in pts]

        def tol(s):              # visibility: the same balance % gives a smaller ΔE on dark steps; also for blue
            return (2.0 if s >= 0.6 else 3.0 if s >= 0.4 else 6.0) * (1.5 if c == "B" else 1.0)

        def norm(dg, do):
            return [abs(r) / tol(s) for (s, _), r in zip(pts, resid(dg, do))]

        def cost(dg, do):        # bright steps matter more: there are more of them on screen and they are brighter
            w = [1.0 if s >= 0.5 else 0.6 if s >= 0.3 else 0.4 for s, _ in pts]
            return sum(wi * x * x for wi, x in zip(w, norm(dg, do))) + 0.15 * (abs(dg) + abs(do))

        before = max(norm(0, 0))
        best = min(((dg, do) for dg in range(-gmax, gmax + 1) for do in range(-3, 4)), key=lambda x: cost(*x))
        after_n = max(norm(*best))
        after = max(abs(r) for r in resid(*best))
        # without a learned Gain ≤ 1.5 tolerance — within run-to-run spread (balance "breathes" by 2–3 % per hour);
        # when "% per step" is learned, fix everything out of tolerance; never make the worst step worse
        def worst(dg, do):       # worst step with the visibility weight
            return max((1.0 if s >= 0.5 else 0.6 if s >= 0.3 else 0.4) * x for (s, _), x in zip(pts, norm(dg, do)))

        ok = [x for x in ((dg, do) for dg in range(-gmax, gmax + 1) for do in range(-3, 4))
              if max(norm(*x)) <= max(before, 1.5) and worst(*x) <= worst(0, 0)]  # never spoil any step more
        best = min(ok, key=lambda x: cost(*x)) if ok else (0, 0)
        after_n = max(norm(*best))
        after = max(abs(r) for r in resid(*best))
        if before <= (1.0 if g_l else 1.5) or best == (0, 0) or cost(*best) > cost(0, 0) - 0.3:
            continue
        dg, do = best
        if dg:
            sets.append({"path": "sdr.gain.%s" % c, "label": "Gain %s" % c, "from": sdr["gain"][c] or 0,
                         "to": (sdr["gain"][c] or 0) + dg})
        if do:
            sets.append({"path": "sdr.offset.%s" % c, "label": "Offset %s" % c, "from": sdr["offset"][c] or 0,
                         "to": (sdr["offset"][c] or 0) + do})
        parts.append("%s/G: now %s → after the change up to %.1f%%%s" % (
            c, ", ".join("%d%% %+.1f" % (round(s * 100), v) for s, v in pts if s in (1.0, 0.5, 0.3, 0.2)), after,
            "" if (g_l or k_l) else " (Gain ≈ %.1f %%/step, Offset ≈ %.1f %%/step at white and ×5 at 20 %%)" % (g, k)))
    if not sets:
        return None
    groups = {x["path"].split(".")[1] for x in sets}
    grp = "wbGain" if groups == {"gain"} else "wbOffset" if groups == {"offset"} else "wb"
    return {"group": grp, "level": "bad", "title": "White balance → White Balance Gain/Offset",
            "text": "; ".join(parts) + ". Green is left alone.", "set": sets}


def _gray_after_offset(gray, wb, sdr, sens):
    """Gray scale forecast after an Offset change: an Offset step of channel c changes it by k/s %, and luminance by the
    channel share in Y (R 0.21, B 0.07); relative to white (which also changes slightly)."""
    share = {"R": 0.2126, "G": 0.7152, "B": 0.0722}
    dos = {x["path"].split(".")[2]: x["to"] - x["from"] for x in wb.get("set", []) if x["path"].startswith("sdr.offset.")}
    if not dos:
        return gray
    rows = []
    for r in gray["rows"]:
        r = dict(r)
        if r.get("measured") and r.get("dev") is not None and r["stim"] > 0:
            sv = r["stim"] / 100.0
            adj = 0.0
            for c, do in dos.items():
                k = sens.get(("wbOffset", "Offset %s" % c)) or OFFSET_K
                adj += share[c] * k * do * (1.0 / sv - 1.0)
            r["dev"] = ((1 + r["dev"] / 100.0) * (1 + adj / 100.0) - 1) * 100
        rows.append(r)
    return dict(gray, rows=rows, predicted=True)


def _plan_hdr(an, settings, sens):
    items = []
    h = an["hdr"]
    rows = {r["stim"]: r for r in h["rows"] if r.get("measured") and r.get("bal")}
    for st, ctl in ((75, "gain"), (60, "gain"), (40, "offset"), (30, "offset")):
        r = rows.get(st)
        if r and max(abs(b - 100) for b in r["bal"]) > 3:
            hints = A.balance_hints(r["bal"], ctl.capitalize())
            items.append({"group": "hdrWb", "level": "ok", "title": "HDR balance at PQ %d%%" % st,
                          "text": "; ".join(x["text"] for x in hints) + " (in the HDR memory)."})
            break
    for x in h["hints"]:
        if "HDR is dark" in x["text"] or "highlight headroom" in x["text"]:
            items.append({"group": "hdrTone", "level": "ok", "title": "HDR brightness", "text": x["text"]})
    if not items:
        items.append({"group": "ok", "level": "good", "title": "HDR is fine ✓",
                      "text": "Neutrality and tone mapping are within reasonable limits."})
    return items


def explain_knowledge(know):
    """Human-readable text of the learned links."""
    out = []
    for e in know:
        ch = ", ".join("%s %s→%s" % (c["label"], _fmt(c["old"]), _fmt(c["new"])) for c in e["changes"][:6])
        if len(e["changes"]) > 6:
            ch += " and %d more" % (len(e["changes"]) - 6)
        eff = "; ".join("%s %s → %s" % (x["label"], _fmt(x["old"]), _fmt(x["new"])) for x in e["effects"][:8])
        out.append({"from": e["from"], "to": e["to"], "mode": e["mode"], "changed": ch,
                    "groups": ", ".join(PS.group_name(g) for g in e["groups"]),
                    "single": e["single"] or e.get("separable", False), "effects": eff or "no noticeable changes",
                    "perStep": e["perStep"]})
    return out


def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return ("%.3g" % v) if abs(v) < 10 else ("%.1f" % v)
    return str(v)


def white_xy_ok(m):
    if m.get("white_x") is None:
        return None
    return math.hypot(m["white_x"] - C.D65_XY[0], m["white_y"] - C.D65_XY[1])
