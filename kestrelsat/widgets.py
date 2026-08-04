"""Small reusable Tk widgets.

Copyright (C) 2026 Wyatt Harris

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

import tkinter as tk

from . import themes

class ToolTip:
    """Simple tooltip class for tkinter widgets"""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        
        # Bind events
        self.widget.bind("<Enter>", self.on_enter)
        self.widget.bind("<Leave>", self.on_leave)
    
    def on_enter(self, event=None):
        """Show tooltip when mouse enters widget"""
        if self.tooltip_window or not self.text:
            return
        
        # hasattr(widget, 'bbox') is useless as a guard: tkinter.Misc aliases
        # bbox to grid_bbox, so every widget has one. On a Button that means
        # 'grid bbox <w> insert', which raises TclError inside this binding.
        # Only text-entry widgets accept an index.
        x = y = 0
        try:
            x, y, _, _ = self.widget.bbox("insert")
        except (tk.TclError, TypeError, ValueError):
            pass
        x += self.widget.winfo_rootx() + 20
        y += self.widget.winfo_rooty() + 20
        
        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True)
        self.tooltip_window.wm_geometry(f"+{x}+{y}")
        
        label = tk.Label(
            self.tooltip_window,
            text=self.text,
            background=themes.CURRENT["tooltip_bg"],
            foreground=themes.CURRENT["tooltip_fg"],
            highlightbackground=themes.CURRENT["tooltip_border"],
            highlightthickness=1,
            relief="flat",
            borderwidth=0,
            padx=4,
            pady=2,
            # TkTooltipFont is scaled by scaling.apply_fonts, so the tooltip
            # tracks the rest of the interface instead of staying at 9pt.
            font="TkTooltipFont"
        )
        label.pack()
    
    def on_leave(self, event=None):
        """Hide tooltip when mouse leaves widget"""
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None


