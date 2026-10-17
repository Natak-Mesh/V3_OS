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

// Human byte + duration formatting for the Reticulum monitor.
function fmtBytes(n) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v.toFixed(0) : v.toFixed(1)) + " " + u[i];
}
function fmtDur(sec) {
  if (sec == null) return "—";
  let s = Math.floor(sec);
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60);
  if (d) return d + "d " + h + "h";
  if (h) return h + "h " + m + "m";
  if (m) return m + "m";
  return Math.floor(sec) + "s";
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
// Channel URL typed into the "Apply a channel URL" field — kept separate from M
// so editing it never overwrites this node's displayed current channel URL.
let MCHURL = "";
// Working copy of the presence-heartbeat node config (separate from radio).
let HB = null;
// Working copy of the per-UID TX rate limit (node config, read live by cot-bridge).
let TXR = null;
// Last wifi-mesh peer query result ({peers, local}); rendered as page items so
// each joinable peer is a selectable row that survives rebuilds.
let PEERS = null;
// Working copy of the full node config, edited on the CONFIG page.
let CFG = null;
// Working copy of the HaLow settings (halow.*), edited on the HALOW page.
let HL = null;
// Whether the HaLow channel-reference <details> is expanded. Kept here (not in
// the DOM) so the page's 5s dynamic rebuild doesn't snap the section shut.
let HL_REF_OPEN = false;
window.__halowRefToggle = (open) => { HL_REF_OPEN = !!open; };
// Working copy of the RNode LoRa radio settings (reticulum.rnode_*), edited on
// the RETICULUM > INTERFACES > CONFIGURE RNODE page.
let RN = null;
// JSON schema of ReticulumConfig (field bounds/enums/descriptions), fetched from
// /api/v1/config/schema so the RNode page reads its limits from the one contract
// (schema.py) instead of hardcoding a second copy.
let RN_SCHEMA = null;
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

// Reticulum/LXMF direct-message state. Separate from the broadcast chat above:
// DMs are per-peer, keyed by the peer's lxmf.delivery hash.
let RNS_DRAFT = "";
let RNS_ADD_HASH = "";     // draft: lxmf.delivery address to add a contact by
let RNS_ADD_NAME = "";     // draft: optional nickname for the add-by-hash above
let RNS_CACHE = {};        // peer hash -> ordered (oldest→newest) message list
let RNS_PATH_SEEN = {};    // peer hash -> epoch of the last operator path request
// Shorten an rnsd interface string to a readable label for the contact row.
// rnsd reports the real interface a path was learned on, e.g.
// "AutoInterfacePeer[wlan1/fe80::…]", "TCPInterface[Entry Node/1.2.3.4:4243]",
// "RNodeInterface[LoRa]". We keep the interface TYPE and its configured
// name/first field so any transport (not just WiFi) is shown truthfully.
function rnsIface(raw) {
  if (!raw) return "unknown iface";
  const m = /^([A-Za-z0-9_]+?)(?:Peer)?\[(.+)\]$/.exec(raw);
  if (!m) return raw;
  const type = m[1].replace(/Interface$/, "");
  const inner = m[2].split("/")[0].trim();   // "wlan1", "Entry Node", "LoRa"
  return inner ? `${type}:${inner}` : type;
}
// Resolve a contact's display label: local nickname, else the LXMF announced
// name, else a short hash. Mirrors ContactStore.display_name on the server.
function rnsName(c) {
  if (!c) return "?";
  const nick = (c.name || "").trim();
  if (nick) return nick;
  const announced = (c.announced_name || "").trim();
  if (announced) return announced;
  return (c.lxmf_hash || "").slice(0, 10) || "?";
}
// Merge a DM into the per-peer cache (by peer+ts+direction; no server id).
function rnsUpsert(m) {
  if (!m || !m.peer) return;
  const list = RNS_CACHE[m.peer] || (RNS_CACHE[m.peer] = []);
  const i = list.findIndex((x) => x.ts === m.ts && x.direction === m.direction && x.text === m.text);
  if (i >= 0) list[i] = m; else list.push(m);
  list.sort((a, b) => a.ts - b.ts);
}

// Voice state: channel list cache for the TUI voice page.
let VOICE_CH = null;

