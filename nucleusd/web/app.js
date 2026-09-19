// Nucleus V3 — data layer + page definitions for the CLI/TUI shell (cli.js).
// No framework, no build step. Pages return an `items` model that the shell
// renders uniformly; the shell owns cursor/focus, keyboard and auto-refresh.

const MB = "/api/v1/meshtastic";

async function jget(url) {
  const r = await fetch(url);
  const d = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, d };
}
async function jsend(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const d = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, d };
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// Human "time since" for last-heard epochs.
function ago(epoch) {
  if (!epoch) return "never";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h";
  return Math.floor(s / 86400) + "d";
}

// ── Shared status header ───────────────────────────────────────
// Populated on a 5s interval by the shell; independent of the current page.
async function refreshHeader() {
  try {
    const { d: st } = await jget("/api/v1/status");
    const wlan1 = (st.interfaces && st.interfaces.wlan1) || {};
    const ip = (wlan1.addrs && wlan1.addrs[0]) ? wlan1.addrs[0].split("/")[0] : "—";
    const meshSvc = (st.services && st.services["nucleus-mesh"]) || "unknown";
    const nbrs = (st.babel_neighbours || []).length;
    const el = document.getElementById("h-mesh");
    let text, cls;
    if (meshSvc !== "active") {
      text = "DOWN";
      cls = "down";
    } else if (nbrs > 0) {
      text = nbrs + " connected";
      cls = "up";
    } else {
      text = "0 connected";
      cls = "warn";
    }
    el.textContent = text;
    el.className = "v " + cls;
    document.getElementById("h-ip").textContent = ip;
  } catch (e) { /* header stays as-is on transient errors */ }
}

async function refreshHeaderOnce() {
  try {
    const { d: cfg } = await jget("/api/v1/config");
    const der = cfg._derived || {};
    document.getElementById("h-node").textContent =
      der.hostname || (cfg.node && cfg.node.hostname) || "—";
  } catch (e) {}
  refreshHeader();
}

// ── Meshtastic radio config field options ──────────────────────
const MODEMS = ["LONG_FAST", "LONG_SLOW", "LONG_MODERATE", "MEDIUM_FAST",
  "MEDIUM_SLOW", "SHORT_FAST", "SHORT_SLOW", "SHORT_TURBO"];
const REGIONS = ["US", "EU_433", "EU_868", "ANZ", "CN", "JP", "KR", "TW", "RU",
  "IN", "NZ_865", "TH", "UA_433", "UA_868", "LORA_24"];
const ROLES = ["CLIENT", "CLIENT_MUTE", "CLIENT_HIDDEN", "TRACKER", "TAK",
  "TAK_TRACKER", "SENSOR", "ROUTER", "ROUTER_CLIENT", "ROUTER_LATE", "REPEATER"];
const PSK_MODES = ["keep", "random", "default", "none"];

// Working copy of the radio config, filled from the cached read.
let M = null;
// Working copy of the presence-heartbeat node config (separate from radio).
let HB = null;
// Working copy of the full node config, edited on the CONFIG page.
let CFG = null;

function meshFillFrom(cfg) {
  M = {
    owner: cfg.owner || "",
    owner_short: cfg.owner_short || "",
    modem_preset: cfg.modem_preset || "LONG_FAST",
    frequency_slot: cfg.frequency_slot ?? 0,
    channel_name: cfg.channel_name || "",
    psk_mode: "keep",
    region: (cfg.region && cfg.region !== "UNSET") ? cfg.region : "US",
    hop_limit: cfg.hop_limit ?? 3,
    tx_power: cfg.tx_power ?? 30,
    role: cfg.role || "CLIENT",
    channel_url: cfg.channel_url || "",
    _base: cfg,
  };
}


