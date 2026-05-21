# wall_v2 companion app

FastAPI server that bridges the 25×25 LED display (Teensy 3.6 over USB serial) to a phone-installable web app. Controls all animations, lets you draw on the display, mirror the phone camera, and use the accelerometer.

---

## Requirements

- Python 3.10 or later
- The Teensy plugged in via USB (optional — the app runs and serves the UI without it)

---

## Quick start

```powershell
pip install -r requirements.txt
```

Then double-click **`start.bat`** (or run it from PowerShell). It will:
1. Create `.env` from the example if it doesn't exist and prompt you to set the COM port
2. Generate `cert.pem` / `key.pem` automatically if they don't exist
3. Print the `https://` address to open on your phone
4. Start the server

On subsequent runs, double-clicking `start.bat` is all you need.

---

## Manual setup

**1. Install dependencies**

```powershell
pip install -r requirements.txt
```

**2. Configure the serial port**

Copy `.env.example` to `.env` and set the port for your Teensy.

```powershell
copy .env.example .env
```

Open `.env` and edit:

```
SERIAL_PORT=COM4      # Windows: check Device Manager → Ports (COM & LPT)
BAUD_RATE=115200
HOST=0.0.0.0
PORT=8000
```

On Windows, the Teensy shows up as **USB Serial Device** in Device Manager under Ports. If you have multiple COM ports, unplug the Teensy, check the list, plug it back in, and see which port appears.

---

## HTTPS setup (required for camera, motion sensors, and PWA install)

Browsers block camera access, accelerometer access, and PWA installation on plain `http://`. You need a local HTTPS certificate.

**1. Generate the certificate**

```powershell
pip install cryptography
python gen_cert.py
```

This creates `cert.pem` and `key.pem` in the app folder, and prints your local IP address.

**2. Install the certificate on your phone** *(one-time)*

The certificate needs to be trusted by your phone's OS, not just accepted as a browser exception. Accepting the "your connection is not private" warning in the browser is not enough — camera and motion APIs will still be blocked.

Transfer `cert.pem` to your phone first. The easiest methods:
- **Windows share**: put `cert.pem` in a shared folder and open it from your phone's file browser
- **Email**: email it to yourself and open the attachment
- **Serve it**: the running app serves it at `https://<your-ip>:8000/static/cert.pem` once the server is up (accept the browser warning once just to download the file)

**Android (Chrome)**

1. Open **Settings → Security → More security settings → Install from storage**
   *(On some phones: Settings → Biometrics and security → Install from device storage)*
2. Select **CA Certificate** when prompted for the certificate type
3. Tap **Install anyway** on the warning
4. Browse to and select `cert.pem`

You should see "1 CA certificate installed."

**iOS / iPadOS (Safari only)**

1. Open the `cert.pem` file on the device — iOS will say "Profile Downloaded"
2. Go to **Settings → General → VPN & Device Management**
3. Tap the **wall-v2-local** profile and tap **Install** (enter passcode if prompted)
4. Go to **Settings → General → About → Certificate Trust Settings**
5. Find **wall-v2-local** and toggle it **on** — tap Continue on the warning

Both steps are required. Installing the profile (step 3) is not enough on its own; you must also enable full trust in Certificate Trust Settings (step 5).

**3. Start the server with HTTPS**

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --ssl-certfile cert.pem --ssl-keyfile key.pem
```

Open `https://<your-ip>:8000` on your phone. Use `https://`, not `http://`.

---

## Installing as a PWA

A PWA (Progressive Web App) adds the app to your home screen and runs it fullscreen, without the browser chrome.

**Requirements:** The page must be served over HTTPS with a trusted certificate (see above). The browser warning "your connection is not private" must *not* appear — it means the cert isn't trusted yet.

**Android (Chrome)**

Once the site loads without a warning:
1. Tap the **three-dot menu** in Chrome
2. Tap **Add to Home screen**
3. Tap **Add**

Or: Chrome may show an install banner or an **install icon** (⊕) in the address bar automatically.

**iOS (Safari only)**

