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
- **Multiple Plots**: Add as many plot tabs as you need with `+`; each has its own
  window and is configured independently
- **Logging**: Continuous logging to file with a live file-size readout, plus one-shot
  display captures
- **Appearance Themes**: Dark, Light, and High Contrast, applied instantly without restarting
- **High-DPI Aware**: Scales itself to stay readable on 4K and other high-resolution displays
- **ZMODEM File Transfer**: Send and receive files over the same serial link, compatible with
  `sz`/`rz`, TeraTerm, minicom and other standard ZMODEM tools
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

Each **Plot** tab is one plot. "Show Plot Window" opens it in its own window, titled
"Serial Plot 1", "Serial Plot 2", and so on.

**Multiple plots.** The app starts with a single `Plot 1` tab and a `+` tab to its right.
Click `+` to add `Plot 2`, `Plot 3`, and so on, each with its own window. Every plot is
configured independently - its own delimiter, buffer sizes, axes, title, and per-channel
colours and visibility - so you can watch the same stream several different ways at once.

Every plot parses every incoming line itself, so all plots see the same data by default;
what differs is how each one is configured to show it. Clearing or pausing one plot leaves
the others untouched. "Remove This Plot" closes a plot and discards its data; the last
remaining plot cannot be removed.

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

### Notepad

A scratch pad for notes taken alongside a session. **Insert Timestamp (Ctrl+T)** stamps the
current date and time at the cursor. The shortcut works from any tab and switches to the
Notepad so you can see the stamp land.

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

### File Transfer (ZMODEM)
Files can be moved over the same serial connection the telemetry uses, so a board with a
ZMODEM-capable bootloader or shell can be loaded and read back without a second cable.

- **File → Send File (ZMODEM)…** picks one or more files and sends them.
- **File → Receive File (ZMODEM)…** waits for the far end to offer a file and asks where to put it.
- **Automatic downloads**: when the connected device starts a transfer (for example by running
  `sz`), the app recognises the offer in the incoming stream and receives into `downloads/`
  without being asked.

The implementation is interoperable with the standard tools — it is tested directly against
`sz` and `rz` in both directions.

Turn it off under **Options → Preferences → File Transfer**. It is on by default; disabling it
removes the automatic download behaviour, which is worth doing if a device happens to emit data
that resembles a ZMODEM header. The menu entries then explain that it is disabled rather than
disappearing.

A transfer takes exclusive use of the port for its duration, so the serial monitor pauses while
one is running and resumes afterwards.

### Display Options
- **Timestamps**: Add time stamps to all messages
- **Auto-scroll**: Automatically scroll to show newest data
- **Hex Display**: Show received data in hexadecimal format

### Send Options
- **Add newline (\\n)**: Append newline character to sent data
- **Add carriage return (\\r)**: Append carriage return character to sent data

## Building a Windows executable

Locally, `build_exe.bat` runs PyInstaller against the project's `.venv`, or build from
the spec directly:

```bat
pyinstaller KestrelSAT_GCS_v1.spec
```

The result is a single `dist/KestrelSAT_GCS.exe` with the icon and splash screen bundled.

Releases are built by GitHub Actions instead: pushing a `v*` tag runs
`.github/workflows/release.yml` on a Windows runner and attaches the executable to the
release. PyInstaller does not cross-compile, so the build has to happen on Windows —
this keeps that off anyone's laptop. The workflow also fails the build if the tag does
not match `kestrelsat.__version__`.

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
    app.py                     # SerialGUI: window, tabs, dialogs, serial I/O
    plotting.py                # PlotTab: one plot's tab, data and window
    themes.py                  # Theme palettes (imports nothing else)
    theming.py                 # ThemeManager: applies a theme to the widget tree
    channels.py                # Channel record
    scaling.py                 # High-DPI display scaling
    zmodem.py                  # ZMODEM file transfer protocol
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
downloads/                     # Auto-generated default folder for received files
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

Copyright (C) 2026 Wyatt Harris

This program is free software: you can redistribute it and/or modify it under
the terms of the [GNU General Public License](LICENSE) as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version.

### Authorship

This software was written by Wyatt Harris in a personal capacity. It is **not** a
work of the United States Government, was not prepared in the course of the
author's official duties, and does not represent the official position of the
U.S. Air Force Academy, the Department of the Air Force, the Department of
Defense, or the U.S. Government. Copyright is held personally by the author,
which is what makes licensing it under the GPL possible.

The USAFA ASTRO course is mentioned only to describe what the program was built
for; it is not a claim of institutional authorship or ownership.

It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the [LICENSE](LICENSE) file for the full terms.

### Why GPL v3 specifically

This is not a free choice: `PyQt5` is distributed by Riverbank Computing under
the GPL v3 (or a paid commercial licence). Because the plot window links PyQt5,
a distributed build of this program has to be GPL v3 as well — GPL v2 is *not*
compatible with GPL v3, so v2 is not an option here.

The other dependencies are permissive and impose no such constraint:
`pyqtgraph` is MIT and `pyserial` is BSD.

Practically, this means anyone you give the program or the `.exe` to is entitled
to the corresponding source, under these same terms.
