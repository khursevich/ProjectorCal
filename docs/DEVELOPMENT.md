# Development

## Architecture

Python 3.8+ using **only the standard library**, a browser UI with no external libraries, and a thin macOS `.app`
wrapper (a bash launcher — no compilation, no signing). Everything — the meter link over a pty, the math, the
controller, the auto-run analyzer — is covered by automated tests that run without a meter or a Mac.

```
projectorcal/
  colorimetry.py   xy/u'v', CCT, RGB balance, Lab, ΔE2000, PQ, BT.1886 (1:1 port of reference/pcal.py, no numpy)
  catalog.py       patches.json, patch sets for the wizard steps
  meter.py         ArgyllMeter (spotread on a pty), OneShotMeter (-O fallback), finding/probing spotread
  mock.py          projector + meter simulation (Gain/Offset, 20-point gamma, native gamut, HDR roll-off,
                   realistic reading times, video playback)
  stability.py     stability / patch-change detector
  analysis.py      gray / color / HDR analysis, hints, "is it the right patch", gamma-point model
  sequence.py      auto-run video schedule + the meter reading-time model (single source of truth)
  autorun.py       auto-run analyzer: sync, runs, DP matching, validation
  fulltest.py      run analysis, drift correction, comparison, learning, plan (WB, gamma, CMS), verdict
  psettings.py     structured projector settings (form fields, groups, diff)
  session.py       sessions/settings as JSON, atomic writes, migration of old sessions
  controller.py    meter worker thread, live mode, passes, jobs, full check, wizard navigation
  report.py        printable report (A4, inline SVG)
  server.py        HTTP on 127.0.0.1 only (Host/Origin checks), export, entry point
  probe.py         command-line meter diagnostics: python3 -m projectorcal.probe
  static/          index.html, style.css, app.js
assets/patches/    patches.json, AUTO/sequence_*.json (the .mp4 files are distributed via Releases)
tools/             build_app.py, make_long_patches.py, make_sequence.py
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
  instrument, failed reading, USB disconnect and reconnect, hang + kill, no orphan after `kill -9`.
* `test_autorun` — synthetic playback with the realistic reading-time model: normal, late start, Plex stalls, menu
  spikes, stopped video, no video, a bright projector, and 30 randomized harsh runs with zero silent errors.
  It also checks that the built videos in `assets/patches/AUTO` match the schedule.
* `test_controller` — end-to-end on the simulator: wizard passes, restart/resume, wrong patch, white fixing, gamma
  wizard, three full auto runs with learning, HDR run, Argyll stub failures.

When you change the reading-time model or the schedule, also run a larger randomized stress test (e.g. 150 seeds,
with the meter 1.0× and 1.3× slower than the model) — see `AutoRunRandomized` in `tests/test_autorun.py`.

## Patches

```bash
python3 reference/gen_patches.py out/          # generate the 74 patches (needs numpy + ffmpeg with libx265)
python3 tools/make_long_patches.py out/ assets/patches 10   # loop them to 10 minutes without re-encoding
python3 tools/make_sequence.py assets/patches  # build AUTO/*_v<N>.mp4 and verify every segment
```

`sequence.VERSION` is part of the auto-run file name; bump it whenever the schedule changes so an old file can never
be played against a new schedule. Release the patches as `ProjectorCal-patches-v<N>.zip`.

## Building the app

```bash
python3 tools/build_app.py               # dist/ProjectorCal.app with patches (~0.9 GB)
python3 tools/build_app.py --no-patches  # small app; patches downloaded separately
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
4. `python3 tools/build_app.py --no-patches`, zip `dist/ProjectorCal.app` as `ProjectorCal-macOS.zip`.
5. Zip `assets/patches` (SDR, HDR10, AUTO, patches.json) as `ProjectorCal-patches-v<N>.zip`.
6. Create a GitHub release with both files.