// Tailscale page state: auth-key input draft + selected tailnet profile.
// TS_ACCT holds the chosen dropdown label; TS_ACCT_IDS maps label -> profile id
// (an account can be logged into multiple tailnets, so the id disambiguates).
let TS_AUTHKEY = "";
let TS_ACCT = null;
let TS_ACCT_IDS = {};

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
        { type: "nav", label: "RADIOS (RF ON/OFF)", to: "radios" },
        { type: "nav", label: "RETICULUM", to: "reticulum" },
        { type: "nav", label: "TAILSCALE (VPN)", to: "tailscale" },
        { type: "nav", label: "RADIO CONFIGURATION", to: "config" },
      ];
      // HaLow is a plug-in USB radio: show its config entry only when the
      // halow0 interface is present (radios.halow.present from live status).
      const { ok: sok, d: st } = await jget("/api/v1/status");
      if (sok && st.radios && st.radios.halow && st.radios.halow.present) {
        items.push({ type: "nav", label: "HALOW", to: "halow" });
      }
      // TAK Server is optional; show the menu entry only when it's installed.
      const { ok, d: tak } = await jget("/api/v1/tak/status");
      if (ok && tak.installed) {
        const label = tak.variant === "opentakserver" ? "OPENTAKSERVER" : "TAK SERVER";
        items.push({ type: "nav", label, to: "tak" });
      }
      items.push({ type: "nav", label: "SYSTEM UPDATE", to: "update" });
      items.push({ type: "nav", label: "POWER", to: "power" });
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
      const meshNodes = st.mesh_nodes || [];
      let h = "";

      h += `<div class="content"><div class="page-title" style="padding-left:0">Wifi mesh nodes</div>`;
      if (!meshNodes.length) {
        h += `<div class="off">no nodes — this node does not see any other node over wlan1</div>`;
      } else {
        h += `<table><tr><th>Node</th><th>Via</th><th>Cost</th><th>Seen</th></tr>`;
        meshNodes.forEach((r) => {
          // Lost nodes keep their row (with age) until dropped after 15 min.
          const via = !r.reachable ? "—" : r.direct ? "direct" : esc(r.via || "—");
          const cost = r.reachable ? (r.metric / 256).toFixed(1) : "—";
          const seen = r.reachable ? "now" : ago(r.last_seen);
          h += `<tr><td>${esc(r.node)}</td><td>${via}</td>` +
            `<td>${esc(cost)}</td>` +
            `<td class="${r.reachable ? "ok" : "warn"}">${esc(seen)}</td></tr>`;
        });
        h += `</table>`;
      }
      h += `</div>`;

      // HaLow (802.11ah) peers — only when the halow0 radio is present. These
      // are the radio-link neighbours (iw station dump), which Babel's route
      // list can hide when the same node is cheaper over wlan1.
      const halowRadio = (st.radios && st.radios.halow) || {};
      if (halowRadio.present) {
        const hp = st.halow_peers || [];
        h += `<div class="content"><div class="page-title" style="padding-left:0">HaLow mesh nodes</div>`;
        if (!hp.length) {
          h += `<div class="off">no HaLow peers — this node sees no other node over halow0</div>`;
        } else {
          h += `<table><tr><th>Node</th><th>Link</th><th>Signal</th><th>State</th><th>Seen</th></tr>`;
          hp.forEach((p) => {
            const node = p.ipv4 || p.mac;
            const link = p.link_pct != null ? p.link_pct + "%" : "—";
            const sig = p.signal_avg_dbm != null ? p.signal_avg_dbm + " dBm" : "—";
            const estab = p.plink === "ESTAB";
            const seen = p.inactive_ms != null ? ago(Date.now() / 1000 - p.inactive_ms / 1000) : "—";
            h += `<tr><td>${esc(node)}</td><td>${esc(link)}</td>` +
              `<td>${esc(sig)}</td>` +
              `<td class="${estab ? "ok" : "warn"}">${esc(p.plink || "—")}</td>` +
              `<td class="${estab ? "ok" : "warn"}">${esc(seen)}</td></tr>`;
          });
          h += `</table>`;
        }
        h += `</div>`;
      }

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
        h += `<tr><td>${esc(tak.unit || "takserver")}</td><td class="${tak.service === "active" ? "ok" : "off"}">${esc(tak.service)}</td></tr>`;
      }
      h += `</table></div>`;

      return { items: [{ type: "content", html: h }] };
    },
  },

  // TAK Server (optional): official (nucleus-tak-setup.sh) OR OpenTAKServer
  // (installer + nucleus-ots-fixup.sh). Cert downloads + web-UI pointer; the
  // menu entry is hidden when neither is installed.
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
      const host = location.hostname;

      if (s.variant === "opentakserver") {
        // OTS web UI is on 8444 (moved off 443 by nucleus-ots-fixup.sh). Link is
        // built from the host used to reach this UI, so no IP is needed.
        const url = `https://${host}:8444`;
        let head = `<div class="content"><div class="kv">` +
          `<span>server <b>OpenTAKServer</b></span>` +
          `<span>status <span class="${cls}">${esc(s.service)}</span></span></div>` +
          `<div class="kv"><span>web UI <a href="${esc(url)}" target="_blank" ` +
          `rel="noopener"><b>${esc(url)}</b></a></span></div>` +
          `<div class="off">Log in to the OpenTAKServer web UI to manage users. ` +
          `Install the truststore on client devices; they connect on port 8089 ` +
          `(TLS) and enroll on 8446.</div></div>`;
        items.push({ type: "content", html: head });
        items.push({ type: "button", label: "» OPEN OPENTAKSERVER WEB UI",
          onEnter: () => window.open(url, "_blank", "noopener") });

        const ts = (s.certs || []).filter((n) => n.startsWith("truststore"));
        if (!ts.length) {
          items.push({ type: "content", html: `<div class="content">` +
            `<div class="warn">no truststore staged — run sudo nucleus-ots-fixup.sh</div></div>` });
        } else {
          items.push({ type: "content", html: `<div class="content">` +
            `<div class="page-title" style="padding-left:0">Download truststore</div></div>` });
          ts.forEach((name) => {
            items.push({ type: "button", label: "» " + name,
              onEnter: () => takDownload(name) });
          });
        }
        return { items };
      }

      let head = `<div class="content"><div class="kv">` +
        `<span>status <span class="${cls}">${esc(s.service)}</span></span></div>`;
      // Web admin lives on TAK's own port (8443), not the Nucleus UI. Point the
      // operator there and remind them the admin cert must be imported first.
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

  // Radios: per-radio RF on/off — one line each with an inline ON/OFF switch.
  // Each radio is its own switch (no kill-all). Two persistence models:
  //   * Wi-Fi mesh + Meshtastic are CONFIG-BACKED (persist) -> PUT /config then
  //     POST /apply, same stage-then-apply flow as the config pages.
  //   * Access point is a LIVE toggle (resets ON at boot) -> POST /api/v1/rf/ap
  //     directly, like the Tailscale page drives the CLI outside the pipeline.
  // Live state comes from /api/v1/status (.radios). Selecting a row (Enter/tap),
  // or tapping its switch, toggles that radio.
  radios: {
    title: "Radios",
    dynamic: 5000,
    async build() {
      const { ok, d: st } = await jget("/api/v1/status");
      if (!ok || !st.radios) {
        return { items: [{ type: "content", html: `<div class="content">` +
          `<div class="off">radio status unavailable</div></div>` }] };
      }
      const r = st.radios;
      // An inline ON/OFF switch button: class + label reflect the live state;
      // on==null renders "—" (unknown).
      const sw = (on, toggle) => ({
        label: on == null ? "—" : (on ? "ON" : "OFF"),
        cls: on == null ? "sw-unknown" : (on ? "sw-on" : "sw-off"),
        onRun: (S) => toggle(S),
      });
      const mesh = r.mesh || {};
      const mt = r.meshtastic || {};
      const ap = r.ap || {};
      const meshOn = mesh.on !== false;
      return { items: [
        { type: "button", label: "Wi-Fi mesh",
          onEnter: (S) => rfSetMesh(S, !meshOn),
          action: sw(mesh.on, (S) => rfSetMesh(S, !meshOn)) },
        { type: "button", label: "Meshtastic",
          onEnter: (S) => rfSetMeshtasticTx(S, mt.receive_only),
          action: sw(mt.on, (S) => rfSetMeshtasticTx(S, mt.receive_only)) },
        { type: "button", label: "Access point",
          onEnter: (S) => rfSetAp(S, !ap.on),
          action: sw(ap.on, (S) => rfSetAp(S, !ap.on)) },
      ] };
    },
  },

  // HaLow (802.11ah) mesh: live status + config for the plug-in Morse USB radio.
  // Only reachable when halow0 is present (the menu entry is hidden otherwise).
  // Settings are CONFIG-BACKED: edits mutate HL (a copy of the halow block + a
  // couple of shared mesh keys) and are saved as one read-modify-write PUT then
  // applied, exactly like the Radios page. Valid channels/bandwidths come from
  // _derived.halow_channels (the schema), never a list hardcoded here.
  halow: {
    title: "HaLow",
    dynamic: 5000,
    async build() {
      const { d: st } = await jget("/api/v1/status");
      const radio = (st.radios && st.radios.halow) || {};

      // Load a working copy once (first open or after Reload). Keep the shared
      // mesh SSID/password/country read-only here — they live on the Config page.
      const { d: cfg } = await jget("/api/v1/config");
      const der = cfg._derived || {};
      const chans = der.halow_channels || {};
      if (!HL) {
        const hw = cfg.halow || {};
        HL = {
          enabled: hw.enabled !== false,
          bandwidth_mhz: hw.bandwidth_mhz ?? 1,
          channel: hw.channel ?? 27,
        };
      }
      // Bandwidth options are the keys of the schema channel map (1/2/4/8).
      const bws = Object.keys(chans).map((k) => parseInt(k, 10)).sort((a, b) => a - b);
      // Channel options for the currently-selected bandwidth.
      const chOpts = (chans[HL.bandwidth_mhz] || []).slice();

      // US S1G centre frequency for a channel number (matches schema:
      // halow_freq_mhz -> 902 + 0.5 * channel). Used for the live readout and
      // the reference table below — all MHz figures derive from the schema's
      // channel list, nothing is hard-coded.
      const centreMhz = (ch) => 902 + 0.5 * ch;
      const fmtMhz = (n) => (Math.round(n * 10) / 10).toString();

      // Live readout for the current selection: channel, centre and the band it
      // occupies (centre ± bandwidth/2).
      const curCentre = centreMhz(HL.channel);
      const curLo = curCentre - HL.bandwidth_mhz / 2;
      const curHi = curCentre + HL.bandwidth_mhz / 2;
      const readoutHtml =
        `<div class="content"><div class="hint" style="padding-left:0">` +
        `ch <b>${esc(HL.channel)}</b> · <b>${esc(fmtMhz(curCentre))} MHz</b> centre · ` +
        `${esc(fmtMhz(curLo))}–${esc(fmtMhz(curHi))} MHz · ` +
        `<b>${esc(HL.bandwidth_mhz)} MHz</b> wide</div></div>`;

      // Reference list (US band): one row per channel, built from the schema
      // channel list — channel number, bandwidth, centre and occupied band.
      // Collapsed into a <details> so it's out of the way until opened; the open
      // state is held in HL_REF_OPEN so the 5s rebuild doesn't close it.
      let refRows = "";
      for (const bw of bws) {
        for (const ch of (chans[bw] || [])) {
          const c = centreMhz(ch);
          refRows +=
            `<tr><td>${esc(ch)}</td><td>${esc(bw)} MHz</td>` +
            `<td>${esc(fmtMhz(c - bw / 2))}–${esc(fmtMhz(c + bw / 2))} MHz</td></tr>`;
        }
      }
      const refHtml =
        `<div class="content"><details${HL_REF_OPEN ? " open" : ""} ` +
        `ontoggle="window.__halowRefToggle(this.open)">` +
        `<summary>Channel reference (US)</summary>` +
        `<table><tr><td><b>Ch</b></td><td><b>BW</b></td>` +
        `<td><b>Band</b></td></tr>` + refRows + `</table></details>` +
        `<div class="hint" style="padding-left:0">All nodes in the mesh must ` +
        `use the same bandwidth and channel.</div></div>`;

      const head = (title, sub) => ({ type: "content", html:
        `<div class="content"><div class="page-title" style="padding-left:0">${esc(title)}</div>` +
        (sub ? `<div class="hint" style="padding-left:0">${sub}</div>` : "") + `</div>` });

      // Live status block.
      const svc = radio.service || "unknown";
      const statusHtml =
        `<div class="content"><table>` +
        `<tr><td>Interface</td><td class="${radio.present ? "ok" : "off"}">` +
          `${radio.present ? "present (halow0)" : "absent"}</td></tr>` +
        `<tr><td>IP</td><td>${esc(der.halow_ip || "—")}</td></tr>` +
        `<tr><td>Channel</td><td>${radio.channel != null ? esc(radio.channel) : "—"}` +
          `${radio.freq_mhz != null ? " (" + esc(radio.freq_mhz) + " MHz)" : ""}</td></tr>` +
        `<tr><td>Bandwidth</td><td>${radio.bandwidth_mhz != null ? esc(radio.bandwidth_mhz) + " MHz" : "—"}</td></tr>` +
        `<tr><td>Service</td><td class="${svc === "active" ? "ok" : "off"}">${esc(svc)}</td></tr>` +
        `</table></div>`;

      const items = [
        head("Status"),
        { type: "content", html: statusHtml },

        head("Settings", "SSID, password and country are shared with the Wi-Fi " +
          "mesh — change them on the Radio Configuration page."),
        { type: "fselect", label: "Enabled", options: ["on", "off"],
          value: HL.enabled ? "on" : "off",
          onChange: (v) => HL.enabled = (v === "on") },
        { type: "fselect", label: "Bandwidth (MHz)", options: bws,
          value: HL.bandwidth_mhz,
          onChange: (v) => {
            HL.bandwidth_mhz = parseInt(v, 10);
            // Keep channel valid for the new bandwidth.
            const opts = chans[HL.bandwidth_mhz] || [];
            if (!opts.includes(HL.channel)) HL.channel = opts[0];
          } },
        { type: "fselect", label: "Channel (S1G)", options: chOpts,
          value: HL.channel, onChange: (v) => HL.channel = parseInt(v, 10) },
        { type: "content", html: readoutHtml },
        { type: "content", html: refHtml },

        head("SSID (read-only)", `<b>${esc(der.halow_ssid || "—")}</b> · country ` +
          `<b>${esc((cfg.mesh && cfg.mesh.country) || "—")}</b>`),

        { type: "button", label: "» Deploy", onEnter: deployHalow },
      ];
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
        // Label each option by tailnet + account so profiles are distinguishable
        // even when one account is logged into several tailnets; map back to the
        // unique profile id, which is what we send to /switch.
        TS_ACCT_IDS = {};
        const opts = accts.map((a) => {
          const label = `${a.tailnet || "?"} — ${a.account}` + (a.active ? " *" : "");
          TS_ACCT_IDS[label] = a.id;
          return label;
        });
        const activeAcct = accts.find((a) => a.active) || accts[0];
        if (!TS_ACCT || !(TS_ACCT in TS_ACCT_IDS)) {
          TS_ACCT = opts[accts.indexOf(activeAcct)];
        }
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

  // Reticulum (rnsd) read-only monitor + hub: rnsd up/down, uptime and path
  // count; this node's messaging address with manual announce; the configured
  // interfaces (traffic + clients); and links to contacts and add-contacts.
  // Data comes from the shared-instance control socket via /api/v1/reticulum
  // and the messaging daemon via /api/v1/messaging/rns.
  reticulum: {
    title: "Reticulum",
    dynamic: 5000,
    async build() {
      const { ok, d: s } = await jget("/api/v1/reticulum/status");
      const items = [];

      if (!ok || !s.running) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="off">rnsd is not running — no Reticulum status available</div></div>` });
        return { items };
      }

      // ── status ───────────────────────────────────────────────
      const t = s.transport || {};
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="kv"><span>rnsd <b class="ok">up</b></span>` +
        `<span>uptime <b>${t.uptime != null ? fmtDur(t.uptime) : "—"}</b></span>` +
        `<span>paths <b>${s.path_count}</b></span></div></div>` });

      // ── my identity + announce ───────────────────────────────
      const ms = await jget("/api/v1/messaging/rns/status");
      const m = ms.ok ? ms.d : {};
      if (m.enabled) {
        let id = `<div class="content"><div class="page-title" style="padding-left:0">My identity</div>`;
        id += `<div class="kv"><span>announce <b>${esc(m.announce_mode || "manual")}</b></span></div></div>`;
        items.push({ type: "content", html: id });
        items.push({ type: "button", label: "» Announce now (make me discoverable)", onEnter: rnsAnnounce });
      }

      // ── contacts / add links ─────────────────────────────────
      const r = await jget("/api/v1/messaging/rns/contacts");
      const n = (r.ok && r.d.contacts) ? r.d.contacts.length : 0;
      items.push({ type: "nav", label: `» DIRECT MESSAGES (${n} contact${n === 1 ? "" : "s"})`, to: "rns_nodes" });
      items.push({ type: "nav", label: "» ADD CONTACTS (my card / mesh / link)", to: "rns_add" });
      items.push({ type: "nav", label: "» INTERFACES", to: "rns_ifaces" });

      return { items };
    },
  },

  // Reticulum interfaces (rnsd): per-interface traffic + client counts. Split
  // off the main Reticulum page to keep that page focused on status/contacts.
  // Data comes from the shared-instance control socket via /api/v1/reticulum.
  rns_ifaces: {
    title: "Interfaces",
    dynamic: 5000,
    async build() {
      const { ok, d: s } = await jget("/api/v1/reticulum/status");
      const items = [];
      if (!ok || !s.running) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="off">rnsd is not running — no Reticulum status available</div></div>` });
        return { items };
      }
      // Hide the internal Shared Instance row.
      const ifaces = (s.interfaces || []).filter((i) =>
        !(i.short_name || i.name || "").startsWith("Shared Instance"));
      let h = `<div class="content"><div class="page-title" style="padding-left:0">Interfaces</div>`;
      if (!ifaces.length) {
        h += `<div class="off">no interfaces configured</div>`;
      } else {
        h += `<table><tr><th>Interface</th><th>Mode</th><th>State</th>` +
          `<th>RX</th><th>TX</th><th>Clients</th></tr>`;
        ifaces.forEach((i) => {
          const cls = i.up ? "ok" : "warn";
          const clients = i.clients != null ? i.clients : "—";
          h += `<tr><td>${esc(i.short_name || i.name)}</td>` +
            `<td>${esc(i.mode)}</td>` +
            `<td class="${cls}">${i.up ? "up" : "down"}</td>` +
            `<td>${fmtBytes(i.rxb)}</td><td>${fmtBytes(i.txb)}</td>` +
            `<td>${esc(clients)}</td></tr>`;
        });
        h += `</table>`;
      }
      h += `</div>`;
      items.push({ type: "content", html: h });
      items.push({ type: "nav", label: "» CONFIGURE RNODE (LoRa radio)", to: "rns_rnode" });
      return { items };
    },
  },

  // Reticulum RNode LoRa radio settings (reticulum.rnode_*). Field types, bounds
  // and the bandwidth options come from /api/v1/config/schema (the schema.py
  // contract) — nothing here is hardcoded. Values come from /api/v1/config. Save
  // is a read-modify-write PUT of only the rnode_* keys; Apply rewrites the rnsd
  // config and restarts only rnsd.
  rns_rnode: {
    title: "RNode LoRa",
    async build() {
      if (!RN_SCHEMA) {
        const { ok, d } = await jget("/api/v1/config/schema");
        if (!ok) return { items: [{ type: "content", html:
          `<div class="content"><div class="off">could not load config schema</div></div>` }] };
        RN_SCHEMA = ((d.$defs || {}).ReticulumConfig || {}).properties || {};
      }
      if (!RN) {
        const { d: cfg } = await jget("/api/v1/config");
        const r = cfg.reticulum || {};
        RN = {
          enabled: r.rnode_enabled !== false,
          frequency: r.rnode_frequency,
          bandwidth: r.rnode_bandwidth,
          txpower: r.rnode_txpower,
          spreadingfactor: r.rnode_spreadingfactor,
          codingrate: r.rnode_codingrate,
        };
      }
      const P = RN_SCHEMA;
      // Build an editable row from a schema property: enum -> fselect, bounded
      // integer -> fnum, wide-range integer -> ftext. Label + hint from the
      // schema's title/description so the UI carries no copy of the contract.
      const numRow = (key, prop) => ({
        type: "fnum", label: prop.title || key, value: RN[key],
        min: prop.minimum, max: prop.maximum, step: 1,
        onChange: (v) => RN[key] = v,
      });
      const items = [
        { type: "content", html: `<div class="content"><div class="hint" ` +
          `style="padding-left:0">USB LoRa radio for Reticulum. All nodes on this ` +
          `link must use identical radio settings. Save, then Apply to restart ` +
          `rnsd.</div></div>` },
        { type: "fselect", label: "RNode", options: ["on", "off"],
          value: RN.enabled ? "on" : "off", onChange: (v) => RN.enabled = (v === "on") },
      ];
      // Frequency: integer Hz with a wide range -> free text (stepping Hz by 1 is
      // useless). Shown/edited in MHz for legibility; stored as Hz on save.
      items.push({ type: "ftext", label: "Frequency (MHz)",
        value: RN.frequency != null ? (RN.frequency / 1e6) : "",
        onChange: (v) => { const f = Math.round(parseFloat(v) * 1e6);
          RN.frequency = Number.isNaN(f) ? RN.frequency : f; } });
      // Bandwidth: schema enum -> dropdown, rendered in kHz.
      const bw = P.rnode_bandwidth || {};
      items.push({ type: "fselect", label: "Bandwidth (kHz)",
        options: (bw.enum || []).map((n) => String(n / 1000)),
        value: RN.bandwidth != null ? String(RN.bandwidth / 1000) : "",
        onChange: (v) => RN.bandwidth = Math.round(parseFloat(v) * 1000) });
      items.push(numRow("spreadingfactor", P.rnode_spreadingfactor || {}));
      items.push(numRow("codingrate", P.rnode_codingrate || {}));
      items.push(numRow("txpower", P.rnode_txpower || {}));
      items.push({ type: "button", label: "» Save (not applied)", onEnter: saveRnode });
      items.push({ type: "button", label: "» Apply", onEnter: (S) => applyCfg(S, false) });
      items.push({ type: "button", label: "» Reload", onEnter: (S) => { RN = null; return S.reload(); } });
      return { items };
    },
  },

  // Meshtastic: a menu of sub-pages. Each setting group is its own page so the
  // operator sees one thing at a time (Enter to open, Back to return), instead
  // of several collapsible sections stacked on one screen.
  meshtastic: {
    title: "Meshtastic Radio",
    async build() {
      const { d: s } = await jget(MB + "/status");
      const statusLine =
        `radio: ${s.radio_detected ? "detected" : "not detected"} · ` +
        `bridge: ${s.service_active ? "running" : "stopped"}` +
        (s.bridge_enabled ? " (enabled)" : " (disabled)");
      return {
        items: [
          { type: "content", html: `<div class="content"><div class="kv">` +
            `<span>${esc(statusLine)}</span></div></div>` },
          { type: "nav", label: "RADIO CONFIGURATION", to: "mt_radio" },
          { type: "nav", label: "SET CHANNEL", to: "mt_channel" },
          { type: "nav", label: "PRESENCE HEARTBEAT", to: "mt_heartbeat" },
          { type: "nav", label: "CoT TX RATE LIMIT", to: "mt_txrate" },
        ],
      };
    },
  },

  // Radio configuration: the fields written to the radio by hand, plus read /
  // apply. Loads the cached radio config (M) on entry if not already loaded.
  mt_radio: {
    title: "Radio Configuration",
    async build() {
      if (!M) {
        const { d } = await jget(MB + "/config");
        if (d.config) meshFillFrom(d.config);
      }
      const items = [{
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
        );
      }
      return { items };
    },
  },

  // Set channel: a small menu into three focused sub-pages so each task
  // (share / apply / join) has its own uncluttered screen.
  mt_channel: {
    title: "Set Channel",
    async build() {
      return {
        items: [
          { type: "nav", label: "SHARE CHANNEL", to: "mt_ch_share" },
          { type: "nav", label: "APPLY CHANNEL URL", to: "mt_ch_apply" },
          { type: "nav", label: "JOIN PEER CHANNEL", to: "mt_ch_join" },
        ],
      };
    },
  },

  // Share: this node's current channel URL + QR code, shown on open. The URL
  // sits in a .content box so it is selectable (the page body disables select).
  mt_ch_share: {
    title: "Share Channel",
    async build() {
      if (!M) {
        const { d } = await jget(MB + "/config");
        if (d.config) meshFillFrom(d.config);
      }
      if (!M || !M.channel_url) {
        return { items: [{ type: "content", html: `<div class="content"><div class="hint" ` +
          `style="padding-left:0">No channel URL yet — read config first.</div></div>` }] };
      }
      let qr = "";
      try { qr = await (await fetch(MB + "/config/qr")).text(); } catch (e) {}
      return {
        items: [{ type: "content", html:
          `<div class="content"><pre>${esc(M.channel_url)}</pre>` +
          (qr ? `<div class="qr">${qr}</div>` : "") + `</div>` }],
      };
    },
  },

  // Apply: paste a channel URL and write it to this radio.
  mt_ch_apply: {
    title: "Apply Channel URL",
    async build() {
      return {
        items: [
          { type: "ftext", key: "apply_channel_url", label: "Paste channel URL here", value: MCHURL, placeholder: "select to paste a channel URL", onChange: (v) => MCHURL = v },
          { type: "button", label: "» Apply channel URL", onEnter: meshImportUrl },
          { type: "content", html: `<div class="content"><div class="hint" ` +
            `style="padding-left:0">Radio reboots after apply.</div></div>` },
        ],
      };
    },
  },

  // Join: copy a Nucleus peer's channel over the wifi mesh.
  mt_ch_join: {
    title: "Join Peer Channel",
    async build() {
      return {
        items: [
          { type: "button", label: "» Query Nucleus peers for channels", onEnter: loadPeers },
          { type: "content", html: `<div class="content"><div class="hint" ` +
            `style="padding-left:0">Copies a peer's channel identity (name, key, region, ` +
            `preset, slot) to this radio. Role, TX power and hop limit are untouched. ` +
            `Radio reboots after joining.</div></div>` },
          ...peerItems(),
        ],
      };
    },
  },

  // Presence heartbeat (node config, not radio) — kept in HB, saved via
  // /api/v1/config. cot-bridge reads it live, so no radio reboot.
  mt_heartbeat: {
    title: "Presence Heartbeat",
    async build() {
      if (!HB) {
        const { d: cfg } = await jget("/api/v1/config");
        const hb = (cfg.meshtastic && cfg.meshtastic.heartbeat) || {};
        HB = { enabled: hb.enabled !== false, interval_secs: hb.interval_secs ?? 300 };
      }
      return {
        items: [
          { type: "content", html: `<div class="content"><div class="hint" ` +
            `style="padding-left:0">Keeps this node in peers' LoRa list without ATAK ` +
            `traffic. Nodes drop off after 15 min of silence.</div></div>` },
          { type: "fselect", key: "hb_enabled", label: "Heartbeat", options: ["on", "off"],
            value: HB.enabled ? "on" : "off", onChange: (v) => HB.enabled = (v === "on") },
          { type: "fnum", key: "hb_interval", label: "Interval (s)", value: HB.interval_secs,
            min: 60, max: 3600, step: 60, onChange: (v) => HB.interval_secs = v },
          { type: "button", label: "» Save heartbeat", onEnter: saveHeartbeat },
          { type: "button", label: "» Send heartbeat now", onEnter: sendHeartbeatNow },
        ],
      };
    },
  },

  // CoT TX rate limit (node config, not radio) — cot-bridge reads it live.
  mt_txrate: {
    title: "CoT TX Rate Limit",
    async build() {
      if (TXR === null) {
        const { d: cfg } = await jget("/api/v1/config");
        const mt = cfg.meshtastic || {};
        TXR = mt.tx_min_interval_secs ?? 30;
      }
      return {
        items: [
          { type: "content", html: `<div class="content"><div class="hint" ` +
            `style="padding-left:0">Min seconds between LoRa transmissions of the same CoT ` +
            `UID. 0 disables the limit.</div></div>` },
          { type: "fnum", key: "tx_min_interval", label: "TX rate limit (s)", value: TXR,
            min: 0, max: 3600, step: 1, onChange: (v) => TXR = v },
          { type: "button", label: "» Save TX rate limit", onEnter: saveTxRateLimit },
        ],
      };
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
      const web = CFG.web = CFG.web || {};

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
          value: mt.hat || "rak6421-slot2", onChange: (v) => mt.hat = v },
        { type: "fselect", label: "GPS", options: ["off", "uart", "i2c"],
          value: mt.gps || "uart", onChange: (v) => mt.gps = v },
        { type: "ftext", label: "GPS serial path", value: mt.gps_serial_path || "/dev/ttyS0",
          onChange: (v) => mt.gps_serial_path = v },
        { type: "ftext", label: "I2C device", value: mt.i2c_device || "/dev/i2c-1",
          onChange: (v) => mt.i2c_device = v },
        { type: "fselect", label: "CoT bridge", options: ["on", "off"],
          value: mt.cot_bridge !== false ? "on" : "off",
          onChange: (v) => mt.cot_bridge = (v === "on") },

        head("Web UI", "Password only required over the ethernet port; " +
          "mesh/AP/Tailscale clients are trusted. Change the default (see manual)."),
        { type: "ftext", label: "Web password (blank = keep current)",
          value: web.password || "",
          onChange: (v) => web.password = v },
        { type: "fselect", label: "Allow web UI over ethernet",
          options: ["on", "off"],
          value: web.eth0_access !== false ? "on" : "off",
          onChange: (v) => web.eth0_access = (v === "on") },

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

  // Power: reboot / power off this node. Each action confirms first (see
  // powerOp) because it takes the node — and its mesh links — down immediately.
  power: {
    title: "Power",
    async build() {
      return {
        items: [
          { type: "content", html: `<div class="content"><div class="kv">` +
            `<span class="warn">These actions take this node down immediately.` +
            ` A power off needs physical access to bring it back.</span></div></div>` },
          { type: "button", label: "» Reboot", onEnter: (S) => powerOp(S, "reboot") },
          { type: "button", label: "» Power off", onEnter: (S) => powerOp(S, "poweroff") },
        ],
      };
    },
  },

  // Messaging: one conversation, delivered over WiFi + LoRa into a single store.
  // A message from a plain Meshtastic radio appears here like any other; the
  // per-message badge shows which transport(s) delivered it.
  messaging: {
    title: "Messaging",
    // Keep the viewport pinned to the newest message as the log grows, unless
    // the operator has scrolled up to read history (see render() in cli.js).
    stickBottom: true,
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

  // Reticulum/LXMF Direct Messages: the saved contacts, each opening a
  // conversation. Node status, this node's address and the Announce button are
  // on the main Reticulum page; adding new contacts (my card, mesh nodes, paste
  // link) lives on the Add Contacts page (rns_add), not here.
  rns_nodes: {
    title: "Direct Messages",
    dynamic: 5000,
    onEnter: (S) => msgWsOpen(S),
    onLeave: () => msgWsClose(),
    async build() {
      const st = await jget("/api/v1/messaging/rns/status");
      const items = [];
      const s = st.ok ? st.d : {};
      if (!s.enabled) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="off">Reticulum messaging is disabled</div>` +
          `<div class="hint" style="padding-left:0">Enable messaging.rns.enabled ` +
          `in the config, then apply.</div></div>` });
        return { items };
      }

      // ── saved contacts ──────────────────────────────────────
      const r = await jget("/api/v1/messaging/rns/contacts");
      const contacts = (r.ok && r.d.contacts) ? r.d.contacts : [];
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="page-title" style="padding-left:0">Contacts</div>` +
        (contacts.length ? "" : `<div class="off">no contacts yet — add one on the Add Contacts page</div>`) + `</div>` });
      // Path state per contact (known/hops + age) is shown on the same row, with
      // an inline REQ PATH button. Reads are parallel and emit no RNS traffic.
      const paths = await Promise.all(contacts.map((c) =>
        jget("/api/v1/messaging/rns/contacts/" + encodeURIComponent(c.lxmf_hash) + "/path")));
      contacts.forEach((c, i) => {
        const who = rnsName(c);
        const key = c.pubkey ? "" : "  ·  no key yet";
        const pr = paths[i];
        const path = (pr && pr.ok && pr.d.path) ? pr.d.path : { known: false };
        let pstr;
        if (path.known) {
          // Show the ACTUAL Reticulum interface the path was learned on (any
          // transport — RNode, TCP, AutoInterface, …), not an assumed "mesh".
          const via = rnsIface(path.interface);
          const hops = path.hops != null
            ? `${path.hops} hop${path.hops === 1 ? "" : "s"}` : "? hops";
          const age = path.updated ? `, ${ago(path.updated)}` : "";
          pstr = `${via} · ${hops}${age}`;
        } else {
          const seen = RNS_PATH_SEEN[c.lxmf_hash];
          pstr = seen ? `no path · req ${ago(seen)} ago` : "no path";
        }
        items.push({
          type: "nav",
          label: `${who}${key}  ·  ${pstr}`,
          to: "rns_chat",
          onEnter: (S) => S.go("rns_chat", { hash: c.lxmf_hash, host: who }),
          action: { label: "req path", onRun: (S) => rnsRequestPath(S, c.lxmf_hash) },
        });
      });

      // Rename/remove live off the message log — they're contact admin, not
      // messaging — so they sit behind one page reached from here.
      if (contacts.length) {
        items.push({ type: "nav", label: "» Modify contacts", to: "rns_edit" });
      }

      return { items };
    },
  },

  // Modify Contacts: rename or remove any saved contact. Kept off the per-peer
  // message log (rns_chat) so the log is messaging only. Both actions transmit
  // nothing — they just edit/forget the local contact store.
  rns_edit: {
    title: "Modify Contacts",
    async build() {
      const items = [];
      const r = await jget("/api/v1/messaging/rns/contacts");
      const contacts = (r.ok && r.d.contacts) ? r.d.contacts : [];
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="page-title" style="padding-left:0">Modify contacts</div>` +
        `<div class="hint" style="padding-left:0">Rename or remove a saved contact.</div>` +
        (contacts.length ? "" : `<div class="off">no contacts yet</div>`) + `</div>` });
      contacts.forEach((c) => {
        const who = rnsName(c);
        const hash = (c.lxmf_hash || "").slice(0, 10);
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="kv"><span>${esc(who)}</span><span class="hint">${esc(hash)}</span></div></div>` });
        items.push({ type: "button", label: `» Rename ${who}`,
          onEnter: (S) => rnsRename(S, c.lxmf_hash, who) });
        items.push({ type: "button", label: `» Remove ${who}`,
          onEnter: (S) => rnsRemove(S, c.lxmf_hash, who) });
      });
      return { items };
    },
  },

  // Add Contacts: add any LXMF contact by its destination hash (the standard
  // Reticulum address every client shows), with an optional nickname; plus a
  // one-tap Add for Nucleus nodes found on the WiFi mesh. This node's own
  // destination hash to share is in the "Share me" section at the top.
  rns_add: {
    title: "Add Contacts",
    async build() {
      const st = await jget("/api/v1/messaging/rns/status");
      const items = [];
      const s = st.ok ? st.d : {};
      if (!s.enabled) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="off">Reticulum messaging is disabled</div>` +
          `<div class="hint" style="padding-left:0">Enable messaging.rns.enabled ` +
          `in the config, then apply.</div></div>` });
        return { items };
      }
      // ── share: my destination hash ───────────────────────────
      let share = `<div class="content"><div class="page-title" style="padding-left:0">Share me</div>`;
      share += `<div class="hint" style="padding-left:0">Give this to anyone who wants to ` +
        `message you — it works in Sideband, MeshChat, NomadNet or another Nucleus node.</div>`;
      if (s.address) {
        share += `<div class="kv"><span>my lxmf.delivery destination hash</span></div>` +
          `<div class="hint" style="padding-left:0;word-break:break-all"><b>${esc(s.address)}</b></div>`;
      } else {
        share += `<div class="off">destination hash not ready — rnsd/lane still starting</div>`;
      }
      share += `</div>`;
      items.push({ type: "content", html: share });

      // ── add by destination hash (keyless, transmits nothing) ──
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="page-title" style="padding-left:0">Add a contact</div>` +
        `<div class="hint" style="padding-left:0">Paste their lxmf.delivery destination hash ` +
        `(32 hex chars) and an optional nickname. Saved with no key yet — press ` +
        `<b>req path</b> on the contact to fetch its key. Nothing is sent.</div></div>` });
      items.push({ type: "ftext", label: "Destination hash", value: RNS_ADD_HASH, max: 64,
        onChange: (v) => RNS_ADD_HASH = v });
      items.push({ type: "ftext", label: "Nickname", value: RNS_ADD_NAME, max: 40,
        onChange: (v) => RNS_ADD_NAME = v });
      items.push({ type: "button", label: "» Add contact", onEnter: rnsAddHash });

      // ── Nucleus nodes on the WiFi mesh (pull their cards) ────
      const r = await jget("/api/v1/messaging/rns/contacts");
      const contacts = (r.ok && r.d.contacts) ? r.d.contacts : [];
      const mp = await jget("/api/v1/messaging/rns/mesh-peers");
      const mesh = (mp.ok && mp.d.peers) ? mp.d.peers : [];
      const known = new Set(contacts.map((c) => c.lxmf_hash));
      const addable = mesh.filter((p) => p.card && !known.has(p.card.lxmf_hash));
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="page-title" style="padding-left:0">On the WiFi mesh</div>` +
        (addable.length ? "" : `<div class="off">no new nodes found on the mesh</div>`) + `</div>` });
      addable.forEach((p) => {
        const who = p.card.name || String(p.card.id || p.ip);
        items.push({ type: "button", label: `» Add ${who} (${p.ip})`,
          onEnter: (S) => rnsAddMesh(S, p.card) });
      });
      return { items };
    },
  },

  // Reticulum/LXMF per-peer conversation. params: {hash, host}.
  rns_chat: {
    title: "DM",
    stickBottom: true,
    dynamic: 3000,
    onEnter: (S) => msgWsOpen(S),
    onLeave: () => msgWsClose(),
    async build(params) {
      const p = params || {};
      const peer = p.hash;
      const items = [];
      if (!peer) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="warn">no peer selected</div></div>` });
        return { items };
      }
      const r = await jget("/api/v1/messaging/rns/messages?peer=" + encodeURIComponent(peer));
      if (r.ok) (r.d.messages || []).forEach(rnsUpsert);
      const list = RNS_CACHE[peer] || [];
      let h = `<div class="content">`;
      if (!r.ok) h += `<div class="warn">messaging service unavailable</div>`;
      else if (!list.length) h += `<div class="off">no messages yet</div>`;
      else {
        h += `<table><tr><th>When</th><th>From</th><th>Message</th><th>State</th></tr>`;
        list.slice(-100).forEach((m) => {
          const mine = m.direction === "out";
          h += `<tr><td>${ago(m.ts)}</td>` +
            `<td class="${mine ? "ok" : ""}">${mine ? "me" : esc(p.host || "peer")}</td>` +
            `<td>${esc(m.text)}</td><td>${esc(m.state || "")}</td></tr>`;
        });
        h += `</table>`;
      }
      h += `</div>`;
      items.push({ type: "content", html: h });
      items.push({ type: "compose", key: "rns_text", value: RNS_DRAFT, max: 200,
        placeholder: "Type a message",
        sendLabel: "Send", onChange: (v) => RNS_DRAFT = v,
        onSubmit: (S, text) => rnsSend(S, peer, text) });
      return { items };
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
  const url = (MCHURL || "").trim();
  if (!url) return S.msg("enter a channel URL in the field first", false);
  return radioOp(S, MB + "/config/channel-url", { url }, "apply");
}

