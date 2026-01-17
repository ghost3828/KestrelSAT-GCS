# Serial Communication GUI

A comprehensive Python GUI application for serial port communication built with tkinter and pyserial.

## Features

- **Connection Management**: Easy connection/disconnection with visual status indicators
- **Port Detection**: Automatic detection and listing of available serial ports
- **Flexible Configuration**: Support for various baud rates, data bits, parity, and stop bits
- **Real-time Data**: Live data reception with threading for non-blocking operation
- **Data Display Options**: 
  - Text or hexadecimal display modes
  - Timestamps for all messages
  - Auto-scroll functionality
- **Data Transmission**: Send text data with optional line endings
- **Logging**: Save received data to text files
- **Settings Persistence**: Automatically saves and restores connection settings

## Requirements

- Python 3.6 or higher
- tkinter (usually included with Python)
- pyserial

## Installation

1. Install the required dependencies:
```bash
pip install -r requirements.txt
```

2. Run the application:
```bash
python serial_gui.py
```

## Usage

### Connecting to a Device

1. **Select Port**: Choose your serial device from the dropdown or click "Refresh" to update the list
2. **Configure Settings**: Set baud rate, data bits, parity, and stop bits as needed
3. **Connect**: Click the "Connect" button to establish the connection

### Sending Data

1. Type your message in the "Send Data" field
2. Choose line ending options (newline/carriage return) if needed
3. Click "Send" or press Enter to transmit

### Receiving Data

- Received data appears in the main display area
- Toggle between text and hex display modes
- Use timestamps to track when data was received
- Auto-scroll keeps the latest data visible

### Additional Features

- **Clear Display**: Remove all received data from the display
- **Save Log**: Export the current session to a text file
- **Settings**: Connection settings are automatically saved and restored

## Configuration Options

### Serial Port Settings
- **Port**: Physical serial port (COM1, COM2, etc. on Windows; /dev/ttyUSB0, etc. on Linux)
- **Baud Rate**: Communication speed (9600, 115200, etc.)
- **Data Bits**: Number of data bits per character (5-8)
- **Parity**: Error checking method (None, Even, Odd, Mark, Space)
- **Stop Bits**: Number of stop bits (1, 1.5, 2)

### Display Options
- **Timestamps**: Add time stamps to all messages
- **Auto-scroll**: Automatically scroll to show newest data
- **Hex Display**: Show received data in hexadecimal format

### Send Options
- **Add newline (\\n)**: Append newline character to sent data
- **Add carriage return (\\r)**: Append carriage return character to sent data

## Troubleshooting

### Common Issues

1. **Port Access Denied**: Make sure the port isn't being used by another application
2. **No Ports Listed**: Check if your device drivers are installed correctly
3. **Connection Lost**: Verify physical connection and device power
4. **Garbled Text**: Check baud rate and other serial parameters match your device

### Error Messages

- **"Failed to connect"**: Port may be in use or device not properly connected
- **"Connection was lost"**: Physical connection interrupted or device disconnected
- **"Invalid configuration"**: Check that all settings are valid numbers/selections

## File Structure

```
serial_gui.py              # Main application file
requirements.txt           # Python dependencies
README.md                 # This file
serial_gui_settings.json  # Auto-generated settings file (created after first use)
```

## Technical Details

- Built with Python's tkinter for cross-platform compatibility
- Uses pyserial for robust serial communication
- Threading ensures the GUI remains responsive during data reception
- JSON-based settings storage for user preferences
- Error handling for common serial communication issues

## License

This project is open source. Feel free to modify and distribute as needed.