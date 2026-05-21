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

    # ── Connection ────────────────────────────────────────────────────

    def connect(self) -> bool:
        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=0.1)
            self._connected = True
            log.info("Serial connected: %s @ %d", self.port, self.baud)
            return True
        except serial.SerialException as e:
            log.warning("Serial not available (%s): %s", self.port, e)
            self._connected = False
            return False

    def start(self, loop: asyncio.AbstractEventLoop):
        """Call once from startup after connect().  Launches read thread and write coroutine."""
        self._loop = loop
        if self._connected:
            threading.Thread(target=self._read_thread, daemon=True).start()
            loop.create_task(self._write_loop())

    # ── Read thread (blocking serial → asyncio) ───────────────────────

    def _read_thread(self):
        """Runs in a daemon thread.  Reads lines and schedules JSON parsing on the loop."""
        while self._connected and self._ser:
            try:
                raw = self._ser.readline()
                if raw:
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if line.startswith("{"):
                        asyncio.run_coroutine_threadsafe(
                            self._on_status_line(line), self._loop
                        )
            except serial.SerialException:
                log.warning("Serial read error — disconnected?")
                self._connected = False
                break

    async def _on_status_line(self, line: str):
        try:
            data = json.loads(line)
            data["serial"] = True
            self._status = data
            await self.broadcast({"type": "status", **data})
        except json.JSONDecodeError:
            pass

    # ── Write loop (asyncio → blocking serial) ────────────────────────

    async def _write_loop(self):
        while True:
            data = await self._write_queue.get()
            if self._ser and self._connected:
                await asyncio.to_thread(self._ser.write, data)

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
