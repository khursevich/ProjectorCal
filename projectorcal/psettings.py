"""Projector settings (what is set in the menu), structured: for the form, run snapshots and learning.

Groups tell what exactly changed between two runs so the program can learn from it:
"only Gain was changed" → the effect can be attributed to Gain.
"""
from __future__ import annotations

import copy
import re

CMS_COLORS = ["red", "yellow", "green", "cyan", "blue", "magenta", "skin"]
CMS_NAME = {"red": "Red", "yellow": "Yellow", "green": "Green", "cyan": "Cyan", "blue": "Blue",
          "magenta": "Magenta (Pink)", "skin": "Skin"}
CMS_FIELDS = [("tone", "Color tone"), ("sat", "Saturation"), ("bright", "Bright")]
GP_COUNT = 20
EXTRA_COUNT = 6
OLD_LASER_KEY = "Brightness level (\u043b\u0430\u0437\u0435\u0440)"   # menu key of sessions before v1.6
PROC_FIELDS = [("dynContrast", "Dynamic/adaptive contrast"), ("motion", "Motion (MEMC)"),
               ("noise", "Noise reduction (NR)"), ("hdmiRange", "HDMI range (Full/Limited/Auto)"),
               ("localContrast", "Local contrast / detail"), ("lightSensor", "Light sensor / Eye care")]


def fresh():
    rgb = lambda: {"R": 0, "G": 0, "B": 0}  # noqa: E731
    return {
        "sdr": {"imageMode": "Filmmaker", "laser": None, "blackLevel": None, "contrast": None, "colorTemp": "",
                "colorSpace": "", "saturation": None, "tint": None, "gain": rgb(), "offset": rgb(), "gammaPreset": "",
                "gammaPoints": {str(k): 0 for k in range(1, GP_COUNT + 1)},
                "cms": {c: {f: 0 for f, _ in CMS_FIELDS} for c in CMS_COLORS},
                "sharpness": None, "proc": {f: "" for f, _ in PROC_FIELDS},
                "extra": {str(k): "" for k in range(1, EXTRA_COUNT + 1)}, "other": ""},
        "hdr": {"imageMode": "", "laser": None, "toneMapping": "", "blackLevel": None, "contrast": None,
                "colorTemp": "", "gammaPreset": "", "colorSpace": "", "saturation": None,
                "gain": rgb(), "offset": rgb(), "other": ""},
    }


def fields():
    """Flat list of fields: (path, label, group, type). Order = form order."""
    out = [
        ("sdr.imageMode", "Picture mode", "mode", "text"),
        ("sdr.laser", "Laser level (Brightness)", "laser", "int"),
        ("sdr.blackLevel", "Black Level", "black", "int"),
        ("sdr.contrast", "Contrast", "contrast", "int"),
        ("sdr.colorTemp", "Color Temp (preset)", "wbPreset", "text"),
        ("sdr.saturation", "Color Saturation", "saturation", "int"),
        ("sdr.tint", "Tint / Hue", "saturation", "int"),
    ]
    for c in "RGB":
        out.append(("sdr.gain.%s" % c, "Gain %s" % c, "wbGain", "int"))
    for c in "RGB":
        out.append(("sdr.offset.%s" % c, "Offset %s" % c, "wbOffset", "int"))
    out.append(("sdr.gammaPreset", "Gamma (preset)", "gammaPreset", "text"))
    for k in range(1, GP_COUNT + 1):
        out.append(("sdr.gammaPoints.%d" % k, "Point %d" % k, "gamma", "int"))
    out.append(("sdr.colorSpace", "Color Space", "colorSpace", "text"))
    for col in CMS_COLORS:
        for f, lab in CMS_FIELDS:
            out.append(("sdr.cms.%s.%s" % (col, f), "%s · %s" % (CMS_NAME[col], lab), "cms:" + col, "int"))
    out.append(("sdr.sharpness", "Sharpness", "other", "int"))
    for f, lab in PROC_FIELDS:
        out.append(("sdr.proc.%s" % f, lab, "processing", "text"))
    for k in range(1, EXTRA_COUNT + 1):
        out.append(("sdr.extra.%d" % k, "Custom setting %d (name = value)" % k, "extra", "text"))
    out += [("sdr.other", "Notes (SDR)", "other", "text"),
            ("hdr.imageMode", "HDR: picture mode", "hdrMode", "text"), ("hdr.laser", "HDR: laser level", "hdrLaser", "int"),
            ("hdr.toneMapping", "HDR: tone mapping", "hdrTone", "text"),
            ("hdr.blackLevel", "HDR: Black Level", "hdrBlack", "int"), ("hdr.contrast", "HDR: Contrast", "hdrBlack", "int"),
            ("hdr.colorTemp", "HDR: Color Temp", "hdrWb", "text"), ("hdr.gammaPreset", "HDR: Gamma (preset)", "hdrTone", "text"),
            ("hdr.colorSpace", "HDR: Color Space", "hdrColor", "text"),
            ("hdr.saturation", "HDR: Color Saturation", "hdrColor", "int")]
    for c in "RGB":
        out.append(("hdr.gain.%s" % c, "HDR: Gain %s" % c, "hdrWb", "int"))
    for c in "RGB":
        out.append(("hdr.offset.%s" % c, "HDR: Offset %s" % c, "hdrWb", "int"))
    out.append(("hdr.other", "Notes (HDR)", "other", "text"))
    return out


