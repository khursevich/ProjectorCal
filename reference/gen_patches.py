#!/usr/bin/env python3
"""Video patch generator for projector calibration (SDR Rec.709 / HDR10 PQ BT.2020).
Each file: 3840x2160, 24p, HEVC Main10, limited range, flat color.
Writes patches.json (targets for pcal.py)."""
import json, os, subprocess, sys
import numpy as np

W, H, FPS = 3840, 2160, 24
DUR = int(os.environ.get("PATCH_DUR", "20"))
OUT = sys.argv[1] if len(sys.argv) > 1 else "out"
os.makedirs(OUT, exist_ok=True)
TMP = os.path.join(OUT, "_tmp"); os.makedirs(TMP, exist_ok=True)

# ---------- colorimetry ----------
M709 = np.array([[0.4124564, 0.3575761, 0.1804375],
                 [0.2126729, 0.7151522, 0.0721750],
                 [0.0193339, 0.1191920, 0.9503041]])

def pq_eotf(e):  # e 0..1 -> nits
    m1, m2 = 2610/16384, 2523/4096*128
    c1, c2, c3 = 3424/4096, 2413/4096*32, 2392/4096*32
    ep = np.power(e, 1/m2)
    return 10000*np.power(np.maximum(ep-c1, 0)/(c2-c3*ep), 1/m1)

def xy_of(rgb_lin):
    X, Y, Z = M709 @ np.array(rgb_lin)
    s = X+Y+Z
    return (X/s, Y/s) if s > 0 else (0.3127, 0.3290)

# ---------- frame writer ----------
def ycc10(r, g, b, kr=0.2126, kb=0.0722):
    kg = 1-kr-kb
    y = kr*r+kg*g+kb*b
    cb = (b-y)/(2*(1-kb)); cr = (r-y)/(2*(1-kr))
    cl = lambda v: int(round(min(max(v, 4), 1019)))
    return cl(64+876*y), cl(512+896*cb), cl(512+896*cr)

