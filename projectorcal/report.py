"""Report for printing / PDF: a standalone HTML page laid out for A4 (no overlaps).

Source — the same session and the same analyses as in the program. Charts — inline SVG.
"""
from __future__ import annotations

import datetime
import html
import math

from . import analysis as A
from . import colorimetry as C
from . import psettings as PS
from .catalog import HUE_NAME

CSS = """
@page { size: A4; margin: 13mm 12mm 14mm 12mm; }
* { box-sizing: border-box; }
body { font: 10pt/1.4 -apple-system, "Helvetica Neue", Arial, sans-serif; color: #1b1d20; background: #fff; margin: 0; }
.wrap { max-width: 186mm; margin: 0 auto; padding: 8mm 0; }
h1 { font-size: 19pt; margin: 0 0 2mm; }
h2 { font-size: 13pt; margin: 7mm 0 2.5mm; padding-bottom: 1.2mm; border-bottom: 1.5px solid #1b1d20; break-after: avoid; }
h3 { font-size: 10.5pt; margin: 4mm 0 1.5mm; break-after: avoid; }
p { margin: 1.2mm 0; }
.muted { color: #5f656d; } .small { font-size: 8.5pt; }
.good { color: #1e7a3f; } .ok { color: #8a6500; } .bad { color: #b2342b; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; font-variant-numeric: tabular-nums; margin: 1mm 0 2mm; }
th, td { border-bottom: 0.6px solid #c9ced4; padding: 1.1mm 1.4mm; text-align: right; vertical-align: top;
         overflow-wrap: anywhere; word-break: normal; font-size: 8.8pt; }
th { color: #4a5058; font-weight: 600; background: #f2f4f6; }
td.l, th.l { text-align: left; }
tr { break-inside: avoid; }
.block { break-inside: avoid; margin-bottom: 3mm; }
.cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 2.5mm; margin: 2mm 0; }
.c { border: 0.8px solid #c9ced4; border-radius: 2mm; padding: 2mm 2.5mm; break-inside: avoid; }
.c .k { font-size: 7.8pt; color: #5f656d; } .c .v { font-size: 13pt; font-weight: 600; } .c .s { font-size: 8pt; }
.pill { display: inline-block; padding: 0.3mm 2mm; border-radius: 3mm; font-size: 8pt; font-weight: 600; }
.pill.good { background: #dff2e6; } .pill.ok { background: #fbf0cf; } .pill.bad { background: #f8dcd9; }
svg { width: 100%; height: auto; display: block; }
.chart text { font-size: 9px; fill: #5f656d; }
.plan li { margin: 1mm 0; break-inside: avoid; }
.toolbar { position: sticky; top: 0; background: #fff; padding: 3mm 0; border-bottom: 1px solid #ddd; }
.toolbar button { font-size: 11pt; padding: 2mm 5mm; margin-right: 3mm; }
@media print { .toolbar { display: none; } .wrap { padding: 0; } }
@media screen { body { background: #e9ebee; } .wrap { background: #fff; padding: 10mm 12mm; margin: 6mm auto; box-shadow: 0 1px 6px rgba(0,0,0,.15); } }
"""

VER = {"good": ("good", "OK"), "ok": ("ok", "within tolerance"), "bad": ("bad", "needs work")}


def e(x):
    return html.escape("" if x is None else str(x))


def f(v, d=2):
    return "—" if not isinstance(v, (int, float)) or (isinstance(v, float) and math.isnan(v)) else ("%.*f" % (d, v))


def fs(v, d=1):
    return "—" if not isinstance(v, (int, float)) else ("%+.*f" % (d, v))


def fy(v):
    if not isinstance(v, (int, float)):
        return "—"
    a = abs(v)
    return "%.*f" % (1 if a >= 100 else 2 if a >= 10 else 3 if a >= 1 else 4, v)


def pill(v):
    if not v:
        return '<span class="pill">no data</span>'
    c, t = VER[v]
    return '<span class="pill %s">%s</span>' % (c, t)


