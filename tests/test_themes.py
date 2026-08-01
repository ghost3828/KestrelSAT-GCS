"""Static validation of the THEMES table in serial_gui.py.

Runs without tkinter/pyserial: the THEMES literal is pulled out with ast.
"""
import ast
import os
import re
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "serial_gui.py")
HEX = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")

mod = ast.parse(open(SRC).read())
themes = order = default = None
for node in mod.body:
    if isinstance(node, ast.Assign):
        name = getattr(node.targets[0], "id", "")
        if name == "THEMES":
            themes = ast.literal_eval(node.value)
        elif name == "THEME_ORDER":
            order = ast.literal_eval(node.value)
        elif name == "DEFAULT_THEME":
            default = ast.literal_eval(node.value)
    elif isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "THEMES":
        themes = ast.literal_eval(node.value)

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def srgb(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lum(color):
    h = color.lstrip("#")[:6]
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b)


def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


check(themes is not None, "THEMES not found")
check(default == "dark", f"DEFAULT_THEME is {default!r}, expected 'dark'")
check(set(order) == set(themes), "THEME_ORDER does not match THEMES")
check(set(themes) == {"dark", "light", "high_contrast"}, f"unexpected themes: {sorted(themes)}")

# 1. key parity
ref = set(themes["dark"])
for name, t in themes.items():
    check(set(t) == ref, f"{name}: key mismatch {set(t) ^ ref}")

# 2. colour format
for name, t in themes.items():
    for key, val in t.items():
        if isinstance(val, str) and key not in ("display_name", "ttk_base", "button_relief"):
            check(bool(HEX.match(val)), f"{name}.{key} = {val!r} is not #RRGGBB[AA]")
    pal = t["plot_palette"]
    check(len(pal) >= 8, f"{name}: plot_palette has only {len(pal)} entries")
    check(len(set(pal)) == len(pal), f"{name}: plot_palette has duplicates")
    for col in pal:
        check(bool(HEX.match(col)), f"{name}: bad palette colour {col!r}")

# 3. contrast
for name, t in themes.items():
    aaa = 7.0 if name == "high_contrast" else 4.5
    checks = [
        ("fg/bg", t["fg"], t["bg"], 7.0),
        ("fg/surface", t["fg"], t["surface"], 7.0),
        ("fg/field_bg", t["fg"], t["field_bg"], 7.0),
        ("fg_muted/surface", t["fg_muted"], t["surface"], 4.5),
        ("accent/surface", t["accent"], t["surface"], aaa),
        ("ok/surface", t["ok"], t["surface"], aaa),
        ("error/surface", t["error"], t["surface"], aaa),
        ("fg_on_accent/accent", t["fg_on_accent"], t["accent"], 4.5),
        ("select_fg/select_bg", t["select_fg"], t["select_bg"], 4.5),
        ("tooltip_fg/tooltip_bg", t["tooltip_fg"], t["tooltip_bg"], 4.5),
        ("log_fg/log_bg", t["log_fg"], t["log_bg"], 7.0),
        ("plot_fg/plot_bg", t["plot_fg"], t["plot_bg"], 7.0),
    ]
    for tag in ("log_received", "log_sent", "log_system", "log_error"):
        checks.append((f"{tag}/log_bg", t[tag], t["log_bg"], 4.5))
    for label, fg, bg, want in checks:
        got = ratio(fg, bg)
        check(got >= want, f"{name}: {label} contrast {got:.2f} < {want}")
    # plot traces only need WCAG non-text 3:1
    for i, col in enumerate(t["plot_palette"]):
        got = ratio(col, t["plot_bg"])
        check(got >= 3.0, f"{name}: plot_palette[{i}] {col} vs plot_bg = {got:.2f} < 3.0")

if fails:
    print("FAIL (%d)" % len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("OK: %d themes, %d tokens each, all contrast checks pass" % (len(themes), len(ref)))