def raw_flat(path, Y, U, V):
    y = np.full((H, W), Y, dtype="<u2")
    u = np.full((H//2, W//2), U, dtype="<u2")
    v = np.full((H//2, W//2), V, dtype="<u2")
    with open(path, "wb") as f:
        f.write(y.tobytes()); f.write(u.tobytes()); f.write(v.tobytes())

def encode(rawpath, outpath, hdr):
    if hdr:
        x265 = ("lossless=1:log-level=error:repeat-headers=1:hdr10=1:hdr10-opt=0:"
                "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:range=limited:"
                "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):"
                "max-cll=1000,400:keyint=24:bframes=0:no-open-gop=1")
    else:
        x265 = ("lossless=1:log-level=error:repeat-headers=1:"
                "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited:keyint=24:bframes=0")
    cmd = ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-f", "rawvideo", "-pix_fmt", "yuv420p10le",
           "-s", f"{W}x{H}", "-framerate", str(FPS), "-i", rawpath, "-t", str(DUR),
           "-c:v", "libx265", "-preset", "ultrafast", "-tag:v", "hvc1", "-x265-params", x265,
           "-pix_fmt", "yuv420p10le", "-an", outpath]
    subprocess.run(cmd, check=True)

patches = []
def add(mode, order, name, fname, kind, Y, U, V, extra):
    raw = os.path.join(TMP, "f.yuv")
    if isinstance(Y, np.ndarray):
        with open(raw, "wb") as f:
            f.write(Y.tobytes())
            f.write(np.full((H//2, W//2), U, dtype="<u2").tobytes())
            f.write(np.full((H//2, W//2), V, dtype="<u2").tobytes())
    else:
        raw_flat(raw, Y, U, V)
    d = os.path.join(OUT, mode); os.makedirs(d, exist_ok=True)
    encode(raw, os.path.join(d, fname), mode == "HDR10")
    rec = dict(mode=mode.lower(), order=order, name=name, file=f"{mode}/{fname}", kind=kind)
    rec.update(extra); patches.append(rec)
    print("ok", mode, fname, flush=True)

# ---------- SDR gray 100..0 step 5 ----------
n = 0
for s in range(100, -1, -5):
    st = s/100
    Y = int(round(64+876*st))
    n += 1
    add("SDR", n, f"Gray {s}%", f"SDR_{n:02d}_gray_{s:03d}.mp4", "gray", Y, 512, 512,
        dict(stim=st, rgb=[st, st, st]))

# ---------- SDR colors ----------
GAM = 2.4
base = {"red": (1, 0, 0), "yellow": (1, 1, 0), "green": (0, 1, 0),
        "cyan": (0, 1, 1), "blue": (0, 0, 1), "magenta": (1, 0, 1)}
names = {"red": "Red", "yellow": "Yellow", "green": "Green",
         "cyan": "Cyan", "blue": "Blue", "magenta": "Magenta"}
def color_patch(hue, sat, amp):
    P = np.array(base[hue], float)
    enc = amp*((1-sat)*np.ones(3) + sat*P)  # encoded R'G'B'
    return enc

# 100% amplitude primaries/secondaries
for hue in base:
    n += 1
    enc = color_patch(hue, 1.0, 1.0)
    y, cb, cr = ycc10(*enc)
    lin = np.power(enc, GAM)
    x, yy = xy_of(lin)
    add("SDR", n, f"{names[hue]} 100%/100", f"SDR_{n:02d}_{hue}_sat100_amp100.mp4", "color", y, cb, cr,
        dict(hue=hue, sat=1.0, amp=1.0, rgb=enc.tolist(), tx=x, ty=yy, tY=float(M709[1] @ lin)))
# saturation 25/50/75/100 at 75% amplitude
for hue in base:
    for sat in (0.25, 0.5, 0.75, 1.0):
        n += 1
        enc = color_patch(hue, sat, 0.75)
        y, cb, cr = ycc10(*enc)
        lin = np.power(enc, GAM)
        x, yy = xy_of(lin)
        add("SDR", n, f"{names[hue]} sat {int(sat*100)}% (amp 75%)",
            f"SDR_{n:02d}_{hue}_sat{int(sat*100):03d}_amp075.mp4", "color", y, cb, cr,
            dict(hue=hue, sat=sat, amp=0.75, rgb=enc.tolist(), tx=x, ty=yy, tY=float(M709[1] @ lin)))

# ---------- SDR PLUGE / clip ----------
def bars(bg8, bar_codes8, bar_w=240, gap=0, vfrac=(0.15, 0.85)):
    Y = np.full((H, W), bg8*4, dtype="<u2")
    tot = len(bar_codes8)*bar_w
    x0 = (W-tot)//2
    for i, c in enumerate(bar_codes8):
        Y[int(H*vfrac[0]):int(H*vfrac[1]), x0+i*bar_w:x0+(i+1)*bar_w] = c*4
    return Y
n += 1
add("SDR", n, "PLUGE (black)", f"SDR_{n:02d}_pluge_black.mp4", "test", bars(16, [4, 8, 12, 16, 20, 24, 28]), 512, 512,
    dict(note="background=16; 8-bit bars: 4,8,12 (below black, invisible) | 16 (same as background) | 20,24,28 (above black)"))
n += 1
add("SDR", n, "White clipping", f"SDR_{n:02d}_clip_white.mp4", "test",
    bars(16, [200, 215, 225, 229, 231, 233, 235, 240, 250], 200), 512, 512,
    dict(note="background=16; 8-bit bars: 200,215,225,229,231,233,235,240,250"))

# ---------- HDR10 gray ----------
n = 0
for s in range(100, -1, -5):
    st = s/100
    Y = int(round(64+876*st))
    n += 1
    nits = float(pq_eotf(st))
    add("HDR10", n, f"HDR gray PQ {s}% (≈{nits:.0f} nits)", f"HDR_{n:02d}_gray_{s:03d}.mp4", "gray", Y, 512, 512,
        dict(stim=st, rgb=[st, st, st], tnits=nits))

with open(os.path.join(OUT, "patches.json"), "w", encoding="utf-8") as f:
    json.dump(patches, f, ensure_ascii=False, indent=1)
print("done", len(patches))
