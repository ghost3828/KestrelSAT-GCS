"""Display scaling, so the interface stays readable on high-DPI screens.

Two different things make the app look tiny on a 4K monitor, and they need
different handling:

1. **Windows display scaling is on (125/150/200%).** By default a Python/Tk
   process is not DPI-aware, so Windows renders it at 96 DPI and bitmap-stretches
   the result: correctly sized but blurry. Declaring DPI awareness stops the
   stretch and makes text crisp, but then Tk draws at real pixels and everything
   shrinks - so the process must scale itself. That is what this module does.

2. **A 4K panel running at 100% scaling.** Windows reports 96 DPI because there
   is no scaling, yet the pixels are physically tiny. DPI alone cannot detect
   this, so `detect_scale` also looks at the raw resolution.

Note that ``tk scaling`` is *not* the mechanism here. It adjusts point-to-pixel
conversion for geometry, but on the Tk builds this app targets it leaves font
sizes alone - measured, not assumed. Fonts are scaled explicitly instead: the
named fonts (TkDefaultFont and friends) drive every ttk widget and menu, and
anything wanting a custom weight derives from them via `derive_font` so it
follows along.


Copyright (C) 2026 Wyatt Harris
Written in a personal capacity. This is not a work of the United States
Government and was not prepared in the course of official duties.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import re
import sys
import tkinter as tk
from tkinter import font as tkfont

# The DPI every desktop platform treats as "100%".
BASE_DPI = 96.0

# Offered in Preferences. "auto" resolves through detect_scale().
SCALE_CHOICES = (
    ("auto", "Automatic (match display)"),
    ("1.0", "100%"),
    ("1.25", "125%"),
    ("1.5", "150%"),
    ("1.75", "175%"),
    ("2.0", "200%"),
    ("2.5", "250%"),
)

MIN_SCALE = 1.0
MAX_SCALE = 3.0

# Named fonts Tk defines. Scaling these covers ttk widgets, menus and the
# default font of classic widgets such as tk.Text.
_NAMED_FONTS = (
    "TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont",
    "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont",
    "TkIconFont", "TkTooltipFont",
)

# Baseline sizes captured before the first scaling pass, so re-applying a
# different factor scales from the original rather than compounding.
_baseline = {}


def enable_dpi_awareness():
    """Tell Windows this process handles its own DPI scaling.

    Must run before the Tk root exists. No-op off Windows. Returns True if
    awareness is now set (including when the manifest already set it).
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        # PROCESS_PER_MONITOR_DPI_AWARE = 2. Available from Windows 8.1.
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except (AttributeError, OSError):
        pass
    try:
        import ctypes
        # Windows 7/8 fallback: system-DPI aware only.
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except (AttributeError, OSError):
        # E_ACCESSDENIED here means a manifest already declared awareness,
        # which is the outcome we wanted anyway.
        return False


def detect_scale(root):
    """Best guess at a comfortable UI scale for the current display."""
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except (tk.TclError, ValueError):
        dpi = BASE_DPI

    factor = dpi / BASE_DPI

    # A 4K panel at 100% Windows scaling reports 96 DPI, so the DPI says
    # "nothing to do" while the pixels are physically tiny. Fall back to the
    # raw resolution in that case.
    if factor < 1.1:
        try:
            width = root.winfo_screenwidth()
        except tk.TclError:
            width = 0
        if width >= 3800:        # 4K and wider
            factor = 2.0
        elif width >= 2500:      # 1440p / 2.5K
            factor = 1.25

    return _clamp(factor)


def resolve(setting, root):
    """Turn a stored ui_scale setting into a numeric factor."""
    if setting is None or str(setting).lower() == "auto":
        return detect_scale(root)
    try:
        return _clamp(float(setting))
    except (TypeError, ValueError):
        return detect_scale(root)


def _clamp(factor):
    # Round to the nearest 5% so a display reporting, say, 1.4993 does not
    # produce oddly-sized fonts.
    factor = round(float(factor) * 20.0) / 20.0
    return max(MIN_SCALE, min(MAX_SCALE, factor))