def lv(x, good, ok):
    if not isinstance(x, (int, float)):
        return ""
    return "good" if abs(x) <= good else "ok" if abs(x) <= ok else "bad"


# ------------------------------------------------------------------ charts (SVG)
def svg_gamma(an, gamma):
    sm = an["summary"]
    Yw = sm.get("whiteY")
    if not Yw:
        return ""
    W, H, L, R, T, B = 640, 230, 46, 10, 8, 26
    Yb = sm.get("blackY") or 0.0

    def tgt(s):
        return C.sdr_target_Y(s, Yw, gamma, Yb)
    mx = Yw * 1.06
    X = lambda s: L + s / 100.0 * (W - L - R)  # noqa: E731
    Yf = lambda y: T + (1 - y / mx) * (H - T - B)  # noqa: E731
    g = []
    for s in range(0, 101, 10):
        g.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="#e1e4e8"/><text x="%.1f" y="%d" text-anchor="%s">%d%%</text>'
                 % (X(s), X(s), T, H - B, X(s), H - 9, "end" if s == 100 else "middle", s))
    step = [1, 2, 2.5, 5, 10, 20, 25, 50, 100]
    st = next(v for v in step if v >= mx / 5)
    v = 0.0
    while v <= mx:
        g.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="#e1e4e8"/><text x="2" y="%.1f">%g</text>'
                 % (L, W - R, Yf(v), Yf(v), Yf(v) + 3, v))
        v += st
    tp = " ".join("%s%.1f %.1f" % ("M" if i == 0 else "L", X(i), Yf(tgt(i / 100.0))) for i in range(0, 101))
    pts = sorted((r["stim"], r["Y"]) for r in an["rows"] if r.get("measured") and r.get("Y") is not None)
    mp = " ".join("%s%.1f %.1f" % ("M" if i == 0 else "L", X(s), Yf(y)) for i, (s, y) in enumerate(pts))
    dots = "".join('<circle cx="%.1f" cy="%.1f" r="2.6" fill="#2f6db5"/>' % (X(s), Yf(y)) for s, y in pts)
    return ('<svg class="chart" viewBox="0 0 %d %d">%s<path d="%s" fill="none" stroke="#8a9099" stroke-dasharray="5 4" '
            'stroke-width="1.4"/><path d="%s" fill="none" stroke="#2f6db5" stroke-width="1.8"/>%s</svg>'
            '<p class="small muted">Dashed — target (γ %s from the measured white), line — measured, nits.</p>'
            % (W, H, "".join(g), tp, mp, dots, "BT.1886" if gamma == "bt1886" else gamma))


def svg_dev(an):
    rows = [r for r in an["rows"] if r.get("measured") and r.get("dev") is not None and 0 < r["stim"] < 100]
    if not rows:
        return ""
    W, H, L, T, B, lim = 640, 130, 40, 8, 20, 20.0
    X = lambda s: L + s / 100.0 * (W - L - 10)  # noqa: E731
    Y = lambda d: T + (1 - (max(-lim, min(lim, d)) + lim) / (2 * lim)) * (H - T - B)  # noqa: E731
    g = "".join('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="#e1e4e8"/><text x="2" y="%.1f">%+d%%</text>'
                % (L, W - 10, Y(v), Y(v), Y(v) + 3, v) for v in (-20, -10, 0, 10, 20))
    col = {"good": "#3a9a5b", "ok": "#c9a227", "bad": "#c4453b"}
    bars = "".join('<rect x="%.1f" y="%.1f" width="12" height="%.1f" fill="%s"/><text x="%.1f" y="%d">%d</text>' % (
        X(r["stim"]) - 6, min(Y(0), Y(r["dev"])), max(1, abs(Y(r["dev"]) - Y(0))),
        col.get(r.get("gLevel"), "#3a9a5b"), X(r["stim"]) - 6, H - 5, r["stim"]) for r in rows)
    return '<svg class="chart" viewBox="0 0 %d %d">%s%s</svg><p class="small muted">Step luminance deviation from the target.</p>' % (
        W, H, g, bars)


