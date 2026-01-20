#!/usr/bin/env python3
"""
Serial Communication GUI Application
A comprehensive GUI for interacting with serial devices using tkinter and pyserial.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog, colorchooser
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Error: pyserial not installed. Run: pip install pyserial")
    exit(1)
try:
    import pyqtgraph as pg
    from PyQt5 import QtWidgets, QtCore
    pg.setConfigOption('background', 'w')
    pg.setConfigOption('foreground', 'k')
    PYQTGRAPH_AVAILABLE = True
except ImportError as e:
    print(f"Warning: PyQtGraph not available: {e}")
    print("Plotting functionality will be disabled.")
    PYQTGRAPH_AVAILABLE = False
    # Create dummy classes to prevent errors
    class DummyQtWidgets:
        class QApplication:
            @staticmethod
            def instance():
                return None
            def __init__(self, *args):
                pass
        class QMainWindow:
            def __init__(self):
                pass
            def setWindowTitle(self, title):
                pass
            def setGeometry(self, *args):
                pass
            def setCentralWidget(self, widget):
                pass
            def show(self):
                pass
            def raise_(self):
                pass
    
    class DummyPG:
        class PlotWidget:
            def __init__(self, *args, **kwargs):
                pass
            def setLabel(self, *args):
                pass
            def showGrid(self, *args):
                pass
            def setBackground(self, *args):
                pass
            def plot(self, *args, **kwargs):
                return DummyCurve()
            def clear(self):
                pass
            def getPlotItem(self):
                return DummyPlotItem()
        
        class LegendItem:
            def __init__(self, *args, **kwargs):
                pass
            def setParentItem(self, *args):
                pass
            def addItem(self, *args):
                pass
        
        @staticmethod
        def mkPen(*args, **kwargs):
            return "dummy_pen"
    
    class DummyPlotItem:
        def addLegend(self):
            return DummyPG.LegendItem()
    
    class DummyCurve:
        def setData(self, *args):
            pass
    
    QtWidgets = DummyQtWidgets()
    pg = DummyPG()
import threading
import time
import datetime
import json
import os
import random
import re
from collections import deque
from typing import Optional, Dict, Any


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
        
        x, y, _, _ = self.widget.bbox("insert") if hasattr(self.widget, 'bbox') else (0, 0, 0, 0)
        x += self.widget.winfo_rootx() + 20
        y += self.widget.winfo_rooty() + 20
        
        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True)
        self.tooltip_window.wm_geometry(f"+{x}+{y}")
        
        label = tk.Label(
            self.tooltip_window,
            text=self.text,
            background="#ffffdd",
            relief="solid",
            borderwidth=1,
            font=("Arial", 9)
        )
        label.pack()
    
    def on_leave(self, event=None):
        """Hide tooltip when mouse leaves widget"""
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None


class SerialGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Serial Communication GUI")
        self.root.geometry("800x700")
        self.root.resizable(True, True)
        
        # Serial connection
        self.serial_connection: Optional[serial.Serial] = None
        self.is_connected = False
        self.read_thread: Optional[threading.Thread] = None
        self.stop_reading = threading.Event()
        self.serial_buffer = ""  # Buffer for accumulating partial serial data
        
        # Test mode for simulation
        self.test_mode = False
        self.test_counter = 0
        
        # Logging functionality
        self.logging_active = False
        self.log_file_path = None
        self.log_file_handle = None
        
        # Plotting functionality
        self.plot_data = {}
        self.plot_colors = ['red', 'blue', 'green', 'orange', 'purple', 'brown', 'pink', 'gray']
        self.plot_max_points = 1000
        self.plot_width = 500  # Number of samples to display in plot
        self.delimiter = ','
        self.custom_delimiter = ''
        self.channel_visibility = {}
        self.plot_curves = {}
        self.plot_paused = False  # Flag to pause/resume plotting
        self.channel_thickness = {}  # Store line thickness for each channel
        self.channel_colors = {}  # Store custom colors for each channel
        self.channel_custom_names = {}  # Store custom names for each channel
        
        # Settings
        self.settings = self.load_settings()
        
        # Sample tracking for status bar
        self.samples_received = 0
        self.last_sample_time = time.time()
        self.current_sps = 0
        
        # Global sample counter for plot x-axis (maintains sample number since boot)
        self.global_sample_counter = 0
        
        # Flag to track first line received (for clearing partial data)
        self.first_line_received = False
        
        # Create logs directory if it doesn't exist
        self.logs_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(self.logs_dir, exist_ok=True)
        
        # Setup GUI
        self.setup_gui()
        self.update_port_list()
        
        # Initialize status indicator
        self.update_status_indicator(False)
        
        # Start file size update timer
        self.update_file_size_display()
        
        # Start SPS update timer
        self.update_sps_display()
        
        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def setup_gui(self):
        """Setup the GUI layout"""
        # Create menu bar
        self.create_menu_bar()
        
        # Create top frame for logging controls
        self.create_top_frame()
        
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
        
        # Create status bar
        self.create_status_bar()
    
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
        
        # Left side - general status
        self.status_label = ttk.Label(self.status_bar, text="Ready")
        self.status_label.pack(side=tk.LEFT, padx=5, pady=2)
        
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
        self.root.after(100, self.update_sps_display)  # Update every 100ms for smoother display

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
        self.status_canvas = tk.Canvas(status_frame, width=16, height=16, highlightthickness=0)
        self.status_canvas.pack(side=tk.LEFT, padx=(0, 8), pady=2)
        
        # Draw initial red circle (disconnected)
        self.status_circle = self.status_canvas.create_oval(2, 2, 14, 14, fill="red", outline="darkred")
        
        # Status text
        self.status_var = tk.StringVar()
        self.status_var.set("Disconnected")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, font=("Arial", 10, "bold"))
        self.status_label.pack(side=tk.LEFT)
        
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
        self.log_status_label = ttk.Label(log_status_frame, textvariable=self.log_status_var, foreground="red", font=("Arial", 10, "bold"))
        self.log_status_label.pack()
    
    def update_status_indicator(self, connected: bool):
        """Update the connection status indicator color"""
        if connected:
            self.status_canvas.itemconfig(self.status_circle, fill="green", outline="darkgreen")
        else:
            self.status_canvas.itemconfig(self.status_circle, fill="red", outline="darkred")
    
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
        data_frame = ttk.LabelFrame(self.connection_frame, text="Data", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Received data display
        ttk.Label(data_frame, text="Received Data:").pack(anchor=tk.W)
        
        # Create frame for received data and scrollbar
        recv_frame = ttk.Frame(data_frame)
        recv_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        self.received_text = scrolledtext.ScrolledText(recv_frame, height=15, state=tk.DISABLED)
        self.received_text.pack(fill=tk.BOTH, expand=True)
        
        # Configure text tags for different message types
        self.received_text.tag_config("RECEIVED", foreground="blue")
        self.received_text.tag_config("SENT", foreground="red")
        self.received_text.tag_config("SYSTEM", foreground="black", font=("Arial", 9, "bold"))
        self.received_text.tag_config("ERROR", foreground="red", font=("Arial", 9, "bold"))
        
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
        control_frame = ttk.LabelFrame(self.connection_frame, text="Controls", padding="10")
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
        
        # Add test mode option
        ports.insert(0, "TEST MODE")
        
        self.port_combo['values'] = ports
        
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])
    
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
                
                # Update status bar
                if hasattr(self, 'status_label'):
                    self.status_label.config(text="Connected to TEST MODE")
                
                # Reset global sample counter on new connection
                self.global_sample_counter = 0
                
                # Reset first line flag
                self.first_line_received = False
                
                # Start test mode thread
                self.stop_reading.clear()
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
            
            # Update status bar
            if hasattr(self, 'status_label'):
                self.status_label.config(text=f"Connected to {port}")
            
            # Reset global sample counter on new connection
            self.global_sample_counter = 0
            
            # Reset first line flag
            self.first_line_received = False
            
            # Start reading thread
            self.stop_reading.clear()
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
        
        if self.serial_connection:
            self.serial_connection.close()
            self.serial_connection = None
        
        if self.read_thread:
            self.read_thread.join(timeout=1.0)
        
        self.connect_btn.config(text="Connect")
        self.send_btn.config(state=tk.DISABLED)
        self.status_var.set("Disconnected")
        self.update_status_indicator(False)
        
        # Update status bar
        if hasattr(self, 'status_label'):
            self.status_label.config(text="Disconnected")
        
        self.log_message("Disconnected", "SYSTEM")
    
    def read_serial_data(self):
        """Read data from serial port in a separate thread"""
        while not self.stop_reading.is_set() and self.is_connected:
            try:
                if self.serial_connection and self.serial_connection.in_waiting > 0:
                    data = self.serial_connection.read(self.serial_connection.in_waiting)
                    if data:
                        self.root.after(0, self.display_received_data, data)
                time.sleep(0.01)  # Small delay to prevent excessive CPU usage
            except serial.SerialException:
                self.root.after(0, self.handle_connection_error)
                break
    
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
                
                # Process complete lines
                while '\n' in self.serial_buffer:
                    line, self.serial_buffer = self.serial_buffer.split('\n', 1)
                    line = line.rstrip('\r')  # Remove carriage return if present
                    
                    if line:  # Only process non-empty lines
                        self.log_message(line, "RECEIVED")
                        
                        # Count samples for SPS calculation
                        self.samples_received += 1
                        
                        # Parse data for plotting
                        self.parse_plot_data(line)
                        
                        # Clear plot data after first line to discard partial data
                        if not self.first_line_received:
                            self.first_line_received = True
                            # Clear plot data after a short delay to ensure parsing is complete
                            self.root.after(10, self.clear_plot_data)
            
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
                    response = f"Echo: {data.rstrip()}"
                    self.root.after(0, lambda: self.log_message(response, "RECEIVED"))
                
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
        """Simulate device responses in test mode"""
        while not self.stop_reading.is_set() and self.is_connected and self.test_mode:
            try:
                # Send periodic sensor data every 3 seconds
                if self.test_counter % 300 == 0:  # 300 * 0.01 = 3 seconds
                    temp = random.uniform(20.0, 30.0)
                    humidity = random.uniform(40.0, 80.0)
                    voltage = random.uniform(3.0, 5.0)
                    
                    sensor_data = f"Sensor: T={temp:.1f}°C, H={humidity:.1f}%, V={voltage:.2f}V"
                    self.root.after(0, lambda msg=sensor_data: self.log_message(msg, "RECEIVED"))
                
                # Send plot data every 100ms (10 times per second)
                if self.test_counter % 10 == 0:
                    # Generate sample plot data with named channels
                    temp_val = 25 + 5 * random.random() * (1 if random.random() > 0.5 else -1)
                    sine_val = 50 + 30 * (time.time() % 10) / 10  # Ramp from 50 to 80
                    noise_val = random.uniform(0, 100)
                    
                    plot_data = f"Temp:{temp_val:.2f},Sine:{sine_val:.2f},Noise:{noise_val:.2f}"
                    
                    # Send as bytes to simulate real serial data
                    data_bytes = (plot_data + '\\n').encode('utf-8')
                    self.root.after(0, self.display_received_data, data_bytes)
                
                # Send system status every 10 seconds
                elif self.test_counter % 1000 == 500:  # Offset timing
                    status_messages = [
                        "System Status: OK",
                        "Memory: 45% used",
                        "Uptime: 2h 15m",
                        "Signal: Strong"
                    ]
                    msg = random.choice(status_messages)
                    self.root.after(0, lambda message=msg: self.log_message(message, "RECEIVED"))
                
                self.test_counter += 1
                time.sleep(0.01)
                
            except Exception as e:
                self.root.after(0, lambda: self.log_message(f"Test mode error: {str(e)}", "ERROR"))
                break
    
    def log_message(self, message: str, msg_type: str = ""):
        """Log a message to the display and optionally to file"""
        self.received_text.config(state=tk.NORMAL)
        
        timestamp_str = ""
        # Always show timestamps for SYSTEM messages, otherwise use user setting
        if msg_type == "SYSTEM" or self.timestamp.get():
            timestamp_str = f"[{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}] "
        
        # Build the message without type prefix
        display_message = f"{timestamp_str}{message}\n"
        
        # For file logging, include the type prefix
        file_message = f"{timestamp_str}{msg_type + ': ' if msg_type else ''}{message}\n"
        
        # Display in GUI with appropriate formatting
        if msg_type and msg_type in ["RECEIVED", "SENT", "SYSTEM", "ERROR"]:
            self.received_text.insert(tk.END, display_message, msg_type)
        else:
            self.received_text.insert(tk.END, display_message)
        
        if self.auto_scroll.get():
            self.received_text.see(tk.END)
        
        self.received_text.config(state=tk.DISABLED)
        
        # Write to log file if logging is active (with type prefix for file)
        if self.logging_active and self.log_file_handle:
            try:
                self.log_file_handle.write(file_message)
                self.log_file_handle.flush()  # Ensure data is written immediately
            except Exception as e:
                print(f"Error writing to log file: {e}")
    
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
                self.log_file_path = filename
                self.logging_active = True
                
                # Update UI
                self.logging_btn.config(text="Stop Logging")
                self.log_status_label.config(foreground="green")
                
                # Write header to log file
                header = f"# Serial Communication Log Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                self.log_file_handle.write(header)
                self.log_file_handle.flush()
                
                self.log_message(f"Started logging to: {os.path.basename(filename)}", "SYSTEM")
                
            except Exception as e:
                messagebox.showerror("Error", f"Failed to start logging: {str(e)}")
    
    def stop_logging(self):
        """Stop continuous logging"""
        if self.logging_active and self.log_file_handle:
            try:
                # Write footer to log file
                footer = f"# Serial Communication Log Ended: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                self.log_file_handle.write(footer)
                self.log_file_handle.close()
                
                filename = os.path.basename(self.log_file_path) if self.log_file_path else "log file"
                self.log_message(f"Stopped logging to: {filename}", "SYSTEM")
                
            except Exception as e:
                print(f"Error closing log file: {e}")
            
            finally:
                self.log_file_handle = None
                self.log_file_path = None
                self.logging_active = False
                
                # Update UI
                self.logging_btn.config(text="Start Logging")
                self.log_status_var.set("Not logging")
                self.log_status_label.config(foreground="darkred")
    
    def update_file_size_display(self):
        """Update the file size display for active logging"""
        if self.logging_active and self.log_file_path and os.path.exists(self.log_file_path):
            try:
                size = os.path.getsize(self.log_file_path)
                if size < 1024:
                    size_str = f"{size} B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size / (1024 * 1024):.1f} MB"
                
                filename = os.path.basename(self.log_file_path)
                self.log_status_var.set(f"Logging to: {filename} ({size_str})")
            except Exception:
                self.log_status_var.set("Logging active")
        elif not self.logging_active:
            self.log_status_var.set("Not logging")
        
        # Schedule next update
        self.root.after(1000, self.update_file_size_display)
    
    def load_settings(self) -> Dict[str, Any]:
        """Load settings from file"""
        settings_file = "serial_gui_settings.json"
        default_settings = {
            'port': '',
            'baud_rate': 9600,
            'data_bits': 8,
            'parity': 'None',
            'stop_bits': 1
        }
        
        try:
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    return {**default_settings, **json.load(f)}
        except Exception:
            pass
        
        return default_settings
    
    def save_current_settings(self):
        """Save current settings to file"""
        settings = {
            'port': self.port_var.get(),
            'baud_rate': int(self.baud_var.get()),
            'data_bits': int(self.databits_var.get()),
            'parity': self.parity_var.get(),
            'stop_bits': float(self.stopbits_var.get())
        }
        
        try:
            with open("serial_gui_settings.json", 'w') as f:
                json.dump(settings, f, indent=2)
        except Exception:
            pass
    
    def on_closing(self):
        """Handle window closing"""
        if self.logging_active:
            self.stop_logging()
        if self.is_connected:
            self.disconnect()
        self.root.destroy()
    
    def show_preferences(self):
        """Show preferences dialog (placeholder)"""
        messagebox.showinfo("Preferences", "Preferences dialog will be implemented in a future version.")
    
    def reset_settings(self):
        """Reset all settings to defaults"""
        result = messagebox.askyesno("Reset Settings", 
                                   "Are you sure you want to reset all settings to defaults?\n"
                                   "This will require restarting the application.")
        if result:
            try:
                if os.path.exists("serial_gui_settings.json"):
                    os.remove("serial_gui_settings.json")
                messagebox.showinfo("Settings Reset", 
                                  "Settings have been reset. Please restart the application.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to reset settings: {str(e)}")
    
    def show_about(self):
        """Show about dialog"""
        about_text = """Serial Communication GUI
        
