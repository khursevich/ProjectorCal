"""Auto sequence: one video file (Plex or USB) with all patches on a fixed schedule.

Single source of truth for both the video generator (tools/make_sequence.py) and the analyzer (autorun.py).

File layout (time in seconds from the start, all durations are whole seconds: patch GOP = 1 s):
* start: gray PREROLL s, white MARK_WHITE s (unambiguous), gray MARK_BLACK s. The anchor is the
  white→gray fall: readings on white are short (≈1 s), so the moment of the fall is known precisely. Play may
  be pressed any time after "Start" in the program — the program waits for the mark.
* patches: gray interleaved, colors from fast to slow; every transition is visible by luminance/color and
  refines the sync (if the player stalls). Segment durations come from the meter reading-time model
  (read_time): the tail of a reading started on the previous picture + 2–3 whole readings.
* control whites 100 % — laser brightness drift during the run.
* finish: the same mark — confirms that the video played without pauses.
Patch duration depends on the expected luminance: the meter needs longer on dark patches.
"""
from __future__ import annotations

PREROLL = 8
MARK_WHITE = 12         # mark: 12 s of white between grays; other whites are ≤ 7 s (patch) or 16 s (drift check)
DRIFT_WHITE = 16
MARK_BLACK = 4          # gray after the mark white (anchor — the white→gray fall)
END_BLACK = 5
END_WHITE = 6
END_TAIL = 5
REF_WHITE_NITS = 35.0      # durations assume a slightly darker white than real (darker → longer readings)
REF_BLACK_NITS = 0.03
VERSION = 3

# Reading time of the colorimeter (Display Plus HL / i1D3 in ArgyllCMS, adaptive integration), fitted to the
# 2026-11-03 run (Vidda C3S, white 37 nits): 37 nits — 1.1 s; 7 nits — 1.7 s; 2.6 nits — 2.3 s; 0.7 nits — 4 s;
# 0.12 nits — 6–8 s; black 0.03 nits — up to 10 s. Colors without a blue component (red, yellow, green) take
# long: integration waits for the sensor's blue channel (red 100 % — ≈5 s at 8 nits).
READ_MAX = 10.0


def slow_luminance(rgb_lin, white):
    """"Effective" luminance for reading time: luminance, but no more than what the sensor's blue channel sees."""
    r, g, b = rgb_lin
    Y = white * (0.2126 * r + 0.7152 * g + 0.0722 * b)
    blue = white * (b + 0.03 * g + 0.01 * r) / 1.04
    return max(min(Y, blue), 1e-4)


def read_time(y_eff, y_new=None):
    """Two models, the longer time wins. Old (2026-11-03 run): bright 1.1 s, y_eff — luminance limited by the blue
    channel. New (2026-11-04): integration jumps to ~6.5 s already below ~1.5 nits; y_new — luminance limited by
    the "red" channel (blue 100 % — 4.8 s). Without y_new — luminance only."""
    y = max(y_eff, 1e-4)
    t_old = min(READ_MAX, 0.7 + 2.6 / y ** 0.5)
    yn = max(y_new if y_new is not None else y, 1e-4)
    t_new = min(7.5, 0.4 + 7.0 / yn ** 0.9)
    return max(t_old, t_new)


def red_luminance(rgb_lin, white):
    """What the sensor's "red" channel sees: blue without red and green needs a long reading (≈5 s)."""
    r, g, b = rgb_lin
    return max(white * (r + 0.3 * g + 0.02 * b), 1e-4)


def read_time_xyz(XYZ):
    """Reading time for what is on screen (XYZ in nits) — for the meter simulator and tests."""
    from . import colorimetry as C
    r, g, b = (max(c, 0.0) for c in C.matvec(C.M709_INV, XYZ))
    return read_time(max(min(XYZ[1], (b + 0.03 * g + 0.01 * r) / 1.04), 1e-4),
                     min(XYZ[1], r + 0.3 * g + 0.02 * b))


def _lin(p):
    if p["mode"] != "sdr":
        return None
    return [max(c, 0.0) ** 2.4 for c in p["rgb"]]


def patch_read_time(catalog, pid, mode):
    p = catalog.get(pid)
    if mode == "sdr":
        y = slow_luminance(_lin(p), REF_WHITE_NITS) + REF_BLACK_NITS
        lin = _lin(p)
        yl = REF_WHITE_NITS * (0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]) + REF_BLACK_NITS
        return read_time(y, min(yl, red_luminance(lin, REF_WHITE_NITS) + REF_BLACK_NITS))
    else:
        # HDR: Vidda tone mapping (2026-11-02) — shadows and mids ≈ 13 % of the PQ target, the top hits a ~41-nit peak
        y = min(0.13 * p.get("tnits", 0.0), REF_WHITE_NITS) + REF_BLACK_NITS
    return read_time(y)


DESIGN_SLOW = 1.25      # margin: the meter may be 25 % slower than the model (another unit, darker white)

# Speed variants of the auto-run video. "std" is tuned to the i1Display Pro / Plus family (verified); "slow" gives
# every reading twice the time — for meters whose readings are slower (ColorMunki Display, i1Display Studio,
# Spyder, i1Pro and other spectrometers, DTP94, Huey, ColorHug) or for an unknown meter.
SPEEDS = {"std": 1.0, "slow": 2.0}