LOCUS = [(0.1741, 0.0050), (0.1714, 0.0051), (0.1644, 0.0109), (0.1440, 0.0297), (0.1241, 0.0578), (0.0913, 0.1327),
         (0.0454, 0.2950), (0.0082, 0.5384), (0.0139, 0.7502), (0.0743, 0.8338), (0.1547, 0.8059), (0.2296, 0.7543),
         (0.3016, 0.6923), (0.3731, 0.6245), (0.4441, 0.5547), (0.5125, 0.4866), (0.5752, 0.4242), (0.6270, 0.3725),
         (0.6915, 0.3083), (0.7190, 0.2809), (0.7347, 0.2653)]
HUE_COL = {"red": "#d0584f", "yellow": "#c9b23a", "green": "#3f9e57", "cyan": "#3aa6b3", "blue": "#4a66c8",
           "magenta": "#b54fb0"}


def svg_xy(col):
    W, H, L, T, B = 300, 320, 24, 6, 18
    X = lambda x: L + x / 0.8 * (W - L - 6)  # noqa: E731
    Y = lambda y: T + (1 - y / 0.9) * (H - T - B)  # noqa: E731
    g = "".join('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="#eceef1"/><text x="%.1f" y="%d" text-anchor="%s">%.1f</text>'
                % (X(v / 10), X(v / 10), T, H - B, X(v / 10), H - 5, "end" if v == 8 else "middle", v / 10)
                for v in range(0, 9))
    g += "".join('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="#eceef1"/><text x="1" y="%.1f">%.1f</text>'
                 % (L, W - 6, Y(v / 10), Y(v / 10), Y(v / 10) + 3, v / 10) for v in range(0, 10))
    loc = " ".join("%s%.1f %.1f" % ("M" if i == 0 else "L", X(x), Y(y)) for i, (x, y) in enumerate(LOCUS)) + "Z"
    tri = " ".join("%s%.1f %.1f" % ("M" if i == 0 else "L", X(x), Y(y))
                   for i, (x, y) in enumerate(C.REC709_PRIMARIES_XY)) + "Z"
    marks = ""
    for r in col["rows"]:
        c = HUE_COL[r["hue"]]
        op = 1 if r["amp"] == 1.0 else 0.55
        marks += '<rect x="%.1f" y="%.1f" width="6" height="6" fill="none" stroke="%s" opacity="%s"/>' % (
            X(r["tx"]) - 3, Y(r["ty"]) - 3, c, op)
        if r.get("measured"):
            marks += ('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" opacity="%s"/>'
                      '<circle cx="%.1f" cy="%.1f" r="2.8" fill="%s" opacity="%s"/>') % (
                X(r["tx"]), Y(r["ty"]), X(r["x"]), Y(r["y"]), c, op, X(r["x"]), Y(r["y"]), c, op)
    return ('<svg class="chart" viewBox="0 0 %d %d" style="max-width:95mm">%s<path d="%s" fill="none" stroke="#8a9099"/>'
            '<path d="%s" fill="none" stroke="#1b1d20" stroke-dasharray="4 3"/>%s</svg>'
            '<p class="small muted">□ Rec.709 target · ● measured (pale — saturations 25–75 %%).</p>' % (W, H, g, loc, tri, marks))