Version: 2.0
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
        
        messagebox.showinfo("About Serial Communication GUI", about_text)
    
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
        
        messagebox.showinfo("User Guide", guide_text)
    
    def show_serial_config(self):
        """Show serial configuration dialog"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Serial Configuration")
        dialog.geometry("220x150")
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
    
    def create_plot_content(self):
        """Create plot tab content"""
        # Delimiter selection frame
        delimiter_frame = ttk.LabelFrame(self.plot_frame, text="Data Parsing", padding="10")
        delimiter_frame.pack(fill=tk.X, padx=10, pady=5)
        
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
        settings_frame = ttk.LabelFrame(self.plot_frame, text="Plot Settings", padding="10")
        settings_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Buffer size setting
        ttk.Label(settings_frame, text="Buffer Size (samples):").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.buffer_size_var = tk.StringVar(value=str(self.plot_max_points))
        buffer_entry = ttk.Entry(settings_frame, textvariable=self.buffer_size_var, width=10)
        buffer_entry.grid(row=0, column=1, padx=(0, 20), sticky=tk.W)
        buffer_entry.bind('<Return>', self.update_buffer_settings)
        buffer_entry.bind('<FocusOut>', self.update_buffer_settings)
        ToolTip(buffer_entry, "Maximum number of samples stored in memory per channel")
        
        # Plot width setting
        ttk.Label(settings_frame, text="Plot Width (samples):").grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        self.plot_width_var = tk.StringVar(value=str(self.plot_width))
        width_entry = ttk.Entry(settings_frame, textvariable=self.plot_width_var, width=10)
        width_entry.grid(row=0, column=3, padx=(0, 20), sticky=tk.W)
        width_entry.bind('<Return>', self.update_buffer_settings)
        width_entry.bind('<FocusOut>', self.update_buffer_settings)
        ToolTip(width_entry, "Number of recent samples to display in the plot")
        
        # Apply button
        apply_btn = ttk.Button(settings_frame, text="Apply", command=self.update_buffer_settings)
        apply_btn.grid(row=0, column=4, padx=(10, 0))
        
        # Channel visibility frame
        self.channel_frame = ttk.LabelFrame(self.plot_frame, text="Channels", padding="10")
        self.channel_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(self.channel_frame, text="No channels detected").pack()
        
        # PyQtGraph widget frame
        plot_container = ttk.Frame(self.plot_frame)
        plot_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Create QApplication if it doesn't exist
        try:
            self.qt_app = QtWidgets.QApplication.instance()
            if self.qt_app is None:
                self.qt_app = QtWidgets.QApplication([])
        except Exception as e:
            self.qt_app = None
            
        # Create PyQtGraph widget in a separate window
        self.plot_window = None
        self.plot_widget = None
        
        # Control buttons for plot
        plot_control_frame = ttk.Frame(plot_container)
        plot_control_frame.pack(pady=10)
        
        self.show_plot_btn = ttk.Button(plot_control_frame, text="Show Plot Window", command=self.show_plot_window)
        self.show_plot_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.pause_plot_btn = ttk.Button(plot_control_frame, text="Pause Plot", command=self.toggle_plot_pause)
        self.pause_plot_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.clear_plot_btn = ttk.Button(plot_control_frame, text="Clear Plot Data", command=self.clear_plot_data)
        self.clear_plot_btn.pack(side=tk.LEFT)
        
        # Status label
        self.plot_status_label = ttk.Label(plot_container, text="Click 'Show Plot Window' to display real-time plots")
        self.plot_status_label.pack(pady=20)
    
    def update_delimiter(self):
        """Update the delimiter based on selection"""
        delim_type = self.delimiter_var.get()
        if delim_type == "comma":
            self.delimiter = ','
        elif delim_type == "space":
            self.delimiter = ' '
        elif delim_type == "tab":
            self.delimiter = '\\t'
        elif delim_type == "other":
            self.delimiter = self.custom_delim_entry.get() or ','
    
    def on_custom_delimiter_change(self, event=None):
        """Handle custom delimiter entry changes"""
        if self.delimiter_var.get() == "other":
            self.delimiter = self.custom_delim_entry.get() or ','
    
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
                for channel_name in self.plot_data:
                    old_data = list(self.plot_data[channel_name])
                    self.plot_data[channel_name] = deque(old_data, maxlen=new_buffer_size)
            
            # Refresh plot display
            self.update_plot_display()
            
        except ValueError:
            # Reset to current values if invalid input
            self.buffer_size_var.set(str(self.plot_max_points))
            self.plot_width_var.set(str(self.plot_width))
    
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
            # Silently ignore parsing errors to avoid spam
            pass
    
    def update_plot_channels(self, new_data: Dict[str, float], sample_number: int):
        """Update plot data structures with new channel data"""
        for channel_name, value in new_data.items():
            # Initialize channel if new
            if channel_name not in self.plot_data:
                self.plot_data[channel_name] = deque(maxlen=self.plot_max_points)
                self.channel_visibility[channel_name] = True
                
                # Set default thickness and color
                self.channel_thickness[channel_name] = 2
                color_index = len(self.channel_colors) % len(self.plot_colors)
                self.channel_colors[channel_name] = self.plot_colors[color_index]
                
                self.add_channel_control(channel_name)
                
                # Add plot curve if plot widget exists
                if hasattr(self, 'plot_widget') and self.plot_widget is not None:
                    try:
                        if PYQTGRAPH_AVAILABLE:
                            # Use custom thickness and color
                            thickness = self.channel_thickness[channel_name]
                            color = self.channel_colors[channel_name]
                            pen = pg.mkPen(color=color, width=thickness)
                            curve = self.plot_widget.plot(pen=pen, symbol='o', symbolSize=4, 
                                                        symbolBrush=color, name=channel_name)
                        else:
                            # Fallback for dummy mode
                            color = self.channel_colors[channel_name]
                            curve = self.plot_widget.plot(pen=color, name=channel_name)
                        self.plot_curves[channel_name] = curve
                        # Add to legend if it exists
                        if hasattr(self, 'plot_legend') and self.plot_legend is not None:
                            display_name = self.channel_custom_names.get(channel_name, channel_name)
                            self.plot_legend.addItem(curve, display_name)
                    except:
                        pass
            
            # Add data point using the provided sample number (same for all channels in this line)
            self.plot_data[channel_name].append((sample_number, value))
        
        # Update plot display
        self.update_plot_display()
    
    def add_channel_control(self, channel_name: str):
        """Add visibility control for a channel with thickness and color options"""
        # Clear the "No channels" message if it's the first channel
        if len(self.plot_data) == 1:
            for widget in self.channel_frame.winfo_children():
                widget.destroy()
        
        # Create frame for this channel's controls
        channel_control_frame = ttk.Frame(self.channel_frame, relief="solid", borderwidth=1, padding=3)
        channel_control_frame.pack(fill=tk.X, padx=2, pady=2)
        
        # Channel visibility checkbox
        var = tk.BooleanVar(value=True)
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
        thickness_var = tk.StringVar(value=str(self.channel_thickness[channel_name]))
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
        color_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        # Store references
        setattr(self, f"channel_var_{channel_name}", var)
        setattr(self, f"name_var_{channel_name}", name_var)
        setattr(self, f"thickness_var_{channel_name}", thickness_var)
        setattr(self, f"color_btn_{channel_name}", color_btn)
        
        # Initialize custom name
        self.channel_custom_names[channel_name] = channel_name
        
        # Update button color to show current selection
        self.update_color_button_appearance(channel_name)
    
    def toggle_channel_visibility(self, channel_name: str, visible: bool):
        """Toggle visibility of a plot channel"""
        self.channel_visibility[channel_name] = visible
        self.update_plot_display()
    
    def update_channel_thickness(self, channel_name: str, thickness_str: str):
        """Update line thickness for a channel"""
        try:
            thickness = int(thickness_str)
            thickness = max(1, min(10, thickness))  # Clamp between 1 and 10
            self.channel_thickness[channel_name] = thickness
            
            # Update the plot curve if it exists
            if channel_name in self.plot_curves and hasattr(self, 'plot_widget') and self.plot_widget is not None:
                try:
                    if PYQTGRAPH_AVAILABLE:
                        color = self.channel_colors[channel_name]
                        pen = pg.mkPen(color=color, width=thickness)
                        self.plot_curves[channel_name].setPen(pen)
                except:
                    pass
                    
        except ValueError:
            # Reset to current value if invalid input
            thickness_var = getattr(self, f"thickness_var_{channel_name}", None)
            if thickness_var:
                thickness_var.set(str(self.channel_thickness[channel_name]))
    
    def update_channel_name(self, channel_name: str, new_name: str):
        """Update custom display name for a channel"""
        if new_name.strip():
            self.channel_custom_names[channel_name] = new_name.strip()
            
            # Update legend if it exists and plot widget is available
            if (hasattr(self, 'plot_legend') and self.plot_legend is not None and 
                channel_name in self.plot_curves and hasattr(self, 'plot_widget') and self.plot_widget is not None):
                try:
                    if PYQTGRAPH_AVAILABLE:
                        # Remove old legend item
                        self.plot_legend.removeItem(channel_name)
                        # Add new legend item with custom name
                        curve = self.plot_curves[channel_name]
                        self.plot_legend.addItem(curve, self.channel_custom_names[channel_name])
                except:
                    pass
        else:
            # Reset to original name if empty
            name_var = getattr(self, f"name_var_{channel_name}", None)
            if name_var:
                name_var.set(self.channel_custom_names[channel_name])
    
    def choose_channel_color(self, channel_name: str):
        """Open color chooser dialog for a channel"""
        current_color = self.channel_colors[channel_name]
        color = colorchooser.askcolor(color=current_color, title=f"Choose color for {channel_name}")
        
        if color[1]:  # color[1] is the hex color string
            self.channel_colors[channel_name] = color[1]
            
            # Update button appearance
            self.update_color_button_appearance(channel_name)
            
            # Update the plot curve if it exists
            if channel_name in self.plot_curves and hasattr(self, 'plot_widget') and self.plot_widget is not None:
                try:
                    if PYQTGRAPH_AVAILABLE:
                        thickness = self.channel_thickness[channel_name]
                        pen = pg.mkPen(color=color[1], width=thickness)
                        self.plot_curves[channel_name].setPen(pen)
                        self.plot_curves[channel_name].setSymbolBrush(color[1])
                except:
                    pass
    
    def update_color_button_appearance(self, channel_name: str):
        """Update the color button appearance to show the selected color"""
        color_btn = getattr(self, f"color_btn_{channel_name}", None)
        if color_btn:
            try:
                # Set button background to match the line color
                color = self.channel_colors[channel_name]
                color_btn.config(text="Color", background=color)
            except:
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
            self.update_plot_display()
    
    def update_plot_display(self):
        """Update the plot display with current data"""
        if not hasattr(self, 'plot_widget') or self.plot_widget is None:
            return
        
        # Skip update if plot is paused
        if self.plot_paused:
            return
            
        try:
            for channel_name, curve in self.plot_curves.items():
                if channel_name in self.plot_data and self.channel_visibility.get(channel_name, True):
                    data_tuples = list(self.plot_data[channel_name])
                    if data_tuples:
                        # Limit displayed data to plot_width
                        if len(data_tuples) > self.plot_width:
                            data_tuples = data_tuples[-self.plot_width:]
                        
                        # Extract sample numbers and values
                        x_data = [sample_num for sample_num, value in data_tuples]
                        y_data = [value for sample_num, value in data_tuples]
                        
                        curve.setData(x_data, y_data)
                else:
                    curve.setData([], [])
        except Exception as e:
            # Silently handle plot update errors
            pass
    
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
                            self.parent_gui.plot_curves.clear()
                            self.parent_gui.plot_legend = None
                            self.parent_gui.plot_status_label.config(text="Click 'Show Plot Window' to display real-time plots")
                            event.accept()
                    
                    self.plot_window = PlotWindow(self)
                else:
                    # Fallback for when PyQtGraph is not available
                    self.plot_window = QtWidgets.QMainWindow()
                
                self.plot_window.setWindowTitle("Serial Data Plot")
                self.plot_window.setGeometry(100, 100, 800, 600)
                
                self.plot_widget = pg.PlotWidget(title="Serial Data Plot")
                self.plot_widget.setLabel('left', 'Value')
                self.plot_widget.setLabel('bottom', 'Sample')
                self.plot_widget.showGrid(True, True)
                self.plot_widget.setBackground('white')
                
                # Add legend using LegendItem
                if PYQTGRAPH_AVAILABLE:
                    try:
                        self.plot_legend = pg.LegendItem(offset=(-70, 30))  # Negative offset for top-right
                        self.plot_legend.setParentItem(self.plot_widget.getPlotItem())
                    except:
                        # Fallback if legend creation fails
                        self.plot_legend = None
                else:
                    self.plot_legend = None
                
                self.plot_window.setCentralWidget(self.plot_widget)
                
                # Recreate all plot curves
                self.plot_curves = {}
                for i, channel_name in enumerate(self.plot_data.keys()):
                    if PYQTGRAPH_AVAILABLE:
                        # Use custom thickness and color settings
                        thickness = self.channel_thickness.get(channel_name, 2)
                        color = self.channel_colors.get(channel_name, self.plot_colors[i % len(self.plot_colors)])
                        pen = pg.mkPen(color=color, width=thickness)
                        curve = self.plot_widget.plot(pen=pen, symbol='o', symbolSize=4, 
                                                    symbolBrush=color, name=channel_name)
                    else:
                        # Fallback for dummy mode
                        color = self.channel_colors.get(channel_name, self.plot_colors[i % len(self.plot_colors)])
                        curve = self.plot_widget.plot(pen=color, name=channel_name)
                    self.plot_curves[channel_name] = curve
                    # Add to legend if it exists
                    if self.plot_legend is not None:
                        try:
                            display_name = self.channel_custom_names.get(channel_name, channel_name)
                            self.plot_legend.addItem(curve, display_name)
                        except:
                            pass
                
                # Update with current data
                self.update_plot_display()
            
            self.plot_window.show()
            self.plot_window.raise_()
            self.plot_status_label.config(text="Plot window is open and updating in real-time")
            
        except Exception as e:
            self.plot_status_label.config(text=f"Error creating plot window: {str(e)}")
    
    def clear_plot_data(self):
        """Clear all plot data"""
        self.plot_data.clear()
        self.plot_curves.clear()
        self.channel_visibility.clear()
        self.channel_thickness.clear()
        self.channel_colors.clear()
        self.channel_custom_names.clear()
        
        # Reset global sample counter
        self.global_sample_counter = 0
        
        # Clear channel controls
        for widget in self.channel_frame.winfo_children():
            widget.destroy()
        ttk.Label(self.channel_frame, text="No channels detected").pack()
        
        # Clear plot if window exists
        if self.plot_widget is not None:
            # Remove existing legend first
            if hasattr(self, 'plot_legend') and self.plot_legend is not None:
                try:
                    self.plot_legend.setParentItem(None)
                except:
                    pass
                self.plot_legend = None
            
            self.plot_widget.clear()
            
            # Recreate legend after clearing
            if PYQTGRAPH_AVAILABLE:
                try:
                    self.plot_legend = pg.LegendItem(offset=(-70, 30))  # Negative offset for top-right
                    self.plot_legend.setParentItem(self.plot_widget.getPlotItem())
                except:
                    self.plot_legend = None
            else:
                self.plot_legend = None
        
        self.plot_status_label.config(text="Plot data cleared")


def main():
    """Main function"""
    try:
        root = tk.Tk()
        app = SerialGUI(root)
        root.mainloop()
    except KeyboardInterrupt:
        print("Application interrupted by user")
    except Exception as e:
        print(f"Error running application: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()