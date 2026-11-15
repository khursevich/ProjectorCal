# FAQ

### Which projectors does this work with?

It was developed and verified on a **Vidda C3S**. Other Vidda / Hisense laser projectors with the same VIDAA Expert
menu (C3 family, C2 family, C1, PX series) are expected to work, and so is any projector or TV with a Gain/Offset
white balance and a CMS. See [Supported projectors and meters](SUPPORTED.md).

### Do I really need a meter? Can't I calibrate by eye or with my phone?

By eye you can set Black Level and Contrast (the program has PLUGE and clipping patterns for that). White balance,
gamma and colors need a meter: our eyes adapt to any white within minutes, and phone cameras process the image.
A used i1Display Pro / ColorMunki Display is the cheapest reliable option; calibration clubs and AV forums often
lend them.

### My meter is not on the list.

If [ArgyllCMS supports it](https://www.argyllcms.com/doc/instruments.html), ProjectorCal can use it. Choose
*Other ArgyllCMS meter* in Settings: the program uses the slow video and flags any patch the meter could not read.

### Which video do I play: standard or slow?

The one the program shows on the *Full check* screen. It follows from **Settings → Meter model**: the i1Display
Pro / Plus family and the Klein K10-A use the standard video; ColorMunki Display, i1Display Studio, Spyder, i1Pro
and other meters use the slow one. You can override it in Settings.

### "Start mark not found"

The program waits for the white block at the start of the video. Check that: the meter looks at the screen (take a
test reading on white), the right file is playing (exactly the name the program shows — the standard and slow
videos have different timing), and no menu or player control bar covers the picture.

### Some patches are flagged ("no readings", "implausible", "single reading")

They are left out of the analysis, never used silently. Typical causes: someone walked through the beam, a light was
switched on, the player stuttered or showed its control bar, or the meter is slower than the video expects (then
choose the slow video). If only one or two patches are flagged, the verdict is still valid.

### The meter is not found

* Plug it directly into the computer, not into a hub; try another port.
* Run `spotread -?` in a terminal: the meter must appear in the `-c` list.
* **Calibrite Display Plus HL / ColorChecker Display**: run Calibrite PROFILER once, then close it.
* **Spyder 4/5**: install the calibration data once with ArgyllCMS `oeminst` (see the ArgyllCMS docs).
* **Linux**: ArgyllCMS may need udev rules to access USB instruments without root; your distribution's `argyll`
  package usually installs them (re-plug the meter after installing).

### Can I use Windows?

Yes — Windows 10 (version 1809 or newer) and Windows 11, with `ProjectorCal-Windows.zip`. It runs `spotread` on the
Windows pseudo console (ConPTY), the same way it uses a terminal on macOS and Linux. Windows support is new (beta):
it passes the simulated tests but has not yet been confirmed on real Windows hardware, so please report how it
works. If the meter link misbehaves, try **Settings → Reading source → spotread -O (one reading per launch)**.

### Windows: "spotread not found" or the meter is not found

* Unzip ArgyllCMS to `C:\` (e.g. `C:\Argyll_V3.3.0`) or set the folder in *Settings → Path to spotread*.
* i1Display Pro / Plus, Calibrite and ColorMunki Display work without a driver. Spyder, i1Pro, ColorMunki Photo and
  older meters need the ArgyllCMS USB driver: argyllcms.com → *Installing on MS Windows*.
* Close other calibration software (Calibrite PROFILER, DisplayCAL, i1Profiler) — only one program can use the
  meter at a time.

### Plex or USB stick?

Use the player you watch movies with. The projector's own media player (USB) is the simplest. With Plex, make sure
the client plays the file directly (no transcoding, no "enhance", no HDR conversion of SDR).

### Does this change anything on the projector?

No. You change the menu values yourself; ProjectorCal only measures and tells you what to set. You can return to the
factory settings at any time by resetting the picture mode.

### How accurate is it?

Gray tracking, gamma and the changes between runs are measured very reliably. Absolute colors depend on the meter:
colorimeters are calibrated for other displays and can be off by a few ΔE on narrow-spectrum laser light. A spectral
correction (`.ccss`) for RGB lasers fixes that — see
[Accuracy on laser projectors](SUPPORTED.md#accuracy-on-laser-projectors).

### Why are mid-saturation colors more important than 100 %?

In films, skin, sky, foliage and interiors are almost never fully saturated. ProjectorCal optimizes the CMS for the
weighted ΔE of 25–75 % saturations first and 100 % second, so the colors you actually watch are the most accurate.

### My verdict suggests a large change. Is that safe?

Changes are limited until the program has learned your projector's step size (for example, at most 2 steps of Gain
the first time). Every change is undone by typing the old value back. The settings snapshot of every run is kept in
the session.

### Where is my data?

Only on your computer: `%APPDATA%\ProjectorCal` (Windows), `~/Library/Application Support/ProjectorCal` (macOS)
or `~/.local/share/ProjectorCal` (Linux).
No accounts, no telemetry, the UI listens on 127.0.0.1 only.

### Can I help?

Yes — calibrate your model and [report it](SUPPORTED.md#help-us-verify-your-model), translate the guide, or improve
the code ([Development](DEVELOPMENT.md)).
