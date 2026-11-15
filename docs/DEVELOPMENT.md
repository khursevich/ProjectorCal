# Development

## Architecture

Python 3.8+ using **only the standard library**, a browser UI with no external libraries, and thin launchers per
system — a macOS `.app` (bash), `ProjectorCal.cmd` on Windows, `projectorcal.sh` on Linux. No compilation, no
signing. Everything — the meter link over a pty, the math, the
controller, the auto-run analyzer — is covered by automated tests that run without a meter or a Mac.

```
projectorcal/
  colorimetry.py   xy/u'v', CCT, RGB balance, Lab, ΔE2000, PQ, BT.1886 (1:1 port of reference/pcal.py, no numpy)
  catalog.py       patches.json, patch sets for the wizard steps
  meter.py         ArgyllMeter (spotread on a pty / Windows pseudo console), OneShotMeter (-O fallback),
                   finding/probing spotread on every system
  conpty.py        Windows: ConPTY pseudo console via ctypes, kill-on-close job object, VT escape cleaning
  mock.py          projector + meter simulation (Gain/Offset, 20-point gamma, native gamut, HDR roll-off,
                   realistic reading times, video playback)
  stability.py     stability / patch-change detector
  analysis.py      gray / color / HDR analysis, hints, "is it the right patch", gamma-point model
  sequence.py      auto-run video schedule + the meter reading-time model (single source of truth)
  autorun.py       auto-run analyzer: sync, runs, DP matching, validation
  fulltest.py      run analysis, drift correction, comparison, learning, plan (WB, gamma, CMS), verdict
  psettings.py     structured projector settings (form fields, groups, diff)
  profiles.py      projector profiles (gamma point layout) and meter profiles (auto-run speed, detection, -y list)
  session.py       sessions/settings as JSON, atomic writes, migration of old sessions
  controller.py    meter worker thread, live mode, passes, jobs, full check, wizard navigation
  report.py        printable report (A4, inline SVG)
  server.py        HTTP on 127.0.0.1 only (Host/Origin checks), export, entry point
  probe.py         command-line meter diagnostics: python3 -m projectorcal.probe
  static/          index.html, style.css, app.js
assets/patches/    patches.json, AUTO/sequence_*.json (the .mp4 files are distributed via Releases)
tools/             build_dist.py (Windows / Linux / macOS packages), build_app.py, make_long_patches.py,
                   make_sequence.py
reference/         gen_patches.py (patch generator), pcal.py (original CLI prototype, math reference)
tests/             unit, integration (pty against spotread stubs), auto-run simulation, UI smoke
```

## Running

```bash
python3 -m projectorcal                 # real meter
python3 -m projectorcal --mock          # simulated projector and meter
python3 -m projectorcal --data-dir /tmp/pc --port 8799 --no-browser
python3 -m projectorcal.probe           # meter diagnostics (spread in each integration mode)
```

## Tests

```bash
python3 -m unittest tests.test_core tests.test_meter tests.test_autorun tests.test_controller   # ~8 min
python3 tests/e2e_ui.py [screenshots_dir]   # UI smoke in Chromium, needs the playwright package
```

* `test_core` — colorimetry vectors (ΔE2000 Sharma), stability, hints, gamma-point model, WB/CMS planning,
  drift model, patch plausibility; comparison with `reference/pcal.py` if numpy is installed.
* `test_meter` — the pty link against `fake_spotread` / `fake_spotread_ext`: many readings, calibration dialog, no
  instrument, failed reading, USB disconnect and reconnect, hang + kill, no orphan after `kill -9`. The Windows code
  path runs against the same stubs through `FakeConPty`, which delivers the output the way ConPTY does (VT
  sequences, CRLF, random chunk boundaries). The real ConPTY layer (`conpty.py`) needs a Windows machine to test.
* `test_autorun` — synthetic playback with the realistic reading-time model: normal, late start, Plex stalls, menu
  spikes, stopped video, no video, a bright projector, and 30 randomized harsh runs with zero silent errors.
  It also checks that the built videos in `assets/patches/AUTO` match the schedule.
* `test_controller` — end-to-end on the simulator: wizard passes, restart/resume, wrong patch, white fixing, gamma
  wizard, three full auto runs with learning, HDR run, Argyll stub failures.

When you change the reading-time model or the schedule, also run a larger randomized stress test (e.g. 150 seeds,
with the meter 1.0× and 1.3× slower than the model, and the slow video with a meter 1.6–2.4× slower) — see
`AutoRunRandomized` in `tests/test_autorun.py` (`Sim(..., slow=2.0)`).

## Adding a projector or meter profile

Edit `projectorcal/profiles.py`: a projector needs `id`, `name`, `gpCount` (20, 10 or 0), `verified` and a `note`;
a meter needs `speed` ("std" or "slow") and a `note`, plus a regex in `_DETECT` if `spotread -?` names it
recognizably. Mark a model `verified` only after real runs, and add it to `docs/SUPPORTED.md`.

## Patches

```bash
python3 reference/gen_patches.py out/          # generate the 74 patches (needs numpy + ffmpeg with libx265)
python3 tools/make_long_patches.py out/ assets/patches 10   # loop them to 10 minutes without re-encoding
python3 tools/make_sequence.py assets/patches  # build AUTO/*_v<N>.mp4 + *_v<N>_slow.mp4, verify every segment
```

`sequence.VERSION` is part of the auto-run file name; bump it whenever the schedule changes so an old file can never
be played against a new schedule. `sequence.SPEEDS` defines the speed variants ("std", "slow"); the standard
schedule carries no `speed` key, so adding a variant does not change the existing standard videos. Release the patches as `ProjectorCal-patches-v<N>.zip`.

## Building the downloads

```bash
python3 tools/build_dist.py                # dist/ProjectorCal-Windows.zip, -Linux.zip, -macOS.zip (no patches)
python3 tools/build_dist.py --with-patches # the same with the videos inside each package
python3 tools/build_app.py                 # only dist/ProjectorCal.app (with patches)
```

## Session format

Sessions are readable JSON (`schemaVersion`), written atomically. Each full-check run stores its schedule (`seq`),
raw readings in file time (`raw`), the settings snapshot, the analyzed segments and sync info, so a newer version can
re-analyze it (*Re-analyze the run*). `session.migrate` upgrades older sessions, including those recorded with the
earlier Russian-language UI (flags and menu keys are translated).

## Release checklist

1. All tests pass; stress test without silent errors.
2. `python3 tools/make_sequence.py` if the schedule changed; bump `sequence.VERSION`.
3. Bump `projectorcal/__init__.py` `__version__`.
4. `python3 tools/build_dist.py` → `ProjectorCal-Windows.zip`, `ProjectorCal-Linux.zip`, `ProjectorCal-macOS.zip`.
5. Zip `assets/patches` (SDR, HDR10, AUTO, patches.json) as `ProjectorCal-patches-v<N>.zip`; also zip only
   `AUTO/*_slow.mp4` + `AUTO/sequence_*_slow.json` as `ProjectorCal-auto-slow-v<N>.zip` for people who already have
   the patches.
6. Create a GitHub release with all the files.
