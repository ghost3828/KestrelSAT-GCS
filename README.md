# KestrelSAT Ground Control Station

A Python GUI for talking to serial devices and plotting their telemetry in real time.
Built for the USAFA ASTRO space systems engineering course.

The interface is tkinter/ttk; the plot opens in a separate PyQtGraph window for fast,
interactive rendering of live data.

## Features

- **Connection Management**: Connect and disconnect with a visual status indicator
- **Port Detection**: Automatic detection and listing of available serial ports
- **Flexible Configuration**: Baud rate, data bits, parity, and stop bits
- **Test Mode**: Generates synthetic telemetry so the app can be used without hardware
- **Real-time Data**: Live reception on a background thread, so the GUI stays responsive
- **Data Display Options**: Text or hexadecimal, optional timestamps, auto-scroll
- **Data Transmission**: Send text with optional line endings
- **Real-time Plotting**: Multi-channel plots with per-channel colours, thickness, dot
  size, custom names, and visibility toggles
- **Logging**: Continuous logging to file with a live file-size readout, plus one-shot
  display captures
- **Appearance Themes**: Dark, Light, and High Contrast, applied instantly without restarting
- **High-DPI Aware**: Scales itself to stay readable on 4K and other high-resolution displays
- **Status Bar**: Connection status and samples-per-second

## Requirements

- Python 3.10 or newer (required by the pinned `pyqtgraph`)
- tkinter (bundled with the standard Windows and macOS Python installers; on Debian or
  Ubuntu install `python3-tk`)
- The packages in `requirements.txt`: `pyserial`, `pyqtgraph`, `PyQt5`

PyQtGraph and PyQt5 are only needed for the plot window. If they are missing the app still
starts and the serial monitor works; plotting is disabled with a warning on the console.

## Installation

1. Install the required dependencies:
```bash
pip install -r requirements.txt
```

2. Run the application:
```bash
python serial_gui.py
```

To list the serial ports visible to Python without launching the GUI:
```bash
python check_ports.py
```

## Usage

### Connecting to a Device

1. **Select Port**: Choose your device from the dropdown, or click "Refresh" to rescan.
   Choose `TEST MODE` to run against simulated data with no hardware attached.
2. **Configure Settings**: Set the baud rate, and use "Configure Serial Settings" for data
   bits, parity, and stop bits.
3. **Connect**: Click "Connect". Leave "Clear on Connect" enabled for a clean start.

### Sending Data

1. Type your message in the "Send Data" field
2. Choose line ending options (newline/carriage return) if needed
3. Click "Send" or press Enter to transmit

### Receiving Data

- Received data appears in the Serial Monitor on the Connection tab
- Toggle between text and hex display
- Enable timestamps to see when each line arrived
- Auto-scroll keeps the newest data visible

### Plotting

The Plot tab configures the plot; "Show Plot Window" opens it in its own window.

1. **Data format**: send one sample per line as delimited values. Named channels work too,
   for example `Temp:25.5,Humidity:67`. Unnamed values are named `Ch1`, `Ch2`, ... by position.
2. **Delimiter**: comma, space, tab, or a custom character.
3. **Buffer Size** is how many samples are retained; **Plot Width** is how many are shown.
4. **X-Axis** can be the sample number or any detected channel, so you can plot one channel
   against another. Axis labels and the plot title can be overridden.
5. **Per-channel controls** appear as channels are detected: visibility, display name, line
   thickness, colour, dot size, and whether to draw the connecting line.
6. **Pause Plot** freezes the display while data keeps being collected. **Clear Plot Data**
   drops the channels and their settings; **Clear Buffer** keeps the channels and discards
   all but the most recent sample of each.

### Logging

- "Start Logging" writes everything received to a file continuously, and the header shows
  the current file size
- "Save Log" captures the current contents of the display in one shot
- Both default to the `logs/` folder, created next to wherever the app is launched from

## Configuration Options

### Serial Port Settings
- **Port**: Physical serial port (COM1, COM2, etc. on Windows; /dev/ttyUSB0, etc. on Linux)
- **Baud Rate**: Communication speed (9600, 115200, etc.)
- **Data Bits**: Number of data bits per character (5-8)
- **Parity**: Error checking method (None, Even, Odd, Mark, Space)
- **Stop Bits**: Number of stop bits (1, 1.5, 2)

