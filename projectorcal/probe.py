"""Meter diagnostics (first-time hardware check): python3 -m projectorcal.probe

1. Finds spotread, shows the ArgyllCMS version and the list of instruments (`spotread -?`).
2. In each integration mode (auto, -Y n, -Y r) takes N readings of white and computes the spread.
3. Saves the report and the raw spotread output to a file on the Desktop (reference for the parser).

Before running: play SDR_01 (white 100%) on the player, the meter is in place, lights off.
ProjectorCal must be closed (only one program can use the meter).
"""
from __future__ import annotations

import argparse
import datetime
import math
import os
import sys

from . import colorimetry as C
from .meter import ArgyllMeter, MeterError, find_spotread, install_hint, probe_spotread


def main(argv=None):
    ap = argparse.ArgumentParser(prog="projectorcal.probe", description=__doc__.split("\n")[0])
    ap.add_argument("-n", type=int, default=10, help="readings per mode (default 10)")
    ap.add_argument("--spotread", help="path to spotread")
    ap.add_argument("--modes", default="auto,n,r", help="-Y modes, comma-separated: auto,n,r")
    ap.add_argument("--out", help="report file")
    args = ap.parse_args(argv)

    out = args.out or os.path.expanduser("~/Desktop/ProjectorCal_probe_%s.txt"
                                         % datetime.datetime.now().strftime("%Y%m%d_%H%M"))
    lines = []

    def say(s=""):
        print(s, flush=True)
        lines.append(s)

    raw = []
    exe = find_spotread(args.spotread)
    if not exe:
        say("spotread not found. " + install_hint())
        return 1
    info = probe_spotread(exe)
    say("spotread: %s  (ArgyllCMS %s)" % (exe, info.get("version") or "?"))
    say("Instruments: " + (", ".join(info["instruments"]) or "NONE FOUND"))
    raw.append("==== spotread -? ====\n" + info.get("raw", ""))
    if not info["instruments"]:
        say("Connect the meter directly to USB-C; if that does not help, run Calibrite PROFILER once.")

    results = []
    for mode in [m.strip() for m in args.modes.split(",") if m.strip()]:
        flags = ["-e"] + ([] if mode == "auto" else ["-Y", mode])
        say("")
        say("== Mode %s (%s), %d readings ==" % (mode, " ".join(flags), args.n))
        buf = []
        m = ArgyllMeter(exe, flags, log=buf.append)
        try:
            st = m.start()
            while st == "calibration":
                input("The meter asks for the calibration position: cover the optics and press Enter… ")
                st = m.continue_calibration()
                if st == "ready":
                    input("Put the meter back in the measuring position and press Enter… ")
            vals = []
            for i in range(args.n):
                try:
                    v = m.read()
                except MeterError as e:
                    say("  reading %d: error: %s" % (i + 1, e.message))
                    if not m.alive:
                        break
                    continue
                vals.append(v)
                x, y = C.xy(v)
                say("  %2d: Y=%9.4f  x=%.4f y=%.4f" % (i + 1, v[1], x, y))
            if len(vals) >= 2:
                Ys = [v[1] for v in vals]
                mean = sum(Ys) / len(Ys)
                sd = math.sqrt(sum((y - mean) ** 2 for y in Ys) / len(Ys)) / mean * 100 if mean > 0 else 0
                mx = sum(v[0] for v in vals) / len(vals)
                mz = sum(v[2] for v in vals) / len(vals)
                bal = C.rgb_balance((mx, mean, mz))
                say("  mean Y=%.3f nits, spread ±%.2f%%, range %.2f%%, CCT %.0f K, RGB %.1f/%.1f/%.1f" % (
                    mean, sd, (max(Ys) - min(Ys)) / mean * 100, C.cct((mx, mean, mz)), *bal))
                results.append((sd, mode))
        except MeterError as e:
            say("  ERROR: %s" % e.message)
            if e.detail:
                say("  " + e.detail.strip().replace("\n", "\n  "))
        finally:
            m.close()
            raw.append("==== mode %s ====\n%s" % (mode, "".join(buf)))

    say("")
    if results:
        sd, mode = min(results)
        say("Most stable mode: %s (±%.2f%%). %s" % (
            mode, sd, "Select it in Settings → Integration mode." if mode != "auto" else "No settings change needed."))
        if sd > 0.7:
            say("Spread above 0.7%: make sure the meter does not move, the lights are off and no airflow hits the tripod.")
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n" + "\n".join(raw))
        print("\nReport with raw spotread output: %s" % out)
    except OSError as e:
        print("Could not save the report: %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