def svg_hdr(h):
    W, H, L, R, T, B = 640, 220, 46, 10, 8, 26
    lo, hi = 0.05, 10000.0
    X = lambda s: L + s / 100.0 * (W - L - R)  # noqa: E731
    Yf = lambda y: T + (1 - (math.log10(max(y, lo)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))) * (H - T - B)  # noqa: E731
    g = "".join('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="#e1e4e8"/><text x="%.1f" y="%d" text-anchor="%s">%d%%</text>'
                % (X(s), X(s), T, H - B, X(s), H - 9, "end" if s == 100 else "middle", s) for s in range(0, 101, 10))
    g += "".join('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="#e1e4e8"/><text x="2" y="%.1f">%g</text>'
                 % (L, W - R, Yf(v), Yf(v), Yf(v) + 3, v) for v in (0.1, 1, 10, 100, 1000, 10000))
    tp = " ".join("%s%.1f %.1f" % ("M" if i == 5 else "L", X(i), Yf(C.pq_eotf(i / 100.0))) for i in range(5, 101))
    pts = sorted((r["stim"], r["Y"]) for r in h["rows"] if r.get("measured") and r.get("Y"))
    mp = " ".join("%s%.1f %.1f" % ("M" if i == 0 else "L", X(s), Yf(y)) for i, (s, y) in enumerate(pts))
    dots = "".join('<circle cx="%.1f" cy="%.1f" r="2.6" fill="#2f6db5"/>' % (X(s), Yf(y)) for s, y in pts)
    return ('<svg class="chart" viewBox="0 0 %d %d">%s<path d="%s" fill="none" stroke="#8a9099" stroke-dasharray="5 4"/>'
            '<path d="%s" fill="none" stroke="#2f6db5" stroke-width="1.8"/>%s</svg>'
            '<p class="small muted">Dashed — PQ target (ST.2084), line — measured (log scale, nits).</p>' % (W, H, g, tp, mp, dots))


# ------------------------------------------------------------------ tables
def tbl_gray(an):
    rows = [r for r in an["rows"] if r.get("measured") and r.get("bal")]
    if not rows:
        return '<p class="muted">no data</p>'
    h = ('<table><colgroup><col style="width:12%"><col><col><col><col><col><col><col><col><col></colgroup>'
         '<tr><th class="l">Step</th><th>Y, nits</th><th>Target</th><th>Dev.</th><th>γ eff.</th><th>ΔE color</th>'
         '<th>R</th><th>G</th><th>B</th><th>CCT</th></tr>')
    for r in rows:
        b = r["bal"]
        h += ('<tr><td class="l">%d%%</td><td>%s</td><td>%s</td><td class="%s">%s</td><td class="%s">%s</td>'
              '<td class="%s">%s</td>%s<td>%s</td></tr>') % (
            r["stim"], fy(r["Y"]), fy(r.get("Yt")), r.get("gLevel") or "", (fs(r["dev"]) + "%") if r.get("dev") is not None else "—",
            r.get("gLevel") or "", f(r.get("gEff")), r.get("deLevel") or "", f(r.get("deColor")),
            "".join('<td class="%s">%s</td>' % (lv(x - 100, 2, 5), f(x, 1)) for x in b), f(r.get("cct"), 0))
    return h + "</table>"


def tbl_color(col):
    rows = [r for r in col["rows"] if r.get("measured")]
    if not rows:
        return '<p class="muted">no data</p>'
    h = ('<table><colgroup><col style="width:17%"><col style="width:15%"><col style="width:15%"><col><col><col><col><col></colgroup>'
         '<tr><th class="l">Color</th><th>x / y meas.</th><th>x / y target</th><th>Y, nits</th><th>ΔE2000</th>'
         '<th>Δh°</th><th>ΔC %</th><th>ΔY %</th></tr>')
    for r in rows:
        name = HUE_NAME[r["hue"]].capitalize() + ("" if r["amp"] == 1.0 else " %d%%" % round(r["sat"] * 100))
        h += ('<tr><td class="l">%s</td><td>%.4f / %.4f</td><td>%.4f / %.4f</td><td>%s</td><td class="%s">%s</td>'
              '<td>%s</td><td>%s</td><td>%s</td></tr>') % (e(name), r["x"], r["y"], r["tx"], r["ty"], fy(r["Y"]),
                                                          lv(r["de"], 3, 5), f(r["de"]), fs(r.get("dh")),
                                                          fs(r.get("dC")), fs(r.get("dY")))
    return h + "</table>"


