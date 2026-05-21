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

    // Bounce off walls
    if (ballX <= 0 || ballX >= 24) velX *= -0.6;
    if (ballY <= 0 || ballY >= 24) velY *= -0.6;

    // Render 25×25 frame
    const buf = new ArrayBuffer(1875);
    const arr = new Uint8Array(buf);
    // Fade background (by leaving it black)
    const bx = Math.round(ballX), by = Math.round(ballY);
    // Glow: centre + 4 neighbours
    const glow = [[bx,by,255],[bx-1,by,80],[bx+1,by,80],[bx,by-1,80],[bx,by+1,80]];
    for (const [gx, gy, bri] of glow) {
      if (gx < 0 || gx >= W || gy < 0 || gy >= H) continue;
      const i = (gy * W + gx) * 3;
      arr[i] = bri; arr[i+1] = Math.floor(bri * 0.3); arr[i+2] = 0;
    }
    window.wallSendBinary?.(buf);
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
  scrollColorSwatch.addEventListener("click", () => scrollColorPicker.click());
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
