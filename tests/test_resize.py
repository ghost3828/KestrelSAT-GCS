"""Tests that no part of the GUI becomes unreachable when the window resizes.

Run:  QT_QPA_PLATFORM=offscreen xvfb-run -a python tests/test_resize.py

Two headless caveats are baked into the assertions here:

* xvfb runs without a window manager, so `wm minsize` is NOT enforced against a
  programmatic root.geometry(). Nothing below shrinks the window and expects Tk
  to refuse - the minimum is asserted through root.minsize() and the pure clamp
  helpers instead.
* winfo_ismapped() stays True for widgets scrolled out of a canvas viewport,
  because canvas items do not unmap their children. Inside a scroll region
  reachability is asserted with winfo_rootx() coordinates; ismapped is only
  meaningful for the pack-starved cases on the Connection tab.
"""
import os
import sys
import tkinter as tk
from tkinter import messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import kestrelsat.app as sg
from kestrelsat import scaling
from kestrelsat.widgets import ScrollableFrame, _WheelRouter

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


if os.path.exists(sg.SETTINGS_FILE):
    os.remove(sg.SETTINGS_FILE)

root = tk.Tk()
app = sg.SerialGUI(root)
root.update_idletasks()
root.update()

# --- pure helpers, no display needed ---------------------------------------
print("geometry helpers")
check(scaling.clamp_size((1200, 1170), (1920, 1040)) == (1200, 1040),
      "clamp_size trims height to the work area")
check(scaling.clamp_size((600, 400), (1920, 1040)) == (600, 400),
      "a size that already fits passes through")
check(scaling.parse_geometry("1200x1170+40+20") == (1200, 1170, 40, 20),
      "parse_geometry reads WxH+x+y")
check(scaling.parse_geometry("800x780-10-10") == (800, 780, -10, -10),
      "parse_geometry reads negative offsets")
check(scaling.parse_geometry("nonsense")[0] is None,
      "garbage geometry parses to None")

# --- structural invariants --------------------------------------------------
print("pack ordering")
check(app.control_frame.pack_info().get("side") == "bottom",
      "Serial Monitor Controls is pinned to the bottom")
check(app.send_frame.pack_info().get("side") == "bottom",
      "Send Data is pinned to the bottom")
data_slaves = app.data_frame.pack_slaves()
check(data_slaves.index(app.send_frame) < data_slaves.index(app.recv_frame),
      "Send Data is allocated before the expanding monitor")
root_slaves = root.pack_slaves()
check(root_slaves.index(app.status_bar) < root_slaves.index(app.notebook),
      "status bar still reserved before the notebook (regression guard)")
check(app.status_bar.pack_info().get("side") == "bottom",
      "status bar still packed to the bottom")

# --- the measured failure: must-see rows survive a short window --------------
print("Connection tab stays usable when short")
app.notebook.select(app.connection_frame)
root.update()
for h in (780, 700, 620, 560, 480, 400, 340):
    root.geometry("820x%d" % h)
    root.update_idletasks()
    root.update()
    check(app.send_frame.winfo_ismapped() and app.send_frame.winfo_height() > 5,
          "Send Data visible at %dpx tall (h=%d)" % (h, app.send_frame.winfo_height()))
    check(app.control_frame.winfo_ismapped() and app.control_frame.winfo_height() > 10,
          "Serial Monitor Controls visible at %dpx tall (h=%d)"
          % (h, app.control_frame.winfo_height()))
    check(app.send_btn.winfo_ismapped() and app.send_btn.winfo_height() > 1,
          "Send button visible at %dpx tall" % h)
    check(app.clear_btn.winfo_ismapped() and app.clear_btn.winfo_height() > 1,
          "Clear Display button visible at %dpx tall" % h)

root.geometry("820x780")
root.update_idletasks()
root.update()

# --- minimum size -----------------------------------------------------------
print("minimum window size")
mw, mh = root.minsize()
aw, ah = scaling.work_area(root)
check(mw > 0 and mh > 0, "a minimum size is set: %dx%d" % (mw, mh))
check(mw <= aw and mh <= ah,
      "minimum never exceeds the work area (%dx%d vs %dx%d)" % (mw, mh, aw, ah))
gw, gh, _, _ = scaling.parse_geometry(root.geometry())
check(gw is not None and gw <= aw and gh <= ah,
      "launch geometry fits the work area: %s" % root.geometry())

# --- plot tab: horizontal reachability --------------------------------------
print("plot tab scrolls horizontally")
plot = app.plots[0]
app.notebook.select(plot.frame)
for n in range(3):
    plot.update_plot_channels({"Ch%02d" % i: float(i) for i in range(5)}, n)
root.update_idletasks()
root.update()
canvas = plot.plot_scroll_canvas

root.geometry("420x700")
root.update_idletasks()
root.update()
x0, _, x1, _ = canvas.bbox("all")
check(x1 - x0 > canvas.winfo_width(),
      "content is wider than the viewport (%d vs %d) so there is something to scroll"
      % (x1 - x0, canvas.winfo_width()))
check(canvas.xview() != (0.0, 1.0), "the x-scrollbar is actually engaged")


def reachable(widget):
    """Scroll the widget into view, then check it really is on screen."""
    left0, _, right0, _ = canvas.bbox("all")
    total = right0 - left0
    wx = widget.winfo_rootx() - canvas.winfo_rootx() + canvas.canvasx(0)
    canvas.xview_moveto(max(0.0, min(1.0, (wx - 20) / total)))
    root.update_idletasks()
    root.update()
    lo = canvas.winfo_rootx()
    hi = lo + canvas.winfo_width()
    return lo - 2 <= widget.winfo_rootx() <= hi + 2


