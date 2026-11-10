#!/usr/bin/env python3
"""
pcal.py — the original command-line prototype (math reference for ProjectorCal): colorimeter readings via ArgyllCMS spotread.

Modes:
  once        one reading (check that everything is connected)
  live        continuous readings + RGB balance hint (for White Balance adjustment)
  sdr-gray    SDR gray scale by patches (white, gamma, balance, contrast)
  sdr-color   SDR colors (primaries/secondaries, saturation)
  hdr-gray    HDR10 gray scale (PQ)

Examples:
  python3 pcal.py once
  python3 pcal.py live
  python3 pcal.py sdr-gray --gamma 2.4
  python3 pcal.py sdr-gray --only 100,50,20      # quick check of 3 points
  python3 pcal.py sdr-color --white 92.5         # white (nits) from sdr-gray, if not measured in this session
"""
import argparse, csv, datetime, json, math, os, re, shutil, sys, time
import numpy as np

try:
    import pexpect
except ImportError:
    sys.exit("pexpect is needed:  pip3 install pexpect")

# ----------------------------------------------------------------- colorimetry
M709 = np.array([[0.4124564, 0.3575761, 0.1804375],
                 [0.2126729, 0.7151522, 0.0721750],
                 [0.0193339, 0.1191920, 0.9503041]])
M709_INV = np.linalg.inv(M709)
D65 = np.array([0.95047, 1.0, 1.08883])
D65_XY = (0.3127, 0.3290)


def xy(XYZ):
    s = XYZ.sum()
    return (XYZ[0] / s, XYZ[1] / s) if s > 0 else (0.0, 0.0)


def uv(XYZ):
    X, Y, Z = XYZ
    d = X + 15 * Y + 3 * Z
    return (4 * X / d, 9 * Y / d) if d > 0 else (0.0, 0.0)


def cct(XYZ):
    x, y = xy(XYZ)
    n = (x - 0.3320) / (0.1858 - y) if (0.1858 - y) != 0 else 0
    return 449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33


def duv_vs_d65(XYZ):
    u, v = uv(XYZ)
    u0, v0 = uv(D65)
    return math.hypot(u - u0, v - v0) * (1 if v >= v0 else -1)


def rgb_balance(XYZ):
    """R,G,B balance (%) relative to Y in linear Rec.709. 100 = neutral."""
    r, g, b = M709_INV @ XYZ
    Y = XYZ[1]
    if Y <= 0:
        return (100.0, 100.0, 100.0)
    return (100 * r / Y, 100 * g / Y, 100 * b / Y)


def lab(XYZ, white):
    def f(t):
        return np.cbrt(t) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    fx, fy, fz = [f(XYZ[i] / white[i]) for i in range(3)]
    return np.array([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)])


def de2000(l1, l2):
    L1, a1, b1 = l1
    L2, a2, b2 = l2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cm = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cm ** 7 / (Cm ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if C1p else 0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if C2p else 0
    dLp, dCp = L2 - L1, C2p - C1p
    if C1p * C2p == 0:
        dhp = 0
    else:
        dhp = h2p - h1p
        dhp -= 360 if dhp > 180 else 0
        dhp += 360 if dhp < -180 else 0
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    else:
        hbp = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dth = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dth)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


def pq_eotf(e):
    m1, m2 = 2610 / 16384, 2523 / 4096 * 128
    c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
    ep = e ** (1 / m2)
    return 10000 * (max(ep - c1, 0) / (c2 - c3 * ep)) ** (1 / m1)


def bt1886(V, Lw, Lb):
    g = 2.4
    a = (Lw ** (1 / g) - Lb ** (1 / g)) ** g
    b = Lb ** (1 / g) / (Lw ** (1 / g) - Lb ** (1 / g))
    return a * max(V + b, 0) ** g


