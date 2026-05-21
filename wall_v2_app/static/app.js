// app.js — WebSocket connection, tab routing, controls tab

const WS_URL = `${location.protocol.replace('http', 'ws')}//${location.host}/ws`;
const RECONNECT_MS = 3000;

let ws = null;
let status = {};

// ── WebSocket ─────────────────────────────────────────────────────────

function connectWS() {
  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    // WebSocket constructor throws synchronously on mixed-content or invalid URL.
    // Set the dot red and schedule a retry so the app doesn't silently stop.
    console.error("WebSocket creation failed:", e);
    document.getElementById("conn-dot").style.background = "#e94560";
    setTimeout(connectWS, RECONNECT_MS);
    return;
  }
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    document.getElementById("conn-dot").style.background = "#4a90d9";
    // Always exit draw mode on (re)connect so a stale gravity ball / camera
    // session from a previous page load can't leave the display stuck.
    send({ type: "draw_mode", active: false });
    send({ type: "query" });
  };

  ws.onclose = () => {
    document.getElementById("conn-dot").style.background = "#e94560"; // red = no server
    document.getElementById("anim-label").textContent = "server offline";
    setTimeout(connectWS, RECONNECT_MS);
  };

  ws.onerror = () => ws.close();

  ws.onmessage = (e) => {
    if (typeof e.data === "string") {
      try { handleMsg(JSON.parse(e.data)); } catch {}
    }
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN)
    ws.send(JSON.stringify(obj));
}

// Send a raw ArrayBuffer (binary frame)
function sendBinary(buf) {
  if (ws && ws.readyState === WebSocket.OPEN)
    ws.send(buf);
}

function handleMsg(msg) {
  if (msg.type === "status") {
    status = msg;
    applyStatus(msg);
    // serial field is injected by the Python bridge
    setSerialDot(msg.serial !== false);
  } else if (msg.type === "serial_state") {
    setSerialDot(msg.connected);
    if (!msg.connected) {
      document.getElementById("anim-label").textContent = msg.msg || "not connected";
    }
  }
}

function setSerialDot(connected) {
  const dot = document.getElementById("conn-dot");
  // Green = serial OK, amber = WS OK but no serial, red = WS down
  dot.style.background = connected ? "#4caf50" : "#ff9800";
  dot.title = connected ? "Teensy connected" : "Teensy not connected";
}

function applyStatus(s) {
  // Status bar
  document.getElementById("anim-label").textContent = s.name || "—";

  // Animation grid
  document.querySelectorAll(".anim-card").forEach(el => {
    el.classList.toggle("selected", parseInt(el.dataset.idx) === s.anim);
  });

  // Sliders — only update if not actively dragging
  const bs = document.getElementById("bright-slider");
  const ss = document.getElementById("speed-slider");
  if (!bs._dragging && s.bright !== undefined) { bs.value = s.bright; document.getElementById("bright-val").textContent = s.bright; }
  if (!ss._dragging && s.speed !== undefined) { ss.value = s.speed;  document.getElementById("speed-val").textContent = s.speed; }

  // Cycle toggle
  const ct = document.getElementById("cycle-toggle");
  if (s.cycle !== undefined) ct.checked = s.cycle;

  // Build anim grid if we have the list
  if (s.anims && s.anims.length && !document.querySelector(".anim-card")) {
    const grid = document.getElementById("anim-grid");
    s.anims.forEach((name, i) => {
      const card = document.createElement("button");
      card.className = "anim-card" + (i === s.anim ? " selected" : "");
      card.dataset.idx = i;
      card.textContent = name;
      card.addEventListener("click", () => send({ type: "event", name: "SELECT_ANIM", value: i }));
      grid.appendChild(card);
    });
  }
}

// ── Tab routing ───────────────────────────────────────────────────────

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const prev = document.querySelector(".tab-btn.active")?.dataset.tab;
    const tab  = btn.dataset.tab;
    // Notify the tab we're leaving so it can clean up hardware/streams
    if (prev && prev !== tab) window[`${prev}TabDeactivated`]?.();
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("active", p.id === `panel-${tab}`));
    if (tab === "draw") window.drawTabActivated?.();
  });
});

// Stop active sensors/camera when the phone screen locks or browser tabs in the background
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    window.sensorsTabDeactivated?.();
    window.cameraTabDeactivated?.();
  }
});

// Last-resort cleanup on page close (best-effort — not guaranteed on mobile)
window.addEventListener("beforeunload", () => {
  send({ type: "draw_mode", active: false });
});

// ── Brightness slider ─────────────────────────────────────────────────

const brightSlider = document.getElementById("bright-slider");
const brightVal    = document.getElementById("bright-val");
brightSlider.addEventListener("input",  () => { brightVal.textContent = brightSlider.value; brightSlider._dragging = true; });
brightSlider.addEventListener("change", () => { brightSlider._dragging = false; send({ type: "event", name: "SET_BRIGHT", value: +brightSlider.value }); });

// ── Speed slider ──────────────────────────────────────────────────────

const speedSlider = document.getElementById("speed-slider");
const speedVal    = document.getElementById("speed-val");
speedSlider.addEventListener("input",  () => { speedVal.textContent = speedSlider.value; speedSlider._dragging = true; });
speedSlider.addEventListener("change", () => { speedSlider._dragging = false; send({ type: "event", name: "SET_SPEED", value: +speedSlider.value }); });

// ── Cycle toggle ──────────────────────────────────────────────────────

document.getElementById("cycle-toggle").addEventListener("change", () => send({ type: "event", name: "TOGGLE_CYCLE" }));

// ── Kick off ──────────────────────────────────────────────────────────
connectWS();

// Expose helpers for other modules
window.wallSend       = send;
window.wallSendBinary = sendBinary;
window.wallStatus     = () => status;