// ── PAGES ──────────────────────────────────────────────────────
// Each page: { title, dynamic?, async build(params) -> { items:[...] } }.
// Item types the shell understands:
//   nav      selectable  -> onEnter navigates
//   button   selectable  -> onEnter runs an action
//   fselect  selectable  -> cycle `options` in edit mode
//   fnum     selectable  -> +/- by step in edit mode (min/max)
//   ftext    selectable  -> focus an <input> in edit mode
//   content  static       -> raw html (skipped by the cursor)
const PAGES = {
  // Dashboard: a menu; live data lives in the persistent header.
  home: {
    title: "Main Menu",
    build() {
      return {
        items: [
          { type: "nav", label: "MONITOR", to: "monitor" },
          { type: "nav", label: "MESHTASTIC RADIO", to: "meshtastic" },
          { type: "nav", label: "SYSTEM", to: "system" },
          { type: "nav", label: "CONFIG", to: "config" },
        ],
      };
    },
  },

  // Who's connected: wifi-mesh (Babel) neighbours only. Diagnostic detail
  // (interfaces, services) lives on the SYSTEM page.
  monitor: {
    title: "Monitor",
    dynamic: 5000,
    async build() {
      const { d: st } = await jget("/api/v1/status");
      const nbrs = st.babel_neighbours || [];
      let h = "";

      h += `<div class="content"><div class="page-title" style="padding-left:0">Wifi mesh neighbours</div>`;
      if (!nbrs.length) {
        h += `<div class="off">no neighbours — this node does not see any other node over wlan1</div>`;
      } else {
        h += `<table><tr><th>Neighbour</th><th>Iface</th><th>Cost</th><th>Link</th></tr>`;
        nbrs.forEach((n) => {
          const good = n.cost && parseInt(n.cost, 10) < 512;
          const linkGood = n.link_pct != null && n.link_pct >= 75;
          const link = n.link_pct != null ? n.link_pct + "%" : "—";
          h += `<tr><td>${esc(n.ipv4 || n.address || n.id)}</td><td>${esc(n["if"] || "—")}</td>` +
            `<td class="${good ? "ok" : "warn"}">${esc(n.cost || "—")}</td>` +
            `<td class="${linkGood ? "ok" : "warn"}">${esc(link)}</td></tr>`;
        });
        h += `</table>`;
      }
      h += `</div>`;

      // LoRa (Meshtastic) nodes heard over RF.
      const { d: md } = await jget(MB + "/nodes");
      const nodes = md.nodes || [];
      h += `<div class="content"><div class="page-title" style="padding-left:0">LoRa nodes</div>`;
      if (!md.bridge_running) {
        h += `<div class="warn">cot-bridge not running — LoRa visibility unavailable</div>`;
      } else if (!nodes.length) {
        h += `<div class="off">no LoRa nodes heard in the last 15 min</div>`;
      } else {
        h += `<table><tr><th>Node</th><th>Heard</th><th>SNR</th><th>Hops</th></tr>`;
        nodes.forEach((n) => {
          const recent = n.last_heard && (Date.now() / 1000 - n.last_heard) < 300;
          h += `<tr><td>${esc(n.long_name || n.short_name || n.id)}</td>` +
            `<td class="${recent ? "ok" : "warn"}">${ago(n.last_heard)}</td>` +
            `<td>${n.snr != null ? esc(n.snr) : "—"}</td>` +
            `<td>${n.hops_away != null ? esc(n.hops_away) : "—"}</td></tr>`;
        });
        h += `</table>`;
      }
      h += `</div>`;

      return { items: [{ type: "content", html: h }] };
    },
  },

  // Diagnostic detail: interface addresses + systemd service states.
  system: {
    title: "System",
    dynamic: 5000,
    async build() {
      const { d: st } = await jget("/api/v1/status");
      let h = "";

      h += `<div class="content"><div class="page-title" style="padding-left:0">Interfaces</div><table>` +
        `<tr><th>Iface</th><th>State</th><th>Addr</th></tr>`;
      Object.entries(st.interfaces || {}).forEach(([name, i]) => {
        const os = i.oper_state || i.state;
        const cls = os === "UP" ? "ok" : (i.state === "absent" ? "off" : "warn");
        h += `<tr><td>${esc(name)}</td><td class="${cls}">${esc(os)}</td>` +
          `<td>${esc((i.addrs || []).join(", ") || "—")}</td></tr>`;
      });
      h += `</table></div>`;

      h += `<div class="content"><div class="page-title" style="padding-left:0">Services</div><table>`;
      Object.entries(st.services || {}).forEach(([u, s]) => {
        h += `<tr><td>${esc(u)}</td><td class="${s === "active" ? "ok" : "off"}">${esc(s)}</td></tr>`;
      });
      h += `</table></div>`;

      return { items: [{ type: "content", html: h }] };
    },
  },

  // Meshtastic radio: field list + read/apply/import/QR actions.
  meshtastic: {
    title: "Meshtastic Radio",
    async build() {
      const { d: s } = await jget(MB + "/status");
      const statusLine =
        `radio: ${s.radio_detected ? "detected" : "not detected"} · ` +
        `bridge: ${s.service_active ? "running" : "stopped"}` +
        (s.bridge_enabled ? " (enabled)" : " (disabled)");

      if (!M) {
        const { d } = await jget(MB + "/config");
        if (d.config) meshFillFrom(d.config);
      }

      const items = [{
        type: "content",
        html: `<div class="content"><div class="kv"><span>${esc(statusLine)}</span></div>` +
          `<div class="hint" style="padding-left:0">Blue = channel identity (must match ` +
          `across nodes) · Amber = this node only</div></div>`,
      }, {
        type: "button", label: "» Read config from radio",
        onEnter: (S) => radioOp(S, MB + "/config/read", null, "read"),
      }];

      if (M) {
        items.push(
          { type: "ftext", key: "owner", label: "Long name", value: M.owner, max: 39, onChange: (v) => M.owner = v },
          { type: "ftext", key: "owner_short", label: "Short name", value: M.owner_short, max: 4, onChange: (v) => M.owner_short = v },
          { type: "fselect", key: "modem_preset", label: "Modem preset", options: MODEMS, value: M.modem_preset, onChange: (v) => M.modem_preset = v },
          { type: "fnum", key: "frequency_slot", label: "Freq slot (0=auto)", value: M.frequency_slot, min: 0, max: 104, onChange: (v) => M.frequency_slot = v },
          { type: "ftext", key: "channel_name", label: "Channel name", value: M.channel_name, max: 11, color: "accent", onChange: (v) => M.channel_name = v },
          { type: "fselect", key: "psk_mode", label: "Encryption key", options: PSK_MODES, value: M.psk_mode, color: "accent", onChange: (v) => M.psk_mode = v },
          { type: "fselect", key: "region", label: "Region", options: REGIONS, value: M.region, color: "accent", onChange: (v) => M.region = v },
          { type: "fnum", key: "hop_limit", label: "Hop limit", value: M.hop_limit, min: 1, max: 7, color: "warn", onChange: (v) => M.hop_limit = v },
          { type: "fnum", key: "tx_power", label: "TX power (dBm)", value: M.tx_power, min: 0, max: 30, color: "warn", onChange: (v) => M.tx_power = v },
          { type: "fselect", key: "role", label: "Role", options: ROLES, value: M.role, color: "warn", onChange: (v) => M.role = v },
          { type: "button", label: "» Apply to radio (reboots, ~30-120s)", onEnter: meshApply },
          { type: "ftext", key: "channel_url", label: "Channel URL", value: M.channel_url, onChange: (v) => M.channel_url = v },
          { type: "button", label: "» Import channel URL", onEnter: meshImportUrl },
          { type: "button", label: "» Show QR code", onEnter: meshShowQr },
          { type: "content", html: `<div id="m-qr-slot"></div>` },
          { type: "content", html: `<div class="content"><div class="page-title" ` +
            `style="padding-left:0">Join a peer's Meshtastic channel</div><div class="hint" ` +
            `style="padding-left:0">Queries other Nucleus devices over the 802.11s wifi mesh ` +
            `and compares their Meshtastic channel settings (channel name, encryption key, ` +
            `region, modem preset, frequency slot) to this device's. Join copies those ` +
            `settings to this device's Meshtastic radio so both LoRa radios share the channel. ` +
            `Role, TX power and hop limit are untouched. The Meshtastic radio reboots after ` +
            `joining.</div><div id="peers-slot" class="content"></div></div>` },
          { type: "button", label: "» Query Nucleus peers for Meshtastic channels", onEnter: loadPeers },
        );
      }

      // Presence heartbeat (node config, not radio) — kept in HB, saved via
      // /api/v1/config. cot-bridge reads it live, so no radio reboot.
      if (!HB) {
        const { d: cfg } = await jget("/api/v1/config");
        const hb = (cfg.meshtastic && cfg.meshtastic.heartbeat) || {};
        HB = { enabled: hb.enabled !== false, interval_secs: hb.interval_secs ?? 300 };
      }
      items.push(
        { type: "content", html: `<div class="content"><div class="page-title" ` +
          `style="padding-left:0">Presence heartbeat</div><div class="hint" ` +
          `style="padding-left:0">Keeps this node in peers' LoRa list without ATAK ` +
          `traffic. Nodes drop off after 15 min of silence.</div></div>` },
        { type: "fselect", key: "hb_enabled", label: "Heartbeat", options: ["on", "off"],
          value: HB.enabled ? "on" : "off", onChange: (v) => HB.enabled = (v === "on") },
        { type: "fnum", key: "hb_interval", label: "Interval (s)", value: HB.interval_secs,
          min: 60, max: 3600, step: 60, onChange: (v) => HB.interval_secs = v },
        { type: "button", label: "» Save heartbeat", onEnter: saveHeartbeat },
      );
      return { items };
    },
  },

  // Config: schema-driven form. Operators set primitives; the header line of
  // each section shows the DERIVED values the schema computes from them.
  config: {
    title: "Config",
    async build() {
      // First open (or after Reload) loads a working copy; edits mutate CFG and
      // are saved as one read-modify-write PUT, matching saveHeartbeat's model.
      const { d } = await jget("/api/v1/config");
      const der = d._derived || {};
      delete d._derived;
      CFG = d;
      const node = CFG.node = CFG.node || {};
      const mesh = CFG.mesh = CFG.mesh || {};
      const brlan = CFG.br_lan = CFG.br_lan || {};
      const ap = CFG.ap = CFG.ap || {};
      const eth0 = CFG.eth0 = CFG.eth0 || {};
      const mt = CFG.meshtastic = CFG.meshtastic || {};

      const head = (title, sub) => ({ type: "content", html:
        `<div class="content"><div class="page-title" style="padding-left:0">${esc(title)}</div>` +
        (sub ? `<div class="hint" style="padding-left:0">${sub}</div>` : "") + `</div>` });

      const items = [
        head("Node", `hostname <b>${esc(der.hostname || "—")}</b> · ` +
          `mesh IP <b>${esc(der.mesh_ip || "—")}</b>`),
        { type: "fnum", label: "Node id (blank=hostname)", value: node.id ?? "",
          min: 1, max: 254, onChange: (v) => node.id = v },
        { type: "ftext", label: "Name (hostname override)", value: node.name || "",
          onChange: (v) => node.name = v },

        head("Wifi Mesh", `subnet <b>${esc(der.mesh_subnet || "—")}</b>`),
        { type: "ftext", label: "SSID", value: mesh.ssid || "",
          onChange: (v) => mesh.ssid = v },
        { type: "fnum", label: "Channel (2.4GHz)", value: mesh.channel ?? 3,
          min: 1, max: 13, onChange: (v) => mesh.channel = v },
        { type: "ftext", label: "Password (min 8)", value: mesh.password || "",
          onChange: (v) => mesh.password = v },
        { type: "ftext", label: "Subnet prefix", value: mesh.subnet_prefix || "",
          onChange: (v) => mesh.subnet_prefix = v },
        { type: "ftext", label: "Country", value: mesh.country || "US",
          onChange: (v) => mesh.country = v },
        { type: "fnum", label: "mcast TTL", value: mesh.mcast_ttl ?? 8,
          min: 0, max: 64, onChange: (v) => mesh.mcast_ttl = v },
        { type: "fnum", label: "802.11s TTL", value: mesh.mesh_802_ttl ?? 8,
          min: 0, max: 31, onChange: (v) => mesh.mesh_802_ttl = v },
        { type: "fnum", label: "RTS threshold (0=off)", value: mesh.rts_threshold ?? 500,
          min: 0, max: 2347, onChange: (v) => mesh.rts_threshold = v },

        head("Access Point", `SSID <b>${esc(der.ap_name || "—")}</b>`),
        { type: "fnum", label: "Channel (5GHz)", value: ap.channel ?? 36,
          min: 1, max: 165, onChange: (v) => ap.channel = v },
        { type: "ftext", label: "Password (min 8)", value: ap.password || "",
          onChange: (v) => ap.password = v },
        { type: "ftext", label: "SSID override", value: ap.name || "",
          onChange: (v) => ap.name = v },

        head("br-lan", `IP <b>${esc(der.br_lan_ip || "—")}</b>`),
        { type: "ftext", label: "Subnet prefix (blank=auto)", value: brlan.subnet_prefix || "",
          onChange: (v) => brlan.subnet_prefix = v },
        { type: "fnum", label: "DHCP pool offset", value: brlan.dhcp_pool_offset ?? 10,
          min: 0, max: 254, onChange: (v) => brlan.dhcp_pool_offset = v },
        { type: "fnum", label: "DHCP pool size", value: brlan.dhcp_pool_size ?? 50,
          min: 1, max: 254, onChange: (v) => brlan.dhcp_pool_size = v },
        { type: "ftext", label: "DNS", value: brlan.dns || "8.8.8.8",
          onChange: (v) => brlan.dns = v },

        head("eth0"),
        { type: "fselect", label: "Mode", options: ["wan", "lan"],
          value: eth0.mode || "wan", onChange: (v) => eth0.mode = v },
        { type: "fnum", label: "DHCP pool offset", value: eth0.dhcp_pool_offset ?? 10,
          min: 0, max: 254, onChange: (v) => eth0.dhcp_pool_offset = v },
        { type: "fnum", label: "DHCP pool size", value: eth0.dhcp_pool_size ?? 100,
          min: 1, max: 254, onChange: (v) => eth0.dhcp_pool_size = v },

        head("Meshtastic Radio"),
        { type: "fselect", label: "Enabled", options: ["on", "off"],
          value: mt.enabled !== false ? "on" : "off",
          onChange: (v) => mt.enabled = (v === "on") },
        { type: "fselect", label: "Region", options: REGIONS,
          value: mt.region || "US", onChange: (v) => mt.region = v },
        { type: "fselect", label: "HAT / slot",
          options: ["rak6421-slot1", "rak6421-slot2", "auto"],
          value: mt.hat || "rak6421-slot1", onChange: (v) => mt.hat = v },
        { type: "fselect", label: "GPS", options: ["off", "uart", "i2c"],
          value: mt.gps || "uart", onChange: (v) => mt.gps = v },
        { type: "ftext", label: "GPS serial path", value: mt.gps_serial_path || "/dev/ttyS0",
          onChange: (v) => mt.gps_serial_path = v },
        { type: "ftext", label: "I2C device", value: mt.i2c_device || "/dev/i2c-1",
          onChange: (v) => mt.i2c_device = v },
        { type: "fselect", label: "CoT bridge", options: ["on", "off"],
          value: mt.cot_bridge !== false ? "on" : "off",
          onChange: (v) => mt.cot_bridge = (v === "on") },

        { type: "button", label: "» Save (not applied)", onEnter: saveCfg },
        { type: "button", label: "» Dry-run apply", onEnter: (S) => applyCfg(S, true) },
        { type: "button", label: "» Apply", onEnter: (S) => applyCfg(S, false) },
        { type: "button", label: "» Reload", onEnter: (S) => { CFG = null; return S.reload(); } },
      ];
      return { items };
    },
  },
};


