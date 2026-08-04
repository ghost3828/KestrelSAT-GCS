"""ZMODEM file transfer over a serial link.

A self-contained implementation of the parts of ZMODEM needed to exchange
files with the standard tools (lrzsz's ``sz``/``rz``, and the ZMODEM support
built into TeraTerm, minicom, SecureCRT and similar): hex and binary headers,
CRC-16 and CRC-32, ZDLE escaping, streaming data subpackets, and ZRPOS-based
error recovery.

The protocol layer here knows nothing about tkinter. It talks to any object
exposing ``read(n)``, ``write(b)`` and ``flush()`` - a ``serial.Serial``, or a
pty in the tests - and reports progress through a callback, so it can be
driven from a worker thread and tested without hardware.

Reference: Chuck Forsberg, "The ZMODEM Inter Application File Transfer
Protocol" (1988).


Copyright (C) 2026 Wyatt Harris

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
"""

import os
import time
import zlib

# -- framing bytes ----------------------------------------------------------
ZPAD = 0x2A          # '*'
ZDLE = 0x18          # ctrl-X, the escape character
ZDLEE = 0x58         # ZDLE escaped ('X' == ZDLE ^ 0x40)

ZBIN = 0x41          # 'A' binary header, CRC-16
ZHEX = 0x42          # 'B' hex header, CRC-16
ZBIN32 = 0x43        # 'C' binary header, CRC-32

# -- frame types ------------------------------------------------------------
ZRQINIT = 0
ZRINIT = 1
ZSINIT = 2
ZACK = 3
ZFILE = 4
ZSKIP = 5
ZNAK = 6
ZABORT = 7
ZFIN = 8
ZRPOS = 9
ZDATA = 10
ZEOF = 11
ZFERR = 12
ZCRC = 13
ZCHALLENGE = 14
ZCOMPL = 15
ZCAN = 16
ZFREECNT = 17
ZCOMMAND = 18
ZSTDERR = 19

FRAME_NAMES = {
    ZRQINIT: "ZRQINIT", ZRINIT: "ZRINIT", ZSINIT: "ZSINIT", ZACK: "ZACK",
    ZFILE: "ZFILE", ZSKIP: "ZSKIP", ZNAK: "ZNAK", ZABORT: "ZABORT",
    ZFIN: "ZFIN", ZRPOS: "ZRPOS", ZDATA: "ZDATA", ZEOF: "ZEOF",
    ZFERR: "ZFERR", ZCRC: "ZCRC", ZCHALLENGE: "ZCHALLENGE", ZCOMPL: "ZCOMPL",
    ZCAN: "ZCAN", ZFREECNT: "ZFREECNT", ZCOMMAND: "ZCOMMAND", ZSTDERR: "ZSTDERR",
}

# -- subpacket terminators (follow a ZDLE inside a data subpacket) ----------
ZCRCE = 0x68         # 'h' CRC follows, frame ends
ZCRCG = 0x69         # 'i' CRC follows, frame continues without acknowledgement
ZCRCQ = 0x6A         # 'j' CRC follows, frame continues, ZACK expected
ZCRCW = 0x6B         # 'k' CRC follows, frame ends, ZACK expected

# -- ZDLE escape substitutions ---------------------------------------------
ZRUB0 = 0x6C         # 'l' -> 0x7F
ZRUB1 = 0x6D         # 'm' -> 0xFF

# -- ZRINIT capability flags (ZF0) -----------------------------------------
CANFDX = 0x01        # full duplex
CANOVIO = 0x02       # can overlap I/O
CANBRK = 0x04
CANFC32 = 0x20       # can accept CRC-32 frames
ESCCTL = 0x40        # escape all control characters
ESC8 = 0x80

# Bytes that must never appear raw in a binary frame: XON/XOFF and DLE would
# be eaten by flow control, and ZDLE is the escape itself.
_MUST_ESCAPE = frozenset((ZDLE, 0x10, 0x11, 0x13, 0x90, 0x91, 0x93))

# The byte sequence a sender emits to announce a transfer. Watching the
# incoming stream for this is how a terminal auto-starts a download.
#   ZPAD ZPAD ZDLE ZHEX '0' '0'   ("**\x18B00...")
RECEIVE_OFFER = b"**\x18B00"
CANCEL_SEQUENCE = bytes([ZDLE] * 8) + b"\b" * 10

