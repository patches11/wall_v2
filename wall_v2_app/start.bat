@echo off
pushd "%~dp0"
title Wall v2

echo ================================================
echo   Wall v2 companion app
echo ================================================
echo.

:: ── Check .env ────────────────────────────────────
if not exist .env (
    echo [SETUP] .env not found - creating from .env.example
    copy .env.example .env >nul
    echo.
    echo   Edit .env and set SERIAL_PORT to your Teensy COM port.
    echo   Find it in Device Manager under "Ports (COM and LPT)".
    echo.
    pause
    exit /b 1
)

:: ── Generate certificate if missing ───────────────
if not exist cert.pem (
    echo [SETUP] No certificate found - generating now...
    echo.
    python gen_cert.py
    if errorlevel 1 (
        echo.
        echo [ERROR] Certificate generation failed.
        echo         Run:  pip install cryptography
        pause
        exit /b 1
    )
    echo.
)

:: ── Print the address your phone should use ───────
for /f %%i in ('python -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect((\"8.8.8.8\",80)); print(s.getsockname()[0]); s.close()"') do set LOCAL_IP=%%i

echo [INFO] Open on your phone:  https://%LOCAL_IP%:8000
echo [INFO] Press Ctrl+C to stop.
echo.

:: ── Start server ──────────────────────────────────
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --ssl-certfile cert.pem --ssl-keyfile key.pem

echo.
echo Server stopped.
popd
pause
