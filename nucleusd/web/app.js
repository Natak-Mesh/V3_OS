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
  $("m-hop").value = cfg.hop_limit ?? "";
  $("m-tx").value = cfg.tx_power ?? "";
  if (cfg.role) $("m-role").value = cfg.role;
  $("m-chan").value = cfg.channel_name || "";
  $("m-region").textContent = cfg.region || "—";
  $("m-url").value = cfg.channel_url || "";
}

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
    hop_limit: parseInt($("m-hop").value, 10),
    tx_power: parseInt($("m-tx").value, 10),
    role: $("m-role").value,
    channel_name: $("m-chan").value.trim(),
  };
  Object.keys(changes).forEach((k) => {
    if (changes[k] === "" || Number.isNaN(changes[k])) delete changes[k];
  });
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

loadCfg();
loadStatus();
meshInit();
