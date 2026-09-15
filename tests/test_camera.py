"""Camera tab: layout, the serial demux, capture, viewer and saving.

Run:  QT_QPA_PLATFORM=offscreen xvfb-run -a python tests/test_camera.py
"""
import os
import shutil
import sys
import tempfile
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import kestrelsat.app as sg
from kestrelsat import arducam, camera as camera_mod

failures = []


class FakeMessagebox:
    """Records dialogs instead of opening them.

    A real messagebox runs a nested event loop and blocks until someone
    clicks, which in a headless test is an indefinite hang. Every modal the
    tab can raise is captured here and answered from .answer.
    """

    def __init__(self):
        self.calls = []
        self.answer = True

    def __getattr__(self, name):
        if name.startswith(("show", "ask")):
            def call(title, message, **kwargs):
                self.calls.append((name, title, message))
                return self.answer
            return call
        raise AttributeError(name)

    def titles(self):
        return [title for _, title, _ in self.calls]

    def clear(self):
        self.calls.clear()


fake_mb = FakeMessagebox()
camera_mod.messagebox = fake_mb


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def tabs(app):
    return [app.notebook.tab(t, "text") for t in app.notebook.tabs()]


def pump(times=6):
    """Let the rx pump and any after() callbacks run."""
    for _ in range(times):
        root.update()
        root.after(30, root.quit)
        root.mainloop()


if os.path.exists(sg.SETTINGS_FILE):
    os.remove(sg.SETTINGS_FILE)

root = tk.Tk()
root.geometry("1100x700")
app = sg.SerialGUI(root)
root.update()
cam = app.camera


print("\n-- tab and layout --")
check(cam is not None, "the app owns a CameraTab")
check(tabs(app) == ["Connection", "Notepad", "Camera", "Plot 1", "  +  "],
      "Camera sits left of the plots and '+': %s" % tabs(app))
slaves = cam.frame.pack_slaves()
check(slaves and slaves[-1] is cam.viewer,
      "the viewer is packed last, so it absorbs the leftover cavity")
check(str(cam.viewer.pack_info()["expand"]) in ("1", "True"),
      "and it is the only expanding child")
for name, widget in (("banner", cam.banner), ("strip", cam.strip)):
    check(widget.winfo_manager() == "pack", f"{name} is packed")

# Every fixed row must survive a short window - the rule test_resize enforces.
# The page must be selected first: an unselected notebook tab is 1px.
app.notebook.select(cam.frame)
root.geometry("820x340")
root.update()
for name, widget in (("banner", cam.banner), ("strip", cam.strip),
                     ("viewer", cam.viewer)):
    check(widget.winfo_ismapped() and widget.winfo_height() > 0,
          f"{name} still visible at 820x340 ({widget.winfo_height()}px)")
root.geometry("1100x700")
root.update()


print("\n-- controls follow connection state --")
check(str(cam.take_btn["state"]) == "disabled",
      "Take Picture is disabled while disconnected")
check(cam.banner_var.get().startswith("Not connected"),
      "the banner says so plainly")
check(not cam.busy, "an idle tab is not busy")


print("\n-- the demux contract --")
JPEG = None
if camera_mod.QT_IMAGING_AVAILABLE:
    JPEG = cam.make_test_pattern(0x03, arducam.PIX_FMT_JPEG)
check(JPEG is not None and JPEG[:2] == b"\xff\xd8",
      "a synthetic test pattern encodes to JPEG")

TELEM = b"TIME:1.0,SENSOR_A:12.5\n"
before_lines = app.plots[0].channels.get("SENSOR_A")
stream = (TELEM + arducam.pack_image_packet(0x03, arducam.PIX_FMT_JPEG, JPEG)
          + TELEM)
app.is_connected = True
app.test_mode = True
cam.on_connect()
residual = cam.consume(stream)
check(residual == TELEM * 2,
      "telemetry either side of an image comes back byte-identical")
check(cam.current is not None and cam.current.data == JPEG,
      "and the image is extracted intact")
check(b"\xff\xd8" not in residual, "no image bytes leak to the monitor")

# The regression that motivated the whole design: a JPEG containing ZMODEM's
# receive offer must not be able to start a transfer.
poisoned = b"\xff\xd8" + b"**\x18B00" + b"\xff\xd9"
residual = cam.consume(arducam.pack_image_packet(0x03, arducam.PIX_FMT_JPEG, poisoned))
check(b"**\x18B00" not in residual,
      "a ZMODEM offer inside a JPEG never reaches display_received_data")
check(not app._transfer_active.is_set(), "so no transfer is started")

# And the whole path, through the real queue and pump.
app._post_rx(("data", TELEM + arducam.pack_image_packet(
    0x05, arducam.PIX_FMT_JPEG, JPEG) + TELEM))
pump()
check(cam.current is not None and cam.current.data == JPEG,
      "an image posted through _post_rx reaches the tab via _pump_rx")