def tbl_hdr(h):
    rows = [r for r in h["rows"] if r.get("measured")]
    if not rows:
        return '<p class="muted">no data</p>'
    out = ('<table><tr><th class="l">PQ</th><th>Target, nits</th><th>Y, nits</th><th>% of target</th><th>R</th><th>G</th>'
           '<th>B</th><th>ΔE color</th><th>CCT</th></tr>')
    for r in rows:
        out += '<tr><td class="l">%d%%</td><td>%s</td><td>%s</td><td>%s</td>%s<td>%s</td><td>%s</td></tr>' % (
            r["stim"], f(r["tnits"], 1), fy(r["Y"]), f(r.get("ratio"), 1),
            "".join('<td class="%s">%s</td>' % (lv(x - 100, 2, 5), f(x, 1)) for x in r["bal"]),
            f(r.get("deColor")), f(r.get("cct"), 0))
    return out + "</table>"


def tbl_settings(ps):
    sdr, hdr = ps["sdr"], ps["hdr"]
    v = lambda x: "—" if x in (None, "") else e(x)  # noqa: E731
    out = ('<table><colgroup><col style="width:25%%"><col style="width:25%%"><col style="width:25%%"><col style="width:25%%">'
           '</colgroup><tr><th class="l">Mode</th><th class="l">Laser</th><th class="l">Black Level / Contrast</th>'
           '<th class="l">Color Temp / Color Space</th></tr><tr><td class="l">%s</td><td class="l">%s</td>'
           '<td class="l">%s / %s</td><td class="l">%s / %s</td></tr></table>') % (
        v(sdr["imageMode"]), v(sdr["laser"]), v(sdr["blackLevel"]), v(sdr["contrast"]), v(sdr["colorTemp"]),
        v(sdr["colorSpace"]))
    out += ('<table><tr><th class="l">SDR balance</th><th>R</th><th>G</th><th>B</th><th class="l">HDR balance</th><th>R</th>'
            '<th>G</th><th>B</th></tr>')
    for k, lab in (("gain", "Gain"), ("offset", "Offset")):
        out += '<tr><td class="l">%s</td>%s<td class="l">%s</td>%s</tr>' % (
            lab, "".join("<td>%s</td>" % v(sdr[k][c]) for c in "RGB"), lab,
            "".join("<td>%s</td>" % v(hdr[k][c]) for c in "RGB"))
    out += "</table>"
    pts = sdr["gammaPoints"]
    out += ('<p><b>Gamma:</b> preset %s · points (Control Point → Gain):</p><table><tr>%s</tr><tr>%s</tr></table>'
            % (v(sdr["gammaPreset"]), "".join("<th>%s<br><span class='muted'>%d%%</span></th>" % (k, 5 * k)
                                              for k in range(1, 21)),
               "".join("<td>%s</td>" % v(pts[str(k)]) for k in range(1, 21))))
    out += '<table><tr><th class="l">CMS</th><th>Color tone</th><th>Saturation</th><th>Bright</th></tr>'
    for col in PS.CMS_COLORS:
        c = sdr["cms"][col]
        out += '<tr><td class="l">%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            e(PS.CMS_NAME[col]), v(c["tone"]), v(c["sat"]), v(c["bright"]))
    out += "</table>"
    more = ["Color Saturation %s" % v(sdr.get("saturation")), "Tint %s" % v(sdr.get("tint")),
            "Sharpness %s" % v(sdr.get("sharpness"))]
    more += ["%s: %s" % (e(lab), e(sdr.get("proc", {}).get(f))) for f, lab in PS.PROC_FIELDS
             if sdr.get("proc", {}).get(f)]
    more += [e(x) for x in (sdr.get("extra") or {}).values() if x]
    if sdr.get("other"):
        more.append(e(sdr["other"]))
    out += '<p class="small"><b>More SDR:</b> %s</p>' % " · ".join(more)
    out += ('<p><b>HDR10:</b> mode %s · laser %s · tone mapping %s · Black/Contrast %s / %s · Color Temp %s · '
            'Gamma %s · Color Space %s · Saturation %s</p>') % (
        v(hdr["imageMode"]), v(hdr["laser"]), v(hdr["toneMapping"]), v(hdr["blackLevel"]), v(hdr["contrast"]),
        v(hdr.get("colorTemp")), v(hdr.get("gammaPreset")), v(hdr.get("colorSpace")), v(hdr.get("saturation")))
    return out


