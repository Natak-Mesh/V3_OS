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
// Whether the collapsible radio-config section is expanded on the Meshtastic page.
let MESH_CFG_OPEN = false;
// Working copy of the presence-heartbeat node config (separate from radio).
let HB = null;
// Working copy of the full node config, edited on the CONFIG page.
let CFG = null;
let MSG_DRAFT = "";
// Live chat state: messages cache + WebSocket, populated by the messaging page.
let MSG_CACHE = [];        // ordered oldest→newest, keyed by id
let MSG_WS = null;         // active WebSocket, or null when the page is closed
let MSG_WS_POLL = false;   // true once WS failed and we fell back to polling

// Merge a message into MSG_CACHE by id (updates transports/mine if it exists).
function msgUpsert(m) {
  if (!m || !m.id) return;
  const i = MSG_CACHE.findIndex((x) => x.id === m.id);
  if (i >= 0) MSG_CACHE[i] = m;
  else MSG_CACHE.push(m);
  // Keep oldest→newest by ts: WS pushes, poll merges and late LoRa copies can
  // arrive out of order. Stable tiebreak on id so equal-ts entries don't jitter.
  MSG_CACHE.sort((a, b) => (a.ts - b.ts) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

// Voice state: channel list cache for the TUI voice page.
let VOICE_CH = null;

// Tailscale page state: auth-key input draft + selected tailnet profile.
let TS_AUTHKEY = "";
let TS_ACCT = null;

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
    async build() {
      const items = [
        { type: "nav", label: "MESH CONNECTIONS", to: "monitor" },
        { type: "nav", label: "MESSAGING", to: "messaging" },
        { type: "nav", label: "VOICE (PTT)", to: "voice" },
        { type: "nav", label: "MESHTASTIC", to: "meshtastic" },
        { type: "nav", label: "INTERFACES AND SERVICES", to: "system" },
        { type: "nav", label: "TAILSCALE (VPN)", to: "tailscale" },
        { type: "nav", label: "RADIO CONFIGURATION", to: "config" },
      ];
      // TAK Server is optional; show the menu entry only when it's installed.
      const { ok, d: tak } = await jget("/api/v1/tak/status");
      if (ok && tak.installed) {
        items.push({ type: "nav", label: "TAK SERVER", to: "tak" });
      }
      items.push({ type: "nav", label: "SYSTEM UPDATE", to: "update" });
      return { items };
    },
  },

  // Who's connected: wifi-mesh (Babel) neighbours only. Diagnostic detail
  // (interfaces, services) lives on the SYSTEM page.
  monitor: {
    title: "Monitor",
    dynamic: 5000,
    async build() {
      const { d: st } = await jget("/api/v1/status");
      const routes = st.babel_routes || [];
      let h = "";

      h += `<div class="content"><div class="page-title" style="padding-left:0">Wifi mesh nodes</div>`;
      if (!routes.length) {
        h += `<div class="off">no nodes — this node does not see any other node over wlan1</div>`;
      } else {
        h += `<table><tr><th>Node</th><th>Via</th><th>Cost</th></tr>`;
        routes.forEach((r) => {
          const via = r.direct ? "direct" : esc(r.via || "—");
          const cost = (r.metric / 256).toFixed(1);
          h += `<tr><td>${esc(r.node)}</td><td>${via}</td>` +
            `<td>${esc(cost)}</td></tr>`;
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
      // TAK Server is optional; show a row only when it's installed.
      const { d: tak } = await jget("/api/v1/tak/status");
      if (tak && tak.installed) {
        h += `<tr><td>takserver</td><td class="${tak.service === "active" ? "ok" : "off"}">${esc(tak.service)}</td></tr>`;
      }
      h += `</table></div>`;

      return { items: [{ type: "content", html: h }] };
    },
  },

  // TAK Server (optional): client-cert downloads + web-admin pointer. Only
  // meaningful on nodes provisioned with nucleus-tak-setup.sh; hidden otherwise.
  tak: {
    title: "TAK Server",
    dynamic: 5000,
    async build() {
      const { ok, d: s } = await jget("/api/v1/tak/status");
      const items = [];

      if (!ok || !s.installed) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="off">TAK Server is not installed on this node</div></div>` });
        return { items };
      }

      const cls = s.service === "active" ? "ok" : "off";
      let head = `<div class="content"><div class="kv">` +
        `<span>status <span class="${cls}">${esc(s.service)}</span></span></div>`;
      // Web admin lives on TAK's own port (8443), not the Nucleus UI. Point the
      // operator there and remind them the admin cert must be imported first.
      const host = location.hostname;
      head += `<div class="kv"><span>web admin ` +
        `<b>https://${esc(host)}:8443</b></span></div>` +
        `<div class="off">Import webadmin.p12 into your browser first, ` +
        `then open the web admin to manage users and certificates. ` +
        `Client devices connect on port 8089.</div></div>`;
      items.push({ type: "content", html: head });

      // One download button per staged cert (webadmin.p12 + intermediate
      // truststore). The button just navigates the browser to the download URL.
      const certs = s.certs || [];
      if (!certs.length) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="warn">no certs staged in /opt/nucleus/tak-certs</div></div>` });
      } else {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="page-title" style="padding-left:0">Download certificates</div></div>` });
        certs.forEach((name) => {
          items.push({ type: "button", label: "» " + name,
            onEnter: () => takDownload(name) });
        });
      }

      return { items };
    },
  },

  // Tailscale (VPN): live connection control. On/off, browser-login for a new
  // tailnet (or paste an auth key), and switch between already-authenticated
  // tailnets. Outside the config pipeline — drives the tailscale CLI directly.
  tailscale: {
    title: "Tailscale",
    dynamic: 5000,
    async build() {
      const { ok, d: s } = await jget("/api/v1/tailscale/status");
      const items = [];

      if (!ok || !s.installed) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="off">tailscale is not installed on this node</div></div>` });
        return { items };
      }

      const state = s.running ? "connected" : (s.logged_in ? "disconnected" : "logged out");
      const cls = s.running ? "ok" : (s.logged_in ? "warn" : "off");
      let head = `<div class="content"><div class="kv">` +
        `<span>status <span class="${cls}">${esc(state)}</span></span></div>`;
      if (s.running) {
        head += `<div class="kv"><span>tailnet <b>${esc(s.tailnet || "—")}</b></span>` +
          `<span>ip <b>${esc(s.self_ip || "—")}</b></span>` +
          `<span>peers <b>${s.peers}</b></span></div>`;
      }
      head += `<div id="ts-auth"></div></div>`;
      items.push({ type: "content", html: head });

      // Connect / disconnect.
      if (s.running) {
        items.push({ type: "button", label: "» Disconnect", onEnter: tsDown });
      } else {
        items.push({ type: "button",
          label: s.logged_in ? "» Connect" : "» Connect (browser login)",
          onEnter: tsUp });
      }

      // Join a different tailnet: paste an auth key (or leave blank + Connect
      // above for browser login), or log out of the current one.
      items.push(
        { type: "ftext", key: "ts_authkey", label: "Auth key (optional)",
          value: TS_AUTHKEY, onChange: (v) => TS_AUTHKEY = v },
        { type: "button", label: "» Join with auth key", onEnter: tsUpKey },
      );
      if (s.logged_in) {
        items.push({ type: "button", label: "» Log out", onEnter: tsLogout });
      }

      // Switch between already-authenticated tailnets (multiple profiles).
      const { d: ad } = await jget("/api/v1/tailscale/accounts");
      const accts = (ad && ad.accounts) || [];
      if (accts.length > 1) {
        if (!TS_ACCT) TS_ACCT = (accts.find((a) => a.active) || accts[0]).account;
        const opts = accts.map((a) => a.account);
        items.push(
          { type: "content", html: `<div class="content"><div class="page-title" ` +
            `style="padding-left:0">Switch tailnet</div></div>` },
          { type: "fselect", key: "ts_acct", label: "Tailnet", options: opts,
            value: TS_ACCT, onChange: (v) => TS_ACCT = v },
          { type: "button", label: "» Switch", onEnter: tsSwitch },
        );
      }

      return { items };
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

      if (MESH_CFG_OPEN && !M) {
        const { d } = await jget(MB + "/config");
        if (d.config) meshFillFrom(d.config);
      }

      const items = [{
        type: "content",
        html: `<div class="content"><div class="kv"><span>${esc(statusLine)}</span></div>` +
          `<div class="hint" style="padding-left:0">Blue = channel identity (must match ` +
          `across nodes) · Amber = this node only</div></div>`,
      }, {
        type: "button",
        label: MESH_CFG_OPEN ? "« Radio configuration" : "» Radio configuration",
        onEnter: (S) => { MESH_CFG_OPEN = !MESH_CFG_OPEN; return S.reload(); },
      }];

      if (MESH_CFG_OPEN) {
        items.push({
          type: "button", label: "» Read config from radio",
          onEnter: (S) => radioOp(S, MB + "/config/read", null, "read"),
        });
      }

      if (MESH_CFG_OPEN && M) {
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

  // Update: compares the installed (running) version with the git remote and
  // launches nucleus-update.sh. Keeps git/live separate — the running version
  // only changes after the update pulls, reinstalls the venv and restarts.
  update: {
    title: "Update",
    async build() {
      const { d: v } = await jget("/api/v1/update/check");
      const installed = v.installed || "?";
      let statusHtml, canUpdate = false, note = "";

      if (v.error && !v.offline) {
        statusHtml = `<span class="off">${esc(v.error)}</span>`;
      } else if (v.offline) {
        statusHtml = `<span class="warn">offline — cannot reach git remote</span>`;
      } else {
        const avail = v.available || "?";
        const behind = v.behind;
        if (behind === 0) {
          statusHtml = `<span class="ok">up to date</span>`;
        } else if (behind > 0) {
          statusHtml = `<span class="warn">update available` +
            ` (${behind} commit${behind === 1 ? "" : "s"} behind)</span>`;
          canUpdate = true;
        } else {
          statusHtml = `<span class="warn">local ahead / diverged</span>`;
        }
        note = `<div class="kv"><span>installed <b>${esc(installed)}</b>` +
          ` (${esc(v.local_head || "?")})</span><span> → available <b>${esc(avail)}</b>` +
          ` (${esc(v.remote_head || "?")})</span></div>`;
      }

      if (v.dirty) {
        note += `<div class="off">working tree has uncommitted changes —` +
          ` update is blocked until they are resolved</div>`;
        canUpdate = false;
      }

      const items = [{
        type: "content",
        html: `<div class="content"><div class="kv"><span>${statusHtml}</span></div>` +
          note + `</div>`,
      }];

      if (canUpdate) {
        items.push({ type: "button", label: "» Update now (pulls, reinstalls, restarts)", onEnter: startUpdate });
      }
      items.push({ type: "button", label: "» Refresh", onEnter: (S) => S.reload() });
      items.push({ type: "content", html: `<div id="upd-log"></div>` });
      return { items };
    },
  },

  // Messaging: one conversation, delivered over WiFi + LoRa into a single store.
  // A message from a plain Meshtastic radio appears here like any other; the
  // per-message badge shows which transport(s) delivered it.
  messaging: {
    title: "Messaging",
    // A live WebSocket pushes new messages (see onEnter). `dynamic` stays as a
    // fallback poll: build() only fetches when the WS isn't delivering.
    dynamic: 3000,
    onEnter: (S) => msgWsOpen(S),
    onLeave: () => msgWsClose(),
    async build() {
      // When the WS is live, render straight from the pushed cache (no fetch).
      // Otherwise fall back to a one-shot history fetch into the same cache.
      let ok = true;
      if (!MSG_WS || MSG_WS_POLL) {
        const r = await jget("/api/v1/messaging/messages");
        ok = r.ok;
        if (ok) (r.d.messages || []).forEach(msgUpsert);
      }
      let h = `<div class="hint">Sends over WiFi + LoRa; the Via badge shows which transport delivered each message.</div>`;
      h += `<div class="content">`;
      if (!ok) {
        h += `<div class="warn">messaging service unavailable</div>`;
      } else if (!MSG_CACHE.length) {
        h += `<div class="off">no messages yet</div>`;
      } else {
        h += `<table><tr><th>When</th><th>From</th><th>Message</th><th>Via</th></tr>`;
        MSG_CACHE.slice(-100).forEach((m) => {
          const via = (m.transports || []).join("+") || "—";
          const who = m.mine ? "me" : esc(m.sender);
          h += `<tr><td>${ago(m.ts)}</td><td class="${m.mine ? "ok" : ""}">${who}</td>` +
            `<td>${esc(m.text)}</td><td>${esc(via)}</td></tr>`;
        });
        h += `</table>`;
      }
      h += `</div>`;
      return {
        items: [
          { type: "content", html: h },
          { type: "compose", key: "msg_text", value: MSG_DRAFT, max: 200,
            placeholder: "Type a message",
            sendLabel: "Send", onChange: (v) => MSG_DRAFT = v,
            onSubmit: (S, text) => sendMessage(S, text) },
        ],
      };
    },
  },

  // Voice (PTT) status + channel control. The full soft-PTT handset lives at
  // /voice; this page mirrors the daemon's control socket so an operator can
  // see mesh audio state and switch channel from the TUI.
  voice: {
    title: "Voice",
    dynamic: 5000,
    async build() {
      const { ok, d } = await jget("/api/v1/voice/status");
      let h = `<div class="content">`;
      const items = [{ type: "content", html: "" }];  // placeholder, filled below
      if (!ok) {
        h += `<div class="warn">voice daemon unavailable</div></div>`;
        items[0].html = h;
        items.push({ type: "button", label: "» Open soft-PTT handset (/voice)",
          onEnter: () => { location.href = "/voice"; } });
        return { items };
      }
      const talkers = (d.sources || []).length;
      h += `<div class="kv"><span>PTT</span>` +
        `<span class="${d.ptt ? "ok" : "off"}">${d.ptt ? "TX" : "idle"}</span></div>`;
      h += `<div class="kv"><span>Channel</span><span>${esc(d.channel)} ` +
        `(${esc(d.channel_label || "—")})</span></div>`;
      h += `<div class="kv"><span>Talkers</span>` +
        `<span class="${talkers ? "ok" : ""}">${talkers}</span></div>`;
      h += `<div class="kv"><span>Handset</span>` +
        `<span class="${d.hardware ? "ok" : "off"}">` +
        `${d.hardware ? "OpenVLM card " + esc(d.card) : "none (phone only)"}</span></div>`;
      const lora = d.lora || {};
      h += `<div class="kv"><span>Transport</span><span>${esc(d.transport || "ip")}</span></div>`;
      if (lora.enabled) {
        h += `<div class="kv"><span>LoRa STT</span>` +
          `<span class="${lora.ready ? "ok" : "warn"}">` +
          `${lora.ready ? esc(lora.stt || "ready") : "not ready"}</span></div>`;
      }
      h += `</div>`;
      items[0].html = h;

      // Channel selector — options come from the daemon's named channel list.
      VOICE_CH = { current: d.channel, list: d.channels || [] };
      const opts = VOICE_CH.list.map((c) => String(c.n));
      if (opts.length) {
        items.push({ type: "fselect", key: "voice_channel", label: "Set channel",
          options: opts, value: String(d.channel),
          onChange: (v) => VOICE_CH.current = parseInt(v, 10) });
        items.push({ type: "button", label: "» Switch channel", onEnter: setVoiceChannel });
      }
      items.push({ type: "button", label: "» Open soft-PTT handset (/voice)",
        onEnter: () => { location.href = "/voice"; } });
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

async function sendMessage(S, text) {
  text = (text != null ? text : MSG_DRAFT || "").trim();
  if (!text) return S.msg("type a message first", false);
  const { ok, d } = await jsend("POST", "/api/v1/messaging/messages", { text });
  if (!ok) return S.msg("send failed: " + (d.detail || "error"), false);
  MSG_DRAFT = "";
  S.msg("sent");
  // The WS push will echo our own message back; still reload for immediate view.
  return S.reload();
}

// ── Messaging live push (WebSocket) ────────────────────────────
// Opens a WS to the daemon-backed /api/v1/messaging/ws endpoint. New messages
// arrive as {event:"message",message:{...}}; the initial frame is the history.
// Falls back to the page's 3s poll if the socket can't be established.
function msgWsOpen(S) {
  msgWsClose();
  MSG_WS_POLL = false;
  let ws;
  try {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + "/api/v1/messaging/ws");
  } catch (e) {
    MSG_WS_POLL = true;
    return;
  }
  MSG_WS = ws;
  ws.onmessage = (ev) => {
    let obj;
    try { obj = JSON.parse(ev.data); } catch (e) { return; }
    if (obj.event === "history") {
      MSG_CACHE = [];
      (obj.messages || []).forEach(msgUpsert);
    } else if (obj.event === "message") {
      msgUpsert(obj.message);
    } else return;
    if (S) S.reload();
  };
  ws.onclose = () => {
    // Fall back to polling only if this socket is still the active one.
    if (MSG_WS === ws) { MSG_WS = null; MSG_WS_POLL = true; }
  };
  ws.onerror = () => { MSG_WS_POLL = true; };
}

function msgWsClose() {
  if (MSG_WS) {
    try { MSG_WS.onclose = null; MSG_WS.close(); } catch (e) {}
    MSG_WS = null;
  }
}

async function setVoiceChannel(S) {
  if (!VOICE_CH) return S.msg("no channel selected", false);
  const n = parseInt(VOICE_CH.current, 10);
  if (Number.isNaN(n)) return S.msg("pick a channel first", false);
  const { ok, d } = await jsend("POST", "/api/v1/voice/channel", { n });
  if (!ok) return S.msg("switch failed: " + (d.detail || "error"), false);
  S.msg("channel " + n);
  return S.reload();
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

// ── TAK Server actions ─────────────────────────────────────────
// Trigger a browser download of a staged client cert. The API serves the .p12
// as an attachment; a hidden anchor click starts the download on the device.
function takDownload(name) {
  const a = document.createElement("a");
  a.href = "/api/v1/tak/certs/" + encodeURIComponent(name);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ── Tailscale (VPN) actions ────────────────────────────────────
// Show a browser-login URL in the page (auth slot) so an operator can complete
// login on any device. Cleared on the next successful connect.
function tsShowAuthUrl(url) {
  const el = document.getElementById("ts-auth");
  if (el) el.innerHTML = `<div class="kv"><span>login: ` +
    `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(url)}</a></span></div>`;
}

async function tsUp(S) {
  S.msg("connecting…");
  const { ok, d } = await jsend("POST", "/api/v1/tailscale/up", {});
  if (!ok) return S.msg("connect failed: " + (d.detail || "error"), false);
  if (d.auth_url) { tsShowAuthUrl(d.auth_url); return S.msg("open the login URL to authenticate", false); }
  S.msg("connected");
  return S.reload();
}

async function tsUpKey(S) {
  const key = (TS_AUTHKEY || "").trim();
  if (!key) return S.msg("paste an auth key first (or use Connect for browser login)", false);
  S.msg("joining tailnet…");
  const { ok, d } = await jsend("POST", "/api/v1/tailscale/up", { authkey: key });
  if (!ok) return S.msg("join failed: " + (d.detail || "error"), false);
  TS_AUTHKEY = "";
  if (d.auth_url) { tsShowAuthUrl(d.auth_url); return S.msg("open the login URL to authenticate", false); }
  S.msg("connected");
  return S.reload();
}

async function tsDown(S) {
  S.msg("disconnecting…");
  const { ok, d } = await jsend("POST", "/api/v1/tailscale/down");
  if (!ok) return S.msg("disconnect failed: " + (d.detail || "error"), false);
  S.msg("disconnected");
  return S.reload();
}

async function tsLogout(S) {
  if (!confirm("Log out of the current tailnet? You'll need an auth key or " +
    "browser login to reconnect.")) return;
  const { ok, d } = await jsend("POST", "/api/v1/tailscale/logout");
  if (!ok) return S.msg("logout failed: " + (d.detail || "error"), false);
  TS_ACCT = null;
  S.msg("logged out");
  return S.reload();
}

async function tsSwitch(S) {
  if (!TS_ACCT) return S.msg("pick a tailnet first", false);
  S.msg("switching…");
  const { ok, d } = await jsend("POST", "/api/v1/tailscale/switch", { account: TS_ACCT });
  if (!ok) return S.msg("switch failed: " + (d.detail || "error"), false);
  S.msg("switched to " + TS_ACCT);
  return S.reload();
}

// Launch the node update, then poll progress. The update restarts nucleusd
// mid-run, so the API may briefly drop; the on-disk status file is the source
// of truth and survives the restart, so we tolerate transient fetch failures.
async function startUpdate(S) {
  if (!confirm("Update this node? Pulls the latest code, reinstalls, and " +
    "restarts services. The web UI may briefly disconnect.")) return;
  S.msg("starting update…");
  const { ok, d } = await jsend("POST", "/api/v1/update/start");
  if (!(ok && d.started)) return S.msg("start failed: " + (d.detail || "error"), false);

  const logEl = () => document.getElementById("upd-log");
  for (let i = 0; i < 300; i++) {          // up to ~10 min
    await new Promise((r) => setTimeout(r, 2000));
    let p;
    try { p = (await jget("/api/v1/update/progress")).d; }
    catch (e) { S.msg("update running… (web UI restarting)"); continue; }
    const el = logEl();
    if (el && p.log) {
      el.innerHTML = `<div class="content"><pre class="log">` +
        esc(p.log.join("\n")) + `</pre></div>`;
    }
    if (p.status === "finished") {
      const good = p.rc === 0 || p.rc === 1;
      S.msg(p.message || ("finished (rc " + p.rc + ")"), good);
      return;
    }
    S.msg(`updating… (${(i + 1) * 2}s)`);
  }
  S.msg("update still running — check again shortly", false);
}