### Appearance
Pick a theme from **Options → Appearance**, or from **Options → Preferences**. Both use the
same setting, the change applies immediately, and your choice is remembered between sessions.

- **Dark (Mission Control)** — the default. Deep-space blues with cyan and amber accents.
- **Light** — a conventional light theme for well-lit indoor use and for screenshots.
- **High Contrast (Sunlight)** — black on white with heavy borders and thicker plot traces,
  for reading the screen outdoors in direct sun.

The theme covers the plot window as well: background, axes, grid, legend, and the default
channel trace colours all follow it. A colour you pick yourself for a channel is left alone.

Note that Windows' own file, colour, and message dialogs are drawn by the operating system
and cannot be themed, so those still appear in the system's colours.

### Display Scale
On a high-DPI screen the interface scales itself so text stays readable. **Options →
Preferences → Display Scale** shows what was detected and lets you override it
(100% through 250%).

Two situations are handled, and they are not the same problem:

- **Windows scaling at 125/150/200%.** The app declares itself DPI-aware, so Windows
  no longer bitmap-stretches it — text is drawn crisply — and it then scales itself to
  match.
- **A 4K panel running at 100% scaling.** Windows reports a normal 96 DPI here because
  no scaling is active, yet the pixels are physically tiny. Automatic falls back to the
  raw resolution and enlarges anyway.

Changing the setting resizes text immediately. The window itself and the connection
indicator take their new size on the next start.

### Display Options
- **Timestamps**: Add time stamps to all messages
- **Auto-scroll**: Automatically scroll to show newest data
- **Hex Display**: Show received data in hexadecimal format

### Send Options
- **Add newline (\\n)**: Append newline character to sent data
- **Add carriage return (\\r)**: Append carriage return character to sent data

## Building a Windows executable

`build_exe.bat` runs PyInstaller against the project's `.venv`, or build from the spec:

```bat
pyinstaller KestrelSAT_GCS_v1.spec
```

The result lands in `dist/`. Themes are defined in the source, so no extra data files need
to be bundled.

## Troubleshooting

### Common Issues

1. **Port Access Denied**: Make sure the port isn't being used by another application
2. **No Ports Listed**: Check if your device drivers are installed correctly
3. **Connection Lost**: Verify physical connection and device power
4. **Garbled Text**: Check baud rate and other serial parameters match your device
5. **Nothing plots**: Confirm the delimiter matches your data, and that each line is a
   complete sample. The first line after connecting is discarded, since it is usually a
   partial fragment left in the device's buffer.
6. **"PyQtGraph not available"**: Install the plotting dependencies with
   `pip install pyqtgraph PyQt5`

### Error Messages

- **"Failed to connect"**: Port may be in use or device not properly connected
- **"Connection was lost"**: Physical connection interrupted or device disconnected
- **"Invalid configuration"**: Check that all settings are valid numbers/selections

## File Structure

```
serial_gui.py                  # Entry point
kestrelsat/
    app.py                     # SerialGUI: window, tabs, dialogs, serial I/O, plotting
    themes.py                  # Theme palettes (imports nothing else)
    theming.py                 # ThemeManager: applies a theme to the widget tree
    channels.py                # Channel record
    widgets.py                 # ToolTip
tests/                         # See tests/README.md
check_ports.py                 # Standalone serial port lister
requirements.txt               # Python dependencies
build_exe.bat                  # Windows executable build script
KestrelSAT_GCS_v1.spec         # PyInstaller spec for the current build
serial_gui_settings.example.json  # Example settings file
README.md                      # This file
serial_gui_settings.json       # Auto-generated settings (created after first use)
logs/                          # Auto-generated default folder for log files
```

`serial_gui.py` stays the entry point, so the PyInstaller spec needs no changes
when the package is reorganised.

Settings and logs are written relative to the working directory the app was launched from.

## Technical Details

- tkinter/ttk for the main interface, PyQtGraph for the plot window
- pyserial for serial communication
- Reception runs on a background thread so the GUI stays responsive
- Plot redraws are throttled to roughly 30 FPS regardless of incoming sample rate
- JSON-based settings storage for user preferences
- Error handling for common serial communication issues

## Tests

```bash
python tests/run_all.py
```

On Linux these need a display: `QT_QPA_PLATFORM=offscreen xvfb-run -a python tests/run_all.py`.
See `tests/README.md` for what each suite covers.

## License

This project is open source. Feel free to modify and distribute as needed.
