"""Run every test file in this directory, each in its own process.

Each suite builds and tears down Tk roots, so they are kept isolated.

    python3 tests/run_all.py
    QT_QPA_PLATFORM=offscreen xvfb-run -a python3 tests/run_all.py   # Linux
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = [
    "test_themes.py",
    "test_correctness.py",
    "test_scaling.py",
    "test_multiplot.py",
    "test_resize.py",
    "test_zmodem.py",
    "test_arducam.py",
    "test_camera.py",
    "test_smoke.py",
    "test_plot.py",
    "test_channel_scroll.py",
]

failed = []
for name in SUITES:
    print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
    result = subprocess.run([sys.executable, os.path.join(HERE, name)])
    if result.returncode != 0:
        failed.append(name)

print(f"\n{'=' * 60}")
if failed:
    print("FAILED: " + ", ".join(failed))
    sys.exit(1)
print(f"All {len(SUITES)} suites passed.")
