"""
run.py — launch the wall_v2 app on the standard web ports.

Serves the FastAPI app over HTTPS on port 443 and runs a tiny HTTP listener on
port 80 that redirects everything to HTTPS.  This lets a phone connect by just
typing the IP (e.g. 192.168.1.50) with no port number.

Run with:
    python run.py

Windows note: ports 80/443 are occasionally reserved by the system (IIS /
http.sys "World Wide Web Publishing" service, or HyperV port reservations).
If binding fails with WinError 10013 (access denied / port in use), stop the
conflicting service or free the reservation.
"""

import asyncio
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import RedirectResponse
from starlette.routing import Route

from main import app

load_dotenv()

HERE      = Path(__file__).parent
HOST      = os.getenv("HOST", "0.0.0.0")
HTTPS_PORT = int(os.getenv("HTTPS_PORT", "443"))
HTTP_PORT  = int(os.getenv("HTTP_PORT", "80"))
CERT      = HERE / "cert.pem"
KEY       = HERE / "key.pem"


# ── HTTP → HTTPS redirect app (port 80) ───────────────────────────────
async def _to_https(request):
    host = request.url.hostname
    target = f"https://{host}"
    if HTTPS_PORT != 443:
        target += f":{HTTPS_PORT}"
    target += request.url.path
    if request.url.query:
        target += f"?{request.url.query}"
    return RedirectResponse(target, status_code=307)


redirect_app = Starlette(routes=[Route("/{path:path}", _to_https)])


async def _serve():
    https = uvicorn.Server(uvicorn.Config(
        app, host=HOST, port=HTTPS_PORT,
        ssl_certfile=str(CERT), ssl_keyfile=str(KEY),
    ))
    http = uvicorn.Server(uvicorn.Config(
        redirect_app, host=HOST, port=HTTP_PORT,
    ))
    await asyncio.gather(https.serve(), http.serve())


if __name__ == "__main__":
    if not CERT.exists() or not KEY.exists():
        raise SystemExit("Missing cert.pem / key.pem — run: python gen_cert.py")
    asyncio.run(_serve())
