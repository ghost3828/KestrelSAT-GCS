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
        
        # Setup GUI
        self.setup_gui()
        self.update_port_list()
        
        # Start file size update timer
        self.update_file_size_display()
        
        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def setup_gui(self):
        """Setup the GUI layout"""
        # Create main frames
        self.create_connection_frame()
        self.create_data_frame()
        self.create_control_frame()
        
        # Status bar
        self.status_var = tk.StringVar()
        self.status_var.set("Disconnected")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    
    def create_connection_frame(self):
        """Create connection settings frame"""
        conn_frame = ttk.LabelFrame(self.root, text="Connection Settings", padding="10")
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
        
        # Data bits
        ttk.Label(conn_frame, text="Data Bits:").grid(row=1, column=0, sticky=tk.W, padx=(0, 5), pady=(5, 0))
        self.databits_var = tk.StringVar(value=str(self.settings.get('data_bits', 8)))
        databits_combo = ttk.Combobox(conn_frame, textvariable=self.databits_var, width=5)
        databits_combo['values'] = ('5', '6', '7', '8')
        databits_combo.grid(row=1, column=1, sticky=tk.W, pady=(5, 0))
        
        # Parity
        ttk.Label(conn_frame, text="Parity:").grid(row=1, column=2, sticky=tk.W, padx=(10, 5), pady=(5, 0))
        self.parity_var = tk.StringVar(value=self.settings.get('parity', 'None'))
        parity_combo = ttk.Combobox(conn_frame, textvariable=self.parity_var, width=8)
        parity_combo['values'] = ('None', 'Even', 'Odd', 'Mark', 'Space')
        parity_combo.grid(row=1, column=3, sticky=tk.W, pady=(5, 0))
        
        # Stop bits
        ttk.Label(conn_frame, text="Stop Bits:").grid(row=1, column=4, sticky=tk.W, padx=(10, 5), pady=(5, 0))
        self.stopbits_var = tk.StringVar(value=str(self.settings.get('stop_bits', 1)))
        stopbits_combo = ttk.Combobox(conn_frame, textvariable=self.stopbits_var, width=5)
        stopbits_combo['values'] = ('1', '1.5', '2')
        stopbits_combo.grid(row=1, column=5, sticky=tk.W, pady=(5, 0))
        
        # Connect/Disconnect button
        self.connect_btn = ttk.Button(conn_frame, text="Connect", command=self.toggle_connection)
        self.connect_btn.grid(row=0, column=6, rowspan=2, padx=(15, 0), sticky=tk.NS)
    
    def create_data_frame(self):
        """Create data display and input frame"""
        data_frame = ttk.LabelFrame(self.root, text="Data", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Received data display
        ttk.Label(data_frame, text="Received Data:").pack(anchor=tk.W)
        
        # Create frame for received data and scrollbar
        recv_frame = ttk.Frame(data_frame)
        recv_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        self.received_text = scrolledtext.ScrolledText(recv_frame, height=15, state=tk.DISABLED)
        self.received_text.pack(fill=tk.BOTH, expand=True)
        
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
        control_frame = ttk.LabelFrame(self.root, text="Controls", padding="10")
        control_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Left side buttons
        left_frame = ttk.Frame(control_frame)
        left_frame.pack(side=tk.LEFT)
        
        self.clear_btn = ttk.Button(left_frame, text="Clear Display", command=self.clear_display)
        self.clear_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.save_btn = ttk.Button(left_frame, text="Save Log", command=self.save_log)
        self.save_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(self.save_btn, "Save current display contents to a file")
        
        # Logging controls
        logging_frame = ttk.Frame(control_frame)
        logging_frame.pack(side=tk.LEFT, padx=(20, 0))
        
        self.start_log_btn = ttk.Button(logging_frame, text="Start Logging", command=self.start_logging)
        self.start_log_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(self.start_log_btn, "Start continuous logging of all received data to a file")
        
        self.stop_log_btn = ttk.Button(logging_frame, text="Stop Logging", command=self.stop_logging, state=tk.DISABLED)
        self.stop_log_btn.pack(side=tk.LEFT, padx=(0, 10))
        ToolTip(self.stop_log_btn, "Stop continuous logging and close the log file")
        
        # Logging status
        self.log_status_var = tk.StringVar()
        self.log_status_var.set("Not logging")
        self.log_status_label = ttk.Label(logging_frame, textvariable=self.log_status_var, foreground="gray")
        self.log_status_label.pack(side=tk.LEFT, padx=(0, 10))
        
        # Right side buttons
        right_frame = ttk.Frame(control_frame)
        right_frame.pack(side=tk.RIGHT)
        
        self.auto_scroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(right_frame, text="Auto-scroll", variable=self.auto_scroll).pack(side=tk.RIGHT)
        
        self.timestamp = tk.BooleanVar(value=True)
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
        if self.timestamp.get():
            timestamp_str = f"[{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}] "
        
        type_str = f"{msg_type}: " if msg_type else ""
        full_message = f"{timestamp_str}{type_str}{message}\n"
        
        # Display in GUI
        self.received_text.insert(tk.END, full_message)
        
        if self.auto_scroll.get():
            self.received_text.see(tk.END)
        
        self.received_text.config(state=tk.DISABLED)
        
        # Write to log file if logging is active
        if self.logging_active and self.log_file_handle:
            try:
                self.log_file_handle.write(full_message)
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
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Save log file"
        )
        
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    content = self.received_text.get(1.0, tk.END)
                    f.write(content)
                messagebox.showinfo("Success", f"Log saved to {filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save log: {str(e)}")
    
    def start_logging(self):
        """Start continuous logging to a selected file"""
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Select log file for continuous logging"
        )
        
        if filename:
            try:
                self.log_file_handle = open(filename, 'w', encoding='utf-8')
                self.log_file_path = filename
                self.logging_active = True
                
                # Update UI
                self.start_log_btn.config(state=tk.DISABLED)
                self.stop_log_btn.config(state=tk.NORMAL)
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
                self.start_log_btn.config(state=tk.NORMAL)
                self.stop_log_btn.config(state=tk.DISABLED)
                self.log_status_var.set("Not logging")
                self.log_status_label.config(foreground="gray")
    
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


def main():
    """Main function"""
    root = tk.Tk()
    app = SerialGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()