from __future__ import annotations

import streamlit as st


HTML = r"""
<div class="ku-wrap">
  <input id="ku-file" type="file"
    accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.mp4,.mov,.mkv,.webm,.avi,.m4v"
    hidden />
  <div id="ku-drop" class="ku-drop" tabindex="0">
    <div class="ku-icon">🎵</div>
    <div class="ku-title">Drop audio or video here</div>
    <div class="ku-sub">or click to choose a file</div>
    <div class="ku-types">MP3, WAV, M4A, MP4, MOV, MKV, WEBM and more</div>
  </div>
  <div id="ku-filecard" class="ku-filecard ku-hidden">
    <div class="ku-file-main">
      <div class="ku-file-icon">🎬</div>
      <div class="ku-file-text">
        <div id="ku-name" class="ku-name"></div>
        <div id="ku-meta" class="ku-meta"></div>
      </div>
      <button id="ku-remove" class="ku-remove" type="button" title="Remove">×</button>
    </div>
    <div id="ku-progress-wrap" class="ku-progress-wrap">
      <div class="ku-progress-bg"><div id="ku-progress" class="ku-progress"></div></div>
      <div class="ku-progress-row">
        <span id="ku-status">Ready</span>
        <span id="ku-percent">0%</span>
      </div>
    </div>
  </div>
</div>
"""

CSS = r"""
.ku-wrap {
  width: 100%;
  font-family: var(--st-font, sans-serif);
  color: var(--st-text-color);
}
.ku-drop {
  border: 1.5px dashed color-mix(in srgb, var(--st-primary-color) 65%, transparent);
  border-radius: 20px;
  min-height: 168px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: .32rem;
  cursor: pointer;
  background: color-mix(in srgb, var(--st-primary-color) 5%, transparent);
  transition: .18s ease;
  outline: none;
}
.ku-drop:hover, .ku-drop.ku-drag {
  border-color: var(--st-primary-color);
  background: color-mix(in srgb, var(--st-primary-color) 10%, transparent);
  transform: translateY(-1px);
}
.ku-icon { font-size: 2rem; }
.ku-title { font-weight: 750; font-size: 1.05rem; }
.ku-sub { opacity: .72; font-size: .93rem; }
.ku-types { opacity: .48; font-size: .78rem; margin-top: .35rem; }
.ku-filecard {
  border: 1px solid color-mix(in srgb, var(--st-primary-color) 40%, transparent);
  border-radius: 18px;
  padding: 14px 15px;
  background: color-mix(in srgb, var(--st-primary-color) 5%, transparent);
}
.ku-file-main {
  display: flex;
  align-items: center;
  gap: 12px;
}
.ku-file-icon {
  width: 42px; height: 42px;
  display: grid; place-items: center;
  border-radius: 12px;
  background: color-mix(in srgb, var(--st-primary-color) 18%, transparent);
}
.ku-file-text { min-width: 0; flex: 1; }
.ku-name {
  font-weight: 720;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ku-meta { opacity: .62; font-size: .84rem; margin-top: 2px; }
.ku-remove {
  width: 34px; height: 34px; border: 0; border-radius: 10px;
  background: transparent; color: inherit; font-size: 1.4rem; cursor: pointer;
}
.ku-remove:hover { background: rgba(255,255,255,.08); }
.ku-progress-wrap { margin-top: 12px; }
.ku-progress-bg {
  height: 8px; border-radius: 999px; overflow: hidden;
  background: rgba(128,128,128,.22);
}
.ku-progress {
  width: 0%; height: 100%; border-radius: 999px;
  background: var(--st-primary-color);
  transition: width .18s ease;
}
.ku-progress-row {
  display: flex; justify-content: space-between;
  margin-top: 7px; font-size: .82rem; opacity: .75;
}
.ku-hidden { display: none !important; }
.ku-error { color: #ff6b6b; opacity: 1; }
.ku-success { color: #58d68d; opacity: 1; }
"""