def plan_html(items):
    if not items:
        return ""
    out = '<ol class="plan">'
    for it in items:
        sets = ""
        if it.get("set"):
            sets = " <span class='small'>[" + "; ".join("%s: %s → <b>%s</b>" % (e(x["label"]), e(x["from"]), e(x["to"]))
                                                         for x in it["set"]) + "]</span>"
        out += '<li><b class="%s">%s</b> — %s%s</li>' % (it.get("level") or "", e(it["title"]), e(it["text"]), sets)
    return out + "</ol>"


def verdict_html(v):
    if not v:
        return ""
    out = '<div class="block"><h3>Verdict</h3><p class="%s" style="font-size:12pt"><b>%s</b></p>' % (
        e(v["level"]), e(v["headline"]))
    if v.get("summary"):
        out += '<p class="small muted">%s</p>' % e(v["summary"])
    if v.get("set"):
        out += ('<table><colgroup><col style="width:40%%"><col style="width:30%%"><col style="width:15%%">'
                '<col style="width:15%%"></colgroup><tr><th class="l">What</th><th class="l">Setting</th>'
                '<th>Now</th><th>Set to</th></tr>%s</table>') % "".join(
            '<tr><td class="l small">%s</td><td class="l">%s</td><td>%s</td><td><b>%s</b></td></tr>' % (
                e(x["title"]), e(x["label"]), e(x["from"]), e(x["to"])) for x in v["set"])
    out += '<p class="small">%s</p></div>' % e(v["next"])
    return out


def card(k, v, s=""):
    return '<div class="c"><div class="k">%s</div><div class="v">%s</div><div class="s">%s</div></div>' % (k, v, s)


# ------------------------------------------------------------------ assembly
def report_title(full):
    """Title = PDF file name on "Save as PDF": by the date and time of the actual last run,
    e.g. "ProjectorCal 2026-11-05 21-08 SDR-5". Without runs — by the current time."""
    runs = [r for r in (full.get("runs") or []) if r.get("startedAt")]
    if runs:
        last = max(runs, key=lambda r: r["startedAt"])
        d = datetime.datetime.fromisoformat(last["startedAt"])
        ids = [x["run"]["id"] for x in (full.get("latest") or {}).values() if x.get("run")]
        tag = " ".join(sorted(ids)) or last["id"]
    else:
        d, tag = datetime.datetime.now(), "no run"
    return "ProjectorCal %s %s" % (d.strftime("%Y-%m-%d %H-%M"), tag)


