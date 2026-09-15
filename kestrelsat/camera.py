"""The Camera tab: drive an ArduCAM Mega over the same serial link.

Copyright (C) 2026 Wyatt Harris
Written in a personal capacity. This is not a work of the United States
Government and was not prepared in the course of official duties.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

This exists for Astro 331 Lab 5, where students photograph an ISO 12233
resolution chart at several distances and work out the finest line spacing the
camera can still resolve. That is why the viewer zooms to 1:1 and carries a
ruler: a chart fitted to an 800 px window has thrown away most of the pixels
the lab is trying to measure. It is also why every capture is scored for
sharpness - Task 5 asks students to rotate the lens and retake until focus
stops improving, which is a much shorter loop when the number is on screen.

The tab never takes the serial port for itself. Its parser runs inside the
existing rx pump and hands back whatever was not camera traffic, so the
monitor and the plots keep working through a fifty-second capture. Decoding
uses QImage, which PyQt5 already provides and which - unlike QPixmap - needs
neither a QApplication nor the GUI thread.
"""

from __future__ import annotations

import base64
import os
import time
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from . import arducam, scaling, themes
from .widgets import ScrollableFrame, ToolTip

try:
    from PyQt5.QtCore import QBuffer, QByteArray, Qt
    from PyQt5.QtGui import QImage
    QT_IMAGING_AVAILABLE = True
except ImportError:  # pragma: no cover - PyQt5 is a declared dependency
    QImage = None
    QT_IMAGING_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:  # pragma: no cover
    np = None
    NUMPY_AVAILABLE = False


CAMERA_BAUD = 115200          # the firmware hard-codes this
COMMAND_GAP_MS = 25           # the MCU flushes anything arriving in one burst
RESOLUTION_SETTLE_MS = 500    # set-resolution also fires an untransmitted shot
STREAM_STOP_MS = 300
TICK_MS = 100                 # progress repaint + watchdog
FIRST_BYTE_TIMEOUT = 8.0      # seconds to wait for any response at all
STALL_TIMEOUT = 5.0           # seconds without progress mid-image
MAX_CAPTURES = 20             # thumbnails kept in the session strip
THUMB_PX = 64
FOCUS_ROI = 512               # sharpness is always measured over this many
                              # native pixels, so readings stay comparable
ZOOM_STEPS = (0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)


@dataclass
class Capture:
    """One image received from the camera, plus what we know about it."""

    data: bytes
    pix_fmt: int
    mode: int
    width: int
    height: int
    when: datetime = field(default_factory=datetime.now)
    distance: str = ""
    sharpness: Optional[float] = None
    saved_path: Optional[str] = None
    image: object = None          # QImage, decoded lazily
    thumb: object = None          # tk.PhotoImage for the strip
    trailer_ok: bool = True

    @property
    def label(self) -> str:
        stamp = self.when.strftime("%H:%M:%S")
        size = f"{self.width}x{self.height}"
        return f"{stamp}  {size}" + (f"  {self.distance}" if self.distance else "")