# ----------------------------------------------------------------- meter
class Meter:
    def __init__(self, spot_args, ccmx=None, refresh=None, wait=True):
        exe = shutil.which("spotread")
        if not exe:
            sys.exit("spotread not found. Install ArgyllCMS:  brew install argyll-cms")
        args = list(spot_args)
        if ccmx:
            args += ["-X", ccmx]
        if refresh:
            args += ["-Y", refresh]
        print("Starting:", exe, " ".join(args))
        self.c = pexpect.spawn(exe, args, encoding="utf-8", timeout=30)
        self.wait = wait
        self._start()

    def _start(self):
        while True:
            i = self.c.expect([r"(?i)any key to continue", r"(?i)(to measure|measure it)", pexpect.EOF, pexpect.TIMEOUT],
                              timeout=15)
            before = (self.c.before or "").strip()
            if i == 0:
                print(before[-600:])
                print(">>> The meter asks for the calibration position (if available: turn the diffuser/cap to CAL).")
                if self.wait:
                    input(">>> Do it and press Enter...")
                self.c.send(" ")
                continue
            if i == 1:
                return
            if i == 2:
                print(before)
                sys.exit("spotread exited. Is the meter connected? Did you run Calibrite PROFILER to unlock the HL?")
            if i == 3:
                return  # assume ready

    def read(self):
        self.c.send(" ")
        self.c.expect(r"Result is XYZ:\s*([-+\d.eE]+)\s+([-+\d.eE]+)\s+([-+\d.eE]+)", timeout=60)
        v = np.array([float(g) for g in self.c.match.groups()])
        time.sleep(0.15)
        return v

    def read_avg(self, n):
        vals = [self.read() for _ in range(n)]
        arr = np.array(vals)
        m = arr.mean(axis=0)
        sd = (arr[:, 1].std() / m[1] * 100) if n > 1 and m[1] > 0 else 0.0
        return m, sd

    def close(self):
        try:
            self.c.send("q")
            self.c.close()
        except Exception:
            pass


# ----------------------------------------------------------------- helpers
HUES = ["red", "yellow", "green", "cyan", "blue", "magenta"]
HUE_NAME = {"red": "red", "yellow": "yellow", "green": "green", "cyan": "cyan",
          "blue": "blue", "magenta": "magenta"}


def load_patches(path):
    cands = [path] if path else []
    here = os.path.dirname(os.path.abspath(__file__))
    cands += [os.path.join(here, "patches.json"), "patches.json", os.path.join("patches", "patches.json")]
    for p in cands:
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    sys.exit("patches.json not found (it should be next to pcal.py).")


def tip_balance(bal, tol=2.0):
    names = ["R", "G", "B"]
    parts = []
    for n, b in zip(names, bal):
        if b < 100 - tol:
            parts.append(f"{n}↑ (weaker by {100 - b:.1f}%)")
        elif b > 100 + tol:
            parts.append(f"{n}↓ (stronger by {b - 100:.1f}%)")
    return "OK ✔" if not parts else "  ".join(parts)


def fmt_row(*cells, widths):
    return "".join(str(c).rjust(w) for c, w in zip(cells, widths))


def wait_patch(args, title, fname):
    print("\n" + "─" * 70)
    print(f"PATCH: {title}")
    print(f"FILE: {fname}")
    if args.no_wait:
        return True
    s = input("Play the patch, wait 5 s. Enter = reading, s = skip, q = quit: ").strip().lower()
    if s == "q":
        raise KeyboardInterrupt
    return s != "s"


def save_csv(rows, header, tag):
    fn = f"pcal_{datetime.datetime.now():%Y%m%d_%H%M%S}_{tag}.csv"
    with open(fn, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"\nLog saved: {os.path.abspath(fn)}")


# ----------------------------------------------------------------- modes
def mode_once(m, args):
    XYZ, sd = m.read_avg(args.avg)
    x, y = xy(XYZ)
    bal = rgb_balance(XYZ)
    print(f"Y={XYZ[1]:.2f} nits  x={x:.4f} y={y:.4f}  CCT≈{cct(XYZ):.0f}K  Δuv={duv_vs_d65(XYZ):+.4f}  Y noise ±{sd:.2f}%")
    print(f"RGB balance: R {bal[0]:.1f}  G {bal[1]:.1f}  B {bal[2]:.1f}   → {tip_balance(bal)}")


def mode_live(m, args):
    print("LIVE: Ctrl+C = quit. Change gain/offset in the projector menu; aim for RGB balance 98–102.")
    print("Arrow ↑ = RAISE this channel, ↓ = LOWER it.\n")
    try:
        while True:
            XYZ, sd = m.read_avg(args.avg)
            x, y = xy(XYZ)
            bal = rgb_balance(XYZ)
            print(f"Y {XYZ[1]:7.2f} | x {x:.4f} y {y:.4f} | {cct(XYZ):5.0f}K Δuv {duv_vs_d65(XYZ):+.4f} | "
                  f"R {bal[0]:5.1f} G {bal[1]:5.1f} B {bal[2]:5.1f} | {tip_balance(bal)}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nLIVE stopped.")


