#!/usr/bin/env python3
"""
Serial Communication GUI Application
A comprehensive GUI for interacting with serial devices using tkinter and pyserial.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Error: pyserial not installed. Run: pip install pyserial")
    exit(1)
import threading
import time
import datetime
import json
import os
import random
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
        
        # Test mode for simulation
        self.test_mode = False
        self.test_counter = 0
        
        # Logging functionality
        self.logging_active = False
        self.log_file_path = None
        self.log_file_handle = None
        
        # Settings
        self.settings = self.load_settings()
        
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
                self.test_mode = True
                self.is_connected = True
                self.connect_btn.config(text="Disconnect")
                self.send_btn.config(state=tk.NORMAL)
                self.status_var.set("Connected to TEST MODE - Simulated Device")
                self.update_status_indicator(True)
                
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
            
            self.test_mode = False
            self.is_connected = True
            self.connect_btn.config(text="Disconnect")
            self.send_btn.config(state=tk.NORMAL)
            self.status_var.set(f"Connected to {port} at {baud_rate} baud")
            self.update_status_indicator(True)
            
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
        
        if self.serial_connection:
            self.serial_connection.close()
            self.serial_connection = None
        
        if self.read_thread:
            self.read_thread.join(timeout=1.0)
        
        self.connect_btn.config(text="Connect")
        self.send_btn.config(state=tk.DISABLED)
        self.status_var.set("Disconnected")
        self.update_status_indicator(False)
        
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
            else:
                text = data.decode('utf-8', errors='replace')
            
            self.log_message(text, "RECEIVED")
            
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
        
Version: 1.0
A comprehensive GUI for serial port communication.

Features:
• Real-time data transmission and reception
• Multiple display formats (text/hex)
• Continuous logging capabilities
• Test mode for offline development
• Configurable connection parameters

Built with Python and tkinter."""
        
        messagebox.showinfo("About Serial Communication GUI", about_text)
    
    def show_user_guide(self):
        """Show user guide dialog"""
        guide_text = """Quick Start Guide:

1. CONNECTION
   • Select port from dropdown or use 'TEST MODE'
   • Configure baud rate and other parameters
   • Click 'Connect' to establish connection

2. DATA COMMUNICATION
   • Type messages in the 'Send Data' field
   • Press Enter or click 'Send' to transmit
   • Received data appears in the main display

3. LOGGING
   • Click 'Start Logging' to continuously log data
   • Click 'Stop Logging' to end logging session
   • Use 'Save Log' for one-time saves

4. OPTIONS
   • Toggle timestamps, auto-scroll, hex display
   • Clear display or save current contents
   
For detailed help, refer to the README.md file."""
        
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


def main():
    """Main function"""
    root = tk.Tk()
    app = SerialGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()