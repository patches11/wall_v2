// draw.js — 25×25 touch/mouse canvas for drawing on the display

(function () {
  const W = 25, H = 25;
  const CELL = 14;        // on-screen pixels per LED cell
  const canvas = document.getElementById("draw-canvas");
  const ctx    = canvas.getContext("2d");

  // Scale up the 25×25 canvas to a touch-friendly size
  canvas.style.width  = `${W * CELL}px`;
  canvas.style.height = `${H * CELL}px`;

  // Internal pixel buffer: flat Uint8Array of [r,g,b] × 625
  const pixels = new Uint8ClampedArray(W * H * 3);
  const imgData = new ImageData(W, H);

  let tool      = "paint";
  let drawColor = hexToRgb("#ff2255");
  let isDown    = false;
  let livePush  = false;
  let pendingPush = false;

  // ── Helpers ──────────────────────────────────────────────────────────

  function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
  }

  function setPixel(x, y, r, g, b) {
    if (x < 0 || x >= W || y < 0 || y >= H) return;
    const i = (y * W + x) * 3;
    pixels[i] = r; pixels[i+1] = g; pixels[i+2] = b;
  }

  function getPixel(x, y) {
    const i = (y * W + x) * 3;
    return [pixels[i], pixels[i+1], pixels[i+2]];
  }

  function render() {
    // Copy pixels → ImageData → canvas (1:1)
    for (let i = 0; i < W * H; i++) {
      imgData.data[i * 4]     = pixels[i * 3];
      imgData.data[i * 4 + 1] = pixels[i * 3 + 1];
      imgData.data[i * 4 + 2] = pixels[i * 3 + 2];
      imgData.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(imgData, 0, 0);
    // Draw faint grid
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 0.5 / CELL;
    ctx.save(); ctx.scale(1, 1);
    // (Grid drawn at 1:1 then CSS scale handles the rest)
    ctx.restore();
  }

  function canvasXY(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    const x = Math.floor((clientX - rect.left) / CELL);
    const y = Math.floor((clientY - rect.top)  / CELL);
    return [x, y];
  }

  function paint(x, y) {
    if (tool === "paint") setPixel(x, y, ...drawColor);
    else if (tool === "erase") setPixel(x, y, 0, 0, 0);
    render();
    if (livePush) schedulePush();
  }

  function floodFill(sx, sy, [tr, tg, tb]) {
    const target = getPixel(sx, sy);
    if (target[0] === tr && target[1] === tg && target[2] === tb) return;
    const stack = [[sx, sy]];
    while (stack.length) {
      const [x, y] = stack.pop();
      if (x < 0 || x >= W || y < 0 || y >= H) continue;
      const p = getPixel(x, y);
      if (p[0] !== target[0] || p[1] !== target[1] || p[2] !== target[2]) continue;
      setPixel(x, y, tr, tg, tb);
      stack.push([x+1,y],[x-1,y],[x,y+1],[x,y-1]);
    }
    render();
    if (livePush) schedulePush();
  }

  function pushFrame() {
    // Pack pixels into a 1875-byte ArrayBuffer and send as binary WS
    const buf = new ArrayBuffer(1875);
    const arr = new Uint8Array(buf);
    arr.set(pixels);
    window.wallSendBinary?.(buf);
  }

  function schedulePush() {
    if (pendingPush) return;
    pendingPush = true;
    requestAnimationFrame(() => { pushFrame(); pendingPush = false; });
  }

  // ── Events ────────────────────────────────────────────────────────────

  canvas.addEventListener("mousedown", e => {
    isDown = true;
    const [x, y] = canvasXY(e.clientX, e.clientY);
    if (tool === "fill") { floodFill(x, y, drawColor); return; }
    paint(x, y);
  });
  canvas.addEventListener("mousemove", e => {
    if (!isDown) return;
    paint(...canvasXY(e.clientX, e.clientY));
  });
  window.addEventListener("mouseup", () => isDown = false);

  canvas.addEventListener("touchstart", e => {
    e.preventDefault(); isDown = true;
    const t = e.touches[0];
    const [x, y] = canvasXY(t.clientX, t.clientY);
    if (tool === "fill") { floodFill(x, y, drawColor); return; }
    paint(x, y);
  }, { passive: false });
  canvas.addEventListener("touchmove", e => {
    e.preventDefault(); if (!isDown) return;
    const t = e.touches[0];
    paint(...canvasXY(t.clientX, t.clientY));
  }, { passive: false });
  canvas.addEventListener("touchend", () => isDown = false);

  // ── Toolbar ───────────────────────────────────────────────────────────

  function setTool(t) {
    tool = t;
    document.querySelectorAll(".tool-btn[id^='tool-']").forEach(b =>
      b.classList.toggle("active", b.id === `tool-${t}`)
    );
  }

  document.getElementById("tool-paint").addEventListener("click", () => setTool("paint"));
  document.getElementById("tool-erase").addEventListener("click", () => setTool("erase"));
  document.getElementById("tool-fill").addEventListener("click", () => setTool("fill"));
  document.getElementById("tool-clear").addEventListener("click", () => {
    pixels.fill(0); render();
    window.wallSend?.({ type: "draw_clear" });
  });

  const colorPicker = document.getElementById("color-picker");
  const colorSwatch = document.getElementById("color-swatch");

  function syncSwatch() {
    colorSwatch.style.background = colorPicker.value;
    drawColor = hexToRgb(colorPicker.value);
  }
  colorPicker.addEventListener("input", syncSwatch);
  colorSwatch.addEventListener("click", () => colorPicker.click());
  syncSwatch();

  document.getElementById("draw-live").addEventListener("change", e => { livePush = e.target.checked; });
  document.getElementById("draw-push-btn").addEventListener("click", () => {
    window.wallSend?.({ type: "draw_mode", active: true });
    pushFrame();
  });

  // Enter draw mode when tab is activated (no-op if already in draw mode)
  window.drawTabActivated = () => window.wallSend?.({ type: "draw_mode", active: true });

  // Initial render
  render();
})();
