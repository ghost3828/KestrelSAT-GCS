# Changelog

## Unreleased

### New

**Multiple plots.** The Plot tab is now `Plot 1`, with a `+` tab beside it that adds
`Plot 2`, `Plot 3`, and so on - each with its own pop-out window titled "Serial Plot N".

Every plot is configured independently: its own delimiter, buffer and plot sizes, x-axis
selection, axis labels, title, and per-channel colours, names and visibility. Pausing or
clearing one plot leaves the others alone, and sample numbering is per plot so clearing
one does not shift another's x-axis. All plots parse every incoming line themselves, so
by default they show the same data - what differs is how each is set up to display it.

"Remove This Plot" closes one and renumbers the rest; the last plot cannot be removed.
Up to 12 plots. With four plots open the receive path still sustains ~80,000 lines/s.

### Under the hood

The ~1,400 lines of per-plot logic moved out of `SerialGUI` into a `PlotTab` class in
`kestrelsat/plotting.py`, so plot state is per instance rather than per application.

## v3.1.0

The headline features are ZMODEM file transfer, high-DPI support and selectable
themes. Underneath, a code review turned up two bugs that were silently costing
data, and the receive path is now roughly 20x faster.

### New

**ZMODEM file transfer.** Files can be sent and received over the same serial
connection the telemetry uses, so a board with a ZMODEM-capable bootloader or
shell can be loaded and read back without a second cable.

- `File → Send File (ZMODEM)…` and `File → Receive File (ZMODEM)…`
- Automatic downloads: when the connected device offers a file (for example by
  running `sz`), the app spots it in the incoming stream and receives into
  `downloads/` without being asked
- Progress dialog with filename, byte count, percentage and Cancel
- Interoperable with the standard tools — tested directly against `sz` and `rz`
  in both directions, at sizes from 1 byte to 60 KB, with payloads full of bytes
  that require protocol escaping
- Turn it off under `Options → Preferences → File Transfer` (on by default)

**High-DPI support.** The interface now scales itself so text stays readable on
4K displays. `Options → Preferences → Display Scale` shows what was detected and
lets you override it from 100% to 250%. Two distinct cases are handled: Windows
display scaling at 125/150/200% (the app declares DPI awareness, so Windows stops
bitmap-stretching it and text is drawn crisply), and a 4K panel running at 100%
scaling, where Windows reports a normal 96 DPI but the pixels are physically tiny.

**Appearance themes.** Dark (Mission Control), Light, and High Contrast, selectable
from `Options → Appearance` or Preferences, applied instantly and remembered
between sessions. High Contrast is black-on-white with heavy borders and thicker
plot traces, for reading the screen outdoors. The theme covers the plot window
too — background, axes, grid, legend and default trace colours — while a colour
you pick for a channel yourself is left alone.

**Notepad tab** for keeping notes alongside a session, a **Change Appearance**
button for cycling themes from the toolbar, and an **X-axis width lock** on the
Plot tab.

### Fixed

- **TEST MODE produced no plot data at all.** The simulated telemetry was
  terminated with a literal backslash-n rather than a newline, so no line was
  ever completed: no channels appeared, and the unparsed data accumulated in
  memory at about 1.5 MB/hour. Only the status messages, which take a different
  path, made it look alive.
- **The Tab delimiter never matched.** Same class of bug — a literal backslash-t —
  so selecting "Tab" made every line parse as a single field, with no error shown.
- **The app could become impossible to close.** Closing the serial port was
  unguarded, and that call routinely fails when a USB adapter is unplugged, which
  is the most common reason for disconnecting at all. The exception left the UI
  wedged and stopped the window from being destroyed.
- **Reconnecting could leave two reader threads on one port**, interleaving
  telemetry, because the stop signal was shared between connections.
- **Tooltips on buttons raised an error instead of appearing.**
- Recurring timers were never cancelled at shutdown; the plot window is now closed
  before the interpreter tears down; a malformed line can no longer create
  thousands of channel rows and lock up the interface; and per-channel Tk
  variables are released when channels are cleared instead of leaking.
- The plot legend no longer resurrects hidden channels when another is renamed,
  and no longer loses its theme colours after Clear Plot Data.

### Performance

Measured with `tests/bench.py`:

| | before | after |
|---|---|---|
| Receive path | 4,866 lines/s | 110,075 lines/s |
| Redraw (8 channels × 1000 samples) | 14.79 ms/frame | 2.85 ms/frame |

The reader thread was handing data to the GUI in a way that blocked it until the
interface finished redrawing, so a slow UI directly caused dropped samples. It now
uses a queue drained on the main thread. The serial monitor is also capped at
5,000 lines — nothing trimmed it before, so a long session accumulated hundreds of
thousands of lines in a widget that degrades badly — and writes are batched per
chunk rather than per line.

### Under the hood

- `serial_gui.py` was a single 2,900-line file; it is now a `kestrelsat` package
  with the themes, theming, scaling, channel model and ZMODEM protocol separated
  out. `serial_gui.py` remains the entry point, so the build is unaffected.
- Per-channel state was spread across sixteen parallel dictionaries and
  dynamically-named attributes; it is now one `Channel` record.
- A test suite: seven suites covering themes and contrast ratios, the crash
  regressions above, display scaling, the ZMODEM protocol and its interoperability,
  and the UI. Run with `python tests/run_all.py`.
- The repository tracked 12,503 files, of which 12,486 were build output and a
  checked-in virtualenv. Those are now ignored.
- Releases are built by GitHub Actions on a Windows runner and the executable is
  attached automatically, since PyInstaller cannot cross-compile.

### Upgrading

Settings from v3.0.0 are read as-is; the new `theme`, `ui_scale` and
`zmodem_enabled` keys take their defaults on first run. `serial_gui_settings.json`
is no longer tracked in the repository — see `serial_gui_settings.example.json`.

Python 3.10 or newer is required (the pinned `pyqtgraph` needs it).

## v3.0.0

Icon and splash screen, port list sorted highest-COM-first, a redesigned Plot tab
with the whole tab scrollable and the controls pinned to a bottom bar, and a
revised TEST MODE signal.
