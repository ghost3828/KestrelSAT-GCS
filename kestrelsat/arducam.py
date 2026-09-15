"""ArduCAM Mega serial protocol: command framing and response parsing.

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

This speaks to the stock Arducam "full_featured" sketch (Arducam_Mega 3.0.0)
exactly as Arducam's own GUI tool does, so the same firmware serves both. No
part of this module imports Tk or Qt: it is pure bytes in, packets out, which
is what makes the whole protocol testable without hardware or a display.

Host -> MCU is one frame, 0x55 <cmd> [args...] 0xAA. The sketch reads a byte,
and on 0x55 waits 2 ms, reads until 0xAA, then *flushes whatever else arrived*
(main.cpp). Two consequences the caller must honour: write each frame in a
single write(), and leave a gap between frames or the second one is discarded.
No argument byte may be 0xAA, which frame() enforces.

MCU -> host is 0xFF 0xAA <type> <len:4 LE> <payload> 0xFF 0xBB, with three
quirks in Arducam's firmware that this parser absorbs rather than trips over:

  * Type 0x05 declares len=6 and then writes 7 bytes (ArducamLink.cpp). So a
    text payload's length is a hint, and the trailer is what really ends it.
  * stop_preivew() writes a bare 0xFF 0xBB to close the frame it interrupted
    before sending its 0x06 packet, so a trailer can arrive while idle.
  * The image header's format byte means different things in the two paths
    that emit it: stills pack the resolution mode into the high nibble, the
    preview callback packs the pixel format there. It is therefore reported
    as-is and never trusted - the caller knows what it asked for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on a numpy-less install
    np = None
    NUMPY_AVAILABLE = False


# -- framing constants -------------------------------------------------------

CMD_START = 0x55
CMD_END = 0xAA
SOF = b"\xff\xaa"          # start of a response packet
EOF = b"\xff\xbb"          # end of a response packet

# Commands, from ArducamLink.h.
CMD_RESET = 0xFF
CMD_SET_PICTURE_RESOLUTION = 0x01
CMD_SET_VIDEO_RESOLUTION = 0x02
CMD_SET_BRIGHTNESS = 0x03
CMD_SET_CONTRAST = 0x04
CMD_SET_SATURATION = 0x05
CMD_SET_EV = 0x06
CMD_SET_WHITEBALANCE = 0x07
CMD_SET_SPECIAL_EFFECTS = 0x08
CMD_SET_FOCUS_CONTROL = 0x09
CMD_SET_EXPOSUREANDGAIN_CONTROL = 0x0A
CMD_SET_WHITEBALANCE_CONTROL = 0x0C
CMD_SET_MANUAL_GAIN = 0x0D
CMD_SET_MANUAL_EXPOSURE = 0x0E
CMD_GET_CAMERA_INFO = 0x0F
CMD_TAKE_PICTURE = 0x10
CMD_SET_SHARPNESS = 0x11
CMD_DEBUG_WRITE_REGISTER = 0x12
CMD_STOP_STREAM = 0x21
CMD_GET_FRM_VER_INFO = 0x30
CMD_GET_SDK_VER_INFO = 0x40
CMD_SET_IMAGE_QUALITY = 0x50

# Response packet types.
PKT_IMAGE = 0x01
PKT_CAMERA_INFO = 0x02
PKT_FRM_VERSION = 0x03
PKT_SDK_VERSION = 0x05
PKT_STREAM_OFF = 0x06
PKT_TEXT = 0x07
PKT_TEXT_ALT = 0x08

TEXT_TYPES = frozenset((PKT_CAMERA_INFO, PKT_FRM_VERSION, PKT_SDK_VERSION,
                        PKT_STREAM_OFF, PKT_TEXT, PKT_TEXT_ALT))
KNOWN_TYPES = frozenset((PKT_IMAGE,)) | TEXT_TYPES

# Pixel formats (CAM_IMAGE_PIX_FMT).
PIX_FMT_JPEG = 0x01
PIX_FMT_RGB565 = 0x02
PIX_FMT_YUV = 0x03
PIX_FMT_NAMES = {PIX_FMT_JPEG: "JPEG", PIX_FMT_RGB565: "RGB565", PIX_FMT_YUV: "YUV"}

# Image quality (IMAGE_QUALITY).
QUALITY_HIGH = 0
QUALITY_DEFAULT = 1
QUALITY_LOW = 2
QUALITY_NAMES = {QUALITY_HIGH: "High", QUALITY_DEFAULT: "Default", QUALITY_LOW: "Low"}

# CAM_IMAGE_MODE -> (width, height). CAM_VIDEO_MODE uses the same indices, and
# so does the supportResolution bitmask: bit N is mode N (ArducamCamera.c).
MODE_TABLE: Dict[int, Tuple[int, int]] = {
    0x00: (96, 96),
    0x01: (128, 128),
    0x02: (160, 120),
    0x03: (320, 240),
    0x04: (320, 320),
    0x05: (640, 480),
    0x06: (800, 600),
    0x07: (1024, 768),
    0x08: (1280, 720),
    0x09: (1280, 1024),
    0x0A: (1600, 1200),
    0x0B: (1920, 1080),
    0x0C: (2048, 1536),
    0x0D: (2592, 1944),
}
MODE_QXGA = 0x0C          # 2048x1536, what Astro 331 Lab 5 calls for

WHITE_BALANCE_NAMES = {0: "Auto", 1: "Sunny", 2: "Office", 3: "Cloudy", 4: "Home"}
COLOR_FX_NAMES = {
    0: "None", 1: "Blueish", 2: "Redish", 3: "Black & white", 4: "Sepia",
    5: "Negative", 6: "Green", 7: "Over exposure", 8: "Solarize",
}
# The signed levels share one encoding: 0 is default, odd is positive, even is
# negative (CAM_BRIGHTNESS_LEVEL and friends).
LEVEL_ENCODING = {0: 0, 1: 1, 2: 3, 3: 5, 4: 7, -1: 2, -2: 4, -3: 6, -4: 8}

# A 3MP module's largest frame is 2048x1536; 8 MiB leaves generous room for an
# uncompressed one while still rejecting a garbage length before it is used to
# size a buffer.
MAX_IMAGE_BYTES = 8 << 20
MAX_TEXT_BYTES = 4096
# How far past a declared length to look for the trailer before giving up.
TRAILER_SCAN = 64

# Bytes per second on the wire: 115200 8N1 is 10 bits/byte, and the firmware's
# arducamUartWrite adds delayUs(12) on top of every one of them.
BYTES_PER_SECOND_115200 = int(1.0 / (10.0 / 115200.0 + 12e-6))


class ArducamError(Exception):
    """A command could not be built, or a response could not be understood."""


# -- command framing ---------------------------------------------------------

def frame(code: int, *args: int) -> bytes:
    """Build one 0x55 <cmd> [args] 0xAA frame.

    An argument equal to 0xAA would terminate the frame early at the MCU, which
    reads arguments until it sees that byte, so it is refused here rather than
    producing a command the camera silently mis-parses.
    """
    for value in (code,) + args:
        if not 0 <= value <= 0xFF:
            raise ArducamError(f"byte out of range: {value}")
    for value in args:
        if value == CMD_END:
            raise ArducamError(
                "argument 0xAA would end the command frame early; "
                "the firmware reads arguments until it sees 0xAA")
    return bytes((CMD_START, code) + args + (CMD_END,))


def set_picture_resolution(pix_fmt: int, mode: int) -> bytes:
    """Set the still format and resolution.

    The firmware also fires a takePicture it never transmits, so the caller
    must leave the sensor time to settle before asking for a real one.
    """
    _check_mode(mode)
    if pix_fmt not in PIX_FMT_NAMES:
        raise ArducamError(f"unknown pixel format {pix_fmt:#04x}")
    return frame(CMD_SET_PICTURE_RESOLUTION, ((pix_fmt & 0x0F) << 4) | (mode & 0x0F))


def set_video_resolution(mode: int) -> bytes:
    _check_mode(mode)
    return frame(CMD_SET_VIDEO_RESOLUTION, mode & 0x0F)


def take_picture() -> bytes:
    return frame(CMD_TAKE_PICTURE)


def stop_stream() -> bytes:
    return frame(CMD_STOP_STREAM)


def get_camera_info() -> bytes:
    return frame(CMD_GET_CAMERA_INFO)


def get_firmware_version() -> bytes:
    return frame(CMD_GET_FRM_VER_INFO)


def get_sdk_version() -> bytes:
    return frame(CMD_GET_SDK_VER_INFO)


def reset_camera() -> bytes:
    return frame(CMD_RESET)


def set_image_quality(quality: int) -> bytes:
    if quality not in QUALITY_NAMES:
        raise ArducamError(f"unknown image quality {quality}")
    return frame(CMD_SET_IMAGE_QUALITY, quality)


def set_level(code: int, level: int) -> bytes:
    """Brightness, contrast, saturation, EV - they share one level encoding."""
    if level not in LEVEL_ENCODING:
        raise ArducamError(f"level {level} out of range (-4..4)")
    return frame(code, LEVEL_ENCODING[level])


def set_white_balance(mode: int) -> bytes:
    return frame(CMD_SET_WHITEBALANCE, mode & 0xFF)


def set_color_effect(effect: int) -> bytes:
    return frame(CMD_SET_SPECIAL_EFFECTS, effect & 0xFF)


def set_sharpness(level: int) -> bytes:
    return frame(CMD_SET_SHARPNESS, level & 0xFF)


def set_auto_focus(enabled: bool) -> bytes:
    return frame(CMD_SET_FOCUS_CONTROL, 1 if enabled else 0)


def set_auto_exposure(enabled: bool) -> bytes:
    return frame(CMD_SET_EXPOSUREANDGAIN_CONTROL, 1 if enabled else 0)


def set_auto_white_balance(enabled: bool) -> bytes:
    return frame(CMD_SET_WHITEBALANCE_CONTROL, 1 if enabled else 0)


def set_manual_gain(value: int) -> bytes:
    """Manual gain, big-endian across two argument bytes.

    Values whose encoding contains 0xAA are refused by frame(); there is no way
    to express them in this protocol, so failing loudly beats sending a command
    the camera would read as truncated.
    """
    if not 0 <= value <= 0xFFFF:
        raise ArducamError(f"gain {value} out of range (0..65535)")
    return frame(CMD_SET_MANUAL_GAIN, (value >> 8) & 0xFF, value & 0xFF)


def set_manual_exposure(value: int) -> bytes:
    """Manual exposure, big-endian across three argument bytes."""
    if not 0 <= value <= 0xFFFFFF:
        raise ArducamError(f"exposure {value} out of range (0..16777215)")
    return frame(CMD_SET_MANUAL_EXPOSURE,
                 (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def _check_mode(mode: int):
    if mode not in MODE_TABLE:
        raise ArducamError(f"unknown resolution mode {mode:#04x}")


# -- responses ---------------------------------------------------------------

@dataclass
class Packet:
    """One decoded MCU -> host packet."""

    type: int
    payload: bytes = b""
    declared_len: int = 0
    format_byte: Optional[int] = None
    trailer_ok: bool = True
    trailer_delta: int = 0

    @property
    def is_image(self) -> bool:
        return self.type == PKT_IMAGE

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8", errors="replace").strip("\r\n")

    def looks_like_jpeg(self) -> bool:
        return self.payload[:2] == b"\xff\xd8"


@dataclass
class StreamMarker:
    """A bare 0xFF 0xBB seen while idle - stop_preivew() closing a frame."""


# Parser states.
_IDLE = 0
_BODY = 1
_TRAILER = 2


class PacketParser:
    """Incremental demultiplexer for the camera's response stream.

    feed() returns the packets it completed plus the bytes that were *not* part
    of any camera packet, so the caller can pass that residue on to the normal
    text monitor. Ordering is preserved: a false-positive 0xFF is released into
    the residue exactly where it appeared.
    """

    def __init__(self, max_image_bytes: int = MAX_IMAGE_BYTES,
                 on_progress: Optional[Callable[[int, int], None]] = None):
        self.max_image_bytes = max_image_bytes
        self.on_progress = on_progress
        self._buf = bytearray()
        self._state = _IDLE
        self._pkt: Optional[Packet] = None
        self._body = bytearray()
        self._need = 0
        self._absorb_extra = False
        self.reset()

    # -- state ---------------------------------------------------------------

    def reset(self):
        """Forget everything. Used on disconnect, so a half-read packet from
        one session cannot corrupt the first capture of the next."""
        self._buf.clear()
        self._state = _IDLE
        self._pkt = None
        self._body = bytearray()
        self._need = 0
        self._absorb_extra = False

    @property
    def in_image(self) -> bool:
        return self._state in (_BODY, _TRAILER) and self._pkt is not None and self._pkt.is_image

    @property
    def progress(self) -> Tuple[int, int]:
        """(bytes received, bytes expected) for the packet in flight."""
        if self._pkt is None:
            return (0, 0)
        return (len(self._body), self._pkt.declared_len)

    def flush(self) -> bytes:
        """Release a byte held back as a possible packet start.

        A trailing 0xFF is ambiguous until the next byte arrives, so feed()
        keeps it rather than guessing. On an idle link that byte would never
        reach the monitor without this.
        """
        if self._state == _IDLE and self._buf:
            held = bytes(self._buf)
            self._buf.clear()
            return held
        return b""

    # -- the machine ---------------------------------------------------------

    def feed(self, data: bytes) -> Tuple[List[object], bytes]:
        packets: List[object] = []
        residual = bytearray()
        self._buf += data

        while True:
            if self._state == _IDLE:
                if not self._scan_idle(packets, residual):
                    break
            elif self._state == _BODY:
                if not self._read_body():
                    break
            else:
                if not self._read_trailer(packets, residual):
                    break

        return packets, bytes(residual)

    def _scan_idle(self, packets, residual) -> bool:
        """Look for a packet start. False means 'need more bytes'."""
        buf = self._buf
        start = buf.find(0xFF)
        if start < 0:
            residual += buf
            buf.clear()
            return False
        if start:
            residual += buf[:start]
            del buf[:start]
        if len(buf) < 2:
            return False              # hold the lone 0xFF; see flush()

        second = buf[1]
        if second == 0xBB:
            # stop_preivew() closes the interrupted frame before its packet.
            packets.append(StreamMarker())
            del buf[:2]
            return True
        if second != 0xAA:
            residual += buf[:1]
            del buf[:1]
            return True

        header = 7                    # FF AA type len:4
        if len(buf) < header:
            return False
        ptype = buf[2]
        declared = int.from_bytes(buf[3:7], "little")
        is_image = ptype == PKT_IMAGE
        if is_image:
            header += 1               # the extra format byte
            if len(buf) < header:
                return False

        if not self._plausible(ptype, declared):
            # Not a packet after all - let the 0xFF through and resync on the
            # next one rather than sizing a buffer from a garbage length.
            residual += buf[:1]
            del buf[:1]
            return True

        self._pkt = Packet(type=ptype, declared_len=declared,
                           format_byte=buf[7] if is_image else None)
        self._body = bytearray()
        self._need = declared
        self._absorb_extra = not is_image   # text length is only a hint
        del buf[:header]
        self._state = _BODY
        return True

    def _plausible(self, ptype: int, declared: int) -> bool:
        if ptype not in KNOWN_TYPES:
            return False
        if declared <= 0:
            return False
        limit = self.max_image_bytes if ptype == PKT_IMAGE else MAX_TEXT_BYTES
        return declared <= limit

    def _read_body(self) -> bool:
        take = min(self._need, len(self._buf))
        if take:
            self._body += self._buf[:take]
            del self._buf[:take]
            self._need -= take
            if self.on_progress is not None and self._pkt is not None and self._pkt.is_image:
                self.on_progress(len(self._body), self._pkt.declared_len)
        if self._need:
            return False
        self._state = _TRAILER
        return True

    def _read_trailer(self, packets, residual) -> bool:
        buf = self._buf
        if len(buf) < 2:
            return False
        if buf[0] == 0xFF and buf[1] == 0xBB:
            del buf[:2]
            return self._emit(packets, 0, True)

        # Arducam's type 0x05 declares one byte fewer than it writes, so the
        # trailer is the real terminator. Look a little way ahead for it.
        limit = min(len(buf), TRAILER_SCAN + 2)
        found = buf.find(EOF, 0, limit)
        if found >= 0:
            extra = bytes(buf[:found])
            del buf[:found + 2]
            if self._absorb_extra:
                self._body += extra
                return self._emit(packets, 0, True)
            return self._emit(packets, len(extra), True)

        if len(buf) < TRAILER_SCAN + 2:
            return False              # the trailer may still be coming

        # No trailer anywhere in reach. Deliver what we have flagged as
        # suspect - a student still gets their photo, and the log says why.
        return self._emit(packets, 0, False)

    def _emit(self, packets, delta: int, ok: bool) -> bool:
        pkt = self._pkt
        assert pkt is not None
        pkt.payload = bytes(self._body)
        pkt.trailer_delta = delta
        pkt.trailer_ok = ok
        packets.append(pkt)
        self._pkt = None
        self._body = bytearray()
        self._need = 0
        self._state = _IDLE
        return True


# -- encoding, for demo mode and the tests -----------------------------------

def pack_image_packet(mode: int, pix_fmt: int, payload: bytes,
                      declared_len: Optional[int] = None) -> bytes:
    """Build the bytes an MCU would send for a still.

    The format byte is written the way cameraGetPicture() writes it - mode in
    the high nibble - so demo mode exercises the same path real hardware does.
    """
    length = len(payload) if declared_len is None else declared_len
    return (SOF + bytes((PKT_IMAGE,)) + length.to_bytes(4, "little")
            + bytes((((mode & 0x0F) << 4) | 0x01,)) + payload + EOF)


def pack_preview_packet(pix_fmt: int, payload: bytes) -> bytes:
    """As above but with the preview callback's format byte, which packs the
    pixel format into the high nibble instead of the mode (main.cpp)."""
    return (SOF + bytes((PKT_IMAGE,)) + len(payload).to_bytes(4, "little")
            + bytes((((pix_fmt & 0x0F) << 4) | 0x01,)) + payload + EOF)


def pack_text_packet(ptype: int, text: str,
                     declared_len: Optional[int] = None) -> bytes:
    """Build a text packet. declared_len overrides the real length, which is
    how a test reproduces the type 0x05 off-by-one exactly."""
    body = text.encode("utf-8") + b"\r\n"
    length = len(body) if declared_len is None else declared_len
    return SOF + bytes((ptype,)) + length.to_bytes(4, "little") + body + EOF


def pack_stream_off() -> bytes:
    """What stop_preivew() emits: a bare trailer, then the streamoff packet."""
    return EOF + pack_text_packet(PKT_STREAM_OFF, "streamoff")


# -- interpreting what came back ---------------------------------------------

_INFO_PATTERNS = {
    "camera_id": (r"Camera Type:\s*(\S+)", str),
    "support_resolution": (r"Camera Support Resolution:\s*(-?\d+)", int),
    "support_special_effects": (r"Camera Support specialeffects:\s*(-?\d+)", int),
    "support_focus": (r"Camera Support Focus:\s*(-?\d+)", int),
    "exposure_max": (r"Camera Exposure Value Max:\s*(-?\d+)", int),
    "exposure_min": (r"Camera Exposure Value Min:\s*(-?\d+)", int),
    "gain_max": (r"Camera Gain Value Max:\s*(-?\d+)", int),
    "gain_min": (r"Camera Gain Value Min:\s*(-?\d+)", int),
    "support_sharpness": (r"Camera Support Sharpness:\s*(-?\d+)", int),
}


def parse_camera_info(text: str) -> Dict[str, object]:
    """Pull the fields out of a ReportCameraInfo block."""
    info: Dict[str, object] = {}
    for key, (pattern, cast) in _INFO_PATTERNS.items():
        match = re.search(pattern, text)
        if match:
            try:
                info[key] = cast(match.group(1))
            except ValueError:
                pass
    mask = info.get("support_resolution")
    if isinstance(mask, int):
        info["modes"] = modes_from_bitmask(mask)
    return info


def modes_from_bitmask(mask: int) -> List[int]:
    """Resolution modes a module supports.

    supportResolution sets bit N for mode N (RESOLUTION_96x96 is 1 << 0), so a
    3MP module's 0x1FFF means modes 0x00..0x0c - up to 2048x1536.
    """
    return [mode for mode in sorted(MODE_TABLE) if mask & (1 << mode)]


def size_for_mode(mode: int) -> Tuple[int, int]:
    return MODE_TABLE[mode]


def mode_label(mode: int) -> str:
    width, height = MODE_TABLE[mode]
    return f"{width}x{height}"


def mode_for_size(width: int, height: int) -> Optional[int]:
    for mode, size in MODE_TABLE.items():
        if size == (width, height):
            return mode
    return None


def estimate_seconds(byte_count: int, baud: int = 115200,
                     per_byte_delay_us: float = 12.0) -> float:
    """How long byte_count takes to arrive, including the firmware's delay."""
    return byte_count * (10.0 / baud + per_byte_delay_us * 1e-6)