async function loadPeers(S) {
  S.msg("querying Nucleus devices on the wifi mesh for Meshtastic configs…");
  const { ok, d } = await jget(MB + "/peers");
  if (!ok) return S.msg("peer query failed: " + (d.detail || "error"), false);
  PEERS = { peers: d.peers || [], local: d.local || {} };
  const n = PEERS.peers.length;
  await S.reload();  // render() resets the message line, so set it afterwards
  S.msg(n ? `${n} Nucleus peer${n === 1 ? "" : "s"} found — select a Join row to copy its channel`
    : "no other Nucleus devices reachable on the wifi mesh", n > 0);
}

const peerMatches = (p, L) =>
  p.channel_name === L.channel_name && p.psk_fingerprint === L.psk_fingerprint &&
  p.modem_preset === L.modem_preset && p.region === L.region && p.frequency_slot === L.frequency_slot;

// Page items for the last peer query: a comparison table (content) plus one
// selectable Join button per peer whose channel differs and can be copied.
function peerItems() {
  if (!PEERS || !PEERS.peers.length) return [];
  const L = PEERS.local;
  let h = `<div class="content"><table><tr><th>Nucleus IP</th><th>Channel</th><th>Key</th>` +
    `<th>Region</th><th>Preset</th><th>Slot</th><th>Match</th></tr>`;
  const cell = (v, match) => `<td class="${match ? "ok" : "off"}">${esc(v === "" || v == null ? "—" : v)}</td>`;
  const joins = [];
  PEERS.peers.forEach((p) => {
    if (!p.reachable) {
      h += `<tr><td>${esc(p.ip)}</td><td class="warn" colspan="6">unreachable / no Meshtastic config</td></tr>`;
      return;
    }
    const match = peerMatches(p, L);
    h += `<tr><td>${esc(p.ip)}</td>` +
      cell(p.channel_name, p.channel_name === L.channel_name) +
      cell(p.psk_fingerprint, p.psk_fingerprint === L.psk_fingerprint) +
      cell(p.region, p.region === L.region) +
      cell(p.modem_preset, p.modem_preset === L.modem_preset) +
      cell(p.frequency_slot, p.frequency_slot === L.frequency_slot) +
      `<td class="${match ? "ok" : "off"}">${match ? "match" : "differs"}</td></tr>`;
    if (!match && p.has_channel_url) {
      joins.push({
        type: "button",
        label: `» Join ${p.ip} (${p.channel_name || "—"} · ${p.modem_preset || "—"} · key ${p.psk_fingerprint || "—"})`,
        onEnter: (S) => joinPeer(S, p.ip),
      });
    }
  });
  h += `</table></div>`;
  return [{ type: "content", html: h }, ...joins];
}

