// camera.js — live camera mirror (25×25 pixelated) and palette capture

(function () {
  const W = 25, H = 25;
  const video      = document.getElementById("cam-video");
  const overlay    = document.getElementById("cam-overlay");
  const ovCtx      = overlay.getContext("2d");
  const startBtn   = document.getElementById("cam-start-btn");
  const mirrorBtn  = document.getElementById("cam-mirror-btn");
  const paletteBtn = document.getElementById("cam-palette-btn");
  const fpsLabel   = document.getElementById("cam-fps");

  // Off-screen 25×25 canvas for pixel extraction
  const small  = document.createElement("canvas");
  small.width  = W; small.height = H;
  const sCtx   = small.getContext("2d", { willReadFrequently: true });

  let streaming  = false;
  let mirroring  = false;
  let rafId      = null;
  let lastFrame  = 0;
  let frameCount = 0;
  let fps        = 0;
  const TARGET_FPS = 15;
  const FRAME_MS   = 1000 / TARGET_FPS;

  // ── Camera start ─────────────────────────────────────────────────────

  startBtn.addEventListener("click", async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment", width: { ideal: 640 }, height: { ideal: 480 } }
      });
      video.srcObject = stream;
      await video.play();
      streaming = true;
      startBtn.textContent = "Stop camera";
      mirrorBtn.disabled = false;
      paletteBtn.disabled = false;

      // Overlay sizing is handled entirely by CSS (width/height 100% of wrapper).
    } catch (err) {
      startBtn.textContent = "Camera denied";
      console.error(err);
    }
  });

  // ── Mirror loop ───────────────────────────────────────────────────────

  mirrorBtn.addEventListener("click", () => {
    if (!streaming) return;
    mirroring = !mirroring;
    mirrorBtn.classList.toggle("active", mirroring);
    mirrorBtn.textContent = mirroring ? "Stop mirror" : "Mirror to display";

    if (mirroring) {
      overlay.style.display = "block";
      window.wallSend?.({ type: "draw_mode", active: true });
      rafId = requestAnimationFrame(mirrorTick);
    } else {
      stopMirror();
    }
  });

  function stopMirror() {
    mirroring = false;
    overlay.style.display = "none";
    mirrorBtn.classList.remove("active");
    mirrorBtn.textContent = "Mirror to display";
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    window.wallSend?.({ type: "draw_mode", active: false });
    fpsLabel.textContent = "";
  }

  // Called by app.js when navigating away from Camera tab or page hidden
  window.cameraTabDeactivated = () => { if (mirroring) stopMirror(); };

  function mirrorTick(ts) {
    if (!mirroring) return;
    rafId = requestAnimationFrame(mirrorTick);
    if (ts - lastFrame < FRAME_MS) return;

    // Compute FPS roughly every 30 frames
    frameCount++;
    if (frameCount % 30 === 0) {
      fps = Math.round(30000 / (ts - lastFrame + FRAME_MS * 29));
      fpsLabel.textContent = `${fps} fps`;
    }
    lastFrame = ts;

    // Draw current video frame squeezed to 25×25
    sCtx.drawImage(video, 0, 0, W, H);
    const px = sCtx.getImageData(0, 0, W, H).data;  // RGBA

    // Pack to 1875-byte RGB buffer
    const buf = new ArrayBuffer(1875);
    const arr = new Uint8Array(buf);
    for (let i = 0; i < W * H; i++) {
      arr[i * 3]     = px[i * 4];
      arr[i * 3 + 1] = px[i * 4 + 1];
      arr[i * 3 + 2] = px[i * 4 + 2];
    }
    window.wallSendBinary?.(buf);

    // Draw overlay: scale 25×25 back up so user can see the pixelation
    ovCtx.clearRect(0, 0, W, H);
    ovCtx.putImageData(sCtx.getImageData(0, 0, W, H), 0, 0);
  }

  // ── Palette capture ───────────────────────────────────────────────────
  // Sample 6 evenly-distributed pixels from the current 25×25 frame.

  paletteBtn.addEventListener("click", () => {
    if (!streaming) return;
    sCtx.drawImage(video, 0, 0, W, H);
    const px = sCtx.getImageData(0, 0, W, H).data;

    // Sample at 6 positions spread across the grid
    const positions = [
      [4, 4], [20, 4], [12, 12], [4, 20], [20, 20], [12, 4]
    ];
    const colors = positions.map(([x, y]) => {
      const i = (y * W + x) * 4;
      return [px[i], px[i+1], px[i+2]];
    });

    window.wallSend?.({ type: "camera_palette", colors });
    paletteBtn.textContent = "Palette sent!";
    setTimeout(() => paletteBtn.textContent = "Capture palette", 1500);
  });
})();
