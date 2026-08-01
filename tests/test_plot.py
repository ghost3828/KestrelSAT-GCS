"""Headless smoke test for the pyqtgraph side of theming."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tkinter as tk
import kestrelsat.app as sg

assert sg.PYQTGRAPH_AVAILABLE, "pyqtgraph must be importable for this test"
import pyqtgraph as pg

if os.path.exists(sg.SETTINGS_FILE):
    os.remove(sg.SETTINGS_FILE)

root = tk.Tk()
app = sg.SerialGUI(root)
root.update()

for n in range(5):
    app.update_plot_channels({"Temp": 20.0 + n, "Sine": n * 0.5}, n)
root.update()

# 1. open the plot window while dark is active
app.show_plot_window()
root.update()
assert app.plot_widget is not None
assert app.plot_legend is not None
print("plot window opened; curves =", sorted(app.channels))


def check(theme_name):
    c = sg.THEMES[theme_name]
    bg = app.plot_widget.backgroundBrush().color().name().lower()
    assert bg == c["plot_bg"].lower(), (theme_name, bg, c["plot_bg"])
    for axis_name in ("left", "bottom"):
        ax = app.plot_widget.getAxis(axis_name)
        assert ax.pen().color().name().lower() == c["plot_axis"].lower(), \
            (theme_name, axis_name, ax.pen().color().name())
        assert ax.textPen().color().name().lower() == c["plot_fg"].lower()
    for name, ch in app.channels.items():
        curve = ch.curve
        want = ch.color
        got = curve.opts["pen"].color().name().lower()
        assert got == want.lower(), (theme_name, name, got, want)
    print("  %-14s plot ok (bg=%s, axis=%s)" % (theme_name, bg, c["plot_axis"]))


check("dark")

# 2. live switch with the window open
for name in ("light", "high_contrast", "dark"):
    app.theme_var.set(name)
    app.on_theme_selected()
    root.update()
    check(name)

# 3. user-picked colour must survive
app.channels["Sine"].color = "#123456"
app.channels["Sine"].color_is_user = True
app._repen_all_curves()
app.theme_var.set("light")
app.on_theme_selected()
root.update()
assert app.channels["Sine"].color == "#123456"
assert app.channels["Sine"].curve.opts["pen"].color().name().lower() == "#123456"
print("user colour preserved across switch")

# 4. close the window, switch theme, reopen -> must come back themed
app.plot_window.close()
root.update()
assert app.plot_widget is None, "closeEvent should null plot_widget"
app.theme_var.set("high_contrast")
app.on_theme_selected()
root.update()
app.show_plot_window()
root.update()
check("high_contrast")
print("reopened window is themed")

# 5. labels/title carry the theme colour
app.update_plot_title()
app.update_x_axis_label()
app.update_y_axis_label()
root.update()

# 6. clear + rebuild while a theme is active
app.clear_plot_data()
root.update()
for n in range(3):
    app.update_plot_channels({"Alpha": n}, n)
root.update()
assert app.channels["Alpha"].color == sg.THEMES["high_contrast"]["plot_palette"][0]
print("clear + re-detect ok:", {k: v.color for k, v in app.channels.items()})

app.on_closing()
print("\nPLOT SMOKE OK")
