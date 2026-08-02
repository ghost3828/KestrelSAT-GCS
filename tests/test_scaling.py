"""Tests for high-DPI display scaling.

Run:  QT_QPA_PLATFORM=offscreen xvfb-run -a python tests/test_scaling.py
"""
import json
import os
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import kestrelsat.app as sg
from kestrelsat import scaling

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def fresh(settings=None):
    if settings is None:
        if os.path.exists(sg.SETTINGS_FILE):
            os.remove(sg.SETTINGS_FILE)
    else:
        with open(sg.SETTINGS_FILE, "w") as fh:
            json.dump(settings, fh)
    root = tk.Tk()
    app = sg.SerialGUI(root)
    root.update()
    return root, app


# --- pure helpers (no display needed) --------------------------------------
print("scale resolution")
check(scaling._clamp(1.4993) == 1.5, "clamp rounds to the nearest 5%%: %s" % scaling._clamp(1.4993))
check(scaling._clamp(0.5) == scaling.MIN_SCALE, "never scales below 100%")
check(scaling._clamp(9.0) == scaling.MAX_SCALE, "clamped at the maximum")
check(scaling.px(1.5, 16) == 24, "px() scales pixel dimensions")
check(scaling.scale_geometry("800x780", 1.5) == "1200x1170", "geometry scales")
check(scaling.scale_geometry("220x150+40+40", 2.0) == "440x300+40+40",
      "geometry scales size but not offset")
check(scaling.scale_geometry("nonsense", 2.0) == "nonsense", "bad geometry passes through")

# --- detection --------------------------------------------------------------
print("display detection")
root = tk.Tk()
detected = scaling.detect_scale(root)
check(scaling.MIN_SCALE <= detected <= scaling.MAX_SCALE,
      "detect_scale in range: %.2f (this display reports %.0f DPI, %dpx wide)"
      % (detected, root.winfo_fpixels("1i"), root.winfo_screenwidth()))
check(scaling.resolve("1.75", root) == 1.75, "explicit setting is honoured")
check(scaling.resolve("auto", root) == detected, "'auto' resolves to the detected scale")
check(scaling.resolve("not-a-number", root) == detected, "garbage falls back to auto")
check(scaling.resolve(None, root) == detected, "missing setting falls back to auto")
root.destroy()


# --- the fonts actually get bigger -----------------------------------------
print("fonts scale")
base_settings = {"theme": "dark", "ui_scale": "1.0"}
root, app = fresh(base_settings)
small_default = tkfont.nametofont("TkDefaultFont", root=root).cget("size")
small_label_h = app.conn_status_label.winfo_reqheight()
small_btn_w = app.show_plot_btn.winfo_reqwidth()
small_geom = root.geometry().split("+")[0]
app.on_closing()

root, app = fresh({"theme": "dark", "ui_scale": "2.0"})
big_default = tkfont.nametofont("TkDefaultFont", root=root).cget("size")
big_label_h = app.conn_status_label.winfo_reqheight()
big_btn_w = app.show_plot_btn.winfo_reqwidth()
big_geom = root.geometry().split("+")[0]

check(abs(big_default) > abs(small_default),
      "TkDefaultFont grew: %s -> %s" % (small_default, big_default))
check(big_label_h > small_label_h,
      "bold status label grew: %dpx -> %dpx (this is the derived font)"
      % (small_label_h, big_label_h))
check(big_btn_w > small_btn_w,
      "buttons grew: %dpx -> %dpx" % (small_btn_w, big_btn_w))
check(big_geom != small_geom,
      "window geometry scaled: %s -> %s" % (small_geom, big_geom))
check(app.status_canvas.winfo_reqwidth() > 16,
      "status indicator scaled: %dpx" % app.status_canvas.winfo_reqwidth())
app.on_closing()

# --- baseline does not compound across repeated applications ---------------
print("repeated scaling does not compound")
root = tk.Tk()
scaling.apply_fonts(root, 1.0)
one = tkfont.nametofont("TkDefaultFont", root=root).cget("size")
scaling.apply_fonts(root, 2.0)
two = tkfont.nametofont("TkDefaultFont", root=root).cget("size")
scaling.apply_fonts(root, 2.0)
two_again = tkfont.nametofont("TkDefaultFont", root=root).cget("size")
scaling.apply_fonts(root, 1.0)
back = tkfont.nametofont("TkDefaultFont", root=root).cget("size")
check(two_again == two, "re-applying the same factor is idempotent (%s == %s)" % (two, two_again))
check(back == one, "returning to 1.0 restores the original size (%s == %s)" % (one, back))
root.destroy()

# --- the setting round-trips and applies live ------------------------------
print("preference round-trip")
root, app = fresh({"theme": "dark", "ui_scale": "1.0"})
before = app.conn_status_label.winfo_reqheight()
app._scale_labels = {label: value for value, label in scaling.SCALE_CHOICES}
app.scale_var = tk.StringVar(value="200%")
app.on_ui_scale_selected()
root.update()
after = app.conn_status_label.winfo_reqheight()
check(after > before, "live switch enlarged the derived font: %dpx -> %dpx" % (before, after))
check(json.load(open(sg.SETTINGS_FILE))["ui_scale"] == "2.0", "setting persisted")
app.on_closing()

root, app = fresh()  # no settings file at all
check(app.ui_scale_setting == "auto", "defaults to auto on a fresh profile")
check(app.ui_scale == scaling.detect_scale(app.root), "auto uses the detected scale")
app.on_closing()

print()
if failures:
    print("FAILED (%d)" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL SCALING TESTS PASS")
