#!/usr/bin/env python3
"""Makes long (10 min by default) versions of the patches without re-encoding: the HEVC stream is looped
(ffmpeg -stream_loop -c copy). Pixels and HDR10 metadata (in the stream) do not change.

Why: a 20-second file ends in Plex and the player goes back to the menu; a 10-minute one can simply be
played and measured, with no pause and no player bar over the picture.

Usage: python3 tools/make_long_patches.py <src_patches_dir> <dst_dir> [minutes]
"""
import json
import os
import shutil
import subprocess
import sys


def main():
    src, dst = sys.argv[1], sys.argv[2]
    minutes = float(sys.argv[3]) if len(sys.argv) > 3 else 10
    with open(os.path.join(src, "patches.json"), encoding="utf-8") as f:
        patches = json.load(f)
    for p in patches:
        inp = os.path.join(src, p["file"])
        out = os.path.join(dst, p["file"])
        os.makedirs(os.path.dirname(out), exist_ok=True)
        dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                             "-of", "csv=p=0", inp]).decode().strip())
        loops = max(0, int(round(minutes * 60 / dur)) - 1)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-stream_loop", str(loops), "-i", inp, "-map", "0",
                        "-c", "copy", "-movflags", "+faststart", out], check=True)
        print("ok", p["file"], flush=True)
    shutil.copy2(os.path.join(src, "patches.json"), os.path.join(dst, "patches.json"))
    print("done", len(patches))


if __name__ == "__main__":
    main()