def estimate_raw_bytes(mode: int, pix_fmt: int) -> int:
    """Payload size for an uncompressed format, for the 'this will take ten
    minutes' warning. JPEG is unpredictable, so it reports 0."""
    if pix_fmt == PIX_FMT_JPEG:
        return 0
    width, height = MODE_TABLE[mode]
    return width * height * 2


# -- raw pixel formats -------------------------------------------------------

def rgb565_to_rgb888(buf: bytes, width: int, height: int) -> bytes:
    """Expand a big-endian RGB565 frame to packed RGB888."""
    if not NUMPY_AVAILABLE:
        raise ArducamError("decoding RGB565 needs numpy")
    count = width * height
    if len(buf) < count * 2:
        raise ArducamError(
            f"RGB565 frame is {len(buf)} bytes, expected {count * 2}")
    pixels = np.frombuffer(buf[:count * 2], dtype=">u2").astype(np.uint32)
    red = ((pixels >> 11) & 0x1F) * 255 // 31
    green = ((pixels >> 5) & 0x3F) * 255 // 63
    blue = (pixels & 0x1F) * 255 // 31
    out = np.empty((count, 3), dtype=np.uint8)
    out[:, 0] = red
    out[:, 1] = green
    out[:, 2] = blue
    return out.reshape(height, width, 3).tobytes()


def yuv422_to_rgb888(buf: bytes, width: int, height: int) -> bytes:
    """Expand a YUYV 4:2:2 frame to packed RGB888."""
    if not NUMPY_AVAILABLE:
        raise ArducamError("decoding YUV needs numpy")
    count = width * height
    if len(buf) < count * 2:
        raise ArducamError(
            f"YUV frame is {len(buf)} bytes, expected {count * 2}")
    raw = np.frombuffer(buf[:count * 2], dtype=np.uint8).reshape(-1, 4)
    y0 = raw[:, 0].astype(np.float32)
    u = raw[:, 1].astype(np.float32) - 128.0
    y1 = raw[:, 2].astype(np.float32)
    v = raw[:, 3].astype(np.float32) - 128.0

    def to_rgb(luma):
        red = luma + 1.402 * v
        green = luma - 0.344136 * u - 0.714136 * v
        blue = luma + 1.772 * u
        return np.stack((red, green, blue), axis=1)

    pairs = np.empty((raw.shape[0], 2, 3), dtype=np.float32)
    pairs[:, 0, :] = to_rgb(y0)
    pairs[:, 1, :] = to_rgb(y1)
    return np.clip(pairs, 0, 255).astype(np.uint8).reshape(height, width, 3).tobytes()