def _dur(t_prev, t_cur, min_dur=5, factor=1.0):
    """A segment must fit the tail of a reading started on the previous picture (up to t_prev) plus n whole
    readings of its own: 3 on bright, 2 on dark (each dark reading is long and precise). +1 s margin."""
    n = 3 if t_cur < 4.0 else 2
    need = DESIGN_SLOW * factor * (t_prev + n * t_cur) + 1.0
    return int(max(min_dur, min(45 * factor, -(-need // 1))))


def file_name(mode, speed="std"):
    return "ProjectorCal_AUTO_%s_v%d%s.mp4" % ("SDR" if mode == "sdr" else "HDR10", VERSION,
                                              "" if speed == "std" else "_" + speed)


def _gray_order():
    """100, 50, 95, 45, …, 55, 5, 0 — upper and lower halves interleaved: neighbors differ in luminance by 5–200
    times, so a "shift by one patch" is immediately implausible (with consecutive steps it is invisible), and the
    tail of a long dark reading eats no more of the next bright patch than its duration allows for."""
    hi, lo = list(range(100, 50, -5)), list(range(50, -1, -5))
    out = []
    for a, b in zip(hi, lo):
        out += [a, b]
    return out + lo[len(hi):]


def _color_order(catalog):
    """Primaries/secondaries: from fast to slow (cyan, magenta, blue, green, yellow, red)."""
    out = []
    for h in ("cyan", "magenta", "blue", "green", "yellow", "red"):
        out += [p for p in catalog.sdr_colors() if catalog.get(p)["hue"] == h]
    return out


def _sweep_order(catalog):
    """Saturations 25→100 %, within each — hues from fast to slow."""
    order = []
    hues = ["cyan", "magenta", "blue", "green", "yellow", "red"]
    for sat in (0.25, 0.5, 0.75, 1.0):
        for h in hues:
            for pid in catalog.sdr_colors(sweeps=True):
                p = catalog.get(pid)
                if p["hue"] == h and p["sat"] == sat and p["amp"] == 0.75:
                    order.append(pid)
    return order


def build(catalog, mode="sdr", speed="std"):
    """Schedule: {"mode", "version", "speed", "segments": [{i, patchId, start, dur, role}], "anchor", "endAnchor",
    "duration"}. role: pre | mark | patch | drift | end. speed: "std" | "slow" (see SPEEDS)."""
    if speed not in SPEEDS:
        speed = "std"
    f = SPEEDS[speed]
    segs = []
    t = 0
    prev_t = [1.0]

    def add(pid, role, dur=None, min_dur=5):
        nonlocal t
        tc = patch_read_time(catalog, pid, mode)
        if dur is None:
            dur = _dur(prev_t[0], tc, min_dur, f)
        else:
            dur = int(round(dur * f))           # service segments (mark, drift whites, finish) scale too
        segs.append({"i": len(segs), "patchId": pid, "start": t, "dur": dur, "role": role, "tr": round(tc, 2)})
        prev_t[0] = tc
        t += dur

    if mode == "sdr":
        black, white, pre = "SDR_21", "SDR_01", "SDR_11"
    else:
        black, white, pre = "HDR_21", "HDR_06", "HDR_13"
    # mark: gray → white → gray. Readings on gray and white are short (1–2 s), so both the rise and the fall are
    # seen precisely; black does not work here — a single black reading is longer than the whole mark.
    add(pre, "pre", PREROLL)
    add(white, "mark", MARK_WHITE)
    anchor = t
    add(pre, "mark", MARK_BLACK)

    if mode == "sdr":
        for s in _gray_order():
            add(catalog.sdr_gray_by_stim(s), "patch")
        add(white, "drift", DRIFT_WHITE)
        for pid in _color_order(catalog):
            add(pid, "patch")
        add(white, "drift", DRIFT_WHITE)
        for pid in _sweep_order(catalog):
            add(pid, "patch")
        add(white, "drift", DRIFT_WHITE)
    else:
        for s in _gray_order():
            add(catalog.hdr_gray([s])[0], "patch")
        add(white, "drift", DRIFT_WHITE)

    # finish — the same mark (gray → white → gray): black does not work here, a reading on it is longer than the segment
    add(pre, "end", END_BLACK)
    add(white, "end", END_WHITE)
    end_anchor = t
    add(pre, "end", END_TAIL)
    out = {"mode": mode, "version": VERSION, "segments": segs, "anchor": anchor, "endAnchor": end_anchor,
           "duration": t, "file": file_name(mode, speed)}
    if speed != "std":
        out["speed"] = speed            # absent for "std": old sessions and the v3 videos stay byte-identical
    return out


def patches_measured(seq):
    """Unique patches measured by the run (without service segments)."""
    out = []
    for s in seq["segments"]:
        if s["role"] == "patch" and s["patchId"] not in out:
            out.append(s["patchId"])
    return out


def expected_xy(catalog, seq):
    """Chromaticity targets of color patches (Rec.709) — "is it the right patch" check by color."""
    out = {}
    for sg in seq["segments"]:
        p = catalog.get(sg["patchId"])
        if p.get("kind") == "color" and p.get("tx") is not None:
            out[p["id"]] = (p["tx"], p["ty"])
    return out


def expected_rel(catalog, seq):
    """Expected patch luminance relative to the mark white (γ 2.4 / Rec.709 targets; HDR — roughly by PQ)."""
    out = {}
    white = seq["segments"][1]["patchId"]
    for sg in seq["segments"]:
        p = catalog.get(sg["patchId"])
        if p["mode"] == "sdr":
            out[p["id"]] = (p["stim"] ** 2.4) if p["kind"] == "gray" else p.get("tY")
        else:
            # HDR: the projector tone mapping is unknown; a rough model from Vidda measurements (2026-11-02):
            # luminance ≈ 3 × (PQ target / PQ target of the mark white), capped at white. The check window is wide.
            tw = catalog.get(white)["tnits"]
            out[p["id"]] = min(1.0, 3 * p["tnits"] / tw) if p.get("tnits") else None
    out[white] = 1.0
    return out
