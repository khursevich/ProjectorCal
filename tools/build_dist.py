#!/usr/bin/env python3
"""Builds the three downloads (no compilation, the program is plain Python 3.8+ with the standard library):

  dist/ProjectorCal-Windows.zip   ProjectorCal.cmd launcher, "Create desktop shortcut.cmd", icon, the program
  dist/ProjectorCal-Linux.zip     projectorcal.sh launcher, install-desktop-entry.sh, icon, the program
  dist/ProjectorCal-macOS.zip     ProjectorCal.app (tools/build_app.py)

The video patches are one separate download for all systems (ProjectorCal-patches-v<N>.zip).
Usage: python3 tools/build_dist.py [--with-patches]
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from projectorcal import __version__  # noqa: E402
from build_app import RUN_PY, _icon  # noqa: E402

WIN_CMD = r"""@echo off
rem ProjectorCal: starts the local calibration UI in your browser. Keep this window open while you calibrate.
setlocal
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
  echo.
  echo  Python 3.8 or newer is needed ^(free^). Opening the download page...
  echo  Install it, then start ProjectorCal again.
  echo.
  start "" "https://www.python.org/downloads/windows/"
  pause
  exit /b 1
)
title ProjectorCal - close this window to quit
echo.
echo  ProjectorCal %VERSION% is starting in your browser.
echo  Keep this window open while you calibrate; close it to quit.
echo.
%PY% -s run.py %*
if errorlevel 1 pause
"""

WIN_SHORTCUT = r"""@echo off
rem Creates a "ProjectorCal" shortcut with an icon on your desktop.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\ProjectorCal.lnk');" ^
  "$s.TargetPath='%~dp0ProjectorCal.cmd'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0ProjectorCal.ico';" ^
  "$s.WindowStyle=7; $s.Description='ProjectorCal - projector calibration'; $s.Save()"
echo Desktop shortcut created.
pause
"""

WIN_README = """ProjectorCal {v} for Windows
=============================

Needs: Windows 10 (version 1809 or newer) or Windows 11.

1. Python 3.8 or newer (free): https://www.python.org/downloads/windows/
   (or in a terminal: winget install Python.Python.3.12)
2. ArgyllCMS (free, talks to the meter): https://www.argyllcms.com/ -> Downloads -> "MS Windows 64 bit".
   Unzip it to C:\\ (you get e.g. C:\\Argyll_V3.3.0\\bin\\spotread.exe). ProjectorCal finds it there.
   - i1Display Pro / Plus, Calibrite ColorChecker Display, ColorMunki Display: no driver needed.
   - Spyder, i1Pro, ColorMunki Photo and older meters: install the ArgyllCMS USB driver
     (argyllcms.com -> "Installing on MS Windows").
3. The video patches: ProjectorCal-patches-v3.zip from the same release page. Unzip the "patches" folder into
   the "assets" folder here (assets\\patches\\SDR, HDR10, AUTO ...) - or copy it straight to your USB stick / Plex.
4. Double-click ProjectorCal.cmd. Your browser opens the program. Keep the black window open while you calibrate.
   "Create desktop shortcut.cmd" puts a ProjectorCal icon on your desktop.

Your sessions are stored in %APPDATA%\\ProjectorCal. Guide: https://github.com/khursevich/ProjectorCal
"""

LINUX_SH = """#!/bin/sh
# ProjectorCal: starts the local calibration UI in your browser.
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$DIR" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.8+ is needed: sudo apt install python3 (or your distribution's package)"; exit 1
fi
exec python3 -s run.py "$@"
"""

LINUX_DESKTOP = """#!/bin/sh
# Adds ProjectorCal to your application menu (no root needed).
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/projectorcal.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ProjectorCal
Comment=Projector calibration with a colorimeter
Exec="$DIR/projectorcal.sh"
Icon=$DIR/projectorcal.png
Terminal=false
Categories=Graphics;Utility;
EOF
chmod +x "$APPS/projectorcal.desktop"
echo "ProjectorCal added to the application menu ($APPS/projectorcal.desktop)."
"""

LINUX_README = """ProjectorCal {v} for Linux
===========================

