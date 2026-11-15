#!/usr/bin/env python3
"""Builds the auto-run videos (SDR and HDR10) from the ready patches without re-encoding and checks every segment.

Usage: python3 tools/make_sequence.py [assets/patches]
Result: <patches>/AUTO/ProjectorCal_AUTO_SDR_v<N>.mp4, ProjectorCal_AUTO_HDR10_v<N>.mp4 and the slow-meter
variants *_v<N>_slow.mp4 (+ sequence_<mode>[_slow].json).
Each patch starts with a keyframe, GOP = 1 s, so splicing on whole seconds is frame-accurate.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from projectorcal import sequence as Q  # noqa: E402
from projectorcal.catalog import Catalog  # noqa: E402


def first_pixel(path, t):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", path, "-frames:v", "1",
                                   "-f", "rawvideo", "-pix_fmt", "yuv420p10le", "-"])
    w, h = 3840, 2160
    y = struct.unpack("<H", raw[(h // 2 * w + w // 2) * 2:(h // 2 * w + w // 2) * 2 + 2])[0]  # frame center
    u_off = w * h * 2
    cu = struct.unpack("<H", raw[u_off + ((h // 4) * (w // 2) + w // 4) * 2:][:2])[0]
    return y, cu


def seq_json(mode, speed):
    return "sequence_%s%s.json" % (mode, "" if speed == "std" else "_" + speed)


def main():
    pdir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "assets", "patches"))
    cat = Catalog(pdir)
    out_dir = os.path.join(pdir, "AUTO")
    os.makedirs(out_dir, exist_ok=True)
    for mode, speed in [(m, sp) for sp in Q.SPEEDS for m in ("sdr", "hdr")]:
        seq = Q.build(cat, mode, speed)
        out = os.path.join(out_dir, seq["file"])
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            for sg in seq["segments"]:
                f.write("file '%s'\noutpoint %d\n" % (os.path.join(pdir, cat.get(sg["patchId"])["file"]), sg["dur"]))
            lst = f.name
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-map", "0",
                        "-c", "copy", "-movflags", "+faststart", out], check=True)
        os.unlink(lst)
        dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                             "-of", "csv=p=0", out]).decode())
        assert abs(dur - seq["duration"]) < 0.2, (dur, seq["duration"])
        cache = {}
        bad = 0
        for sg in seq["segments"]:
            pid = sg["patchId"]
            if pid not in cache:
                cache[pid] = first_pixel(os.path.join(pdir, cat.get(pid)["file"]), 0.5)
            got = first_pixel(out, sg["start"] + sg["dur"] / 2)
            if got != cache[pid]:
                bad += 1
                print("MISMATCH", mode, sg, got, cache[pid])
        with open(os.path.join(out_dir, seq_json(mode, speed)), "w", encoding="utf-8") as f:
            json.dump(seq, f, ensure_ascii=False, indent=1)
        print("%s/%s: %s, %.1f s, segments %d, errors %d, %.1f MB" % (mode, speed, out, dur, len(seq["segments"]), bad,
                                                                   os.path.getsize(out) / 1e6))
        if bad:
            sys.exit(1)


if __name__ == "__main__":
    main()
