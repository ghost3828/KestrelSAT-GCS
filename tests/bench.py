"""Micro-benchmarks for the receive and redraw hot paths.

Run:  QT_QPA_PLATFORM=offscreen xvfb-run -a python tests/bench.py
"""
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import kestrelsat.app as sg

if os.path.exists(sg.SETTINGS_FILE):
    os.remove(sg.SETTINGS_FILE)

root = tk.Tk()
app = sg.SerialGUI(root)
app.notebook.select(app.plots[0].frame)
root.update()

# --- 1. serial monitor throughput ------------------------------------------
N_LINES = 10000
lines = [f"Temp:{20 + i % 10}.5,Sine:{i % 50}.2,Noise:{i % 7}.1" for i in range(N_LINES)]
t0 = time.perf_counter()
for i in range(0, N_LINES, 50):        # 50-line chunks, like a real burst
    chunk = ("\n".join(lines[i:i + 50]) + "\n").encode()
    app.display_received_data(chunk)
root.update()
t1 = time.perf_counter()
print(f"receive path : {N_LINES} lines in {t1 - t0:.3f}s "
      f"({N_LINES / (t1 - t0):,.0f} lines/s)")
monitor_lines = int(app.received_text.index('end-1c').split('.')[0])
print(f"               monitor holds {monitor_lines} lines (cap {app.MAX_MONITOR_LINES})")

# --- 2. channel creation (the O(N^2) restyle) ------------------------------
app.plots[0].clear_plot_data()
root.update()
N_CH = 32
t0 = time.perf_counter()
for n in range(2):
    app.plots[0].update_plot_channels({f"Ch{i:02d}": float(i) for i in range(N_CH)}, n)
root.update()
t1 = time.perf_counter()
print(f"add {N_CH} channels: {t1 - t0:.3f}s")

# --- 3. redraw ------------------------------------------------------------
if sg.PYQTGRAPH_AVAILABLE:
    app.plots[0].show_plot_window()
    root.update()
    for n in range(1000):
        app.plots[0].update_plot_channels({f"Ch{i:02d}": float(i + n) for i in range(8)}, n)
    root.update()
    FRAMES = 500
    t0 = time.perf_counter()
    for _ in range(FRAMES):
        app.plots[0].update_plot_display()
    t1 = time.perf_counter()
    per = (t1 - t0) / FRAMES * 1000
    print(f"redraw       : {FRAMES} frames, 8 channels x 1000 samples "
          f"-> {per:.2f} ms/frame ({1000 / per:.0f} fps ceiling)")
else:
    print("redraw       : skipped (pyqtgraph unavailable)")

app.on_closing()
