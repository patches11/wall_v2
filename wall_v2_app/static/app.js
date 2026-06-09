// app.js — WebSocket connection, tab routing, controls tab

const WS_URL = `${location.protocol.replace('http', 'ws')}//${location.host}/ws`;
const RECONNECT_MS = 2000;
const HEARTBEAT_MS = 5000;
const STALE_MS     = 12000;

let ws = null;
let status = {};
let lastMsgAt = Date.now();
let hbTimer = null;
let pendingResume = false;

// ── WebSocket ─────────────────────────────────────────────────────────

function connectWS() {
  stopHeartbeat();
  // Tear down any prior socket first so this doubles as a force-reconnect.
  // (A phone-locked socket can read as OPEN while actually half-open.)
  if (ws) {
    try { ws.onclose = null; ws.onerror = null; ws.close(); } catch {}
    ws = null;
  }
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
    lastMsgAt = Date.now();
    pendingResume = true;
    // Ask for current state.  If the wall reports it's still in draw mode we
    // resume the drawing (see handleMsg) instead of wiping it.
    send({ type: "query" });
    startHeartbeat();
  };

  ws.onclose = () => {
    stopHeartbeat();
    document.getElementById("conn-dot").style.background = "#e94560"; // red = no server
    document.getElementById("anim-label").textContent = "server offline";
    setTimeout(connectWS, RECONNECT_MS);
  };

  ws.onerror = () => ws.close();

  ws.onmessage = (e) => {
    lastMsgAt = Date.now();
    if (typeof e.data === "string") {
      try { handleMsg(JSON.parse(e.data)); } catch {}
    }
  };
}

// App-level heartbeat: ping periodically and, if the server has gone silent
// for too long (typical of an iOS-suspended half-open socket), force a reconnect.
function startHeartbeat() {
  stopHeartbeat();
  hbTimer = setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    send({ type: "ping" });
    if (Date.now() - lastMsgAt > STALE_MS) {
      try { ws.close(); } catch {}  // onclose schedules the reconnect
    }
  }, HEARTBEAT_MS);
}
function stopHeartbeat() { if (hbTimer) { clearInterval(hbTimer); hbTimer = null; } }

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
  if (msg.type === "pong") {
    // Heartbeat reply — lastMsgAt already bumped in onmessage.
  } else if (msg.type === "status") {
    status = msg;
    applyStatus(msg);
    window.videoApplyStatus?.(msg);
    // serial field is injected by the Python bridge
    setSerialDot(msg.serial !== false);
    if (msg.saved !== undefined) window.drawUpdateSaved?.(msg);
    // On the first status after (re)connect, resume an in-progress drawing
    // by pulling the wall's current frame back instead of wiping it.
    if (pendingResume) {
      pendingResume = false;
      if (msg.draw) send({ type: "request_frame" });
      send({ type: "list_drawings" });
      send({ type: "list_videos" });
    }
  } else if (msg.type === "videos") {
    window.videoUpdateList?.(msg);
  } else if (msg.type === "upload_progress") {
    window.videoUploadProgress?.(msg);
  } else if (msg.type === "video_uploaded") {
    window.videoUploadAck?.(msg);
  } else if (msg.type === "frame_data") {
    window.drawLoadFrame?.(msg.frame);
  } else if (msg.type === "saved") {
    window.drawUpdateSaved?.(msg);
  } else if (msg.type === "serial_state") {
    setSerialDot(msg.connected);
    if (!msg.connected) {
      document.getElementById("anim-label").textContent = msg.msg || "not connected";
    } else {
      send({ type: "query" });  // wall just (re)appeared — refresh state
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

  // Cycle-sound toggle
  const cst = document.getElementById("cycle-sound-toggle");
  if (s.cyclesound !== undefined) cst.checked = s.cyclesound;

  // Build anim grid if we have the list. Sound-reactive anims (flagged in
  // s.sound, a 0/1 array parallel to s.anims) go in their own section.
  if (s.anims && s.anims.length && !document.querySelector(".anim-card")) {
    const grid      = document.getElementById("anim-grid");
    const soundGrid = document.getElementById("sound-grid");
    const soundFlags = s.sound || [];
    let anySound = false;
    s.anims.forEach((name, i) => {
      const card = document.createElement("button");
      card.className = "anim-card" + (i === s.anim ? " selected" : "");
      card.dataset.idx = i;
      card.textContent = name;
      card.addEventListener("click", () => send({ type: "event", name: "SELECT_ANIM", value: i }));
      if (soundFlags[i]) { soundGrid.appendChild(card); anySound = true; }
      else               { grid.appendChild(card); }
    });
    if (anySound) document.getElementById("sound-section").style.display = "";
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
    if (tab === "videos") window.videosTabActivated?.();
  });
});

// Stop active sensors/camera when the phone screen locks or browser tabs in the background
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    window.sensorsTabDeactivated?.();
    window.cameraTabDeactivated?.();
  } else {
    // A backgrounded socket often reads as OPEN while actually half-open, so
    // force a fresh connection rather than trusting readyState.
    connectWS();
  }
});

// iOS BFCache restore: page shown from the back-forward cache without a reload
window.addEventListener("pageshow", e => {
  if (e.persisted) connectWS();
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
document.getElementById("cycle-sound-toggle").addEventListener("change", () => send({ type: "cycle_sound" }));

// ── Kick off ──────────────────────────────────────────────────────────
connectWS();

// Expose helpers for other modules
window.wallSend       = send;
window.wallSendBinary = sendBinary;
window.wallStatus     = () => status;
window.wallBufferedAmount = () => (ws ? ws.bufferedAmount : 0);