1. Python 3.8+ (preinstalled on most distributions) and ArgyllCMS:
     Debian/Ubuntu: sudo apt install python3 argyll
     Fedora:        sudo dnf install python3 argyllcms
     Arch:          sudo pacman -S python argyllcms
   Re-plug the meter afterwards (the package installs the USB access rules).
2. The video patches: ProjectorCal-patches-v3.zip from the release page:
     unzip ProjectorCal-patches-v3.zip -d assets/
3. Start: ./projectorcal.sh   (./install-desktop-entry.sh adds it to the application menu)

Sessions: ~/.local/share/ProjectorCal. Guide: https://github.com/khursevich/ProjectorCal
"""


def ico(path):
    """ICO with PNG images inside (Windows Vista+)."""
    import struct
    imgs = [(s, _icon(s)) for s in (16, 32, 48, 256)]
    head = struct.pack("<HHH", 0, 1, len(imgs))
    off = 6 + 16 * len(imgs)
    dirs, data = b"", b""
    for s, png in imgs:
        dirs += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), off + len(data))
        data += png
    with open(path, "wb") as f:
        f.write(head + dirs + data)


def common(dst, with_patches):
    shutil.copytree(os.path.join(ROOT, "projectorcal"), os.path.join(dst, "projectorcal"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    pdst = os.path.join(dst, "assets", "patches")
    if with_patches:
        shutil.copytree(os.path.join(ROOT, "assets", "patches"), pdst)
    else:
        os.makedirs(pdst)
        shutil.copy2(os.path.join(ROOT, "assets", "patches", "patches.json"), pdst)
    with open(os.path.join(dst, "run.py"), "w", encoding="utf-8") as f:
        f.write(RUN_PY)
    shutil.copy2(os.path.join(ROOT, "LICENSE"), dst)


def write(path, text, crlf=False, exe=False):
    with open(path, "w", encoding="utf-8", newline="\r\n" if crlf else "\n") as f:
        f.write(text)
    if exe:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def zipdir(src, out):
    """Zip keeping the Unix permissions (the Linux launchers stay executable)."""
    base = os.path.dirname(src)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(src):
            for fn in sorted(files):
                p = os.path.join(root, fn)
                info = zipfile.ZipInfo.from_file(p, os.path.relpath(p, base))
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(p, "rb") as f:
                    z.writestr(info, f.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-patches", action="store_true", help="put the video patches into every package (~1 GB)")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    w = os.path.join(a.out, "ProjectorCal-Windows")
    shutil.rmtree(w, ignore_errors=True)
    common(w, a.with_patches)
    write(os.path.join(w, "ProjectorCal.cmd"), WIN_CMD.replace("%VERSION%", __version__), crlf=True)
    write(os.path.join(w, "Create desktop shortcut.cmd"), WIN_SHORTCUT, crlf=True)
    write(os.path.join(w, "README-Windows.txt"), WIN_README.format(v=__version__), crlf=True)
    ico(os.path.join(w, "ProjectorCal.ico"))

    lx = os.path.join(a.out, "ProjectorCal-Linux")
    shutil.rmtree(lx, ignore_errors=True)
    common(lx, a.with_patches)
    write(os.path.join(lx, "projectorcal.sh"), LINUX_SH, exe=True)
    write(os.path.join(lx, "install-desktop-entry.sh"), LINUX_DESKTOP, exe=True)
    write(os.path.join(lx, "README-Linux.txt"), LINUX_README.format(v=__version__))
    with open(os.path.join(lx, "projectorcal.png"), "wb") as f:
        f.write(_icon(256))

    args = [sys.executable, os.path.join(ROOT, "tools", "build_app.py"), "--out", a.out]
    if not a.with_patches:
        args.append("--no-patches")
    subprocess.run(args, check=True)

    for name in ("ProjectorCal-Windows", "ProjectorCal-Linux"):
        zipdir(os.path.join(a.out, name), os.path.join(a.out, name + ".zip"))
    zipdir(os.path.join(a.out, "ProjectorCal.app"), os.path.join(a.out, "ProjectorCal-macOS.zip"))
    for name in ("ProjectorCal-Windows.zip", "ProjectorCal-Linux.zip", "ProjectorCal-macOS.zip"):
        p = os.path.join(a.out, name)
        print("%-26s %6.2f MB" % (name, os.path.getsize(p) / 1e6))


if __name__ == "__main__":
    main()
