"""Regression tests for the correctness fixes.

Every test here fails on the code as it stood before the fixes.

Run:  QT_QPA_PLATFORM=offscreen xvfb-run -a python tests/test_correctness.py
"""
import os
import sys
import time
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import kestrelsat.app as sg

failures = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        failures.append(msg)


def make_app():
    if os.path.exists(sg.SETTINGS_FILE):
        os.remove(sg.SETTINGS_FILE)
    root = tk.Tk()
    app = sg.SerialGUI(root)
    root.update()
    return root, app


# --- T0.1: TEST MODE emitted a literal backslash-n -------------------------
# Drive the real test_mode_loop rather than a hand-built payload: the bug was
# in how that loop terminated its line, so feeding our own "\n" would not
# exercise it.
print("T0.1 TEST MODE actually produces plot channels")
root, app = make_app()
app.port_var.set("TEST MODE")
app.connect()
# A real mainloop, not a root.update() spin loop: the worker marshals through
# root.after(), which is a *blocking* cross-thread call into Tcl and is only
# released by the actual event loop. (That coupling is finding T1.1.)
root.after(2500, root.quit)
root.mainloop()
check(sorted(app.plots[0].channels) == ["SENSOR_A", "SENSOR_B", "TIME"],
      "TEST MODE created channels: %s" % sorted(app.plots[0].channels))
check(len(app.serial_buffer) < 200,
      "serial_buffer is drained, not accumulating (len=%d)" % len(app.serial_buffer))
app.on_closing()

# --- T0.2: the Tab delimiter was a literal backslash-t ---------------------
print("T0.2 tab delimiter splits real tabs")
root, app = make_app()
app.plots[0].delimiter_var.set("tab")
app.plots[0].update_delimiter()
check(app.plots[0].delimiter == "\t", "delimiter is a real tab, not %r" % app.plots[0].delimiter)
app.display_received_data(b"A:1\tB:2\tC:3\n")
app.display_received_data(b"A:4\tB:5\tC:6\n")
root.update()
check(sorted(app.plots[0].channels) == ["A", "B", "C"],
      "tab-separated line split into channels: %s" % sorted(app.plots[0].channels))
app.on_closing()

# --- T0.2/A2: buffer cap ---------------------------------------------------
print("A2 serial buffer is capped")
root, app = make_app()
chunk = b"x" * 65536
for _ in range(32):  # 2 MiB, no newline anywhere
    app.display_received_data(chunk)
root.update()
check(len(app.serial_buffer) <= app.MAX_SERIAL_BUFFER,
      "buffer capped at %d (is %d)" % (app.MAX_SERIAL_BUFFER, len(app.serial_buffer)))
app.on_closing()

# --- T1.2: disconnect survives a close() that raises -----------------------
print("T1.2 disconnect survives a failing close()")
root, app = make_app()


class ExplodingPort:
    is_open = True
    in_waiting = 0

    def close(self):
        raise OSError("device disappeared")

    def read(self, n):
        return b""


app.serial_connection = ExplodingPort()
app.is_connected = True
try:
    app.disconnect()
    check(True, "disconnect() did not propagate the exception")
except Exception as exc:
    check(False, "disconnect() raised %r" % exc)
check(app.serial_connection is None, "serial_connection cleared despite close() failure")
check(app.is_connected is False, "is_connected cleared")

# and on_closing must still be able to destroy the root
app.serial_connection = ExplodingPort()
app.is_connected = True
try:
    app.on_closing()
    check(True, "on_closing() completed with a failing port")
except Exception as exc:
    check(False, "on_closing() raised %r" % exc)

# --- T1.5: timers are cancelled, on_closing is re-entrant safe -------------
print("T1.5 timers cancelled on close")
root, app = make_app()
check(app._sps_timer is not None, "SPS timer registered")
check(app._filesize_timer is not None, "file-size timer registered")
app.on_closing()
check(app._sps_timer is None and app._filesize_timer is None, "timers cleared")
try:
    app.on_closing()  # second call, e.g. Ctrl+Q after the window manager close
    check(True, "second on_closing() is a no-op, not a TclError")
