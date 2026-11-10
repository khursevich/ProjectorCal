# Case study: calibrating a Vidda C3S

A real calibration done with ProjectorCal in November 2026, from the first full run to the final settings. It is
also where most of the projector-specific knowledge built into the program comes from.

**Setup:** Vidda C3S (RGB triple laser, DLP), gray ALR-type screen, streaming box with Plex, Calibrite Display Plus HL
on ArgyllCMS `spotread -e` (no spectral correction), dark room, target D65 / gamma 2.4 / Rec.709.

## The runs

| Run | Changed before the run | Gamma | Gray ΔE mean / max | Color ΔE, weighted | Note |
|---|---|---|---|---|---|
| SDR-1 | first full run (old video) | — | — | — | only 22 of 51 patches captured — see below |
| SDR-2 | gamma points as entered from the menu | 2.44 | 0.84 / 1.61 | 2.5 | shadows 10–45 % were 7–17 % too dark |
| SDR-3 | gamma points 2–10, Offset R −1 → 0, CMS blue/magenta | 2.40 | 3.11 / 4.57 | 2.4 | gamma fixed; Offset made everything warm |
| SDR-4 | Offset R back to −1, gamma points re-computed | 2.39 | 0.98 / 2.22 | 2.5 | best gray so far |
| SDR-5 | gamma points 2/6/10/11, CMS on all six colors | 2.41 | 1.05 / 2.15 | 2.13 | laser warm-up of 5 % corrected; room light at the end |
| SDR-6 | CMS saturation of red/yellow/green/blue, blue Bright | 2.39 | 1.34 / 2.79 | 1.79 | white balance breathed 2–3 % warmer with no change |
| SDR-7 | magenta Saturation +3 / Bright −1 | **2.40** | **1.04 / 2.09** | **1.67** | best overall |

Gamma in SDR-7: every step from 10 % to 95 % within ±1.8 % of the target. Contrast ≈ 1340:1 (black 0.028 nits).

## Final settings (SDR, Filmmaker)

| Group | Values |
|---|---|
| Basics | Laser (Brightness) 10 · Black Level 54 · Contrast 47 · Color Temp *hot* · Color Space *auto* · Sharpness 0 |
| White balance | Gain R/G/B 1 / −5 / −12 · Offset R/G/B −1 / −1 / −1 |
| Gamma | preset *dark*; points 1…12: −20, −7, −8, −7, −3, −3, −4, −4, −2, −1, −4, −3; point 14: −2; others 0 |
| CMS (tone / sat / bright) | Red 0/0/2 · Yellow 0/−1/0 · Green 0/−1/1 · Cyan −1/6/0 · Blue −1/−1/2 · Magenta 0/−2/3 |
| HDR10 | Filmmaker, laser 10, Gain R/G/B 7 / 0 / −6, Offset R/G/B −1 / 0 / −2 |

These values are specific to this unit, screen and meter — use them only as a starting point; run the full check on
your own projector.

## What we learned about the projector

* **20 gamma points, point k = 5·k % stimulus.** With all points at 0 vs the menu values, the "20 points at 5 %"
  model explained 98 % of the luminance change on 20–90 % steps, the "10 points at 10 %" model only 75 %. One step
  ≈ 2.2 % luminance of its step. The 10 % step varies ±3–6 % between runs (0.15 nits, 7-second readings), so it is not
  worth chasing.
* **Offset is extremely coarse.** Offset R −1 → 0 changed R/G by +5.8 % at 100 %, +10 % at 50 %, +26 % at 20 % and
  +47 % at 10 % — almost exactly 5 %/s. With −1 the shadows are 5–7 % short of red, which is ΔE ≈ 1 and invisible;
  with 0 the whole scale turns warm (ΔE 3–4.6). There is nothing in between.
* **CMS Bright ≈ 10–12 % of the color's luminance per step**, acting on the whole hue: ≈ 0.27 of that at 25 %
  saturation, ≈ 0.6 at 50 %, fully from 75 %. Lowering blue/magenta Bright made 100 % blue darker than the target but
  brought the mid-saturation blues and magentas, which are far more common in films, from ΔE 5–7 down to 1.6–3.5.
* **CMS Saturation ≈ 1.4–2 % chroma per step** at 100 %.
* **The white balance breathes** by 2–3 % (≈ 100–150 K) between sessions without any change — do not chase it.
* **Warm-up:** right after power-on the white rose by 5 % with τ ≈ 26 s (most of it in the first 1.5 minutes).
* **Intermediate saturations** stay 5–10 % undersaturated even with accurate primaries — a property of the
  projector's color processing; Color Space BT.709 instead of Auto is the next thing to try.
* **HDR10:** the reference white (203 nits in the master) lands at ≈ 23 nits, 56 % of the ≈ 41-nit peak — a
  sensible tone mapping for a dark room.

## What we learned about the measurement

* The first full run captured only 22 of 51 patches and silently mixed up three colors: the simulator assumed
  0.25–3.5 s per reading, the real meter needs up to 7 s on dark patches and 3–6 s on single primaries. The video
  schedule was rebuilt around a measured reading-time model and the analyzer gained a chromaticity check, a
  multi-candidate sync and stored raw readings (any run can be re-analyzed later).
* A start mark on black does not work: a single black reading is longer than the whole mark. The mark is now
  gray → white → gray.
* A person blocking the beam for a moment shows up as one odd reading and is rejected; switching on the room light
  near the end spoiled one patch, which is now flagged ("before failure").
* Moving the meter changed the absolute luminance by 8 % but not the relative results (gamma, balance, colors).
