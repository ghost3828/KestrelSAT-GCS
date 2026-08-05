"""Headless smoke test: build the GUI, cycle every theme, exercise dialogs."""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
import kestrelsat.app as sg

print("PYQTGRAPH_AVAILABLE =", sg.PYQTGRAPH_AVAILABLE)

# fresh profile -> must default to dark
if os.path.exists(sg.SETTINGS_FILE):
    os.remove(sg.SETTINGS_FILE)

root = tk.Tk()
app = sg.SerialGUI(root)
root.update()
assert app.theme_name == "dark", app.theme_name
assert sg.themes.CURRENT is sg.THEMES["dark"]
print("default theme:", app.theme_name)

# some log traffic so the monitor tags have content to recolour
for kind in ("RECEIVED", "SENT", "SYSTEM", "ERROR"):
    app.log_message("sample %s line" % kind, kind)
root.update()

# synthetic channels -> exercises add_channel_control + swatches
for n in range(3):
    app.plots[0].update_plot_channels({"Temp": 20.0 + n, "Sine": n * 0.5, "Noise": n}, n)
root.update()
print("channels:", sorted(app.plots[0].channels))
print("colors  :", {k: app.plots[0].channels[k].color for k in sorted(app.plots[0].channels)})

# pin one channel by hand; it must survive theme switches
app.plots[0].channels["Sine"].color = "#123456"
app.plots[0].channels["Sine"].color_is_user = True
app.plots[0].update_color_button_appearance("Sine")

for name in sg.THEME_ORDER:
    app.theme_var.set(name)
    app.on_theme_selected()
    root.update()
    c = sg.THEMES[name]
    assert sg.themes.CURRENT is c
    assert root.cget("bg") == c["bg"], (root.cget("bg"), c["bg"])
    assert app.received_text.cget("bg") == c["log_bg"]
    assert app.received_text.tag_cget("ERROR", "foreground") == c["log_error"]
    assert app.status_canvas.itemcget(app.status_circle, "fill") == c["error"]
    assert str(app.log_status_label.cget("foreground")) == c["error"]
    assert app.plot_colors == c["plot_palette"]
    assert app.plots[0].channels["Sine"].color == "#123456", "user colour was clobbered"
    assert app.plots[0].channels["Temp"].color == c["plot_palette"][app.plots[0].channels["Temp"].color_index]
    swatch = app.plots[0].channels["Temp"].color_btn
    assert swatch.cget("bg") == app.plots[0].channels["Temp"].color
    assert json.load(open(sg.SETTINGS_FILE))["theme"] == name
    print("  %-14s ok  (bg=%s, Temp=%s)" % (name, c["bg"], app.plots[0].channels["Temp"].color))

# connection indicator uses the ok colours
app.update_status_indicator(True)
root.update()
assert app.status_canvas.itemcget(app.status_circle, "fill") == sg.themes.CURRENT["ok"]
app.update_status_indicator(False)

# dialogs must build and be themed
for opener, kwargs in ((app.show_preferences, {}), (app.show_serial_config, {}),
                       (app.show_about, {}), (app.show_user_guide, {})):
    opener(**kwargs)
    root.update()
    tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    assert tops, "no Toplevel created by %s" % opener.__name__
    dlg = tops[-1]
    assert dlg.cget("bg") == sg.themes.CURRENT["bg"], (opener.__name__, dlg.cget("bg"))
    dlg.grab_release()
    dlg.destroy()
    root.update()
    print("  dialog %-20s ok" % opener.__name__)

# settings merge: writing connection settings must not drop the theme
app.save_current_settings()
data = json.load(open(sg.SETTINGS_FILE))
assert data["theme"] == "high_contrast", data
assert "baud_rate" in data, data
print("settings merge ok:", data)

# clearing plot data must rebuild the channel frame cleanly
app.plots[0].clear_plot_data()
root.update()
assert app.plots[0].channels == {}
print("clear_plot_data ok")

# menu colours
menubar = root.nametowidget(root.cget("menu"))
assert str(menubar.cget("bg")) == sg.themes.CURRENT["surface"], menubar.cget("bg")
assert str(menubar.cget("activebackground")) == sg.themes.CURRENT["accent"]
for sub in menubar.winfo_children():
    assert str(sub.cget("bg")) == sg.themes.CURRENT["surface"], (sub, sub.cget("bg"))
print("menu ok (%d cascades)" % len(menubar.winfo_children()))

# notepad timestamp hotkey
import re as _re
_TS = _re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

assert app.insert_timestamp_btn.cget("text") == "Insert Timestamp (Ctrl+T)", \
    app.insert_timestamp_btn.cget("text")

# Inside the notepad: exactly one timestamp, and Tk's default Control-t
# transpose must not also fire (it would reorder the surrounding characters).
app.notebook.select(app.notepad_frame)
app.notepad_text.delete("1.0", "end")
app.notepad_text.insert("1.0", "abcd")
app.notepad_text.mark_set("insert", "1.2")
app.notepad_text.focus_force()
root.update()
app.notepad_text.event_generate("<Control-t>")
root.update()
_got = app.notepad_text.get("1.0", "end-1c")
assert len(_TS.findall(_got)) == 1, _got
assert "".join(c for c in _got if c.isalpha()) == "abcd", "Ctrl+T transposed characters"

# From another tab it switches to the Notepad, so the insert is never invisible.
app.notebook.select(app.plots[0].frame)
app.notepad_text.delete("1.0", "end")
root.update()
root.event_generate("<Control-t>")
root.update()
assert app.notebook.tab(app.notebook.select(), "text") == "Notepad"
assert len(_TS.findall(app.notepad_text.get("1.0", "end-1c"))) == 1
print("notepad Ctrl+T hotkey ok")

app.notes_dirty = False
app.on_closing()
print("\nSMOKE OK")
