"""ZMODEM protocol and app-integration tests.

The protocol half is checked against lrzsz's `sz`/`rz` where those are
installed, which is the only verification that really counts: agreeing with
another implementation of your own reading of the spec proves nothing.

Run:  QT_QPA_PLATFORM=offscreen xvfb-run -a python tests/test_zmodem.py
"""
import hashlib
import os
import pty
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import kestrelsat.app as sg
from kestrelsat import zmodem

failures = []
HAVE_LRZSZ = bool(shutil.which("sz") and shutil.which("rz"))


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


class FdPort:
    """Serial-like wrapper over a file descriptor, for pty-based tests."""

    def __init__(self, fd):
        self.fd = fd
        os.set_blocking(fd, False)

    def read(self, n):
        try:
            return os.read(self.fd, n) or b""
        except (BlockingIOError, InterruptedError):
            time.sleep(0.002)
            return b""
        except OSError:
            return b""

    def write(self, data):
        sent = 0
        while sent < len(data):
            try:
                sent += os.write(self.fd, data[sent:])
            except BlockingIOError:
                time.sleep(0.002)
        return sent

    def flush(self):
        pass


def awkward_bytes(size):
    """Content that exercises every escape rule the protocol has."""
    blob = bytearray()
    nasty = bytes([zmodem.ZDLE, 0x10, 0x11, 0x13, 0x90, 0x91, 0x93, 0x40, 0x0D, 0x0A])
    i = 0
    while len(blob) < size:
        blob += nasty
        blob += bytes([(n * 7) % 256 for n in range(i, i + 40)])
        blob += b"*\x18B" + b"@\r\n"          # looks like a header, must survive
        i += 1
    return bytes(blob[:size])


# ---------------------------------------------------------------------------
print("checksums and escaping")
# CRC-16/XMODEM published check value: "123456789" -> 0x31C3
check(zmodem.crc16(b"123456789") == 0x31C3,
      "CRC-16/XMODEM check value 0x31C3 (got 0x%04X)" % zmodem.crc16(b"123456789"))
check(zmodem.crc32(b"123456789") == 0xCBF43926,
      "CRC-32 check value 0xCBF43926 (got 0x%08X)" % zmodem.crc32(b"123456789"))

check(zmodem.escape(b"abc") == b"abc", "plain bytes are untouched")
check(zmodem.escape(bytes([zmodem.ZDLE])) == bytes([zmodem.ZDLE, 0x58]),
      "ZDLE is escaped to ZDLE 'X'")
for raw in (0x10, 0x11, 0x13, 0x90, 0x91, 0x93):
    got = zmodem.escape(bytes([raw]))
    check(got == bytes([zmodem.ZDLE, raw ^ 0x40]), "0x%02X escaped" % raw)
check(zmodem.escape(b"@\r") == bytes([0x40, zmodem.ZDLE, 0x4D]),
      "CR after '@' is escaped (the modem-escape rule)")
check(zmodem.escape(b"x\r") == b"x\r", "CR elsewhere is left alone")

check(zmodem.find_offer(b"junk**\x18B00 more") == 4, "receive offer is located")
check(zmodem.find_offer(b"nothing here") == -1, "no false positive on plain text")


# ---------------------------------------------------------------------------
print("round trip through our own sender and receiver")


