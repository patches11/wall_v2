// sensors.js — accelerometer gravity ball, shake-to-next, text scroll UI

(function () {
  const W = 25, H = 25;

  // ── Sensor permission ─────────────────────────────────────────────────

  let sensorsEnabled = false;
  document.getElementById("perm-btn").addEventListener("click", async () => {
    if (typeof DeviceOrientationEvent !== "undefined" &&
        typeof DeviceOrientationEvent.requestPermission === "function") {
      try {
        const perm = await DeviceOrientationEvent.requestPermission();
        if (perm === "granted") enableSensors();
        else document.getElementById("grav-status").textContent = "Permission denied";
      } catch { enableSensors(); }
    } else {
      enableSensors();  // Android / desktop: no permission needed
    }
  });

  function enableSensors() {
    sensorsEnabled = true;
    document.getElementById("perm-btn").textContent = "Sensors active ✓";
    document.getElementById("perm-btn").style.background = "#2e7d32";
    window.addEventListener("deviceorientation", onOrientation);
    window.addEventListener("devicemotion", onMotion);
  }

  // ── Gravity ball ──────────────────────────────────────────────────────

  let ballX = 12, ballY = 12;
  let velX = 0, velY = 0;
  let gravBeta = 0, gravGamma = 0;
  let gravActive = false;
  let gravRaf = null;
  let lastGravFrame = 0;
  const GRAVITY_VIZ = document.getElementById("accel-ball");
  const VIZ_W = document.getElementById("accel-viz").clientWidth  || 280;
  const VIZ_H = document.getElementById("accel-viz").clientHeight || 160;

  function onOrientation(e) {
    // beta: front-back tilt (−180 to 180, ~0 = flat, ~90 = upright)
    // gamma: left-right tilt (−90 to 90)
    gravBeta  = Math.max(-1, Math.min(1, (e.beta  || 0) / 45));
    gravGamma = Math.max(-1, Math.min(1, (e.gamma || 0) / 45));

    // Update tilt visualiser regardless of gravActive
    const px = ((gravGamma + 1) / 2) * (VIZ_W - 20);
    const py = ((gravBeta  + 1) / 2) * (VIZ_H - 20);
    GRAVITY_VIZ.style.left = px + "px";
    GRAVITY_VIZ.style.top  = py + "px";
  }

  // Toggle: checkbox in the Sensors tab (we'll wire it below via data attr)
  function startGravBall() {
    gravActive = true;
    document.getElementById("grav-status").textContent = "Gravity ball: ON";
    window.wallSend?.({ type: "draw_mode", active: true });
    gravRaf = requestAnimationFrame(gravTick);
  }

  function stopGravBall() {
    gravActive = false;
    trailBuf.fill(0);
    const toggle = document.getElementById("grav-toggle");
    if (toggle) toggle.checked = false;
    document.getElementById("grav-status").textContent = "Gravity ball: off";
    if (gravRaf) { cancelAnimationFrame(gravRaf); gravRaf = null; }
    window.wallSend?.({ type: "draw_mode", active: false });
  }

  // Called by app.js when navigating away from the Sensors tab or page hidden
  window.sensorsTabDeactivated = () => { if (gravActive) stopGravBall(); };

  // Wire up a hidden toggle (we re-use the existing toggle-row in Sensors)
  // The toggle is created dynamically here since index.html doesn't have one
  const gravRow = document.createElement("div");
  gravRow.className = "toggle-row";
  gravRow.innerHTML = `
    <label>Gravity ball</label>
    <label class="toggle">
      <input type="checkbox" id="grav-toggle">
      <div class="toggle-track"></div>
    </label>`;
  document.getElementById("grav-status").after(gravRow);
  document.getElementById("grav-toggle").addEventListener("change", e => {
    e.target.checked ? startGravBall() : stopGravBall();
  });

  // ── Ball rendering helpers ────────────────────────────────────────────

  // Persistent trail buffer — not cleared each frame, faded instead
  const trailBuf = new Uint8Array(1875);

  // Convert HSV (h 0-360, s/v 0-1) to [r,g,b] 0-255
  function hsvToRgb(h, s, v) {
    const c = v * s, x = c * (1 - Math.abs((h / 60) % 2 - 1)), m = v - c;
    let r = 0, g = 0, b = 0;
    if      (h < 60)  { r=c; g=x; }
    else if (h < 120) { r=x; g=c; }
    else if (h < 180) { g=c; b=x; }
    else if (h < 240) { g=x; b=c; }
    else if (h < 300) { r=x; b=c; }
    else              { r=c; b=x; }
    return [Math.round((r+m)*255), Math.round((g+m)*255), Math.round((b+m)*255)];
  }

  // Map speed (px/frame) to a hue: slow = deep blue (220°), fast = white-hot cyan (180°→0°)
  function speedColor(speed) {
    const t = Math.min(speed / 1.2, 1.0);
    const h = 220 - t * 220;     // 220 (blue) → 0 (red) through cyan
    const s = 1.0 - t * 0.4;    // desaturates slightly at high speed
    const v = 0.4 + t * 0.6;    // dim when slow, bright when fast
    return hsvToRgb(h, s, v);
  }

  function renderBall() {
    // Fade the trail each frame
    for (let i = 0; i < trailBuf.length; i++) trailBuf[i] = (trailBuf[i] * 3 >> 2);

    const bx = Math.round(ballX), by = Math.round(ballY);
    const speed = Math.hypot(velX, velY);
    const [r, g, b] = speedColor(speed);

    // 9-pixel cross glow: centre full, cardinals 50%, diagonals 25%
    const glow = [
      [0, 0, 1.0], [1,0,.5], [-1,0,.5], [0,1,.5], [0,-1,.5],
      [1,1,.25], [-1,1,.25], [1,-1,.25], [-1,-1,.25]
    ];
    for (const [dx, dy, br] of glow) {
      const gx = bx + dx, gy = by + dy;
      if (gx < 0 || gx >= W || gy < 0 || gy >= H) continue;
      const i = (gy * W + gx) * 3;
      trailBuf[i]   = Math.min(255, trailBuf[i]   + ((r * br) | 0));
      trailBuf[i+1] = Math.min(255, trailBuf[i+1] + ((g * br) | 0));
      trailBuf[i+2] = Math.min(255, trailBuf[i+2] + ((b * br) | 0));
    }
    window.wallSendBinary?.(trailBuf.buffer);
  }

  function gravTick(ts) {
    if (!gravActive) return;
    gravRaf = requestAnimationFrame(gravTick);
    if (ts - lastGravFrame < 50) return;  // ~20 fps
    lastGravFrame = ts;

    velX += gravGamma * 0.4;
    velY += gravBeta  * 0.4;
    velX *= 0.88;
    velY *= 0.88;
    ballX = Math.max(0, Math.min(24, ballX + velX));
    ballY = Math.max(0, Math.min(24, ballY + velY));

    if (ballX <= 0 || ballX >= 24) velX *= -0.6;
    if (ballY <= 0 || ballY >= 24) velY *= -0.6;

    renderBall();
  }

  // ── Shake to next ─────────────────────────────────────────────────────

  let lastAcc = { x: 0, y: 0, z: 0 };
  let shakeDebounce = 0;

  function onMotion(e) {
    if (!document.getElementById("shake-toggle").checked) return;
    const a = e.accelerationIncludingGravity || {};
    const dx = (a.x || 0) - lastAcc.x;
    const dy = (a.y || 0) - lastAcc.y;
    const dz = (a.z || 0) - lastAcc.z;
    lastAcc = { x: a.x || 0, y: a.y || 0, z: a.z || 0 };

    const mag = Math.sqrt(dx*dx + dy*dy + dz*dz);
    const now = Date.now();
    if (mag > 20 && now - shakeDebounce > 1500) {
      shakeDebounce = now;
      window.wallSend?.({ type: "event", name: "NEXT_ANIM" });
    }
  }

  // ── Text scroll ───────────────────────────────────────────────────────

  const scrollColorPicker = document.getElementById("scroll-color-picker");
  const scrollColorSwatch = document.getElementById("scroll-color-swatch");
  const fpsSl   = document.getElementById("scroll-fps");
  const fpsLbl  = document.getElementById("scroll-fps-val");

  function syncScrollSwatch() { scrollColorSwatch.style.background = scrollColorPicker.value; }
  scrollColorPicker.addEventListener("input", syncScrollSwatch);
  // iOS fix: overlay input inside swatch (same pattern as draw.js color picker)
  scrollColorSwatch.style.position = 'relative';
  Object.assign(scrollColorPicker.style, {
    position: 'absolute', top: '0', left: '0',
    width: '100%', height: '100%', opacity: '0', cursor: 'pointer'
  });
  scrollColorSwatch.appendChild(scrollColorPicker);
  fpsSl.addEventListener("input", () => fpsLbl.textContent = fpsSl.value);
  syncScrollSwatch();

  function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
  }

  document.getElementById("scroll-send-btn").addEventListener("click", () => {
    const text = document.getElementById("scroll-textarea").value.trim();
    if (!text) return;
    const [r, g, b] = hexToRgb(scrollColorPicker.value);
    window.wallSend?.({
      type: "scroll_text",
      text, r, g, b,
      fps: parseFloat(fpsSl.value),
      repeat: 2,
    });
  });
})();
