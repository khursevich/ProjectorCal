"""Colorimetry. A 1:1 port of reference/pcal.py without numpy (standard library only).

All luminances are in nits (cd/m²). XYZ is a tuple of three floats.
"""
from __future__ import annotations

import math

M709 = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)
D65 = (0.95047, 1.0, 1.08883)
D65_XY = (0.3127, 0.3290)
REC709_PRIMARIES_XY = ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060))


def _inv3(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return (
        ((e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det),
        ((f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det),
        ((d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det),
    )


M709_INV = _inv3(M709)


def matvec(m, v):
    return tuple(m[r][0] * v[0] + m[r][1] * v[1] + m[r][2] * v[2] for r in range(3))


def scale(v, k):
    return (v[0] * k, v[1] * k, v[2] * k)


def xy(XYZ):
    s = XYZ[0] + XYZ[1] + XYZ[2]
    return (XYZ[0] / s, XYZ[1] / s) if s > 0 else (0.0, 0.0)


def uv(XYZ):
    X, Y, Z = XYZ
    d = X + 15 * Y + 3 * Z
    return (4 * X / d, 9 * Y / d) if d > 0 else (0.0, 0.0)


def cct(XYZ):
    """Correlated color temperature, McCamy's formula."""
    x, y = xy(XYZ)
    n = (x - 0.3320) / (0.1858 - y) if (0.1858 - y) != 0 else 0
    return 449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33


def duv_vs_d65(XYZ):
    """u'v' distance to the D65 white, signed by v' (as in pcal.py)."""
    u, v = uv(XYZ)
    u0, v0 = uv(D65)
    return math.hypot(u - u0, v - v0) * (1 if v >= v0 else -1)


def rgb_balance(XYZ):
    """R,G,B balance (%) relative to Y in linear Rec.709. 100 = neutral."""
    r, g, b = matvec(M709_INV, XYZ)
    Y = XYZ[1]
    if Y <= 0:
        return (100.0, 100.0, 100.0)
    return (100 * r / Y, 100 * g / Y, 100 * b / Y)


def lab(XYZ, white):
    def f(t):
        return math.copysign(abs(t) ** (1 / 3), t) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = [f(XYZ[i] / white[i]) for i in range(3)]
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def lch(L):
    """(L, C, h°) from Lab."""
    return (L[0], math.hypot(L[1], L[2]), math.degrees(math.atan2(L[2], L[1])) % 360)


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
    """SMPTE ST.2084: signal 0..1 → nits."""
    m1, m2 = 2610 / 16384, 2523 / 4096 * 128
    c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
    ep = max(e, 0.0) ** (1 / m2)
    return 10000 * (max(ep - c1, 0) / (c2 - c3 * ep)) ** (1 / m1)


def bt1886(V, Lw, Lb):
    g = 2.4
    a = (Lw ** (1 / g) - Lb ** (1 / g)) ** g
    b = Lb ** (1 / g) / (Lw ** (1 / g) - Lb ** (1 / g))
    return a * max(V + b, 0) ** g


def gamma_value(gamma):
    """'2.4' → 2.4; 'bt1886' → 2.4 (for color targets)."""
    try:
        return float(gamma)
    except (TypeError, ValueError):
        return 2.4


def sdr_target_Y(stim, Yw, gamma, Yb=0.0):
    if gamma == "bt1886":
        return bt1886(stim, Yw, max(Yb, 1e-4))
    return Yw * stim ** gamma_value(gamma)


def color_target_XYZ(rgb_encoded, Yw, gamma):
    g = gamma_value(gamma)
    lin = tuple(max(c, 0.0) ** g for c in rgb_encoded)
    return scale(matvec(M709, lin), Yw)


def gamma_eff(Y, Yw, stim):
    if Yw and Yw > 0 and 0 < stim < 1 and Y > 0:
        return math.log(Y / Yw) / math.log(stim)
    return None


def gray_de_color(XYZ, Yw):
    """Color-only ΔE2000: against D65 of the same luminance (luminance equalized)."""
    if XYZ[1] <= 0:
        return None
    wref = scale(D65, Yw or XYZ[1])
    return de2000(lab(XYZ, wref), lab(scale(D65, XYZ[1]), wref))


def gray_de_full(XYZ, Yw, Yt):
    wref = scale(D65, Yw)
    return de2000(lab(XYZ, wref), lab(scale(D65, Yt), wref))


def hue_diff(h_meas, h_target):
    """Angle difference in the range −180…180."""
    return (h_meas - h_target + 180) % 360 - 180
