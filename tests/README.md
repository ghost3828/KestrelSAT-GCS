# Tests

These drive the real `SerialGUI` against a real Tk interpreter, so they need a
display. On Linux use `xvfb`; on Windows they run directly.

| file | what it covers | needs a display |
|---|---|---|
| `test_themes.py` | Theme table: token parity across the three themes, colour format, WCAG contrast ratios. Parses `serial_gui.py` with `ast`, so it imports nothing. | no |
| `test_scaling.py` | High-DPI scaling: detection, font/geometry scaling, the Preferences round-trip, and that repeated application does not compound. | yes |
| `test_correctness.py` | Regression tests for the crash and data-loss fixes: TEST MODE, tab delimiter, buffer cap, disconnect with a failing `close()`, timer cancellation, tooltips on buttons, channel cap, channel cleanup, legend consistency. | yes |
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

Or run any file on its own, e.g.
`QT_QPA_PLATFORM=offscreen xvfb-run -a python3 tests/test_correctness.py`.

## Note on the event loop

`test_correctness.py` drives a real `root.mainloop()` rather than spinning on
`root.update()`. That is deliberate: the worker threads hand data to the GUI
through a queue, and anything still using `root.after()` from a thread blocks
until the actual event loop services it — a `root.update()` loop will not
release it. The TEST MODE test would silently see zero channels otherwise.
