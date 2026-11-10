# How ProjectorCal works

## Principles

* Calibrate with the **projector's own controls** (white balance Gain/Offset, multi-point gamma, 6-color CMS).
  The program measures and says what to change and by how much.
* Measure through the **real signal path**: the patches are played by the same player you watch movies with.
* Targets: SDR — D65 white, gamma 2.4 (2.2 / 2.3 / BT.1886 selectable) relative to the *measured* white, Rec.709
  colors. HDR10 — neutral gray over the whole range; the projector's tone mapping is reported, not "fixed".
* Gray scale is measured reliably; saturated colors from a colorimeter on a laser projector are read as a trend.

## Meter link

* ArgyllCMS `spotread -e` (emissive, absolute nits) runs as an interactive process on a pseudo-terminal. The pty is
  its controlling terminal, so if ProjectorCal dies the kernel sends `spotread` SIGHUP — no orphaned processes.
* A key press is sent **only after** the `spotread` prompt (a key during a reading may be taken as cancel).
* Result line: `Result is XYZ: X Y Z`. A calibration request (`any key to continue`) opens a dialog.
  EOF/timeouts produce a clear message, automatic retry after 10 s and a *Retry* button.
* Optional: `-Y n|r` (integration mode), `-X file.ccmx|.ccss` (spectral correction), extra arguments.
* All raw output goes to `sessions/<id>.spotread.log` and the *Meter log* window.

## Formulas

* xy = (X, Y) / (X+Y+Z); u′v′ = (4X, 9Y) / (X+15Y+3Z).
* CCT (McCamy): n = (x − 0.3320)/(0.1858 − y); CCT = 449n³ + 3525n² + 6823.3n + 5520.33.
* **RGB balance** (%): (r, g, b) = M₇₀₉⁻¹ · XYZ, balanceᶜ = 100 · c / Y. D65 neutral → 100/100/100.
* Lab with white = D65 · Y_white; **ΔE2000** (Sharma, verified to 4 decimals on the reference pairs).
* Gray "color ΔE": Lab(measured) vs Lab(D65 · Y_measured) — luminance equalized, pure color error.
* γ_eff = ln(Y / Y_white) / ln(stim), 0 < stim < 1.
* SDR target: Y_t = Y_white · stim^γ. BT.1886 option: a = (Lw^(1/2.4) − Lb^(1/2.4))^2.4,
  b = Lb^(1/2.4) / (Lw^(1/2.4) − Lb^(1/2.4)), L = a · max(V + b, 0)^2.4.
* HDR target: PQ EOTF (ST.2084), code = 64 + 876 · s (10-bit limited).
* Color target: XYZ_t = Y_white · M₇₀₉ · (rgb_encoded^γ). Hue/saturation errors in Lab (Δh°, ΔC% = C/C_t − 1).

## Tolerances

| Quantity | Good | Acceptable |
|---|---|---|
| Gray RGB balance (bright steps) | 98–102 | 95–105 |
| Gray color ΔE mean / max | < 1.5 / < 2.5 | < 3 / < 4 |
| Gamma, 10–90 % | target ± 0.05 | ± 0.1 |
| Gray step luminance, ≥ 30 % | ± 1.5 % | ± 3 % |
| Gray step luminance, 10–25 % | ± 3 % | ± 6 % |
| ΔE2000 of the 6 primaries (mean) | < 3 | < 5 |

Balance tolerances in the planner follow visibility: 2 % on steps ≥ 60 %, 3 % at 40–50 %, 6 % at 20–30 %
(blue ×1.5) — the same percentage is a much smaller ΔE on dark steps.

## The full automatic check

### The video

One file per mode, built from the patches without re-encoding (`tools/make_sequence.py`, every segment and splice is
verified frame by frame). The schedule lives in `projectorcal/sequence.py` and is the single source of truth for the
video generator and the analyzer.

* **Start mark:** gray 8 s → **white 12 s** → gray. The anchor is the white → gray fall: readings on white take ≈ 1 s,
  so its moment is known precisely. Black cannot be used: a single reading on black takes longer than the mark.
* **Order:** gray 100, 50, 95, 45, …, 55, 5, 0 — upper and lower halves interleaved so neighbors differ 5–200× in
  luminance and a "shift by one patch" is immediately implausible; then the six colors from fast to slow to read;
  then the saturation sweeps; 16 s control whites between blocks; the same mark at the end.
* **Segment length** = 1.25 × (tail of a reading started on the previous picture + 2–3 whole readings) + 1 s, from a
  model of the meter's reading time fitted to real runs: ≈ 0.4–1.1 s on white, 1.7 s at 7 nits, 4 s at 0.7 nits,
  6–7 s at 0.2 nits and on black; single primaries without blue (red, green, yellow) or blue alone take 3–6 s because
  the adaptive integration waits for the "empty" sensor channels.