except Exception as exc:
    check(False, "second on_closing() raised %r" % exc)

# --- T1.6: tooltips on non-entry widgets -----------------------------------
print("T1.6 tooltip on a Button does not raise")
root, app = make_app()
btn = ttk.Button(root, text="x")
btn.pack()
root.update()
tip = sg.ToolTip(btn, "hello")
try:
    tip.on_enter()
    check(tip.tooltip_window is not None, "tooltip window created for a Button")
    tip.on_leave()
except tk.TclError as exc:
    check(False, "ToolTip.on_enter raised TclError: %s" % exc)
app.on_closing()

# --- T1.9: channel count is capped -----------------------------------------
print("T1.9 channel explosion is capped")
root, app = make_app()
huge = ",".join(str(i) for i in range(5000)) + "\n"
app.display_received_data(huge.encode())
app.display_received_data(huge.encode())
root.update()
check(len(app.plots[0].channels) <= app.MAX_CHANNELS,
      "channels capped at %d (is %d)" % (app.MAX_CHANNELS, len(app.plots[0].channels)))
app.on_closing()

# --- T3.3: per-channel attributes are released on clear --------------------
print("T3.3 no attribute growth across add/clear cycles")
root, app = make_app()


def channel_attrs():
    """Anything still hanging off the app that should have gone with a clear."""
    return [a for a in vars(app)
            if a.startswith(("channel_var_", "name_var_", "thickness_var_",
                             "dot_size_var_", "line_var_", "color_btn_"))]


for cycle in range(5):
    for n in range(3):
        app.plots[0].update_plot_channels({"Ch%d_%d" % (cycle, i): float(i) for i in range(4)}, n)
    root.update()
    app.plots[0].clear_plot_data()
    root.update()
check(channel_attrs() == [], "no leaked per-channel attributes (found %d)" % len(channel_attrs()))
check(app.plots[0].channels == {}, "channel registry empty after clear (%d left)" % len(app.plots[0].channels))
app.on_closing()

# --- T3.2: legend consistency, fixed by the shared helpers -----------------
if sg.PYQTGRAPH_AVAILABLE:
    print("T3.2 legend stays consistent")
    root, app = make_app()
    for n in range(3):
        app.plots[0].update_plot_channels({"A": 1.0 + n, "B": 2.0 + n, "C": 3.0 + n}, n)
    root.update()
    app.plots[0].show_plot_window()
    root.update()

    def legend_names():
        return sorted(lbl.text for _s, lbl in app.plots[0].plot_legend.items)

    check(legend_names() == ["A", "B", "C"], "all channels in legend: %s" % legend_names())

    # Hide B, then rename A. Renaming used to rebuild the legend without the
    # visibility filter, so B reappeared.
    app.plots[0].toggle_channel_visibility("B", False)
    root.update()
    check("B" not in legend_names(), "hidden channel dropped from legend: %s" % legend_names())

    app.plots[0].update_channel_name("A", "Alpha")
    root.update()
    check("B" not in legend_names(),
          "hidden channel still absent after renaming another: %s" % legend_names())
    check("Alpha" in legend_names(), "rename reflected in legend: %s" % legend_names())

    # clear_plot_data used to rebuild a bare, unthemed LegendItem
    app.theme_var.set("dark")
    app.on_theme_selected()
    root.update()
    app.plots[0].clear_plot_data()
    root.update()
    # normalise: the constructor stores whatever it was given (a str here),
    # while setLabelTextColor() stores a QColor
    import pyqtgraph as _pg
    expected = _pg.mkColor(sg.THEMES["dark"]["plot_fg"]).name().lower()
    actual = _pg.mkColor(app.plots[0].plot_legend.opts["labelTextColor"]).name().lower()
    check(actual == expected,
          "legend themed after clear_plot_data (%s vs %s)" % (actual, expected))
    app.on_closing()

print()
if failures:
    print("FAILED (%d)" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL CORRECTNESS TESTS PASS")
