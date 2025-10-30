// static/script.js

const byId = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  const text = await res.text();
  try { return text ? JSON.parse(text) : {}; } catch { return {}; }
}

function renderStatus(st) {
  byId("coreStatus").textContent = st.core_active ? "active" : "inactive";
  byId("pipeStatus").textContent = st.pipe_active ? "active" : "inactive";
  byId("masterToggle").checked = !!st.both_active;
  if (!st.both_active) {
    byId("outputsSection").style.display = "none";
    byId("outputsWrap").innerHTML = "";
  }
}

function renderOutputs(data) {
  const outs = data.outputs || [];
  const wrap = byId("outputsWrap");
  wrap.innerHTML = "";
  if (!outs.length) {
    byId("outputsSection").style.display = "none";
    return;
  }
  byId("outputsSection").style.display = "block";

  outs.forEach((o) => {
    const row = document.createElement("div");
    row.className = "output-row";

    const name = document.createElement("div");
    name.className = "output-name";
    name.textContent = o.name || `Output ${o.id}`;

    // Default flag (no separate default-volume input)
    const defWrap = document.createElement("label");
    defWrap.className = "default-wrap";
    const defChk = document.createElement("input");
    defChk.type = "checkbox";
    defChk.checked = !!o.default;
    const defSpan = document.createElement("span");
    defSpan.textContent = "Default";
    defWrap.appendChild(defChk);
    defWrap.appendChild(defSpan);

    // Live enable/disable switch
    const swWrap = document.createElement("label");
    swWrap.className = "switch";
    const sw = document.createElement("input");
    sw.type = "checkbox";
    sw.checked = !!o.selected;
    const slider = document.createElement("span");
    slider.className = "slider";
    swWrap.appendChild(sw);
    swWrap.appendChild(slider);

    // Live volume slider
    const vol = document.createElement("input");
    vol.type = "range";
    vol.min = "0";
    vol.max = "100";
    vol.value = parseInt(o.volume ?? 0, 10);
    vol.className = "vol";

    // Handlers

    // When marking as default, persist the *current* slider value as default_volume
    defChk.addEventListener("change", async (e) => {
      const makeDefault = !!e.target.checked;
      const body = makeDefault
        ? { default: true, default_volume: Math.max(0, Math.min(100, parseInt(vol.value, 10))) }
        : { default: false };
      try {
        await api(`/api/outputs/${o.id}`, { method: "PUT", body: JSON.stringify(body) });
      } catch (err) {
        e.target.checked = !e.target.checked; // revert on error
        alert("Failed to update default: " + err);
      }
    });

    // Toggle output selection
    sw.addEventListener("change", async (e) => {
      try {
        await api(`/api/outputs/${o.id}`, {
          method: "PUT",
          body: JSON.stringify({ selected: !!e.target.checked })
        });
      } catch (err) {
        e.target.checked = !e.target.checked;
        alert("Failed to toggle output: " + err);
      }
    });

    // Change live volume; if this output is a default, also update its stored default_volume
    vol.addEventListener("change", async (e) => {
      const newVol = Math.max(0, Math.min(100, parseInt(e.target.value, 10)));
      try {
        const body = defChk.checked
          ? { volume: newVol, default_volume: newVol }
          : { volume: newVol };
        await api(`/api/outputs/${o.id}`, {
          method: "PUT",
          body: JSON.stringify(body)
        });
      } catch (err) {
        alert("Failed to set volume: " + err);
      }
    });

    row.appendChild(name);
    row.appendChild(defWrap);
    row.appendChild(swWrap);
    row.appendChild(vol);
    wrap.appendChild(row);
  });
}

async function refreshOnce() {
  const st = await api("/api/status");
  renderStatus(st);
  if (st.both_active) {
    const data = await api("/api/outputs");
    renderOutputs(data);
  }
}

async function startStop(on) {
  try {
    const res = await api(on ? "/api/start" : "/api/stop", { method: "POST" });
    if (on && !res.ok) {
      byId("masterToggle").checked = false;
      alert("Start failed: " + (res.error || "Unknown error"));
    }
  } catch (e) {
    if (on) byId("masterToggle").checked = false;
    alert("Request failed: " + e);
  }
}

function setup() {
  byId("masterToggle").addEventListener("change", (e) => startStop(e.target.checked));

  // Live updates via SSE (with polling fallback)
  try {
    const es = new EventSource("/api/events");
    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "status") renderStatus(msg);
        if (msg.type === "outputs") renderOutputs(msg);
      } catch (_) {}
    };
    es.onerror = () => setInterval(refreshOnce, 5000);
  } catch (_) {
    setInterval(refreshOnce, 5000);
  }

  refreshOnce();
}

document.addEventListener("DOMContentLoaded", setup);
