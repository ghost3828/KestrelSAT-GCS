# Tests

These drive the real `SerialGUI` against a real Tk interpreter, so they need a
display. On Linux use `xvfb`; on Windows they run directly.

| file | what it covers | needs a display |
|---|---|---|
| `test_themes.py` | Theme table: token parity across the three themes, colour format, WCAG contrast ratios. Parses `serial_gui.py` with `ast`, so it imports nothing. | no |
| `test_scaling.py` | High-DPI scaling: detection, font/geometry scaling, the Preferences round-trip, and that repeated application does not compound. | yes |
| `test_multiplot.py` | Multiple plot tabs: the default layout and `+` tab, adding and removing plots, renumbering (tabs and window titles), that every plot sees every line, and that configuration, data, clearing and sample numbering stay independent. | yes |
| `test_resize.py` | Resize safety: Send Data and the monitor controls stay visible at every window height, the plot tab scrolls horizontally so no channel control is unreachable, the computed minimum size and clamped launch geometry both fit the work area, wheel events do not scroll two regions at once, and every dialog is resizable, on-screen and closes on Escape. | yes |
| `test_correctness.py` | Regression tests for the crash and data-loss fixes: TEST MODE, tab delimiter, buffer cap, disconnect with a failing `close()`, timer cancellation, tooltips on buttons, channel cap, channel cleanup, legend consistency. | yes |
| `test_zmodem.py` | ZMODEM: CRC check values, every escape rule, round trips 0B-40KB, **interoperability against lrzsz's `sz`/`rz`** over a pty in both directions, and a full transfer driven through the app with its reader thread running. Interop is skipped if `sz`/`rz` are absent. | yes |
| `test_smoke.py` | Builds the whole UI, cycles all three themes, opens every dialog, checks settings persistence. | yes |
| `test_plot.py` | pyqtgraph window: background, axes, curve colours, live theme switching, close/reopen. | yes |
| `test_channel_scroll.py` | Plot tab layout: control ordering and the scrollable channel list. | yes |
| `bench.py` | Micro-benchmarks for the receive path, channel creation and redraw. Prints numbers; asserts nothing. | yes |

## Running

```bash
python3 tests/run_all.py                                   # Windows
QT_QPA_PLATFORM=offscreen xvfb-run -a python3 tests/run_all.py   # Linux
```

`QT_QPA_PLATFORM=offscreen` keeps PyQt5 from needing an X server of its own;
`xvfb-run` supplies one for Tk.

For the ZMODEM interoperability tests, install the reference tools:
`apt-get install lrzsz` (Debian/Ubuntu). Without them those checks are skipped
and only the self round-trip runs.

Or run any file on its own, e.g.
`QT_QPA_PLATFORM=offscreen xvfb-run -a python3 tests/test_correctness.py`.

## Note on the event loop

`test_correctness.py` drives a real `root.mainloop()` rather than spinning on
`root.update()`. That is deliberate: the worker threads hand data to the GUI
through a queue, and anything still using `root.after()` from a thread blocks
until the actual event loop services it — a `root.update()` loop will not
release it. The TEST MODE test would silently see zero channels otherwise.