FIELD_BY_PATH = {f[0]: f for f in fields()}

GROUP_NAME = {"mode": "picture mode", "laser": "laser", "black": "Black Level", "contrast": "Contrast",
            "wbPreset": "color temperature preset", "wb": "white balance", "wbGain": "Gain balance", "wbOffset": "Offset balance",
            "gammaPreset": "gamma preset", "gamma": "gamma points", "colorSpace": "Color Space", "other": "other",
            "hdrMode": "HDR mode", "hdrLaser": "HDR laser", "hdrTone": "HDR tone mapping",
            "hdrBlack": "HDR Black/Contrast", "hdrWb": "HDR balance", "saturation": "saturation/tint",
            "processing": "picture processing", "extra": "custom settings", "hdrColor": "HDR color"}


def group_name(g):
    if g.startswith("cms:"):
        return "CMS " + CMS_NAME[g[4:]].lower()
    return GROUP_NAME.get(g, g)


# What each group affects (which checks become outdated)
AFFECTS = {"mode": "everything", "laser": "all of SDR (brightness and balance)", "black": "black, shadows, gamma",
           "contrast": "white and bright steps", "wbPreset": "balance across the scale", "wbGain": "balance of bright steps, slightly everything",
           "wbOffset": "balance of dark steps, slightly the mids", "gammaPreset": "the whole gamma (may reset the points)",
           "gamma": "brightness of steps near the point (±5 %)", "colorSpace": "all colors",
           "hdrWb": "HDR balance (possibly SDR too — check)", "hdrTone": "HDR brightness",
           "saturation": "all colors and saturations", "processing": "may change everything (gamma, black, colors)",
           "extra": "unknown — check with a full run"}


def get(s, path):
    cur = s
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def set_(s, path, value):
    parts = path.split(".")
    cur = s
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    kind = FIELD_BY_PATH.get(path, (None, None, None, "text"))[3]
    if kind == "int":
        if value in (None, ""):
            value = None
        else:
            try:
                value = int(round(float(str(value).replace(",", ".").replace("−", "-"))))
            except ValueError:
                value = None
    else:
        value = str(value or "")[:120]
    old = cur.get(parts[-1])
    cur[parts[-1]] = value
    return old, value


def flatten(s):
    return {f[0]: get(s, f[0]) for f in fields()}


def diff(a, b):
    """Changes between two settings sets: [{path, label, group, old, new}]."""
    fa, fb = flatten(a or fresh()), flatten(b or fresh())
    out = []
    for path, label, group, kind in fields():
        x, y = fa.get(path), fb.get(path)
        if (x or 0) != (y or 0) if kind == "int" else (x or "") != (y or ""):
            out.append({"path": path, "label": label, "group": group, "old": x, "new": y})
    return out


