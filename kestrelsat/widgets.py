"""Small reusable Tk widgets."""

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
            font=("Arial", 9)
        )
        label.pack()
    
    def on_leave(self, event=None):
        """Hide tooltip when mouse leaves widget"""
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None


