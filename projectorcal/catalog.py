"""Patch catalog (assets/patches/patches.json) and patch sets for the wizard steps."""
from __future__ import annotations

import json
import os

HUES = ["red", "yellow", "green", "cyan", "blue", "magenta"]
HUE_NAME = {"red": "red", "yellow": "yellow", "green": "green", "cyan": "cyan",
          "blue": "blue", "magenta": "magenta"}
HUE_TO = {"red": "red", "yellow": "yellow", "green": "green", "cyan": "cyan",
              "blue": "blue", "magenta": "magenta"}
# In the Vidda menu magenta is usually called Pink/Magenta.
HUE_MENU = {"red": "Red", "yellow": "Yellow", "green": "Green", "cyan": "Cyan", "blue": "Blue",
            "magenta": "Magenta (Pink)"}


def patch_id(p):
    """'SDR/SDR_11_gray_050.mp4' → 'SDR_11'."""
    base = os.path.basename(p["file"])
    parts = base.split("_")
    return parts[0] + "_" + parts[1]


class Catalog:
    def __init__(self, patches_dir):
        self.dir = patches_dir
        with open(os.path.join(patches_dir, "patches.json"), encoding="utf-8") as f:
            raw = json.load(f)
        self.patches = []
        for p in raw:
            p = dict(p)
            p["id"] = patch_id(p)
            p["filename"] = os.path.basename(p["file"])
            self.patches.append(p)
        self.by_id = {p["id"]: p for p in self.patches}

    def get(self, pid):
        return self.by_id[pid]

    def sdr_gray(self, stims=None):
        sel = sorted((p for p in self.patches if p["mode"] == "sdr" and p["kind"] == "gray"),
                     key=lambda p: p["order"])
        if stims is not None:
            want = set(stims)
            sel = [p for p in sel if round(p["stim"] * 100) in want]
        return [p["id"] for p in sel]

    def sdr_gray_by_stim(self, s):
        return self.sdr_gray([s])[0]

    def sdr_colors(self, sweeps=False):
        sel = sorted((p for p in self.patches if p["mode"] == "sdr" and p["kind"] == "color"),
                     key=lambda p: p["order"])
        if not sweeps:
            sel = [p for p in sel if p["amp"] == 1.0]
        return [p["id"] for p in sel]

    def hdr_gray(self, stims=None):
        sel = sorted((p for p in self.patches if p["mode"] == "hdr10" and p["kind"] == "gray"),
                     key=lambda p: p["order"])
        if stims is not None:
            want = set(stims)
            sel = [p for p in sel if round(p["stim"] * 100) in want]
        return [p["id"] for p in sel]

    def public(self):
        return {p["id"]: p for p in self.patches}


# Patch sets for passes
QUICK_GRAY = [100, 70, 50, 30, 20, 10]
CIRCLE = [100, 80, 60, 40, 20, 10]   # gray circle: balance (100/20) and gamma (80…10) in one pass
HDR_PASS = [75, 65, 60, 55, 50, 45, 40, 30, 20]
RECHECK = [100, 50, 20, 10]


def pass_patches(catalog, step_id, opts):
    """Patch ids for a step pass. opts — step options (quick, sweeps)."""
    if step_id == "gray":
        return catalog.sdr_gray(None if opts.get("full") else CIRCLE)
    if step_id == "gpoints":
        return catalog.sdr_gray(None)
    if step_id in ("gamma", "verify"):
        return catalog.sdr_gray(QUICK_GRAY if opts.get("quick") else None)
    if step_id == "color":
        return catalog.sdr_colors(sweeps=bool(opts.get("sweeps")))
    if step_id == "hdr":
        return catalog.hdr_gray(HDR_PASS)
    if step_id == "black":
        return catalog.sdr_gray([100, 95])
    if step_id == "summary":
        return catalog.sdr_gray(RECHECK)
    return []