def migrate(s, menu_values=None, gp_values=None):
    """Complete the structure; migrate old free-text menu values and the wizard's gamma points."""
    base = fresh()
    if s is None:
        s = base
        if menu_values:
            _import_menu(s, menu_values)
    else:
        for side in base:
            s.setdefault(side, {})
            for k, v in base[side].items():
                if isinstance(v, dict):
                    s[side].setdefault(k, copy.deepcopy(v))
                    for kk, vv in v.items():
                        if isinstance(vv, dict):
                            s[side][k].setdefault(kk, copy.deepcopy(vv))
                            for k3, v3 in vv.items():
                                s[side][k][kk].setdefault(k3, v3)
                        else:
                            s[side][k].setdefault(kk, vv)
                else:
                    s[side].setdefault(k, v)
    if gp_values and not any(s["sdr"]["gammaPoints"].values()):
        for k, v in gp_values.items():
            if str(k) in s["sdr"]["gammaPoints"]:
                s["sdr"]["gammaPoints"][str(k)] = int(v or 0)
    return s


def _num(x):
    try:
        return int(round(float(str(x).strip().replace("−", "-").replace(",", "."))))
    except (TypeError, ValueError):
        return None


def _import_menu(s, mv):
    sdr, hdr = mv.get("sdr", {}), mv.get("hdr10", {})
    s["sdr"]["imageMode"] = sdr.get("Image Mode") or s["sdr"]["imageMode"]
    s["sdr"]["laser"] = _num(sdr.get("Brightness level (laser)", sdr.get(OLD_LASER_KEY)))
    s["sdr"]["blackLevel"] = _num(sdr.get("Black Level"))
    s["sdr"]["contrast"] = _num(sdr.get("Contrast"))
    s["sdr"]["colorTemp"] = sdr.get("Color Temp", "")
    s["sdr"]["colorSpace"] = sdr.get("Color Space", "")
    s["sdr"]["gammaPreset"] = sdr.get("Gamma", "")
    s["sdr"]["sharpness"] = _num(sdr.get("Sharpness"))
    for c in "RGB":
        s["sdr"]["gain"][c] = _num(sdr.get("Gain " + c)) or 0
        s["sdr"]["offset"][c] = _num(sdr.get("Offset " + c)) or 0
        s["hdr"]["gain"][c] = _num(hdr.get("Gain " + c)) or 0
        s["hdr"]["offset"][c] = _num(hdr.get("Offset " + c)) or 0
    names = {"red": "CMS Red", "yellow": "CMS Yellow", "green": "CMS Green", "cyan": "CMS Cyan", "blue": "CMS Blue",
             "magenta": "CMS Magenta/Pink"}
    for col, key in names.items():
        parts = [p for p in re.split(r"[,;/ ]+", sdr.get(key, "") or "") if p]
        for (f, _), v in zip(CMS_FIELDS, parts):
            s["sdr"]["cms"][col][f] = _num(v) or 0
    pts = sdr.get("Gamma Control Points", "") or ""
    for k, v in re.findall(r"(\d+)\s*:\s*([+\-−]?\d+)", pts):
        if k in s["sdr"]["gammaPoints"]:
            s["sdr"]["gammaPoints"][k] = _num(v) or 0
    s["hdr"]["imageMode"] = hdr.get("Image Mode", "")
    s["hdr"]["laser"] = _num(hdr.get("Brightness level (laser)", hdr.get(OLD_LASER_KEY)))
    s["hdr"]["toneMapping"] = hdr.get("Dynamic tone mapping", "")


def gp_text(s):
    pts = s["sdr"]["gammaPoints"]
    ch = ["%s:%+d" % (k, v) for k, v in sorted(pts.items(), key=lambda kv: int(kv[0])) if v]
    return ", ".join(ch) if ch else "all 0"
