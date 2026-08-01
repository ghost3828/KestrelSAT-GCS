"""Headless test for the channel-list overflow fix.

Verifies:
  1. The plot control buttons come before the channel list in the Plot tab,
     so they're never pushed out of reach by a long channel list.
  2. With many channels, the LabelFrame ("Set Y-Axis") stays bounded in
     height rather than growing without limit.
  3. The channel scroll canvas actually needs to scroll once there are more
     rows than fit, and the scrollbar/canvas can move through the full list.
  4. clear_plot_data empties the list, keeps the Y-axis label entry intact,
     and resets scroll position.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
import serial_gui as sg

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
plot_frame_children = app.plot_frame.winfo_children()
show_btn_ancestor = app.show_plot_btn.master.master  # plot_control_frame -> plot_container
assert show_btn_ancestor in plot_frame_children
assert app.channel_frame in plot_frame_children
assert plot_frame_children.index(show_btn_ancestor) < plot_frame_children.index(app.channel_frame), \
    "plot controls must come before the channel list"
print("ordering ok: plot controls before channel list")

# 2. add many channels and confirm the LabelFrame height stays bounded
NUM_CHANNELS = 25
data = {"Ch%02d" % i: float(i) for i in range(NUM_CHANNELS)}
for n in range(3):
    app.update_plot_channels(data, n)
root.update()
root.update_idletasks()

assert len(app.channels) == NUM_CHANNELS
frame_h = app.channel_frame.winfo_height()
canvas_h = app.channel_canvas.winfo_height()
print("channel_frame height =", frame_h, " channel_canvas height =", canvas_h)
# the label frame should be roughly: canvas height + ylabel row + padding,
# NOT proportional to the number of channels (25 rows would be 800px+)
assert frame_h < 350, "Set Y-Axis frame grew with channel count: %d" % frame_h

# 3. scrolling must actually be needed and functional
bbox = app.channel_canvas.bbox("all")
assert bbox is not None
content_height = bbox[3] - bbox[1]
print("scrollable content height =", content_height, "vs canvas height", canvas_h)
assert content_height > canvas_h, "content should overflow the visible canvas"

app.channel_canvas.yview_moveto(0)
root.update()
top_y = app.channel_canvas.yview()[0]
app.channel_canvas.yview_moveto(1)
root.update()
bottom_y = app.channel_canvas.yview()[0]
assert bottom_y > top_y, "canvas did not scroll"
print("scroll range ok: top=%.3f bottom=%.3f" % (top_y, bottom_y))

# mouse-wheel handler scrolls too
app.channel_canvas.yview_moveto(0)
root.update()
class FakeEvent:
    num = 0
    delta = -120  # Windows convention: negative delta = scroll down
app._on_channel_mousewheel(FakeEvent())
root.update()
after_wheel = app.channel_canvas.yview()[0]
assert after_wheel > 0, "mousewheel handler did not move the view"
print("mousewheel handler ok: yview now %.3f" % after_wheel)

# last row must be reachable (not clipped) - its window coords should fall
# within the canvas's bounding box once scrolled to the bottom
app.channel_canvas.yview_moveto(1)
root.update()
root.update_idletasks()
last_channel = "Ch%02d" % (NUM_CHANNELS - 1)
last_row_btn = app.channels[last_channel].color_btn
canvas_top = app.channel_canvas.winfo_rooty()
canvas_bottom = canvas_top + app.channel_canvas.winfo_height()
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
assert app.channel_canvas.yview()[0] == 0.0
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
    assert str(app.channel_canvas.cget("bg")) == c["surface"], (name, app.channel_canvas.cget("bg"))
print("theme + scroll area interplay ok")

app.on_closing()
print("\nSCROLL SMOKE OK")
