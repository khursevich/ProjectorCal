# Calibrate your Vidda / Hisense projector: a beginner's guide

No experience with calibration needed. If you can copy a file to a USB stick and change a value in the projector
menu, you can do this. Expect **one evening**: about an hour of warm-up and setup, then 3–6 automatic runs of
15–30 minutes each. You can leave the room during each run.

## Why bother?

Out of the box, even the "Filmmaker" mode of a laser projector is rarely accurate: gray is often slightly green or
magenta, the shadows are too dark or too bright, and some colors are pushed. Calibration fixes this with the
projector's **own** controls. The result: skin tones that look like skin, neutral grays, shadow detail as the
director intended. Nothing is installed on the projector, and you can always switch back to another picture mode.

## What you need

| | | Approx. cost |
|---|---|---|
| **A colorimeter** | Best: Calibrite Display Plus HL / Display Pro, or an X-Rite i1Display Pro (used is fine). Also works: ColorMunki Display, Spyder X / X2 / 5, i1Pro — see [supported meters](SUPPORTED.md#meters-colorimeters-and-spectrometers). Borrowing one works too. | $150–300 (used $80+) |
| **A computer** | Windows 10/11, a Mac or a Linux PC/laptop. It sits near the meter during the runs. | — |
| **ArgyllCMS** | Free software that talks to the meter. | free |
| **ProjectorCal** | This program + the video patches (≈ 1 GB). | free |
| **A way to play videos** | A USB stick for the projector's own media player, or Plex. | — |
| **A tripod or a stack of books** | To hold the meter. Most meters have a tripod thread. | — |
| **A dark room** | Calibrate at night or with blackout curtains. | — |

## Step 1 — Install (10 minutes)

**Windows 10 / 11** *(beta — please report how it works for you)*

1. Install **Python** from [python.org](https://www.python.org/downloads/windows/) (or `winget install Python.Python.3.12`).
2. Download **ArgyllCMS** for *MS Windows 64 bit* from [argyllcms.com](https://www.argyllcms.com/) and unzip it to
   `C:\` — you get e.g. `C:\Argyll_V3.3.0\bin\spotread.exe`. i1Display / ColorMunki Display / Calibrite meters need
   no driver; Spyder and i1Pro need the ArgyllCMS USB driver (see *Installing on MS Windows* on the same site).
3. Download `ProjectorCal-Windows.zip` and `ProjectorCal-patches-v3.zip` from the
   [latest release](https://github.com/khursevich/ProjectorCal/releases/latest) and unzip both.
4. Double-click **`ProjectorCal.cmd`**. Keep its window open while you calibrate.
   *Create desktop shortcut.cmd* puts a ProjectorCal icon on your desktop.

**macOS**

1. Install [Homebrew](https://brew.sh) if you don't have it, then in Terminal: `brew install argyll-cms`.
2. Download `ProjectorCal-macOS.zip` and `ProjectorCal-patches-v3.zip` from the
   [latest release](https://github.com/khursevich/ProjectorCal/releases/latest).
3. Unzip, move `ProjectorCal.app` to Applications, right-click → **Open** the first time (the app is not signed).

**Linux**

```bash
sudo apt install argyll python3          # Fedora: sudo dnf install argyllcms · Arch: sudo pacman -S argyllcms
unzip ProjectorCal-Linux.zip && cd ProjectorCal-Linux
unzip ~/Downloads/ProjectorCal-patches-v3.zip -d assets/
./projectorcal.sh                        # ./install-desktop-entry.sh adds it to the application menu
```

Want to look around first? Start it with `--mock` (`./projectorcal.sh --mock`, or `py -3 run.py --mock` in the
Windows folder): it runs the whole program with a simulated projector and
meter.

## Step 2 — Put the patches on a USB stick (10 minutes)

1. Format a USB stick as **exFAT** (the videos are larger than FAT32 allows).
2. Copy the `patches` folder from the zip onto it.
3. Plug it into the projector and open the folder in the projector's media player once, to check that the files play.

With Plex: put the folder on your Plex server as a library of type **Other Videos**. Use the player you watch
movies with — calibration is only valid for the signal path you measured.

## Step 3 — Prepare the projector (5 minutes + 45 minutes warm-up)

1. Choose the picture mode you want to calibrate — **Filmmaker** (or *Cinema* / *Theater*) is the right start.
2. Turn **off** everything automatic: Dynamic Contrast / Local Contrast, AI picture, motion smoothing (MEMC), light
   sensor / eye care, noise reduction.
3. Set your normal laser level (Brightness) and **do not change it later**.
4. Let the projector run for **45–60 minutes**. Laser brightness drifts while warming up.

## Step 4 — Place the meter (5 minutes)

* Point the meter at the **center of the screen**, 0.7–1.5 m away, slightly **below the light beam** so its shadow
  falls on the bottom edge of the picture, not in the middle.
* Do not put it against the screen (that is for TVs). Do not move it during the session.
* Plug it into the computer directly (not through a hub).
* **Spyder**: let it warm up 10–15 minutes. **Calibrite Display Plus HL**: run Calibrite PROFILER once beforehand.

## Step 5 — Start ProjectorCal (5 minutes)

1. Open ProjectorCal. It opens in your web browser.
2. **Settings** (top right): choose your **projector model** and your **meter model**. The program tells you if
   your meter needs the *slow* video. Check that the **gamma points layout** matches your menu (count the Control
   Points under *Gamma → Adjust*).
3. **Preparation**: press *Check again* — the program finds the meter. Play `SDR/SDR_01` (white) and press
   *Test reading*. Then cover the meter lens and measure the room light: it should be close to zero.

## Step 6 — Enter your current settings (10 minutes, only the first time)

Go to **Full check**, open *Projector settings* and type in what is set in the menu now: picture mode, laser level,
Black Level, Contrast, Color Temperature, Gain/Offset R G B, gamma preset and all gamma points, CMS values. Usually
most are 0. The program compares later runs with this snapshot — it needs to know where you started.

## Step 7 — The automatic run (15–30 minutes, hands off)

1. Press **Start SDR**.
2. On the projector, play the file the program shows (`AUTO/ProjectorCal_AUTO_SDR_v3.mp4`, or the `_slow` one).
3. Don't touch the remote, don't walk in front of the beam, keep the lights off. Go make tea.

The program finds the start mark by itself, assigns every reading to its patch and throws away anything suspicious
(a menu on screen, someone in the beam, a stuttering player).

## Step 8 — Read the verdict and change the menu (10 minutes)

The verdict lists every value to change, for example:

> **Gamma → Control Point:** Point 6: −11 → −3 · Point 7: −10 → −4
> **White Balance:** Offset R: 0 → −1
> **CMS Blue:** Bright: 4 → 5

Set exactly these values in the projector menu, close the menu, and press **"I set all this in the menu → save to
settings"**. Then run the automatic check again (Step 7).

## Step 9 — Repeat until it says "within tolerance"

Usually 3–6 runs. Each run makes the program smarter about *your* projector: it measures how far one step of each
control really moves the picture, so its next suggestion lands closer. When the verdict says everything is within
tolerance, you're done. Print the **report** if you like (top right).

Optional: **HDR10** — press *Start HDR10* and play the HDR video. HDR mostly gets a white-balance check; the
projector's own tone mapping is reported, not changed.

## What "good" looks like

| | Good | What it means |
|---|---|---|
| Gray ΔE2000 | < 1.5 | neutral gray, no color tint you could see |
| Gamma | 2.4 ± 0.05 (dark room) | shadows and mid-tones at the right brightness |
| Color ΔE (weighted) | < 3 | accurate skin, sky, foliage |
| Contrast | as high as the projector allows | set by Black Level, measured for you |

A Vidda C3S reached gray ΔE ≈ 1.0, gamma 2.40 and color ΔE ≈ 1.7 in seven runs —
see the [case study](case-study/vidda-c3s.md).

## Problems?

See the [FAQ](FAQ.md). Still stuck? [Open an issue](https://github.com/khursevich/ProjectorCal/issues) with the
meter log (*Settings → Meter log → Download*).