def select(patches, mode, kind, only):
    sel = [p for p in patches if p["mode"] == mode and p["kind"] == kind]
    sel.sort(key=lambda p: p["order"])
    if only:
        items = [v.strip().lower() for v in only.split(",")]
        if kind == "gray":
            want = {int(v) for v in items}
            sel = [p for p in sel if round(p["stim"] * 100) in want]
        else:  # colors: --only red,blue (hues) and/or primaries (100%) / sweeps (saturation 25–100%)
            hues = [v for v in items if v in HUES]
            groups = [v for v in items if v in ("primaries", "sweeps")]
            if hues:
                sel = [p for p in sel if p["hue"] in hues]
            if groups:
                sel = [p for p in sel if ("primaries" in groups and p["amp"] == 1.0) or ("sweeps" in groups and p["amp"] != 1.0)]
    return sel


def mode_sdr_gray(m, args, patches):
    sel = select(patches, "sdr", "gray", args.only)
    rows, res = [], {}
    Yw = args.white
    try:
        for p in sel:
            if not wait_patch(args, p["name"], p["file"]):
                continue
            XYZ, sd = m.read_avg(args.avg)
            st = p["stim"]
            if st == 1.0:
                Yw = XYZ[1]
            x, y_ = xy(XYZ)
            bal = rgb_balance(XYZ)
            # color error: compared with D65 at the same luminance
            if XYZ[1] > 0.02:
                wref = D65 * (Yw or XYZ[1])
                de_c = de2000(lab(XYZ, wref), lab(D65 * XYZ[1], wref))
            else:
                de_c = float("nan")
            g_eff = (math.log(XYZ[1] / Yw) / math.log(st)) if (Yw and 0 < st < 1 and XYZ[1] > 0) else float("nan")
            res[round(st * 100)] = (st, XYZ)
            print(f"  Y={XYZ[1]:.3f} nits (±{sd:.1f}%)  xy={x:.4f},{y_:.4f}  CCT≈{cct(XYZ):.0f}K  ΔE(color)={de_c:.1f}  γeff={g_eff:.2f}")
            print(f"  RGB balance: R {bal[0]:.1f}  G {bal[1]:.1f}  B {bal[2]:.1f}  → {tip_balance(bal)}")
            rows.append([p["name"], round(st * 100), f"{XYZ[0]:.4f}", f"{XYZ[1]:.4f}", f"{XYZ[2]:.4f}"])
    except KeyboardInterrupt:
        print("\nInterrupted, computing results from what is available.")
    if 100 not in res:
        print("No 100% white reading — results are impossible."); return
    Yw = res[100][1][1]
    Yb = res[0][1][1] if 0 in res else 0.0
    print("\n" + "=" * 78)
    print(f"SDR RESULT  | White {Yw:.1f} nits | Black {Yb:.3f} nits" + (f" | contrast ≈ {Yw / Yb:.0f}:1" if Yb > 0.001 else ""))
    gam = args.gamma
    use_1886 = (gam == "bt1886")
    gtxt = "BT.1886" if use_1886 else f"gamma {gam}"
    print(f"Target: D65 + {gtxt}")
    w = [8, 11, 11, 9, 8, 9, 9, 9, 9]
    print(fmt_row("Step", "Y meas.", "Y target", "Y dev.", "γeff", "ΔE full", "ΔE color", "R", "B", widths=w))
    des, gms = [], []
    for k in sorted(res, reverse=True):
        st, XYZ = res[k]
        if st == 0:
            continue
        Yt = bt1886(st, Yw, max(Yb, 1e-4)) if use_1886 else Yw * st ** float(gam)
        wref = D65 * Yw
        de_full = de2000(lab(XYZ, wref), lab(D65 * Yt, wref))
        de_c = de2000(lab(XYZ, wref), lab(D65 * XYZ[1], wref)) if XYZ[1] > 0 else float("nan")
        gE = math.log(XYZ[1] / Yw) / math.log(st) if st < 1 and XYZ[1] > 0 else float("nan")
        bal = rgb_balance(XYZ)
        print(fmt_row(f"{k}%", f"{XYZ[1]:.2f}", f"{Yt:.2f}", f"{(XYZ[1] / Yt - 1) * 100:+.1f}%" if Yt > 0 else "-",
                      f"{gE:.2f}" if not math.isnan(gE) else "-", f"{de_full:.1f}", f"{de_c:.1f}",
                      f"{bal[0]:.0f}", f"{bal[2]:.0f}", widths=w))
        if 10 <= k <= 90:
            des.append(de_c)
            if not math.isnan(gE):
                gms.append(gE)
    if des:
        print(f"\nMean ΔE(color) 10–90%: {np.mean(des):.2f}   max: {np.max(des):.2f}   (target: mean < 2, max < 3)")
    if gms and not use_1886:
        print(f"Mean γeff 10–90%: {np.mean(gms):.2f}  (target {gam})  → " +
              ("mids BRIGHTER than target" if np.mean(gms) < float(gam) - 0.05 else
               "mids DARKER than target" if np.mean(gms) > float(gam) + 0.05 else "OK"))
    print("If 10–30% are far off with a good white: adjust offset; if 70–100%: gain.")
    save_csv(rows, ["patch", "stim_%", "X", "Y", "Z"], "sdr-gray")


