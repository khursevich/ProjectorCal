# ProjectorCal — free calibration for Vidda & Hisense laser projectors

**Calibrate your Vidda / Hisense projector yourself — with a colorimeter, a USB stick and one evening.**
ProjectorCal measures the picture, tells you **exactly which menu values to set** (white balance Gain/Offset,
gamma control points, CMS), and learns from every run how *your* projector reacts.

Works with Vidda C3S (verified), C3 / C3 Pro / C3 Ultra, Hisense C2 / C2 Pro / C2 Ultra, C1, PX1 / PX2 / PX3 laser
UST projectors and Hisense laser TVs — and with any colorimeter or spectrometer ArgyllCMS supports: i1Display Pro,
Calibrite Display Plus HL / ColorChecker Display, ColorMunki Display, Datacolor SpyderX / Spyder5, i1Pro, Klein K10.

Free and open source (MIT). Runs locally in your browser on **Windows, macOS and Linux**. No account, no telemetry.

**→ [Beginner's guide: calibrate your projector step by step](docs/GUIDE.md)** ·
[Supported projectors & meters](docs/SUPPORTED.md) · [FAQ](docs/FAQ.md) · [Русский](README.ru.md)

![workflow](docs/img/workflow.svg)

## How it works in one minute

1. **Enter** the current projector menu values (once).
2. **Play one video** on the projector — from a USB stick or Plex — and leave the room (SDR 14 min).
3. ProjectorCal reads the meter, recognizes all 51 patches by itself and checks every reading.
4. You get a **verdict with numbers**: *"Point 6: −11 → −3, Offset R: 0 → −1, Blue Bright: 4 → 5"*.
5. Set them, run again. Usually **3–6 runs** to reach gray ΔE ≈ 1 and gamma 2.4 on every step.

## Features

* **Fully automatic check from one video file.** Gray scale 0–100 % in 5 % steps, six primaries/secondaries and
  24 saturation sweeps. Robust to real life: menus on screen, someone walking through the beam, the room light
  switched on, a stuttering player — suspicious readings are flagged, never used silently.
* **Concrete menu values, not just charts.** All changes at once: Gain/Offset solved together, all gamma points in
  one least-squares fit, CMS per color.
* **Learns your projector.** Every run is compared with the previous one; the program measures how much one step of
  each control really moves the picture and uses that for the next plan. A new model needs no database.
* **Movie-oriented color.** CMS suggestions minimize ΔE2000 over all saturations, with mid-tones (25–75 %) weighted
  above 100 % — skin, sky and foliage matter more than pure primaries.
* **Any ArgyllCMS meter.** Meter auto-detection, a *slow* video variant for slower meters (ColorMunki Display,
  Spyder, i1Pro), display-type (`-y`) and `.ccss`/`.ccmx` spectral-correction support.
* **Projector profiles.** 20-point (5 %), 10-point (10 %) or no gamma-point menus; the point probe tells you where
  an unknown point acts.
* **Warm-up drift correction**, raw readings stored (re-analyze old runs with a newer version), guided manual wizard
  (PLUGE / clipping, gray scale with live RGB bars, gamma by points, CMS, HDR10, Dolby Vision checklist), printable
  report, CSV/JSON export.

## Supported hardware

| Projector | Status | | Meter | Status | Video |
|---|---|---|---|---|---|
| Vidda C3S | ✅ verified | | Calibrite Display Plus HL | ✅ verified | standard |
| Vidda C3 / C3 Pro / C3 Ultra | expected | | i1Display Pro / Pro Plus, ColorChecker Display Pro / Plus | expected | standard |
| Hisense / Vidda C2 family | expected | | ColorMunki Display, i1Display Studio | expected | slow |
| Hisense / Vidda C1, C1S | expected | | SpyderX / X2 / Spyder5 / Spyder4 | expected | slow |
| Hisense PX1 / PX2 / PX3 (Pro) | expected | | i1Pro 1/2/3, ColorMunki Photo, i1Studio | expected | slow |
| Hisense Laser TV | WB + CMS | | Klein K10-A | expected | standard |
| Other projector / TV with Gain/Offset + CMS | WB + CMS | | Any other ArgyllCMS instrument | should work | slow |

Details, menu layouts and accuracy notes: [docs/SUPPORTED.md](docs/SUPPORTED.md). Calibrated a model that isn't
verified yet? [Tell us](docs/SUPPORTED.md#help-us-verify-your-model) — one report is enough.

## Install

Download from [Releases](https://github.com/khursevich/ProjectorCal/releases/latest): the package for your system
(≈ 0.2 MB) and `ProjectorCal-patches-v3.zip` (≈ 1 GB, the video patches — the same for every system).

| | Windows 10 (1809+) / 11 — *beta* | macOS 12+ | Linux |
|---|---|---|---|
| Download | `ProjectorCal-Windows.zip` | `ProjectorCal-macOS.zip` | `ProjectorCal-Linux.zip` |
| Python 3.8+ | [python.org](https://www.python.org/downloads/windows/) or `winget install Python.Python.3.12` | comes with the Xcode tools (`xcode-select --install`) | preinstalled |
| ArgyllCMS | [argyllcms.com](https://www.argyllcms.com/) → *MS Windows 64 bit*, unzip to `C:\` | `brew install argyll-cms` | `sudo apt install argyll` / `dnf install argyllcms` / `pacman -S argyllcms` |
| Start | double-click `ProjectorCal.cmd` (*Create desktop shortcut.cmd* adds an icon) | `ProjectorCal.app` (right-click → **Open** the first time) | `./projectorcal.sh` (*install-desktop-entry.sh* adds a menu entry) |
| Your data | `%APPDATA%\ProjectorCal` | `~/Library/Application Support/ProjectorCal` | `~/.local/share/ProjectorCal` |

Put the patches into `assets/` next to the program (`assets/patches/…`) — or just copy the `patches` folder to your
USB stick / Plex. *Windows meter drivers:* the i1Display family / ColorMunki Display / Calibrite need none; Spyder,
i1Pro and older meters need the ArgyllCMS USB driver (argyllcms.com → *Installing on MS Windows*). Windows support
is new: the meter link passes automated tests on Windows, but nobody has calibrated with a real meter on Windows
yet — please [report](https://github.com/khursevich/ProjectorCal/issues) how it goes.

**From source (any system)** — Python 3.8+, standard library only:

```bash
git clone https://github.com/khursevich/ProjectorCal.git
cd ProjectorCal
unzip ProjectorCal-patches-v3.zip -d assets/   # the video patches → assets/patches/
python3 -m projectorcal            # opens the UI in your browser
python3 -m projectorcal --mock     # try it without a meter (simulated projector)
```


## The patches

3840×2160, 24p, HEVC Main 10, limited range; SDR Rec.709, HDR10 PQ / BT.2020 (P3-D65, 1000 nits). Copy the folder to
an **exFAT USB stick** for the projector's media player, or to a **Plex** library of type *Other Videos*. Use the
player you watch movies with and make sure it doesn't alter the picture.

| Files | Content |
|---|---|
| `SDR_01…21` | gray 100 % → 0 % in 5 % steps |
| `SDR_22…27` | red, yellow, green, cyan, blue, magenta at 100 % |
| `SDR_28…51` | the six hues at 25/50/75/100 % saturation (75 % amplitude) |
| `SDR_52`, `SDR_53` | PLUGE (black level) and white clipping (contrast) |
| `HDR_01…21` | HDR10 PQ gray 100 % → 0 % |
| `AUTO/ProjectorCal_AUTO_SDR_v3.mp4`, `…_HDR10_v3.mp4` | the automatic checks (14 / 6.5 min) |
| `AUTO/…_v3_slow.mp4` | the same for slower meters (27 / 13 min) |

## Results you can expect

From a Vidda C3S with a Calibrite Display Plus HL, seven runs from factory Filmmaker mode:
**gray ΔE2000 ≈ 1.0, gamma 2.40 ± 0.02 on every step from 10 % to 95 %, weighted color ΔE ≈ 1.7, contrast ≈ 1350:1.**
Every run, every change and what the program learned: [case study](docs/case-study/vidda-c3s.md).

## Documentation

* [Beginner's guide](docs/GUIDE.md) — what to buy, step by step, what "good" looks like.
* [Supported projectors and meters](docs/SUPPORTED.md) — menu layouts, meter notes, laser-light accuracy.
* [FAQ](docs/FAQ.md) — troubleshooting.
* [How it works](docs/HOW_IT_WORKS.md) — formulas, tolerances, the auto-run analyzer, the planners.
* [Case study: Vidda C3S](docs/case-study/vidda-c3s.md).
* [Development](docs/DEVELOPMENT.md) — architecture, tests, building the app and the patches.

## Privacy

Everything stays on your computer: sessions are JSON files in the data folder (see the table above). The UI server listens on 127.0.0.1 only.

## License

MIT — see [LICENSE](LICENSE). ArgyllCMS is a separate program by Graeme W. Gill (AGPL), used as an external tool.
Vidda, Hisense, Calibrite, X-Rite, Datacolor and the other names are trademarks of their owners; this project is
not affiliated with them.

<sub>Keywords: Vidda projector calibration, Hisense projector calibration, Vidda C3S / C3 Pro / C3 Ultra settings,
Hisense C2 Ultra / C2 Pro / C1 / PX3 Pro calibration, laser projector white balance, 20-point gamma, CMS,
Windows, macOS, Linux, ArgyllCMS spotread, i1Display Pro, Calibrite, ColorMunki, SpyderX, Plex, USB, калибровка проектора Vidda.</sub>