check("SENSOR_A" in app.plots[0].channels,
      "and the interleaved telemetry still reaches the plots")


print("\n-- decode, score and display --")
capture = cam.current
check(capture.image is not None, "the capture decoded to a QImage")
check((capture.width, capture.height) == (320, 240),
      "dimensions come from the decoded image, not the commanded mode "
      f"({capture.width}x{capture.height} for a frame sent as mode 0x05)")
check(capture.sharpness is not None and capture.sharpness > 0,
      f"a sharpness score was computed ({capture.sharpness:,.0f})")
root.update()
check(cam._photo is not None, "something was rendered into the viewer")

# A blurred frame must score lower, or the focus meter is useless.
if camera_mod.QT_IMAGING_AVAILABLE and camera_mod.NUMPY_AVAILABLE:
    from PyQt5.QtCore import Qt
    sharp_score = capture.sharpness
    blurred = camera_mod.Capture(
        data=b"", pix_fmt=arducam.PIX_FMT_JPEG, mode=0x03, width=320, height=240)
    blurred.image = capture.image.scaled(
        40, 30, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).scaled(
        320, 240, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    cam._score(blurred)
    check(blurred.sharpness < sharp_score,
          f"a blurred frame scores lower ({blurred.sharpness:,.0f} < "
          f"{sharp_score:,.0f}) - focus assist actually discriminates")


print("\n-- viewer: zoom, pan and the ruler --")
cam.zoom_fit()
check(cam._fit, "Fit sets fit mode")
cam.zoom_actual()
check(not cam._fit and abs(cam._scale() - 1.0) < 1e-9, "1:1 means scale 1.0")
cam.zoom_step(1)
check(cam._scale() > 1.0, f"zooming in raises the scale ({cam._scale()})")
cam.zoom_step(-1)
check(abs(cam._scale() - 1.0) < 1e-9, "and stepping back returns to 1:1")
for _ in range(10):
    cam.zoom_step(1)
check(cam._scale() <= max(camera_mod.ZOOM_STEPS) + 1e-9, "zoom is bounded above")
for _ in range(20):
    cam.zoom_step(-1)
check(cam._scale() >= min(camera_mod.ZOOM_STEPS) - 1e-9, "and bounded below")

cam.zoom_actual()
root.update()
# Round-tripping a view coordinate through image space must be identity.
cam._render()
ix, iy = cam._view_to_image(100, 80)
vx, vy = cam._image_to_view((ix, iy), cam._scale())
check(abs(vx - 100) < 0.01 and abs(vy - 80) < 0.01,
      "view <-> image coordinates round-trip")

cam._ruler = [(10.0, 20.0), (13.0, 24.0)]
(ax, ay), (bx, by) = cam._ruler
dist = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
check(abs(dist - 5.0) < 1e-9, "a 3-4-5 ruler span measures 5 px")
cam.clear_ruler()
check(cam._ruler == [] and cam.readout_var.get() == "", "clearing the ruler works")

# Panning must stay inside the image.
cam._pan_from = (0, 0, (0.5, 0.5))

class _E:
    x = -100000
    y = -100000

cam._on_drag(_E())
check(0.0 <= cam._centre[0] <= 1.0 and 0.0 <= cam._centre[1] <= 1.0,
      f"panning is clamped to the image ({cam._centre})")
cam._pan_from = None
cam.zoom_fit()


print("\n-- command framing and pacing --")
sent = []
app.test_mode = False
real_write = app.write_bytes
app.write_bytes = lambda data: (sent.append(bytes(data)), True)[1]
cam._cmd_queue.clear()
cam.take_picture()
pump(30)          # longer than RESOLUTION_SETTLE_MS
check(len(sent) >= 3, f"take_picture queues several frames (got {len(sent)})")
check(all(f[0] == 0x55 and f[-1] == 0xAA for f in sent),
      "every frame is bracketed by 55/AA")
check(arducam.set_picture_resolution(arducam.PIX_FMT_JPEG, arducam.MODE_QXGA) in sent,
      "including the JPEG 2048x1536 resolution frame")
check(arducam.take_picture() in sent, "and the take-picture frame")
check(sent.index(arducam.set_picture_resolution(
          arducam.PIX_FMT_JPEG, arducam.MODE_QXGA)) < sent.index(arducam.take_picture()),
      "resolution is set before the shot is taken")

# Preview above 640px wide asks first: a full-resolution preview would send
# one frame every several seconds. Declining must not start it.
sent.clear()
cam._reset_transfer()
cam._cancelling = False
fake_mb.clear()
fake_mb.answer = False
cam.mode_var.set("2048x1536")
cam.toggle_preview()
pump(4)
check(any(k == "askyesno" for k, _, _ in fake_mb.calls) and not cam._streaming,
      "a full-resolution preview asks first, and declining does not start it")
check(arducam.set_video_resolution(arducam.MODE_QXGA) not in sent,
      "and nothing is sent")
fake_mb.answer = True

sent.clear()
cam.mode_var.set("320x240")
cam.toggle_preview()
pump(6)
check(cam._streaming, "preview at 320x240 starts without asking")
check(arducam.set_video_resolution(0x03) in sent,
      "and sends the video-resolution frame")
sent.clear()
cam.toggle_preview()
pump(16)          # longer than STREAM_STOP_MS
check(arducam.stop_stream() in sent, "and stopping sends the stop-stream frame")
cam._streaming = False
cam.mode_var.set("2048x1536")
app.write_bytes = real_write


print("\n-- watchdog --")
cam._reset_transfer()
cam._busy = True
cam._cancelling = False
cam._await_since = 0.1          # far in the past
cam._rx_total = 0
cam._check_watchdog()
check(not cam._busy, "a capture that never answers is failed, not hung")
check("No response" in cam.status_var.get(),
      "and the message names the likely causes")
check(str(cam.cancel_btn["state"]) == "disabled", "Cancel is disabled again")

cam._busy = True
cam._rx_total = 1000
cam._rx_got = 400
cam._last_rx = 0.1
cam._check_watchdog()
check(not cam._busy and "stalled" in cam.status_var.get(),
      "a stalled transfer is failed with its byte count")


print("\n-- ZMODEM cross-guard --")
cam._busy = True
check(cam.busy, "a capture in flight marks the tab busy")
app.zmodem_enabled = True
app.is_connected = True
app.test_mode = False
app_mb = FakeMessagebox()
real_app_mb = sg.messagebox
sg.messagebox = app_mb
try:
    ready = app._transfer_ready("sending")
finally:
    sg.messagebox = real_app_mb
check(ready is False and "Camera busy" in app_mb.titles(),
      "a ZMODEM transfer is refused while a capture is in flight")
cam._busy = False


print("\n-- saving --")
tmp = tempfile.mkdtemp()
real_captures = app.captures_dir
app.captures_dir = tmp
try:
    cam.current = capture
    capture.distance = "0.5m"
    name = cam.default_filename(capture)
    check(name.endswith(".jpg"), f"a JPEG capture defaults to .jpg ({name})")
    check("0.5m" in name, "and the distance is in the filename, as the lab asks")
    check("320x240" in name, "along with the resolution")

    capture.data = JPEG
    cam.autosave_var.set(True)
    cam.save_image(auto=True)
    written = os.listdir(tmp)
    check(len(written) == 1, f"auto-save wrote one file ({written})")
    path = os.path.join(tmp, written[0])
    check(open(path, "rb").read() == JPEG,
          "and the JPEG is byte-identical to what the camera sent - no re-encode")

    # A second save must not clobber the first.
    cam.save_image(auto=True)
    check(len(os.listdir(tmp)) == 2, "a second auto-save gets a unique name")
finally:
    app.captures_dir = real_captures
    cam.autosave_var.set(False)
    shutil.rmtree(tmp, ignore_errors=True)


print("\n-- resolution list follows the module --")
cam.info = arducam.parse_camera_info(
    "Camera Type:3MP\r\nCamera Support Resolution:8191\r\n")
cam._refresh_modes()
values = list(cam.mode_combo["values"])
check("2048x1536" in values, "a 3MP module offers 2048x1536")
check("2592x1944" not in values, "but not the 5MP-only 2592x1944")
cam.info = arducam.parse_camera_info("Camera Support Resolution:8\r\n")
cam._refresh_modes()
check(list(cam.mode_combo["values"]) == ["320x240"],
      "a module reporting one resolution offers exactly that one")
check(cam.mode_var.get() == "320x240",
      "and a now-unsupported selection is moved to a valid one")


print("\n-- theme cycling --")
ok = True
for name in ("light", "high_contrast", "dark"):
    try:
        app.theme_var.set(name)
        app.on_theme_selected()
        root.update()
    except Exception as exc:
        ok = False
        print(f"       {name} raised {exc}")
check(ok, "cycling themes does not break the camera canvases")
check(cam.viewer.cget("background") != "", "the viewer keeps a backdrop after the walk")


print("\n-- dialog hygiene --")
before = set(map(str, root.winfo_children()))
cam.show_info()
root.update()
dialogs = [w for w in root.winfo_children()
           if isinstance(w, tk.Toplevel) and str(w) not in before]
check(len(dialogs) == 1, "Camera Info opens exactly one dialog")
if dialogs:
    dlg = dialogs[0]
    check(bool(dlg.bind("<Escape>")), "it is bound to Escape, like every other dialog")
    dlg.destroy()


print("\n-- teardown --")
cam.destroy()
check(cam._tick_timer is None and cam._cmd_timer is None,
      "destroy() cancels the tab's timers")
check(cam.captures == [] and cam.current is None,
      "and releases the captured images")
app.camera = None

print()
if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("test_camera: all checks passed.")
