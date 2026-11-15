"""Projector and meter profiles.

A projector profile sets the gamma point layout (how many Control Points the Expert menu has and which stimulus
each one controls) and the hints shown in the UI. Only the Vidda C3S is verified with real runs; the other models
start from the same assumptions and say so. Step sizes (Gain, Offset, gamma point, CMS) are never taken from a
profile as facts: the program learns them from your own runs.

A meter profile sets which auto-run video to play (standard or slow schedule) and the setup notes. The meter family
is suggested from the instrument name in `spotread -?`; the i1Display family shares one USB name, so the exact model
is confirmed by the user.
"""
from __future__ import annotations

import re

# gamma layouts: number of Control Points → % stimulus per point (point k controls k·step %)
GP_LAYOUTS = {20: 5, 10: 10, 0: None}

PROJECTORS = [
    {"id": "vidda-c3s", "name": "Vidda C3S", "gpCount": 20, "verified": True,
     "note": "Developed and verified on this model: 20 gamma points at 5 %, 2-point white balance, 7-color CMS."},
    {"id": "vidda-c3", "name": "Vidda C3 / C3 Pro / C3 Ultra", "gpCount": 20, "verified": False,
     "note": "Same VIDAA Expert menu family as the C3S. The 20-point layout is assumed: confirm it once with "
             "Gamma by points → point probe."},
    {"id": "hisense-c2", "name": "Hisense / Vidda C2, C2 Pro, C2 Ultra, C2 Max", "gpCount": 20, "verified": False,
     "note": "VIDAA picture menu with white balance, gamma and CMS. Gamma point count not verified: run the point "
             "probe, or pick the 10-point layout if your menu shows 10 points."},
    {"id": "hisense-c1", "name": "Hisense / Vidda C1, C1S", "gpCount": 20, "verified": False,
     "note": "Not verified. Check how many gamma Control Points your menu has and pick the layout below."},
    {"id": "hisense-px", "name": "Hisense PX1 / PX2 / PX3 (Pro) laser UST", "gpCount": 20, "verified": False,
     "note": "These have White Balance and Gamma Calibration in Expert settings. Point count not verified: "
             "pick the layout that matches your menu."},
    {"id": "hisense-ltv", "name": "Hisense Laser TV (L5 / L9 / similar)", "gpCount": 0, "verified": False,
     "note": "Usually 2-point and 10/20-point white balance instead of luminance-only gamma points. Start with "
             "'no point gamma': the program corrects white balance, gamma preset and CMS."},
    {"id": "vidda-other", "name": "Other Vidda / Hisense projector", "gpCount": 20, "verified": False,
     "note": "Pick the gamma layout that matches your menu. Everything else is learned from your runs."},
    {"id": "other", "name": "Other projector or TV", "gpCount": 0, "verified": False,
     "note": "Any display with Gain/Offset white balance and a CMS works. Pick the gamma layout that matches "
             "your menu (or none)."},
]
PROJECTOR_BY_ID = {p["id"]: p for p in PROJECTORS}