def build(ctl):
    with ctl.lock:
        sv = ctl.session_view()
    s, an = sv["session"], sv["analysis"]
    full = an.get("full") or {}
    pp = s.get("projectorProfile") or {}
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    title = report_title(full)
    o = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>%s</title>'
         '<style>%s</style></head><body><div class="wrap">' % (e(title), CSS)]
    o.append('<div class="toolbar"><button onclick="window.print()">Print / save PDF</button>'
             '<span class="muted small">In the print dialog: “Save as PDF”, default margins.</span></div>')
    o.append('<h1>Calibration of %s</h1><p class="muted">%s · report of %s · ProjectorCal</p>' % (
        e(pp.get("name") or "Vidda C3S"), e(title.replace("ProjectorCal ", "", 1)), now))
    o.append('<p class="small">Chain: source → HDMI → projector → %s. Meter: ArgyllCMS spotread %s%s. '
             'SDR target: D65, γ %s from the measured white, Rec.709.</p>' % (
                 e(pp.get("notes") or "screen"), e((s.get("meter") or {}).get("flag") or "-e"),
                 " · correction " + e(s["meter"]["ccmx"]) if (s.get("meter") or {}).get("ccmx") else " · no spectral correction",
                 "BT.1886" if s.get("gamma") == "bt1886" else e(s.get("gamma"))))

    latest = full.get("latest", {})
    sdr_l, hdr_l = latest.get("sdr"), latest.get("hdr")
    # summary
    o.append("<h2>Summary</h2>")
    if sdr_l:
        m, g = sdr_l["analysis"]["metrics"], sdr_l["analysis"]["gray"]["summary"]
        o.append('<div class="cards">%s%s%s%s%s%s%s%s</div>' % (
            card("White", fy(m.get("whiteY")) + " nits", "%s K" % f(m.get("whiteCCT"), 0)),
            card("Black / contrast", fy(m.get("blackY")), ("%d:1" % m["contrast"]) if m.get("contrast") else ""),
            card("γ eff. 10–90 %", f(m.get("gMean")), e(g.get("gText") or "")),
            card("Gray ΔE mean/max", "%s / %s" % (f(m.get("deMean")), f(m.get("deMax"))), pill(g.get("verdict"))),
            card("Balance 20–100 %", (f(g.get("balMax"), 1) + "%") if g.get("balMax") is not None else "—", "max dev."),
            card("Color ΔE (weighted)", f(m.get("colorDeW") if m.get("colorDeW") is not None else m.get("colorDeMean")), pill(sdr_l["analysis"]["color"]["summary"].get("verdict"))),
            card("Saturations 25–75 %", "%s / %s" % (fs(m.get("innerDC"), 0), fs(m.get("innerDY"), 0)), "ΔC % / ΔY %"),
            card("Run", e(sdr_l["run"]["id"]), e(sdr_l["run"]["startedAt"].replace("T", " ")[:16]))))
    if hdr_l:
        h = hdr_l["analysis"]["hdr"]["summary"]
        o.append('<div class="cards">%s%s%s%s</div>' % (
            card("HDR peak", f(h.get("peak"), 1) + " nits", ""),
            card("HDR ref. white", f(h.get("refWhite"), 1) + " nits", "%s%% of peak (target 50–70 %%)" % f(h.get("refWhitePct"), 0)),
            card("Shadows/mids", f(h.get("scale"), 0) + "%", "of PQ target"),
            card("HDR neutrality", pill(h.get("verdict")), "max %s%%" % f(h.get("balMax"), 1))))
    if not sdr_l and not hdr_l:
        o.append('<p class="muted">No full check yet — results of the manual steps below.</p>')

    o.append('<h2>Projector settings</h2>%s' % tbl_settings(s["psettings"]))

    if sdr_l:
        A_ = sdr_l["analysis"]
        o.append('<h2>SDR: full check %s</h2>' % e(sdr_l["run"]["id"]))
        sync = sdr_l["run"].get("sync") or {}
        total = len([x for x in sdr_l["run"]["segments"] if x["role"] == "patch"])
        o.append('<p class="small">Counted %d of %d patches · %s%s</p>' % (
            total - len(A_["unreliable"]), total,
            "the video reached the end without a shift" if sync.get("endMarker") else "finish mark not found",
            (" · white drift %s%%" % f(A_["drift"]["pct"], 1)) if A_.get("drift") else ""))
        if A_["unreliable"]:
            o.append('<p class="small ok">Not counted: %s</p>' % e(", ".join("%s (%s)" % (u["patchId"], ", ".join(u["flags"]))
                                                                        for u in A_["unreliable"])))
        o.append(verdict_html(sdr_l.get("verdict")))
        o.append('<h3>Adjustment plan</h3>%s' % plan_html(sdr_l["plan"]))
        o.append('<div class="block"><h3>Gamma</h3>%s%s</div>' % (svg_gamma(A_["gray"], s.get("gamma", "2.4")),
                                                                   svg_dev(A_["gray"])))
        o.append('<div class="block"><h3>Gray scale</h3>%s</div>' % tbl_gray(A_["gray"]))
        o.append('<div class="block"><h3>Colors (100 %% and saturations)</h3>%s</div>' % tbl_color(A_["color"]))
        o.append('<div class="block"><h3>xy diagram</h3>%s</div>' % svg_xy(A_["color"]))
    if hdr_l:
        A_ = hdr_l["analysis"]
        o.append('<h2>HDR10: full check %s</h2>' % e(hdr_l["run"]["id"]))
        o.append(verdict_html(hdr_l.get("verdict")))
        o.append('<h3>Plan</h3>%s' % plan_html(hdr_l["plan"]))
        o.append('<div class="block">%s</div><div class="block">%s</div>' % (svg_hdr(A_["hdr"]), tbl_hdr(A_["hdr"])))

    if not sdr_l:
        g = an.get("verify") if an.get("verify", {}).get("summary", {}).get("measuredCount") else an.get("gpoints")
        if g and g["summary"].get("measuredCount"):
            o.append('<h2>SDR: gray scale (manual pass)</h2><div class="block">%s%s</div>%s' % (
                svg_gamma(g, s.get("gamma", "2.4")), svg_dev(g), tbl_gray(g)))
        if an.get("color", {}).get("summary", {}).get("count"):
            o.append('<h2>SDR: colors (manual pass)</h2>%s<div class="block">%s</div>' % (
                tbl_color(an["color"]), svg_xy(an["color"])))
    if not hdr_l and an.get("hdr", {}).get("summary", {}).get("count"):
        o.append('<h2>HDR10 (manual pass)</h2><div class="block">%s</div>%s' % (svg_hdr(an["hdr"]), tbl_hdr(an["hdr"])))

    runs = full.get("runs") or []
    if runs:
        o.append('<h2>Run history</h2><table><colgroup><col style="width:9%"><col style="width:13%"><col style="width:8%">'
                 '<col style="width:8%"><col style="width:7%"><col style="width:8%"><col style="width:8%"><col></colgroup>'
                 '<tr><th class="l">Run</th><th class="l">When</th><th>White</th><th>γ</th><th>Gray ΔE</th>'
                 '<th>Color ΔE</th><th>Contr.</th><th class="l">Settings changes before the run</th></tr>')
        for r in runs:
            m = r["metrics"]
            o.append('<tr><td class="l">%s</td><td class="l">%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
                     '<td class="l small">%s</td></tr>' % (
                         e(r["id"]), e(r["startedAt"].replace("T", " ")[5:16]), fy(m.get("whiteY") or m.get("peak")),
                         f(m.get("gMean")), f(m.get("deMean")), f(m.get("colorDeMean")),
                         f(m.get("contrast"), 0), e(", ".join("%s %s→%s" % (c["label"], c["old"], c["new"])
                                                             for c in r["changes"]) or "—")))
        o.append("</table>")
    know = full.get("knowledge") or []
    if know:
        o.append("<h2>Setting links (from your runs)</h2>")
        if full.get("sens"):
            o.append('<table style="width:70%%"><tr><th class="l">Group</th><th class="l">What</th><th>Per step</th></tr>%s</table>'
                     % "".join('<tr><td class="l">%s</td><td class="l">%s</td><td>%s</td></tr>' % (
                         e(x["group"]), e(x["key"]), fs(x["value"], 2)) for x in full["sens"]))
        for k in know:
            o.append('<p class="small"><b>%s → %s</b>: %s <span class="muted">(%s%s)</span><br>→ %s</p>' % (
                e(k["from"]), e(k["to"]), e(k["changed"]), e(k["groups"]),
                "" if k["single"] else " — several groups, effect cannot be separated", e(k["effects"])))
    ev = s.get("events", [])[-40:]
    if ev:
        o.append('<h2>Change log</h2><table><colgroup><col style="width:18%%"><col></colgroup>%s</table>' % "".join(
            '<tr><td class="l small">%s</td><td class="l small">%s</td></tr>' % (e(x["t"].replace("T", " ")[:16]), e(x["text"]))
            for x in ev))
    o.append("</div></body></html>")
    return "".join(o)
