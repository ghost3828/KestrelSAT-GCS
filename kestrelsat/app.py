"""Main application window for the KestrelSAT Ground Control Station."""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Error: pyserial not installed. Run: pip install pyserial")
    raise SystemExit(1)
try:
    import pyqtgraph as pg
    from PyQt5 import QtCore, QtWidgets
    # NOTE: background/foreground are set by ThemeManager once settings are loaded.
    PYQTGRAPH_AVAILABLE = True
except ImportError as e:
    print(f"Warning: PyQtGraph not available: {e}")
    print("Plotting functionality will be disabled.")
    print("Install with: pip install pyqtgraph PyQt5")
    PYQTGRAPH_AVAILABLE = False
    # No stand-in objects: every site that touches pg or QtWidgets is already
    # behind a PYQTGRAPH_AVAILABLE check, and show_plot_window() returns early,
    # so plot_widget stays None and the plotting paths are never entered.
    pg = None
    QtCore = None
    QtWidgets = None

import sys
import threading
import queue
import time
import itertools
import datetime
import json
import os
import math
import random
import re
from collections import deque
from typing import Optional, Dict, Any

from . import themes
from .themes import (
    SETTINGS_FILE, DEFAULT_THEME, THEME_ORDER, THEMES, contrast_fg_for,
)
from .channels import Channel
from . import scaling
from .theming import ThemeManager
from .widgets import ToolTip


