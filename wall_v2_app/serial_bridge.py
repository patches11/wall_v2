"""
serial_bridge.py — serial port manager for the wall_v2 companion app.

Runs a blocking-read thread alongside the asyncio event loop.  Commands are
queued from async code and drained by a write coroutine.  JSON status lines
from the Teensy are parsed and cached; all connected WebSocket clients are
notified whenever status changes.
"""

import asyncio
import json
import logging
import threading
import time
from typing import Any

import serial  # pyserial

log = logging.getLogger(__name__)

W = H = 25
GRID_PIXELS = W * H  # 625


class SerialBridge:
    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self._ser: serial.Serial | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._write_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._clients: set[Any] = set()   # WebSocket instances
        self._status: dict = {}
        self._connected = False
        self._scroll_task: asyncio.Task | None = None
        self._video_remaining = 0   # bytes left in the active video upload (0 = idle)
        self._video_total = 0       # total size of the active upload (for logging)
        self._video_sent = 0        # bytes handed to the serial queue so far
        self._video_t0 = 0.0        # monotonic start time of the active upload
        self._video_log_mark = 0    # next byte threshold to log progress at

    # ── Connection ────────────────────────────────────────────────────

    def connect(self) -> bool:
        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=0.1, write_timeout=10)
            self._connected = True
            log.info("Serial connected: %s @ %d", self.port, self.baud)
            return True
        except (serial.SerialException, OSError) as e:
            log.warning("Serial not available (%s): %s", self.port, e)
            self._connected = False
            return False

    def start(self, loop: asyncio.AbstractEventLoop):
        """Call once from startup.  Launches the reconnect supervisor and write coroutine."""
        self._loop = loop
        threading.Thread(target=self._serial_supervisor, daemon=True).start()
        loop.create_task(self._write_loop())

    # ── Reconnect supervisor (blocking serial → asyncio) ──────────────

    def _serial_supervisor(self):
        """Owns the serial port for the process lifetime: (re)opens on demand,
        reads lines while connected, and recovers from unplug/replug without a
        server restart."""
        backoff = 0.5
        while True:
            if not self._connected:
                if self.connect():
                    backoff = 0.5
                    asyncio.run_coroutine_threadsafe(self._on_reconnect(), self._loop)
                else:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 3.0)
                    continue
            try:
                raw = self._ser.readline()
                if raw:
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if line.startswith("{"):
                        asyncio.run_coroutine_threadsafe(
                            self._on_status_line(line), self._loop
                        )
            except (serial.SerialException, OSError, TypeError):
                log.warning("Serial read error — disconnected")
                self._mark_disconnected()

    def _mark_disconnected(self):
        self._connected = False
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self._ser = None
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self.broadcast({
                    "type": "serial_state", "connected": False,
                    "msg": f"Teensy disconnected ({self.port})",
                }),
                self._loop,
            )

    async def _on_reconnect(self):
        # Drop any frames queued while we were down, then resync clients.
        while not self._write_queue.empty():
            try:
                self._write_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        await self.broadcast({"type": "serial_state", "connected": True})
        await self.query()

    async def _on_status_line(self, line: str):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return
        # Video list / upload acks — forward as-is, don't touch cached status.
        if data.get("type") in ("videos", "video_uploaded", "video_deleted"):
            await self.broadcast(data)
            return
        # Frame readback (base64) — forward without touching cached status.
        if "frame" in data:
            await self.broadcast({"type": "frame_data", "frame": data["frame"]})
            return
        # Saved-drawing slot list.
        if "saved" in data:
            await self.broadcast({"type": "saved", **data})
            return
        data["serial"] = True
        self._status = data
        await self.broadcast({"type": "status", **data})

    # ── Write loop (asyncio → blocking serial) ────────────────────────

    async def _write_loop(self):
        while True:
            data = await self._write_queue.get()
            if self._ser and self._connected:
                try:
                    t0 = time.monotonic()
                    await asyncio.to_thread(self._ser.write, data)
                    dt = time.monotonic() - t0
                    # A single chunk should flush in milliseconds; a multi-second
                    # write means the Teensy stopped reading (busy on SD) — log it
                    # so we can see exactly where an upload stalls.
                    if dt > 0.5:
                        log.warning("Slow serial write: %d bytes took %.1fs", len(data), dt)
                except serial.SerialTimeoutException:
                    log.error("Serial write timed out (%d bytes) — Teensy not draining USB; "
                              "aborting upload", len(data))
                    self._video_remaining = 0
                except (serial.SerialException, OSError):
                    self._mark_disconnected()

    # ── Public send helpers ───────────────────────────────────────────

    async def send_text(self, cmd: str):
        """Send a newline-terminated text command to the Teensy."""
        if not self._connected:
            return
        await self._write_queue.put((cmd + "\n").encode())

    async def send_binary(self, data: bytes):
        """Send raw bytes (e.g. b'F' + 1875-byte frame)."""
        if not self._connected:
            return
        await self._write_queue.put(data)

    async def query(self):
        """Ask the Teensy for a JSON status update."""
        await self.send_text("?")

    # ── Video upload (browser → serial → SD) ──────────────────────────

    async def begin_video_upload(self, name: str, size: int):
        """Send the upload header; the raw .wv25 bytes follow as binary chunks."""
        if not self._connected:
            return
        name = name.replace(",", "").replace("\n", "").replace("\r", "")
        self._video_remaining = max(0, int(size))
        self._video_total = self._video_remaining
        self._video_sent = 0
        self._video_t0 = time.monotonic()
        self._video_log_mark = 0
        log.info("Upload begin: %s (%d bytes)", name, self._video_total)
        await self.send_text(f"u{name},{self._video_remaining}")

    async def feed_video_chunk(self, data: bytes):
        """Forward one chunk of an in-progress upload straight to the Teensy."""
        if self._video_remaining <= 0:
            return
        # Backpressure: the serial link drains far slower than the browser can
        # send.  Without this the whole file piles into _write_queue in RAM and
        # the progress bar hits 100% long before the SD write finishes (looks
        # like a hang).  Waiting here stalls the WS read, which pauses the
        # browser, so progress tracks the real serial throughput and RAM stays
        # bounded (~64 chunks ≈ 256 KB in flight).
        while self._write_queue.qsize() > 64:
            await asyncio.sleep(0.005)
        await self.send_binary(data)
        self._video_remaining = max(0, self._video_remaining - len(data))
        self._video_sent += len(data)
        # Log every ~2 MB so the console shows the live rate and the exact byte
        # offset if/where an upload stalls.
        if self._video_sent >= self._video_log_mark:
            self._video_log_mark += 2 * 1024 * 1024
            elapsed = max(1e-3, time.monotonic() - self._video_t0)
            rate = self._video_sent / elapsed / 1024  # KB/s
            log.info("Upload progress: %.1f/%.1f MB  %.0f KB/s  queue=%d",
                     self._video_sent / 1048576, self._video_total / 1048576,
                     rate, self._write_queue.qsize())
            # Emit a liveness/progress message to the client.  The serial link is
            # silent for the duration of a (multi-minute) upload, so without this
            # the browser's heartbeat sees a stale socket and force-closes it,
            # aborting the transfer.  This also drives an accurate progress bar.
            await self.broadcast({"type": "upload_progress",
                                  "sent": self._video_sent, "total": self._video_total})

    @property
    def uploading(self) -> bool:
        return self._video_remaining > 0

    # ── WebSocket client registry ─────────────────────────────────────

    def add_client(self, ws):
        self._clients.add(ws)

    def remove_client(self, ws):
        self._clients.discard(ws)

    async def broadcast(self, msg: dict):
        if not self._clients:
            return
        payload = json.dumps(msg)
        dead = set()
        for ws in self._clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    # ── Text scroll ───────────────────────────────────────────────────
    # 5×7 pixel ASCII font, printable chars 0x20–0x7E.
    # Each char is 5 columns × 7 rows, packed as 5 bytes (bit 0 = top row).

    FONT_5X7: dict[int, bytes] = {
        0x20: bytes([0x00,0x00,0x00,0x00,0x00]),  # space
        0x21: bytes([0x00,0x00,0x5F,0x00,0x00]),  # !
        0x22: bytes([0x00,0x07,0x00,0x07,0x00]),  # "
        0x23: bytes([0x14,0x7F,0x14,0x7F,0x14]),  # #
        0x24: bytes([0x24,0x2A,0x7F,0x2A,0x12]),  # $
        0x25: bytes([0x23,0x13,0x08,0x64,0x62]),  # %
        0x26: bytes([0x36,0x49,0x55,0x22,0x50]),  # &
        0x27: bytes([0x00,0x05,0x03,0x00,0x00]),  # '
        0x28: bytes([0x00,0x1C,0x22,0x41,0x00]),  # (
        0x29: bytes([0x00,0x41,0x22,0x1C,0x00]),  # )
        0x2A: bytes([0x08,0x2A,0x1C,0x2A,0x08]),  # *
        0x2B: bytes([0x08,0x08,0x3E,0x08,0x08]),  # +
        0x2C: bytes([0x00,0x50,0x30,0x00,0x00]),  # ,
        0x2D: bytes([0x08,0x08,0x08,0x08,0x08]),  # -
        0x2E: bytes([0x00,0x60,0x60,0x00,0x00]),  # .
        0x2F: bytes([0x20,0x10,0x08,0x04,0x02]),  # /
        0x30: bytes([0x3E,0x51,0x49,0x45,0x3E]),  # 0
        0x31: bytes([0x00,0x42,0x7F,0x40,0x00]),  # 1
        0x32: bytes([0x42,0x61,0x51,0x49,0x46]),  # 2
        0x33: bytes([0x21,0x41,0x45,0x4B,0x31]),  # 3
        0x34: bytes([0x18,0x14,0x12,0x7F,0x10]),  # 4
        0x35: bytes([0x27,0x45,0x45,0x45,0x39]),  # 5
        0x36: bytes([0x3C,0x4A,0x49,0x49,0x30]),  # 6
        0x37: bytes([0x01,0x71,0x09,0x05,0x03]),  # 7
        0x38: bytes([0x36,0x49,0x49,0x49,0x36]),  # 8
        0x39: bytes([0x06,0x49,0x49,0x29,0x1E]),  # 9
        0x3A: bytes([0x00,0x36,0x36,0x00,0x00]),  # :
        0x3B: bytes([0x00,0x56,0x36,0x00,0x00]),  # ;
        0x3C: bytes([0x00,0x08,0x14,0x22,0x41]),  # <
        0x3D: bytes([0x14,0x14,0x14,0x14,0x14]),  # =
        0x3E: bytes([0x41,0x22,0x14,0x08,0x00]),  # >
        0x3F: bytes([0x02,0x01,0x51,0x09,0x06]),  # ?
        0x40: bytes([0x32,0x49,0x79,0x41,0x3E]),  # @
        0x41: bytes([0x7E,0x11,0x11,0x11,0x7E]),  # A
        0x42: bytes([0x7F,0x49,0x49,0x49,0x36]),  # B
        0x43: bytes([0x3E,0x41,0x41,0x41,0x22]),  # C
        0x44: bytes([0x7F,0x41,0x41,0x22,0x1C]),  # D
        0x45: bytes([0x7F,0x49,0x49,0x49,0x41]),  # E
        0x46: bytes([0x7F,0x09,0x09,0x09,0x01]),  # F
        0x47: bytes([0x3E,0x41,0x49,0x49,0x7A]),  # G
        0x48: bytes([0x7F,0x08,0x08,0x08,0x7F]),  # H
        0x49: bytes([0x00,0x41,0x7F,0x41,0x00]),  # I
        0x4A: bytes([0x20,0x40,0x41,0x3F,0x01]),  # J
        0x4B: bytes([0x7F,0x08,0x14,0x22,0x41]),  # K
        0x4C: bytes([0x7F,0x40,0x40,0x40,0x40]),  # L
        0x4D: bytes([0x7F,0x02,0x04,0x02,0x7F]),  # M
        0x4E: bytes([0x7F,0x04,0x08,0x10,0x7F]),  # N
        0x4F: bytes([0x3E,0x41,0x41,0x41,0x3E]),  # O
        0x50: bytes([0x7F,0x09,0x09,0x09,0x06]),  # P
        0x51: bytes([0x3E,0x41,0x51,0x21,0x5E]),  # Q
        0x52: bytes([0x7F,0x09,0x19,0x29,0x46]),  # R
        0x53: bytes([0x46,0x49,0x49,0x49,0x31]),  # S
        0x54: bytes([0x01,0x01,0x7F,0x01,0x01]),  # T
        0x55: bytes([0x3F,0x40,0x40,0x40,0x3F]),  # U
        0x56: bytes([0x1F,0x20,0x40,0x20,0x1F]),  # V
        0x57: bytes([0x3F,0x40,0x38,0x40,0x3F]),  # W
        0x58: bytes([0x63,0x14,0x08,0x14,0x63]),  # X
        0x59: bytes([0x07,0x08,0x70,0x08,0x07]),  # Y
        0x5A: bytes([0x61,0x51,0x49,0x45,0x43]),  # Z
        0x5B: bytes([0x00,0x7F,0x41,0x41,0x00]),  # [
        0x5C: bytes([0x02,0x04,0x08,0x10,0x20]),  # backslash
        0x5D: bytes([0x00,0x41,0x41,0x7F,0x00]),  # ]
        0x5E: bytes([0x04,0x02,0x01,0x02,0x04]),  # ^
        0x5F: bytes([0x40,0x40,0x40,0x40,0x40]),  # _
        0x60: bytes([0x00,0x01,0x02,0x04,0x00]),  # `
        0x61: bytes([0x20,0x54,0x54,0x54,0x78]),  # a
        0x62: bytes([0x7F,0x48,0x44,0x44,0x38]),  # b
        0x63: bytes([0x38,0x44,0x44,0x44,0x20]),  # c
        0x64: bytes([0x38,0x44,0x44,0x48,0x7F]),  # d
        0x65: bytes([0x38,0x54,0x54,0x54,0x18]),  # e
        0x66: bytes([0x08,0x7E,0x09,0x01,0x02]),  # f
        0x67: bytes([0x0C,0x52,0x52,0x52,0x3E]),  # g
        0x68: bytes([0x7F,0x08,0x04,0x04,0x78]),  # h
        0x69: bytes([0x00,0x44,0x7D,0x40,0x00]),  # i
        0x6A: bytes([0x20,0x40,0x44,0x3D,0x00]),  # j
        0x6B: bytes([0x7F,0x10,0x28,0x44,0x00]),  # k
        0x6C: bytes([0x00,0x41,0x7F,0x40,0x00]),  # l
        0x6D: bytes([0x7C,0x04,0x18,0x04,0x78]),  # m
        0x6E: bytes([0x7C,0x08,0x04,0x04,0x78]),  # n
        0x6F: bytes([0x38,0x44,0x44,0x44,0x38]),  # o
        0x70: bytes([0x7C,0x14,0x14,0x14,0x08]),  # p
        0x71: bytes([0x08,0x14,0x14,0x18,0x7C]),  # q
        0x72: bytes([0x7C,0x08,0x04,0x04,0x08]),  # r
        0x73: bytes([0x48,0x54,0x54,0x54,0x20]),  # s
        0x74: bytes([0x04,0x3F,0x44,0x40,0x20]),  # t
        0x75: bytes([0x3C,0x40,0x40,0x20,0x7C]),  # u
        0x76: bytes([0x1C,0x20,0x40,0x20,0x1C]),  # v
        0x77: bytes([0x3C,0x40,0x30,0x40,0x3C]),  # w
        0x78: bytes([0x44,0x28,0x10,0x28,0x44]),  # x
        0x79: bytes([0x0C,0x50,0x50,0x50,0x3C]),  # y
        0x7A: bytes([0x44,0x64,0x54,0x4C,0x44]),  # z
        0x7B: bytes([0x00,0x08,0x36,0x41,0x00]),  # {
        0x7C: bytes([0x00,0x00,0x7F,0x00,0x00]),  # |
        0x7D: bytes([0x00,0x41,0x36,0x08,0x00]),  # }
        0x7E: bytes([0x08,0x04,0x08,0x10,0x08]),  # ~
    }
    CHAR_W = 5
    CHAR_GAP = 1  # 1 pixel between characters
    CHAR_STRIDE = CHAR_W + CHAR_GAP  # 6 pixels per character

    def _text_to_columns(self, text: str) -> list[int]:
        """Return a flat list of column bitmasks (one int per column) for the full text.
        Each int is 7 bits wide: bit 0 = top row, bit 6 = bottom row of the 5×7 glyph.
        """
        cols: list[int] = [0] * W  # leading blank columns
        for ch in text:
            glyph = self.FONT_5X7.get(ord(ch), self.FONT_5X7[0x3F])  # '?' for unknown
            cols.extend(glyph)          # bytes iterates as ints — exactly what we want
            cols.extend([0] * self.CHAR_GAP)
        cols.extend([0] * W)            # trailing blank columns
        return cols

    def render_text_frame(
        self,
        cols: list[int],
        offset: int,
        r: int, g: int, b: int,
        bg_r: int = 0, bg_g: int = 0, bg_b: int = 0,
    ) -> bytes:
        """
        Render a 25×25 RGB frame from scrolling-text column data.

        offset: first column index into cols[] visible at x=0.
        Font is centred vertically (top margin = (25 - 7) // 2 = 9).
        """
        TEXT_TOP = (H - 7) // 2  # 9
        buf = bytearray(GRID_PIXELS * 3)
        for x in range(W):
            col_idx = offset + x
            bits = cols[col_idx] if col_idx < len(cols) else 0
            for y in range(H):
                row = y - TEXT_TOP
                pixel_on = (0 <= row < 7) and bool(bits & (1 << row))
                idx = (y * W + x) * 3
                if pixel_on:
                    buf[idx], buf[idx+1], buf[idx+2] = r, g, b
                else:
                    buf[idx], buf[idx+1], buf[idx+2] = bg_r, bg_g, bg_b
        return bytes(buf)

    async def scroll_text(
        self,
        text: str,
        r: int = 255, g: int = 255, b: int = 255,
        fps: float = 12.0,
        repeat: int = 1,
    ):
        """
        Stream a scrolling text message to the display.
        Cancels any in-progress scroll task before starting.
        No-op if serial is not connected.
        """
        if not self._connected:
            return
        await self.cancel_scroll()
        self._scroll_task = asyncio.create_task(
            self._scroll_loop(text, r, g, b, fps, repeat)
        )

    async def cancel_scroll(self):
        if self._scroll_task and not self._scroll_task.done():
            self._scroll_task.cancel()
            try:
                await self._scroll_task
            except asyncio.CancelledError:
                pass
        self._scroll_task = None

    async def _scroll_loop(
        self,
        text: str,
        r: int, g: int, b: int,
        fps: float,
        repeat: int,
    ):
        cols = self._text_to_columns(text)
        interval = 1.0 / fps
        total_cols = len(cols)
        await self.send_text("d")  # enter draw mode
        try:
            for _ in range(repeat):
                for offset in range(total_cols - W + 1):
                    frame = self.render_text_frame(cols, offset, r, g, b)
                    await self.send_binary(b"F" + frame)
                    await asyncio.sleep(interval)
        finally:
            await self.send_text("D")  # return to animation mode

    @property
    def status(self) -> dict:
        return self._status

    @property
    def connected(self) -> bool:
        return self._connected