### The analyzer (`projectorcal/autorun.py`)

1. **Sync.** All mark candidates in the readings are tried; the one the schedule fits best wins (a frozen frame or a
   similar place in the file cannot fool it).
2. **Runs of constant picture.** Consecutive readings are grouped by log-luminance + chromaticity. A reading that
   straddles a transition is recognized as a mix; its mixing fraction gives the exact transition time. Single
   outliers (menu, glare) are dropped.
3. **Matching** runs to schedule segments by dynamic programming: cost for time shift (jitter tolerance grows with
   the reading length around the transition; player pauses are allowed, going back in time is not),
   extra/missing runs, implausible luminance or chromaticity. Identical neighboring patches (HDR above the peak) are
   covered by one run and split by time.
4. **Assignment.** A reading counts for a segment only if it lies entirely inside it with margins and also within the
   scheduled end (a long dark reading must not drag a value toward its neighbor). Robust mean per segment.
5. **Validation.** Luminance relative to the mark white; color patches must be closer to their own target
   chromaticity than to their scheduled neighbors' (catches a shift invisible in luminance); gray must be monotonic.
   A tail of failed segments (room light on, beam blocked, video stopped) also flags the patch right before it.
   Flags: `no readings`, `single reading` (only if that reading was fast), `implausible`, `unstable`,
   `non-monotonic`, `before failure`. Flagged segments never enter the analysis.

Verified on randomized simulations (Plex stalls, menus in the meter, jitter, a meter 30 % slower than the model):
≈ 6000 patches per test batch, no silent errors.

### Warm-up / drift correction

White is measured several times per run (mark, the 100 % patch, control whites, finish). If it changed by more than
1 %, W(t) = A − B·e^(−t/τ) is fitted (τ by search, A/B by least squares; piecewise-linear if that fits badly) and every
reading is scaled by W_ref / W(t). A warm-up of 5 % in the first two minutes is corrected to ± 0.3 %.

## The plan and the verdict (`projectorcal/fulltest.py`)

### White balance

Gain and Offset are solved **together** from the shape of R/G and B/G over 20–100 %: Gain shifts the whole scale
equally, Offset acts like k/s (stimulus s), strong in the shadows and still noticeable at white. Whole steps are
searched; a solution is accepted only if it lowers the visibility-weighted cost, makes no step worse than the worst
one was before, and the deviation is outside the run-to-run spread (≈ 2–3 %) unless the step size is already
learned. The first trial of an unlearned Gain is limited to 2 steps.

### Gamma points

ΔlnY(s) = g · Σ Δk · tent(s, 5k), g = ln(1 + per-step %). Least squares over the 10–95 % steps with light
regularization and smoothing; steps whose target is below twice the black level are excluded (that would crush
near-black into black); a step already within the good tolerance is not touched for ±1. If the plan also changes
Offset, the gray scale is first predicted after that Offset change (red/blue share in Y) so both are consistent.

### CMS

For each hue the program searches Bright and Saturation steps that minimize the **weighted ΔE2000 over all its
saturations**: weights 1.0 (25 %, 50 %), 0.8 (75 %), 0.5 (100 %). A step's effect at saturation s is modeled as
min(1, (s/0.75)^1.2) of its full effect (measured: ≈ 0.27 at 25 %, ≈ 0.6 at 50 %). Bright ≈ 10 %/step and
Saturation ≈ 2 %/step until learned. Color tone is suggested only after its direction is learned. If no combination
improves the hue by ≥ 0.2 ΔE, the program says CMS cannot help and points to Color Space.

### Learning

Consecutive runs of one mode are compared; the settings diff is grouped (Gain, Offset, gamma points, each CMS color,
…). If the groups are separable (one of Gain/Offset + gamma + any CMS colors), each group's metric change is
converted to "per step" (Gain: R/G at 100 %; Offset: k from R/G·s at 20–50 %; gamma: fit of the tent model; CMS: dY/dC
of the 100 % patch). The median of all learned values replaces the defaults.

## The manual wizard

Preparation (checklist, meter check, room light) → Black level/contrast (PLUGE, clipping) → Laser level (fix the
white) → Gray scale circles (100/80/60/40/20/10 with one "next action": Gain → Offset → gamma → mids) → Gamma by
points (base pass → corrections for all 20 points → check pass, with a point probe) → Colors → SDR verification →
Visual check → HDR10 (PQ gray, tone-mapping report, reference white 203 nits at 50–70 % of the peak) → Dolby Vision
checklist → Summary and report.

A manual reading is rejected if its samples come from two pictures; a reading that does not look like the expected
patch asks before recording; a menu change marks dependent steps for recheck.
