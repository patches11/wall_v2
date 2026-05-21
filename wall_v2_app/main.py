"""
main.py — FastAPI companion app for the wall_v2 LED display.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Then open http://<your-ip>:8000 on your phone.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from serial_bridge import SerialBridge

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
BAUD_RATE   = int(os.getenv("BAUD_RATE", "115200"))
STATIC_DIR  = Path(__file__).parent / "static"

bridge = SerialBridge(SERIAL_PORT, BAUD_RATE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bridge.connect()
    bridge.start(asyncio.get_event_loop())
    # Ask the Teensy for its current state on startup
    if bridge.connected:
        await asyncio.sleep(0.5)
        await bridge.query()
    yield
    await bridge.cancel_scroll()


app = FastAPI(title="wall_v2", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── HTTP routes ───────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/sw.js")
async def service_worker():
    # Serve the service worker from the root path so its default scope is "/"
    # and it can control the main page.  The Service-Worker-Allowed header
    # permits a scope broader than the script's own directory.
    return FileResponse(STATIC_DIR / "sw.js", headers={"Service-Worker-Allowed": "/"})


@app.get("/api/status")
async def api_status():
    return bridge.status or {"error": "no status yet"}


# Text scroll — POST /api/text  body: {"text":"...", "r":255,"g":255,"b":255,"fps":12,"repeat":2}
@app.post("/api/text")
async def api_text(payload: dict):
    text   = str(payload.get("text", ""))[:200]
    r      = int(payload.get("r", 255))
    g      = int(payload.get("g", 255))
    b      = int(payload.get("b", 255))
    fps    = float(payload.get("fps", 12.0))
    repeat = int(payload.get("repeat", 2))
    asyncio.create_task(bridge.scroll_text(text, r, g, b, fps, repeat))
    return {"ok": True, "text": text}


@app.post("/api/stop_scroll")
async def api_stop_scroll():
    await bridge.cancel_scroll()
    await bridge.send_text("D")
    return {"ok": True}


# ── WebSocket ─────────────────────────────────────────────────────────

# Map JSON event names to Teensy text commands
_EVENT_TO_CMD: dict[str, str | None] = {
    "NEXT_ANIM":    "n",
    "PREV_ANIM":    "p",
    "TOGGLE_CYCLE": "c",
    "BRIGHT_UP":    "b+",
    "BRIGHT_DOWN":  "b-",
    "SPEED_UP":     "s+",
    "SPEED_DOWN":   "s-",
}


async def _dispatch(ws: WebSocket, msg: str | bytes):
    """Handle one incoming WebSocket message from the browser."""
    # ── Binary frame (1875 bytes of raw RGB) ─────────────────────────
    if isinstance(msg, bytes):
        if len(msg) == 1875:
            await bridge.send_binary(b"F" + msg)
        return

    # ── JSON text message ─────────────────────────────────────────────
    try:
        data = json.loads(msg)
    except json.JSONDecodeError:
        return

    mtype = data.get("type")

    if mtype == "query":
        await bridge.query()
        await asyncio.sleep(0.15)
        await bridge.broadcast({"type": "status", **bridge.status})

    elif mtype == "event":
        name = data.get("name", "")
        cmd = _EVENT_TO_CMD.get(name)
        if cmd:
            await bridge.send_text(cmd)
        elif name == "SELECT_ANIM":
            await bridge.send_text(f"a{int(data['value'])}")
        elif name == "SET_BRIGHT":
            v = max(0, min(255, int(data["value"])))
            await bridge.send_text(f"B{v}")
        elif name == "SET_SPEED":
            v = max(1, min(255, int(data["value"])))
            await bridge.send_text(f"S{v}")
        # Refresh status after every command
        await asyncio.sleep(0.1)
        await bridge.query()

    elif mtype == "draw_mode":
        active = bool(data.get("active"))
        await bridge.send_text("d" if active else "D")

    elif mtype == "draw_pixel":
        x, y = int(data["x"]), int(data["y"])
        r, g, b = int(data["r"]), int(data["g"]), int(data["b"])
        await bridge.send_text(f"px{x},{y},{r},{g},{b}")

    elif mtype == "draw_show":
        await bridge.send_text("ps")

    elif mtype == "draw_clear":
        await bridge.send_text("pC")

    elif mtype == "camera_palette":
        # Browser sampled 6 dominant colors; for now just acknowledge.
        # Future: map hues to Teensy voronoi seed commands.
        await ws.send_text(json.dumps({"type": "palette_ack", "colors": data.get("colors")}))

    elif mtype == "scroll_text":
        text   = str(data.get("text", ""))[:200]
        r      = int(data.get("r", 255))
        g      = int(data.get("g", 255))
        b      = int(data.get("b", 255))
        fps    = float(data.get("fps", 12.0))
        repeat = int(data.get("repeat", 2))
        asyncio.create_task(bridge.scroll_text(text, r, g, b, fps, repeat))

    elif mtype == "stop_scroll":
        await bridge.cancel_scroll()
        await bridge.send_text("D")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    bridge.add_client(ws)
    log.info("WS client connected (total: %d)", len(bridge._clients))

    # Always send serial connection state first so the UI can reflect it
    if not bridge.connected:
        await ws.send_text(json.dumps({
            "type": "serial_state", "connected": False,
            "msg": f"Teensy not found on {SERIAL_PORT}"
        }))
    elif bridge.status:
        await ws.send_text(json.dumps({"type": "status", **bridge.status}))
    else:
        await bridge.query()

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if "text" in msg:
                await _dispatch(ws, msg["text"])
            elif "bytes" in msg:
                await _dispatch(ws, msg["bytes"])
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bridge.remove_client(ws)
        log.info("WS client disconnected (total: %d)", len(bridge._clients))
