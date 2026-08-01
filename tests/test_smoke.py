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
    app.update_plot_channels({"Temp": 20.0 + n, "Sine": n * 0.5, "Noise": n}, n)
root.update()
print("channels:", sorted(app.channels))
print("colors  :", {k: app.channels[k].color for k in sorted(app.channels)})

# pin one channel by hand; it must survive theme switches
app.channels["Sine"].color = "#123456"
app.channels["Sine"].color_is_user = True
app.update_color_button_appearance("Sine")

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
    assert app.channels["Sine"].color == "#123456", "user colour was clobbered"
    assert app.channels["Temp"].color == c["plot_palette"][app.channels["Temp"].color_index]
    swatch = app.channels["Temp"].color_btn
    assert swatch.cget("bg") == app.channels["Temp"].color
    assert json.load(open(sg.SETTINGS_FILE))["theme"] == name
    print("  %-14s ok  (bg=%s, Temp=%s)" % (name, c["bg"], app.channels["Temp"].color))

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
app.clear_plot_data()
root.update()
assert app.channels == {}
print("clear_plot_data ok")

# menu colours
menubar = root.nametowidget(root.cget("menu"))
assert str(menubar.cget("bg")) == sg.themes.CURRENT["surface"], menubar.cget("bg")
assert str(menubar.cget("activebackground")) == sg.themes.CURRENT["accent"]
for sub in menubar.winfo_children():
    assert str(sub.cget("bg")) == sg.themes.CURRENT["surface"], (sub, sub.cget("bg"))
print("menu ok (%d cascades)" % len(menubar.winfo_children()))

app.on_closing()
print("\nSMOKE OK")