SUBPACKET_SIZE = 1024
DEFAULT_TIMEOUT = 10.0
MAX_ERRORS = 20


class ZModemError(Exception):
    """Transfer failed."""


class ZModemCancelled(ZModemError):
    """Transfer was cancelled, locally or by the peer."""


def crc16(data, crc=0):
    """CRC-16/XMODEM, as ZMODEM uses for headers and 16-bit subpackets."""
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc32(data, crc=0):
    """CRC-32 as ZMODEM uses it - identical to zlib's."""
    return zlib.crc32(data, crc) & 0xFFFFFFFF


def escape(data):
    """ZDLE-escape a payload for transmission inside a binary frame."""
    out = bytearray()
    previous = 0
    for byte in data:
        if byte in _MUST_ESCAPE:
            out.append(ZDLE)
            out.append(byte ^ 0x40)
        elif byte == 0x0D and (previous & 0x7F) == 0x40:
            # "@\r" is escaped: some modems treat it as an escape to command
            # mode. This mirrors lrzsz's zsendline().
            out.append(ZDLE)
            out.append(byte ^ 0x40)
        else:
            out.append(byte)
        previous = byte
    return bytes(out)


class _Link:
    """Header and subpacket framing over a byte stream."""

    def __init__(self, port, timeout=DEFAULT_TIMEOUT, log=None):
        self.port = port
        self.timeout = timeout
        self.log = log or (lambda msg: None)
        self.can_count = 0
        self._pushback = bytearray()

    # -- raw I/O -----------------------------------------------------------
    def write(self, data):
        self.port.write(data)
        try:
            self.port.flush()
        except Exception:
            pass

    def read_byte(self, timeout=None):
        """One raw byte, or None on timeout."""
        if self._pushback:
            return self._pushback.pop(0)
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            chunk = self.port.read(1)
            if chunk:
                return chunk[0]
            if time.monotonic() >= deadline:
                return None

    def push_back(self, data):
        self._pushback.extend(data)

    def send_cancel(self):
        try:
            self.write(CANCEL_SEQUENCE)
        except Exception:
            pass

    # -- headers -----------------------------------------------------------
    def send_header(self, fmt, htype, flags=(0, 0, 0, 0)):
        payload = bytes([htype]) + bytes(flags)
        if fmt == ZHEX:
            crc = crc16(payload)
            body = payload + bytes([(crc >> 8) & 0xFF, crc & 0xFF])
            frame = bytes([ZPAD, ZPAD, ZDLE, ZHEX]) + body.hex().encode("ascii")
            frame += b"\r\n"
            if htype not in (ZFIN, ZACK):
                frame += b"\x11"  # XON, so the peer's flow control stays sane
        elif fmt == ZBIN32:
            crc = crc32(payload)
            body = payload + bytes([(crc >> (8 * i)) & 0xFF for i in range(4)])
            frame = bytes([ZPAD, ZDLE, ZBIN32]) + escape(body)
        else:
            crc = crc16(payload)
            body = payload + bytes([(crc >> 8) & 0xFF, crc & 0xFF])
            frame = bytes([ZPAD, ZDLE, ZBIN]) + escape(body)
        self.log(f"-> {FRAME_NAMES.get(htype, htype)} {list(flags)}")
        self.write(frame)

    def _read_escaped(self, timeout=None):
        """Next logical byte. Returns (kind, value).

        kind is 'data' for a payload byte, 'end' for a subpacket terminator
        (value is one of ZCRCE/ZCRCG/ZCRCQ/ZCRCW), or 'timeout'.
        """
        while True:
            byte = self.read_byte(timeout)
            if byte is None:
                return ("timeout", None)
            if byte != ZDLE:
                if byte == 0x18:  # cannot happen, ZDLE is 0x18 - defensive
                    continue
                self.can_count = 0
                return ("data", byte)

            nxt = self.read_byte(timeout)
            if nxt is None:
                return ("timeout", None)
            if nxt in (ZCRCE, ZCRCG, ZCRCQ, ZCRCW):
                return ("end", nxt)
            if nxt == ZRUB0:
                return ("data", 0x7F)
            if nxt == ZRUB1:
                return ("data", 0xFF)
            if nxt == ZDLE:
                # ZDLE ZDLE means the peer is cancelling
                self.can_count += 1
                if self.can_count >= 3:
                    raise ZModemCancelled("peer cancelled the transfer")
                continue
            if nxt in (0x0D, 0x0A, 0x11, 0x13):
                continue  # line noise between frames
            return ("data", nxt ^ 0x40)

    def recv_header(self, timeout=None):
        """Next header as (type, flags-list), or None on timeout."""
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())

            # Hunt for the ZPAD ... ZDLE preamble.
            byte = self.read_byte(remaining)
            if byte is None:
                return None
            if byte == 0x18:  # a bare CAN run also means cancel
                cancels = 1
                while cancels < 5:
                    nxt = self.read_byte(0.2)
                    if nxt != 0x18:
                        self.push_back(bytes([nxt]) if nxt is not None else b"")
                        break
                    cancels += 1
                if cancels >= 5:
                    raise ZModemCancelled("peer cancelled the transfer")
                continue
            if byte != ZPAD:
                continue
            byte = self.read_byte(remaining)
            if byte is None:
                return None
            if byte == ZPAD:
                byte = self.read_byte(remaining)
                if byte is None:
                    return None
            if byte != ZDLE:
                continue

            fmt = self.read_byte(remaining)
            if fmt is None:
                return None
            if fmt == ZHEX:
                header = self._recv_hex_header(remaining)
            elif fmt in (ZBIN, ZBIN32):
                header = self._recv_bin_header(fmt, remaining)
            else:
                continue
            if header is not None:
                self.log(f"<- {FRAME_NAMES.get(header[0], header[0])} {header[1]}")
                return header
        return None

    def _recv_hex_header(self, timeout):
        digits = bytearray()
        while len(digits) < 14:
            byte = self.read_byte(timeout)
            if byte is None:
                return None
            if byte in (0x0D, 0x0A):
                break
            digits.append(byte)
        if len(digits) < 14:
            return None
        try:
            raw = bytes.fromhex(digits[:14].decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return None
        payload, received = raw[:5], (raw[5] << 8) | raw[6]
        if crc16(payload) != received:
            return None
        # Trailing CR/LF/XON is consumed opportunistically.
        for _ in range(2):
            nxt = self.read_byte(0.05)
            if nxt is None or nxt not in (0x0D, 0x0A, 0x11):
                if nxt is not None:
                    self.push_back(bytes([nxt]))
                break
        return payload[0], list(payload[1:5])

    def _recv_bin_header(self, fmt, timeout):
        want = 5 + (4 if fmt == ZBIN32 else 2)
        raw = bytearray()
        while len(raw) < want:
            kind, value = self._read_escaped(timeout)
            if kind != "data":
                return None
            raw.append(value)
        payload = bytes(raw[:5])
        if fmt == ZBIN32:
            received = int.from_bytes(raw[5:9], "little")
            if crc32(payload) != received:
                return None
        else:
            received = (raw[5] << 8) | raw[6]
            if crc16(payload) != received:
                return None
        return payload[0], list(payload[1:5])

    # -- data subpackets ---------------------------------------------------
    def send_data(self, data, frameend, use_crc32=True):
        body = escape(data)
        body += bytes([ZDLE, frameend])
        check = data + bytes([frameend])
        if use_crc32:
            crc = crc32(check)
            body += escape(bytes([(crc >> (8 * i)) & 0xFF for i in range(4)]))
        else:
            crc = crc16(check)
            body += escape(bytes([(crc >> 8) & 0xFF, crc & 0xFF]))
        self.write(body)

    def recv_data(self, timeout=None, use_crc32=True):
        """Next data subpacket as (bytes, frameend), or (None, None) on error."""
        payload = bytearray()
        while True:
            kind, value = self._read_escaped(timeout)
            if kind == "timeout":
                return None, None
            if kind == "data":
                payload.append(value)
                if len(payload) > SUBPACKET_SIZE * 4:
                    return None, None  # runaway subpacket, treat as corrupt
                continue

            frameend = value
            want = 4 if use_crc32 else 2
            check = bytearray()
            while len(check) < want:
                kind2, value2 = self._read_escaped(timeout)
                if kind2 != "data":
                    return None, None
                check.append(value2)

            verified = bytes(payload) + bytes([frameend])
            if use_crc32:
                if crc32(verified) != int.from_bytes(check, "little"):
                    return None, None
            else:
                if crc16(verified) != ((check[0] << 8) | check[1]):
                    return None, None
            return bytes(payload), frameend


def _pos_bytes(position):
    return [(position >> (8 * i)) & 0xFF for i in range(4)]


def _pos_from(flags):
    return flags[0] | (flags[1] << 8) | (flags[2] << 16) | (flags[3] << 24)


class ZModemSender:
    """Send one or more files. Mirrors ``sz``."""

    def __init__(self, port, progress=None, cancel=None, timeout=DEFAULT_TIMEOUT, log=None):
        self.link = _Link(port, timeout, log)
        self.progress = progress or (lambda **kw: None)
        self.cancel = cancel
        self.use_crc32 = True
        self.log = log or (lambda msg: None)

    def _cancelled(self):
        return self.cancel is not None and self.cancel.is_set()

    def send(self, paths):
        """Send each path. Returns the list actually transferred."""
        sent = []
        try:
            self.link.send_header(ZHEX, ZRQINIT)
            if not self._await_receiver():
                raise ZModemError("receiver did not respond to ZRQINIT")

            for path in paths:
                if self._cancelled():
                    raise ZModemCancelled("cancelled")
                if self._send_one(path):
                    sent.append(path)

            # Sign off. A peer that is not ready yet answers ZRINIT rather
            # than ZFIN, so keep asking rather than walking away mid-handshake
            # and leaving it waiting forever.
            for _ in range(MAX_ERRORS):
                self.link.send_header(ZHEX, ZFIN)
                header = self.link.recv_header(timeout=5)
                if header is None:
                    continue
                if header[0] == ZFIN:
                    self.link.write(b"OO")
                    break
        except ZModemCancelled:
            self.link.send_cancel()
            raise
        except ZModemError:
            self.link.send_cancel()
            raise
        return sent

    def _await_receiver(self):
        for _ in range(MAX_ERRORS):
            header = self.link.recv_header(timeout=10)
            if header is None:
                self.link.send_header(ZHEX, ZRQINIT)
                continue
            htype, flags = header
            if htype == ZRINIT:
                self.use_crc32 = bool(flags[3] & CANFC32)
                return True
            if htype == ZCHALLENGE:
                self.link.send_header(ZHEX, ZACK, flags)
            elif htype in (ZABORT, ZFIN, ZCAN):
                raise ZModemCancelled("receiver refused the transfer")
        return False

    def _send_one(self, path):
        size = os.path.getsize(path)
        name = os.path.basename(path)
        mtime = int(os.path.getmtime(path))
        # "name\0size mtime mode files-remaining bytes-remaining\0"
        info = name.encode("utf-8", "replace") + b"\0"
        info += f"{size} {mtime:o} 0 0 1 {size}".encode("ascii") + b"\0"

        for _ in range(MAX_ERRORS):
            self.link.send_header(ZBIN32 if self.use_crc32 else ZBIN, ZFILE)
            self.link.send_data(info, ZCRCW, self.use_crc32)

            # A receiver typically restates its capabilities with ZRINIT and
            # only then sends ZRPOS. Resending ZFILE on the ZRINIT desynchronises
            # it, so keep reading until something conclusive arrives.
            for _ in range(MAX_ERRORS):
                header = self.link.recv_header(timeout=10)
                if header is None:
                    break  # nothing at all - resend ZFILE
                htype, flags = header
                if htype == ZRPOS:
                    return self._send_body(path, size, _pos_from(flags))
                if htype == ZSKIP:
                    self.progress(name=name, sent=0, total=size, status="skipped")
                    return False
                if htype in (ZABORT, ZFIN, ZCAN):
                    raise ZModemCancelled("receiver aborted")
                # ZRINIT or anything else: keep waiting
        raise ZModemError(f"receiver never accepted {name}")

    def _send_body(self, path, size, position):
        name = os.path.basename(path)
        errors = 0
        with open(path, "rb") as handle:
            while True:
                if self._cancelled():
                    raise ZModemCancelled("cancelled")

                handle.seek(position)
                self.link.send_header(ZBIN32 if self.use_crc32 else ZBIN,
                                      ZDATA, _pos_bytes(position))

                streaming = True
                while streaming:
                    if self._cancelled():
                        raise ZModemCancelled("cancelled")
                    chunk = handle.read(SUBPACKET_SIZE)
                    if not chunk:
                        break
                    position += len(chunk)
                    at_eof = position >= size
                    # ZCRCW at the end forces an acknowledgement, so we learn
                    # about errors before declaring EOF.
                    frameend = ZCRCW if at_eof else ZCRCG
                    self.link.send_data(chunk, frameend, self.use_crc32)
                    self.progress(name=name, sent=position, total=size, status="sending")

                    if frameend == ZCRCW:
                        header = self.link.recv_header(timeout=10)
                        if header is None:
                            errors += 1
                            streaming = False
                            break
                        htype, flags = header
                        if htype == ZRPOS:
                            position = _pos_from(flags)
                            streaming = False
                            errors += 1
                            break
                        if htype in (ZABORT, ZCAN):
                            raise ZModemCancelled("receiver aborted")

                if errors > MAX_ERRORS:
                    raise ZModemError(f"too many errors sending {name}")
                if position < size:
                    continue

                # End of file: announce it and wait for the receiver to be
                # ready for the next one.
                for _ in range(MAX_ERRORS):
                    self.link.send_header(ZHEX, ZEOF, _pos_bytes(size))
                    header = self.link.recv_header(timeout=10)
                    if header is None:
                        continue
                    htype, flags = header
                    if htype == ZRINIT:
                        self.progress(name=name, sent=size, total=size, status="done")
                        return True
                    if htype == ZRPOS:
                        resume = _pos_from(flags)
                        if resume >= size:
                            continue  # already has everything; re-announce EOF
                        position = resume
                        break
                    if htype in (ZABORT, ZCAN):
                        raise ZModemCancelled("receiver aborted")
                else:
                    raise ZModemError(f"no acknowledgement of EOF for {name}")


class ZModemReceiver:
    """Receive files into a directory. Mirrors ``rz``."""

    def __init__(self, port, dest_dir, progress=None, cancel=None,
                 timeout=DEFAULT_TIMEOUT, log=None, overwrite=False):
        self.link = _Link(port, timeout, log)
        self.dest_dir = dest_dir
        self.progress = progress or (lambda **kw: None)
        self.cancel = cancel
        self.overwrite = overwrite
        self.use_crc32 = True
        self.log = log or (lambda msg: None)

    def _cancelled(self):
        return self.cancel is not None and self.cancel.is_set()

    def _zrinit(self):
        # CANFDX|CANOVIO|CANFC32: full duplex, overlapped I/O, CRC-32 welcome.
        self.link.send_header(ZHEX, ZRINIT, (0, 0, 0, CANFDX | CANOVIO | CANFC32))

    def receive(self):
        """Receive until the sender finishes. Returns the paths written."""
        written = []
        try:
            self._zrinit()
            errors = 0
            while errors < MAX_ERRORS:
                if self._cancelled():
                    raise ZModemCancelled("cancelled")

                header = self.link.recv_header(timeout=10)
                if header is None:
                    errors += 1
                    self._zrinit()
                    continue
                htype, flags = header

                if htype == ZRQINIT:
                    self._zrinit()
                elif htype == ZFILE:
                    path = self._receive_file()
                    if path:
                        written.append(path)
                    self._zrinit()
                elif htype == ZFIN:
                    self.link.send_header(ZHEX, ZFIN)
                    # The sender signs off with "OO"; consume it so the bytes
                    # do not land in the serial monitor.
                    self.link.read_byte(1.0)
                    self.link.read_byte(0.5)
                    return written
                elif htype == ZSINIT:
                    self.link.recv_data(timeout=5, use_crc32=self.use_crc32)
                    self.link.send_header(ZHEX, ZACK)
                elif htype in (ZABORT, ZCAN):
                    raise ZModemCancelled("sender aborted")
                elif htype == ZDATA:
                    # Stray data with no open file: ask for a restart.
                    self.link.send_header(ZHEX, ZRPOS, _pos_bytes(0))
                    errors += 1
            raise ZModemError("no response from sender")
        except ZModemCancelled:
            self.link.send_cancel()
            raise
        except ZModemError:
            self.link.send_cancel()
            raise

    def _receive_file(self):
        payload, _frameend = self.link.recv_data(timeout=10, use_crc32=self.use_crc32)
        if payload is None:
            self.link.send_header(ZHEX, ZNAK)
            return None

        name_part, _, rest = payload.partition(b"\0")
        name = os.path.basename(name_part.decode("utf-8", "replace")).strip()
        if not name or name in (".", ".."):
            name = "received.dat"
        total = 0
        fields = rest.split(b"\0")[0].split()
        if fields:
            try:
                total = int(fields[0])
            except ValueError:
                total = 0

        path = os.path.join(self.dest_dir, name)
        if not self.overwrite:
            path = _unique_path(path)

        self.progress(name=name, received=0, total=total, status="starting")

        position = 0
        errors = 0
        with open(path, "wb") as handle:
            self.link.send_header(ZHEX, ZRPOS, _pos_bytes(position))
            while True:
                if self._cancelled():
                    raise ZModemCancelled("cancelled")

                header = self.link.recv_header(timeout=10)
                if header is None:
                    errors += 1
                    if errors > MAX_ERRORS:
                        raise ZModemError(f"timed out receiving {name}")
                    self.link.send_header(ZHEX, ZRPOS, _pos_bytes(position))
                    continue
                htype, flags = header

                if htype == ZDATA:
                    if _pos_from(flags) != position:
                        # Sender is somewhere else in the file; resynchronise.
                        self.link.send_header(ZHEX, ZRPOS, _pos_bytes(position))
                        errors += 1
                        continue
                    ok, position, errors = self._receive_stream(
                        handle, position, total, name, errors)
                    if not ok and errors > MAX_ERRORS:
                        raise ZModemError(f"too many errors receiving {name}")
                elif htype == ZEOF:
                    if _pos_from(flags) != position:
                        errors += 1
                        self.link.send_header(ZHEX, ZRPOS, _pos_bytes(position))
                        continue
                    handle.flush()
                    self.progress(name=name, received=position, total=total, status="done")
                    return path
                elif htype == ZFILE:
                    # Sender restarted the offer; take it from the top.
                    self.link.send_header(ZHEX, ZRPOS, _pos_bytes(position))
                elif htype in (ZFIN, ZABORT, ZCAN):
                    raise ZModemCancelled("sender aborted mid-file")

    def _receive_stream(self, handle, position, total, name, errors):
        """Consume data subpackets until the frame ends. Returns (ok, pos, errors)."""
        while True:
            if self._cancelled():
                raise ZModemCancelled("cancelled")

            payload, frameend = self.link.recv_data(timeout=10, use_crc32=self.use_crc32)
            if payload is None:
                errors += 1
                self.link.send_header(ZHEX, ZRPOS, _pos_bytes(position))
                return False, position, errors

            handle.write(payload)
            position += len(payload)
            self.progress(name=name, received=position, total=total, status="receiving")

            if frameend == ZCRCW:
                self.link.send_header(ZHEX, ZACK, _pos_bytes(position))
                return True, position, errors
            if frameend == ZCRCQ:
                self.link.send_header(ZHEX, ZACK, _pos_bytes(position))
                continue
            if frameend == ZCRCE:
                return True, position, errors
            # ZCRCG: keep streaming


def _unique_path(path):
    """Avoid clobbering an existing file: name.txt -> name (1).txt."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    index = 1
    while os.path.exists(f"{stem} ({index}){ext}"):
        index += 1
    return f"{stem} ({index}){ext}"


def find_offer(buffer):
    """Index of a receive offer in *buffer*, or -1.

    Used to auto-start a download when the far end runs ``sz``.
    """
    return buffer.find(RECEIVE_OFFER)
