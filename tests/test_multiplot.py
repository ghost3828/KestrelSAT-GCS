"""Tests for multiple independent plot tabs.

Run:  QT_QPA_PLATFORM=offscreen xvfb-run -a python tests/test_multiplot.py
"""
import os
import sys
import tkinter as tk
from tkinter import messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import kestrelsat.app as sg

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def tabs(app):
    return [app.notebook.tab(t, "text") for t in app.notebook.tabs()]


if os.path.exists(sg.SETTINGS_FILE):
    os.remove(sg.SETTINGS_FILE)

root = tk.Tk()
app = sg.SerialGUI(root)
root.update()

# --- defaults ---------------------------------------------------------------
print("default layout")
check(tabs(app) == ["Connection", "Notepad", "Camera", "Plot 1", "  +  "],
      "one plot tab plus '+': %s" % tabs(app))
check(len(app.plots) == 1, "exactly one plot to start")
check(app.plots[0].window_title == "Serial Plot 1",
      "window title is 'Serial Plot 1': %s" % app.plots[0].window_title)

# --- adding -----------------------------------------------------------------
print("adding plots")
app.add_plot_tab()
root.update()
check(tabs(app) == ["Connection", "Notepad", "Camera", "Plot 1", "Plot 2", "  +  "],
      "'+' stays rightmost after adding: %s" % tabs(app))
check(app.plots[1].window_title == "Serial Plot 2",
      "second window is 'Serial Plot 2'")

# Selecting '+' adds a tab rather than showing an empty one.
app.notebook.select(app.add_tab_frame)
root.update()
check(len(app.plots) == 3, "selecting '+' created a third plot")
check(app.notebook.tab(app.notebook.select(), "text") == "Plot 3",
      "the new plot is selected, not '+'")

# --- every plot sees every line --------------------------------------------
print("data reaches every plot")
for i in range(5):
    app.display_received_data(f"Temp:{20 + i}.5,Sine:{i}.2\n".encode())
root.update()
for i, p in enumerate(app.plots, 1):
    check(sorted(p.channels) == ["Sine", "Temp"],
          "plot %d picked up both channels" % i)

# --- independence -----------------------------------------------------------
print("independent configuration")
app.plots[1].delimiter = ';'
app.plots[1].plot_max_points = 77
app.plots[1].plot_paused = True
check(app.plots[0].delimiter == ',', "delimiter is per plot")
check(app.plots[0].plot_max_points == 1000, "buffer size is per plot")
check(app.plots[0].plot_paused is False, "pause is per plot")

app.plots[0].channels["Temp"].color = "#123456"
app.plots[0].channels["Temp"].color_is_user = True
check(app.plots[1].channels["Temp"].color != "#123456",
      "channel colour is per plot")

before = len(app.plots[0].channels["Temp"].data)
app.plots[1].clear_plot_data()
root.update()
check(len(app.plots[1].channels) == 0, "cleared plot 2")
check(len(app.plots[0].channels["Temp"].data) == before,
      "clearing one plot leaves the others' data alone")

# Sample numbering is per plot, so a clear does not shift another plot's x-axis.
app.plots[1].sample_counter = 0
app.display_received_data(b"Temp:1.0\n")
root.update()
check(app.plots[0].sample_counter != app.plots[1].sample_counter,
      "sample counters are independent after a clear")

# --- removal ----------------------------------------------------------------
print("removing plots")
messagebox.askyesno = lambda *a, **k: True
notices = []
messagebox.showinfo = lambda t, m, *a, **k: notices.append(m)

app.remove_plot_tab(app.plots[1])
root.update()
check(tabs(app) == ["Connection", "Notepad", "Camera", "Plot 1", "Plot 2", "  +  "],
      "tab removed: %s" % tabs(app))
check([p.window_title for p in app.plots] == ["Serial Plot 1", "Serial Plot 2"],
      "remaining plots renumbered, windows included")

app.remove_plot_tab(app.plots[1])
app.remove_plot_tab(app.plots[0])
root.update()
check(len(app.plots) == 1, "the last plot cannot be removed")
check(any("last plot" in m.lower() for m in notices),
      "removing the last plot explains why it is refused")

app.on_closing()

print()
if failures:
    print("FAILED (%d)" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL MULTI-PLOT TESTS PASS")