// ── Actions (S = shell helper: msg(), reload()) ────────────────
async function radioOp(S, url, body, label) {
  S.msg(label + "…");
  const { ok, d } = await jsend("POST", url, body);
  if (!(ok && d.started)) return S.msg(`${label} failed: ${d.detail || "error"}`, false);
  for (let i = 0; i < 90; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    const { d: st } = await jget(MB + "/config/op-status");
    if (st.status === "done") {
      if (st.config) meshFillFrom(st.config);
      S.msg(label + " done");
      return S.reload();
    }
    if (st.status === "error") return S.msg(`${label} failed: ${st.error}`, false);
    S.msg(`${label}… (${(i + 1) * 2}s)`);
  }
  S.msg(label + " timed out", false);
}

async function meshApply(S) {
  if (!M) return S.msg("read config first", false);
  const changes = {
    owner: M.owner.trim(), owner_short: M.owner_short.trim(),
    modem_preset: M.modem_preset, frequency_slot: parseInt(M.frequency_slot, 10),
    hop_limit: parseInt(M.hop_limit, 10), tx_power: parseInt(M.tx_power, 10),
    role: M.role, channel_name: M.channel_name.trim(), region: M.region,
  };
  Object.keys(changes).forEach((k) => {
    if (changes[k] === "" || Number.isNaN(changes[k])) delete changes[k];
  });
  if (M.psk_mode !== "keep") changes.psk = M.psk_mode;
  const base = M._base || {};
  if (changes.region === base.region) delete changes.region;
  if (changes.frequency_slot === base.frequency_slot) delete changes.frequency_slot;
  if (changes.psk && !confirm(
    "Change the encryption key? Other radios can't communicate until they " +
    "receive the new channel URL (QR / paste / peer join).")) return;
  return radioOp(S, MB + "/config/apply", { changes }, "apply");
}

