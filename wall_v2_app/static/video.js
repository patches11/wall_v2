// video.js — Videos tab: upload .wv25 clips, manage the SD card, play on the wall

(function () {
  const fileInput    = document.getElementById("video-file");
  const uploadBtn    = document.getElementById("video-upload-btn");
  const progress     = document.getElementById("video-progress");
  const progressFill = document.getElementById("video-progress-fill");
  const uploadStatus = document.getElementById("video-upload-status");
  const listEl       = document.getElementById("video-list");
  const emptyEl      = document.getElementById("video-empty");
  const storageFill  = document.getElementById("video-storage-fill");
  const storageLabel = document.getElementById("video-storage-label");
  const modeToggle   = document.getElementById("video-mode-toggle");

  const CHUNK        = 2048;       // bytes per WS binary message
  const MAX_BUFFERED = 64 * 1024;  // keep the browser send buffer modest
  let uploading   = false;
  let playingName = null;

  function fmtBytes(n) {
    if (n == null) return "?";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  // Match the firmware's filename sanitizer so the name we display/play lines up.
  function sanitize(name) {
    return name.replace(/[^A-Za-z0-9._-]/g, "");
  }

  // ── Upload ─────────────────────────────────────────────────────────
  uploadBtn.addEventListener("click", () => { if (!uploading) fileInput.click(); });

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    fileInput.value = "";  // allow re-picking the same file later
    if (!file) return;
    const name = sanitize(file.name);
    if (!name.endsWith(".wv25")) {
      uploadStatus.textContent = "Pick a .wv25 file (make one with convert.py).";
      return;
    }
    const buf = new Uint8Array(await file.arrayBuffer());
    if (buf.length < 12 ||
        buf[0] !== 0x57 || buf[1] !== 0x56 || buf[2] !== 0x32 || buf[3] !== 0x35) {
      uploadStatus.textContent = "Not a valid .wv25 file (bad header).";
      return;
    }
    await uploadFile(name, buf);
  });

  function waitForDrain() {
    return new Promise((resolve) => {
      const check = () => {
        if ((window.wallBufferedAmount?.() || 0) < MAX_BUFFERED) resolve();
        else setTimeout(check, 15);
      };
      check();
    });
  }

  async function uploadFile(name, buf) {
    uploading = true;
    uploadBtn.disabled = true;
    progress.style.display = "block";
    progressFill.style.width = "0%";
    uploadStatus.textContent = `Uploading ${name} (${fmtBytes(buf.length)})…`;

    window.wallSend?.({ type: "upload_video_begin", name, size: buf.length });

    let sent = 0;
    while (sent < buf.length) {
      const end = Math.min(sent + CHUNK, buf.length);
      window.wallSendBinary?.(buf.slice(sent, end).buffer);
      sent = end;
      // The bar is driven by upload_progress from the server (bytes actually
      // committed to the serial link), not by how fast we hand bytes to the
      // socket — those two diverge and make the bar jump.
      await waitForDrain();
    }
    uploadStatus.textContent = "Writing to SD card on the wall…";
    // Completion is confirmed by the firmware's video_uploaded ack below.
  }

  // Server-driven progress: reflects bytes actually committed to the serial
  // link (the browser send buffer runs ahead of it), and keeps the WS alive.
  window.videoUploadProgress = (msg) => {
    if (!uploading || !msg.total) return;
    const pct = Math.min(100, Math.round((msg.sent / msg.total) * 100));
    progressFill.style.width = `${pct}%`;
    uploadStatus.textContent =
      `Uploading… ${fmtBytes(msg.sent)} of ${fmtBytes(msg.total)} (${pct}%)`;
  };

  window.videoUploadAck = (msg) => {
    uploading = false;
    uploadBtn.disabled = false;
    progress.style.display = "none";
    if (msg.ok) {
      uploadStatus.textContent = `Uploaded ${msg.name} ✓`;
    } else if (msg.size != null && msg.received != null) {
      uploadStatus.textContent = msg.received < msg.size
        ? `Upload stalled — got ${fmtBytes(msg.received)} of ${fmtBytes(msg.size)}. ` +
          `Retry (large clips can drop over USB).`
        : `Write failed — all ${fmtBytes(msg.size)} arrived but the SD write errored ` +
          `(card full or removed?).`;
    } else {
      uploadStatus.textContent = "Upload failed (old firmware — reflash to see details).";
    }
    // The firmware also auto-sends a fresh list, but ask again to be safe.
    window.wallSend?.({ type: "list_videos" });
  };

  // ── Clip list + storage ────────────────────────────────────────────
  window.videoUpdateList = (msg) => {
    const items = msg.items || [];
    listEl.innerHTML = "";
    emptyEl.style.display = items.length ? "none" : "block";

    items.forEach((it) => {
      const row = document.createElement("div");
      row.className = "video-row" + (it.name === playingName ? " playing" : "");

      const nm = document.createElement("div");
      nm.className = "vname";
      nm.textContent = it.name;

      const sz = document.createElement("div");
      sz.className = "vsize";
      sz.textContent = fmtBytes(it.size);

      const del = document.createElement("button");
      del.className = "video-del";
      del.textContent = "✕";
      del.title = "Delete clip";

      row.addEventListener("click", () => {
        playingName = it.name;
        window.wallSend?.({ type: "play_video", name: it.name });
        document.querySelectorAll(".video-row").forEach((r) => r.classList.toggle("playing", r === row));
        if (modeToggle) modeToggle.checked = true;  // playing a clip enters video mode
      });
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        if (it.name === playingName) playingName = null;
        window.wallSend?.({ type: "delete_video", name: it.name });
      });

      row.appendChild(nm);
      row.appendChild(sz);
      row.appendChild(del);
      listEl.appendChild(row);
    });

    if (msg.total) {
      const pct = Math.min(100, Math.round((msg.used / msg.total) * 100));
      storageFill.style.width = `${pct}%`;
      storageLabel.textContent = `${fmtBytes(msg.used)} used of ${fmtBytes(msg.total)} (${pct}%)`;
    } else {
      storageFill.style.width = "0%";
      storageLabel.textContent = msg.sd ? "SD card present" : "No SD card detected.";
    }
  };

  // ── Video-mode toggle (Controls tab) ───────────────────────────────
  modeToggle?.addEventListener("change", () => {
    if (!modeToggle.checked) playingName = null;
    window.wallSend?.({ type: "video_mode", active: modeToggle.checked });
  });

  // Reflect wall status: which mode we're in and which clip is playing.
  window.videoApplyStatus = (s) => {
    if (s.video !== undefined && modeToggle) modeToggle.checked = !!s.video;
    if (s.videoname !== undefined) {
      playingName = s.video ? (s.videoname || null) : null;
      document.querySelectorAll(".video-row").forEach((r) =>
        r.classList.toggle("playing", r.querySelector(".vname")?.textContent === playingName)
      );
    }
  };

  // Refresh the list whenever the Videos tab is opened.
  window.videosTabActivated = () => window.wallSend?.({ type: "list_videos" });
})();