def mode_sdr_color(m, args, patches):
    if not args.white:
        print("100% white (nits) is needed. Set --white <nits> from sdr-gray or measure it now.")
        if not args.no_wait:
            input("Play SDR_01_gray_100.mp4 and press Enter to measure white...")
        XYZ, _ = m.read_avg(args.avg)
        args.white = XYZ[1]
        print(f"White = {args.white:.2f} nits")
    Yw = args.white
    wref = D65 * Yw
    g = float(args.gamma) if args.gamma != "bt1886" else 2.4
    sel = select(patches, "sdr", "color", args.only)
    rows = []
    print(f"\nTargets: Rec.709, D65, γ {g}; white {Yw:.1f} nits")
    try:
        for p in sel:
            if not wait_patch(args, p["name"], p["file"]):
                continue
            XYZ, sd = m.read_avg(args.avg)
            enc = np.array(p["rgb"])
            tXYZ = Yw * (M709 @ (enc ** g))
            lm, lt = lab(XYZ, wref), lab(tXYZ, wref)
            de = de2000(lm, lt)
            Cm, Ct = math.hypot(lm[1], lm[2]), math.hypot(lt[1], lt[2])
            hm, ht = math.degrees(math.atan2(lm[2], lm[1])) % 360, math.degrees(math.atan2(lt[2], lt[1])) % 360
            dh = (hm - ht + 180) % 360 - 180
            dC = (Cm / Ct - 1) * 100 if Ct > 1 else float("nan")
            dY = (XYZ[1] / tXYZ[1] - 1) * 100
            x, y_ = xy(XYZ)
            tx, ty = xy(tXYZ)
            hue = p["hue"]
            i = HUES.index(hue)
            direction = HUE_NAME[HUES[(i + 1) % 6]] if dh > 0 else HUE_NAME[HUES[(i - 1) % 6]]
            print(f"  ΔE2000={de:.1f}   xy meas {x:.4f},{y_:.4f}  target {tx:.4f},{ty:.4f}")
            msg = []
            if abs(dh) > 2 and p["sat"] >= 0.5:
                msg.append(f"hue shifted {abs(dh):.0f}° toward {direction}")
            if not math.isnan(dC) and abs(dC) > 5:
                msg.append(f"saturation {'ABOVE' if dC > 0 else 'BELOW'} target by {abs(dC):.0f}%")
            if abs(dY) > 5:
                msg.append(f"brightness {'ABOVE' if dY > 0 else 'BELOW'} target by {abs(dY):.0f}%")
            print("  → " + ("; ".join(msg) if msg else "within tolerance ✔"))
            rows.append([p["name"], f"{XYZ[0]:.4f}", f"{XYZ[1]:.4f}", f"{XYZ[2]:.4f}", f"{de:.2f}", f"{dh:.1f}", f"{dC:.1f}", f"{dY:.1f}"])
    except KeyboardInterrupt:
        print("\nInterrupted.")
    if rows:
        des = [float(r[4]) for r in rows]
        print(f"\nRESULT: mean ΔE2000 = {np.mean(des):.2f}, max = {np.max(des):.2f}  (target: mean < 3)")
        print("IMPORTANT: a colorimeter on a laser projector without correction may be off on saturated colors;")
        print("trust the trend and do the final check by eye (skin tones, gray).")
        save_csv(rows, ["patch", "X", "Y", "Z", "dE2000", "dHue_deg", "dChroma_%", "dY_%"], "sdr-color")