# speed: which auto-run video to play — "std" (tuned to i1Display Pro/Plus) or "slow" (twice the time per reading)
METERS = [
    {"id": "i1d3", "name": "i1Display Pro / Pro Plus, Calibrite ColorChecker Display Pro / Plus / Plus HL",
     "speed": "std", "verified": True, "kind": "colorimeter",
     "note": "Verified (Display Plus HL). Fast on bright patches, up to ~10 s on black. Calibrite Plus HL: run "
             "Calibrite PROFILER once so third-party software can use it."},
    {"id": "i1d3-slow", "name": "ColorMunki Display, i1Display Studio, Calibrite ColorChecker Display",
     "speed": "slow", "verified": False, "kind": "colorimeter",
     "note": "Same sensor family as the i1Display Pro, but the firmware makes every reading slower. Use the slow "
             "auto-run video."},
    {"id": "spyder", "name": "Datacolor Spyder X / X2 / Spyder (2024) / Spyder 5 / Spyder 4",
     "speed": "slow", "verified": False, "kind": "colorimeter",
     "note": "Let it warm up 10–15 min before the first reading (ArgyllCMS then asks for a black calibration: "
             "cover the lens). Spyder 4/5 need the calibration data installed once: `oeminst` (ArgyllCMS) with "
             "the Datacolor installer. Noisier than an i1Display in deep shadows."},
    {"id": "i1pro", "name": "i1Pro / i1Pro 2 / i1Pro 3, ColorMunki Photo / Design, i1Studio (spectrometers)",
     "speed": "slow", "verified": False, "kind": "spectro",
     "note": "Spectrometers are accurate on color but noisy below ~1 nit: black, 0–10 % gray and the darkest "
             "colors may be flagged. ArgyllCMS asks for a white-tile calibration at start. Best use: create a "
             ".ccss for your projector and use it with a colorimeter."},
    {"id": "k10", "name": "Klein K10-A", "speed": "std", "verified": False, "kind": "colorimeter",
     "note": "Very fast and good in low light. The standard schedule is fine."},
    {"id": "other-slow", "name": "Other ArgyllCMS meter (DTP94, Huey, ColorHug, Spyder 2/3, JETI …)",
     "speed": "slow", "verified": False, "kind": "colorimeter",
     "note": "Use the slow auto-run video. Patches the meter could not read in time are flagged, never silently "
             "wrong."},
]
METER_BY_ID = {m["id"]: m for m in METERS}

# instrument name in `spotread -?` → suggested meter profile (first match wins)
_DETECT = [
    (r"klein|k-?10", "k10"),
    (r"i1 ?pro|eye-one pro|colormunki(?! ?display| ?smile)|i1 ?studio", "i1pro"),
    (r"spyder ?[45x]|spyderx|spyder ?20\d\d|datacolor spyder\b", "spyder"),
    (r"colormunki ?(display|smile)|i1 ?display ?studio", "i1d3"),     # the i1d3 USB name lists both — see note
    (r"i1 ?display|displaypro|colorchecker display|i1d3|calibrite", "i1d3"),
    (r"spyder|dtp ?94|huey|colorhug|jeti|specbos|spectraval|hcfr|dtp ?92|chroma ?5", "other-slow"),
]


def detect_meter(instruments):
    """Instrument names from `spotread -?` → (profile id, ambiguous). The i1Display family reports one USB name for
    the Pro/Plus and the ColorMunki Display, so the user picks the exact one."""
    for name in instruments or []:
        low = name.lower()
        for rx, mid in _DETECT:
            if re.search(rx, low):
                return mid, mid == "i1d3"
    return None, False


def projector(pid):
    return PROJECTOR_BY_ID.get(pid) or PROJECTOR_BY_ID["vidda-other"]


def meter(mid):
    return METER_BY_ID.get(mid) or METER_BY_ID["i1d3"]


def gp_count(profile):
    """Gamma point count of a session's projectorProfile (20 by default — the verified layout)."""
    try:
        n = int((profile or {}).get("gpCount", 20))
    except (TypeError, ValueError):
        n = 20
    return n if n in GP_LAYOUTS else 20


def parse_display_types(raw):
    """`spotread -?` with an instrument connected lists its display types (-y), e.g.
         -y X                 Display type - instrument specific list to choose from:
            l                    LCD (Generic)
            p                    Projector (Generic)
            1                    Factory Default [Default] (Generic)
            u                    WLED Family Display (DisplayCAL)        <- an installed .ccss
    Returns [(selector, description)]; empty when nothing could be parsed."""
    out, on = [], False
    for line in (raw or "").splitlines():
        if re.match(r"\s*-y\b", line):
            on = True
            continue
        if not on:
            continue
        if re.match(r"\s*-\w", line):
            break                                  # the next option
        m = re.match(r"\s{2,}([A-Za-z0-9]{1,2})\s{2,}(\S.*)$", line)
        if m:
            if m.group(1) not in [k for k, _ in out]:
                out.append((m.group(1), m.group(2).strip()))
        elif out and not line.strip():
            break
    return out


def public():
    """For the UI."""
    return {"projectors": PROJECTORS, "meters": METERS,
            "layouts": [{"count": n, "step": s} for n, s in GP_LAYOUTS.items()]}
