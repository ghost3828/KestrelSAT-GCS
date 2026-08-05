"""Main application window for the KestrelSAT Ground Control Station.

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
from tkinter import ttk, messagebox, filedialog
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
from typing import Any, Dict, List, Optional

from . import themes
from .themes import (
    SETTINGS_FILE, DEFAULT_THEME, THEME_ORDER, THEMES,
)
from .plotting import PlotTab
from . import scaling
from . import __version__
from . import zmodem
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
    MAX_PLOTS = 12
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

        self.root.title(
            f"USAFA ASTRO - KestrelSAT Ground Control Station v{__version__}")
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

        # ZMODEM file transfer
        self.zmodem_enabled = bool(self.settings.get('zmodem_enabled', True))
        self._transfer_active = threading.Event()
        self._transfer_cancel = None
        self._transfer_thread = None
        self._transfer_dialog = None
        self._plot_error_reported = False
        # Tracks whether each curve currently holds data, so hidden curves are
        # cleared once rather than on every frame.

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

        # Notepad functionality
        self.notes_dirty = False
        self.notes_file_path = None
        
        # Plotting. The per-plot settings (buffers, delimiter, axes,
        # channels) live on each PlotTab; only the shared palette and
        # the list of plots are held here.
        self.plot_colors = list(themes.CURRENT["plot_palette"])
        self.plots: List[PlotTab] = []
        
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
        
        # Flag to track first line received (for clearing partial data)
        self.first_line_received = False
        
        # Create logs directory if it doesn't exist
        self.logs_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(self.logs_dir, exist_ok=True)
        self.notes_dir = os.path.join(os.getcwd(), "notes")
        os.makedirs(self.notes_dir, exist_ok=True)
        self.downloads_dir = os.path.join(os.getcwd(), "downloads")
        os.makedirs(self.downloads_dir, exist_ok=True)
        
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

        # Create Notepad tab
        self.notepad_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.notepad_frame, text="Notepad")

        # Setup Connection tab content
        self.create_connection_content()

        # Setup Notepad tab content
        self.create_notepad_content()

        # Plot tabs, plus the '+' that adds another one. Built last so the
        # plot tabs and '+' sit to the right of the fixed tabs.
        self.create_plot_tabs()
    
    def create_menu_bar(self):
        """Create the menu bar"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Send File (ZMODEM)...", command=self.send_file_zmodem)
        file_menu.add_command(label="Receive File (ZMODEM)...", command=self.receive_file_zmodem)
        file_menu.add_separator()
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

        # Ctrl+T stamps a note from anywhere. Two bindings are needed because
        # Tk's Text class already binds Control-t to tk::TextTranspose, and
        # bind_all runs *after* the class binding - so inside any Text widget
        # the global binding alone would transpose two characters as well as
        # insert the timestamp. Overriding the class binding and returning
        # "break" replaces the transpose and stops the chain before bind_all.
        self.root.bind_class("Text", "<Control-t>", self._on_timestamp_hotkey)
        self.root.bind_all("<Control-t>", self._on_timestamp_hotkey)

    def _on_timestamp_hotkey(self, event=None):
        """Insert a notepad timestamp, showing the Notepad tab if hidden.

        Without the tab switch the keystroke would silently append to notes the
        user cannot see whenever it is pressed from another tab.
        """
        try:
            if self.notebook.select() != str(self.notepad_frame):
                self.notebook.select(self.notepad_frame)
            self.notepad_text.focus_set()
        except Exception:
            pass
        self.insert_notepad_timestamp()
        return "break"
    
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

        self.change_appearance_btn = ttk.Button(
            logging_frame,
            text="Change Appearance",
            command=self.cycle_theme
        )
        self.change_appearance_btn.pack(side=tk.LEFT, padx=(0, 10))
        ToolTip(self.change_appearance_btn, "Cycle theme: Light -> Dark -> High Contrast")
        
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
    def cycle_theme(self):
        """Cycle through the three available themes from the top toolbar."""
        cycle_order = ("light", "dark", "high_contrast")
        current = self.theme_var.get()
        try:
            idx = cycle_order.index(current)
        except ValueError:
            idx = -1

        next_theme = cycle_order[(idx + 1) % len(cycle_order)]
        self.theme_var.set(next_theme)
        self.on_theme_selected()

    def on_zmodem_toggled(self):
        """Enable or disable ZMODEM, and persist the choice."""
        self.zmodem_enabled = bool(self.zmodem_var.get())
        self._write_settings({'zmodem_enabled': self.zmodem_enabled})
        self.log_message(
            "ZMODEM file transfer enabled." if self.zmodem_enabled else
            "ZMODEM file transfer disabled - incoming offers will be ignored.",
            "SYSTEM")

    # -- plot tabs --------------------------------------------------------

    def create_plot_tabs(self):
        """Create the first plot tab and the '+' tab that adds more."""
        # One QApplication for the whole process, created before any plot
        # window. The DPI attributes must be set before it exists, or plot
        # windows render at physical pixels on a scaled display.
        try:
            self.qt_app = QtWidgets.QApplication.instance()
            if self.qt_app is None:
                QtWidgets.QApplication.setAttribute(
                    QtCore.Qt.AA_EnableHighDpiScaling, True)
                QtWidgets.QApplication.setAttribute(
                    QtCore.Qt.AA_UseHighDpiPixmaps, True)
                self.qt_app = QtWidgets.QApplication([])
        except Exception:
            self.qt_app = None

        # A real tab used as a button. ttk.Notebook has no native affordance
        # for this, so selecting it is intercepted below and turned into
        # "add a plot", leaving it never actually shown.
        self.add_tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.add_tab_frame, text="  +  ")

        self.add_plot_tab(select=False)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def add_plot_tab(self, select: bool = True):
        """Append a new independently configured plot."""
        if len(self.plots) >= self.MAX_PLOTS:
            messagebox.showinfo(
                "Plot limit",
                f"You can have at most {self.MAX_PLOTS} plots open at once.")
            return None

        plot = PlotTab(self, len(self.plots) + 1, self.notebook)
        self.plots.append(plot)
        # Insert ahead of '+' so the button stays rightmost.
        self.notebook.insert(self.notebook.index(self.add_tab_frame),
                             plot.frame, text=plot.tab_title)
        self.themes.restyle(plot.frame)
        if select:
            self.notebook.select(plot.frame)
        return plot

    def remove_plot_tab(self, plot):
        """Close a plot and renumber the ones after it."""
        if len(self.plots) <= 1:
            messagebox.showinfo("Plot", "The last plot cannot be removed.")
            return
        if not messagebox.askyesno(
                "Remove plot",
                f"Remove {plot.tab_title}? Its data and settings are discarded."):
            return

        self.plots.remove(plot)
        plot.destroy()
        for i, p in enumerate(self.plots, start=1):
            p.renumber(i)
            try:
                self.notebook.tab(p.frame, text=p.tab_title)
            except Exception:
                pass
        self.notebook.select(self.plots[-1].frame)

    def _on_tab_changed(self, event=None):
        """Turn a click on '+' into a new plot tab."""
        try:
            current = self.notebook.select()
            if current and self.notebook.nametowidget(current) is self.add_tab_frame:
                # Never leave '+' showing: either open a new plot, or fall
                # back to the last real one when the limit is reached.
                if self.add_plot_tab() is None and self.plots:
                    self.notebook.select(self.plots[-1].frame)
        except Exception:
            pass

    def _on_theme_applied(self, c: Dict[str, Any]):
        """Fixups the generic widget walk cannot cover. Order matters."""
        self.plot_colors = list(c["plot_palette"])
        self._configure_text_tags(c)
        self._style_notepad_widget(c)
        for plot in self.plots:
            plot.plot_colors = list(c["plot_palette"])
            plot._remap_auto_channel_colors(c)
            for channel_name in plot.channels:
                plot.update_color_button_appearance(channel_name)
        self.update_status_indicator(self.is_connected)
        self._refresh_log_status_color()
        for plot in self.plots:
            plot._apply_pyqtgraph_theme(c)


    # -- curve styling ----------------------------------------------------
    #
    # Every per-channel appearance change funnels through these two helpers.
    # They previously existed as seven near-identical inline blocks that had
    # drifted apart: three different default line widths, and a legend rebuild
    # that filtered by visibility in one place but not the other.








    
    def create_connection_content(self):
        """Create all content for the Connection tab"""
        self.create_connection_frame()
        self.create_data_frame()
        self.create_control_frame()

    def create_notepad_content(self):
        """Create all content for the Notepad tab"""
        toolbar = ttk.Frame(self.notepad_frame)
        toolbar.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.insert_timestamp_btn = ttk.Button(
            toolbar,
            text="Insert Timestamp (Ctrl+T)",
            command=self.insert_notepad_timestamp
        )
        self.insert_timestamp_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.save_notes_btn = ttk.Button(
            toolbar,
            text="Save Notes",
            command=self.save_notepad_notes
        )
        self.save_notes_btn.pack(side=tk.LEFT)

        self.notes_status_var = tk.StringVar(value="Unsaved")
        ttk.Label(toolbar, textvariable=self.notes_status_var).pack(side=tk.RIGHT)

        notes_frame = ttk.Frame(self.notepad_frame)
        notes_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.notepad_text = tk.Text(notes_frame, wrap=tk.WORD, undo=True)
        notes_scroll = ttk.Scrollbar(notes_frame, orient=tk.VERTICAL, command=self.notepad_text.yview)
        self.notepad_text.configure(yscrollcommand=notes_scroll.set)
        self.notepad_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        notes_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.notepad_text.bind('<<Modified>>', self.on_notepad_modified)
        self._style_notepad_widget(themes.CURRENT)

    def _style_notepad_widget(self, c: Dict[str, Any]):
        """Apply active theme colors to the classic Tk notepad widget."""
        try:
            self.notepad_text.configure(
                bg=c["log_bg"],
                fg=c["log_fg"],
                insertbackground=c["log_fg"],
                selectbackground=c["select_bg"],
                selectforeground=c["select_fg"],
            )
        except Exception:
            pass

    def _default_notepad_filename(self):
        """Default file name for notes export."""
        return datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S_notepad_notes")

    def on_notepad_modified(self, _event=None):
        """Track dirty state for unsaved notes prompts."""
        if not self.notepad_text.edit_modified():
            return
        self.notes_dirty = True
        self.notes_status_var.set("Unsaved")
        self.notepad_text.edit_modified(False)

    def insert_notepad_timestamp(self):
        """Insert timestamp at the current insertion cursor."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.notepad_text.insert(tk.INSERT, timestamp)
        self.notepad_text.focus_set()

    def save_notepad_notes(self):
        """Save Notepad contents to a text file.

        Returns True on success and False when save is cancelled or fails.
        """
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Save notes",
            initialfile=self._default_notepad_filename(),
            initialdir=self.notes_dir
        )

        if not filename:
            return False

        try:
            content = self.notepad_text.get('1.0', tk.END)
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)

            self.notes_file_path = filename
            self.notes_dirty = False
            self.notes_status_var.set(f"Saved: {os.path.basename(filename)}")
            self.notepad_text.edit_modified(False)
            return True
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save notes: {str(e)}")
            return False

    def _confirm_notepad_close(self):
        """Prompt to save Notepad changes before closing.

        Returns True when shutdown should continue.
        """
        if not self.notes_dirty:
            return True

        result = messagebox.askyesnocancel(
            "Unsaved Notes",
            "Notepad notes have unsaved changes. Save before closing?"
        )
        if result is None:
            return False
        if result:
            return self.save_notepad_notes()
        return True
    
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
                    for plot in self.plots:
                        plot.clear_plot_data()
                
                self.test_mode = True
                self.is_connected = True
                self.connect_btn.config(text="Disconnect")
                self.send_btn.config(state=tk.NORMAL)
                self.status_var.set("Connected to TEST MODE - Simulated Device")
                self.update_status_indicator(True)
                
                # Restart sample numbering on every plot
                for plot in self.plots:
                    plot.sample_counter = 0
                
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
                for plot in self.plots:
                    plot.clear_plot_data()
            
            self.test_mode = False
            self.is_connected = True
            self.connect_btn.config(text="Disconnect")
            self.send_btn.config(state=tk.NORMAL)
            self.status_var.set(f"Connected to {port} at {baud_rate} baud")
            self.update_status_indicator(True)
            
            # Restart sample numbering on every plot
            for plot in self.plots:
                plot.sample_counter = 0
            
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
        if self._transfer_active.is_set() and self._transfer_cancel is not None:
            self._transfer_cancel.set()
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
                # A ZMODEM transfer needs the port to itself: two readers would
                # each get half the protocol frames. Idle until it is done.
                if self._transfer_active.is_set():
                    time.sleep(0.05)
                    continue

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

        # Keep Tk updates on the Tk event loop. The plot zoom callback comes
        # from Qt, so it only marks these flags and leaves widget updates here.
        for plot in self.plots:
            if plot._x_width_ui_dirty:
                plot._x_width_ui_dirty = False
                try:
                    plot.x_width_var.set(str(plot.x_axis_width))
                except Exception:
                    pass
            if plot._x_width_unlock_notice_pending:
                plot._x_width_unlock_notice_pending = False
                plot._status(
                    "X-axis width unlocked by zoom/pan. Click Apply to re-lock.")

        for _ in range(self.RX_PUMP_BUDGET):
            try:
                kind, payload = self._rx_queue.get_nowait()
            except queue.Empty:
                break

            if kind == 'data':
                self.display_received_data(payload)
            elif kind == 'log':
                self.log_message(payload[0], payload[1])
            elif kind == 'zmodem':
                self._on_transfer_event(payload)
            elif kind == 'error':
                self.handle_connection_error()
                break  # disconnected; anything still queued is stale

        if self._rx_dropped and not self._rx_drop_reported:
            self._rx_drop_reported = True
            self.log_message(
                f"Receive queue overflowed - {self._rx_dropped} chunk(s) dropped. "
                "The interface cannot keep up with the incoming data rate.", "ERROR")

        self._rx_timer = self.root.after(self.RX_PUMP_INTERVAL_MS, self._pump_rx)
    
    # ------------------------------------------------------------------
    # ZMODEM file transfer
    # ------------------------------------------------------------------
    #
    # Transfers run on their own thread with exclusive use of the port: the
    # reader thread idles on _transfer_active so the two do not each consume
    # half the protocol frames. Progress comes back through the same queue as
    # serial data, so all widget updates still happen on the Tk thread.

    def _auto_receive(self, offset):
        """Start receiving because the far end offered a file.

        Everything before the offer is ordinary output and still belongs in the
        monitor; everything from the offer onwards is protocol and is handed to
        the receiver, since those bytes are already out of the port.
        """
        before = self.serial_buffer[:offset]
        protocol = self.serial_buffer[offset:].encode("latin-1")
        self.serial_buffer = ""

        if before.strip():
            self.log_lines([line for line in before.splitlines() if line], "RECEIVED")

        if self.test_mode or self.serial_connection is None:
            return  # nothing real to talk to

        self.log_message(
            f"ZMODEM: incoming file detected, receiving into {self.downloads_dir}",
            "SYSTEM")
        self._begin_transfer("receive", dest=self.downloads_dir, prefix=protocol)

    def _transfer_ready(self, action):
        """True if a transfer can start now; explains itself if not."""
        if not self.zmodem_enabled:
            messagebox.showinfo(
                "ZMODEM disabled",
                "File transfer is turned off.\n\n"
                "Enable it under Options > Preferences > File Transfer.")
            return False
        if not self.is_connected:
            messagebox.showerror("Not connected", f"Connect to a port before {action}.")
            return False
        if self.test_mode:
            messagebox.showinfo(
                "TEST MODE",
                "TEST MODE simulates telemetry and has no real serial port, "
                "so files cannot be transferred.")
            return False
        if self._transfer_active.is_set():
            messagebox.showinfo("Transfer in progress",
                                "Wait for the current transfer to finish.")
            return False
        return True

    def send_file_zmodem(self):
        """Pick one or more files and send them with ZMODEM."""
        if not self._transfer_ready("sending a file"):
            return
        paths = filedialog.askopenfilenames(title="Send file(s) with ZMODEM")
        if not paths:
            return
        total = sum(os.path.getsize(p) for p in paths)
        self.log_message(
            f"ZMODEM: sending {len(paths)} file(s), {total} bytes. "
            "Start a receive on the other end if it is not automatic.", "SYSTEM")
        self._begin_transfer("send", paths=list(paths))

    def receive_file_zmodem(self):
        """Wait for the far end to send a file."""
        if not self._transfer_ready("receiving a file"):
            return
        dest = filedialog.askdirectory(
            title="Save received file(s) to", initialdir=self.downloads_dir)
        if not dest:
            return
        self.log_message(f"ZMODEM: waiting for a file into {dest}", "SYSTEM")
        self._begin_transfer("receive", dest=dest)

    def _begin_transfer(self, direction, paths=None, dest=None, prefix=b""):
        self._transfer_cancel = threading.Event()
        self._transfer_active.set()
        self._show_transfer_dialog(direction)

        self._transfer_thread = threading.Thread(
            target=self._run_transfer,
            args=(direction, paths, dest, prefix),
            daemon=True)
        self._transfer_thread.start()

    def _run_transfer(self, direction, paths, dest, prefix):
        """Worker thread. Must not touch a widget - post events instead."""
        # The reader polls every 10ms with a 100ms port timeout, so give it a
        # moment to notice _transfer_active and let go of the port.
        time.sleep(0.2)

        port = self.serial_connection
        try:
            if port is None:
                raise zmodem.ZModemError("serial port is not open")

            def progress(**kw):
                self._post_rx(('zmodem', dict(kw, event="progress")))

            if direction == "send":
                engine = zmodem.ZModemSender(
                    port, progress=progress, cancel=self._transfer_cancel)
                done = engine.send(paths)
                self._post_rx(('zmodem', {
                    "event": "finished", "direction": "send",
                    "files": [os.path.basename(p) for p in done]}))
            else:
                engine = zmodem.ZModemReceiver(
                    port, dest, progress=progress, cancel=self._transfer_cancel)
                if prefix:
                    engine.link.push_back(prefix)
                written = engine.receive()
                self._post_rx(('zmodem', {
                    "event": "finished", "direction": "receive",
                    "files": [os.path.basename(p) for p in written],
                    "dest": dest}))
        except zmodem.ZModemCancelled as exc:
            self._post_rx(('zmodem', {"event": "cancelled", "detail": str(exc)}))
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI below
            self._post_rx(('zmodem', {"event": "failed", "detail": str(exc)}))
        finally:
            self._transfer_active.clear()
            self._post_rx(('zmodem', {"event": "closed"}))

    # -- progress dialog ---------------------------------------------------
    def _show_transfer_dialog(self, direction):
        title = "Sending file" if direction == "send" else "Receiving file"
        dialog = tk.Toplevel(self.root)
        dialog.title(f"ZMODEM - {title}")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.geometry("+{}+{}".format(self.root.winfo_rootx() + 80,
                                        self.root.winfo_rooty() + 80))
        dialog.protocol("WM_DELETE_WINDOW", self.cancel_transfer)

        frame = ttk.Frame(dialog, padding="12")
        frame.pack(fill=tk.BOTH, expand=True)

        self._transfer_name_var = tk.StringVar(value="Waiting for the other end...")
        ttk.Label(frame, textvariable=self._transfer_name_var).pack(anchor=tk.W)

        self._transfer_bar = ttk.Progressbar(frame, mode="indeterminate", length=320)
        self._transfer_bar.pack(fill=tk.X, pady=(8, 4))
        self._transfer_bar.start(15)

        self._transfer_detail_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._transfer_detail_var,
                  foreground=themes.CURRENT["fg_muted"]).pack(anchor=tk.W)

        ttk.Button(frame, text="Cancel", command=self.cancel_transfer).pack(pady=(10, 0))

        self._transfer_dialog = dialog
        self.themes.restyle(dialog)

    def cancel_transfer(self):
        """Ask the running transfer to stop."""
        if self._transfer_cancel is not None:
            self._transfer_cancel.set()
        if self._transfer_dialog is not None:
            self._transfer_detail_var.set("Cancelling...")

    def _close_transfer_dialog(self):
        if self._transfer_dialog is not None:
            try:
                self._transfer_bar.stop()
                self._transfer_dialog.destroy()
            except tk.TclError:
                pass
            self._transfer_dialog = None

    def _on_transfer_event(self, info):
        """Handle a transfer message on the Tk thread."""
        event = info.get("event")

        if event == "progress" and self._transfer_dialog is not None:
            name = info.get("name", "")
            total = info.get("total") or 0
            done = info.get("sent", info.get("received", 0)) or 0
            self._transfer_name_var.set(name or "Transferring...")
            if total > 0:
                if str(self._transfer_bar.cget("mode")) != "determinate":
                    self._transfer_bar.stop()
                    self._transfer_bar.configure(mode="determinate", maximum=total)
                self._transfer_bar.configure(value=done)
                self._transfer_detail_var.set(
                    f"{done:,} of {total:,} bytes ({done * 100 // max(total, 1)}%)")
            else:
                self._transfer_detail_var.set(f"{done:,} bytes")

        elif event == "finished":
            names = info.get("files") or []
            if info["direction"] == "send":
                text = (f"ZMODEM: sent {', '.join(names)}" if names
                        else "ZMODEM: the other end skipped every file")
            else:
                text = (f"ZMODEM: received {', '.join(names)} into {info.get('dest')}"
                        if names else "ZMODEM: no files received")
            self.log_message(text, "SYSTEM")

        elif event == "cancelled":
            self.log_message(f"ZMODEM: transfer cancelled ({info.get('detail')})", "ERROR")

        elif event == "failed":
            self.log_message(f"ZMODEM: transfer failed - {info.get('detail')}", "ERROR")
            messagebox.showerror("Transfer failed",
                                 f"ZMODEM transfer failed:\n\n{info.get('detail')}")

        elif event == "closed":
            # Ignore a late 'closed' from a previous transfer if another has
            # already started - it would otherwise kill the new dialog.
            if not self._transfer_active.is_set():
                self._close_transfer_dialog()

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

                # A sender running sz announces itself with a ZRQINIT header.
                # Spotting it here is what makes a download start by itself,
                # and is the behaviour the Preferences toggle governs.
                if self.zmodem_enabled and not self._transfer_active.is_set():
                    offer = self.serial_buffer.find(
                        zmodem.RECEIVE_OFFER.decode("latin-1"))
                    if offer >= 0:
                        self._auto_receive(offer)
                        return

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

                        # Each plot parses the line itself, with its
                        # own delimiter and channel set.
                        for plot in self.plots:
                            plot.parse_plot_data(line)

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
            'ui_scale': 'auto',
            'zmodem_enabled': True
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

        if not self._confirm_notepad_close():
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

        # Stop any transfer before the port closes underneath it.
        if self._transfer_active.is_set():
            if self._transfer_cancel is not None:
                self._transfer_cancel.set()
            if self._transfer_thread is not None:
                self._transfer_thread.join(timeout=2.0)
            self._transfer_active.clear()

        if self.logging_active:
            self.stop_logging()
        if self.is_connected:
            self.disconnect()

        # Tear the Qt side down before the Tk root. Leaving a live QMainWindow
        # referenced from self.plot_widget/plot_curves while the interpreter
        # shuts down is a known PyQt5 segfault-on-exit ordering hazard.
        for plot in self.plots:
            plot.destroy()

        self.root.destroy()

    
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

        transfer_frame = ttk.LabelFrame(main_frame, text="File Transfer", padding="10")
        transfer_frame.grid(row=3, column=0, sticky=tk.EW, pady=(12, 0))

        self.zmodem_var = tk.BooleanVar(value=self.zmodem_enabled)
        zmodem_check = ttk.Checkbutton(
            transfer_frame, text="Enable ZMODEM file transfer",
            variable=self.zmodem_var, command=self.on_zmodem_toggled)
        zmodem_check.grid(row=0, column=0, sticky=tk.W)
        ToolTip(zmodem_check,
                "Adds Send/Receive File to the File menu, and starts a download "
                "automatically when the connected device offers a file. Turn off "
                "if a device sends data that resembles a ZMODEM header.")

        ttk.Label(transfer_frame,
                  text="Received files are saved to the downloads folder.",
                  foreground=themes.CURRENT["fg_muted"]).grid(
                      row=1, column=0, sticky=tk.W, pady=(6, 0))

        ttk.Button(main_frame, text="Close", command=dialog.destroy).grid(
            row=4, column=0, pady=(12, 0))

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
        about_text = f"""KestrelSAT Ground Control Station
        
Version: {__version__}
Created by Wyatt Harris

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

Built with Python, tkinter, and PyQtGraph for professional data visualization.

License:
Copyright (C) 2026 Wyatt Harris
Written in a personal capacity. This is not a work of the United States
Government and was not prepared in the course of official duties.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. It comes with ABSOLUTELY NO WARRANTY.

See the LICENSE file, or <https://www.gnu.org/licenses/>, for the full terms.

This program links PyQt5, which is itself distributed under the GPL v3."""

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

7. KEYBOARD SHORTCUTS
   • Ctrl+T  Insert a timestamp into the Notepad. Works from any tab;
             switches to the Notepad so you can see it land.
   • Ctrl+Q  Quit

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