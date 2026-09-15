"""ArduCAM Mega protocol codec: framing, parsing, resync and chunk-invariance.

Pure bytes in, packets out - no Tk, no Qt, no display, no hardware. That is
the whole point of keeping arducam.py free of UI imports, and it means this
suite can run in CI next to test_themes.py.

    python3 tests/test_arducam.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kestrelsat import arducam

failures = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        failures.append(msg)


def parse_all(stream, chunk=None, parser=None):
    """Feed a stream (optionally in fixed-size chunks) and collect everything."""
    parser = parser or arducam.PacketParser()
    packets, residual = [], bytearray()
    if chunk is None:
        pkts, res = parser.feed(stream)
        packets += pkts
        residual += res
    else:
        for i in range(0, len(stream), chunk):
            pkts, res = parser.feed(stream[i:i + chunk])
            packets += pkts
            residual += res
    residual += parser.flush()
    return packets, bytes(residual)


print("\n-- command framing --")
check(arducam.frame(arducam.CMD_TAKE_PICTURE) == b"\x55\x10\xaa",
      "take picture is 55 10 AA")
check(arducam.set_picture_resolution(arducam.PIX_FMT_JPEG, arducam.MODE_QXGA)
      == b"\x55\x01\x1c\xaa",
      "JPEG 2048x1536 is 55 01 1C AA (the Lab 5 setting)")
check(arducam.set_video_resolution(0x03) == b"\x55\x02\x03\xaa",
      "video 320x240 is 55 02 03 AA")
check(arducam.stop_stream() == b"\x55\x21\xaa", "stop stream is 55 21 AA")
for name, builder in (("take_picture", arducam.take_picture),
                      ("get_camera_info", arducam.get_camera_info),
                      ("get_firmware_version", arducam.get_firmware_version),
                      ("get_sdk_version", arducam.get_sdk_version),
                      ("reset_camera", arducam.reset_camera)):
    out = builder()
    check(out[0] == 0x55 and out[-1] == 0xAA, f"{name} is bracketed by 55/AA")

# An argument equal to 0xAA would end the frame early at the MCU.
try:
    arducam.set_manual_gain(0xAA00)
    check(False, "manual gain 0xAA00 is refused")
except arducam.ArducamError:
    check(True, "manual gain whose high byte is 0xAA is refused, not truncated")
try:
    arducam.set_manual_exposure(0x00AA00)
    check(False, "manual exposure containing 0xAA is refused")
except arducam.ArducamError:
    check(True, "manual exposure containing 0xAA is refused, not truncated")
check(arducam.set_manual_gain(0x1234) == b"\x55\x0d\x12\x34\xaa",
      "manual gain is big-endian across two argument bytes")
try:
    arducam.set_picture_resolution(arducam.PIX_FMT_JPEG, 0x7F)
    check(False, "an unknown resolution mode is refused")
except arducam.ArducamError:
    check(True, "an unknown resolution mode is refused")

# Every resolution/format pair the UI can offer must be expressible.
bad = [(f, m) for f in arducam.PIX_FMT_NAMES for m in arducam.MODE_TABLE
       if 0xAA in arducam.set_picture_resolution(f, m)[2:3]]
check(not bad, "no format/mode pair encodes to the 0xAA terminator")


print("\n-- resolution tables --")
check(len(arducam.MODE_TABLE) == 14, "14 resolution modes")
check(arducam.MODE_TABLE[arducam.MODE_QXGA] == (2048, 1536), "mode 0x0c is 2048x1536")
check(arducam.modes_from_bitmask(0x1FFF) == list(range(0x00, 0x0D)),
      "a 3MP module's 0x1FFF is modes 0x00..0x0c")
check(arducam.mode_label(max(arducam.modes_from_bitmask(0x1FFF))) == "2048x1536",
      "3MP tops out at 2048x1536")
check(arducam.modes_from_bitmask(0x3FFF)[-1] == 0x0D, "a 5MP mask reaches 2592x1944")
check(arducam.mode_for_size(2048, 1536) == arducam.MODE_QXGA, "size maps back to mode")
check(all(arducam.mode_for_size(*arducam.MODE_TABLE[m]) is not None
          for m in arducam.MODE_TABLE), "every mode round-trips through its size")


print("\n-- camera info --")
INFO = ("ReportCameraInfo\r\nCamera Type:3MP\r\nCamera Support Resolution:8191\r\n"
        "Camera Support specialeffects:319\r\nCamera Support Focus:0\r\n"
        "Camera Exposure Value Max:10000\r\nCamera Exposure Value Min:1\r\n"
        "Camera Gain Value Max:240\r\nCamera Gain Value Min:0\r\n"
        "Camera Support Sharpness:0\r\n")
info = arducam.parse_camera_info(INFO)
check(info["camera_id"] == "3MP", "camera id parsed")
check(info["support_resolution"] == 8191, "resolution bitmask parsed")
check(info["exposure_max"] == 10000 and info["gain_max"] == 240, "limits parsed")
check(info["support_focus"] == 0, "a zero-valued field is kept, not dropped")
check(info["modes"] == list(range(0x00, 0x0D)), "modes derived from the bitmask")
check(arducam.parse_camera_info("nonsense") == {}, "garbage yields no fields")


print("\n-- packet round trips --")
JPEG = b"\xff\xd8" + bytes(range(256)) * 8 + b"\xff\xd9"
stream = arducam.pack_image_packet(arducam.MODE_QXGA, arducam.PIX_FMT_JPEG, JPEG)
packets, residual = parse_all(stream)
check(len(packets) == 1 and packets[0].payload == JPEG, "image packet round-trips")
check(residual == b"", "a clean image packet leaves no residue")
check(packets[0].is_image and packets[0].trailer_ok, "flagged as a good image")
check(packets[0].looks_like_jpeg(), "JPEG SOI detected")
check(packets[0].format_byte == ((arducam.MODE_QXGA << 4) | 1),
      "stills put the resolution mode in the format byte's high nibble")

preview = arducam.pack_preview_packet(arducam.PIX_FMT_JPEG, JPEG)
pkts, _ = parse_all(preview)
check(pkts[0].format_byte == ((arducam.PIX_FMT_JPEG << 4) | 1),
      "preview frames put the pixel format there instead - so it is never trusted")

# Payloads that contain the framing bytes themselves must survive untouched.
NASTY = b"\xff\xd8" + b"\xff\xaa\xff\xbb\x55\xaa" * 40 + b"\xff" * 32 + b"\xff\xd9"
pkts, res = parse_all(arducam.pack_image_packet(0x03, arducam.PIX_FMT_JPEG, NASTY))
check(len(pkts) == 1 and pkts[0].payload == NASTY,
      "a payload full of FF AA / FF BB / 55 survives strict length framing")
check(res == b"", "and leaks nothing into the residue")

text = arducam.pack_text_packet(arducam.PKT_TEXT, "[INFO] Mega Initialized!")
pkts, res = parse_all(text)
check(len(pkts) == 1 and pkts[0].text == "[INFO] Mega Initialized!",
      "text packet round-trips")


print("\n-- firmware quirks --")
# Type 0x05 declares len=6 but writes 7 bytes (ArducamLink.cpp reportSdkVerInfo).
short = arducam.pack_text_packet(arducam.PKT_SDK_VERSION, "v3.0.0", declared_len=6)
pkts, res = parse_all(short)
check(len(pkts) == 1 and pkts[0].type == arducam.PKT_SDK_VERSION,
      "the type 0x05 off-by-one still yields exactly one packet")
check(pkts[0].payload == b"v3.0.0\r\n",
      "and the bytes past the declared length are recovered")
check(res == b"", "with nothing spilled into the residue")

# stop_preivew() writes a bare trailer before its packet.
pkts, res = parse_all(arducam.pack_stream_off())
check(any(isinstance(p, arducam.StreamMarker) for p in pkts),
      "a bare FF BB while idle is recognised, not passed through")
check(any(getattr(p, "type", None) == arducam.PKT_STREAM_OFF for p in pkts),
      "the streamoff packet that follows it is parsed")
check(res == b"", "neither leaks into the residue")

# A whole preview frame followed by a stop.
seq = (arducam.pack_preview_packet(arducam.PIX_FMT_JPEG, JPEG)
       + arducam.pack_stream_off())
pkts, res = parse_all(seq)
check(sum(1 for p in pkts if getattr(p, "is_image", False)) == 1,
      "preview frame then stop: the frame is delivered")
check(res == b"", "preview stop sequence leaves no residue")


print("\n-- interleaved telemetry (the demux contract) --")
TELEM = b"TIME:1.0,SENSOR_A:12.5\r\n"
mixed = (TELEM + arducam.pack_image_packet(0x03, arducam.PIX_FMT_JPEG, JPEG)
         + TELEM + arducam.pack_text_packet(arducam.PKT_TEXT, "hello") + TELEM)
pkts, res = parse_all(mixed)
check(res == TELEM * 3, "telemetry around and between packets comes back byte-identical")
check(sum(1 for p in pkts if getattr(p, "is_image", False)) == 1,
      "and the image is still extracted")
check(b"\xff" not in res, "no image bytes leak into the text path")

# A JPEG can contain ZMODEM's receive offer; if that reached the monitor the
# app would seize the port mid-capture (app.py:1518).
OFFER = b"**\x18B00"
pkts, res = parse_all(arducam.pack_image_packet(0x03, arducam.PIX_FMT_JPEG,
                                                b"\xff\xd8" + OFFER + b"\xff\xd9"))
check(OFFER not in res, "a ZMODEM offer inside a JPEG never reaches the monitor")


print("\n-- chunk invariance (fuzz) --")
BIG = (TELEM
       + arducam.pack_image_packet(arducam.MODE_QXGA, arducam.PIX_FMT_JPEG, NASTY)
       + TELEM
       + arducam.pack_text_packet(arducam.PKT_SDK_VERSION, "v3.0.0", declared_len=6)
       + arducam.pack_stream_off()
       + TELEM
       + arducam.pack_preview_packet(arducam.PIX_FMT_JPEG, JPEG))
want_pkts, want_res = parse_all(BIG)
check(len(want_pkts) >= 4 and want_res == TELEM * 3,
      "baseline: the whole mixed stream parses in one feed")

ok = True
for size in (1, 2, 3, 5, 7, 13, 64, 255, 1000, 100000):
    pkts, res = parse_all(BIG, chunk=size)
    same = (res == want_res
            and len(pkts) == len(want_pkts)
            and all(getattr(a, "payload", None) == getattr(b, "payload", None)
                    and type(a) is type(b) for a, b in zip(pkts, want_pkts)))
    if not same:
        ok = False
        print(f"       mismatch at chunk size {size}")
check(ok, "fixed chunk sizes 1..100000 all give byte-identical results")

rng = random.Random(20260915)
ok = True
for trial in range(60):
    parser = arducam.PacketParser()
    packets, residual = [], bytearray()
    pos = 0
    while pos < len(BIG):
        step = rng.randint(1, 300)
        pkts, res = parser.feed(BIG[pos:pos + step])
        packets += pkts
        residual += res
        pos += step
    residual += parser.flush()
    if bytes(residual) != want_res or len(packets) != len(want_pkts):
        ok = False
        print(f"       mismatch on random trial {trial}")
        break
check(ok, "60 seeded-random chunk splits all give byte-identical results")


print("\n-- resync and defence --")
parser = arducam.PacketParser()
pkts, res = parser.feed(b"\xff")
check(pkts == [] and res == b"",
      "a trailing lone FF is held back rather than guessed at")
check(parser.flush() == b"\xff", "flush() releases it")

pkts, res = parse_all(b"\xff\x41\xff\x42")
check(res == b"\xff\x41\xff\x42", "FF followed by non-AA/BB passes through intact")

# A garbage length must not be used to size a buffer.
huge = arducam.SOF + bytes((arducam.PKT_IMAGE,)) + (0xFFFFFFFF).to_bytes(4, "little") + b"\x11"
pkts, res = parse_all(huge + TELEM)
check(pkts == [], "an absurd declared length yields no packet")
check(res.endswith(TELEM), "and the parser resyncs onto the telemetry after it")

zero = arducam.SOF + bytes((arducam.PKT_IMAGE,)) + (0).to_bytes(4, "little") + b"\x11"
pkts, _ = parse_all(zero + TELEM)
check(pkts == [], "a zero length is rejected too")

unknown = arducam.SOF + bytes((0x7E,)) + (4).to_bytes(4, "little") + b"abcd" + arducam.EOF
pkts, res = parse_all(unknown)
# The trailing FF BB is legitimately read as a bare trailer once the header is
# rejected, so the contract is "no Packet is invented", not "nothing happens".
check(not any(isinstance(p, arducam.Packet) for p in pkts),
      "an unknown packet type is not invented")
check(b"abcd" in res, "its bytes fall through to the text path instead")

# Truncation mid-image must leave the parser usable for the next capture.
full = arducam.pack_image_packet(0x03, arducam.PIX_FMT_JPEG, JPEG)
parser = arducam.PacketParser()
pkts, _ = parser.feed(full[:len(full) // 2])
check(pkts == [], "a truncated image yields nothing yet")
check(parser.in_image, "and the parser knows it is mid-image")
got, total = parser.progress
check(total == len(JPEG) and 0 < got < total, f"progress reports {got}/{total}")
parser.reset()
pkts, res = parser.feed(full)
check(len(pkts) == 1 and pkts[0].payload == JPEG,
      "after reset() the next capture parses cleanly")

# A trailer that never arrives still delivers the image, flagged.
missing = (arducam.SOF + bytes((arducam.PKT_IMAGE,))
           + len(JPEG).to_bytes(4, "little") + b"\x31" + JPEG + b"Z" * 200)
pkts, res = parse_all(missing)
check(len(pkts) == 1 and pkts[0].payload == JPEG,
      "a missing trailer still delivers the image")
check(not pkts[0].trailer_ok, "but flags it as suspect rather than claiming success")

# A trailer a few bytes late is tolerated and the delta reported.
late = (arducam.SOF + bytes((arducam.PKT_IMAGE,))
        + len(JPEG).to_bytes(4, "little") + b"\x31" + JPEG + b"XY" + arducam.EOF)
pkts, _ = parse_all(late)
check(len(pkts) == 1 and pkts[0].trailer_delta == 2,
      "a trailer 2 bytes late is tolerated and the delta recorded")


print("\n-- transfer estimates --")
secs = arducam.estimate_seconds(500_000)
check(45 < secs < 55, f"500 KB at 115200 with the firmware delay is ~49 s (got {secs:.1f})")
check(9500 < arducam.BYTES_PER_SECOND_115200 < 10500,
      f"~10 KB/s on the wire (got {arducam.BYTES_PER_SECOND_115200})")
check(arducam.estimate_raw_bytes(arducam.MODE_QXGA, arducam.PIX_FMT_RGB565) == 2048 * 1536 * 2,
      "RGB565 at 2048x1536 is 6.3 MB - the warning the UI needs")
check(arducam.estimate_raw_bytes(arducam.MODE_QXGA, arducam.PIX_FMT_JPEG) == 0,
      "JPEG size is unpredictable, so no estimate is claimed")


print("\n-- raw pixel formats --")
if arducam.NUMPY_AVAILABLE:
    rgb = arducam.rgb565_to_rgb888(b"\xf8\x00\x07\xe0\x00\x1f\xff\xff", 4, 1)
    check(rgb[0:3] == b"\xff\x00\x00", "RGB565 pure red expands to FF0000")
    check(rgb[3:6] == b"\x00\xff\x00", "RGB565 pure green expands to 00FF00")
    check(rgb[6:9] == b"\x00\x00\xff", "RGB565 pure blue expands to 0000FF")
    check(rgb[9:12] == b"\xff\xff\xff", "RGB565 white expands to FFFFFF")
    yuv = arducam.yuv422_to_rgb888(b"\x80\x80\x80\x80" * 2, 4, 1)
    check(len(yuv) == 12 and all(0x7E <= b <= 0x82 for b in yuv),
          "YUV mid-grey decodes to mid-grey")
    try:
        arducam.rgb565_to_rgb888(b"\x00" * 4, 320, 240)
        check(False, "a short RGB565 frame is rejected")
    except arducam.ArducamError:
        check(True, "a short RGB565 frame is rejected, not silently padded")
else:
    print("  skip numpy not available - RGB565/YUV decode untested")


print()
if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("test_arducam: all checks passed.")