class SerialGUI:
    # -- Tunables ---------------------------------------------------------
    # Discard the accumulated receive buffer if no line ending shows up within
    # this many bytes, so a misconfigured link cannot exhaust memory.
    MAX_SERIAL_BUFFER = 1 << 20  # 1 MiB
    # Upper bound on channels created from parsed data. One malformed line with
    # thousands of fields would otherwise build thousands of widget rows.
    MAX_CHANNELS = 64
    # Lines retained in the serial monitor. Tk's Text widget degrades badly
    # once it holds hundreds of thousands of lines.
    MAX_MONITOR_LINES = 5000
    # Reader -> GUI queue. Bounded so a stalled UI cannot exhaust memory.
    RX_QUEUE_MAX = 4096
    RX_PUMP_INTERVAL_MS = 20
    # Chunks drained per pump, so one burst cannot monopolise the event loop.
    RX_PUMP_BUDGET = 200
    # Legend placement, negative offset anchors it to the top right.
    LEGEND_OFFSET = (-70, 30)

    # Window size at 100% scale; scaled up on high-DPI displays.
    BASE_GEOMETRY = "800x780"

    def __init__(self, root: tk.Tk):
        self.root = root

        # Settings and display scale come first: every widget below is built
        # at the scaled font size, so this cannot wait until after setup_gui().
        self.settings = self.load_settings()
        self.ui_scale_setting = self.settings.get('ui_scale', 'auto')
        self.ui_scale = scaling.resolve(self.ui_scale_setting, root)
        scaling.apply_fonts(root, self.ui_scale)
        # Derived from the scaled named fonts, so these follow the UI size
        # instead of being pinned to a literal point size.
        self.ui_font_bold = scaling.derive_font(root, weight="bold")
        self.ui_font_small_bold = scaling.derive_font(root, weight="bold", size_delta=-1)

        self.root.title("USAFA ASTRO - KestrelSAT Ground Control Station v3.0.0")
        self.root.geometry(scaling.scale_geometry(self.BASE_GEOMETRY, self.ui_scale))
        self.root.resizable(True, True)

        # Set window and taskbar icon. Look next to the package, and next to
        # the frozen executable when running from a PyInstaller build.
        for _base in (os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      getattr(sys, "_MEIPASS", None)):
            if not _base:
                continue
            try:
                _icon_img = tk.PhotoImage(file=os.path.join(_base, "KestrelSAT_logo.png"))
                self.root.iconphoto(True, _icon_img)
                self._icon_img = _icon_img  # keep a reference so GC does not collect it
                break
            except Exception:
                continue
        
        # Shutdown state. Timer ids are kept so on_closing() can cancel them
        # before destroying the root.
        self._closing = False
        self._sps_timer = None
        self._filesize_timer = None
        self._plot_timer = None
        self._rx_timer = None

        # Reader -> GUI transport (see _post_rx / _pump_rx)
        self._rx_queue = queue.Queue(maxsize=self.RX_QUEUE_MAX)
        self._rx_dropped = 0
        self._rx_drop_reported = False
        self._log_dirty = False
        self._log_error_reported = False
        self._plot_error_reported = False
        # Tracks whether each curve currently holds data, so hidden curves are
        # cleared once rather than on every frame.
        # Per-channel state, keyed by channel name. See the Channel dataclass.
        self.channels: Dict[str, Channel] = {}

        # Serial connection
        self.serial_connection: Optional[serial.Serial] = None
        self.is_connected = False
        self.read_thread: Optional[threading.Thread] = None
        # Replaced with a fresh Event on every connect, so a worker that
        # outlives its join timeout can never be revived.
        self.stop_reading = threading.Event()
        self.serial_buffer = ""  # Buffer for accumulating partial serial data

        # One-shot flags so recurring failures are reported once, not per line
        self._parse_error_reported = False
        self._channel_cap_reported = False

        # Test mode for simulation
        self.test_mode = False
        self.test_counter = 0
        
        # Logging functionality
        self.logging_active = False
        self.log_file_path = None
        self.log_file_handle = None
        
        # Plotting functionality
        self.plot_colors = list(themes.CURRENT["plot_palette"])  # refreshed on theme change
        self.plot_max_points = 1000
        self.plot_width = 500  # Number of samples to display in plot
        self.delimiter = ','
        self.plot_paused = False  # Flag to pause/resume plotting
        self.x_axis_selection = "Sample Number"  # Default x-axis is sample number
        self.x_axis_custom_label = ""  # Custom X-axis label override
        self.y_axis_custom_label = ""  # Custom Y-axis label override
        self.plot_title_custom = ""  # Custom plot title override
        
        # Appearance / theme. Applied once here so that the ttk style database
        # and the Tk option database (menus, combobox popdowns) are already
        # correct while setup_gui() builds the widgets.
        self.theme_name = self.settings.get('theme', DEFAULT_THEME)
        if self.theme_name not in THEMES:
            self.theme_name = DEFAULT_THEME
        self.theme_var = tk.StringVar(value=self.theme_name)
        self.themes = ThemeManager(root, self.theme_name)
        self.themes.apply()
        
        # Sample tracking for status bar
        self.samples_received = 0
        self.last_sample_time = time.time()
        self.current_sps = 0
        
        # Global sample counter for plot x-axis (maintains sample number since boot)
        self.global_sample_counter = 0
        
        # Flag to track first line received (for clearing partial data)
        self.first_line_received = False
        
        # Plot update optimization
        self.last_plot_update = 0
        self.plot_update_interval = 0.033  # ~30 FPS (33ms between updates)
        self.pending_plot_update = False
        
        # Create logs directory if it doesn't exist
        self.logs_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(self.logs_dir, exist_ok=True)
        
        # Setup GUI
        self.setup_gui()

        # Second pass: now that the widget tree exists, walk it for the classic
        # tk widgets and run the app-specific fixups.
        self.themes.add_listener(self._on_theme_applied)
        self.themes.apply()

        self.update_port_list()
        
        # Initialize status indicator
        self.update_status_indicator(False)
        
        # Start file size update timer
        self.update_file_size_display()
        
        # Start SPS update timer
        self.update_sps_display()

        # Start draining the reader queue
        self._pump_rx()

        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def setup_gui(self):
        """Setup the GUI layout"""
        # Create menu bar
        self.create_menu_bar()
        
        # Create top frame for logging controls
        self.create_top_frame()

        # Create status bar before the notebook. Tk's packer allocates
        # cavity space in the order widgets are packed, not by "side" alone:
        # the notebook below is packed with fill=BOTH, expand=True, which
        # claims the entire remaining cavity at the moment it is packed. If
        # the status bar were packed afterwards, it would find no cavity
        # left and be squeezed to nothing regardless of its side="bottom".
        # Reserving its slice first makes the notebook fill exactly what's
        # left, so the status bar always stays visible.
        self.create_status_bar()

        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Create Connection tab
        self.connection_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.connection_frame, text="Connection")

        # Create Plot tab (empty for now)
        self.plot_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.plot_frame, text="Plot")

        # Setup Connection tab content
        self.create_connection_content()

        # Setup Plot tab content
        self.create_plot_content()
    
    def create_menu_bar(self):
        """Create the menu bar"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Quit", command=self.on_closing, accelerator="Ctrl+Q")
        
        # Options menu
        options_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Options", menu=options_menu)

        # Appearance submenu - the fast path for switching themes
        appearance_menu = tk.Menu(options_menu, tearoff=0)
        options_menu.add_cascade(label="Appearance", menu=appearance_menu)
        for theme_key in THEME_ORDER:
            appearance_menu.add_radiobutton(
                label=THEMES[theme_key]["display_name"],
                value=theme_key,
                variable=self.theme_var,
                command=self.on_theme_selected
            )

        options_menu.add_separator()
        options_menu.add_command(label="Preferences", command=self.show_preferences)
        options_menu.add_separator()
        options_menu.add_command(label="Reset Settings", command=self.reset_settings)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)
        help_menu.add_separator()
        help_menu.add_command(label="User Guide", command=self.show_user_guide)
        
        # Bind keyboard shortcut for quit
        self.root.bind_all("<Control-q>", lambda e: self.on_closing())
    
    def create_status_bar(self):
        """Create status bar at the bottom of the window"""
        # Status bar frame
        self.status_bar = ttk.Frame(self.root, relief="sunken", borderwidth=1)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=2, pady=2)
        
        # Left side - plot status
        self.plot_status_label = ttk.Label(
            self.status_bar, text="Click 'Show Plot Window' to display real-time plots")
        self.plot_status_label.pack(side=tk.LEFT, padx=5, pady=2)
        
        # Right side - samples per second
        self.sps_label = ttk.Label(self.status_bar, text="(0 SPS)")
        self.sps_label.pack(side=tk.RIGHT, padx=5, pady=2)
    
    def update_sps_display(self):
        """Update the samples per second display"""
        current_time = time.time()
        time_elapsed = current_time - self.last_sample_time
        
        if time_elapsed >= 1.0:  # Update every second
            self.current_sps = self.samples_received / time_elapsed
            self.sps_label.config(text=f"({int(self.current_sps)} SPS)")
            
            # Reset counters
            self.samples_received = 0
            self.last_sample_time = current_time
        
        # Schedule next update
        # The reading only changes once a second, so there is nothing to gain
        # from waking 10x more often than that.
        self._sps_timer = self.root.after(1000, self.update_sps_display)

    def create_top_frame(self):
        """Create top frame with status and logging controls"""
        top_frame = ttk.Frame(self.root)
        top_frame.pack(fill=tk.X, padx=10, pady=(10, 0))
        
        # Connection status on the left
        status_outer_frame = ttk.Frame(top_frame)
        status_outer_frame.pack(side=tk.LEFT)
        
        status_frame = ttk.Frame(status_outer_frame, relief="solid", borderwidth=1, padding=5)
        status_frame.pack()
        
        # Status indicator (colored circle)
        _dot = scaling.px(self.ui_scale, 16)
        self.status_canvas = tk.Canvas(status_frame, width=_dot, height=_dot, highlightthickness=0)
        self.status_canvas.pack(side=tk.LEFT, padx=(0, 8), pady=2)
        
        # Draw initial red circle (disconnected)
        _inset = scaling.px(self.ui_scale, 2)
        self.status_circle = self.status_canvas.create_oval(
            _inset, _inset, _dot - _inset, _dot - _inset,
            fill=themes.CURRENT["error"], outline=themes.CURRENT["error_dim"])
        
        # Status text
        self.status_var = tk.StringVar()
        self.status_var.set("Disconnected")
        # NOTE: named conn_status_label so it is not shadowed by the bottom
        # status bar's self.plot_status_label, which is created later.
        self.conn_status_label = ttk.Label(status_frame, textvariable=self.status_var, font=self.ui_font_bold)
        self.conn_status_label.pack(side=tk.LEFT)
        
        # Logging controls on the right
        logging_outer_frame = ttk.Frame(top_frame)
        logging_outer_frame.pack(side=tk.RIGHT)
        
        logging_frame = ttk.Frame(logging_outer_frame)
        logging_frame.pack()
        
        self.logging_btn = ttk.Button(logging_frame, text="Start Logging", command=self.toggle_logging)
        self.logging_btn.pack(side=tk.LEFT, padx=(0, 10))
        ToolTip(self.logging_btn, "Start or stop continuous logging of all received data to a file")
        
        # Logging status with border
        log_status_frame = ttk.Frame(logging_frame, relief="solid", borderwidth=1, padding=5)
        log_status_frame.pack(side=tk.LEFT, padx=(0, 0))
        
        self.log_status_var = tk.StringVar()
        self.log_status_var.set("Not logging")
        self.log_status_label = ttk.Label(log_status_frame, textvariable=self.log_status_var, font=self.ui_font_bold)
        self.log_status_label.pack()
        self._refresh_log_status_color()
    
    def update_status_indicator(self, connected: bool):
        """Update the connection status indicator color"""
        c = themes.CURRENT
        fill, outline = (c["ok"], c["ok_dim"]) if connected else (c["error"], c["error_dim"])
        try:
            self.status_canvas.itemconfig(self.status_circle, fill=fill, outline=outline)
        except tk.TclError:
            pass

    def _refresh_log_status_color(self):
        """Colour the logging status label for the active theme and state"""
        c = themes.CURRENT
        try:
            self.log_status_label.config(
                foreground=c["ok"] if self.logging_active else c["error"])
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------
    def _configure_text_tags(self, c: Dict[str, Any]):
        """Colour the serial monitor message tags.

        tag_config is retroactive - Tk stores tag ranges by name - so the
        history already in the widget recolours in place, with no re-insert
        and no loss of scroll position.
        """
        try:
            self.received_text.tag_config("RECEIVED", foreground=c["log_received"])
            self.received_text.tag_config("SENT", foreground=c["log_sent"])
            self.received_text.tag_config("SYSTEM", foreground=c["log_system"],
                                          font=self.ui_font_small_bold)
            self.received_text.tag_config("ERROR", foreground=c["log_error"],
                                          font=self.ui_font_small_bold)
        except tk.TclError:
            pass

    def on_theme_selected(self, *_args):
        """Apply and persist the theme chosen from the menu or Preferences"""
        name = self.theme_var.get()
        if name not in THEMES:
            return
        self.theme_name = name
        self.themes.apply(name)
        self._write_settings({'theme': name})

    def on_ui_scale_selected(self, *_args):
        """Apply and persist the display scale chosen in Preferences."""
        value = self._scale_labels.get(self.scale_var.get())
        if value is None:
            return
        self.ui_scale_setting = value
        self.ui_scale = scaling.resolve(value, self.root)

        # Font changes are live: the named fonts drive every ttk widget, and
        # ui_font_bold / ui_font_small_bold are derived from them. Pixel
        # dimensions fixed at build time (the window itself, the status dot)
        # keep their old size until restart, which is why the dialog says so.
        scaling.apply_fonts(self.root, self.ui_scale)
        self.ui_font_bold.configure(**scaling.derive_font(self.root, weight="bold").actual())
        self.ui_font_small_bold.configure(
            **scaling.derive_font(self.root, weight="bold", size_delta=-1).actual())
        self._configure_text_tags(themes.CURRENT)

        self._write_settings({'ui_scale': value})
        self.log_message(
            f"Display scale set to {self.ui_scale:.0%}. "
            "Restart to resize the window and status indicator to match.", "SYSTEM")

    def _on_theme_applied(self, c: Dict[str, Any]):
        """Fixups the generic widget walk cannot cover. Order matters."""
        self.plot_colors = list(c["plot_palette"])
        self._configure_text_tags(c)
        self._remap_auto_channel_colors(c)
        for channel_name in self.channels:
            self.update_color_button_appearance(channel_name)
        self.update_status_indicator(self.is_connected)
        self._refresh_log_status_color()
        self._apply_pyqtgraph_theme(c)

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

    # -- curve styling ----------------------------------------------------
    #
    # Every per-channel appearance change funnels through these two helpers.
    # They previously existed as seven near-identical inline blocks that had
    # drifted apart: three different default line widths, and a legend rebuild
    # that filtered by visibility in one place but not the other.

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
    
    def create_connection_content(self):
        """Create all content for the Connection tab"""
        self.create_connection_frame()
        self.create_data_frame()
        self.create_control_frame()
    
    def create_connection_frame(self):
        """Create connection settings frame"""
        conn_frame = ttk.LabelFrame(self.connection_frame, text="Connection Settings", padding="10")
        conn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Port selection
        ttk.Label(conn_frame, text="Port:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.port_var = tk.StringVar(value=self.settings.get('port', ''))
        self.port_combo = ttk.Combobox(conn_frame, textvariable=self.port_var, width=15)
        self.port_combo.grid(row=0, column=1, padx=(0, 10), sticky=tk.W)
        
        ttk.Button(conn_frame, text="Refresh", command=self.update_port_list).grid(row=0, column=2, padx=(0, 10))
        
        # Baud rate
        ttk.Label(conn_frame, text="Baud Rate:").grid(row=0, column=3, sticky=tk.W, padx=(0, 5))
        self.baud_var = tk.StringVar(value=str(self.settings.get('baud_rate', 9600)))
        baud_combo = ttk.Combobox(conn_frame, textvariable=self.baud_var, width=10)
        baud_combo['values'] = ('9600', '19200', '38400', '57600', '115200', '230400', '460800', '921600')
        baud_combo.grid(row=0, column=4, padx=(0, 10))
        
        # Configure Serial Settings button
        ttk.Button(conn_frame, text="Configure Serial Settings", command=self.show_serial_config).grid(row=1, column=1, pady=(10, 0), sticky=tk.W)
        
        # Clear on Connect option
        self.clear_on_connect = tk.BooleanVar(value=True)
        ttk.Checkbutton(conn_frame, text="Clear on Connect", variable=self.clear_on_connect).grid(row=1, column=2, pady=(10, 0), sticky=tk.W, padx=(10, 0))
        
        # Connect/Disconnect button
        self.connect_btn = ttk.Button(conn_frame, text="Connect", command=self.toggle_connection)
        self.connect_btn.grid(row=0, column=5, rowspan=2, padx=(15, 0), sticky=tk.NS)
        
        # Initialize variables for serial settings (moved from inline to here)
        self.databits_var = tk.StringVar(value=str(self.settings.get('data_bits', 8)))
        self.parity_var = tk.StringVar(value=self.settings.get('parity', 'None'))
        self.stopbits_var = tk.StringVar(value=str(self.settings.get('stop_bits', 1)))
    
    def create_data_frame(self):
        """Create data display and input frame"""
        data_frame = ttk.LabelFrame(self.connection_frame, text="Serial Monitor", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Received data display
        ttk.Label(data_frame, text="Received Data:").pack(anchor=tk.W)
        
        # Create frame for received data and scrollbar
        recv_frame = ttk.Frame(data_frame)
        recv_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # tk.Text + ttk.Scrollbar rather than scrolledtext.ScrolledText: the
        # latter embeds a classic tk.Scrollbar, which renders badly on a dark
        # background. The widget itself is still a tk.Text, so log_message(),
        # clear_display() and save_log() are unaffected.
        self.received_text = tk.Text(recv_frame, height=15, state=tk.DISABLED, wrap=tk.CHAR)
        recv_scroll = ttk.Scrollbar(recv_frame, orient=tk.VERTICAL, command=self.received_text.yview)
        self.received_text.configure(yscrollcommand=recv_scroll.set)
        self.received_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        recv_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Configure text tags for different message types
        self._configure_text_tags(themes.CURRENT)
        
        # Send data section
        send_frame = ttk.Frame(data_frame)
        send_frame.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Label(send_frame, text="Send Data:").pack(anchor=tk.W)
        
        # Send input frame
        input_frame = ttk.Frame(send_frame)
        input_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.send_entry = ttk.Entry(input_frame)
        self.send_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.send_entry.bind('<Return>', lambda e: self.send_data())
        
        self.send_btn = ttk.Button(input_frame, text="Send", command=self.send_data, state=tk.DISABLED)
        self.send_btn.pack(side=tk.RIGHT)
        
        # Send options
        options_frame = ttk.Frame(send_frame)
        options_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.add_newline = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Add newline (\\n)", variable=self.add_newline).pack(side=tk.LEFT)
        
        self.add_carriage_return = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="Add carriage return (\\r)", variable=self.add_carriage_return).pack(side=tk.LEFT, padx=(10, 0))
        
        self.hex_display = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="Hex display", variable=self.hex_display).pack(side=tk.RIGHT)
    
    def create_control_frame(self):
        """Create control buttons frame"""
        control_frame = ttk.LabelFrame(self.connection_frame, text="Serial Monitor Controls", padding="10")
        control_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Left side buttons
        left_frame = ttk.Frame(control_frame)
        left_frame.pack(side=tk.LEFT)
        
        self.clear_btn = ttk.Button(left_frame, text="Clear Display", command=self.clear_display)
        self.clear_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.save_btn = ttk.Button(left_frame, text="Save Log", command=self.save_log)
        self.save_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(self.save_btn, "Save current display contents to a file")
        
        # Right side buttons
        right_frame = ttk.Frame(control_frame)
        right_frame.pack(side=tk.RIGHT)
        
        self.auto_scroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(right_frame, text="Auto-scroll", variable=self.auto_scroll).pack(side=tk.RIGHT)
        
        self.timestamp = tk.BooleanVar(value=False)
        ttk.Checkbutton(right_frame, text="Timestamps", variable=self.timestamp).pack(side=tk.RIGHT, padx=(0, 10))
    
    def update_port_list(self):
        """Update the list of available serial ports"""
        ports = [port.device for port in serial.tools.list_ports.comports()]

        # Sort so the highest COM number comes first - on Windows the board
        # that was just plugged in is usually the highest-numbered port.
        def _port_key(p):
            m = re.search(r'(\d+)$', p)
            return int(m.group(1)) if m else 0
        ports = sorted(ports, key=_port_key, reverse=True)

        if ports:
            self.port_combo['values'] = ports + ["TEST MODE"]
            current = self.port_var.get()
            if not current or current == "TEST MODE" or current not in ports:
                self.port_var.set(ports[0])  # highest COM number
        else:
            # No real ports - offer only TEST MODE as default
            self.port_combo['values'] = ["TEST MODE"]
            self.port_var.set("TEST MODE")
    
    def get_parity(self) -> str:
        """Convert parity string to pyserial constant"""
        parity_map = {
            'None': serial.PARITY_NONE,
            'Even': serial.PARITY_EVEN,
            'Odd': serial.PARITY_ODD,
            'Mark': serial.PARITY_MARK,
            'Space': serial.PARITY_SPACE
        }
        return parity_map.get(self.parity_var.get(), serial.PARITY_NONE)
    
    def get_stopbits(self) -> float:
        """Convert stopbits string to float"""
        stopbits_map = {
            '1': serial.STOPBITS_ONE,
            '1.5': serial.STOPBITS_ONE_POINT_FIVE,
            '2': serial.STOPBITS_TWO
        }
        return stopbits_map.get(self.stopbits_var.get(), serial.STOPBITS_ONE)
    
    def toggle_connection(self):
        """Toggle serial connection"""
        if self.is_connected:
            self.disconnect()
        else:
            self.connect()
    
    def connect(self):
        """Connect to serial port or start test mode"""
        try:
            port = self.port_var.get()
            
            if not port:
                messagebox.showerror("Error", "Please select a port")
                return
            
            # Check for test mode
            if port == "TEST MODE":
                # Clear data if option is enabled
                if self.clear_on_connect.get():
                    self.clear_display()
                    self.clear_plot_data()
                
                self.test_mode = True
                self.is_connected = True
                self.connect_btn.config(text="Disconnect")
                self.send_btn.config(state=tk.NORMAL)
                self.status_var.set("Connected to TEST MODE - Simulated Device")
                self.update_status_indicator(True)
                
                # Reset global sample counter on new connection
                self.global_sample_counter = 0
                
                # Reset first line flag
                self.first_line_received = False
                
                # Start test mode thread
                # A fresh Event per connection: clearing the shared one could
                # revive a previous worker that outlived its join timeout.
                self.stop_reading = threading.Event()
                self.read_thread = threading.Thread(target=self.test_mode_loop, daemon=True)
                self.read_thread.start()
                
                self.log_message("Connected to TEST MODE - Simulated Device", "SYSTEM")
                return
            
            # Regular serial connection
            baud_rate = int(self.baud_var.get())
            data_bits = int(self.databits_var.get())
            parity = self.get_parity()
            stop_bits = self.get_stopbits()
            
            self.serial_connection = serial.Serial(
                port=port,
                baudrate=baud_rate,
                bytesize=data_bits,
                parity=parity,
                stopbits=stop_bits,
                timeout=0.1
            )
            
            # Clear data if option is enabled
            if self.clear_on_connect.get():
                self.clear_display()
                self.clear_plot_data()
            
            self.test_mode = False
            self.is_connected = True
            self.connect_btn.config(text="Disconnect")
            self.send_btn.config(state=tk.NORMAL)
            self.status_var.set(f"Connected to {port} at {baud_rate} baud")
            self.update_status_indicator(True)
            
            # Reset global sample counter on new connection
            self.global_sample_counter = 0
            
            # Reset first line flag
            self.first_line_received = False
            
            # Start reading thread
            # A fresh Event per connection - see the test-mode path above.
            self.stop_reading = threading.Event()
            self.read_thread = threading.Thread(target=self.read_serial_data, daemon=True)
            self.read_thread.start()
            
            # Save settings
            self.save_current_settings()
            
            self.log_message(f"Connected to {port}", "SYSTEM")
            
        except serial.SerialException as e:
            messagebox.showerror("Connection Error", f"Failed to connect to {self.port_var.get()}: {str(e)}")
        except ValueError as e:
            messagebox.showerror("Configuration Error", f"Invalid configuration: {str(e)}")
    
    def disconnect(self):
        """Disconnect from serial port"""
        self.is_connected = False
        self.test_mode = False
        self.stop_reading.set()
        self.serial_buffer = ""  # Clear buffer on disconnect
        self.first_line_received = False  # Reset first line flag

        # Stop the worker before closing the port, not after: closing a port
        # the reader is still blocked inside is undefined behaviour.
        if self.read_thread:
            self.read_thread.join(timeout=2.0)
            if self.read_thread.is_alive():
                # Do not reuse a thread we could not stop. connect() creates a
                # fresh Event, so the stale worker can no longer be revived by
                # a later stop_reading.clear().
                print("Warning: serial reader thread did not stop within 2s")
            self.read_thread = None

        # close() routinely raises when the adapter has been unplugged, which
        # is the most common reason we get here. Swallowing it would leave the
        # UI stuck showing "Disconnect" and, via on_closing(), make the window
        # impossible to close.
        if self.serial_connection:
            try:
                self.serial_connection.close()
            except Exception as e:
                print(f"Error closing serial port: {e}")
            finally:
                self.serial_connection = None

        self.connect_btn.config(text="Connect")
        self.send_btn.config(state=tk.DISABLED)
        self.status_var.set("Disconnected")
        self.update_status_indicator(False)
        
        self.log_message("Disconnected", "SYSTEM")
    
    def read_serial_data(self):
        """Read data from serial port in a separate thread"""
        # Capture the Event and the port once. Re-reading self.serial_connection
        # mid-iteration races disconnect(), which sets it to None.
        stop_event = self.stop_reading
        conn = self.serial_connection

        while not stop_event.is_set() and self.is_connected:
            try:
                pending = conn.in_waiting  # one syscall, not two
                if pending > 0:
                    data = conn.read(pending)
                    if data:
                        self._post_rx(('data', data))
                time.sleep(0.01)  # Small delay to prevent excessive CPU usage
            except serial.SerialException:
                self._post_rx(('error', None))
                break
            except Exception:
                break

    # -- reader -> GUI transport ------------------------------------------
    #
    # Workers must not call root.after() directly. tkinter marshals a
    # cross-thread call by queueing a Tcl event and then *blocking* on a
    # condition variable until the main loop runs it, so every millisecond the
    # GUI spent redrawing was a millisecond the reader was not draining the
    # serial port. A plain queue plus a periodic pump on the main thread
    # decouples the two, and gives us somewhere to apply backpressure.

    def _post_rx(self, item):
        """Called from a worker thread. Never touches Tcl."""
        try:
            self._rx_queue.put_nowait(item)
        except queue.Full:
            self._rx_dropped += 1

    def _pump_rx(self):
        """Drain the worker queue on the main thread."""
        if self._closing:
            return

        for _ in range(self.RX_PUMP_BUDGET):
            try:
                kind, payload = self._rx_queue.get_nowait()
            except queue.Empty:
                break

            if kind == 'data':
                self.display_received_data(payload)
            elif kind == 'log':
                self.log_message(payload[0], payload[1])
            elif kind == 'error':
                self.handle_connection_error()
                break  # disconnected; anything still queued is stale

        if self._rx_dropped and not self._rx_drop_reported:
            self._rx_drop_reported = True
            self.log_message(
                f"Receive queue overflowed - {self._rx_dropped} chunk(s) dropped. "
                "The interface cannot keep up with the incoming data rate.", "ERROR")

        self._rx_timer = self.root.after(self.RX_PUMP_INTERVAL_MS, self._pump_rx)
    
    def handle_connection_error(self):
        """Handle connection errors"""
        self.disconnect()
        messagebox.showerror("Connection Lost", "Serial connection was lost")
    
    def display_received_data(self, data: bytes):
        """Display received data in the text widget"""
        try:
            if self.hex_display.get():
                text = ' '.join([f'{byte:02X}' for byte in data])
                self.log_message(text, "RECEIVED")
                # Count hex data chunks as samples
                self.samples_received += 1
            else:
                # Decode and add to buffer
                new_text = data.decode('utf-8', errors='replace')
                self.serial_buffer += new_text

                # A device that never sends a newline (wrong baud rate, binary
                # data, or CR-only line endings) would otherwise grow this
                # string without bound, with an O(n) copy on every chunk.
                if len(self.serial_buffer) > self.MAX_SERIAL_BUFFER:
                    self.serial_buffer = ""
                    self.log_message(
                        f"No line ending seen in {self.MAX_SERIAL_BUFFER} bytes - buffer discarded. "
                        "Check the baud rate and that the device terminates lines with \\n.",
                        "ERROR")
                    return

                # Split once rather than repeatedly slicing the buffer: the
                # previous 'while \n in buffer: split(\n, 1)' loop copied the
                # remaining buffer once per line, making a chunk of k lines
                # O(k * len(chunk)).
                parts = self.serial_buffer.split('\n')
                self.serial_buffer = parts.pop()  # trailing partial line

                lines = []
                for line in parts:
                    line = line.rstrip('\r')  # Remove carriage return if present

                    if line:  # Only process non-empty lines
                        # The first line after connecting is usually a partial
                        # fragment left in the device's buffer, so drop it
                        # rather than parsing it and clearing up afterwards.
                        if not self.first_line_received:
                            self.first_line_received = True
                            continue

                        lines.append(line)

                        # Count samples for SPS calculation
                        self.samples_received += 1

                        # Parse data for plotting
                        self.parse_plot_data(line)

                # One widget update for the whole chunk. Per line this used to
                # cost two config() calls, an insert, a scrollbar callback and
                # a see(END) - roughly seven Tcl round trips each.
                if lines:
                    self.log_lines(lines, "RECEIVED")

        except Exception as e:
            self.log_message(f"Error displaying data: {str(e)}", "ERROR")
    
    def send_data(self):
        """Send data through serial port or test mode"""
        if not self.is_connected:
            messagebox.showerror("Error", "Not connected to any port")
            return
        
        try:
            data = self.send_entry.get()
            
            if self.add_carriage_return.get():
                data += '\r'
            if self.add_newline.get():
                data += '\n'
            
            if self.test_mode:
                # Simulate sending in test mode
                self.log_message(data.rstrip('\r\n'), "SENT")
                
                # Simulate echo response after a short delay
                def simulate_echo():
                    time.sleep(0.1)
                    self._post_rx(('log', (f"Echo: {data.rstrip()}", "RECEIVED")))
                
                threading.Thread(target=simulate_echo, daemon=True).start()
            else:
                # Real serial communication
                if self.serial_connection is not None:
                    self.serial_connection.write(data.encode('utf-8'))
                    self.log_message(data.rstrip('\r\n'), "SENT")
                else:
                    messagebox.showerror("Error", "Serial connection is not available")
            
            # Clear the input field
            self.send_entry.delete(0, tk.END)
            
        except serial.SerialException as e:
            messagebox.showerror("Send Error", f"Failed to send data: {str(e)}")
        except Exception as e:
            messagebox.showerror("Error", f"Unexpected error: {str(e)}")
    
    def test_mode_loop(self):
        """Simulate device responses in test mode.

        Emits a fixed-rate stream of a noisy channel and a clean sine, which
        is enough to exercise plotting, channel detection and the X-axis
        controls without hardware.
        """
        stop_event = self.stop_reading
        interval = 0.2  # 5 SPS
        sample = 0

        while not stop_event.is_set() and self.is_connected and self.test_mode:
            try:
                t = sample * interval
                sensor_a = random.gauss(3.0, 1.0)
                sensor_b = math.sin(2 * math.pi * 0.1 * t)  # 0.1 Hz sine, amplitude 1

                line = f"TIME:{t:.2f},SENSOR_A:{sensor_a:.4f},SENSOR_B:{sensor_b:.4f}"
                # Through the queue, not root.after: a cross-thread Tcl call
                # blocks this worker until the GUI services it.
                self._post_rx(('data', (line + '\n').encode('utf-8')))

                sample += 1
                time.sleep(interval)

            except Exception as e:
                self._post_rx(('log', (f"Test mode error: {e}", "ERROR")))
                break

    def log_message(self, message: str, msg_type: str = ""):
        """Log a single message to the display and optionally to file"""
        self.log_lines((message,), msg_type)

    def log_lines(self, messages, msg_type: str = ""):
        """Log a batch of same-type messages in one widget update.

        Written for the receive path, where a single serial chunk routinely
        carries dozens of lines. Doing this per line cost two config() calls,
        an insert, a scrollbar callback and a see(END) each.
        """
        if not messages:
            return

        timestamp_str = ""
        # Always show timestamps for SYSTEM messages, otherwise use user setting
        if msg_type == "SYSTEM" or self.timestamp.get():
            timestamp_str = f"[{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}] "

        display_block = "".join(f"{timestamp_str}{m}\n" for m in messages)

        self.received_text.config(state=tk.NORMAL)
        if msg_type and msg_type in ("RECEIVED", "SENT", "SYSTEM", "ERROR"):
            self.received_text.insert(tk.END, display_block, msg_type)
        else:
            self.received_text.insert(tk.END, display_block)

        self._trim_monitor()

        if self.auto_scroll.get():
            self.received_text.see(tk.END)
        self.received_text.config(state=tk.DISABLED)

        # Write to log file if logging is active (with type prefix for file)
        if self.logging_active and self.log_file_handle:
            prefix = f"{msg_type}: " if msg_type else ""
            try:
                self.log_file_handle.writelines(
                    f"{timestamp_str}{prefix}{m}\n" for m in messages)
                # Deliberately not flushed here. A flush per line is a syscall
                # per line (10-40 us on Windows); update_file_size_display()
                # flushes once a second instead, and stop_logging() flushes on
                # the way out. Worst case on a hard kill is the tail of an 8 KB
                # buffer - note flush() never protected against power loss
                # anyway, only against process crash.
                self._log_dirty = True
            except Exception as e:
                self._report_log_write_error(e)

    def _trim_monitor(self):
        """Keep the monitor bounded. Caller must have set state=NORMAL."""
        try:
            line_count = int(self.received_text.index('end-1c').split('.')[0])
            if line_count > self.MAX_MONITOR_LINES:
                excess = line_count - self.MAX_MONITOR_LINES
                self.received_text.delete('1.0', f'{excess + 1}.0')
        except (tk.TclError, ValueError):
            pass

    def _report_log_write_error(self, exc):
        """Surface a log-file write failure once, in the UI.

        print() is useless here: a windowed PyInstaller build has no stdout.
        """
        if not self._log_error_reported:
            self._log_error_reported = True
            self.log_message(f"Error writing to log file: {exc}", "ERROR")
    
    def clear_display(self):
        """Clear the received data display"""
        self.received_text.config(state=tk.NORMAL)
        self.received_text.delete(1.0, tk.END)
        self.received_text.config(state=tk.DISABLED)
    
    def save_log(self):
        """Save the received data to a file"""
        # Generate default filename with current date/time (using underscores for time)
        current_time = datetime.datetime.now()
        default_filename = current_time.strftime("%Y-%m-%d_%H%M%S_saved_log")
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("CSV files", "*.csv"), ("All files", "*.*")],
            title="Save log file",
            initialfile=default_filename,
            initialdir=self.logs_dir
        )
        
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    content = self.received_text.get(1.0, tk.END)
                    f.write(content)
                messagebox.showinfo("Success", f"Log saved to {filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save log: {str(e)}")
    
    def toggle_logging(self):
        """Toggle between starting and stopping logging"""
        if self.logging_active:
            self.stop_logging()
        else:
            self.start_logging()
    
    def start_logging(self):
        """Start continuous logging to a selected file"""
        # Generate default filename with current date/time (using underscores for time)
        current_time = datetime.datetime.now()
        default_filename = current_time.strftime("%Y-%m-%d_%H%M%S_log_file")
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("CSV files", "*.csv"), ("All files", "*.*")],
            title="Select log file for continuous logging",
            initialfile=default_filename,
            initialdir=self.logs_dir
        )
        
        if filename:
            try:
                self.log_file_handle = open(filename, 'w', encoding='utf-8')
                self._log_dirty = False
                self._log_error_reported = False
                self.log_file_path = filename
                self.logging_active = True
                
                # Update UI
                self.logging_btn.config(text="Stop Logging")
                self._refresh_log_status_color()
                
                # Write header to log file
                header = f"# Serial Communication Log Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                self.log_file_handle.write(header)
                self.log_file_handle.flush()
                
                self.log_message(f"Started logging to: {os.path.basename(filename)}", "SYSTEM")
                
            except Exception as e:
                messagebox.showerror("Error", f"Failed to start logging: {str(e)}")
    
    def stop_logging(self):
        """Stop continuous logging"""
        # Deliberately not "and self.log_file_handle": if the handle were
        # missing while logging_active was set, the finally below would be
        # skipped and the UI would stay stuck on "Stop Logging" forever.
        if self.logging_active:
            try:
                if self.log_file_handle:
                    # Write footer to log file
                    footer = f"# Serial Communication Log Ended: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    self.log_file_handle.write(footer)
                    self.log_file_handle.close()  # implies a final flush
                    self._log_dirty = False

                filename = os.path.basename(self.log_file_path) if self.log_file_path else "log file"
                self.log_message(f"Stopped logging to: {filename}", "SYSTEM")

            except Exception as e:
                self._report_log_write_error(e)

            finally:
                self.log_file_handle = None
                self.log_file_path = None
                self.logging_active = False
                
                # Update UI
                self.logging_btn.config(text="Start Logging")
                self.log_status_var.set("Not logging")
                self._refresh_log_status_color()
    
    def update_file_size_display(self):
        """Flush the log and refresh the file size readout, once a second"""
        # Batched counterpart to the per-line flush that log_lines() no longer
        # does. Also makes the size below reflect what is actually on disk.
        if self._log_dirty and self.log_file_handle:
            self._log_dirty = False
            try:
                self.log_file_handle.flush()
            except Exception as e:
                self._report_log_write_error(e)

        status = "Not logging"
        if self.logging_active and self.log_file_path:
            status = "Logging active"
            try:
                size = os.path.getsize(self.log_file_path)
                if size < 1024:
                    size_str = f"{size} B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size / (1024 * 1024):.1f} MB"

                status = f"Logging to: {os.path.basename(self.log_file_path)} ({size_str})"
            except OSError:
                pass

        # Only touch the variable when it actually changes; setting it
        # unconditionally dirtied the label and forced a redraw every second
        # for the life of the process.
        if self.log_status_var.get() != status:
            self.log_status_var.set(status)

        # Schedule next update
        self._filesize_timer = self.root.after(1000, self.update_file_size_display)
    
    def load_settings(self) -> Dict[str, Any]:
        """Load settings from file"""
        default_settings = {
            'port': '',
            'baud_rate': 9600,
            'data_bits': 8,
            'parity': 'None',
            'stop_bits': 1,
            'theme': DEFAULT_THEME,
            'ui_scale': 'auto'
        }

        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r') as f:
                    return {**default_settings, **json.load(f)}
        except Exception:
            pass

        return default_settings

    def _write_settings(self, updates: Dict[str, Any]):
        """Merge updates into the settings file.

        Read-merge-write rather than overwrite, so that saving connection
        settings cannot discard the theme (and vice versa).
        """
        data = {}
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r') as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data = loaded
        except Exception:
            data = {}

        data.update(updates)
        self.settings.update(updates)

        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def save_current_settings(self):
        """Save current connection settings to file"""
        try:
            self._write_settings({
                'port': self.port_var.get(),
                'baud_rate': int(self.baud_var.get()),
                'data_bits': int(self.databits_var.get()),
                'parity': self.parity_var.get(),
                'stop_bits': float(self.stopbits_var.get())
            })
        except ValueError:
            pass
    
    def on_closing(self):
        """Handle window closing"""
        # Reachable from WM_DELETE_WINDOW, File > Quit and Ctrl+Q, so guard
        # against a second pass calling destroy() on an already-dead root.
        if self._closing:
            return
        self._closing = True

        # Cancel the recurring timers first. stop_logging() and disconnect()
        # below both call log_message(), and handle_connection_error() can pop
        # a modal dialog - all of which run a nested event loop in which these
        # would otherwise keep firing against half-torn-down state.
        for attr in ('_sps_timer', '_filesize_timer', '_plot_timer', '_rx_timer'):
            timer_id = getattr(self, attr, None)
            if timer_id is not None:
                try:
                    self.root.after_cancel(timer_id)
                except Exception:
                    pass
                setattr(self, attr, None)

        if self.logging_active:
            self.stop_logging()
        if self.is_connected:
            self.disconnect()

        # Tear the Qt side down before the Tk root. Leaving a live QMainWindow
        # referenced from self.plot_widget/plot_curves while the interpreter
        # shuts down is a known PyQt5 segfault-on-exit ordering hazard.
        self._close_plot_window()

        self.root.destroy()

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
    
    def show_preferences(self):
        """Show the preferences dialog"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Preferences")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("+{}+{}".format(self.root.winfo_rootx() + 60,
                                        self.root.winfo_rooty() + 60))

        main_frame = ttk.Frame(dialog, padding="12")
        main_frame.pack(fill=tk.BOTH, expand=True)

        theme_frame = ttk.LabelFrame(main_frame, text="Appearance", padding="10")
        theme_frame.grid(row=0, column=0, sticky=tk.EW)

        # Bound to the same variable and command as the Options > Appearance
        # menu, so the two stay in sync and each click is a live preview.
        for row, theme_key in enumerate(THEME_ORDER):
            ttk.Radiobutton(
                theme_frame,
                text=THEMES[theme_key]["display_name"],
                value=theme_key,
                variable=self.theme_var,
                command=self.on_theme_selected
            ).grid(row=row, column=0, sticky=tk.W, pady=2)

        ttk.Label(main_frame,
                  text="High Contrast is intended for outdoor use in direct sunlight.",
                  foreground=themes.CURRENT["fg_muted"]).grid(row=1, column=0,
                                                             sticky=tk.W, pady=(8, 0))

        scale_frame = ttk.LabelFrame(main_frame, text="Display Scale", padding="10")
        scale_frame.grid(row=2, column=0, sticky=tk.EW, pady=(12, 0))

        ttk.Label(scale_frame, text="Interface size:").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))

        self._scale_labels = {label: value for value, label in scaling.SCALE_CHOICES}
        current_label = next(
            (label for value, label in scaling.SCALE_CHOICES
             if value == str(self.ui_scale_setting)),
            scaling.SCALE_CHOICES[0][1])
        self.scale_var = tk.StringVar(value=current_label)
        scale_combo = ttk.Combobox(scale_frame, textvariable=self.scale_var,
                                   state="readonly", width=24,
                                   values=[label for _v, label in scaling.SCALE_CHOICES])
        scale_combo.grid(row=0, column=1, sticky=tk.W)
        scale_combo.bind("<<ComboboxSelected>>", self.on_ui_scale_selected)
        ToolTip(scale_combo,
                "Enlarges all text and controls. Automatic follows your display's "
                "DPI, and assumes a 4K screen needs enlarging even at 100% scaling.")

        detected = scaling.detect_scale(self.root)
        ttk.Label(scale_frame,
                  text=f"Currently {self.ui_scale:.0%}  (display suggests {detected:.0%})",
                  foreground=themes.CURRENT["fg_muted"]).grid(
                      row=1, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

        ttk.Button(main_frame, text="Close", command=dialog.destroy).grid(
            row=3, column=0, pady=(12, 0))

        self.themes.restyle(dialog)

    def reset_settings(self):
        """Reset all settings to defaults"""
        result = messagebox.askyesno("Reset Settings",
                                   "Are you sure you want to reset all settings to defaults?\n"
                                   "This will require restarting the application.")
        if result:
            try:
                if os.path.exists(SETTINGS_FILE):
                    os.remove(SETTINGS_FILE)

                # Revert the appearance right away - no restart needed for it.
                # Deliberately not written back to disk here; that would
                # immediately recreate the file we just deleted.
                self.theme_var.set(DEFAULT_THEME)
                self.theme_name = DEFAULT_THEME
                self.themes.apply(DEFAULT_THEME)

                messagebox.showinfo(
                    "Settings Reset",
                    "Settings have been reset and the appearance is back to "
                    f"{THEMES[DEFAULT_THEME]['display_name']}.\n"
                    "Please restart the application to reset connection settings.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to reset settings: {str(e)}")
    
    def show_about(self):
        """Show about dialog"""
        about_text = """KestrelSAT Ground Control Station
        
Version: 3.0.0
Created by Lt Col Wyatt Harris, US Air Force Academy

A comprehensive GUI for serial port communication with advanced real-time plotting.

Features:
• Real-time data transmission and reception
• Advanced real-time plotting with PyQtGraph
• Multiple display formats (text/hex)
• Continuous logging capabilities with file size tracking
• Test mode for offline development and testing
• Configurable connection parameters
• Interactive plot controls (pause, clear, buffer settings)
• Channel customization (colors, thickness, custom names)
• Plot legend with customizable channel names
• Status bar with samples-per-second tracking
• Auto-clear on connect for clean data acquisition
• Automatic first-line filtering for reliable plots

Built with Python, tkinter, and PyQtGraph for professional data visualization."""

        self._show_text_dialog("About KestrelSAT Ground Control Station", about_text,
                               width=72, height=24)
    
    def show_user_guide(self):
        """Show user guide dialog"""
        guide_text = """Quick Start Guide:

1. CONNECTION
   • Select port from dropdown or use 'TEST MODE' for simulation
   • Configure baud rate and advanced serial parameters
   • Enable 'Clear on Connect' for fresh start (recommended)
   • Click 'Connect' to establish connection

2. DATA COMMUNICATION
   • Type messages in the 'Send Data' field
   • Press Enter or click 'Send' to transmit
   • Received data appears in the main display
   • Toggle hex display, timestamps, and auto-scroll as needed

3. REAL-TIME PLOTTING
   • Click 'Show Plot Window' to open interactive plots
   • Supports named channels (e.g., "Temp:25.5,Humidity:67")
   • Configure delimiters (comma, space, tab, custom)
   • Adjust buffer size and plot width in settings
   • Use 'Pause Plot' to freeze display while collecting data

4. CHANNEL CUSTOMIZATION
   • Change channel names using the Name field
   • Adjust line thickness (1-10) with spinbox controls
   • Select custom colors with color picker buttons
   • Toggle channel visibility with checkboxes

5. LOGGING
   • Click 'Start Logging' for continuous file logging
   • Monitor file size in real-time
   • Use 'Save Log' for one-time display captures
   • All received data is timestamped and categorized

6. ADVANCED FEATURES
   • Status bar shows connection status and samples/second
   • Automatic first-line filtering ensures clean plot data
   • Buffer management prevents memory overflow
   • Plot legend updates with custom channel names
   
For technical support, refer to the README.md file."""

        self._show_text_dialog("User Guide", guide_text, width=72, height=30)

    def _show_text_dialog(self, title: str, body: str, width: int = 72, height: int = 24):
        """Show a long block of text in a themed, scrollable, read-only window.

        messagebox dialogs are native OS windows and cannot be themed, so the
        two largest text panes in the app get their own Toplevel instead.
        """
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.geometry("+{}+{}".format(self.root.winfo_rootx() + 50,
                                        self.root.winfo_rooty() + 50))

        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        text_frame = ttk.Frame(main_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)

        text = tk.Text(text_frame, width=width, height=height, wrap=tk.WORD)
        scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        text.insert(tk.END, body)
        text.config(state=tk.DISABLED)

        ttk.Button(main_frame, text="Close", command=dialog.destroy).pack(pady=(10, 0))

        self.themes.restyle(dialog)
        dialog.grab_set()
    
    def show_serial_config(self):
        """Show serial configuration dialog"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Serial Configuration")
        dialog.geometry(scaling.scale_geometry("220x150", self.ui_scale))
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Center the dialog
        dialog.geometry("+{}+{}".format(
            self.root.winfo_rootx() + 50,
            self.root.winfo_rooty() + 50
        ))
        
        # Main frame
        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Data bits
        ttk.Label(main_frame, text="Data Bits:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5), pady=(0, 5))
        databits_combo = ttk.Combobox(main_frame, textvariable=self.databits_var, width=8)
        databits_combo['values'] = ('5', '6', '7', '8')
        databits_combo.grid(row=0, column=1, sticky=tk.W, pady=(0, 5))
        
        # Parity
        ttk.Label(main_frame, text="Parity:").grid(row=1, column=0, sticky=tk.W, padx=(0, 5), pady=(0, 5))
        parity_combo = ttk.Combobox(main_frame, textvariable=self.parity_var, width=8)
        parity_combo['values'] = ('None', 'Even', 'Odd', 'Mark', 'Space')
        parity_combo.grid(row=1, column=1, sticky=tk.W, pady=(0, 5))
        
        # Stop bits
        ttk.Label(main_frame, text="Stop Bits:").grid(row=2, column=0, sticky=tk.W, padx=(0, 5), pady=(0, 10))
        stopbits_combo = ttk.Combobox(main_frame, textvariable=self.stopbits_var, width=8)
        stopbits_combo['values'] = ('1', '1.5', '2')
        stopbits_combo.grid(row=2, column=1, sticky=tk.W, pady=(0, 10))
        
        # Buttons frame
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=3, column=0, columnspan=2, pady=(5, 0))
        
        ttk.Button(button_frame, text="OK", command=dialog.destroy).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT)

        self.themes.restyle(dialog)
    
    def create_plot_content(self):
        """Create plot tab content.

        The whole tab scrolls, not just the channel list: with enough channels
        every panel below them would otherwise be pushed out of reach. The
        control buttons are packed to the bottom first so they keep their slice
        of the window no matter how tall the scrollable content grows.
        """
        # Create QApplication if it doesn't exist
        try:
            self.qt_app = QtWidgets.QApplication.instance()
            if self.qt_app is None:
                # Both attributes must be set before the QApplication exists.
                # Without them the plot window renders at physical pixels on a
                # scaled display, so its axis text ends up far smaller than the
                # rest of the interface.
                QtWidgets.QApplication.setAttribute(
                    QtCore.Qt.AA_EnableHighDpiScaling, True)
                QtWidgets.QApplication.setAttribute(
                    QtCore.Qt.AA_UseHighDpiPixmaps, True)
                self.qt_app = QtWidgets.QApplication([])
        except Exception:
            self.qt_app = None

        self.plot_window = None
        self.plot_widget = None

        # Button bar - packed BOTTOM first so it is always visible regardless
        # of how much content sits above it.
        btn_bar = ttk.Frame(self.plot_frame, relief="groove", borderwidth=1)
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 5))

        btn_inner = ttk.Frame(btn_bar)
        btn_inner.pack(anchor="center", pady=6)

        self.show_plot_btn = ttk.Button(btn_inner, text="Show Plot Window", command=self.show_plot_window)
        self.show_plot_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.pause_plot_btn = ttk.Button(btn_inner, text="Pause Plot", command=self.toggle_plot_pause)
        self.pause_plot_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.clear_plot_btn = ttk.Button(btn_inner, text="Clear Plot Data", command=self.clear_plot_data)
        self.clear_plot_btn.pack(side=tk.LEFT)

        # Outer scrollable area - covers all content panels
        scroll_outer = ttk.Frame(self.plot_frame)
        scroll_outer.pack(fill=tk.BOTH, expand=True)

        self.plot_scroll_canvas = tk.Canvas(scroll_outer, highlightthickness=0)
        plot_scrollbar = ttk.Scrollbar(scroll_outer, orient=tk.VERTICAL,
                                       command=self.plot_scroll_canvas.yview)
        self.plot_scroll_canvas.configure(yscrollcommand=plot_scrollbar.set)

        plot_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.plot_scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        content_frame = ttk.Frame(self.plot_scroll_canvas)
        self._plot_canvas_window = self.plot_scroll_canvas.create_window(
            (0, 0), window=content_frame, anchor="nw")

        content_frame.bind("<Configure>", self._on_plot_content_configure)
        self.plot_scroll_canvas.bind(
            "<Configure>",
            lambda e: self.plot_scroll_canvas.itemconfig(self._plot_canvas_window, width=e.width))

        # Only claim the wheel while the pointer is over the tab
        self.plot_scroll_canvas.bind("<Enter>", lambda e: self._bind_plot_scroll())
        self.plot_scroll_canvas.bind("<Leave>", lambda e: self._unbind_plot_scroll())

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

        ttk.Label(settings_frame, text="Buffer Size (samples):").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.buffer_size_var = tk.StringVar(value=str(self.plot_max_points))
        buffer_entry = ttk.Entry(settings_frame, textvariable=self.buffer_size_var, width=10)
        buffer_entry.grid(row=0, column=1, padx=(0, 20), sticky=tk.W)
        buffer_entry.bind('<Return>', self.update_buffer_settings)
        buffer_entry.bind('<FocusOut>', self.update_buffer_settings)
        ToolTip(buffer_entry, "Maximum number of samples stored in memory per channel")

        ttk.Label(settings_frame, text="Plot Width (samples):").grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        self.plot_width_var = tk.StringVar(value=str(self.plot_width))
        width_entry = ttk.Entry(settings_frame, textvariable=self.plot_width_var, width=10)
        width_entry.grid(row=0, column=3, padx=(0, 20), sticky=tk.W)
        width_entry.bind('<Return>', self.update_buffer_settings)
        width_entry.bind('<FocusOut>', self.update_buffer_settings)
        ToolTip(width_entry, "Number of recent samples to display in the plot")

        apply_btn = ttk.Button(settings_frame, text="Apply", command=self.update_buffer_settings)
        apply_btn.grid(row=0, column=4, padx=(10, 0))

        clear_btn = ttk.Button(settings_frame, text="Clear Buffer", command=self.clear_buffer_only)
        clear_btn.grid(row=0, column=5, padx=(10, 0))
        ToolTip(clear_btn, "Clear only plot data buffer, preserve all channels and settings")

        ttk.Label(settings_frame, text="Plot Title:").grid(row=1, column=0, sticky=tk.W, padx=(0, 5), pady=(10, 0))
        self.title_var = tk.StringVar(value=self.plot_title_custom)
        title_entry = ttk.Entry(settings_frame, textvariable=self.title_var, width=30)
        title_entry.grid(row=1, column=1, columnspan=2, sticky=tk.W, pady=(10, 0))
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
        self.plot_scroll_canvas.configure(scrollregion=self.plot_scroll_canvas.bbox("all"))

    def _bind_plot_scroll(self):
        self.plot_scroll_canvas.bind_all("<MouseWheel>", self._on_plot_mousewheel)
        self.plot_scroll_canvas.bind_all("<Button-4>", self._on_plot_mousewheel)
        self.plot_scroll_canvas.bind_all("<Button-5>", self._on_plot_mousewheel)

    def _unbind_plot_scroll(self):
        self.plot_scroll_canvas.unbind_all("<MouseWheel>")
        self.plot_scroll_canvas.unbind_all("<Button-4>")
        self.plot_scroll_canvas.unbind_all("<Button-5>")

    def _on_plot_mousewheel(self, event):
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
                        title = "Serial Data Plot"  # Default plot title
                    
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
                # Increment global sample counter once per line (not per channel)
                self.global_sample_counter += 1
                self.update_plot_channels(parsed_channels, self.global_sample_counter)
                
        except Exception as e:
            # Report once rather than per line, so a broken parser is visible
            # without flooding the monitor.
            if not self._parse_error_reported:
                self._parse_error_reported = True
                self.log_message(
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
                        self.log_message(
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
        self.themes.exempt(color_btn)

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

        # Theme only the row just built. Restyling self.channel_frame here
        # walked every previously added row too, making the cost of adding N
        # channels O(N^2) - and this runs on the receive path.
        self.themes.restyle(channel_control_frame)
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
            self._plot_timer = self.root.after(delay_ms, self.execute_pending_plot_update)
    
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
            self.plot_status_label.config(text="Plot paused - data still being collected")
        else:
            self.pause_plot_btn.config(text="Pause Plot")
            self.plot_status_label.config(text="Plot window is open and updating in real-time")
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

        except Exception as e:
            # Report once. This handler previously discarded every plotting
            # failure with no trace at all, so a channel could silently stop
            # updating for the rest of the session.
            if not self._plot_error_reported:
                self._plot_error_reported = True
                self.log_message(f"Error updating plot: {e}", "ERROR")

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
            self.plot_status_label.config(text="PyQtGraph not available. Please install: pip install pyqtgraph PyQt5")
            return
            
        if self.qt_app is None:
            try:
                self.qt_app = QtWidgets.QApplication.instance()
                if self.qt_app is None:
                    self.qt_app = QtWidgets.QApplication([])
            except Exception as e:
                self.plot_status_label.config(text=f"Error creating Qt application: {str(e)}")
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
                            self.parent_gui.plot_status_label.config(text="Click 'Show Plot Window' to display real-time plots")
                            event.accept()
                    
                    self.plot_window = PlotWindow(self)
                
                self.plot_window.setWindowTitle("Serial Data Plot")
                self.plot_window.setGeometry(
                    100, 100,
                    scaling.px(self.ui_scale, 800), scaling.px(self.ui_scale, 600))
                
                self.plot_widget = pg.PlotWidget()
                
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
            self.plot_status_label.config(text="Plot window is open and updating in real-time")
            
        except Exception as e:
            # Roll back, otherwise plot_window stays non-None with a None
            # plot_widget and every later click skips the init block above and
            # shows a permanently empty window.
            self.plot_window = None
            self.plot_widget = None
            self.plot_legend = None
            self._drop_curves()
            self.plot_status_label.config(text=f"Error creating plot window: {str(e)}")

    def clear_plot_data(self):
        """Discard all channels: their data, per-channel settings, and UI rows.

        Axis labels, the plot title and the buffer settings are preserved.
        """
        # One structure to clear, so it cannot be partially done - and the Tk
        # variables each Channel owns go with it. That is what used to leak:
        # the six dynamically-named attributes per channel were never released.
        self.channels.clear()

        # Reset global sample counter
        self.global_sample_counter = 0
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
        except Exception:
            pass

        self.themes.restyle(self.channel_frame)

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
        
        self.plot_status_label.config(text="Plot data and channels cleared - axis labels preserved")
    
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
            self.global_sample_counter = 1
        else:
            self.global_sample_counter = 0
        
        # Update plot display to show only the remaining samples
        if self.plot_widget is not None:
            self.update_plot_display()
        
        self.plot_status_label.config(text="Buffer cleared - channels preserved")


def main():
    """Main function"""
    try:
        # Before tk.Tk(): once the root exists Windows has already decided how
        # to treat this process.
        scaling.enable_dpi_awareness()

        root = tk.Tk()
        app = SerialGUI(root)

        # Close the PyInstaller splash screen (no-op when running from source)
        try:
            import pyi_splash  # type: ignore
            pyi_splash.close()
        except ImportError:
            pass

        root.mainloop()
    except KeyboardInterrupt:
        print("Application interrupted by user")
    except Exception as e:
        print(f"Error running application: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()