def mode_hdr_gray(m, args, patches):
    sel = select(patches, "hdr10", "gray", args.only)
    rows, tracked = [], []
    try:
        for p in sel:
            if not wait_patch(args, p["name"], p["file"]):
                continue
            XYZ, sd = m.read_avg(args.avg)
            tn = p["tnits"]
            x, y_ = xy(XYZ)
            bal = rgb_balance(XYZ)
            de_c = None
            if XYZ[1] > 0.02:
                wref = D65 * XYZ[1]
                de_c = de2000(lab(XYZ, wref), lab(D65 * XYZ[1], wref))
            ratio = XYZ[1] / tn * 100 if tn > 0 else float("nan")
            print(f"  Y={XYZ[1]:.3f} nits (PQ target {tn:.2f}; {ratio:.0f}% of target)  xy={x:.4f},{y_:.4f}  CCT≈{cct(XYZ):.0f}K"
                  + (f"  ΔE(color)={de_c:.1f}" if de_c is not None else ""))
            print(f"  RGB balance: R {bal[0]:.1f}  G {bal[1]:.1f}  B {bal[2]:.1f}  → {tip_balance(bal)}")
            tracked.append((p["stim"], tn, XYZ[1]))
            rows.append([p["name"], f"{XYZ[0]:.4f}", f"{XYZ[1]:.4f}", f"{XYZ[2]:.4f}", f"{tn:.3f}"])
    except KeyboardInterrupt:
        print("\nInterrupted.")
    if tracked:
        tracked.sort()
        peak = max(t[2] for t in tracked)
        print(f"\nHDR RESULT: projector peak ≈ {peak:.0f} nits.")
        for st, tn, y in tracked:
            if tn > 3 and y < 0.9 * tn:
                print(f"Roll-off (tone mapping) starts around PQ {st * 100:.0f}% (≈{tn:.0f} nits target → {y:.0f} nits). "
                      f"Below that point the luminance should match the target.")
                break
        print("HDR goal: neutral gray (RGB balance 98–102) over the whole range; the projector shapes the roll-off itself.")
        save_csv(rows, ["patch", "X", "Y", "Z", "target_nits"], "hdr-gray")


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Projector calibration: measurement helper (ArgyllCMS spotread)")
    ap.add_argument("mode", choices=["once", "live", "sdr-gray", "sdr-color", "hdr-gray"])
    ap.add_argument("--gamma", default="2.4", help="SDR target: 2.4 (default), 2.2, 2.35 or bt1886")
    ap.add_argument("--avg", type=int, default=3, help="how many readings to average (default 3)")
    ap.add_argument("--only", help="only these steps, %% comma-separated (100,50,20,0); for colors: primaries, sweeps or hues red,green,blue")
    ap.add_argument("--white", type=float, help="white luminance, nits (for sdr-color)")
    ap.add_argument("--ccmx", help="correction file .ccmx/.ccss, if any")
    ap.add_argument("--refresh", choices=["n", "r"], help="spotread integration mode -Y (n = non-refresh, r = refresh)")
    ap.add_argument("--flag", default="-e", help="spotread mode: -e (default). Change only if there are problems")
    ap.add_argument("--spot-args", default="", help="extra spotread arguments in quotes")
    ap.add_argument("--interval", type=float, default=0.3, help="pause between readings in live")
    ap.add_argument("--patches", help="path to patches.json")
    ap.add_argument("--no-wait", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    spot = [args.flag] + args.spot_args.split()
    m = Meter(spot, args.ccmx, args.refresh, wait=not args.no_wait)
    try:
        if args.mode == "once":
            mode_once(m, args)
        elif args.mode == "live":
            mode_live(m, args)
        else:
            patches = load_patches(args.patches)
            {"sdr-gray": mode_sdr_gray, "sdr-color": mode_sdr_color, "hdr-gray": mode_hdr_gray}[args.mode](m, args, patches)
    finally:
        m.close()


if __name__ == "__main__":
    main()
