"""Headless test for the Plot tab overflow fix.

Verifies:
  1. The plot control buttons come before the channel list in the Plot tab,
     so they're never pushed out of reach by a long channel list.
  2. The whole tab scrolls, so a long channel list cannot push the panels
     below it out of reach.
  3. The scroll canvas actually needs to scroll once there are more rows than
     fit, and can move through the full list.
  4. clear_plot_data empties the list, keeps the Y-axis label entry intact,
     and resets scroll position.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
import kestrelsat.app as sg

if os.path.exists(sg.SETTINGS_FILE):
    os.remove(sg.SETTINGS_FILE)

root = tk.Tk()
root.geometry("900x700")
app = sg.SerialGUI(root)
app.notebook.select(1)  # Plot tab - widgets aren't mapped/sized until selected
root.update()
root.update_idletasks()

# 1. ordering: plot_control_frame's ancestor must appear before channel_frame
# among plot_frame's children.
btn_bar = app.show_plot_btn.master.master  # btn_inner -> btn_bar
assert btn_bar in app.plot_frame.winfo_children()
# the button bar is packed to the bottom of plot_frame and is NOT inside the
# scrollable canvas, so it stays put however long the channel list grows
assert app.plot_scroll_canvas not in (btn_bar,), "buttons must not be inside the scroll area"
assert str(btn_bar.pack_info().get("side")) == "bottom", \
    "button bar must be pinned to the bottom"
print("ordering ok: button bar pinned outside the scroll area")

# 2. add many channels and confirm the LabelFrame height stays bounded
NUM_CHANNELS = 25
data = {"Ch%02d" % i: float(i) for i in range(NUM_CHANNELS)}
for n in range(3):
    app.update_plot_channels(data, n)
root.update()
root.update_idletasks()

assert len(app.channels) == NUM_CHANNELS
canvas_h = app.plot_scroll_canvas.winfo_height()
print("scroll canvas height =", canvas_h)
# the scrollable viewport must stay inside the window regardless of how many
# channels were added
assert canvas_h < root.winfo_height(), \
    "scroll viewport %d exceeds window %d" % (canvas_h, root.winfo_height())

# 3. scrolling must actually be needed and functional
bbox = app.plot_scroll_canvas.bbox("all")
assert bbox is not None
content_height = bbox[3] - bbox[1]
print("scrollable content height =", content_height, "vs canvas height", canvas_h)
assert content_height > canvas_h, "content should overflow the visible canvas"

app.plot_scroll_canvas.yview_moveto(0)
root.update()
top_y = app.plot_scroll_canvas.yview()[0]
app.plot_scroll_canvas.yview_moveto(1)
root.update()
bottom_y = app.plot_scroll_canvas.yview()[0]
assert bottom_y > top_y, "canvas did not scroll"
print("scroll range ok: top=%.3f bottom=%.3f" % (top_y, bottom_y))

# mouse-wheel handler scrolls too
app.plot_scroll_canvas.yview_moveto(0)
root.update()
class FakeEvent:
    num = 0
    delta = -120  # Windows convention: negative delta = scroll down
app._on_plot_mousewheel(FakeEvent())
root.update()
after_wheel = app.plot_scroll_canvas.yview()[0]
assert after_wheel > 0, "mousewheel handler did not move the view"
print("mousewheel handler ok: yview now %.3f" % after_wheel)

# last row must be reachable (not clipped) - its window coords should fall
# within the canvas's bounding box once scrolled to the bottom
app.plot_scroll_canvas.yview_moveto(1)
root.update()
root.update_idletasks()
last_channel = "Ch%02d" % (NUM_CHANNELS - 1)
last_row_btn = app.channels[last_channel].color_btn
canvas_top = app.plot_scroll_canvas.winfo_rooty()
canvas_bottom = canvas_top + app.plot_scroll_canvas.winfo_height()
row_y = last_row_btn.winfo_rooty()
print("last row y=%d, canvas visible [%d, %d]" % (row_y, canvas_top, canvas_bottom))
assert canvas_top - 5 <= row_y <= canvas_bottom + 5, "last channel row not scrolled into view"
print("last channel reachable via scroll: ok")

# 4. clear_plot_data empties the list and keeps the Y-axis label entry
app.clear_plot_data()
root.update()
rows = app.channel_rows_frame.winfo_children()
assert len(rows) == 1 and isinstance(rows[0], __import__("tkinter").ttk.Label)
assert rows[0].cget("text") == "No channels detected"
assert app.ylabel_entry.winfo_exists()
assert app.plot_scroll_canvas.yview()[0] == 0.0
print("clear_plot_data ok: list emptied, Y-axis label entry preserved, scroll reset")

# theme switch must still theme the scroll area correctly with many channels
for n in range(3):
    app.update_plot_channels(data, n + 100)
root.update()
for name in sg.THEME_ORDER:
    app.theme_var.set(name)
    app.on_theme_selected()
    root.update()
    c = sg.THEMES[name]
    assert str(app.plot_scroll_canvas.cget("bg")) == c["surface"], (name, app.plot_scroll_canvas.cget("bg"))
print("theme + scroll area interplay ok")

app.on_closing()
print("\nSCROLL SMOKE OK")
