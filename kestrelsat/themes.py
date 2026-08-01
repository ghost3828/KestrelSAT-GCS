"""Appearance themes for the KestrelSAT Ground Control Station.

A leaf module: it imports nothing from the rest of the application, so any
other module can import it without risking a cycle.

IMPORTANT: read the active palette as ``themes.CURRENT``, never
``from .themes import CURRENT``. ThemeManager rebinds this name when the theme
changes, and a ``from ... import`` would capture the dict that was current at
import time and never see a switch.
"""

from typing import Any, Dict

SETTINGS_FILE = "serial_gui_settings.json"
DEFAULT_THEME = "dark"
THEME_ORDER = ("dark", "light", "high_contrast")

THEMES: Dict[str, Dict[str, Any]] = {
    # -- Dark: deep-space / mission-control ---------------------------------
    "dark": {
        "display_name": "Dark (Mission Control)",
        "ttk_base": "clam",
        # surfaces
        "bg": "#0B0F17",           # window base - deep space
        "surface": "#131A26",      # panels, label frames, status bar
        "surface_alt": "#1B2433",  # buttons, tab strip, scrollbars
        "field_bg": "#0E141E",     # entries, spinboxes, combobox field
        # borders
        "border": "#27374D",
        "border_light": "#1E2836",
        # text
        "fg": "#DCE6F2",
        "fg_muted": "#8FA3BC",
        "fg_disabled": "#55647A",
        "fg_on_accent": "#06121C",
        # interactive
        "accent": "#38BDF8",
        "accent_hover": "#7DD3FC",
        "accent_active": "#0EA5E9",
        "select_bg": "#1E3A5F",
        "select_fg": "#E6F1FF",
        "button_bg": "#1B2433",
        "button_hover": "#243244",
        "button_active": "#2E4257",
        "button_relief": "flat",
        "button_border_width": 1,
        # semantic
        "ok": "#34D399",
        "ok_dim": "#166E52",
        "error": "#FF5C6C",
        "error_dim": "#8B1E2B",
        "warn": "#FFB020",
        # serial monitor
        "log_bg": "#0E141E",
        "log_fg": "#C7D4E4",
        "log_received": "#7FD1FF",
        "log_sent": "#FFB86B",
        "log_system": "#DCE6F2",
        "log_error": "#FF6B7A",
        # tooltip
        "tooltip_bg": "#1B2433",
        "tooltip_fg": "#DCE6F2",
        "tooltip_border": "#38BDF8",
        # pyqtgraph
        "plot_bg": "#0B0F17",
        "plot_fg": "#DCE6F2",
        "plot_axis": "#7C8FA6",
        "plot_grid_alpha": 0.25,
        "plot_legend_bg": "#131A26E0",
        "plot_legend_border": "#27374D",
        "plot_palette": ["#38BDF8", "#FFB020", "#34D399", "#F472B6",
                         "#A78BFA", "#FF7A5C", "#E2E8F0", "#9EF01A"],
        "default_line_width": 2,
    },

    # -- Light ---------------------------------------------------------------
    "light": {
        "display_name": "Light",
        "ttk_base": "clam",
        "bg": "#F4F6F9",
        "surface": "#FFFFFF",
        "surface_alt": "#E8ECF2",
        "field_bg": "#FFFFFF",
        "border": "#C9D2DD",
        "border_light": "#E2E8F0",
        "fg": "#1B2430",
        "fg_muted": "#5A6779",
        "fg_disabled": "#9AA5B4",
        "fg_on_accent": "#FFFFFF",
        "accent": "#0B63CE",
        "accent_hover": "#1D77E6",
        "accent_active": "#094FA6",
        "select_bg": "#CDE2FF",
        "select_fg": "#10243D",
        "button_bg": "#E8ECF2",
        "button_hover": "#DCE3EC",
        "button_active": "#CBD5E1",
        "button_relief": "flat",
        "button_border_width": 1,
        "ok": "#14804A",
        "ok_dim": "#0B4F2E",
        "error": "#C62828",
        "error_dim": "#7F1D1D",
        "warn": "#B45309",
        "log_bg": "#FFFFFF",
        "log_fg": "#1B2430",
        "log_received": "#1148A8",
        "log_sent": "#B3261E",
        "log_system": "#1B2430",
        "log_error": "#C62828",
        "tooltip_bg": "#FFFFE1",
        "tooltip_fg": "#1B2430",
        "tooltip_border": "#8A8A6B",
        "plot_bg": "#FFFFFF",
        "plot_fg": "#1B2430",
        "plot_axis": "#4A5568",
        "plot_grid_alpha": 0.20,
        "plot_legend_bg": "#FFFFFFE0",
        "plot_legend_border": "#C9D2DD",
        # tab10 hues, with orange and pink darkened to clear 3:1 on white
        "plot_palette": ["#D62728", "#1F77B4", "#2CA02C", "#C25E00",
                         "#9467BD", "#8C564B", "#C2408F", "#7F7F7F"],
        "default_line_width": 2,
    },

    # -- High contrast: for outdoor / direct-sunlight use ---------------------
    "high_contrast": {
        "display_name": "High Contrast (Sunlight)",
        "ttk_base": "clam",
        "bg": "#FFFFFF",
        "surface": "#FFFFFF",
        "surface_alt": "#FFFFFF",
        "field_bg": "#FFFFFF",
        "border": "#000000",
        "border_light": "#000000",
        "fg": "#000000",
        "fg_muted": "#000000",
        "fg_disabled": "#595959",
        "fg_on_accent": "#FFFFFF",
        "accent": "#0033CC",
        "accent_hover": "#0029A3",
        "accent_active": "#001F7A",
        "select_bg": "#000000",
        "select_fg": "#FFFFFF",
        "button_bg": "#FFFFFF",
        "button_hover": "#E0E0E0",
        "button_active": "#000000",
        # white-on-white needs an explicit outline to read as a button
        "button_relief": "solid",
        "button_border_width": 2,
        "ok": "#00661A",
        "ok_dim": "#003D0F",
        "error": "#B00020",
        "error_dim": "#6B0014",
        "warn": "#6B3A00",
        "log_bg": "#FFFFFF",
        "log_fg": "#000000",
        "log_received": "#0033CC",
        "log_sent": "#B00020",
        "log_system": "#000000",
        "log_error": "#B00020",
        "tooltip_bg": "#FFFFFF",
        "tooltip_fg": "#000000",
        "tooltip_border": "#000000",
        "plot_bg": "#FFFFFF",
        "plot_fg": "#000000",
        "plot_axis": "#000000",
        "plot_grid_alpha": 0.45,
        "plot_legend_bg": "#FFFFFFF0",
        "plot_legend_border": "#000000",
        "plot_palette": ["#000000", "#0033CC", "#C41200", "#006B27",
                         "#7A00B8", "#A34F00", "#00666B", "#B5006E"],
        "default_line_width": 3,
    },
}

# Every theme must expose exactly the same tokens - a missing key would only
# surface as a KeyError halfway through a live theme switch.
assert all(set(t) == set(THEMES[DEFAULT_THEME]) for t in THEMES.values()), \
    "THEMES entries have mismatched keys"
assert set(THEME_ORDER) == set(THEMES), "THEME_ORDER does not match THEMES"

# Active palette. Read directly by code that runs outside SerialGUI (ToolTip)
# or that colours non-widget objects (canvas items, pyqtgraph pens).
CURRENT: Dict[str, Any] = THEMES[DEFAULT_THEME]



def contrast_fg_for(color: str) -> str:
    """Return black or white, whichever is readable on the given #RRGGBB."""
    try:
        h = color.lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return "#000000"
    # Perceived luminance (ITU-R BT.601)
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#FFFFFF"


