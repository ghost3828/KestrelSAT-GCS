#!/usr/bin/env python3
"""
Check available serial ports on the system
"""

try:
    import serial.tools.list_ports
except ImportError:
    print("Error: pyserial not installed. Run: pip install pyserial")
    exit(1)

def list_serial_ports():
    """List all available serial ports"""
    ports = list(serial.tools.list_ports.comports())
    
    if ports:
        print("Available serial ports:")
        for i, port in enumerate(ports, 1):
            print(f"{i}. {port.device} - {port.description}")
    else:
        print("No serial ports found on this system.")
        print("\nOptions:")
        print("1. Use virtual serial port software (recommended for testing)")
        print("2. Connect a USB-to-Serial adapter")
        print("3. Use Arduino or other serial device")
        
    return [port.device for port in ports]

if __name__ == "__main__":
    available_ports = list_serial_ports()
    
    if available_ports:
        print("\nSelect any of these ports in the Ground Control Station:")
        for port in available_ports:
            print(f"  {port}")
        print("\nNo hardware? Choose 'TEST MODE' in the port dropdown instead.")
    else:
        print("\nFor testing without hardware, I recommend using virtual serial ports:")
        print("- Download 'com0com' (free virtual serial port driver)")
        print("- Or use 'Virtual Serial Port Driver' software")
        print("- This creates paired ports like COM1↔COM2 for testing")