def apply_fonts(root, factor):
    """Scale Tk's named fonts, which is what actually resizes the interface."""
    for name in _NAMED_FONTS:
        try:
            font = tkfont.nametofont(name, root=root)
        except tk.TclError:
            continue  # not every build defines every named font

        if name not in _baseline:
            _baseline[name] = font.cget("size")

        base = _baseline[name]
        if not base:
            continue

        # Tk font sizes: positive is points, negative is pixels. Preserve the
        # sign so we do not silently change the unit.
        scaled = int(round(abs(base) * factor))
        font.configure(size=scaled if base > 0 else -scaled)


def derive_font(root, weight=None, size_delta=0, family=None):
    """A font that tracks the scaled UI size.

    Use instead of a literal like ``("Arial", 10, "bold")``, which is a fixed
    point size and would stay small when everything else grows.
    """
    base = tkfont.nametofont("TkDefaultFont", root=root)
    font = tkfont.Font(root=root, font=base)
    if weight:
        font.configure(weight=weight)
    if size_delta:
        size = font.cget("size")
        step = int(round(abs(size_delta)))
        if size > 0:
            font.configure(size=max(1, size + (step if size_delta > 0 else -step)))
        else:
            font.configure(size=min(-1, size - (step if size_delta > 0 else -step)))
    if family:
        font.configure(family=family)
    return font


def px(factor, value):
    """Scale a pixel dimension."""
    return int(round(value * factor))


def scale_geometry(geometry, factor):
    """Scale a 'WxH' Tk geometry string, leaving any +x+y offset alone."""
    try:
        size, _, rest = geometry.partition("+")
        width, _, height = size.partition("x")
        scaled = f"{px(factor, int(width))}x{px(factor, int(height))}"
        return scaled + ("+" + rest if rest else "")
    except (ValueError, AttributeError):
        return geometry


def parse_geometry(geometry):
    """'WxH+x+y' -> (w, h, x, y). Any part that will not parse comes back None.

    Handles the negative-offset form ('800x780-10-10') too, which Tk uses to
    mean "measured from the right/bottom edge" - scale_geometry's simpler
    partition('+') cannot see those.
    """
    m = re.match(r"^\s*(\d+)x(\d+)([+-]\d+)?([+-]\d+)?\s*$", geometry or "")
    if not m:
        return (None, None, None, None)
    w, h = int(m.group(1)), int(m.group(2))
    x = int(m.group(3)) if m.group(3) else None
    y = int(m.group(4)) if m.group(4) else None
    return (w, h, x, y)


def screen_size(root):
    """Raw display size in pixels, or (0, 0) if Tk cannot answer."""
    try:
        return (int(root.winfo_screenwidth()), int(root.winfo_screenheight()))
    except Exception:
        return (0, 0)


def work_area(root):
    """Usable desktop area, with the taskbar excluded where we can tell.

    On Windows SPI_GETWORKAREA reports the real figure, in physical pixels once
    enable_dpi_awareness() has run - which main() does before the root exists.
    Anywhere else, and on any failure at all, fall back to the screen size less
    a nominal taskbar strip. This must never raise: it is called during startup
    and from the tests, which run headless.
    """
    sw, sh = screen_size(root)
    if not sw or not sh:
        return (0, 0)
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        # SPI_GETWORKAREA = 0x0030
        if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0):
            w = int(rect.right - rect.left)
            h = int(rect.bottom - rect.top)
            if w > 0 and h > 0:
                return (min(w, sw), min(h, sh))
    except Exception:
        pass
    # No reliable answer: assume a taskbar-sized strip is unavailable.
    return (sw, max(1, sh - 70))


def clamp_size(size, area, margin=(0, 0)):
    """Shrink (w, h) to fit inside area. Pure, so it unit-tests with no display."""
    w, h = size
    aw, ah = area
    mw, mh = margin
    if aw and aw > 0:
        w = min(w, max(1, aw - mw))
    if ah and ah > 0:
        h = min(h, max(1, ah - mh))
    return (int(w), int(h))