def loopback(payload, name="payload.bin"):
    """Run our sender against our receiver over a socketpair."""
    import socket
    left, right = socket.socketpair()
    left.settimeout(0.05)
    right.settimeout(0.05)

    class SockPort:
        def __init__(self, sock):
            self.sock = sock

        def read(self, n):
            try:
                return self.sock.recv(n)
            except (socket.timeout, BlockingIOError):
                return b""
            except OSError:
                return b""

        def write(self, data):
            self.sock.sendall(data)

        def flush(self):
            pass

    src_dir = tempfile.mkdtemp()
    dst_dir = tempfile.mkdtemp()
    src = os.path.join(src_dir, name)
    with open(src, "wb") as fh:
        fh.write(payload)

    out = {}

    def rx():
        try:
            out["written"] = zmodem.ZModemReceiver(
                SockPort(right), dst_dir, timeout=6).receive()
        except Exception as exc:  # noqa: BLE001
            out["rx_error"] = exc

    def tx():
        try:
            out["sent"] = zmodem.ZModemSender(SockPort(left), timeout=6).send([src])
        except Exception as exc:  # noqa: BLE001
            out["tx_error"] = exc

    threads = [threading.Thread(target=rx, daemon=True),
               threading.Thread(target=tx, daemon=True)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(45)
    left.close()
    right.close()
    return out, dst_dir


for size in (0, 1, 1023, 1024, 1025, 40000):
    out, dst = loopback(awkward_bytes(size))
    if out.get("rx_error") or out.get("tx_error"):
        check(False, "%dB round trip: %r / %r"
              % (size, out.get("tx_error"), out.get("rx_error")))
        continue
    written = out.get("written") or []
    if not written:
        check(False, "%dB round trip produced no file" % size)
        continue
    got = open(written[0], "rb").read()
    check(got == awkward_bytes(size),
          "%dB round trip is byte-identical" % size)

# a name that must not be allowed to escape the destination directory
out, dst = loopback(b"payload", name="evil.bin")
written = (out.get("written") or [None])[0]
check(written is not None and os.path.dirname(os.path.abspath(written)) == dst,
      "received file lands inside the destination directory")


# ---------------------------------------------------------------------------
print("interoperability with lrzsz" if HAVE_LRZSZ else "interoperability (SKIPPED - no lrzsz)")
if HAVE_LRZSZ:
    import pty

    def with_reference(mode, payload):
        src_dir = tempfile.mkdtemp()
        dst_dir = tempfile.mkdtemp()
        src = os.path.join(src_dir, "payload.bin")
        with open(src, "wb") as fh:
            fh.write(payload)

        primary, secondary = pty.openpty()
        if mode == "recv":
            proc = subprocess.Popen(["sz", "--zmodem", "-q", src],
                                    stdin=secondary, stdout=secondary,
                                    stderr=subprocess.DEVNULL, close_fds=True)
        else:
            proc = subprocess.Popen(["rz", "--zmodem", "-q", "-y"],
                                    stdin=secondary, stdout=secondary,
                                    stderr=subprocess.DEVNULL, cwd=dst_dir,
                                    close_fds=True)
        os.close(secondary)

        out = {}

        def run():
            port = FdPort(primary)
            try:
                if mode == "recv":
                    out["written"] = zmodem.ZModemReceiver(port, dst_dir, timeout=8).receive()
                else:
                    out["sent"] = zmodem.ZModemSender(port, timeout=8).send([src])
            except Exception as exc:  # noqa: BLE001
                out["error"] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(60)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out.setdefault("error", "reference tool did not exit")
        os.close(primary)
        return out, dst_dir

    for size in (1, 999, 60000):
        payload = awkward_bytes(size)
        out, dst = with_reference("recv", payload)
        written = (out.get("written") or [None])[0]
        ok = (not out.get("error") and written
              and open(written, "rb").read() == payload)
        check(ok, "reference sz -> our receiver, %dB%s"
              % (size, "" if ok else " (%r)" % out.get("error")))

    for size in (1, 999, 60000):
        payload = awkward_bytes(size)
        out, dst = with_reference("send", payload)
        landed = os.path.join(dst, "payload.bin")
        ok = (not out.get("error") and os.path.exists(landed)
              and open(landed, "rb").read() == payload)
        check(ok, "our sender -> reference rz, %dB%s"
              % (size, "" if ok else " (%r)" % out.get("error")))


# ---------------------------------------------------------------------------
print("app integration")


def make_app(settings=None):
    import json
    if settings is None:
        if os.path.exists(sg.SETTINGS_FILE):
            os.remove(sg.SETTINGS_FILE)
    else:
        with open(sg.SETTINGS_FILE, "w") as fh:
            json.dump(settings, fh)
    root = tk.Tk()
    app = sg.SerialGUI(root)
    root.update()
    return root, app


root, app = make_app({"theme": "dark", "ui_scale": "1.0"})
check(app.zmodem_enabled is True, "enabled by default")
check(os.path.isdir(app.downloads_dir), "downloads directory created")

file_menu = None
menubar = root.nametowidget(root.cget("menu"))
for sub in menubar.winfo_children():
    labels = [sub.entrycget(i, "label")
              for i in range(sub.index("end") + 1)
              if sub.type(i) == "command"]
    if any("ZMODEM" in lbl for lbl in labels):
        file_menu = labels
        break
check(file_menu is not None, "File menu offers ZMODEM entries: %s" % (file_menu,))

# The offer must be recognised in the receive path, but only when enabled and
# only for a real port.
app.is_connected = True
app.test_mode = False
app.serial_connection = None      # so _auto_receive bails before starting a thread
app.display_received_data(b"telemetry line\n" + zmodem.RECEIVE_OFFER + b"0000")
root.update()
check(app.serial_buffer == "", "offer consumed from the monitor buffer")
check(not app._transfer_active.is_set(), "no transfer started without a port")

app.zmodem_enabled = False
app.display_received_data(zmodem.RECEIVE_OFFER + b"0000")
root.update()
check(zmodem.RECEIVE_OFFER.decode("latin-1") in app.serial_buffer,
      "with ZMODEM disabled the offer is left as ordinary data")

# toggling persists
import json
app.zmodem_var = tk.BooleanVar(value=False)
app.on_zmodem_toggled()
check(json.load(open(sg.SETTINGS_FILE))["zmodem_enabled"] is False, "toggle persisted")
app.zmodem_var.set(True)
app.on_zmodem_toggled()
check(json.load(open(sg.SETTINGS_FILE))["zmodem_enabled"] is True, "toggle persisted back")

app.on_closing()

# a fresh app with the setting off keeps it off
root, app = make_app({"theme": "dark", "zmodem_enabled": False})
check(app.zmodem_enabled is False, "disabled setting is honoured at startup")
app.on_closing()

# ---------------------------------------------------------------------------
# The real integration proof: run a transfer through the app itself, with its
# reader thread running, against the reference tool over a pty. This is what
# exercises the port hand-off between read_serial_data and the transfer.
if HAVE_LRZSZ:
    print("end-to-end through the app (reader thread + port arbitration)")

    class PtyPort(FdPort):
        """Enough of serial.Serial for SerialGUI to drive."""

        @property
        def in_waiting(self):
            import select
            r, _, _ = select.select([self.fd], [], [], 0)
            return 1 if r else 0

        def close(self):
            try:
                os.close(self.fd)
            except OSError:
                pass

    payload = awkward_bytes(20000)
    dst_dir = tempfile.mkdtemp()
    src_dir = tempfile.mkdtemp()
    src = os.path.join(src_dir, "telemetry.bin")
    with open(src, "wb") as fh:
        fh.write(payload)

    # --- app receives a file offered by sz ---------------------------------
    primary, secondary = pty.openpty()
    proc = subprocess.Popen(["sz", "--zmodem", "-q", src],
                            stdin=secondary, stdout=secondary,
                            stderr=subprocess.DEVNULL, close_fds=True)
    os.close(secondary)

    root, app = make_app({"theme": "dark", "ui_scale": "1.0"})
    app.downloads_dir = dst_dir
    app.serial_connection = PtyPort(primary)
    app.is_connected = True
    app.test_mode = False
    app.stop_reading = threading.Event()
    app.read_thread = threading.Thread(target=app.read_serial_data, daemon=True)
    app.read_thread.start()

    deadline = time.time() + 45
    while time.time() < deadline:
        root.update()
        if not app._transfer_active.is_set() and os.listdir(dst_dir):
            # give the closing handshake a moment
            if proc.poll() is not None:
                break
        time.sleep(0.01)
    root.update()

    landed = [f for f in os.listdir(dst_dir)]
    ok = bool(landed) and open(os.path.join(dst_dir, landed[0]), "rb").read() == payload
    check(ok, "app auto-received %dB from reference sz via its reader thread%s"
          % (len(payload), "" if ok else " (dir=%s)" % landed))

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    app.is_connected = False
    app.stop_reading.set()
    app.on_closing()

    # --- app sends a file to rz --------------------------------------------
    dst_dir2 = tempfile.mkdtemp()
    primary, secondary = pty.openpty()
    proc = subprocess.Popen(["rz", "--zmodem", "-q", "-y"],
                            stdin=secondary, stdout=secondary,
                            stderr=subprocess.DEVNULL, cwd=dst_dir2, close_fds=True)
    os.close(secondary)

    root, app = make_app({"theme": "dark", "ui_scale": "1.0"})
    app.serial_connection = PtyPort(primary)
    app.is_connected = True
    app.test_mode = False
    app.stop_reading = threading.Event()
    app.read_thread = threading.Thread(target=app.read_serial_data, daemon=True)
    app.read_thread.start()

    app._begin_transfer("send", paths=[src])
    deadline = time.time() + 45
    while time.time() < deadline and app._transfer_active.is_set():
        root.update()
        time.sleep(0.01)
    root.update()

    landed2 = os.path.join(dst_dir2, "telemetry.bin")
    ok = os.path.exists(landed2) and open(landed2, "rb").read() == payload
    check(ok, "app sent %dB to reference rz%s"
          % (len(payload), "" if ok else " (dir=%s)" % os.listdir(dst_dir2)))

    # The worker clears _transfer_active and *then* queues its 'closed' event,
    # which the 20ms pump acts on, so give the pump a chance to run.
    teardown = time.time() + 3
    while time.time() < teardown and app._transfer_dialog is not None:
        root.update()
        time.sleep(0.02)
    check(app._transfer_dialog is None, "progress dialog closed itself")

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    app.is_connected = False
    app.stop_reading.set()
    app.on_closing()

print()
if failures:
    print("FAILED (%d)" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL ZMODEM TESTS PASS")