Chrome on iOS cannot install PWAs — use Safari.

1. Open `https://<your-ip>:8000` in **Safari**
2. Tap the **Share button** (box with arrow pointing up)
3. Scroll down and tap **Add to Home Screen**
4. Tap **Add**

The app icon will appear on your home screen and open fullscreen.

---

## Enabling camera and motion permissions

**Camera**

The first time you open the Camera tab and tap **Start camera**, the browser will ask for camera permission. Tap **Allow**.

If you previously denied it:
- **Android Chrome**: tap the lock icon in the address bar → **Permissions** → Camera → **Allow**
- **iOS Safari**: Settings → Safari → Camera → **Ask** or **Allow**

**Motion sensors (accelerometer)**

iOS 13 and later requires an explicit permission request for motion sensors — the browser cannot access them silently.

1. Go to the **Sensors** tab
2. Tap **Enable motion sensors**
3. Tap **Allow** on the system prompt

If the button shows "Sensors active ✓" without a system prompt, your device granted permission automatically (most Android devices do this).

If the prompt never appears, your browser may not support `DeviceOrientationEvent.requestPermission`. This is a known limitation on some Android browsers; try Chrome if you're on a different browser.

---

## Running without HTTPS (limited functionality)

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Over plain HTTP:
- **Controls tab**: works fully
- **Draw tab**: works fully
- **Camera tab**: blocked by browser (requires HTTPS)
- **Sensors tab**: blocked on iOS; may work on Android Chrome depending on version
- **PWA install**: not available

---

## Connection status indicator

The dot in the top-right corner of the app shows connection state:

| Colour | Meaning |
|--------|---------|
| 🔴 Red | Python server unreachable — check that uvicorn is running |
| 🔵 Blue | Server reachable, waiting for Teensy status |
| 🟠 Amber | Server running, Teensy not detected on the configured serial port |
| 🟢 Green | Teensy connected and responding |

---

## USB serial commands (development)

Connect with any serial terminal at 115200 baud. All commands are newline-terminated.

| Command | Effect |
|---------|--------|
| `?` | Print JSON status (animation, brightness, speed, cycle, draw mode, animation list) |
| `n` / `p` | Next / previous animation |
| `a<N>` | Select animation by index, e.g. `a5` |
| `b+` / `b-` | Brightness up / down (steps of 16) |
| `B<N>` | Set brightness to exact value 0–255 |
| `s+` / `s-` | Speed up / down |
| `S<N>` | Set speed to exact value 0–255 |
| `c` | Toggle auto-cycle on/off |
| `d` / `D` | Enter / exit draw mode |
| `px<x>,<y>,<r>,<g>,<b>` | Set one pixel in draw mode (no auto-show) |
| `ps` | Show (flush pixel updates to display) |
| `pC` | Clear all pixels and show |

---

## Troubleshooting

**"python" is not recognized**

Find the Python executable via pip:
```powershell
pip --version
# prints the path, e.g. ...Python310\Lib\site-packages\pip (python 3.10)
# use the full path: C:\...\Python310\python.exe -m uvicorn ...
```

**Camera says "Camera denied" immediately**

The certificate is not trusted by the OS. A browser-level "proceed anyway" is not sufficient. Follow the certificate installation steps above for your platform.

**Motion sensors button does nothing on iOS**

The permission prompt only appears in Safari, not Chrome or Firefox on iOS. Open the app in Safari.

**Display stuck after using gravity ball or camera mirror**

Reload the page — on reconnect the app automatically sends an exit-draw-mode command to the Teensy. Alternatively, open the browser console and run:
```js
wallSend({type:"draw_mode",active:false})
```

**Teensy not found (amber dot)**

- Check that the correct COM port is set in `.env`
- On Windows, verify in Device Manager → Ports (COM & LPT) that the Teensy appears as "USB Serial Device"
- Try a different USB cable (data cables only — some phone charger cables carry power only)
- Restart the server after changing `.env`

**Text scroll does nothing**

Ensure the Teensy is connected (green dot). The scroll runs server-side and requires an active serial connection.