check(reachable(plot.channels["Ch04"].line_checkbox),
      "'Show Line' - the first control clipped on a narrow window - is reachable")
canvas.xview_moveto(0)
root.update()

# --- wheel routing ----------------------------------------------------------
print("wheel routing")
router = _WheelRouter.for_root(root)
check(len(router.regions) >= 2,
      "both the plot and connection scroll regions are registered (%d)"
      % len(router.regions))


class FakeEvent:
    num = 0
    delta = -120
    state = 0
    x_root = -1          # forces the fallback to .widget
    y_root = -1
    widget = None


canvas.yview_moveto(0)
root.update()
ev = FakeEvent()
ev.widget = app.received_text
router._dispatch(ev)
root.update()
check(canvas.yview()[0] == 0.0,
      "a wheel over the serial monitor does not also scroll the region around it")

ev.widget = plot.channels["Ch00"].color_btn
router._dispatch(ev)
root.update()
check(canvas.yview()[0] > 0, "a wheel over plot content scrolls the plot region")

before = len(router.regions)
before_plots = len(app.plots)
extra = app.add_plot_tab()
root.update()
messagebox.askyesno = lambda *a, **k: True
app.remove_plot_tab(extra)
root.update()
check(len(router.regions) == before,
      "removing a plot tab unregisters exactly its own region (%d -> %d)"
      % (before + 1, len(router.regions)))
check(len(app.plots) == before_plots,
      "removing a plot tab does not silently re-create one")
check(app.notebook.tab(app.notebook.select(), "text") != "  +  ",
      "the '+' tab is never left selected after a removal")
router._dispatch(ev)          # must not raise on a stale target

# a destroyed child must NOT unregister its parent region
before = len(router.regions)
probe = tk.Label(plot._scroll.inner, text="probe")
probe.pack()
root.update()
probe.destroy()
root.update()
check(len(router.regions) == before,
      "a destroyed child widget leaves its region registered")

# --- the plot tab's own vertical scrolling still works -----------------------
print("plot tab still scrolls vertically")
root.geometry("820x600")
root.update_idletasks()
root.update()
for n in range(2):
    plot.update_plot_channels({"Ch%02d" % i: float(i) for i in range(25)}, n)
root.update_idletasks()
root.update()
_, y0, _, y1 = canvas.bbox("all")
check(y1 - y0 > canvas.winfo_height(),
      "content overflows vertically (%d vs %d)" % (y1 - y0, canvas.winfo_height()))
check(canvas.yview()[1] < 1.0, "the y-scrollbar is engaged")

# --- ScrollableFrame does not storm on <Configure> --------------------------
print("scroll region does not loop on <Configure>")
probe_win = tk.Toplevel(root)
probe_win.geometry("400x300")
sf = ScrollableFrame(probe_win, vscroll=True, hscroll=True)
sf.pack(fill=tk.BOTH, expand=True)
for i in range(20):
    tk.Label(sf.inner, text=("row %d " % i) + "x" * 120).pack(anchor="w")
calls = [0]
_orig = sf._sync


def counting(event=None):
    calls[0] += 1
    return _orig(event)


sf._sync = counting
sf.inner.bind("<Configure>", sf._sync)
sf.canvas.bind("<Configure>", sf._sync)
root.update_idletasks()
root.update()
for w in (400, 600, 350, 500):
    probe_win.geometry("%dx300" % w)
    root.update_idletasks()
    root.update()
check(calls[0] < 200,
      "no <Configure> feedback loop: %d syncs across 4 resizes" % calls[0])
probe_win.destroy()
root.update()

# --- dialogs ----------------------------------------------------------------
print("dialogs are dismissible and on-screen")
for name, opener in (("Preferences", app.show_preferences),
                     ("Serial Configuration", app.show_serial_config),
                     ("About", app.show_about),
                     ("User Guide", app.show_user_guide)):
    opener()
    root.update_idletasks()
    root.update()
    dlg = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    check(bool(dlg.bind("<Escape>")), "%s binds Escape" % name)
    check(dlg.wm_resizable() == (1, 1), "%s is resizable" % name)
    dw, dh, dx, dy = scaling.parse_geometry(dlg.geometry())
    check(dw is not None and dw <= aw and dh <= ah and dx >= 0 and dy >= 0,
          "%s opens on-screen: %s" % (name, dlg.geometry()))
    dlg.grab_release()
    # focus_force first: without a window manager a bare event_generate is not
    # delivered to the toplevel, which says nothing about the binding itself.
    dlg.focus_force()
    root.update()
    dlg.event_generate("<Escape>")
    root.update()
    check(not dlg.winfo_exists(), "%s closes on Escape" % name)

# the transfer dialog scales its progress bar and cancels on Escape
app._show_transfer_dialog("send")
root.update()
check(int(app._transfer_bar.cget("length")) == scaling.px(app.ui_scale, 320),
      "transfer progress bar is scaled, not a fixed 320px (%s)"
      % app._transfer_bar.cget("length"))
check(bool(app._transfer_dialog.bind("<Escape>")), "transfer dialog binds Escape")
app._close_transfer_dialog()
root.update()

app.notes_dirty = False
app.on_closing()

print()
if failures:
    print("FAILED (%d)" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL RESIZE TESTS PASS")