async function joinPeer(S, ip) {
  if (!confirm(`Join ${ip}'s Meshtastic channel? Copies its channel name, key, region, ` +
    `preset and slot to this device's Meshtastic radio (role, TX power, hop limit unchanged). ` +
    `Meshtastic radio reboots.`)) return;
  await radioOp(S, MB + "/config/join-peer", { host: ip }, "join");
  // Refresh the comparison so the joined peer shows "match", keeping radioOp's
  // result message (render() resets the message line).
  const m = document.getElementById("shell-msg");
  const text = m ? m.textContent : "";
  const good = !(m && m.classList.contains("err"));
  const { ok, d } = await jget(MB + "/peers");
  if (ok) PEERS = { peers: d.peers || [], local: d.local || {} };
  await S.reload();
  S.msg(text, good);
}

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

// Ask the cot-bridge to broadcast a presence heartbeat immediately. Works even
// when the periodic heartbeat is off; the bridge must be running.
async function sendHeartbeatNow(S) {
  S.msg("sending heartbeat…");
  const { ok, d } = await jsend("POST", "/api/v1/meshtastic/heartbeat", {});
  if (ok) S.msg("heartbeat sent");
  else S.msg("send failed: " + (d.detail || "error"), false);
}

async function saveTxRateLimit(S) {
  if (TXR === null) return S.msg("nothing to save", false);
  // Read-modify-write the full node config so we don't clobber other fields.
  const { d: cfg } = await jget("/api/v1/config");
  delete cfg._derived;
  cfg.meshtastic = cfg.meshtastic || {};
  cfg.meshtastic.tx_min_interval_secs = parseInt(TXR, 10);
  const { ok, d } = await jsend("PUT", "/api/v1/config", cfg);
  if (ok) S.msg("TX rate limit saved — active within one bridge cycle (~10s)");
  else S.msg("save failed: " + JSON.stringify(d.detail), false);
}