async function meshImportUrl(S) {
  const url = (M && M.channel_url || "").trim();
  if (!url) return S.msg("enter a channel URL in the field first", false);
  return radioOp(S, MB + "/config/channel-url", { url }, "import");
}

async function meshShowQr(S) {
  const { ok } = await jget(MB + "/config/qr");
  const slot = document.getElementById("m-qr-slot");
  if (!ok) { if (slot) slot.innerHTML = ""; return S.msg("no channel URL yet — read config first", false); }
  const svg = await (await fetch(MB + "/config/qr")).text();
  if (slot) slot.innerHTML = `<div class="qr">${svg}</div>`;
  S.msg("");
}

async function loadPeers(S) {
  const slot = document.getElementById("peers-slot");
  if (slot) slot.innerHTML = `<div class="hint">querying Nucleus devices on the wifi mesh for Meshtastic configs…</div>`;
  const { d } = await jget(MB + "/peers");
  const L = d.local || {};
  const peers = d.peers || [];
  if (!peers.length) { if (slot) slot.innerHTML = `<div class="off">no other Nucleus devices reachable on the wifi mesh</div>`; return; }
  let h = `<table><tr><th>Nucleus IP</th><th>Channel</th><th>Key</th><th>Region</th>` +
    `<th>Preset</th><th>Slot</th><th>Match</th></tr>`;
  const cell = (v, match) => `<td class="${match ? "ok" : "off"}">${esc(v === "" || v == null ? "—" : v)}</td>`;
  peers.forEach((p) => {
    if (!p.reachable) {
      h += `<tr><td>${esc(p.ip)}</td><td class="warn" colspan="6">unreachable / no Meshtastic config</td></tr>`;
      return;
    }
    const match = p.channel_name === L.channel_name && p.psk_fingerprint === L.psk_fingerprint &&
      p.modem_preset === L.modem_preset && p.region === L.region && p.frequency_slot === L.frequency_slot;
    h += `<tr><td>${esc(p.ip)}</td>` +
      cell(p.channel_name, p.channel_name === L.channel_name) +
      cell(p.psk_fingerprint, p.psk_fingerprint === L.psk_fingerprint) +
      cell(p.region, p.region === L.region) +
      cell(p.modem_preset, p.modem_preset === L.modem_preset) +
      cell(p.frequency_slot, p.frequency_slot === L.frequency_slot) +
      `<td class="${match ? "ok" : "off"}">` +
      ((match || !p.has_channel_url) ? (match ? "match" : "differs")
        : `<button class="act" onclick="joinPeer('${esc(p.ip)}')">Join</button>`) +
      `</td></tr>`;
  });
  h += `</table>`;
  if (slot) slot.innerHTML = h;
}