class CameraTab:
    """Camera control, live view and capture bookkeeping for one MCU."""

    def __init__(self, app, parent):
        self.app = app
        self.frame = ttk.Frame(parent)

        self.parser = arducam.PacketParser(on_progress=self._on_rx_progress)
        self.captures: List[Capture] = []
        self.current: Optional[Capture] = None
        self.info: Dict[str, object] = {}

        self._cmd_queue: List[Tuple[bytes, int]] = []
        self._cmd_timer = None
        self._tick_timer = None
        self._destroyed = False

        self._busy = False            # a still capture is in flight
        self._cancelling = False
        self._streaming = False
        self._await_since = 0.0
        self._last_rx = 0.0
        self._rx_got = 0
        self._rx_total = 0
        self._progress_dirty = False
        self._capture_started = 0.0
        self._decoder_ready = None

        # Viewer state, in image coordinates.
        self._photo = None
        self._fit = True
        self._zoom = 1.0
        self._centre = (0.5, 0.5)
        self._pan_from = None
        self._ruler: List[Tuple[float, float]] = []

        settings = app.settings
        self.pix_fmt_var = tk.StringVar(
            value=arducam.PIX_FMT_NAMES.get(
                int(settings.get("camera_pix_fmt", arducam.PIX_FMT_JPEG)), "JPEG"))
        self.mode_var = tk.StringVar(
            value=arducam.mode_label(int(settings.get("camera_mode", arducam.MODE_QXGA))))
        self.quality_var = tk.StringVar(
            value=arducam.QUALITY_NAMES.get(
                int(settings.get("camera_quality", arducam.QUALITY_DEFAULT)), "Default"))
        self.distance_var = tk.StringVar(value=str(settings.get("camera_distance", "")))
        self.autosave_var = tk.BooleanVar(value=bool(settings.get("camera_autosave", False)))
        self.trace_var = tk.BooleanVar(value=bool(settings.get("camera_trace", False)))
        self.roi_var = tk.StringVar(value="Centre")
        self.status_var = tk.StringVar(value="Not connected.")
        self.detail_var = tk.StringVar(value="")
        self.banner_var = tk.StringVar(value="")
        self.sharp_var = tk.StringVar(value="Sharpness: -")
        self.readout_var = tk.StringVar(value="")
        self.zoom_var = tk.StringVar(value="Fit")

        self._available_modes = list(arducam.MODE_TABLE)
        self.build_ui()
        self._schedule_tick()

    # -- construction --------------------------------------------------------

    def build_ui(self):
        """Build the tab.

        Pack order is load-bearing. Tk hands out cavity in packing order, so
        every row that must stay visible is packed before the viewer, which
        takes expand=True and would otherwise claim the lot. tests/test_resize
        enforces this.
        """
        px = lambda n: scaling.px(self.app.ui_scale, n)

        # 1. Connection banner.
        self.banner = ttk.Frame(self.frame)
        self.banner.pack(side=tk.TOP, fill=tk.X, padx=px(6), pady=(px(4), 0))
        self.banner_label = ttk.Label(self.banner, textvariable=self.banner_var,
                                      anchor=tk.W)
        self.banner_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 2. Progress and status.
        status_row = ttk.Frame(self.frame)
        status_row.pack(side=tk.BOTTOM, fill=tk.X, padx=px(6), pady=px(4))
        ttk.Label(status_row, textvariable=self.status_var).pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(status_row, text="Cancel", width=9,
                                     command=self.cancel_capture, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.RIGHT)
        self.progress = ttk.Progressbar(status_row, mode="determinate",
                                        length=px(220))
        self.progress.pack(side=tk.RIGHT, padx=px(8))
        ttk.Label(status_row, textvariable=self.detail_var).pack(side=tk.RIGHT)

        # 3. Capture strip.
        self.strip = tk.Canvas(self.frame, height=px(THUMB_PX + 16),
                               highlightthickness=0, takefocus=0)
        self.strip.pack(side=tk.BOTTOM, fill=tk.X, padx=px(6), pady=(0, px(2)))
        self.strip.bind("<Button-1>", self._on_strip_click)

        # 4. Control panel.
        panel = ScrollableFrame(self.frame, vscroll=True)
        panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, px(6)), pady=px(4))
        panel.canvas.configure(width=px(232))
        self._build_controls(panel.inner, px)

        # 5. The viewer, last, so it absorbs everything left over.
        self.viewer = tk.Canvas(self.frame, highlightthickness=0, takefocus=1)
        self.viewer.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                         padx=(px(6), px(4)), pady=px(4))
        self.viewer.bind("<Configure>", lambda e: self._render())
        self.viewer.bind("<ButtonPress-1>", self._on_press)
        self.viewer.bind("<B1-Motion>", self._on_drag)
        self.viewer.bind("<ButtonRelease-1>", self._on_release)
        self.viewer.bind("<Motion>", self._on_motion)
        self.viewer.bind("<Button-3>", self._on_right_click)
        # Bound on the widget, so these run before the global wheel router.
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.viewer.bind(seq, self._on_wheel)

        self.update_banner()
        self._set_controls_enabled(False)

    def _build_controls(self, box, px):
        pad = dict(padx=px(6), pady=px(2))

        cap = ttk.LabelFrame(box, text="Capture", padding=px(6))
        cap.pack(fill=tk.X, **pad)
        # Labels beside the fields rather than above them: three stacked
        # label-over-combo pairs pushed Save off the bottom of the panel at an
        # ordinary window size, and saving is half of what this tab is for.
        grid = ttk.Frame(cap)
        grid.pack(fill=tk.X)
        grid.columnconfigure(1, weight=1)
        self.fmt_combo = ttk.Combobox(
            grid, textvariable=self.pix_fmt_var, state="readonly", width=10,
            values=[arducam.PIX_FMT_NAMES[f] for f in sorted(arducam.PIX_FMT_NAMES)])
        self.mode_combo = ttk.Combobox(grid, textvariable=self.mode_var,
                                       state="readonly", width=10)
        self.quality_combo = ttk.Combobox(
            grid, textvariable=self.quality_var, state="readonly", width=10,
            values=[arducam.QUALITY_NAMES[q] for q in sorted(arducam.QUALITY_NAMES)])
        for row, (text, combo) in enumerate((("Format", self.fmt_combo),
                                             ("Size", self.mode_combo),
                                             ("Quality", self.quality_combo))):
            ttk.Label(grid, text=text).grid(row=row, column=0, sticky=tk.W,
                                            pady=px(1), padx=(0, px(4)))
            combo.grid(row=row, column=1, sticky=tk.EW, pady=px(1))
        self.fmt_combo.bind("<<ComboboxSelected>>", lambda e: self._on_capture_setting())
        self.mode_combo.bind("<<ComboboxSelected>>", lambda e: self._on_capture_setting())
        self.quality_combo.bind("<<ComboboxSelected>>", lambda e: self._on_quality())
        self.estimate_label = ttk.Label(cap, text="", wraplength=px(200),
                                        foreground=themes.CURRENT["fg_muted"])
        self.estimate_label.pack(anchor=tk.W, pady=(px(4), 0))

        self.take_btn = ttk.Button(cap, text="Take Picture", command=self.take_picture)
        self.take_btn.pack(fill=tk.X, pady=(px(6), 0))
        self.preview_btn = ttk.Button(cap, text="Start Preview",
                                      command=self.toggle_preview)
        self.preview_btn.pack(fill=tk.X, pady=(px(2), 0))
        ToolTip(self.preview_btn,
                "Preview streams JPEG frames over the same 115200 link, so it "
                "runs at roughly one frame a second. Useful for framing and "
                "coarse focus, not smooth video.")
        self.info_btn = ttk.Button(cap, text="Camera Info", command=self.show_info)
        self.info_btn.pack(fill=tk.X, pady=(px(2), 0))

        view = ttk.LabelFrame(box, text="View", padding=px(6))
        view.pack(fill=tk.X, **pad)
        row = ttk.Frame(view)
        row.pack(fill=tk.X)
        ttk.Button(row, text="Fit", width=5, command=self.zoom_fit).pack(side=tk.LEFT)
        ttk.Button(row, text="1:1", width=5, command=self.zoom_actual).pack(side=tk.LEFT)
        ttk.Button(row, text="-", width=3,
                   command=lambda: self.zoom_step(-1)).pack(side=tk.LEFT)
        ttk.Button(row, text="+", width=3,
                   command=lambda: self.zoom_step(1)).pack(side=tk.LEFT)
        ttk.Label(view, textvariable=self.zoom_var).pack(anchor=tk.W, pady=(px(2), 0))
        ttk.Label(view, textvariable=self.readout_var).pack(anchor=tk.W)
        ttk.Button(view, text="Clear ruler", command=self.clear_ruler).pack(
            fill=tk.X, pady=(px(4), 0))
        ToolTip(view,
                "Drag to pan, wheel to zoom. Click two points to measure the "
                "distance between them in image pixels - that is how you read "
                "off the finest line pair the camera still resolves.")

        save = ttk.LabelFrame(box, text="Save", padding=px(6))
        save.pack(fill=tk.X, **pad)
        ttk.Label(save, text="Distance (in filename)").pack(anchor=tk.W)
        ttk.Entry(save, textvariable=self.distance_var).pack(fill=tk.X)
        self.save_btn = ttk.Button(save, text="Save Image...", command=self.save_image)
        self.save_btn.pack(fill=tk.X, pady=(px(4), 0))
        ttk.Checkbutton(save, text="Auto-save every capture",
                        variable=self.autosave_var,
                        command=self._persist).pack(anchor=tk.W, pady=(px(2), 0))
        ttk.Checkbutton(save, text="Log raw packets",
                        variable=self.trace_var,
                        command=self._persist).pack(anchor=tk.W)

        focus = ttk.LabelFrame(box, text="Focus assist", padding=px(6))
        focus.pack(fill=tk.X, **pad)
        ttk.Label(focus, textvariable=self.sharp_var).pack(anchor=tk.W)
        self.sharp_canvas = tk.Canvas(focus, height=px(46), highlightthickness=0,
                                      takefocus=0)
        self.sharp_canvas.pack(fill=tk.X, pady=(px(2), 0))
        ttk.Label(focus, text="Region").pack(anchor=tk.W, pady=(px(4), 0))
        roi = ttk.Combobox(focus, textvariable=self.roi_var, state="readonly",
                           values=["Centre", "Whole frame"])
        roi.pack(fill=tk.X)
        roi.bind("<<ComboboxSelected>>", lambda e: self._rescore())
        ToolTip(focus,
                f"Laplacian variance over a fixed {FOCUS_ROI}x{FOCUS_ROI} block "
                "of native pixels, so successive shots are comparable. Higher "
                "is sharper. Only compare readings taken at the same distance, "
                "resolution and lighting.")

        self._refresh_modes()
        self._update_estimate()

    # -- connection state ----------------------------------------------------

    def update_banner(self):
        """Say plainly what is wrong with the link, if anything."""
        app = self.app
        if not app.is_connected:
            text = ("Not connected. Open the Connection tab, pick the MCU's port "
                    f"and connect at {CAMERA_BAUD} baud.")
        elif app.test_mode:
            text = ("TEST MODE: captures are generated locally so the tab can be "
                    "used without hardware.")
        else:
            try:
                baud = int(app.baud_var.get())
            except (ValueError, AttributeError):
                baud = None
            if baud != CAMERA_BAUD:
                text = (f"Connected at {baud} baud. The ArduCAM firmware runs at "
                        f"{CAMERA_BAUD}; change it on the Connection tab.")
            else:
                text = ""
        self.banner_var.set(text)
        try:
            colour = themes.CURRENT["warn"] if text else themes.CURRENT["fg_muted"]
            self.banner_label.configure(foreground=colour)
        except tk.TclError:
            pass

    def on_connect(self):
        self.parser.reset()
        self._reset_transfer()
        self.update_banner()
        self._set_controls_enabled(True)
        self.status_var.set("Ready.")
        if not self.app.test_mode:
            # Ask what module this is, so the resolution list matches reality.
            self._queue(arducam.get_camera_info())
            self._queue(arducam.get_firmware_version())
            self._queue(arducam.get_sdk_version())

    def on_disconnect(self):
        self._cancel_timers(commands=True)
        self.parser.reset()
        self._reset_transfer()
        self._streaming = False
        self.preview_btn.configure(text="Start Preview")
        self.update_banner()
        self._set_controls_enabled(False)
        self.status_var.set("Not connected.")

    def _set_controls_enabled(self, on: bool):
        state = tk.NORMAL if on else tk.DISABLED
        for widget in (self.take_btn, self.preview_btn, self.info_btn):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        # Saving works offline: a capture from earlier in the session is still
        # worth keeping after the cable has been pulled.
        try:
            self.save_btn.configure(
                state=tk.NORMAL if self.current is not None else tk.DISABLED)
        except tk.TclError:
            pass

    @property
    def busy(self) -> bool:
        return self._busy or self._streaming

    # -- the demux -----------------------------------------------------------

    def consume(self, data: bytes) -> bytes:
        """Pull camera packets out of the serial stream.

        Returns whatever was not camera traffic, for the normal text monitor.
        Called from _pump_rx on the Tk thread, so it may touch widgets - at
        ~10 KB/s this is about a hundred hundred-byte chunks a second, well
        inside the pump's budget.
        """
        try:
            packets, residual = self.parser.feed(data)
        except Exception as exc:                       # never break the monitor
            self.app.log_message(f"Camera: parser error: {exc}", "ERROR")
            self.parser.reset()
            return data
        for packet in packets:
            try:
                self._on_packet(packet)
            except Exception as exc:
                self.app.log_message(f"Camera: {exc}", "ERROR")
        return residual

    def _on_rx_progress(self, received: int, total: int):
        self._rx_got = received
        self._rx_total = total
        self._last_rx = time.monotonic()
        self._progress_dirty = True

    def _on_packet(self, packet):
        if isinstance(packet, arducam.StreamMarker):
            return
        if self.trace_var.get():
            self.app.log_message(
                f"Camera: packet type={packet.type:#04x} len={packet.declared_len} "
                f"fmt={packet.format_byte} trailer="
                f"{'ok' if packet.trailer_ok else 'MISSING'}"
                + (f" (+{packet.trailer_delta})" if packet.trailer_delta else ""),
                "SYSTEM")

        if packet.is_image:
            self._on_image(packet)
        elif packet.type == arducam.PKT_CAMERA_INFO:
            self.info = arducam.parse_camera_info(packet.text)
            self._refresh_modes()
            self.app.log_message(
                f"Camera: {self.info.get('camera_id', 'module')} reported, "
                f"{len(self.info.get('modes', []))} resolutions", "SYSTEM")
        elif packet.type == arducam.PKT_FRM_VERSION:
            self.info["firmware"] = packet.payload.hex()
        elif packet.type == arducam.PKT_SDK_VERSION:
            self.info["sdk"] = packet.payload.hex()
        elif packet.type == arducam.PKT_STREAM_OFF:
            self._streaming = False
            self.preview_btn.configure(text="Start Preview")
            self.status_var.set("Preview stopped.")
        else:
            text = packet.text
            if text:
                self.app.log_message(f"Camera: {text}", "SYSTEM")

    def _on_image(self, packet):
        self._reset_transfer()
        if self._cancelling:
            self._cancelling = False
            self.status_var.set("Capture cancelled.")
            self._set_controls_enabled(self.app.is_connected)
            return

        pix_fmt = self._selected_pix_fmt()
        mode = self._selected_mode()
        # The format byte is not trustworthy (stills and preview pack different
        # things into it), but a JPEG always starts FF D8, so that much can be
        # checked.
        if pix_fmt == arducam.PIX_FMT_JPEG and not packet.looks_like_jpeg():
            self.app.log_message(
                "Camera: expected JPEG but the payload has no SOI marker", "ERROR")
        width, height = arducam.size_for_mode(mode)
        capture = Capture(data=packet.payload, pix_fmt=pix_fmt, mode=mode,
                          width=width, height=height,
                          distance=self.distance_var.get().strip(),
                          trailer_ok=packet.trailer_ok)
        if not packet.trailer_ok:
            self.app.log_message(
                "Camera: image arrived without its end marker - it may be "
                "truncated", "ERROR")

        elapsed = time.monotonic() - self._capture_started if self._capture_started else 0
        self.add_capture(capture)
        if not self._streaming:
            self.status_var.set(
                f"Captured {len(packet.payload):,} bytes in {elapsed:.1f} s.")
            self._set_controls_enabled(self.app.is_connected)
        if self.autosave_var.get():
            self.save_image(auto=True)

    def add_capture(self, capture: Capture):
        """Decode, score and show a capture. Public so demo mode can reuse it."""
        self._decode(capture)
        self._score(capture)
        self.captures.append(capture)
        if len(self.captures) > MAX_CAPTURES:
            self.captures.pop(0)
        self.current = capture
        self._fit = True
        self._centre = (0.5, 0.5)
        self._ruler = []
        self._render()
        self._draw_strip()
        self._draw_sharpness()
        self._set_controls_enabled(self.app.is_connected)

    # -- imaging -------------------------------------------------------------

    def _ensure_decoder(self) -> bool:
        """Check Qt can decode, on first use.

        Deliberately not done in __init__: the QApplication is created later,
        in create_plot_tabs(). QImage does not need one, but leaving the check
        until first use keeps the ordering question from arising at all.
        """
        if self._decoder_ready is None:
            self._decoder_ready = QT_IMAGING_AVAILABLE
            if not self._decoder_ready:
                self.app.log_message(
                    "Camera: PyQt5 image support is unavailable, so captures "
                    "can be saved but not displayed.", "ERROR")
        return self._decoder_ready

    def _decode(self, capture: Capture):
        if not self._ensure_decoder():
            return
        try:
            if capture.pix_fmt == arducam.PIX_FMT_JPEG:
                image = QImage()
                if not image.loadFromData(capture.data, "JPG"):
                    raise ValueError("Qt could not decode the JPEG")
                capture.width, capture.height = image.width(), image.height()
            elif capture.pix_fmt == arducam.PIX_FMT_RGB565:
                rgb = arducam.rgb565_to_rgb888(capture.data, capture.width, capture.height)
                image = QImage(rgb, capture.width, capture.height,
                               capture.width * 3, QImage.Format_RGB888).copy()
            else:
                rgb = arducam.yuv422_to_rgb888(capture.data, capture.width, capture.height)
                image = QImage(rgb, capture.width, capture.height,
                               capture.width * 3, QImage.Format_RGB888).copy()
            capture.image = image
        except Exception as exc:
            capture.image = None
            self.app.log_message(f"Camera: could not decode image: {exc}", "ERROR")

    def _score(self, capture: Capture):
        """Laplacian variance over a fixed block of native pixels."""
        if capture.image is None or not NUMPY_AVAILABLE:
            return
        try:
            image = capture.image
            if self.roi_var.get() == "Centre":
                side = min(FOCUS_ROI, image.width(), image.height())
                image = image.copy((image.width() - side) // 2,
                                   (image.height() - side) // 2, side, side)
            grey = image.convertToFormat(QImage.Format_Grayscale8)
            ptr = grey.constBits()
            ptr.setsize(grey.sizeInBytes())
            rows, stride = grey.height(), grey.bytesPerLine()
            block = np.frombuffer(bytes(ptr), np.uint8).reshape(rows, stride)
            block = block[:, :grey.width()].astype(np.float32)
            lap = (4 * block[1:-1, 1:-1] - block[:-2, 1:-1] - block[2:, 1:-1]
                   - block[1:-1, :-2] - block[1:-1, 2:])
            capture.sharpness = float(lap.var())
        except Exception:
            capture.sharpness = None

    def _rescore(self):
        if self.current is not None:
            self._score(self.current)
            self._draw_sharpness()

    def _photo_from(self, image) -> Optional[tk.PhotoImage]:
        """QImage -> tk.PhotoImage, via PNG in memory."""
        try:
            blob = QByteArray()
            buffer = QBuffer(blob)
            buffer.open(QBuffer.WriteOnly)
            ok = image.save(buffer, "PNG")
            buffer.close()
            if not ok:
                return None
            return tk.PhotoImage(data=base64.b64encode(bytes(blob)).decode("ascii"))
        except Exception:
            return None

    # -- the viewer ----------------------------------------------------------

    def _viewport(self) -> Tuple[int, int]:
        return max(self.viewer.winfo_width(), 1), max(self.viewer.winfo_height(), 1)

    def _scale(self) -> float:
        capture = self.current
        if capture is None or capture.image is None:
            return 1.0
        if self._fit:
            view_w, view_h = self._viewport()
            return min(view_w / capture.image.width(), view_h / capture.image.height())
        return self._zoom

    def _render(self):
        """Draw the visible part of the image, and only that.

        A full-resolution PhotoImage of a 2048x1536 frame is ~12 MB in Tk's
        cache, and one per capture would exhaust memory. Cropping to the
        viewport first keeps the cost proportional to the window instead.
        """
        if self._destroyed:
            return
        try:
            self.viewer.delete("all")
        except tk.TclError:
            return
        capture = self.current
        palette = themes.CURRENT
        try:
            self.viewer.configure(background=palette["surface_alt"])
        except tk.TclError:
            pass

        if capture is None or capture.image is None:
            view_w, view_h = self._viewport()
            message = ("Take a picture to see it here."
                       if capture is None else "This capture could not be decoded.")
            self.viewer.create_text(view_w // 2, view_h // 2, text=message,
                                    fill=palette["fg_muted"])
            self.zoom_var.set("Fit")
            return

        image = capture.image
        view_w, view_h = self._viewport()
        scale = self._scale()
        # Source rectangle, in image pixels, centred on the current point.
        src_w = min(image.width(), max(1, int(round(view_w / scale))))
        src_h = min(image.height(), max(1, int(round(view_h / scale))))
        cx = self._centre[0] * image.width()
        cy = self._centre[1] * image.height()
        x = int(round(min(max(cx - src_w / 2, 0), image.width() - src_w)))
        y = int(round(min(max(cy - src_h / 2, 0), image.height() - src_h)))

        crop = image.copy(x, y, src_w, src_h)
        scaled = crop.scaled(max(1, int(src_w * scale)), max(1, int(src_h * scale)),
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)
        photo = self._photo_from(scaled)
        if photo is None:
            self.viewer.create_text(view_w // 2, view_h // 2,
                                    text="Could not render this image.",
                                    fill=palette["error"])
            return
        self._photo = photo           # keep a reference or Tk drops the image
        self._draw_origin = (max(0, (view_w - photo.width()) // 2),
                             max(0, (view_h - photo.height()) // 2))
        self._src_origin = (x, y)
        self.viewer.create_image(self._draw_origin[0], self._draw_origin[1],
                                 anchor=tk.NW, image=photo)
        self.zoom_var.set("Fit ({:.0f}%)".format(scale * 100) if self._fit
                          else "{:.0f}%".format(scale * 100))
        self._draw_ruler(scale)

    def _draw_ruler(self, scale: float):
        if len(self._ruler) < 1:
            return
        palette = themes.CURRENT
        points = [self._image_to_view(p, scale) for p in self._ruler]
        for vx, vy in points:
            self.viewer.create_line(vx - 6, vy, vx + 6, vy, fill=palette["accent"])
            self.viewer.create_line(vx, vy - 6, vx, vy + 6, fill=palette["accent"])
        if len(points) == 2:
            self.viewer.create_line(points[0][0], points[0][1],
                                    points[1][0], points[1][1],
                                    fill=palette["accent"], dash=(4, 2))

    def _image_to_view(self, point, scale):
        ox, oy = getattr(self, "_draw_origin", (0, 0))
        sx, sy = getattr(self, "_src_origin", (0, 0))
        return (ox + (point[0] - sx) * scale, oy + (point[1] - sy) * scale)

    def _view_to_image(self, vx, vy):
        scale = self._scale()
        ox, oy = getattr(self, "_draw_origin", (0, 0))
        sx, sy = getattr(self, "_src_origin", (0, 0))
        return (sx + (vx - ox) / scale, sy + (vy - oy) / scale)

    def zoom_fit(self):
        self._fit = True
        self._render()

    def zoom_actual(self):
        self._fit = False
        self._zoom = 1.0
        self._render()

    def zoom_step(self, direction: int):
        current = self._scale()
        steps = ZOOM_STEPS if direction > 0 else tuple(reversed(ZOOM_STEPS))
        nxt = next((s for s in steps
                    if (s > current * 1.01 if direction > 0 else s < current * 0.99)),
                   current)
        self._fit = False
        self._zoom = nxt
        self._render()

    def clear_ruler(self):
        self._ruler = []
        self.readout_var.set("")
        self._render()

    def _on_wheel(self, event):
        delta = getattr(event, "delta", 0)
        if event.num == 4:
            delta = 120
        elif event.num == 5:
            delta = -120
        self.zoom_step(1 if delta > 0 else -1)
        return "break"               # do not also scroll an enclosing region

    def _on_press(self, event):
        self._pan_from = (event.x, event.y, self._centre)
        self._press_at = (event.x, event.y)

    def _on_drag(self, event):
        if self._pan_from is None or self.current is None or self.current.image is None:
            return
        x0, y0, centre = self._pan_from
        scale = self._scale()
        image = self.current.image
        dx = (event.x - x0) / scale / image.width()
        dy = (event.y - y0) / scale / image.height()
        self._centre = (min(max(centre[0] - dx, 0.0), 1.0),
                        min(max(centre[1] - dy, 0.0), 1.0))
        self._render()

    def _on_release(self, event):
        moved = (abs(event.x - self._press_at[0]) + abs(event.y - self._press_at[1])
                 if getattr(self, "_press_at", None) else 99)
        self._pan_from = None
        if moved > 3 or self.current is None or self.current.image is None:
            return
        # A click, not a drag: drop a ruler point.
        point = self._view_to_image(event.x, event.y)
        if len(self._ruler) >= 2:
            self._ruler = []
        self._ruler.append(point)
        if len(self._ruler) == 2:
            (ax, ay), (bx, by) = self._ruler
            dist = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
            self.readout_var.set(f"Ruler: {dist:.1f} px")
        else:
            self.readout_var.set("Ruler: click a second point")
        self._render()

    def _on_right_click(self, event):
        self.clear_ruler()

    def _on_motion(self, event):
        if self.current is None or self.current.image is None or self._ruler:
            return
        ix, iy = self._view_to_image(event.x, event.y)
        image = self.current.image
        if 0 <= ix < image.width() and 0 <= iy < image.height():
            self.readout_var.set(f"x={int(ix)}  y={int(iy)}")

    # -- capture strip and sharpness history ---------------------------------

    def _draw_strip(self):
        px = lambda n: scaling.px(self.app.ui_scale, n)
        try:
            self.strip.delete("all")
            self.strip.configure(background=themes.CURRENT["surface"])
        except tk.TclError:
            return
        size = px(THUMB_PX)
        for index, capture in enumerate(self.captures):
            if capture.thumb is None and capture.image is not None:
                capture.thumb = self._photo_from(
                    capture.image.scaled(size, size, Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
            x = px(6) + index * (size + px(8))
            if capture.thumb is not None:
                self.strip.create_image(x, px(6), anchor=tk.NW, image=capture.thumb)
            else:
                self.strip.create_rectangle(x, px(6), x + size, px(6) + size,
                                            outline=themes.CURRENT["border"])
            if capture is self.current:
                self.strip.create_rectangle(x - 2, px(4), x + size + 2, px(8) + size,
                                            outline=themes.CURRENT["accent"], width=2)
        self.strip.configure(scrollregion=self.strip.bbox("all"))

    def _on_strip_click(self, event):
        px = lambda n: scaling.px(self.app.ui_scale, n)
        size = px(THUMB_PX) + px(8)
        index = max(0, (event.x - px(6)) // size)
        if index < len(self.captures):
            self.current = self.captures[int(index)]
            self._fit = True
            self._ruler = []
            self.readout_var.set("")
            self._render()
            self._draw_strip()
            self._draw_sharpness()

    def _draw_sharpness(self):
        capture = self.current
        if capture is not None and capture.sharpness is not None:
            self.sharp_var.set(f"Sharpness: {capture.sharpness:,.0f}")
        elif capture is not None:
            self.sharp_var.set("Sharpness: unavailable")
        else:
            self.sharp_var.set("Sharpness: -")

        try:
            self.sharp_canvas.delete("all")
            self.sharp_canvas.configure(background=themes.CURRENT["surface"])
        except tk.TclError:
            return
        scores = [c.sharpness for c in self.captures if c.sharpness is not None]
        if not scores:
            return
        best = max(scores)
        width = max(self.sharp_canvas.winfo_width(), 60)
        height = max(self.sharp_canvas.winfo_height(), 20)
        slot = width / max(len(scores), 1)
        for index, score in enumerate(scores):
            bar = (score / best) * (height - 4) if best else 0
            x0 = index * slot + 1
            colour = (themes.CURRENT["ok"] if score == best
                      else themes.CURRENT["accent"])
            self.sharp_canvas.create_rectangle(x0, height - bar, x0 + slot - 2,
                                               height, fill=colour, outline="")

    # -- commands ------------------------------------------------------------

    def _queue(self, payload: bytes, gap_ms: int = COMMAND_GAP_MS):
        """Queue one frame. The MCU flushes anything arriving in the same
        burst, so frames are written one at a time with a gap between."""
        self._cmd_queue.append((payload, gap_ms))
        if self._cmd_timer is None:
            self._cmd_timer = self.app.root.after(1, self._drain_commands)

    def _drain_commands(self):
        self._cmd_timer = None
        if self._destroyed or not self._cmd_queue:
            return
        payload, gap = self._cmd_queue.pop(0)
        self.app.write_bytes(payload)
        if self._cmd_queue:
            self._cmd_timer = self.app.root.after(gap, self._drain_commands)

    def _selected_pix_fmt(self) -> int:
        for value, name in arducam.PIX_FMT_NAMES.items():
            if name == self.pix_fmt_var.get():
                return value
        return arducam.PIX_FMT_JPEG

    def _selected_mode(self) -> int:
        label = self.mode_var.get()
        for mode in arducam.MODE_TABLE:
            if arducam.mode_label(mode) == label:
                return mode
        return arducam.MODE_QXGA

    def _selected_quality(self) -> int:
        for value, name in arducam.QUALITY_NAMES.items():
            if name == self.quality_var.get():
                return value
        return arducam.QUALITY_DEFAULT

    def _refresh_modes(self):
        modes = self.info.get("modes") if isinstance(self.info, dict) else None
        self._available_modes = list(modes) if modes else list(arducam.MODE_TABLE)
        labels = [arducam.mode_label(m) for m in self._available_modes]
        try:
            self.mode_combo.configure(values=labels)
        except tk.TclError:
            return
        if self.mode_var.get() not in labels:
            preferred = (arducam.mode_label(arducam.MODE_QXGA)
                         if arducam.MODE_QXGA in self._available_modes else labels[-1])
            self.mode_var.set(preferred)
        self._update_estimate()

    def _on_capture_setting(self):
        self._update_estimate()
        self._persist()

    def _update_estimate(self):
        mode = self._selected_mode()
        pix_fmt = self._selected_pix_fmt()
        raw = arducam.estimate_raw_bytes(mode, pix_fmt)
        if raw:
            secs = arducam.estimate_seconds(raw)
            text = (f"{arducam.PIX_FMT_NAMES[pix_fmt]} at {arducam.mode_label(mode)} "
                    f"is {raw / 1e6:.1f} MB - about {secs / 60:.0f} min at 115200. "
                    "JPEG is far quicker.")
        else:
            text = "JPEG size varies; a full-resolution frame takes roughly 30-50 s."
        try:
            self.estimate_label.configure(text=text)
        except tk.TclError:
            pass

    def _on_quality(self):
        self._persist()
        if self.app.is_connected and not self.app.test_mode:
            self._queue(arducam.set_image_quality(self._selected_quality()))

    # -- capture -------------------------------------------------------------

    def take_picture(self):
        if not self._ready("taking a picture"):
            return
        self._busy = True
        self._cancelling = False
        self._capture_started = time.monotonic()
        self._await_since = time.monotonic()
        self._last_rx = time.monotonic()
        self._rx_got = self._rx_total = 0
        self.status_var.set("Capturing...")
        self.detail_var.set("")
        self.progress.configure(value=0, maximum=100)
        self.take_btn.configure(state=tk.DISABLED)
        self.preview_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)

        if self.app.test_mode:
            self._demo_capture()
            return
        self._queue(arducam.set_image_quality(self._selected_quality()))
        self._queue(arducam.set_picture_resolution(self._selected_pix_fmt(),
                                                   self._selected_mode()),
                    gap_ms=RESOLUTION_SETTLE_MS)
        self._queue(arducam.take_picture())

    def toggle_preview(self):
        if self._streaming:
            self._queue(arducam.stop_stream(), gap_ms=STREAM_STOP_MS)
            self.status_var.set("Stopping preview...")
            return
        if not self._ready("starting preview"):
            return
        mode = self._selected_mode()
        width, _ = arducam.size_for_mode(mode)
        if width > 640 and not messagebox.askyesno(
                "Preview resolution",
                f"Preview at {arducam.mode_label(mode)} sends a frame every "
                "several seconds over a 115200 link.\n\n"
                "320x240 or 640x480 is far more usable. Start anyway?"):
            return
        self._streaming = True
        self._last_rx = time.monotonic()
        self.preview_btn.configure(text="Stop Preview")
        self.status_var.set("Preview running (about one frame a second).")
        self._queue(arducam.set_video_resolution(mode))

    def cancel_capture(self):
        """Give up on the image, but keep reading it.

        There is no abort at the MCU - it is inside a blocking read of the
        whole FIFO - so the bytes are still coming. Dropping them on the floor
        would leave the parser mid-packet and corrupt the next capture, so the
        packet is consumed to its end and discarded.
        """
        if self._streaming:
            self._queue(arducam.stop_stream(), gap_ms=STREAM_STOP_MS)
            return
        if not self._busy:
            return
        self._cancelling = True
        remaining = max(self._rx_total - self._rx_got, 0)
        self.status_var.set(
            f"Cancelling - discarding the remaining {remaining:,} bytes."
            if remaining else "Cancelling...")

    def _ready(self, action: str) -> bool:
        app = self.app
        if not app.is_connected:
            messagebox.showerror("Not connected",
                                 f"Connect to the MCU's port before {action}.")
            return False
        if self._busy or self._streaming:
            messagebox.showinfo("Camera busy",
                                "Wait for the current capture to finish.")
            return False
        if app._transfer_active.is_set():
            messagebox.showinfo("Transfer in progress",
                                "A ZMODEM file transfer has the port. Wait for "
                                "it to finish.")
            return False
        return True

    def _reset_transfer(self):
        self._busy = False
        self._rx_got = self._rx_total = 0
        self._await_since = 0.0
        self._progress_dirty = True
        try:
            self.cancel_btn.configure(state=tk.DISABLED)
            self.progress.configure(value=0)
            self.detail_var.set("")
        except tk.TclError:
            pass

    # -- timers --------------------------------------------------------------

    def _schedule_tick(self):
        if self._destroyed:
            return
        self._tick_timer = self.app.root.after(TICK_MS, self._tick)

    def _tick(self):
        self._tick_timer = None
        if self._destroyed:
            return
        try:
            self._update_progress()
            self._check_watchdog()
        except tk.TclError:
            return
        finally:
            self._schedule_tick()

    def _update_progress(self):
        if not self._progress_dirty:
            return
        self._progress_dirty = False
        got, total = self._rx_got, self._rx_total
        if total > 0 and (self._busy or self._streaming):
            self.progress.configure(maximum=total, value=got)
            elapsed = max(time.monotonic() - self._capture_started, 0.001)
            rate = got / elapsed if elapsed else 0
            eta = (total - got) / rate if rate > 0 else 0
            self.detail_var.set(
                f"{got:,} / {total:,} B   {rate / 1000:.1f} kB/s   {eta:.0f}s left")

    def _check_watchdog(self):
        """Fail a capture that has stopped arriving rather than hanging."""
        if not self._busy or self._cancelling:
            return
        now = time.monotonic()
        if self._rx_total == 0:
            if self._await_since and now - self._await_since > FIRST_BYTE_TIMEOUT:
                self._fail("No response from the camera. Check the port is at "
                           f"{CAMERA_BAUD} baud, the wiring, and that the blue "
                           "STAT LED is lit.")
        elif now - self._last_rx > STALL_TIMEOUT:
            self._fail(f"Transfer stalled at {self._rx_got:,} of "
                       f"{self._rx_total:,} bytes.")

    def _fail(self, message: str):
        self.parser.reset()
        self._reset_transfer()
        self._cancelling = False
        self.status_var.set(message)
        self.app.log_message(f"Camera: {message}", "ERROR")
        self._set_controls_enabled(self.app.is_connected)

    # -- demo mode -----------------------------------------------------------

    def _demo_capture(self):
        """Synthesise a capture in TEST MODE.

        The bytes go through the real codec and the real packet parser, so this
        exercises the same path hardware does - it is a rehearsal, not a mock.
        """
        mode = self._selected_mode()
        pix_fmt = self._selected_pix_fmt()
        payload = self.make_test_pattern(mode, pix_fmt)
        if payload is None:
            self._fail("TEST MODE needs PyQt5 image support to synthesise a frame.")
            return
        stream = arducam.pack_image_packet(mode, pix_fmt, payload)
        self._rx_total = len(payload)
        # Deliver in wire-sized pieces so the progress bar and ETA behave as
        # they would on a real link, just faster.
        self._demo_feed(stream, 0)

    def _demo_feed(self, stream: bytes, offset: int):
        if self._destroyed or not self._busy:
            return
        step = 8192
        chunk = stream[offset:offset + step]
        if not chunk:
            return
        self.app._post_rx(("data", chunk))
        if offset + step < len(stream):
            self.app.root.after(30, self._demo_feed, stream, offset + step)

    def make_test_pattern(self, mode: int, pix_fmt: int) -> Optional[bytes]:
        """A resolution-chart-flavoured frame for TEST MODE.

        Converging line wedges in both orientations, plus a slanted edge, so
        that zooming in and running the ruler over it behaves the way the real
        chart does - which is the point of being able to rehearse the lab
        without hardware.
        """
        if not self._ensure_decoder():
            return None
        try:
            from PyQt5.QtGui import QColor, QFont, QPainter

            width, height = arducam.size_for_mode(mode)
            image = QImage(width, height, QImage.Format_RGB888)
            image.fill(QColor(242, 242, 242))
            painter = QPainter(image)
            ink = QColor(24, 24, 24)

            def wedge(x0, y0, w, h, vertical, start_pitch):
                """Bars whose pitch shrinks across the block."""
                pos, pitch = 0.0, float(start_pitch)
                span = w if vertical else h
                while pos < span and pitch >= 1.0:
                    bar = max(1, int(pitch / 2))
                    if vertical:
                        painter.fillRect(x0 + int(pos), y0, bar, h, ink)
                    else:
                        painter.fillRect(x0, y0 + int(pos), w, bar, ink)
                    pos += pitch
                    pitch *= 0.965

            margin = max(int(min(width, height) * 0.05), 4)
            block_w = int(width * 0.4)
            block_h = int(height * 0.32)
            wedge(margin, margin, block_w, block_h, True, max(width / 42.0, 3.0))
            wedge(width - margin - block_w, margin, block_w, block_h, False,
                  max(height / 30.0, 3.0))
            wedge(margin, height - margin - block_h, block_w, block_h, False,
                  max(height / 30.0, 3.0))

            # A slanted edge, the other half of an ISO 12233 target.
            edge_x = width - margin - block_w
            edge_y = height - margin - block_h
            for row in range(block_h):
                painter.fillRect(edge_x + int(row * 0.18), edge_y + row,
                                 block_w - int(block_h * 0.18), 1, ink)

            # Corner markers, so panning at 1:1 has something to orient by.
            tick = max(int(min(width, height) * 0.04), 4)
            for cx, cy in ((0, 0), (width - tick, 0), (0, height - tick),
                           (width - tick, height - tick)):
                painter.fillRect(cx, cy, tick, tick, ink)

            if min(width, height) >= 240:
                painter.setPen(ink)
                font = QFont()
                font.setPixelSize(max(int(height * 0.05), 8))
                painter.setFont(font)
                painter.drawText(int(width * 0.42), int(height * 0.54),
                                 "TEST MODE %dx%d" % (width, height))
            painter.end()

            if pix_fmt == arducam.PIX_FMT_JPEG:
                blob = QByteArray()
                buffer = QBuffer(blob)
                buffer.open(QBuffer.WriteOnly)
                ok = image.save(buffer, "JPG", 88)
                buffer.close()
                return bytes(blob) if ok else None

            # Uncompressed formats: a frame of the right size for the decoder.
            return bytes(width * height * 2)
        except Exception:
            return None

    # -- saving --------------------------------------------------------------

    def default_filename(self, capture: Capture) -> str:
        stamp = capture.when.strftime("%Y-%m-%d_%H%M%S")
        size = f"{capture.width}x{capture.height}"
        distance = (capture.distance or self.distance_var.get()).strip()
        distance = "".join(ch for ch in distance if ch.isalnum() or ch in ".-_")
        suffix = ".jpg" if capture.pix_fmt == arducam.PIX_FMT_JPEG else ".png"
        parts = [stamp, "cam", size] + ([distance] if distance else [])
        return "_".join(parts) + suffix

    def save_image(self, auto: bool = False):
        capture = self.current
        if capture is None:
            messagebox.showinfo("No image", "Take a picture first.")
            return
        name = self.default_filename(capture)
        if auto:
            from .zmodem import unique_path
            path = unique_path(os.path.join(self.app.captures_dir, name))
        else:
            path = filedialog.asksaveasfilename(
                title="Save camera image",
                initialdir=self.app.captures_dir,
                initialfile=name,
                defaultextension=os.path.splitext(name)[1],
                filetypes=[("JPEG image", "*.jpg"), ("PNG image", "*.png"),
                           ("All files", "*.*")])
            if not path:
                return
        try:
            self._write_capture(capture, path)
        except Exception as exc:
            self.app.log_message(f"Camera: could not save image: {exc}", "ERROR")
            if not auto:
                messagebox.showerror("Save failed", str(exc))
            return
        capture.saved_path = path
        self.app.log_message(f"Camera: saved {path}", "SYSTEM")
        self.status_var.set(f"Saved {os.path.basename(path)}")
        self._persist()

    def _write_capture(self, capture: Capture, path: str):
        """Write the capture out.

        A JPEG is written byte for byte as it came off the camera - decoding
        and re-encoding would cost quality for nothing. Raw formats are saved
        as PNG, with the original bytes alongside.
        """
        if capture.pix_fmt == arducam.PIX_FMT_JPEG and path.lower().endswith(
                (".jpg", ".jpeg")):
            with open(path, "wb") as handle:
                handle.write(capture.data)
            return
        if capture.image is None:
            raise ValueError("this capture could not be decoded, so only the "
                             "raw bytes can be saved")
        if not capture.image.save(path):
            raise ValueError(f"Qt could not write {os.path.basename(path)}")
        if capture.pix_fmt != arducam.PIX_FMT_JPEG:
            raw_path = os.path.splitext(path)[0] + ".raw"
            with open(raw_path, "wb") as handle:
                handle.write(capture.data)

    # -- camera info ---------------------------------------------------------

    def show_info(self):
        px = lambda n: scaling.px(self.app.ui_scale, n)
        dialog = tk.Toplevel(self.app.root)
        dialog.title("Camera Info")
        dialog.transient(self.app.root)
        body = ttk.Frame(dialog, padding=px(12))
        body.pack(fill=tk.BOTH, expand=True)

        if self.info:
            rows = [
                ("Module", self.info.get("camera_id", "unknown")),
                ("Resolutions", ", ".join(
                    arducam.mode_label(m) for m in self.info.get("modes", []))),
                ("Exposure range", f"{self.info.get('exposure_min', '?')} - "
                                   f"{self.info.get('exposure_max', '?')}"),
                ("Gain range", f"{self.info.get('gain_min', '?')} - "
                               f"{self.info.get('gain_max', '?')}"),
                ("Autofocus", "yes" if self.info.get("support_focus") else "no"),
                ("Sharpness control",
                 "yes" if self.info.get("support_sharpness") else "no"),
                ("Firmware", self.info.get("firmware", "not reported")),
                ("SDK", self.info.get("sdk", "not reported")),
            ]
        else:
            rows = [("Status", "The camera has not reported yet. Connect at "
                               f"{CAMERA_BAUD} baud and press Camera Info again.")]

        for label, value in rows:
            row = ttk.Frame(body)
            row.pack(fill=tk.X, pady=px(2))
            ttk.Label(row, text=label + ":", width=18, anchor=tk.W).pack(side=tk.LEFT)
            ttk.Label(row, text=str(value), wraplength=px(320),
                      anchor=tk.W, justify=tk.LEFT).pack(side=tk.LEFT, fill=tk.X,
                                                         expand=True)

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(px(10), 0))
        ttk.Button(buttons, text="Refresh", command=self._refresh_info).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side=tk.RIGHT)

        self.app._prepare_dialog(dialog, resizable=(True, True))
        self.app.themes.restyle(dialog)

    def _refresh_info(self):
        if self.app.is_connected and not self.app.test_mode:
            self._queue(arducam.get_camera_info())
            self._queue(arducam.get_firmware_version())
            self._queue(arducam.get_sdk_version())

    # -- settings, theming, teardown -----------------------------------------

    def _persist(self):
        self.app._write_settings({
            "camera_pix_fmt": self._selected_pix_fmt(),
            "camera_mode": self._selected_mode(),
            "camera_quality": self._selected_quality(),
            "camera_autosave": bool(self.autosave_var.get()),
            "camera_trace": bool(self.trace_var.get()),
            "camera_distance": self.distance_var.get().strip(),
        })

    def on_theme(self, palette):
        """Re-colour the classic Tk canvases.

        ThemeManager's tree walk paints every Canvas with the generic surface
        colour; this runs afterwards, as a listener, so the viewer keeps its
        own backdrop.
        """
        try:
            self.viewer.configure(background=palette["surface_alt"])
            self.strip.configure(background=palette["surface"])
            self.sharp_canvas.configure(background=palette["surface"])
            self.estimate_label.configure(foreground=palette["fg_muted"])
        except tk.TclError:
            return
        self.update_banner()
        self._render()
        self._draw_strip()
        self._draw_sharpness()

    def _cancel_timers(self, commands: bool = False):
        for attr in ("_tick_timer", "_cmd_timer"):
            timer = getattr(self, attr, None)
            if timer is not None:
                try:
                    self.app.root.after_cancel(timer)
                except Exception:
                    pass
                setattr(self, attr, None)
        if commands:
            self._cmd_queue.clear()
        if not self._destroyed and not commands:
            self._schedule_tick()

    def destroy(self):
        self._destroyed = True
        self._cancel_timers(commands=True)
        self._photo = None
        for capture in self.captures:
            capture.thumb = None
            capture.image = None
        self.captures = []
        self.current = None
