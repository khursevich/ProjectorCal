# Supported projectors and meters

ProjectorCal does not need a model-specific database. It measures, suggests a change, measures again and learns how
many percent one menu step moves *your* projector. What a profile adds is the menu layout that cannot be learned:
how many gamma points there are, and which meter timing to use for the automatic run.

**Verified** means full calibration runs on real hardware. Everything else is expected to work and is set up from
the same assumptions. Please report your model (see [Help us verify your model](#help-us-verify-your-model)).

## Projectors

| Model | Status | Gamma points (Control Point) | Notes |
|---|---|---|---|
| **Vidda C3S** | ✅ verified (7 full runs) | 20 × 5 % | [Case study](case-study/vidda-c3s.md) |
| Vidda C3 / C3 Pro / C3 Ultra | expected to work | 20 × 5 % (assumed) | Same VIDAA Expert menu family |
| Hisense / Vidda C2, C2 Pro, C2 Ultra, C2 Max | expected to work | 20 × 5 % (assumed) | Check the point count in your menu |
| Hisense / Vidda C1, C1S | expected to work | check | |
| Hisense PX1 / PX2 / PX3 (Pro) laser UST | expected to work | check | White Balance + Gamma Calibration in the Expert menu |
| Hisense Laser TV (L5, L9 …) | white balance + CMS | none (start with this) | 2-point and multi-point white balance |
| Any other projector or TV | white balance + CMS | 20, 10 or none | Needs Gain/Offset (or RGB high/low) white balance |

The models above run Hisense's VIDAA system and share the same style of Expert picture menu: **Color Temperature →
White Balance (Gain / Offset R G B)**, **Gamma → Adjust → Control Point (Gain)**, **Color Tuner / CMS (Hue,
Saturation, Brightness of 6–7 colors)**. Menu names differ slightly between models and firmware versions.

### Choosing the gamma layout

Open **Gamma → Adjust** (or *Gamma Calibration*) in your projector menu and count the Control Points.

* **20 points** → point *k* acts on the *5·k %* gray step (point 10 = 50 %). This is the Vidda C3S layout.
* **10 points** → point *k* acts on *10·k %*.
* **No points** (only presets like 2.0 / 2.2 / 2.4 / BT.1886) → choose *no luminance gamma points*: the program then
  suggests which preset to use.

Not sure? Pick the closest layout and use **Gamma by points → point probe**: change one point by a few steps, do a
check pass, and the program shows at which gray step that point's effect peaks.

### What else differs between models (and how the program copes)

| Quantity | On the C3S | On other models |
|---|---|---|
| Gain: effect per step | learned in the first run with a Gain change | the same |
| Offset: effect per step | coarse, ≈ 5 % at white, 25 % at 20 % gray | learned from a run with an Offset change |
| Gamma point: % per step | ≈ 2.3 % of its step's luminance | learned from the first gamma round |
| CMS Bright / Saturation | ≈ 10–12 % / ≈ 2 % per step | learned from the first CMS change |

Until a step size is learned, the program limits the first change (e.g. at most 2 steps of Gain) so an unknown
projector is never pushed far in the wrong direction.

## Meters (colorimeters and spectrometers)

ProjectorCal talks to the meter through [ArgyllCMS](https://www.argyllcms.com/doc/instruments.html) `spotread`, so
any instrument ArgyllCMS supports can be used. Pick the model in **Settings → Meter model**: it decides which
automatic video to play.

| Meter | Status | Auto-run video | Notes |
|---|---|---|---|
| **Calibrite Display Plus HL** | ✅ verified | standard | Run Calibrite PROFILER once so third-party software can use it |
| i1Display Pro, i1Display Pro Plus, Calibrite ColorChecker Display Pro / Plus | expected to work | standard | Same i1Display family; ≈ 1 s on white, up to ≈ 10 s on black |
| ColorMunki Display, i1Display Studio, Calibrite ColorChecker Display | expected to work | **slow** | Same sensor, firmware-limited to slower readings |
| Datacolor SpyderX / X2 / Spyder (2024) / Spyder5 / Spyder4 | expected to work | **slow** | Warm up 10–15 min; black calibration when asked. Spyder4/5: install calibration data once with ArgyllCMS `oeminst` |
| X-Rite i1Pro / i1Pro 2 / i1Pro 3, ColorMunki Photo / Design, i1Studio | expected to work | **slow** | Spectrometers: accurate color, noisy below ≈ 1 nit — the darkest patches may be flagged. White-tile calibration at start |
| Klein K10-A | expected to work | standard | Fast and very good in low light |
| DTP94, Huey, ColorHug / ColorHug2, Spyder2/3, JETI specbos / spectraval, HCFR | should work | **slow** | Older/slower devices: gray tracking and gamma are fine, absolute color is less certain |

The **slow** videos show every patch twice as long (SDR ≈ 27 min instead of 14, HDR10 ≈ 13 min instead of 6.5).
If a meter is even slower than that, the program does not guess: the patches it could not read are flagged and left
out of the analysis.

### Accuracy on laser projectors

Laser light has very narrow spectral peaks. A colorimeter's factory calibration was made for other displays, so its
absolute white point and saturated colors can be off by a few hundred kelvin / a few ΔE on an RGB-laser projector.
What stays reliable: **gray-scale tracking, gamma, and every change between runs** — which is what calibration is
about. To get absolute accuracy as well:

* use a spectral correction (`.ccss`) for RGB-laser displays, e.g. from the
  [DisplayCAL colorimeter corrections database](https://colorimetercorrections.displaycal.net/), and set it in
  **Settings → Correction (-X)**, or install it into the meter and pick it under **Display type (-y)**;
* or, if you have access to a spectrometer (i1Pro family), create your own `.ccss` with ArgyllCMS `ccxxmake` and use
  it with your colorimeter.

## Help us verify your model

Calibrated a model that is not marked as verified? Open an issue titled **"Verified: <projector> + <meter>"** with:

1. projector model and firmware version, meter model, ArgyllCMS version;
2. the number of gamma Control Points in your menu and, if you ran it, the point-probe result;
3. the exported JSON of your runs, or at least the run table from the *Full check* screen (γ, gray ΔE, color ΔE).

That is enough to mark the model as verified and to add its measured step sizes as starting values.
