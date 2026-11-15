"""spotread stand-in for Windows: reads keys from the CONSOLE (msvcrt.getwch), exactly like ArgyllCMS does —
a plain stdin pipe cannot press them, only a pseudo console can."""
import msvcrt
import sys

VALUES = [(47.5235, 50.0, 54.4415), (95.1, 100.2, 108.9), (0.01, 0.011, 0.012)]
w = sys.stdout.write
w("Setting up the instrument\n")
i = 0
while True:
    w("\nPlace instrument on spot to be measured,\nand then hit 'r' to set reference, 'k' to do a calibration,\n"
      "Hit ESC or Q to exit, instrument switch or any other key to take a reading: ")
    sys.stdout.flush()
    c = msvcrt.getwch()
    if c in ("q", "Q", "\x1b"):
        break
    x, y, z = VALUES[i % len(VALUES)]
    i += 1
    w("\n\n Result is XYZ: %.6f %.6f %.6f, D50 Lab is 50.0 0.0 0.0\n" % (x, y, z))
    sys.stdout.flush()
