#!/usr/bin/env python3
"""Builds dist/ProjectorCal.app (macOS) without compiling: a bash launcher + the Python package + patches.

Usage: python3 tools/build_app.py [--no-patches]
Running on a Mac needs nothing but python3 (part of the Xcode Command Line Tools that Homebrew installs)
and ArgyllCMS (brew install argyll-cms).
"""
import argparse
import os
import shutil
import stat
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from projectorcal import __version__  # noqa: E402

LAUNCHER = r"""#!/bin/bash
# ProjectorCal: start the local UI server and open the browser.
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
LOG="$HOME/Library/Logs/ProjectorCal.log"
mkdir -p "$HOME/Library/Logs"
PY=""
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$p" ] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    PY="$p"; break
  fi
done
if [ -z "$PY" ]; then
  /usr/bin/osascript -e 'display alert "ProjectorCal" message "Python 3 not found. Open Terminal and run: xcode-select --install" as critical'
  exit 1
fi
echo "=== $(date) start $PY" >>"$LOG"
cd "$RES" || exit 1
exec "$PY" -s "$RES/run.py" "$@" >>"$LOG" 2>&1
"""

RUN_PY = '''import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from projectorcal.server import main  # noqa: E402

sys.exit(main())
'''

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>ProjectorCal</string>
  <key>CFBundleDisplayName</key><string>ProjectorCal</string>
  <key>CFBundleIdentifier</key><string>local.projectorcal</string>
  <key>CFBundleVersion</key><string>{v}</string>
  <key>CFBundleShortVersionString</key><string>{v}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>ProjectorCal</string>
  <key>CFBundleIconFile</key><string>ProjectorCal</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHumanReadableCopyright</key><string>ProjectorCal — open-source Vidda projector calibration (MIT)</string>
</dict>
</plist>
"""


# ------------------------------------------------------------------ icon (PNG → ICNS without external libraries)
def _png(w, h, pixels):
    raw = b"".join(b"\x00" + bytes(pixels[y * w * 4:(y + 1) * w * 4]) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _icon(n):
    px = bytearray(n * n * 4)
    r = n * 0.18
    pad = n * 0.06
    bars = [((0.22, 0.42, 0.47, 0.74), (199, 90, 90)), ((0.40, 0.30, 0.60, 0.74), (89, 168, 107)),
            ((0.58, 0.48, 0.78, 0.74), (90, 127, 208))]
    for y in range(n):
        for x in range(n):
            # rounded square with a soft edge
            cx = min(max(x + 0.5, pad + r), n - pad - r)
            cy = min(max(y + 0.5, pad + r), n - pad - r)
            d = ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) ** 0.5
            a = max(0.0, min(1.0, r - d + 0.5))
            if a <= 0:
                continue
            col = (24, 26, 30)
            fx, fy = x / n, y / n
            for (x0, y0, x1, y1), c in bars:
                if x0 <= fx < x1 and y0 <= fy < y1:
                    col = c
            if 0.16 <= fx <= 0.84 and 0.80 <= fy <= 0.83:
                col = (160, 165, 172)
            i = (y * n + x) * 4
            px[i:i + 4] = bytes((col[0], col[1], col[2], int(255 * a)))
    return _png(n, n, px)


def make_icns(path):
    data = b""
    for typ, size in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512)):
        png = _icon(size)
        data += typ + struct.pack(">I", len(png) + 8) + png
    with open(path, "wb") as f:
        f.write(b"icns" + struct.pack(">I", len(data) + 8) + data)


# ------------------------------------------------------------------ build
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-patches", action="store_true", help="do not put the video patches into the app (patches.json only)")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    args = ap.parse_args()
    app = os.path.join(args.out, "ProjectorCal.app")
    if os.path.exists(app):
        shutil.rmtree(app)
    macos = os.path.join(app, "Contents", "MacOS")
    res = os.path.join(app, "Contents", "Resources")
    os.makedirs(macos)
    os.makedirs(res)
    with open(os.path.join(app, "Contents", "Info.plist"), "w", encoding="utf-8") as f:
        f.write(PLIST.format(v=__version__))
    exe = os.path.join(macos, "ProjectorCal")
    with open(exe, "w", encoding="utf-8") as f:
        f.write(LAUNCHER)
    os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    with open(os.path.join(res, "run.py"), "w", encoding="utf-8") as f:
        f.write(RUN_PY)
    shutil.copytree(os.path.join(ROOT, "projectorcal"), os.path.join(res, "projectorcal"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    pdst = os.path.join(res, "assets", "patches")
    if args.no_patches:
        os.makedirs(pdst)
        shutil.copy2(os.path.join(ROOT, "assets", "patches", "patches.json"), pdst)
    else:
        shutil.copytree(os.path.join(ROOT, "assets", "patches"), pdst)
    make_icns(os.path.join(res, "ProjectorCal.icns"))
    print("Built:", app)


if __name__ == "__main__":
    main()