async function saveRnode(S) {
  if (!RN) return S.msg("nothing to save", false);
  // Read-modify-write the full node config so only the rnode_* keys change.
  const { d: cfg } = await jget("/api/v1/config");
  delete cfg._derived;
  cfg.reticulum = cfg.reticulum || {};
  cfg.reticulum.rnode_enabled = RN.enabled;
  cfg.reticulum.rnode_frequency = parseInt(RN.frequency, 10);
  cfg.reticulum.rnode_bandwidth = parseInt(RN.bandwidth, 10);
  cfg.reticulum.rnode_txpower = parseInt(RN.txpower, 10);
  cfg.reticulum.rnode_spreadingfactor = parseInt(RN.spreadingfactor, 10);
  cfg.reticulum.rnode_codingrate = parseInt(RN.codingrate, 10);
  const { ok, d } = await jsend("PUT", "/api/v1/config", cfg);
  if (ok) S.msg("RNode saved — not yet applied; press Apply to restart rnsd");
  else S.msg("validation failed: " + JSON.stringify(d.detail), false);
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

// Send a Reticulum/LXMF direct message to one peer (lxmf.delivery hash).
async function rnsSend(S, peer, text) {
  text = (text != null ? text : RNS_DRAFT || "").trim();
  if (!text) return S.msg("type a message first", false);
  const { ok, d } = await jsend("POST", "/api/v1/messaging/rns/messages", { dest: peer, text });
  if (!ok) return S.msg("send failed: " + (d.detail || "error"), false);
  RNS_DRAFT = "";
  S.msg("sent");
  // Inbound/echoed DMs also arrive over the WS; reload for an immediate view.
  return S.reload();
}

// Announce this node's lxmf.delivery once so peers can resolve a path to it.
async function rnsAnnounce(S) {
  const { ok, d } = await jsend("POST", "/api/v1/messaging/rns/announce", {});
  if (!ok) return S.msg("announce failed: " + (d.detail || "error"), false);
  return S.msg("announced");
}

// Emit ONE path request for a contact (announces off → this is how we find it).
// Records the request time so the page can show how long since we last looked.
async function rnsRequestPath(S, peer) {
  const { ok, d } = await jsend("POST",
    "/api/v1/messaging/rns/contacts/" + encodeURIComponent(peer) + "/path", {});
  if (!ok) return S.msg("path request failed: " + (d.detail || "error"), false);
  RNS_PATH_SEEN[peer] = Math.floor(Date.now() / 1000);
  const known = d.path && d.path.known;
  S.msg(known ? "path known (" + (d.path.hops != null ? d.path.hops + " hops" : "—") + ")"
              : "path requested — waiting for a response");
  return S.reload();
}

// Add a contact pulled from a mesh node's card (verified server-side on import).
async function rnsAddMesh(S, card) {
  const { ok, d } = await jsend("POST", "/api/v1/messaging/rns/contacts",
    { card, source: "mesh" });
  if (!ok) return S.msg("add failed: " + (d.detail || "error"), false);
  S.msg("added " + (card.name || card.id || "contact"));
  return S.reload();
}

// Add a contact by its lxmf.delivery address (+ optional nickname). Keyless and
// out-of-band: the server only stores it — nothing is transmitted. The key comes
// later via req path (or an inbound message).
async function rnsAddHash(S) {
  const dest = (RNS_ADD_HASH || "").trim().toLowerCase();
  if (!dest) return S.msg("enter an lxmf.delivery address first", false);
  const name = (RNS_ADD_NAME || "").trim();
  const { ok, d } = await jsend("POST", "/api/v1/messaging/rns/contacts",
    { dest, name, source: "hash" });
  if (!ok) return S.msg("add failed: " + (d.detail || "error"), false);
  RNS_ADD_HASH = ""; RNS_ADD_NAME = "";
  const who = (d.contact && (d.contact.name || d.contact.lxmf_hash.slice(0, 10))) || dest.slice(0, 10);
  S.msg("added " + who + " — no key yet (req path to fetch it)");
  return S.reload();
}

// Rename a contact (set its local nickname). Transmits nothing.
async function rnsRename(S, peer, cur) {
  const name = (typeof prompt === "function")
    ? prompt("Nickname for this contact (blank to clear):", cur || "") : null;
  if (name === null) return;
  const { ok, d } = await jsend("PATCH",
    "/api/v1/messaging/rns/contacts/" + encodeURIComponent(peer), { name: name.trim() });
  if (!ok) return S.msg("rename failed: " + (d.detail || "error"), false);
  S.msg("renamed");
  return S.reload();
}

// Remove a contact after confirmation. Transmits nothing.
async function rnsRemove(S, peer, who) {
  if (typeof confirm === "function" &&
      !confirm("Remove contact " + (who || peer) + "?")) return;
  const { ok, d } = await jsend("DELETE",
    "/api/v1/messaging/rns/contacts/" + encodeURIComponent(peer));
  if (!ok) return S.msg("remove failed: " + (d.detail || "error"), false);
  S.msg("removed");
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
    } else if (obj.event === "rns_message") {
      rnsUpsert(obj.message);
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

// Deploy the HaLow settings in one step: read the full config, overlay the
// edited halow block, PUT it back (validated by schema.py — bad channel/
// bandwidth is rejected there), then apply to reconcile nucleus-halow and
// reload the page from the live config. If nothing changed, says so.
async function deployHalow(S) {
  if (!HL) return S.msg("nothing to deploy", false);
  const { d: cfg } = await jget("/api/v1/config");
  delete cfg._derived;
  cfg.halow = cfg.halow || {};
  cfg.halow.enabled = HL.enabled;
  cfg.halow.bandwidth_mhz = parseInt(HL.bandwidth_mhz, 10);
  cfg.halow.channel = parseInt(HL.channel, 10);
  const put = await jsend("PUT", "/api/v1/config", cfg);
  if (!put.ok) return S.msg("validation failed: " + JSON.stringify(put.d.detail), false);
  const { ok, d } = await jsend("POST", "/api/v1/apply?dry_run=false");
  if (!ok) return S.msg("apply failed", false);
  if (!d.changed.length) { S.msg("no changes — system in sync"); }
  else {
    S.msg("deployed — changed " + d.changed.length + " file(s); units: " +
      (d.units_restarted.join(", ") || "none"));
  }
  HL = null;
  return S.reload();
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
  const id = TS_ACCT_IDS[TS_ACCT];
  if (!id) return S.msg("unknown profile", false);
  S.msg("switching…");
  const { ok, d } = await jsend("POST", "/api/v1/tailscale/switch", { account: id });
  if (!ok) return S.msg("switch failed: " + (d.detail || "error"), false);
  S.msg("switched to " + TS_ACCT);
  return S.reload();
}

// Reboot / power off the node. The API returns before the transition, after
// which the node (and its web UI) drops — so there is no progress to poll.
async function powerOp(S, action) {
  const verb = action === "poweroff" ? "Power off" : "Reboot";
  if (!confirm(`${verb} this node now? It will go down immediately` +
    (action === "poweroff" ? " and needs physical access to restart." : ".")))
    return;
  S.msg(`${verb.toLowerCase()}…`);
  const { ok, d } = await jsend("POST", `/api/v1/power/${action}`);
  if (ok && d.started) {
    S.msg(action === "poweroff" ? "powering off — node going down" : "rebooting — node going down");
  } else {
    S.msg("failed: " + (d.detail || "error"), false);
  }
}

// ── Radios (RF on/off) actions ─────────────────────────────────
// A config-backed toggle: read-modify-write one key via PUT /config, then apply.
// Used for the persistent radios (wlan1 mesh, Meshtastic TX). `section`/`key` is
// the config path to set; `val` the new value; `label` for status/apply output.
async function rfSaveAndApply(S, section, key, val, label) {
  S.msg(label + "… (saving)");
  const { d: cfg } = await jget("/api/v1/config");
  delete cfg._derived;
  cfg[section] = cfg[section] || {};
  cfg[section][key] = val;
  const put = await jsend("PUT", "/api/v1/config", cfg);
  if (!put.ok) return S.msg("validation failed: " + JSON.stringify(put.d.detail), false);
  S.msg(label + "… (applying)");
  const { ok, d } = await jsend("POST", "/api/v1/apply?dry_run=false");
  if (!ok) return S.msg("apply failed", false);
  S.msg(label + " done — units: " + (d.units_restarted.join(", ") || "none"));
  return S.reload();
}

// wlan1 mesh on/off (persists). Turning it OFF can cut the operator's own link,
// so confirm and name the wired fallbacks first.
async function rfSetMesh(S, on) {
  if (!on && !confirm(
    "Turn the Wi-Fi MESH (wlan1) OFF? If you're connected over the mesh you " +
    "will lose this session. Wired fallbacks: USB-C (RNDIS), eth0, or console. " +
    "Persists across reboots — turn it back on here or edit config.yaml.")) return;
  return rfSaveAndApply(S, "mesh", "enabled", on, on ? "mesh on" : "mesh off");
}

// Meshtastic TX on / receive-only (persists). rxOnly=true means currently
// receive-only, so the action turns TX back on; otherwise set receive-only.
async function rfSetMeshtasticTx(S, rxOnly) {
  const on = rxOnly;  // enabling TX when currently receive-only
  return rfSaveAndApply(S, "meshtastic", "tx_enabled", on,
    on ? "Meshtastic TX on" : "Meshtastic receive-only");
}

// wlan0 AP on/off — LIVE toggle, outside the config pipeline (resets ON at boot).
// Turning it OFF can cut an AP-connected operator, so confirm with fallbacks and
// note the AP returns on reboot as the recovery path.
async function rfSetAp(S, on) {
  if (!on && !confirm(
    "Turn the Wi-Fi AP (wlan0) OFF? If you're connected over the AP you will " +
    "lose this session. Wired fallbacks: USB-C (RNDIS), eth0, or console. " +
    "The AP always turns back ON at the next reboot (lockout recovery).")) return;
  S.msg(on ? "AP on…" : "AP off…");
  const { ok, d } = await jsend("POST", "/api/v1/rf/ap", { on });
  if (!(ok && d.ok)) return S.msg("AP toggle failed: " + (d.detail || "error"), false);
  S.msg(on ? "AP on" : "AP off");
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