// Exposed for the inline Join button rendered above.
window.joinPeer = async function (ip) {
  if (!confirm(`Join ${ip}'s Meshtastic channel? Copies its channel name, key, region, ` +
    `preset and slot to this device's Meshtastic radio (role, TX power, hop limit unchanged). ` +
    `Meshtastic radio reboots.`)) return;
  const S = window.__shell;
  await radioOp(S, MB + "/config/join-peer", { host: ip }, "join");
  loadPeers(S);
};

async function saveHeartbeat(S) {
  if (!HB) return S.msg("nothing to save", false);
  // Read-modify-write the full node config so we don't clobber other fields.
  const { d: cfg } = await jget("/api/v1/config");
  delete cfg._derived;
  cfg.meshtastic = cfg.meshtastic || {};
  cfg.meshtastic.heartbeat = {
    enabled: HB.enabled,
    interval_secs: parseInt(HB.interval_secs, 10) || 300,
  };
  const { ok, d } = await jsend("PUT", "/api/v1/config", cfg);
  if (ok) S.msg("heartbeat saved — active within one bridge cycle (~10s)");
  else S.msg("save failed: " + JSON.stringify(d.detail), false);
}

async function saveCfg(S) {
  if (!CFG) return S.msg("nothing to save", false);
  // Deep copy so we can strip empty optionals without mutating the live form.
  const body = JSON.parse(JSON.stringify(CFG));
  delete body._derived;
  // Empty string / null optionals => omit so schema defaults + derivation apply.
  const prune = (obj, keys) => keys.forEach((k) => {
    if (obj && (obj[k] === "" || obj[k] == null)) delete obj[k];
  });
  prune(body.node, ["id", "name"]);
  prune(body.br_lan, ["subnet_prefix"]);
  prune(body.ap, ["name"]);
  const { ok, d } = await jsend("PUT", "/api/v1/config", body);
  if (ok) S.msg("saved (" + d.hostname + ") — not yet applied");
  else S.msg("validation failed: " + JSON.stringify(d.detail), false);
}

async function applyCfg(S, dry) {
  const { ok, d } = await jsend("POST", "/api/v1/apply?dry_run=" + dry);
  if (!ok) return S.msg("apply failed", false);
  if (!d.changed.length) return S.msg("no changes — system in sync");
  S.msg((dry ? "would change " : "changed ") + d.changed.length +
    " file(s); units: " + (d.units_restarted.join(", ") || "none"));
}

