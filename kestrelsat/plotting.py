"""One independently configurable plot: its tab, its data and its window.

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

Every plot owns its channels, buffers, axes and pop-out window, so two plots
can show the same serial stream parsed and styled completely differently.
They share only the incoming lines: each tab parses every line itself, with
its own delimiter.
"""

import itertools
import time
import tkinter as tk
from collections import deque
from tkinter import ttk, colorchooser
from typing import Any, Dict

from . import scaling, themes
from .channels import Channel
from .themes import contrast_fg_for
from .widgets import ScrollableFrame, ToolTip

try:
    import pyqtgraph as pg
    from PyQt5 import QtCore, QtWidgets
    PYQTGRAPH_AVAILABLE = True
except ImportError:
    pg = None
    QtCore = None
    QtWidgets = None
    PYQTGRAPH_AVAILABLE = False


class PlotTab:
    """A single plot: one notebook tab plus one pyqtgraph window."""

    MAX_CHANNELS = 64
    LEGEND_OFFSET = (-70, 30)

    def __init__(self, app, index: int, parent):
        self.app = app
        self.index = index
        self.frame = ttk.Frame(parent)

        # --- plot state, all per instance ---
        self.channels: Dict[str, Channel] = {}
        self.plot_window = None
        self.plot_widget = None
        self.plot_legend = None
        self.plot_colors = list(themes.CURRENT["plot_palette"])

        self.plot_max_points = 1000
        self.plot_width = 500
        self.x_axis_width = 500
        self.x_axis_width_locked = False
        self._applying_x_range = False
        self._x_width_ui_dirty = False
        self._x_width_unlock_notice_pending = False

        self.delimiter = ','
        self.plot_paused = False
        self.x_axis_selection = "Sample Number"
        self.x_axis_custom_label = ""
        self.y_axis_custom_label = ""
        self.plot_title_custom = ""

        self.sample_counter = 0
        self.last_plot_update = 0
        self.plot_update_interval = 0.033  # ~30 FPS
        self.pending_plot_update = False
        self._plot_timer = None

        self._parse_error_reported = False
        self._channel_cap_reported = False
        self._plot_error_reported = False

        self.build_ui()

    # -- identity ---------------------------------------------------------

    @property
    def tab_title(self) -> str:
        return f"Plot {self.index}"

    @property
    def window_title(self) -> str:
        """Custom title if the user set one, else 'Serial Plot N'."""
        if self.plot_title_custom.strip():
            return self.plot_title_custom
        return f"Serial Plot {self.index}"

    def _status(self, text: str):
        """Write to the shared status bar, naming the plot when ambiguous."""
        try:
            if len(self.app.plots) > 1:
                text = f"{self.tab_title}: {text}"
            self.app.plot_status_label.config(text=text)
        except Exception:
            pass

    def renumber(self, index: int):
        """Take a new position after an earlier plot was removed."""
        self.index = index
        if self.plot_window is not None:
            try:
                self.plot_window.setWindowTitle(self.window_title)
            except Exception:
                pass
        self.update_plot_title()

    def destroy(self):
        """Tear down the Qt window and the tab's Tk widgets."""
        if self._plot_timer is not None:
            try:
                self.app.root.after_cancel(self._plot_timer)
            except Exception:
                pass
            self._plot_timer = None
        self._close_plot_window()
        try:
            self.frame.destroy()
        except Exception:
            pass

    def _remap_auto_channel_colors(self, c: Dict[str, Any]):
        """Re-map default channel colours, leaving user picks untouched.

        The stored palette slot (not the current channel count) is used, so a
        channel keeps its hue position when the theme changes.
        """
        palette = c["plot_palette"]
        for ch in self.channels.values():
            if ch.color_is_user:
                continue
            ch.color = palette[ch.color_index % len(palette)]

    def _channel_style(self, channel_name: str):
        """pen/symbol keyword arguments for a channel's current settings."""
        ch = self.channels[channel_name]
        return {
            'pen': pg.mkPen(color=ch.color, width=ch.thickness) if ch.show_line else None,
            'symbol': 'o' if ch.dot_size > 0 else None,
            'symbolSize': ch.dot_size if ch.dot_size > 0 else 1,
            'symbolBrush': ch.color,
        }

    def _create_curve(self, channel_name: str):
        """Create the plot curve for a channel and add it to the legend."""
        ch = self.channels.get(channel_name)
        if not PYQTGRAPH_AVAILABLE or self.plot_widget is None or ch is None:
            return
        try:
            ch.curve = self.plot_widget.plot(name=ch.name, **self._channel_style(channel_name))
            ch.has_data = False
            if self.plot_legend is not None:
                self.plot_legend.addItem(ch.curve, ch.label)
        except Exception:
            pass

    def _apply_channel_style(self, channel_name: str):
        """Re-apply a channel's pen and symbol to its existing curve."""
        ch = self.channels.get(channel_name)
        if not PYQTGRAPH_AVAILABLE or ch is None or ch.curve is None:
            return
        curve = ch.curve
        try:
            style = self._channel_style(channel_name)
            curve.setPen(style['pen'])
            curve.setSymbol(style['symbol'])
            if style['symbol'] is not None:
                curve.setSymbolSize(style['symbolSize'])
                curve.setSymbolBrush(style['symbolBrush'])
        except Exception:
            pass

    def _drop_curves(self):
        """Forget every curve, e.g. after the plot window has been closed."""
        for ch in self.channels.values():
            ch.curve = None
            ch.has_data = False

    def _rebuild_legend(self):
        """Rebuild legend entries from the current names and visibility."""
        if not PYQTGRAPH_AVAILABLE or self.plot_legend is None:
            return
        try:
            self.plot_legend.clear()
            for ch in self.channels.values():
                # Filtering here consistently is the point: update_channel_name
                # used to rebuild without it, so renaming any channel made
                # previously hidden ones reappear in the legend.
                if ch.curve is not None and ch.visible:
                    self.plot_legend.addItem(ch.curve, ch.label)
        except Exception:
            pass

    def _make_legend(self):
        """Create a themed LegendItem attached to the current plot item."""
        if not PYQTGRAPH_AVAILABLE or self.plot_widget is None:
            return None
        try:
            legend = pg.LegendItem(
                offset=self.LEGEND_OFFSET,
                brush=pg.mkBrush(themes.CURRENT["plot_legend_bg"]),
                pen=pg.mkPen(themes.CURRENT["plot_legend_border"]),
                labelTextColor=themes.CURRENT["plot_fg"],
            )
            legend.setParentItem(self.plot_widget.getPlotItem())
            return legend
        except Exception:
            return None

    def _repen_all_curves(self):
        """Push the current channel colours onto live pyqtgraph curves"""
        for channel_name in self.channels:
            self._apply_channel_style(channel_name)

    def _apply_pyqtgraph_theme(self, c: Dict[str, Any]):
        """Theme the pop-out plot window.

        setConfigOption only affects items created afterwards, so an already
        open window needs its axes, grid and legend updated explicitly.
        """
        if not PYQTGRAPH_AVAILABLE:
            return

        try:
            pg.setConfigOption('background', c["plot_bg"])
            pg.setConfigOption('foreground', c["plot_fg"])
        except Exception:
            pass

        # Closed or never opened: the config options above are enough, and
        # show_plot_window() re-applies the theme when it builds the window.
        if getattr(self, 'plot_widget', None) is None:
            return

        try:
            self.plot_widget.setBackground(c["plot_bg"])

            for axis_name in ('left', 'bottom', 'right', 'top'):
                try:
                    axis = self.plot_widget.getAxis(axis_name)
                    axis.setPen(pg.mkPen(color=c["plot_axis"]))
                    axis.setTextPen(pg.mkPen(color=c["plot_fg"]))
                except Exception:
                    pass

            # The grid is drawn with the axis pen, so it follows the above.
            self.plot_widget.showGrid(x=True, y=True, alpha=c["plot_grid_alpha"])

            if getattr(self, 'plot_legend', None) is not None:
                try:
                    self.plot_legend.setLabelTextColor(c["plot_fg"])
                    self.plot_legend.setBrush(pg.mkBrush(c["plot_legend_bg"]))
                    self.plot_legend.setPen(pg.mkPen(c["plot_legend_border"]))
                    # setLabelTextColor only stores the option; the label HTML
                    # is rebuilt by setText, so re-issue it for existing items.
                    for _sample, label in self.plot_legend.items:
                        try:
                            label.setText(label.text)
                        except Exception:
                            pass
                except Exception:
                    pass

            if getattr(self, 'plot_window', None) is not None:
                try:
                    self.plot_window.setStyleSheet(
                        "QMainWindow { background-color: %s; }" % c["plot_bg"])
                except Exception:
                    pass

            # Re-issue the labels so they pick up the new text colour
            self.update_plot_title()
            self.update_x_axis_label()
            self.update_y_axis_label()
            self._repen_all_curves()
        except Exception:
            pass

    def _close_plot_window(self):
        """Close and release the pop-out plot window, if one is open"""
        if not PYQTGRAPH_AVAILABLE:
            return
        try:
            if self.plot_window is not None:
                self.plot_window.close()
        except Exception:
            pass
        finally:
            self.plot_window = None
            self.plot_widget = None
            self.plot_legend = None
            self._drop_curves()

    def build_ui(self):
        """Create plot tab content.

        The whole tab scrolls, not just the channel list: with enough channels
        every panel below them would otherwise be pushed out of reach. The
        control buttons are packed to the bottom first so they keep their slice
        of the window no matter how tall the scrollable content grows.
        """
        self.plot_window = None
        self.plot_widget = None

        # Button bar - packed BOTTOM first so it is always visible regardless
        # of how much content sits above it.
        btn_bar = ttk.Frame(self.frame, relief="groove", borderwidth=1)
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 5))

        btn_inner = ttk.Frame(btn_bar)
        btn_inner.pack(anchor="center", pady=6)

        self.show_plot_btn = ttk.Button(btn_inner, text="Show Plot Window", command=self.show_plot_window)
        self.show_plot_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.pause_plot_btn = ttk.Button(btn_inner, text="Pause Plot", command=self.toggle_plot_pause)
        self.pause_plot_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.clear_plot_btn = ttk.Button(btn_inner, text="Clear Plot Data", command=self.clear_plot_data)
        self.clear_plot_btn.pack(side=tk.LEFT, padx=(0, 10))

        # Without this the '+' tab would be a one-way door.
        self.remove_plot_btn = ttk.Button(
            btn_inner, text="Remove This Plot",
            command=lambda: self.app.remove_plot_tab(self))
        self.remove_plot_btn.pack(side=tk.LEFT)
        ToolTip(self.remove_plot_btn,
                "Close this plot and discard its data and settings")

        # Outer scrollable area - covers all content panels. Horizontal as well
        # as vertical: a channel row is ~700px wide, so on a narrow window its
        # rightmost controls ("Show Line", dot size) used to be clipped with no
        # way at all to reach them.
        self._scroll = ScrollableFrame(self.frame, vscroll=True, hscroll=True)
        self._scroll.pack(fill=tk.BOTH, expand=True)

        # Kept under the old name: clear_plot_data and the scroll tests use it.
        self.plot_scroll_canvas = self._scroll.canvas
        content_frame = self._scroll.inner

        # --- All content panels go inside content_frame ---

        # Delimiter selection frame
        delimiter_frame = ttk.LabelFrame(content_frame, text="Data Parsing", padding="10")
        delimiter_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        ttk.Label(delimiter_frame, text="Delimiter:").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))

        self.delimiter_var = tk.StringVar(value="comma")
        delim_frame = ttk.Frame(delimiter_frame)
        delim_frame.grid(row=0, column=1, sticky=tk.W)

        ttk.Radiobutton(delim_frame, text="Comma (,)", variable=self.delimiter_var,
                       value="comma", command=self.update_delimiter).pack(side=tk.LEFT)
        ttk.Radiobutton(delim_frame, text="Space", variable=self.delimiter_var,
                       value="space", command=self.update_delimiter).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Radiobutton(delim_frame, text="Tab", variable=self.delimiter_var,
                       value="tab", command=self.update_delimiter).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Radiobutton(delim_frame, text="Other:", variable=self.delimiter_var,
                       value="other", command=self.update_delimiter).pack(side=tk.LEFT, padx=(10, 0))

        self.custom_delim_entry = ttk.Entry(delim_frame, width=5)
        self.custom_delim_entry.pack(side=tk.LEFT, padx=(5, 0))
        self.custom_delim_entry.bind('<KeyRelease>', self.on_custom_delimiter_change)

        # Buffer and display settings frame
        settings_frame = ttk.LabelFrame(content_frame, text="Plot Settings", padding="10")
        settings_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(settings_frame, text="Buffer Size (number of samples/channel stored in memory):").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.buffer_size_var = tk.StringVar(value=str(self.plot_max_points))
        buffer_entry = ttk.Entry(settings_frame, textvariable=self.buffer_size_var, width=10)
        buffer_entry.grid(row=0, column=1, padx=(0, 20), sticky=tk.W)
        buffer_entry.bind('<Return>', self.update_buffer_settings)
        buffer_entry.bind('<FocusOut>', self.update_buffer_settings)
        ToolTip(buffer_entry, "Maximum number of samples stored in memory per channel")

        ttk.Label(settings_frame, text="Plot Size (number of samples/channel shown on plot):").grid(row=1, column=0, sticky=tk.W, padx=(0, 5), pady=(10, 0))
        self.plot_width_var = tk.StringVar(value=str(self.plot_width))
        width_entry = ttk.Entry(settings_frame, textvariable=self.plot_width_var, width=10)
        width_entry.grid(row=1, column=1, padx=(0, 20), sticky=tk.W, pady=(10, 0))
        width_entry.bind('<Return>', self.update_buffer_settings)
        width_entry.bind('<FocusOut>', self.update_buffer_settings)
        ToolTip(width_entry, "Number of recent samples to display in the plot")

        apply_btn = ttk.Button(settings_frame, text="Apply", command=self.update_buffer_settings)
        apply_btn.grid(row=0, column=4, padx=(10, 0))

        clear_btn = ttk.Button(settings_frame, text="Clear Buffer", command=self.clear_buffer_only)
        clear_btn.grid(row=0, column=5, padx=(10, 0))
        ToolTip(clear_btn, "Clear only plot data buffer, preserve all channels and settings")

        ttk.Label(settings_frame, text="Plot Title:").grid(row=2, column=0, sticky=tk.W, padx=(0, 5), pady=(10, 0))
        self.title_var = tk.StringVar(value=self.plot_title_custom)
        title_entry = ttk.Entry(settings_frame, textvariable=self.title_var, width=30)
        title_entry.grid(row=2, column=1, columnspan=2, sticky=tk.W, pady=(10, 0))
        title_entry.bind('<Return>', self.on_title_changed)
        title_entry.bind('<FocusOut>', self.on_title_changed)
        ToolTip(title_entry, "Optional custom title for the plot (leave empty for default)")

        # X-Axis selection frame
        xaxis_frame = ttk.LabelFrame(content_frame, text="Set X-Axis", padding="10")
        xaxis_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(xaxis_frame, text="X-Axis:").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))

        self.xaxis_var = tk.StringVar(value="Sample Number")
        self.xaxis_combo = ttk.Combobox(xaxis_frame, textvariable=self.xaxis_var, width=20, state="readonly")
        self.xaxis_combo['values'] = ('Sample Number',)
        self.xaxis_combo.grid(row=0, column=1, sticky=tk.W)
        self.xaxis_combo.bind('<<ComboboxSelected>>', self.on_xaxis_changed)
        ToolTip(self.xaxis_combo, "Choose what data to display on the X-axis: sample number or any channel data")

        ttk.Label(xaxis_frame, text="Custom X-Axis Label:").grid(row=1, column=0, sticky=tk.W, padx=(0, 10), pady=(5, 0))
        self.xlabel_var = tk.StringVar(value=self.x_axis_custom_label)
        xlabel_entry = ttk.Entry(xaxis_frame, textvariable=self.xlabel_var, width=25)
        xlabel_entry.grid(row=1, column=1, sticky=tk.W, pady=(5, 0))
        xlabel_entry.bind('<Return>', self.on_x_label_changed)
        xlabel_entry.bind('<FocusOut>', self.on_x_label_changed)
        ToolTip(xlabel_entry, "Optional custom label for X-axis (leave empty for automatic)")

        ttk.Label(xaxis_frame, text="X-Axis Width (samples):").grid(row=2, column=0, sticky=tk.W, padx=(0, 10), pady=(5, 0))
        self.x_width_var = tk.StringVar(value=str(self.x_axis_width))
        x_width_entry = ttk.Entry(xaxis_frame, textvariable=self.x_width_var, width=10)
        x_width_entry.grid(row=2, column=1, sticky=tk.W, pady=(5, 0))
        x_width_entry.bind('<Return>', self.on_x_width_changed)
        ToolTip(x_width_entry, "Visible X-axis window width in samples")

        x_width_apply_btn = ttk.Button(xaxis_frame, text="Apply", command=self.on_x_width_changed)
        x_width_apply_btn.grid(row=2, column=2, sticky=tk.W, padx=(10, 0), pady=(5, 0))
        ToolTip(x_width_apply_btn, "Apply X-axis width and lock it until you zoom/pan")

        # Set Y-Axis frame
        self.channel_frame = ttk.LabelFrame(content_frame, text="Set Y-Axis", padding="10")
        self.channel_frame.pack(fill=tk.X, padx=10, pady=5)

        ylabel_frame = tk.Frame(self.channel_frame)
        ylabel_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(ylabel_frame, text="Custom Y-Axis Label:").pack(side=tk.LEFT, padx=(0, 5))
        self.ylabel_var = tk.StringVar(value=self.y_axis_custom_label)
        self.ylabel_entry = ttk.Entry(ylabel_frame, textvariable=self.ylabel_var, width=25)
        self.ylabel_entry.pack(side=tk.LEFT, padx=(0, 5))
        self.ylabel_entry.bind('<Return>', self.on_y_label_changed)
        self.ylabel_entry.bind('<FocusOut>', self.on_y_label_changed)

        # Channel rows sit directly in channel_frame; the outer canvas scrolls them
        self.channel_rows_frame = ttk.Frame(self.channel_frame)
        self.channel_rows_frame.pack(fill=tk.X)

        ttk.Label(self.channel_rows_frame, text="No channels detected").pack()

    def _on_plot_content_configure(self, event=None):
        """Keep the outer scroll region in sync with the content frame."""
        self._scroll._sync()

    def _on_plot_mousewheel(self, event):
        """Scroll this tab by one notch.

        Wheel *routing* - deciding which region an event belongs to, and
        leaving events over a Text or Spinbox alone - lives in
        widgets._WheelRouter. This stays a plain scroll so it keeps working
        with a bare event carrying only num/delta, which is how the scroll
        tests drive it.
        """
        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.plot_scroll_canvas.yview_scroll(delta, "units")

    def update_delimiter(self):
        """Update the delimiter based on selection"""
        delim_type = self.delimiter_var.get()
        if delim_type == "comma":
            self.delimiter = ','
        elif delim_type == "space":
            self.delimiter = ' '
        elif delim_type == "tab":
            self.delimiter = '\t'
        elif delim_type == "other":
            self.delimiter = self.custom_delim_entry.get() or ','

    def on_custom_delimiter_change(self, event=None):
        """Handle custom delimiter entry changes"""
        if self.delimiter_var.get() == "other":
            self.delimiter = self.custom_delim_entry.get() or ','

    def on_xaxis_changed(self, event=None):
        """Handle x-axis selection changes"""
        self.x_axis_selection = self.xaxis_var.get()
        
        # Update X-axis label if no custom label is set
        if not self.x_axis_custom_label.strip():
            self.update_x_axis_label()
        
        # Update plot display with new x-axis
        self.schedule_plot_update()

    def on_x_width_changed(self, event=None):
        """Handle X-axis width changes from the Set X-Axis panel"""
        try:
            new_x_width = int(self.x_width_var.get())
            if new_x_width < 1:
                new_x_width = 1
            elif new_x_width > 100000:
                new_x_width = 100000

            self.x_axis_width = new_x_width
            self.x_axis_width_locked = True
            self.x_width_var.set(str(new_x_width))
            self.schedule_plot_update()
        except ValueError:
            self.x_width_var.set(str(self.x_axis_width))

    def _on_plot_xrange_changed(self, *_args):
        """Track manual zoom/pan and release X-axis width lock on user override."""
        if self._applying_x_range:
            return

        try:
            x_range = None
            if _args:
                candidate = _args[-1]
                if isinstance(candidate, (tuple, list)) and len(candidate) == 2:
                    x_range = candidate
            if x_range is None:
                return

            if not x_range or len(x_range) != 2:
                return
            width = max(1, int(round(abs(x_range[1] - x_range[0]))))
            self.x_axis_width = width
            self._x_width_ui_dirty = True

            if self.x_axis_width_locked:
                self.x_axis_width_locked = False
                self._x_width_unlock_notice_pending = True
        except Exception:
            pass

    def update_buffer_settings(self, event=None):
        """Update buffer size and plot width settings"""
        try:
            # Validate and update buffer size
            new_buffer_size = int(self.buffer_size_var.get())
            if new_buffer_size < 10:
                new_buffer_size = 10
                self.buffer_size_var.set("10")
            elif new_buffer_size > 100000:
                new_buffer_size = 100000
                self.buffer_size_var.set("100000")
            
            # Validate and update plot width
            new_plot_width = int(self.plot_width_var.get())
            if new_plot_width < 10:
                new_plot_width = 10
                self.plot_width_var.set("10")
            elif new_plot_width > new_buffer_size:
                new_plot_width = new_buffer_size
                self.plot_width_var.set(str(new_buffer_size))
            
            # Update settings
            old_max_points = self.plot_max_points
            self.plot_max_points = new_buffer_size
            self.plot_width = new_plot_width
            
            # Update existing deques if buffer size changed
            if old_max_points != new_buffer_size:
                for ch in self.channels.values():
                    ch.data = deque(ch.data, maxlen=new_buffer_size)
            
            # Refresh plot display with throttling
            self.schedule_plot_update()
            
        except ValueError:
            # Reset to current values if invalid input
            self.buffer_size_var.set(str(self.plot_max_points))
            self.plot_width_var.set(str(self.plot_width))

    def on_x_label_changed(self, event=None):
        """Handle X-axis label override change"""
        self.x_axis_custom_label = self.xlabel_var.get()
        self.update_x_axis_label()

    def update_x_axis_label(self):
        """Update the X-axis label based on selection and custom override"""
        if self.plot_widget is not None:
            try:
                if PYQTGRAPH_AVAILABLE:
                    # Use custom label if provided, otherwise use default based on selection
                    if self.x_axis_custom_label.strip():
                        label = self.x_axis_custom_label
                    else:
                        if self.x_axis_selection == "Sample Number":
                            label = "Sample Number"
                        else:
                            # Use channel name or custom name if available
                            channel_name = self.x_axis_selection
                            ch = self.channels.get(channel_name)
                            label = ch.label if ch else channel_name
                    
                    self.plot_widget.setLabel('bottom', label, color=themes.CURRENT["plot_fg"])
            except Exception:
                pass

    def on_y_label_changed(self, event=None):
        """Handle Y-axis label override change"""
        self.y_axis_custom_label = self.ylabel_var.get()
        self.update_y_axis_label()

    def update_y_axis_label(self):
        """Update the Y-axis label based on custom override"""
        if self.plot_widget is not None:
            try:
                if PYQTGRAPH_AVAILABLE:
                    # Use custom label if provided, otherwise use default
                    if self.y_axis_custom_label.strip():
                        label = self.y_axis_custom_label
                    else:
                        label = "Value"  # Default Y-axis label
                    
                    self.plot_widget.setLabel('left', label, color=themes.CURRENT["plot_fg"])
            except Exception:
                pass

    def on_title_changed(self, event=None):
        """Handle plot title override change"""
        self.plot_title_custom = self.title_var.get()
        self.update_plot_title()

    def update_plot_title(self):
        """Update the plot title based on custom override"""
        if self.plot_widget is not None:
            try:
                if PYQTGRAPH_AVAILABLE:
                    # Use custom title if provided, otherwise use default
                    if self.plot_title_custom.strip():
                        title = self.plot_title_custom
                    else:
                        title = self.window_title
                    
                    self.plot_widget.setTitle(title, color=themes.CURRENT["plot_fg"]) # type: ignore
            except Exception:
                pass

    def parse_plot_data(self, raw_data: str):
        """Parse incoming data for plotting"""
        try:
            # Split by delimiter
            parts = raw_data.strip().split(self.delimiter)
            parsed_channels = {}
            
            for i, part in enumerate(parts):
                part = part.strip()
                if ':' in part:
                    # Named channel format "name:value"
                    name, value_str = part.split(':', 1)
                    name = name.strip()
                    try:
                        value = float(value_str.strip())
                        parsed_channels[name] = value
                    except ValueError:
                        continue
                else:
                    # Unnamed channel, use index
                    try:
                        value = float(part)
                        channel_name = f"Ch{i+1}"
                        parsed_channels[channel_name] = value
                    except ValueError:
                        continue
            
            # Update plot data (increment sample counter once per line)
            if parsed_channels:
                # Per plot, so clearing one plot does not shift another's x-axis
                self.sample_counter += 1
                self.update_plot_channels(parsed_channels, self.sample_counter)
                
        except Exception as e:
            # Report once rather than per line, so a broken parser is visible
            # without flooding the monitor.
            if not self._parse_error_reported:
                self._parse_error_reported = True
                self.app.log_message(
                    f"Error parsing data for plotting: {e}. Check the delimiter setting. "
                    "Further parse errors will not be reported.", "ERROR")

    def update_plot_channels(self, new_data: Dict[str, float], sample_number: int):
        """Update plot data structures with new channel data"""
        for channel_name, value in new_data.items():
            # Initialize channel if new
            if channel_name not in self.channels:
                # A single malformed line can carry thousands of fields; each
                # new channel costs a deque plus a row of six widgets, so cap
                # it rather than letting the UI lock up.
                if len(self.channels) >= self.MAX_CHANNELS:
                    if not self._channel_cap_reported:
                        self._channel_cap_reported = True
                        self.app.log_message(
                            f"Channel limit of {self.MAX_CHANNELS} reached - ignoring "
                            f"'{channel_name}' and any further new channels. "
                            "Check the delimiter setting, then use Clear Plot Data to reset.",
                            "ERROR")
                    continue

                color_index = len(self.channels) % len(self.plot_colors)
                self.channels[channel_name] = Channel(
                    name=channel_name,
                    data=deque(maxlen=self.plot_max_points),
                    color=self.plot_colors[color_index],
                    # Remember the palette slot so this channel keeps its hue
                    # position when the theme changes.
                    color_index=color_index,
                    thickness=themes.CURRENT["default_line_width"],
                    # Lines only by default. A symbol turns the curve into a
                    # ScatterPlotItem, which rasterises one pixmap per point:
                    # 500 points x 5 channels x 30 fps is 75k symbol draws/s.
                    dot_size=0,
                )

                self.add_channel_control(channel_name)
                
                # Update x-axis dropdown with new channel
                self.update_xaxis_dropdown()
                
                # Add plot curve if the plot window is open
                self._create_curve(channel_name)
            
            # Add data point using the provided sample number (same for all channels in this line)
            self.channels[channel_name].data.append((sample_number, value))
        
        # Schedule throttled plot update instead of immediate update
        self.schedule_plot_update()

    def add_channel_control(self, channel_name: str):
        """Build the Plot tab row of controls for one channel"""
        ch = self.channels[channel_name]

        # Clear only the "No channels detected" message if it's the first channel
        if len(self.channels) == 1:
            # Only destroy the "No channels detected" label
            widgets_to_remove = []
            for widget in self.channel_rows_frame.winfo_children():
                if isinstance(widget, ttk.Label) and widget.cget("text") == "No channels detected":
                    widgets_to_remove.append(widget)

            for widget in widgets_to_remove:
                widget.destroy()

        # Create frame for this channel's controls
        channel_control_frame = ttk.Frame(self.channel_rows_frame, relief="solid", borderwidth=1, padding=3)
        channel_control_frame.pack(fill=tk.X, padx=2, pady=2)
        
        # Channel visibility checkbox
        var = tk.BooleanVar(value=ch.visible)
        checkbox = ttk.Checkbutton(
            channel_control_frame,
            text=channel_name,
            variable=var,
            command=lambda: self.toggle_channel_visibility(channel_name, var.get())
        )
        checkbox.pack(side=tk.LEFT, padx=(0, 10))
        
        # Channel name override field
        ttk.Label(channel_control_frame, text="Name:").pack(side=tk.LEFT, padx=(0, 2))
        name_var = tk.StringVar(value=channel_name)
        name_entry = ttk.Entry(channel_control_frame, textvariable=name_var, width=8)
        name_entry.pack(side=tk.LEFT, padx=(0, 10))
        name_entry.bind('<Return>', lambda e: self.update_channel_name(channel_name, name_var.get()))
        name_entry.bind('<FocusOut>', lambda e: self.update_channel_name(channel_name, name_var.get()))
        
        # Line thickness control
        ttk.Label(channel_control_frame, text="Thickness:").pack(side=tk.LEFT, padx=(0, 2))
        thickness_var = tk.StringVar(value=str(ch.thickness))
        thickness_spinbox = ttk.Spinbox(
            channel_control_frame,
            from_=1, to=10, width=3,
            textvariable=thickness_var,
            command=lambda: self.update_channel_thickness(channel_name, thickness_var.get())
        )
        thickness_spinbox.pack(side=tk.LEFT, padx=(0, 10))
        thickness_spinbox.bind('<Return>', lambda e: self.update_channel_thickness(channel_name, thickness_var.get()))
        thickness_spinbox.bind('<FocusOut>', lambda e: self.update_channel_thickness(channel_name, thickness_var.get()))
        
        # Color selection button
        color_btn = tk.Button(
            channel_control_frame,
            text="Color",
            width=8,
            command=lambda: self.choose_channel_color(channel_name)
        )
        color_btn.pack(side=tk.LEFT, padx=(0, 10))
        # The swatch carries the channel colour - the theme walk must skip it
        self.app.themes.exempt(color_btn)

        # Dot size control
        ttk.Label(channel_control_frame, text="Dot Size:").pack(side=tk.LEFT, padx=(0, 2))
        dot_size_var = tk.StringVar(value=str(ch.dot_size))
        dot_size_spinbox = ttk.Spinbox(
            channel_control_frame,
            from_=0, to=20, width=3,
            textvariable=dot_size_var,
            command=lambda: self.update_channel_dot_size(channel_name, dot_size_var.get())
        )
        dot_size_spinbox.pack(side=tk.LEFT, padx=(0, 10))
        dot_size_spinbox.bind('<Return>', lambda e: self.update_channel_dot_size(channel_name, dot_size_var.get()))
        dot_size_spinbox.bind('<FocusOut>', lambda e: self.update_channel_dot_size(channel_name, dot_size_var.get()))
        
        # Show line toggle
        line_var = tk.BooleanVar(value=ch.show_line)
        line_checkbox = ttk.Checkbutton(
            channel_control_frame,
            text="Show Line",
            variable=line_var,
            command=lambda: self.toggle_channel_line(channel_name, line_var.get())
        )
        line_checkbox.pack(side=tk.LEFT, padx=(0, 5))
        
        # Keep the row's widgets on the Channel, so clearing self.channels
        # releases them - the old per-channel setattr()s were never deleted.
        ch.row = channel_control_frame
        ch.visible_var = var
        ch.name_var = name_var
        ch.thickness_var = thickness_var
        ch.dot_size_var = dot_size_var
        ch.line_var = line_var
        ch.color_btn = color_btn
        # Rightmost control in the row - the first thing clipped on a
        # narrow window, so the resize test needs a handle on it.
        ch.line_checkbox = line_checkbox

        # Theme only the row just built. Restyling self.channel_frame here
        # walked every previously added row too, making the cost of adding N
        # channels O(N^2) - and this runs on the receive path.
        self.app.themes.restyle(channel_control_frame)
        self.update_color_button_appearance(channel_name)

    def toggle_channel_visibility(self, channel_name: str, visible: bool):
        """Toggle visibility of a plot channel"""
        ch = self.channels.get(channel_name)
        if ch is None:
            return
        ch.visible = visible
        
        self._rebuild_legend()
        
        self.schedule_plot_update()  # Use throttled update

    def schedule_plot_update(self):
        """Schedule a throttled plot update to improve performance"""
        # Called once per received line, so bail before doing any work when
        # there is nothing to redraw.
        if self.plot_widget is None or self.plot_paused:
            return

        current_time = time.time()
        
        # If enough time has passed since last update, update immediately
        if current_time - self.last_plot_update >= self.plot_update_interval:
            self.update_plot_display()
            # After, not before: if a redraw takes longer than the interval
            # this keeps the next one a full interval away.
            self.last_plot_update = time.time()
            self.pending_plot_update = False
        elif not self.pending_plot_update:
            # Schedule an update for later
            self.pending_plot_update = True
            delay_ms = int((self.plot_update_interval - (current_time - self.last_plot_update)) * 1000)
            self._plot_timer = self.app.root.after(delay_ms, self.execute_pending_plot_update)

    def execute_pending_plot_update(self):
        """Execute a pending plot update"""
        if self.pending_plot_update:
            self.update_plot_display()
            self.last_plot_update = time.time()
            self.pending_plot_update = False

    def update_xaxis_dropdown(self):
        """Update x-axis dropdown with available channels"""
        try:
            # Get current selection
            current_selection = self.xaxis_var.get()
            
            # Build list of options: Sample Number + all channels
            options = ['Sample Number']
            for channel_name in sorted(self.channels):
                display_name = self.channels[channel_name].label
                options.append(display_name)
            
            # Update combobox values
            self.xaxis_combo['values'] = options
            
            # Restore selection if it still exists, otherwise default to Sample Number
            if current_selection not in options:
                self.xaxis_var.set('Sample Number')
                self.x_axis_selection = 'Sample Number'
        except Exception:
            pass

    def update_channel_thickness(self, channel_name: str, thickness_str: str):
        """Update line thickness for a channel"""
        try:
            thickness = int(thickness_str)
            thickness = max(1, min(10, thickness))  # Clamp between 1 and 10
            self.channels[channel_name].thickness = thickness
            
            self._apply_channel_style(channel_name)

        except ValueError:
            # Reset to current value if invalid input
            ch = self.channels.get(channel_name)
            thickness_var = ch.thickness_var if ch else None
            ch = self.channels.get(channel_name)
            if thickness_var and ch:
                thickness_var.set(str(ch.thickness))

    def update_channel_dot_size(self, channel_name: str, dot_size_str: str):
        """Update dot size for a channel"""
        try:
            dot_size = int(dot_size_str)
            dot_size = max(0, min(20, dot_size))  # Clamp between 0 and 20
            self.channels[channel_name].dot_size = dot_size
            
            self._apply_channel_style(channel_name)

        except ValueError:
            # Reset to current value if invalid input
            ch = self.channels.get(channel_name)
            dot_size_var = ch.dot_size_var if ch else None
            ch = self.channels.get(channel_name)
            if dot_size_var and ch:
                dot_size_var.set(str(ch.dot_size))

    def toggle_channel_line(self, channel_name: str, show_line: bool):
        """Toggle line visibility for a channel"""
        self.channels[channel_name].show_line = show_line
        self._apply_channel_style(channel_name)

    def update_channel_name(self, channel_name: str, new_name: str):
        """Update custom display name for a channel"""
        if new_name.strip():
            self.channels[channel_name].display_name = new_name.strip()
            
            # Update X-axis label if this channel is selected as X-axis and no custom label is set
            if (self.x_axis_selection == channel_name and not self.x_axis_custom_label.strip()):
                self.update_x_axis_label()
            
            # Update x-axis dropdown with new display name
            self.update_xaxis_dropdown()
            
            self._rebuild_legend()
        else:
            # Reset to original name if empty
            ch = self.channels.get(channel_name)
            name_var = ch.name_var if ch else None
            ch = self.channels.get(channel_name)
            if name_var and ch:
                name_var.set(ch.label)

    def choose_channel_color(self, channel_name: str):
        """Open color chooser dialog for a channel"""
        ch = self.channels.get(channel_name)
        if ch is None:
            return  # channel was cleared while its row was still on screen
        current_color = ch.color
        color = colorchooser.askcolor(color=current_color, title=f"Choose color for {channel_name}")
        
        if color[1]:  # color[1] is the hex color string
            ch.color = color[1]
            # Hand-picked colours survive theme switches untouched
            ch.color_is_user = True

            # Update button appearance
            self.update_color_button_appearance(channel_name)
            
            self._apply_channel_style(channel_name)

    def update_color_button_appearance(self, channel_name: str):
        """Update the color button appearance to show the selected color"""
        ch = self.channels.get(channel_name)
        color_btn = ch.color_btn if ch else None
        if color_btn:
            try:
                # Set button background to match the line color, and pick a
                # label colour that stays readable on top of it.
                color = ch.color
                color_btn.config(text="Color", background=color,
                                 foreground=contrast_fg_for(color),
                                 activebackground=color,
                                 activeforeground=contrast_fg_for(color),
                                 relief="flat", bd=1,
                                 highlightbackground=themes.CURRENT["border"])
            except Exception:
                pass

    def toggle_plot_pause(self):
        """Toggle pause/resume state for plot updates"""
        self.plot_paused = not self.plot_paused
        
        if self.plot_paused:
            self.pause_plot_btn.config(text="Resume Plot")
            self._status("Plot paused - data still being collected")
        else:
            self.pause_plot_btn.config(text="Pause Plot")
            self._status("Plot window is open and updating in real-time")
            # Update plot with accumulated data when resuming
            self.schedule_plot_update()  # Use throttled update

    def update_plot_display(self):
        """Push the current buffers onto the plot curves.

        Runs up to ~30x/s, so everything here is per-frame cost. Only the last
        plot_width samples are ever needed, so they are sliced straight out of
        the deque with islice rather than materialising the whole buffer (which
        can hold up to 100,000 points per channel) and discarding most of it.
        """
        if self.plot_widget is None or self.plot_paused:
            return

        try:
            window_x_data = None

            # Resolve the x-axis channel once per frame, not per curve
            x_axis_channel = None
            if self.x_axis_selection != "Sample Number":
                for name, ch in self.channels.items():
                    if ch.label == self.x_axis_selection:
                        x_axis_channel = name
                        break

            # Hoisted out of the per-curve loop: this used to be re-materialised
            # once for every channel, so N channels copied the same deque N
            # times per frame.
            x_tail = None
            if x_axis_channel and x_axis_channel in self.channels:
                x_tail = self._tail(self.channels[x_axis_channel].data)

            for ch in self.channels.values():
                curve = ch.curve
                if curve is None:
                    continue

                if not ch.visible:
                    # setData() is not free - it reconfigures the item, drops
                    # the cached bounds and triggers an auto-range recompute -
                    # so only do it on the transition, not every frame.
                    if ch.has_data:
                        curve.setData([], [])
                        ch.has_data = False
                    continue

                y_tail = self._tail(ch.data)
                if not y_tail:
                    if ch.has_data:
                        curve.setData([], [])
                        ch.has_data = False
                    continue

                if x_tail is not None:
                    # Pair the two channels by position from the newest end
                    n = min(len(y_tail), len(x_tail))
                    if n == 0:
                        if ch.has_data:
                            curve.setData([], [])
                            ch.has_data = False
                        continue
                    x_data = [p[1] for p in x_tail[-n:]]
                    y_data = [p[1] for p in y_tail[-n:]]
                else:
                    # Default: sample number on x
                    x_data, y_data = zip(*y_tail)

                curve.setData(x_data, y_data)
                ch.has_data = True

                if window_x_data is None:
                    window_x_data = x_data

            self._apply_x_axis_window(window_x_data)

        except Exception as e:
            # Report once. This handler previously discarded every plotting
            # failure with no trace at all, so a channel could silently stop
            # updating for the rest of the session.
            if not self._plot_error_reported:
                self._plot_error_reported = True
                self.app.log_message(f"Error updating plot: {e}", "ERROR")

    def _apply_x_axis_window(self, x_data):
        """Apply the visible X-axis limits without changing plotted samples."""
        if self.plot_widget is None or not x_data or not self.x_axis_width_locked:
            return

        try:
            width = max(1, int(self.x_axis_width))
            x_values = list(x_data)
            start_index = max(0, len(x_values) - width)
            x_min = x_values[start_index]
            x_max = x_values[-1]

            if x_min == x_max:
                x_min -= 0.5
                x_max += 0.5

            plot_item = self.plot_widget.getPlotItem()
            plot_item.enableAutoRange(axis='x', enable=False)
            self._applying_x_range = True
            try:
                plot_item.setXRange(x_min, x_max, padding=0)
            finally:
                self._applying_x_range = False
        except Exception:
            self._applying_x_range = False
            pass

    def _tail(self, samples):
        """Return the newest plot_width samples of a deque as a list.

        islice avoids building an intermediate copy of the whole buffer just to
        throw most of it away. Decimation is left to pyqtgraph's own
        setDownsampling, which is numpy-based and preserves the min/max
        envelope instead of aliasing spikes away like a [::step] slice.
        """
        n = len(samples)
        if n == 0:
            return []
        width = self.plot_width
        if n <= width:
            return list(samples)
        return list(itertools.islice(samples, n - width, n))

    def show_plot_window(self):
        """Show the plot window"""
        if not PYQTGRAPH_AVAILABLE:
            self._status("PyQtGraph not available. Please install: pip install pyqtgraph PyQt5")
            return
            
        if self.app.qt_app is None:
            try:
                self.qt_app = QtWidgets.QApplication.instance()
                if self.app.qt_app is None:
                    self.qt_app = QtWidgets.QApplication([])
            except Exception as e:
                self._status(f"Error creating Qt application: {str(e)}")
                return
        
        try:
            if self.plot_window is None:
                if PYQTGRAPH_AVAILABLE:
                    # Import the real Qt classes for inheritance
                    from PyQt5.QtWidgets import QMainWindow
                    
                    # Create a custom QMainWindow class with proper close event handling
                    class PlotWindow(QMainWindow):
                        def __init__(self, parent_gui):
                            super().__init__()
                            self.parent_gui = parent_gui
                        
                        def closeEvent(self, event):
                            self.parent_gui.plot_window = None
                            self.parent_gui.plot_widget = None
                            self.parent_gui._drop_curves()
                            self.parent_gui.plot_legend = None
                            self.parent_gui._status(
                                "Click 'Show Plot Window' to display real-time plots")
                            event.accept()
                    
                    self.plot_window = PlotWindow(self)
                
                self.plot_window.setWindowTitle(self.window_title)
                self.plot_window.setGeometry(
                    100, 100,
                    scaling.px(self.app.ui_scale, 800), scaling.px(self.app.ui_scale, 600))
                
                self.plot_widget = pg.PlotWidget()
                try:
                    self.plot_widget.getPlotItem().getViewBox().sigXRangeChanged.connect(
                        self._on_plot_xrange_changed)
                except Exception:
                    pass
                
                # Set initial plot title
                self.update_plot_title()
                
                # Set initial Y-axis label
                self.update_y_axis_label()
                
                # Set initial X-axis label
                self.update_x_axis_label()
                
                self.plot_widget.showGrid(True, True)

                # Let pyqtgraph do the data reduction. Clipping to the visible
                # range and peak-preserving downsampling are numpy-based and
                # keep spikes visible, unlike a [::step] slice.
                try:
                    self.plot_widget.setClipToView(True)
                    self.plot_widget.setDownsampling(auto=True, mode='peak')
                except Exception:
                    pass

                self.plot_legend = self._make_legend()

                # Colour the freshly built window for the active theme
                self._apply_pyqtgraph_theme(themes.CURRENT)

                self.plot_window.setCentralWidget(self.plot_widget)
                
                # Recreate all plot curves
                for channel_name in self.channels:
                    self._create_curve(channel_name)

                # Update with current data
                self.update_plot_display()
            
            self.plot_window.show()
            self.plot_window.raise_()
            self._status("Plot window is open and updating in real-time")
            
        except Exception as e:
            # Roll back, otherwise plot_window stays non-None with a None
            # plot_widget and every later click skips the init block above and
            # shows a permanently empty window.
            self.plot_window = None
            self.plot_widget = None
            self.plot_legend = None
            self._drop_curves()
            self._status(f"Error creating plot window: {str(e)}")

    def clear_plot_data(self):
        """Discard all channels: their data, per-channel settings, and UI rows.

        Axis labels, the plot title and the buffer settings are preserved.
        """
        # One structure to clear, so it cannot be partially done - and the Tk
        # variables each Channel owns go with it. That is what used to leak:
        # the six dynamically-named attributes per channel were never released.
        self.channels.clear()

        # Reset global sample counter
        self.sample_counter = 0
        self._channel_cap_reported = False
        self._parse_error_reported = False
        
        # Clear the channel rows. The Y-axis label frame lives outside the
        # scrollable channel_rows_frame (it's a sibling, not a child), so it
        # is untouched by this and needs no special-casing to preserve.
        for widget in self.channel_rows_frame.winfo_children():
            widget.destroy()

        ttk.Label(self.channel_rows_frame, text="No channels detected").pack()

        # Reset the scroll position now that the list is empty
        try:
            self.plot_scroll_canvas.yview_moveto(0)
            self.plot_scroll_canvas.xview_moveto(0)
        except Exception:
            pass

        self.app.themes.restyle(self.channel_frame)

        # Clear plot if window exists but preserve settings
        if self.plot_widget is not None:
            # Remove existing legend first
            if hasattr(self, 'plot_legend') and self.plot_legend is not None:
                try:
                    self.plot_legend.setParentItem(None)
                except Exception:
                    pass
                self.plot_legend = None
            
            self.plot_widget.clear()
            
            # Recreate legend after clearing. This used to construct a bare
            # LegendItem, so the legend lost its theme colours after every
            # Clear Plot Data - dark text on a dark background until the next
            # theme switch.
            self.plot_legend = self._make_legend()
        
        self._status("Plot data and channels cleared - axis labels preserved")

    def clear_buffer_only(self):
        """Clear only plot data buffer, preserve all channels and settings"""
        # Keep one sample for each channel to preserve channel structure
        for ch in self.channels.values():
            if ch.data:
                # Keep only the last sample
                last_value = ch.data[-1]
                ch.data.clear()
                ch.data.append(last_value)
        
        # Reset global sample counter but keep it at 1 if we have data
        if self.channels:
            self.sample_counter = 1
        else:
            self.sample_counter = 0
        
        # Update plot display to show only the remaining samples
        if self.plot_widget is not None:
            self.update_plot_display()
        
        self._status("Buffer cleared - channels preserved")
