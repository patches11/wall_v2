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

  // ── Tilt-driven water ─────────────────────────────────────────────────
  // Particle-based viscoelastic fluid (Clavet 2005, "double-density relaxation")
  // with a spatial-hash neighbour search so we can afford enough particles to
  // fill a real body of water on the 25×25 grid. The phone is a "tank hung on
  // the wall": water always falls toward the current down-edge; tilt rotates
  // which edge is down (gentle slosh, or a decisive lean to pour it across).

  const NP = 220;
  const pX = new Float32Array(NP), pY = new Float32Array(NP);
  const vX = new Float32Array(NP), vY = new Float32Array(NP);
  const prevX = new Float32Array(NP), prevY = new Float32Array(NP);
  let fluidSeeded = false;
  let gravActive = false;
  let gravRaf = null;
  let lastGravFrame = 0;

  // ── Tunables (safe to tweak; effect is visible live in #fluid-preview) ──
  const H_RAD   = 2.6;          // interaction radius
  const H2      = H_RAD * H_RAD;
  const K_STIFF = 0.004;        // pressure stiffness (incompressibility)
  const K_NEAR  = 0.010;        // near-pressure (surface tension / anti-clump)
  const RHO0    = 3.0;          // rest density → how much the body spreads out
  const SIGMA   = 0.0;          // linear viscosity
  const BETA    = 0.20;         // quadratic viscosity (cohesion)
  const GRAV    = 0.030;        // constant gravity magnitude (gentle)
  const VEL_CAP = 4.0;          // anti-tunnelling speed clamp (cells/step)

  // Smoothed gravity direction in grid coords (+y = down). Starts pointing down.
  let gDirX = 0, gDirY = 1;
  let rawBeta = 90, rawGamma = 0;   // latest device orientation angles (deg)
  let ambientT = 0;                  // idle-wave phase

  // Spatial hash: uniform grid, cell size = interaction radius, counting-sorted.
  const GW = Math.ceil(W / H_RAD), GH = Math.ceil(H / H_RAD), NC = GW * GH;
  const cellCount  = new Int32Array(NC);
  const cellStart  = new Int32Array(NC + 1);
  const cellCursor = new Int32Array(NC);
  const cellItems  = new Int32Array(NP);

  // Live preview that renders the exact RGB frame streamed to the wall.
  const previewCanvas = document.getElementById("fluid-preview");
  const previewCtx = previewCanvas.getContext("2d");
  const previewImg = previewCtx.createImageData(W, H);

  function onOrientation(e) {
    // beta: front-back tilt (−180..180, ~0 = flat, ~90 = upright)
    // gamma: left-right tilt (−90..90)
    if (e.beta  != null) rawBeta  = e.beta;
    if (e.gamma != null) rawGamma = e.gamma;
  }

  function seedFluid() {
    // Pack particles in a rough grid across the bottom half so the body starts
    // settled and full.
    const cols = 20, x0 = 2.5, y0 = 13.0, sx = 1.0, sy = 1.0;
    for (let i = 0; i < NP; i++) {
      const cx = i % cols, cy = (i / cols) | 0;
      pX[i] = x0 + cx * sx + (Math.random() - 0.5) * 0.3;
      pY[i] = y0 + cy * sy + (Math.random() - 0.5) * 0.3;
      vX[i] = 0; vY[i] = 0;
    }
    fluidSeeded = true;
  }

  function startGravBall() {
    gravActive = true;
    if (!fluidSeeded) seedFluid();
    document.getElementById("grav-status").textContent = "Fluid: ON";
    window.wallSend?.({ type: "draw_mode", active: true });
    gravRaf = requestAnimationFrame(gravTick);
  }

  function stopGravBall() {
    gravActive = false;
    densS.fill(0);
    outBuf.fill(0);
    previewCtx.clearRect(0, 0, W, H);
    const toggle = document.getElementById("grav-toggle");
    if (toggle) toggle.checked = false;
    document.getElementById("grav-status").textContent = "Fluid: off";
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
    <label>Fluid</label>
    <label class="toggle">
      <input type="checkbox" id="grav-toggle">
      <div class="toggle-track"></div>
    </label>`;
  document.getElementById("grav-status").after(gravRow);
  document.getElementById("grav-toggle").addEventListener("change", e => {
    e.target.checked ? startGravBall() : stopGravBall();
  });

  // ── Fluid rendering (metaball density field) ──────────────────────────

  // RGB frame streamed to the wall; also drawn into the preview canvas so the
  // phone shows exactly what the wall shows.
  const outBuf = new Uint8Array(1875);
  // Density field per cell + a temporally-smoothed copy (reduces flicker so it
  // reads as a connected liquid mass instead of scattered dots).
  const dens  = new Float32Array(W * H);
  const densS = new Float32Array(W * H);
  // Accumulated particle speed per cell → foam highlights on fast surfaces.
  const spd   = new Float32Array(W * H);

  function buildDensity() {
    dens.fill(0);
    spd.fill(0);
    for (let i = 0; i < NP; i++) {
      const x = pX[i], y = pY[i];
      const s = Math.hypot(vX[i], vY[i]);
      const x0 = Math.floor(x), y0 = Math.floor(y);
      for (let dy = -2; dy <= 2; dy++) {
        const gy = y0 + dy; if (gy < 0 || gy >= H) continue;
        for (let dx = -2; dx <= 2; dx++) {
          const gx = x0 + dx; if (gx < 0 || gx >= W) continue;
          const ddx = gx - x, ddy = gy - y;
          const w = Math.exp(-(ddx * ddx + ddy * ddy) * 0.7);
          const idx = gy * W + gx;
          dens[idx] += w;
          spd[idx]  += w * s;
        }
      }
    }
  }

  function renderFluid() {
    buildDensity();
    for (let p = 0; p < W * H; p++) {
      densS[p] += (dens[p] - densS[p]) * 0.55;   // ease toward new sample
      const d = densS[p];
      let r = 0, g = 0, b = 0;
      if (d > 0.5) {
        const t = Math.min((d - 0.5) / 2.5, 1);   // 0 = thin surface, 1 = deep body
        r = (8 + 14 * t) | 0;
        g = (195 - 85 * t) | 0;                    // surface cyan → deep blue
        b = 255;
        // Foam: fast-moving thin surface (crests/splashes) picks up white.
        const sp = dens[p] > 0 ? spd[p] / dens[p] : 0;
        if (t < 0.5 && sp > 0.45) {
          const f = Math.min((sp - 0.45) * 1.4, 1) * (1 - t * 2);
          r = Math.min(255, r + ((210 * f) | 0));
          g = Math.min(255, g + ((130 * f) | 0));
        }
      }
      const o = p * 3;
      outBuf[o] = r; outBuf[o + 1] = g; outBuf[o + 2] = b;
      const q = p * 4;
      previewImg.data[q] = r; previewImg.data[q + 1] = g;
      previewImg.data[q + 2] = b; previewImg.data[q + 3] = 255;
    }
    previewCtx.putImageData(previewImg, 0, 0);
    window.wallSendBinary?.(outBuf.buffer);
  }

  function cellOf(i) {
    let cx = (pX[i] / H_RAD) | 0; if (cx < 0) cx = 0; else if (cx >= GW) cx = GW - 1;
    let cy = (pY[i] / H_RAD) | 0; if (cy < 0) cy = 0; else if (cy >= GH) cy = GH - 1;
    return cy * GW + cx;
  }

  function buildGrid() {
    cellCount.fill(0);
    for (let i = 0; i < NP; i++) cellCount[cellOf(i)]++;
    let acc = 0;
    for (let c = 0; c < NC; c++) { cellStart[c] = acc; cellCursor[c] = acc; acc += cellCount[c]; }
    cellStart[NC] = acc;
    for (let i = 0; i < NP; i++) { const c = cellOf(i); cellItems[cellCursor[c]++] = i; }
  }

  // Clavet viscosity: neighbour pairs exchange radial-velocity impulses → the
  // body moves as a cohesive sheet rather than independent dots.
  function applyViscosity() {
    for (let i = 0; i < NP; i++) {
      const xi = pX[i], yi = pY[i];
      let cx = (xi / H_RAD) | 0; if (cx < 0) cx = 0; else if (cx >= GW) cx = GW - 1;
      let cy = (yi / H_RAD) | 0; if (cy < 0) cy = 0; else if (cy >= GH) cy = GH - 1;
      for (let ny = cy - 1; ny <= cy + 1; ny++) {
        if (ny < 0 || ny >= GH) continue;
        for (let nx = cx - 1; nx <= cx + 1; nx++) {
          if (nx < 0 || nx >= GW) continue;
          const c = ny * GW + nx;
          for (let e = cellStart[c]; e < cellStart[c + 1]; e++) {
            const j = cellItems[e];
            if (j <= i) continue;                 // visit each pair once
            const dx = pX[j] - xi, dy = pY[j] - yi;
            const r2 = dx * dx + dy * dy;
            if (r2 >= H2 || r2 === 0) continue;
            const r = Math.sqrt(r2);
            const ux = dx / r, uy = dy / r;
            const u = (vX[i] - vX[j]) * ux + (vY[i] - vY[j]) * uy;
            if (u > 0) {
              const q = 1 - r / H_RAD;
              const I = q * (SIGMA * u + BETA * u * u) * 0.5;
              const Ix = I * ux, Iy = I * uy;
              vX[i] -= Ix; vY[i] -= Iy;
              vX[j] += Ix; vY[j] += Iy;
            }
          }
        }
      }
    }
  }

  // Clavet double-density relaxation: per particle compute density + near-density
  // over neighbours, then displace neighbours/self → incompressibility + surface
  // tension. Runs in-place on predicted positions.
  function doubleDensityRelaxation() {
    for (let i = 0; i < NP; i++) {
      const xi = pX[i], yi = pY[i];
      let cx = (xi / H_RAD) | 0; if (cx < 0) cx = 0; else if (cx >= GW) cx = GW - 1;
      let cy = (yi / H_RAD) | 0; if (cy < 0) cy = 0; else if (cy >= GH) cy = GH - 1;
      let rho = 0, rhoNear = 0;
      for (let ny = cy - 1; ny <= cy + 1; ny++) {
        if (ny < 0 || ny >= GH) continue;
        for (let nx = cx - 1; nx <= cx + 1; nx++) {
          if (nx < 0 || nx >= GW) continue;
          const c = ny * GW + nx;
          for (let e = cellStart[c]; e < cellStart[c + 1]; e++) {
            const j = cellItems[e];
            if (j === i) continue;
            const dx = pX[j] - xi, dy = pY[j] - yi;
            const r2 = dx * dx + dy * dy;
            if (r2 >= H2 || r2 === 0) continue;
            const q = 1 - Math.sqrt(r2) / H_RAD;
            rho += q * q;
            rhoNear += q * q * q;
          }
        }
      }
      const P  = K_STIFF * (rho - RHO0);
      const Pn = K_NEAR * rhoNear;
      let ddx = 0, ddy = 0;
      for (let ny = cy - 1; ny <= cy + 1; ny++) {
        if (ny < 0 || ny >= GH) continue;
        for (let nx = cx - 1; nx <= cx + 1; nx++) {
          if (nx < 0 || nx >= GW) continue;
          const c = ny * GW + nx;
          for (let e = cellStart[c]; e < cellStart[c + 1]; e++) {
            const j = cellItems[e];
            if (j === i) continue;
            const dx = pX[j] - xi, dy = pY[j] - yi;
            const r2 = dx * dx + dy * dy;
            if (r2 >= H2 || r2 === 0) continue;
            const r = Math.sqrt(r2);
            const q = 1 - r / H_RAD;
            const mag = (P * q + Pn * q * q) * 0.5;
            const Dx = (dx / r) * mag, Dy = (dy / r) * mag;
            pX[j] += Dx; pY[j] += Dy;
            ddx -= Dx; ddy -= Dy;
          }
        }
      }
      pX[i] += ddx; pY[i] += ddy;
    }
  }

  function stepFluid() {
    // 1. Tank + flippable gravity direction from tilt, heavily smoothed so it
    //    steers gently rather than driving the whole motion.
    const br = rawBeta * Math.PI / 180, gr = rawGamma * Math.PI / 180;
    let tx = Math.sin(gr);     // left/right tilt → horizontal flow (flip sign to invert L/R)
    let ty = Math.sin(br);     // upright (β≈90) → +y down; leaning past vertical flips it
    const m = Math.hypot(tx, ty);
    if (m > 0.12) { tx /= m; ty /= m; }
    else          { tx = gDirX; ty = gDirY; }   // near-flat: keep current down-edge
    gDirX += (tx - gDirX) * 0.06;
    gDirY += (ty - gDirY) * 0.06;
    const gm = Math.hypot(gDirX, gDirY) || 1;
    gDirX /= gm; gDirY /= gm;

    // 2. Apply gravity + a tiny ambient sway (perpendicular to "down") so the
    //    surface ripples gently even when the phone is held level.
    ambientT += 0.06;
    const fgx = gDirX * GRAV, fgy = gDirY * GRAV;
    const perpX = -gDirY, perpY = gDirX;
    for (let i = 0; i < NP; i++) {
      const amb = 0.010 * Math.sin(ambientT + pX[i] * 0.45) + 0.006 * Math.sin(ambientT * 0.37);
      vX[i] += fgx + perpX * amb;
      vY[i] += fgy + perpY * amb;
    }

    // 3. Viscosity on the current grid.
    buildGrid();
    if (BETA > 0 || SIGMA > 0) applyViscosity();

    // 4. Predict positions.
    for (let i = 0; i < NP; i++) {
      prevX[i] = pX[i]; prevY[i] = pY[i];
      pX[i] += vX[i];   pY[i] += vY[i];
    }

    // 5. Incompressibility + surface tension on the predicted grid.
    buildGrid();
    doubleDensityRelaxation();

    // 6. Resolve walls and recover velocity from the actual displacement.
    for (let i = 0; i < NP; i++) {
      if (pX[i] < 0) pX[i] = 0; else if (pX[i] > 24) pX[i] = 24;
      if (pY[i] < 0) pY[i] = 0; else if (pY[i] > 24) pY[i] = 24;
      let nvx = pX[i] - prevX[i], nvy = pY[i] - prevY[i];
      const sp = Math.hypot(nvx, nvy);
      if (sp > VEL_CAP) { const k = VEL_CAP / sp; nvx *= k; nvy *= k; }
      vX[i] = nvx; vY[i] = nvy;
    }
  }

  function gravTick(ts) {
    if (!gravActive) return;
    gravRaf = requestAnimationFrame(gravTick);
    if (ts - lastGravFrame < 33) return;  // ~30 fps
    lastGravFrame = ts;
    stepFluid();
    renderFluid();
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