JS = r"""
export default function(component) {
  const { parentElement, data, setStateValue } = component;

  const wrap = parentElement.querySelector(".ku-wrap");
  if (!wrap) {
    throw new Error("Karaoke uploader root element was not mounted.");
  }

  // parentElement is a ShadowRoot by default in Streamlit Components V2.
  // ShadowRoot has no `dataset`, so keep the initialization marker on
  // the component's actual HTMLElement instead.
  if (wrap.dataset.kuInitialized === "1") {
    return;
  }
  wrap.dataset.kuInitialized = "1";

  const drop = parentElement.querySelector("#ku-drop");
  const input = parentElement.querySelector("#ku-file");
  const card = parentElement.querySelector("#ku-filecard");
  const nameEl = parentElement.querySelector("#ku-name");
  const metaEl = parentElement.querySelector("#ku-meta");
  const removeBtn = parentElement.querySelector("#ku-remove");
  const progressEl = parentElement.querySelector("#ku-progress");
  const percentEl = parentElement.querySelector("#ku-percent");
  const statusEl = parentElement.querySelector("#ku-status");

  const chunkSize = data?.chunk_size ?? (4 * 1024 * 1024);
  let activeUploadId = data?.existing?.upload_id ?? null;
  let cancelled = false;

  function humanBytes(bytes) {
    if (!Number.isFinite(bytes)) return "";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes, idx = 0;
    while (value >= 1024 && idx < units.length - 1) {
      value /= 1024; idx += 1;
    }
    return `${value.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
  }

  function setProgress(done, total, text) {
    const pct = total > 0 ? Math.max(0, Math.min(100, Math.round(done / total * 100))) : 0;
    progressEl.style.width = `${pct}%`;
    percentEl.textContent = `${pct}%`;
    statusEl.textContent = text;
    statusEl.classList.remove("ku-error", "ku-success");
  }

  function showReady(info) {
    drop.classList.add("ku-hidden");
    card.classList.remove("ku-hidden");
    nameEl.textContent = info.filename || "Uploaded file";
    metaEl.textContent = `${humanBytes(info.size || 0)} · ready`;
    progressEl.style.width = "100%";
    percentEl.textContent = "100%";
    statusEl.textContent = "Upload complete";
    statusEl.classList.remove("ku-error");
    statusEl.classList.add("ku-success");
    activeUploadId = info.upload_id || null;
  }

  function resetUI() {
    cancelled = true;
    input.value = "";
    activeUploadId = null;
    card.classList.add("ku-hidden");
    drop.classList.remove("ku-hidden");
    progressEl.style.width = "0%";
    percentEl.textContent = "0%";
    statusEl.textContent = "Ready";
    statusEl.classList.remove("ku-error", "ku-success");
    setStateValue("uploaded", null);
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) {
      throw new Error(payload.error || `Upload failed (${response.status})`);
    }
    return payload;
  }

  async function sendChunk(uploadId, index, blob) {
    const attempts = 4;
    let lastError = null;
    for (let attempt = 0; attempt < attempts; attempt++) {
      if (cancelled) throw new Error("Upload cancelled");
      try {
        return await api(`/api/upload/chunk/${uploadId}/${index}`, {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: blob,
        });
      } catch (error) {
        lastError = error;
        if (attempt < attempts - 1) {
          await new Promise(resolve => setTimeout(resolve, 500 * Math.pow(2, attempt)));
        }
      }
    }
    throw lastError || new Error("Chunk upload failed");
  }

  async function uploadFile(file) {
    cancelled = false;
    card.classList.remove("ku-hidden");
    drop.classList.add("ku-hidden");
    nameEl.textContent = file.name;
    metaEl.textContent = humanBytes(file.size);
    setProgress(0, file.size, "Preparing upload…");

    const totalChunks = Math.ceil(file.size / chunkSize);
    const fingerprint = `${file.name}:${file.size}:${file.lastModified}`;

    try {
      const init = await api("/api/upload/init", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: file.name,
          size: file.size,
          chunk_size: chunkSize,
          total_chunks: totalChunks,
          fingerprint,
        }),
      });

      activeUploadId = init.upload_id;

      if (init.completed) {
        showReady(init);
        setStateValue("uploaded", {
          upload_id: init.upload_id,
          filename: init.filename,
          size: init.size,
        });
        return;
      }

      const received = new Set(init.received || []);
      let uploadedBytes = 0;

      for (let i = 0; i < totalChunks; i++) {
        const start = i * chunkSize;
        const end = Math.min(file.size, start + chunkSize);
        if (received.has(i)) {
          uploadedBytes += (end - start);
        }
      }
      setProgress(uploadedBytes, file.size, received.size ? "Resuming upload…" : "Uploading…");

      for (let i = 0; i < totalChunks; i++) {
        if (cancelled) throw new Error("Upload cancelled");
        const start = i * chunkSize;
        const end = Math.min(file.size, start + chunkSize);
        if (!received.has(i)) {
          await sendChunk(init.upload_id, i, file.slice(start, end));
          uploadedBytes += (end - start);
        }
        setProgress(uploadedBytes, file.size, "Uploading…");
      }

      setProgress(file.size, file.size, "Finalizing…");
      const completed = await api(`/api/upload/complete/${init.upload_id}`, {
        method: "POST",
      });

      showReady(completed);
      setStateValue("uploaded", {
        upload_id: completed.upload_id,
        filename: completed.filename,
        size: completed.size,
      });
    } catch (error) {
      statusEl.textContent = error?.message || "Upload failed";
      statusEl.classList.add("ku-error");
      percentEl.textContent = "Retry";
      throw error;
    }
  }

  drop.onclick = () => input.click();
  drop.onkeydown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.click();
    }
  };

  drop.ondragover = (event) => {
    event.preventDefault();
    drop.classList.add("ku-drag");
  };
  drop.ondragleave = () => drop.classList.remove("ku-drag");
  drop.ondrop = (event) => {
    event.preventDefault();
    drop.classList.remove("ku-drag");
    const file = event.dataTransfer?.files?.[0];
    if (file) uploadFile(file);
  };

  input.onchange = () => {
    const file = input.files?.[0];
    if (file) uploadFile(file);
  };

  removeBtn.onclick = async () => {
    cancelled = true;
    if (activeUploadId) {
      try {
        await api(`/api/upload/${activeUploadId}`, { method: "DELETE" });
      } catch (_) {}
    }
    resetUI();
  };

  if (data?.existing?.upload_id) {
    showReady(data.existing);
  }
}
"""

_uploader_component = st.components.v2.component(
    "karaoke_chunk_uploader",
    html=HTML,
    css=CSS,
    js=JS,
)


def chunk_uploader(*, key: str, chunk_size: int = 4 * 1024 * 1024):
    current = st.session_state.get(key, {})
    existing = current.get("uploaded") if isinstance(current, dict) else None

    result = _uploader_component(
        data={
            "chunk_size": int(chunk_size),
            "existing": existing,
        },
        default={"uploaded": existing},
        key=key,
        on_uploaded_change=lambda: None,
    )

    return result.uploaded
