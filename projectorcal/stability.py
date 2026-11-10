"""Stability and patch-change detector (docs/HOW_IT_WORKS.md)."""
from __future__ import annotations

import math

from .colorimetry import xy


def y_tolerance(Y, base_pct=0.7):
    """Y tolerance (fraction) for "stable": the HL sensor is noisier on dark patches."""
    if Y >= 1.0:
        return base_pct / 100
    if Y >= 0.3:
        return 0.03
    return 0.06


class StabilityDetector:
    """Window of recent readings → "stable" / "patch changed".

    stable: the last `window` values: Y spread within ±tol (of the mean) and x, y spread < 0.0015
    (xy is checked only at Y ≥ 1 nit — chromaticity is noisy on dark patches).
    changed(ref): Y differs from the reference by > 8 % or xy by > 0.01.
    """

    def __init__(self, window=3, y_pct=0.7, xy_tol=0.0015, change_y=0.08, change_xy=0.01, min_span=0.0):
        self.window = window
        self.min_span = min_span      # the stability window must last at least this long, s
        self.times = []
        self.y_pct = y_pct
        self.xy_tol = xy_tol
        self.change_y = change_y
        self.change_xy = change_xy
        self.values = []

    def reset(self):
        self.values = []
        self.times = []

    def add(self, XYZ, t=None):
        import time
        self.values.append(tuple(XYZ))
        self.times.append(time.monotonic() if t is None else t)
        if len(self.values) > 200:
            self.values = self.values[-200:]
            self.times = self.times[-200:]

    def last(self, n=None):
        """Window for evaluation: at least `window` readings and at least `min_span` seconds."""
        if n is None and self.min_span and self.times:
            t_end = self.times[-1]
            k = self.window
            while k < len(self.values) and t_end - self.times[-k] < self.min_span:
                k += 1
            return self.values[-k:]
        n = n or self.window
        return self.values[-n:]

    def is_stable(self):
        w = self.last()
        if len(w) < self.window:
            return False
        if self.min_span and self.times[-1] - self.times[-len(w)] < self.min_span:
            return False
        Ys = [v[1] for v in w]
        mean = sum(Ys) / len(Ys)
        if mean <= 0:
            return False
        tol = y_tolerance(mean, self.y_pct)
        if max(abs(y / mean - 1) for y in Ys) > tol:
            return False
        if mean >= 1.0:
            xs = [xy(v) for v in w]
            if max(p[0] for p in xs) - min(p[0] for p in xs) > self.xy_tol:
                return False
            if max(p[1] for p in xs) - min(p[1] for p in xs) > self.xy_tol:
                return False
        return True

    def mean(self):
        w = self.last()
        n = len(w)
        return tuple(sum(v[i] for v in w) / n for i in range(3)) if n else None

    def sd_pct(self):
        w = self.last()
        if len(w) < 2:
            return 0.0
        Ys = [v[1] for v in w]
        m = sum(Ys) / len(Ys)
        if m <= 0:
            return 0.0
        return math.sqrt(sum((y - m) ** 2 for y in Ys) / len(Ys)) / m * 100

    def changed_from(self, ref, XYZ=None):
        """Whether the current (or given) value differs from the reference enough to be another patch."""
        cur = XYZ or (self.values[-1] if self.values else None)
        if cur is None or ref is None:
            return True
        if ref[1] <= 0 or cur[1] <= 0:
            return abs(cur[1] - ref[1]) > 0.02
        if abs(cur[1] / ref[1] - 1) > self.change_y:
            return True
        if cur[1] >= 1.0 and ref[1] >= 1.0:
            a, b = xy(cur), xy(ref)
            if math.hypot(a[0] - b[0], a[1] - b[1]) > self.change_xy:
                return True
        return False
