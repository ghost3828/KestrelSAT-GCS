"""Applies a theme to a live tkinter/ttk widget tree.

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

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional

from . import themes

class ThemeManager:
    """Applies a THEMES entry to a live tkinter/ttk widget tree.

    ttk widgets repaint themselves whenever the style database changes, so they
    need no per-widget work. Classic tk widgets (Menu, Canvas, Text, Scrollbar,
    the tooltip Label, the channel colour swatches) do not participate in ttk
    theming at all and are recoloured by walking the widget tree.
    """

    def __init__(self, root: tk.Tk, theme_name: str = themes.DEFAULT_THEME):
        self.root = root
        self.style = ttk.Style(root)
        self.name = theme_name if theme_name in themes.THEMES else themes.DEFAULT_THEME
        self.colors = themes.THEMES[self.name]
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []

    # -- public API ---------------------------------------------------------
    def add_listener(self, fn: Callable[[Dict[str, Any]], None]):
        """Register a callback fired after every apply() with the new palette."""
        self._listeners.append(fn)

    def exempt(self, widget):
        """Mark a widget so the tree walk never recolours it."""
        try:
            widget._no_theme = True
        except Exception:
            pass

    def restyle(self, widget):
        """Re-apply the active theme to one subtree (new dialogs, new rows)."""
        try:
            self._walk(widget, self.colors)
        except Exception:
            pass

    def apply(self, theme_name: Optional[str] = None):
        """Apply a theme to the whole application. Main thread only."""
        if theme_name is not None:
            self.name = theme_name if theme_name in themes.THEMES else themes.DEFAULT_THEME
        self.colors = themes.THEMES[self.name]
        # Rebind on the module, so every reader of themes.CURRENT sees it.
        themes.CURRENT = self.colors
        c = self.colors

        # 1. Base ttk theme. Only 'clam' honours full recolouring of every
        #    element; the native Windows themes ignore -background on buttons,
        #    entries and notebook tabs. theme_use() resets the style database,
        #    so _configure_ttk() must always run after it.
        base = c.get("ttk_base", "clam")
        try:
            if self.style.theme_use() != base:
                self.style.theme_use(base)
        except tk.TclError:
            pass  # keep whatever theme is active

        self._configure_ttk(c)
        self._configure_option_db(c)

        try:
            self.root.configure(bg=c["bg"])
        except tk.TclError:
            pass

        self._walk(self.root, c)

        for fn in self._listeners:
            try:
                fn(c)
            except Exception:
                pass

    # -- ttk style database -------------------------------------------------
    def _configure_ttk(self, c: Dict[str, Any]):
        s = self.style

        s.configure(".",
                    background=c["surface"], foreground=c["fg"],
                    fieldbackground=c["field_bg"], bordercolor=c["border"],
                    lightcolor=c["surface_alt"], darkcolor=c["surface_alt"],
                    troughcolor=c["bg"], arrowcolor=c["fg"],
                    focuscolor=c["accent"], insertcolor=c["fg"],
                    selectbackground=c["select_bg"], selectforeground=c["select_fg"])

        s.configure("TFrame", background=c["surface"])
        s.configure("TLabel", background=c["surface"], foreground=c["fg"])

        s.configure("TButton", background=c["button_bg"], foreground=c["fg"],
                    bordercolor=c["border"], lightcolor=c["button_bg"],
                    darkcolor=c["button_bg"], relief=c["button_relief"],
                    borderwidth=c["button_border_width"], padding=(8, 4))
        s.map("TButton",
              background=[("pressed", c["button_active"]),
                          ("active", c["button_hover"]),
                          ("disabled", c["surface"])],
              foreground=[("disabled", c["fg_disabled"])],
              bordercolor=[("focus", c["accent"])])

        s.configure("TEntry", fieldbackground=c["field_bg"], foreground=c["fg"],
                    bordercolor=c["border"], insertcolor=c["fg"], padding=3)
        s.map("TEntry",
              fieldbackground=[("disabled", c["surface"]), ("readonly", c["surface"])],
              foreground=[("disabled", c["fg_disabled"])],
              bordercolor=[("focus", c["accent"])])

        s.configure("TCombobox", fieldbackground=c["field_bg"],
                    background=c["button_bg"], foreground=c["fg"],
                    arrowcolor=c["fg"], bordercolor=c["border"], padding=3)
        s.map("TCombobox",
              fieldbackground=[("readonly", c["field_bg"]), ("disabled", c["surface"])],
              foreground=[("readonly", c["fg"]), ("disabled", c["fg_disabled"])],
              # without these a readonly combobox paints a solid highlight block
              selectbackground=[("readonly", c["field_bg"])],
              selectforeground=[("readonly", c["fg"])],
              arrowcolor=[("disabled", c["fg_disabled"])],
              bordercolor=[("focus", c["accent"])])

        s.configure("TSpinbox", fieldbackground=c["field_bg"], foreground=c["fg"],
                    background=c["button_bg"], arrowcolor=c["fg"],
                    bordercolor=c["border"], padding=2)
        s.map("TSpinbox",
              fieldbackground=[("disabled", c["surface"])],
              foreground=[("disabled", c["fg_disabled"])])

        for cls in ("TCheckbutton", "TRadiobutton"):
            s.configure(cls, background=c["surface"], foreground=c["fg"],
                        indicatorbackground=c["field_bg"],
                        indicatorforeground=c["accent"],
                        focuscolor=c["accent"])
            s.map(cls,
                  background=[("active", c["surface"])],
                  foreground=[("disabled", c["fg_disabled"])],
                  indicatorbackground=[("selected", c["accent"]),
                                       ("pressed", c["button_active"]),
                                       ("!selected", c["field_bg"])],
                  indicatorforeground=[("selected", c["fg_on_accent"])])

        s.configure("TNotebook", background=c["bg"], bordercolor=c["border"],
                    tabmargins=(2, 4, 2, 0))
        s.configure("TNotebook.Tab", background=c["surface_alt"],
                    foreground=c["fg_muted"], bordercolor=c["border"],
                    lightcolor=c["surface_alt"], padding=(12, 6))
        s.map("TNotebook.Tab",
              background=[("selected", c["surface"]), ("active", c["button_hover"])],
              foreground=[("selected", c["fg"]), ("disabled", c["fg_disabled"])],
              expand=[("selected", (1, 1, 1, 0))])

        s.configure("TLabelframe", background=c["surface"], bordercolor=c["border"],
                    lightcolor=c["border"], darkcolor=c["border"], relief="solid")
        s.configure("TLabelframe.Label", background=c["surface"], foreground=c["accent"])

        s.configure("TScrollbar", background=c["surface_alt"], troughcolor=c["bg"],
                    bordercolor=c["border"], arrowcolor=c["fg_muted"],
                    relief="flat", gripcount=0)
        s.map("TScrollbar",
              background=[("pressed", c["accent_active"]), ("active", c["button_hover"])],
              arrowcolor=[("pressed", c["accent"])])

        # Semantic label styles
        s.configure("Status.TLabel", background=c["surface"], foreground=c["fg"])
        s.configure("Ok.TLabel", background=c["surface"], foreground=c["ok"])
        s.configure("Error.TLabel", background=c["surface"], foreground=c["error"])

    # -- option database (applies to widgets created later) -----------------
    def _configure_option_db(self, c: Dict[str, Any]):
        try:
            o = self.root.option_add
            o("*Menu.background", c["surface"])
            o("*Menu.foreground", c["fg"])
            o("*Menu.activeBackground", c["accent"])
            o("*Menu.activeForeground", c["fg_on_accent"])
            o("*Menu.selectColor", c["accent"])
            o("*Menu.relief", "flat")
            o("*TCombobox*Listbox.background", c["field_bg"])
            o("*TCombobox*Listbox.foreground", c["fg"])
            o("*TCombobox*Listbox.selectBackground", c["select_bg"])
            o("*TCombobox*Listbox.selectForeground", c["select_fg"])
        except tk.TclError:
            pass

    # -- widget tree walk ---------------------------------------------------
    def _walk(self, widget, c: Dict[str, Any]):
        if getattr(widget, "_no_theme", False):
            return
        if isinstance(widget, ttk.Combobox):
            self._theme_combobox_popdown(widget, c)
        elif not isinstance(widget, ttk.Widget):
            self._style_classic(widget, c)
        try:
            children = widget.winfo_children()
        except Exception:
            return
        for child in children:
            self._walk(child, c)

    def _style_classic(self, w, c: Dict[str, Any]):
        cls = w.winfo_class()
        try:
            if cls in ("Tk", "Toplevel"):
                w.configure(bg=c["bg"])
            elif cls in ("Frame", "Labelframe"):
                w.configure(bg=c["surface"], highlightbackground=c["border"])
            elif cls == "Label":
                w.configure(bg=c["surface"], fg=c["fg"])
            elif cls == "Button":
                w.configure(bg=c["button_bg"], fg=c["fg"],
                            activebackground=c["button_hover"],
                            activeforeground=c["fg"],
                            highlightbackground=c["surface"],
                            relief="flat", bd=1)
            elif cls == "Canvas":
                w.configure(bg=c["surface"], highlightthickness=0)
            elif cls == "Text":
                w.configure(bg=c["log_bg"], fg=c["log_fg"],
                            insertbackground=c["fg"],
                            selectbackground=c["select_bg"],
                            selectforeground=c["select_fg"],
                            highlightbackground=c["border"],
                            highlightcolor=c["accent"],
                            bd=0, relief="flat")
            elif cls == "Scrollbar":
                w.configure(bg=c["surface_alt"], troughcolor=c["bg"],
                            activebackground=c["accent"],
                            highlightbackground=c["surface"],
                            bd=0, relief="flat")
            elif cls == "Listbox":
                w.configure(bg=c["field_bg"], fg=c["fg"],
                            selectbackground=c["select_bg"],
                            selectforeground=c["select_fg"],
                            highlightbackground=c["border"], bd=0)
            elif cls == "Menu":
                w.configure(bg=c["surface"], fg=c["fg"],
                            activebackground=c["accent"],
                            activeforeground=c["fg_on_accent"],
                            disabledforeground=c["fg_disabled"],
                            selectcolor=c["accent"],
                            bd=0, relief="flat")
            elif cls in ("Entry", "Spinbox"):
                w.configure(bg=c["field_bg"], fg=c["fg"],
                            insertbackground=c["fg"],
                            selectbackground=c["select_bg"],
                            selectforeground=c["select_fg"],
                            highlightbackground=c["border"], bd=1, relief="flat")
        except tk.TclError:
            pass

    def _theme_combobox_popdown(self, widget, c: Dict[str, Any]):
        """Recolour an existing combobox dropdown.

        The popdown listbox is created by Tk itself, so the option database
        cannot retrofit comboboxes that already exist.
        """
        try:
            popdown = self.root.tk.eval("ttk::combobox::PopdownWindow %s" % widget)
            self.root.tk.call("%s.f.l" % popdown, "configure",
                              "-background", c["field_bg"],
                              "-foreground", c["fg"],
                              "-selectbackground", c["select_bg"],
                              "-selectforeground", c["select_fg"])
            self.root.tk.call("%s.f" % popdown, "configure",
                              "-background", c["border"])
        except tk.TclError:
            pass


