// Minimal web UI — talks to the same REST API an external app would use.
const $ = (id) => document.getElementById(id);
const msg = (t, ok = true) => { const m = $("msg"); m.textContent = t; m.className = "msg " + (ok ? "ok" : "err"); };

async function loadCfg() {
  const r = await fetch("/api/v1/config");
  const data = await r.json();
  delete data._derived; // display only the editable config
  $("cfg").value = JSON.stringify(data, null, 2);
  msg("loaded current config");
}

async function saveCfg() {
  let body;
  try { body = JSON.parse($("cfg").value); }
  catch (e) { return msg("invalid JSON: " + e.message, false); }
  const r = await fetch("/api/v1/config", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json();
  if (r.ok) msg("saved (" + data.hostname + ") — not yet applied");
  else msg("validation failed: " + JSON.stringify(data.detail), false);
}

async function applyCfg(dry) {
  const r = await fetch("/api/v1/apply?dry_run=" + dry, { method: "POST" });
  const data = await r.json();
  if (!r.ok) return msg("apply failed", false);
  const n = data.changed.length;
  if (n === 0) return msg("no changes — system in sync");
  msg((dry ? "would change " : "changed ") + n + " file(s); units: " + (data.units_restarted.join(", ") || "none"));
}

async function loadStatus() {
  const r = await fetch("/api/v1/status");
  $("status").textContent = JSON.stringify(await r.json(), null, 2);
}

// ── Meshtastic radio ────────────────────────────────────────────
const mmsg = (t, ok = true) => { const m = $("mesh-msg"); m.textContent = t; m.className = "msg " + (ok ? "ok" : "err"); };
const MB = "/api/v1/meshtastic";
let meshCfgCache = null;
let peersLocal = null;

async function meshRefresh() {
  try {
    const r = await fetch(MB + "/status");
    const s = await r.json();
    $("mesh-status").textContent =
      `radio: ${s.radio_detected ? "detected" : "not detected"} · ` +
      `bridge: ${s.service_active ? "running" : "stopped"}` +
      (s.bridge_enabled ? " (enabled)" : " (disabled)");
  } catch (e) { $("mesh-status").textContent = "meshtastic API unavailable"; }
}

function meshFill(cfg) {
  if (!cfg) return;
  $("mesh-cfg").style.display = "block";
  $("m-owner").value = cfg.owner || "";
  $("m-owner-short").value = cfg.owner_short || "";
  if (cfg.modem_preset) $("m-modem").value = cfg.modem_preset;
  $("m-slot").value = cfg.frequency_slot ?? 0;
  $("m-hop").value = cfg.hop_limit ?? "";
  $("m-tx").value = cfg.tx_power ?? "";
  if (cfg.role) $("m-role").value = cfg.role;
  $("m-chan").value = cfg.channel_name || "";
  if (cfg.region && cfg.region !== "UNSET") $("m-region").value = cfg.region;
  $("m-psk-mode").value = "keep";
  $("m-psk-custom").value = "";
  $("m-psk-custom").style.display = "none";
  $("m-psk-custom-label").style.display = "none";
  $("m-url").value = cfg.channel_url || "";
  meshCfgCache = cfg;
}

// Show the custom-key input only when PSK mode = custom.
document.addEventListener("change", (e) => {
  if (e.target && e.target.id === "m-psk-mode") {
    const show = e.target.value === "custom";
    $("m-psk-custom").style.display = show ? "block" : "none";
    $("m-psk-custom-label").style.display = show ? "block" : "none";
  }
});

// Poll op-status until the background radio op finishes.
async function meshPoll(label) {
  for (let i = 0; i < 90; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    const r = await fetch(MB + "/config/op-status");
    const st = await r.json();
    if (st.status === "done") { meshFill(st.config); mmsg(label + " done"); return; }
    if (st.status === "error") { mmsg(label + " failed: " + st.error, false); return; }
    mmsg(label + "… (" + (i + 1) * 2 + "s)");
  }
  mmsg(label + " timed out", false);
}

async function meshReadConfig() {
  mmsg("reading radio config…");
  const r = await fetch(MB + "/config/read", { method: "POST" });
  const d = await r.json();
  if (r.ok && d.started) return meshPoll("read");
  mmsg("read failed: " + (d.detail || r.status), false);
}

async function meshApplyConfig() {
  const changes = {
    owner: $("m-owner").value.trim(),
    owner_short: $("m-owner-short").value.trim(),
    modem_preset: $("m-modem").value,
    frequency_slot: parseInt($("m-slot").value, 10),
    hop_limit: parseInt($("m-hop").value, 10),
    tx_power: parseInt($("m-tx").value, 10),
    role: $("m-role").value,
    channel_name: $("m-chan").value.trim(),
    region: $("m-region").value,
  };
  Object.keys(changes).forEach((k) => {
    if (changes[k] === "" || Number.isNaN(changes[k])) delete changes[k];
  });
  // PSK mode -> psk change (only when not "keep").
  const pskMode = $("m-psk-mode").value;
  if (pskMode === "random" || pskMode === "default" || pskMode === "none") {
    changes.psk = pskMode;
  } else if (pskMode === "custom") {
    const key = $("m-psk-custom").value.trim();
    if (key) changes.psk = key;
  }
  // Don't re-send unchanged channel-identity fields against the last read.
  if (meshCfgCache) {
    if (changes.region === meshCfgCache.region) delete changes.region;
    if (changes.frequency_slot === meshCfgCache.frequency_slot) delete changes.frequency_slot;
  }
  if (changes.psk && !confirm(
      "Change the encryption key? Other radios can't communicate until they " +
      "receive the new channel URL (QR / paste / peer join).")) return;
  mmsg("applying to radio (radio reboots, ~30-120s)…");
  const r = await fetch(MB + "/config/apply", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ changes }),
  });
  const d = await r.json();
  if (r.ok && d.started) return meshPoll("apply");
  mmsg("apply failed: " + (d.detail || r.status), false);
}

