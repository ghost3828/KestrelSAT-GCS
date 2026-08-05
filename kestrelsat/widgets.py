"""Small reusable Tk widgets.

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

from . import themes


# Widget classes that scroll themselves. The router stops at these instead of
# also scrolling the enclosing region - see _WheelRouter._dispatch.
_WHEEL_CLAIMANTS = {"Text", "Listbox", "Treeview", "Spinbox", "TSpinbox",
                    "TCombobox"}


class _WheelRouter:
    """One wheel binding per Tk root, dispatched by what is under the pointer.

    The obvious implementation - each scroll region binding the wheel on
    <Enter> and unbinding on <Leave> - is broken as soon as there are two
    regions. bind_all/unbind_all mutate a single global slot per event type,
    and crossing from region A to region B fires B's <Enter> before A's
    <Leave>, so A's unbind_all deletes the binding B just installed. B stays
    dead to the wheel until the pointer leaves and re-enters it.

    So the binding is installed once and routes by target instead.
    """

    _by_root = {}

    @classmethod
    def for_root(cls, root):
        # Keyed by path, but re-made only when the stored router belongs to a
        # *different* root object - which happens when a test builds a second
        # Tk(), since the path of both is ".". Rebuilding on anything else
        # (an existence check, say) silently splits regions across two router
        # instances, so a region registered with one can never be removed
        # through the other.
        key = str(root)
        router = cls._by_root.get(key)
        if router is None or router.root is not root:
            router = cls(root)
            cls._by_root[key] = router
        return router

    def __init__(self, root):
        self.root = root
        self.regions = []
        for seq in ("<MouseWheel>", "<Shift-MouseWheel>",
                    "<Button-4>", "<Button-5>",     # X11 vertical
                    "<Button-6>", "<Button-7>"):    # X11 horizontal
            try:
                root.bind_all(seq, self._dispatch, add="+")
            except tk.TclError:
                pass

    def register(self, region):
        if region not in self.regions:
            self.regions.append(region)

    def unregister(self, region):
        if region in self.regions:
            self.regions.remove(region)

    def _target(self, event):
        """Widget under the pointer, falling back to the event's own widget.

        On X11 event.widget is already the widget under the pointer, but on
        Windows <MouseWheel> goes to the focused widget instead, which is why
        pointer position is preferred where it is available.
        """
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
            if w is not None:
                return w
        except (tk.TclError, AttributeError):
            pass
        return getattr(event, "widget", None)

    def _dispatch(self, event):
        target = self._target(event)
        if target is None:
            return None

        horizontal = getattr(event, "num", 0) in (6, 7) or \
            bool(getattr(event, "state", 0) & 0x0001)

        node = target
        while node is not None:
            try:
                if node.winfo_class() in _WHEEL_CLAIMANTS:
                    # Its own class binding already scrolled it. Without this
                    # the serial monitor and the region around it both move.
                    return None
            except tk.TclError:
                return None

            for region in self.regions:
                if node is region.canvas:
                    region.scroll(self._delta(event), horizontal)
                    return "break"

            node = self._parent(node)
        return None

    @staticmethod
    def _delta(event):
        num = getattr(event, "num", 0)
        if num in (4, 6):
            return -1
        if num in (5, 7):
            return 1
        return -1 if getattr(event, "delta", 0) > 0 else 1

    @staticmethod
    def _parent(node):
        try:
            path = node.winfo_parent()
            return node._nametowidget(path) if path else None
        except (KeyError, AttributeError, tk.TclError):
            return None


class ScrollableFrame(ttk.Frame):
    """A frame whose content can exceed the viewport on either axis.

    Pack or grid widgets into ``.inner``.

    ``vscroll``/``hscroll`` control both the scrollbar and the pinning policy.
    An axis that does not scroll has its content pinned to the viewport, so
    layout inside behaves exactly as if the canvas were not there. An axis that
    does scroll takes ``max(viewport, natural request)``, which is what gives
    the scrollbar something to move: pinning to the viewport - as the inline
    version in PlotTab did - leaves the scrollregion exactly as wide as the
    visible area, so content clipped off the right edge stays unreachable.
    """

    def __init__(self, parent, vscroll: bool = True, hscroll: bool = False,
                 **kwargs):
        super().__init__(parent, **kwargs)
        self._vscroll = vscroll
        self._hscroll = hscroll
        self._pinned = None

        self.canvas = tk.Canvas(self, highlightthickness=0, takefocus=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._vbar = self._hbar = None
        if vscroll:
            self._vbar = ttk.Scrollbar(self, orient=tk.VERTICAL,
                                       command=self.canvas.yview)
            self._vbar.grid(row=0, column=1, sticky="ns")
            self.canvas.configure(yscrollcommand=self._vbar.set)
        if hscroll:
            self._hbar = ttk.Scrollbar(self, orient=tk.HORIZONTAL,
                                       command=self.canvas.xview)
            self._hbar.grid(row=1, column=0, sticky="ew")
            self.canvas.configure(xscrollcommand=self._hbar.set)

        # Scrollbars stay put rather than being hidden when everything fits.
        # Hiding one resizes the canvas, which re-fires <Configure>, which
        # re-evaluates the hide condition - an oscillator the _pinned guard
        # below does not cover. 15px is cheaper than a hang.

        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner,
                                                 anchor="nw")

        self.inner.bind("<Configure>", self._sync)
        self.canvas.bind("<Configure>", self._sync)
        self.bind("<Destroy>", self._on_destroy)

        # Hold the router directly rather than re-resolving it on teardown:
        # winfo_toplevel() is unreliable while a widget is being destroyed,
        # and looking it up again there would unregister from a different
        # router instance, leaving this region registered forever.
        self._router = _WheelRouter.for_root(self.winfo_toplevel())
        self._router.register(self)

    # -- geometry ---------------------------------------------------------

    def _sync(self, event=None):
        try:
            view_w = self.canvas.winfo_width()
            view_h = self.canvas.winfo_height()

            # Width: stretch to the viewport so panels fill the tab, but never
            # below the content's natural width - that is what leaves the
            # x-scrollbar something to move.
            w = max(view_w, self.inner.winfo_reqwidth()) if self._hscroll else view_w

            # Height: 0 means "use the widget's own requested height". The
            # vertical axis must be left natural, because pinning an axis
            # freezes the inner frame's size on it, and a frame that never
            # changes size never fires <Configure> - which is the only signal
            # that content was added. Pinning both axes made the region go
            # stale: 25 channel rows reported a content height identical to the
            # viewport and nothing scrolled.
            h = 0 if self._vscroll else view_h

            # Do not remove this guard. itemconfigure resizes inner, which
            # fires inner's <Configure>, which re-enters _sync: without a
            # change check that is an unbounded event storm - pegged CPU and a
            # frozen window, reproducing at some sizes and not others.
            if (w, h) != self._pinned:
                self._pinned = (w, h)
                self.canvas.itemconfigure(self._window, width=w, height=h)

            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            pass

    # -- wheel ------------------------------------------------------------

    def scroll(self, delta: int, horizontal: bool = False):
        """Move one wheel notch. Ignores an axis this region does not have."""
        try:
            if horizontal:
                if self._hscroll:
                    self.canvas.xview_scroll(delta, "units")
            elif self._vscroll:
                self.canvas.yview_scroll(delta, "units")
        except tk.TclError:
            pass

    def _on_destroy(self, event=None):
        # <Destroy> propagates from every descendant, so without this guard the
        # region would unregister the first time any child row is destroyed -
        # e.g. clear_plot_data() - silently killing the wheel for a live tab.
        if event is not None and event.widget is not self:
            return
        try:
            self._router.unregister(self)
        except (AttributeError, tk.TclError):
            pass



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

        # Pull back inside the screen if the widget sits near an edge -
        # otherwise the tooltip is drawn half off-display, and more often so at
        # high DPI, where TkTooltipFont makes it larger.
        try:
            self.tooltip_window.update_idletasks()
            w = self.tooltip_window.winfo_reqwidth()
            h = self.tooltip_window.winfo_reqheight()
            screen_w = self.tooltip_window.winfo_screenwidth()
            screen_h = self.tooltip_window.winfo_screenheight()
            x = max(0, min(x, screen_w - w))
            y = max(0, min(y, screen_h - h))
            self.tooltip_window.wm_geometry(f"+{x}+{y}")
        except tk.TclError:
            pass
    
    def on_leave(self, event=None):
        """Hide tooltip when mouse leaves widget"""
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None