async function meshApplyUrl() {
  const url = $("m-url").value.trim();
  if (!url) return mmsg("paste a channel URL first", false);
  mmsg("importing channel URL…");
  const r = await fetch(MB + "/config/channel-url", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const d = await r.json();
  if (r.ok && d.started) return meshPoll("import");
  mmsg("import failed: " + (d.detail || r.status), false);
}

async function meshShowQr() {
  const r = await fetch(MB + "/config/qr");
  if (!r.ok) return mmsg("no channel URL yet — read config first", false);
  $("m-qr").innerHTML = await r.text();
}

async function meshInit() {
  await meshRefresh();
  const r = await fetch(MB + "/config");
  const d = await r.json();
  if (d.config) meshFill(d.config);
}

// ── Mesh peers: discover + one-click join ───────────────────────
const pmsg = (t, ok = true) => { const m = $("peers-msg"); m.textContent = t; m.className = "msg " + (ok ? "ok" : "err"); };

function peerCell(val, match) {
  const shown = (val === "" || val === null || val === undefined) ? "—" : val;
  return `<td style="padding:6px; color:${match ? "#3fb950" : "#f85149"}">${shown}</td>`;
}

async function meshLoadPeers() {
  pmsg("scanning mesh for peers…");
  try {
    const r = await fetch(MB + "/peers");
    const d = await r.json();
    peersLocal = d.local || null;
    renderPeers(d.peers || []);
    pmsg("");
  } catch (e) { pmsg("peer scan failed: " + e.message, false); }
}

function renderPeers(peers) {
  const table = $("peers-table"), body = $("peers-body");
  body.innerHTML = "";
  if (!peers.length) { table.style.display = "none"; pmsg("no mesh peers found", false); return; }
  table.style.display = "table";
  const L = peersLocal || {};
  peers.forEach((p) => {
    const tr = document.createElement("tr");
    tr.style.borderBottom = "1px solid #21262d";
    if (!p.reachable) {
      tr.innerHTML = `<td style="padding:6px">${p.ip}</td>` +
        `<td style="padding:6px; color:#8b949e" colspan="5">unreachable / no config</td>` +
        `<td style="padding:6px; color:#8b949e">—</td><td></td>`;
      body.appendChild(tr); return;
    }
    const match = p.channel_name === L.channel_name && p.psk_fingerprint === L.psk_fingerprint &&
      p.modem_preset === L.modem_preset && p.region === L.region && p.frequency_slot === L.frequency_slot;
    let html = `<td style="padding:6px">${p.ip}</td>`;
    html += peerCell(p.channel_name, p.channel_name === L.channel_name);
    html += peerCell(p.psk_fingerprint, p.psk_fingerprint === L.psk_fingerprint);
    html += peerCell(p.region, p.region === L.region);
    html += peerCell(p.modem_preset, p.modem_preset === L.modem_preset);
    html += peerCell(p.frequency_slot, p.frequency_slot === L.frequency_slot);
    html += `<td style="padding:6px; color:${match ? "#3fb950" : "#f85149"}">${match ? "match" : "differs"}</td>`;
    html += (match || !p.has_channel_url) ? "<td></td>" :
      `<td style="padding:6px"><button onclick="meshJoinPeer('${p.ip}')">Join</button></td>`;
    tr.innerHTML = html;
    body.appendChild(tr);
  });
}

async function meshJoinPeer(ip) {
  if (!confirm(`Join ${ip}'s LoRa channel? Copies its channel name, key, region, ` +
    `preset and slot to this radio (your role, TX power, hop limit unchanged). Radio reboots.`)) return;
  pmsg(`joining ${ip} (radio reboots, ~30-120s)…`);
  try {
    const r = await fetch(MB + "/config/join-peer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host: ip }),
    });
    const d = await r.json();
    if (r.ok && d.started) { await meshPoll("join"); return meshLoadPeers(); }
    pmsg("join failed: " + (d.detail || r.status), false);
  } catch (e) { pmsg("join failed: " + e.message, false); }
}

loadCfg();
loadStatus();
meshInit();
