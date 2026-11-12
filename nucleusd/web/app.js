// Nucleus V3 — data layer + page definitions for the CLI/TUI shell (cli.js).
// No framework, no build step. Pages return an `items` model that the shell
// renders uniformly; the shell owns cursor/focus, keyboard and auto-refresh.

const MB = "/api/v1/meshtastic";

// ── Display theme ────────────────────────────────────────────────
// Pure client-side preference (no backend/config): the palette, font and base
// size are driven by [data-theme] on <html> (see cli.css). "classic" is the
// implicit default — no attribute, matching the pre-paint loader in index.html.
const THEMES = ["classic", "htop", "day", "dark", "night"];
function getTheme() {
  try { return localStorage.getItem("nucleus-theme") || "classic"; }
  catch (e) { return "classic"; }
}
function setTheme(name) {
  const t = THEMES.includes(name) ? name : "classic";
  try { localStorage.setItem("nucleus-theme", t); } catch (e) {}
  if (t === "classic") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}

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
    // Count distinct NODES, not Babel links: a single node reached over several
    // radios (wlan1 + halow0) is one entry in neighbour_nodes but several in
    // babel_neighbours. The header must not read as "N other nodes" when it is
    // really one node over N interfaces.
    const nodes = st.neighbour_nodes || [];
    const n = nodes.length;
    const el = document.getElementById("h-mesh");
    const meshRadio = (st.radios && st.radios.mesh) || {};
    let text, cls;
    if (meshRadio.on === false) {
      // Deliberately switched off (mesh.enabled: false) — say so, rather than
      // "DOWN", which reads as a fault. See the MESH CONNECTIONS alert.
      text = "RF OFF";
      cls = "warn";
    } else if (meshSvc !== "active") {
      text = "DOWN";
      cls = "down";
    } else if (n > 0) {
      // Count distinct nodes only. Interfaces are intentionally NOT shown here:
      // with several nodes each over different radios an interface list would be
      // ambiguous. Per-node interfaces live in neighbour_nodes for the Monitor.
      text = n + (n === 1 ? " node" : " nodes");
      cls = "up";
    } else {
      text = "0 nodes";
      cls = "warn";
    }
    el.textContent = text;
    el.className = "v " + cls;
    document.getElementById("h-ip").textContent = ip;
  } catch (e) { /* header stays as-is on transient errors */ }
}

// ── Radio-off alerts (MESH CONNECTIONS) ────────────────────────
// Map the status `radios` block to operator warnings. Pure field -> message
// mapping only: the state itself (incl. mesh.rf_mismatch) is derived in
// status.radios() server-side. Unknown (null/undefined) never alerts, so a
// failed status read can't raise a false alarm. The AP is deliberately not
// listed: it is live-only, resets ON at boot, and if it's off you are already
// connected some other way.
function radioOffAlerts(radios) {
  const r = radios || {};
  const mesh = r.mesh || {};
  const mt = r.meshtastic || {};
  const hl = r.halow || {};
  const out = [];
  if (mesh.on === false) {
    out.push("<b>Wi-Fi mesh radio is OFF.</b> This node cannot see or reach " +
      "other Wi-Fi mesh nodes. Turn it on under RADIOS (RF ON/OFF).");
  } else if (mesh.rf_mismatch === true) {
    out.push("<b>Wi-Fi mesh is enabled but the wlan1 radio is still " +
      "switched off.</b> Apply the config or reboot the node.");
  }
  if (mt.receive_only === true) {
    out.push("<b>Meshtastic is receive-only.</b> LoRa nodes are still heard, " +
      "but this node will not transmit.");
  }
  if (hl.present === true && hl.enabled === false) {
    out.push("<b>HaLow radio is plugged in but turned off.</b> Enable it on " +
      "the HALOW page.");
  }
  return out;
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
// Last frequency descriptor from /meshtastic/freq (region/preset/name/slot →
// band edges MHz). Drives the live readout on the Radio Configuration page.
let MF = null;
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
// modem73 page: latest pushed view (/api/v1/modem73/ws, polling fallback)
// and the operator's working copy of its settings (null = follow live/saved).
let M73_VIEW = null;
let M73_WS = null;
let M73_WS_POLL = false;
let M73_EDIT = null;
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
// Per-browser "read up to" marks for DM conversations (peer -> epoch of the
// newest message seen). Drives the ● new-message marker on the contact list.
function rnsReadMap() {
  try { return JSON.parse(localStorage.getItem("nucleus-rns-read") || "{}") || {}; }
  catch (e) { return {}; }
}
function rnsMarkRead(peer, ts) {
  const m = rnsReadMap();
  if ((m[peer] || 0) >= ts) return;
  m[peer] = ts;
  try { localStorage.setItem("nucleus-rns-read", JSON.stringify(m)); } catch (e) {}
}
// One-line path state from a /contacts/{hash}/path response. Read-only.
function rnsPathStr(pr, peer) {
  const path = (pr && pr.ok && pr.d.path) ? pr.d.path : { known: false };
  if (path.known) {
    const hops = path.hops != null ? `${path.hops}h` : "?h";
    return `${rnsIface(path.interface)} ${hops}` + (path.updated ? ` ${ago(path.updated)}` : "");
  }
  const seen = RNS_PATH_SEEN[peer];
  return seen ? `no path · req ${ago(seen)} ago` : "no path";
}
// Merge a DM into the per-peer cache (by peer+ts+direction; no server id).
function rnsUpsert(m) {
  if (!m || !m.peer) return;
  const list = RNS_CACHE[m.peer] || (RNS_CACHE[m.peer] = []);
  const i = list.findIndex((x) => x.ts === m.ts && x.direction === m.direction && x.text === m.text);
  if (i >= 0) list[i] = m; else list.push(m);
  list.sort((a, b) => a.ts - b.ts);
}

// Meshtastic client state. MC = MB + "/client".
const MC = MB + "/client";
const MC_EMOJI = ["👍", "❤️", "😂", "😮", "😢", "✅"];
let MC_DRAFT = "";
let MC_CUR = null;         // open chat {chat, title} (Back drops page params)
let MC_NODE = null;        // open node id on the node page
let MC_REPLY = null;       // message being replied to: {chat, pkt_id, from, text}
let MC_LAST = { chat: null, messages: [] };   // last fetched conversation
let MC_FILTER = "";

// Menu new-message dots. Never throw: any failure → false (no dot).
async function mcHasUnread() {
  try {
    const { ok, d } = await jget(MC);
    return ok && (d.chats || []).some((c) => c.unread > 0);
  } catch (e) { return false; }
}
async function rnsHasUnread() {
  try {
    const { ok, d } = await jget("/api/v1/messaging/rns/latest");
    if (!ok || !d.latest) return false;
    const read = rnsReadMap();
    return Object.entries(d.latest).some(([hash, l]) =>
      l && l.direction === "in" && l.ts > (read[hash] || 0));
  } catch (e) { return false; }
}
let MC_SORT = "heard";
let MC_SHOW = "all";

function mcDist(m) {
  if (m == null) return "—";
  return m < 1000 ? m + " m" : (m / 1000).toFixed(1) + " km";
}
function mcNodeName(n) {
  return n.long_name || n.short_name || n.id;
}

// ── Link test page state ─────────────────────────────────────────
// Selected link + per-link target (fselect labels) and the last result HTML.
const LT_LINKS = {
  wifi: "Wi-Fi mesh (wlan1): ping + iperf3. The peer starts iperf3 only for the test.",
  halow: "HaLow mesh (halow0): ping + iperf3. The peer starts iperf3 only for the test.",
  lora: "Meshtastic LoRa: traceroute — hops, per-hop SNR and round-trip time. Uses airtime.",
  reticulum: "Reticulum: rnprobe to a contact over whichever interface rnsd routes it " +
    "(RNode LoRa, mesh, eth0). Shows RTT, hops and RNode RSSI/SNR. Can take ~40 s.",
};
let LT = { link: "wifi", mesh: "", lora: "", rns: "", result: "", busy: false };

// Direct peers on one mesh radio (status wifi_peers / halow_peers), same
// columns for every radio. Radio fields (signal/seen) are null when the
// station MAC could not be joined to the Babel row — shown as a neutral "—".
// plink is not shown: the kernel keeps a silent peer ESTAB for up to
// mesh_plink_timeout (1800 s), so it read healthy on a dead link.
function linkPeersTable(peers, emptyMsg) {
  if (!peers.length) return `<div class="off">${esc(emptyMsg)}</div>`;
  let h = `<table><tr><th>Node</th><th>Link</th><th>Signal</th><th>Seen</th></tr>`;
  peers.forEach((p) => {
    const node = p.ipv4 || p.ipv6 || p.mac;
    const link = p.link_pct != null ? p.link_pct + "%" : "—";
    const sig = p.signal_avg_dbm != null ? p.signal_avg_dbm + " dBm" : "—";
    const seen = p.inactive_ms != null ? ago(Date.now() / 1000 - p.inactive_ms / 1000) : "—";
    h += `<tr><td>${esc(node)}</td><td>${esc(link)}</td>` +
      `<td>${esc(sig)}</td><td>${esc(seen)}</td></tr>`;
  });
  return h + `</table>`;
}

// Mesh peers by node id ("0047" -> 47) from Babel routes, neighbours and HaLow
// radio peers. Every address is 10.20.1.<id> on wlan1, so the id is the host part.
function ltMeshPeers(st) {
  const ids = new Set();
  const add = (ip) => { const m = /^10\.20\.1\.(\d+)$/.exec(ip || ""); if (m) ids.add(+m[1]); };
  (st.mesh_nodes || []).forEach((n) => add(n.node));
  (st.neighbour_nodes || []).forEach((n) => add(n.node));
  (st.wifi_peers || []).forEach((p) => add(p.ipv4));
  (st.halow_peers || []).forEach((p) => add(p.ipv4));
  const out = {};
  [...ids].sort((a, b) => a - b).forEach((id) => { out[String(id).padStart(4, "0")] = id; });
  return out;
}

function ltTraceHtml(tr) {
  const kv = (k, v) => `<div class="kv"><span class="k">${k}</span><span>${esc(v)}</span></div>`;
  if (tr.pending) return kv(`Traceroute (${ago(tr.ts)})`, "waiting for reply…");
  if (tr.error) return kv(`Traceroute (${ago(tr.ts)})`, `failed: ${tr.error}`);
  const hop = (ids, snr) => ids.map((x, i) => (i ? `${snr[i - 1] ?? "?"}dB → ` : "") + x).join(" ");
  const r = tr.route;
  return kv(`Traceroute (${ago(tr.ts)})`, hop(r.towards, r.snr_towards)) +
    (r.back.length ? kv("Back", hop(r.back, r.snr_back)) : "") +
    kv("Hops", `${r.towards.length - 2} towards` + (r.back.length ? ` / ${r.back.length - 2} back` : "")) +
    (tr.rtt_s != null ? kv("Round trip", `${tr.rtt_s} s`) : "");
}

function ltResultHtml(kind, r) {
  const kv = (k, v) => `<div class="kv"><span class="k">${k}</span><span>${esc(v ?? "—")}</span></div>`;
  const lossCls = (p) => (p === 0 ? "ok" : "warn");
  if (kind === "ping") {
    const rtt = r.rtt_ms;
    return kv("Ping", `${r.dev} → ${r.peer_ip}`) +
      `<div class="kv"><span class="k">Received</span><span class="${lossCls(r.loss_pct)}">` +
      `${r.received}/${r.sent} (loss ${r.loss_pct}%)</span></div>` +
      kv("RTT min/avg/max", rtt ? `${rtt.min} / ${rtt.avg} / ${rtt.max} ms` : null);
  }
  if (kind === "iperf") {
    return kv("Throughput", `${r.direction === "send" ? "send" : "receive"} ` +
        `${r.dev} ${r.direction === "send" ? "→" : "←"} ${r.peer_ip}`) +
      `<div class="kv"><span class="k">Rate</span><span class="ok">${r.mbps} Mbit/s</span></div>` +
      kv("Transferred", `${fmtBytes(r.bytes)} in ${r.seconds} s`) +
      kv("Retransmits", r.retransmits);
  }
  // rnprobe
  if (r.error && !r.sent) return `<div class="warn">${esc(r.error)}</div>`;
  const phy = [r.rssi_dbm != null ? `RSSI ${r.rssi_dbm} dBm` : null,
    r.snr_db != null ? `SNR ${r.snr_db} dB` : null,
    r.quality_pct != null ? `quality ${r.quality_pct}%` : null].filter(Boolean).join(" · ");
  return kv("Probe", `<${r.dest}>`) +
    `<div class="kv"><span class="k">Received</span><span class="${lossCls(r.loss_pct)}">` +
    `${r.received}/${r.sent} (loss ${r.loss_pct}%)</span></div>` +
    kv("RTT avg", r.rtt_avg_ms != null ? `${r.rtt_avg_ms} ms` : null) +
    kv("Hops", r.hops) + kv("Interface", r.interface) + kv("Signal", phy || null);
}
// One-line status strip shared by the client pages.
function mcStatusLine(s) {
  if (!s.bridge_running) return `<div class="warn">cot-bridge not running — radio unavailable</div>`;
  const parts = [
    `${esc(s.my_name || "?")} ${esc(s.my_id || "")}`,
    `online ${s.online}`,
    `ch util ${s.channel_util != null ? s.channel_util.toFixed(1) + "%" : "—"}`,
    `air tx ${s.air_util_tx != null ? s.air_util_tx.toFixed(1) + "%" : "—"}`,
  ];
  let h = `<div class="kv"><span>${parts.join(" · ")}</span></div>`;
  if (s.tx_enabled === false) h += `<div class="warn">receive-only — sending is disabled</div>`;
  return h;
}

// Voice state: channel list cache for the TUI voice page.
let VOICE_CH = null;

// Tailscale page state: auth-key input draft + selected tailnet profile.
// TS_ACCT holds the chosen dropdown label; TS_ACCT_IDS maps label -> profile id
// (an account can be logged into multiple tailnets, so the id disambiguates).
let TS_AUTHKEY = "";
let TS_ACCT = null;
let TS_ACCT_IDS = {};

// Build the frequency readout line from a /meshtastic/freq descriptor (F) and
// the currently-selected slot. Shows the real band edges (low–high MHz) so the
// operator never has to work out the frequency from the slot number. Pure
// display — the numbers are computed server-side in lora_freq.
function mtFreqLine(F) {
  if (!F) return `<div class="content"><div class="hint" ` +
    `style="padding-left:0">frequency: —</div></div>`;
  const fmt = (n) => (Math.round(n * 1000) / 1000).toFixed(3);
  let body;
  if (F.low_mhz == null || F.high_mhz == null) {
    body = `slot ${esc(F.resolved_slot)} out of range ` +
      `(region allows 1–${esc(F.num_slots)})`;
  } else {
    const band = `${fmt(F.low_mhz)}–${fmt(F.high_mhz)} MHz`;
    body = F.auto
      ? `auto → slot <b>${esc(F.resolved_slot)}</b> · <b>${band}</b>`
      : `slot <b>${esc(F.resolved_slot)}</b> · <b>${band}</b>`;
  }
  return `<div class="content"><div class="hint" style="padding-left:0">` +
    `${body}</div></div>`;
}

// Fetch the frequency descriptor for the current working copy (M). Pure
// server-side math — no radio access. Returns null on error so the readout
// degrades to "—" rather than throwing.
async function mtFetchFreq() {
  if (!M) return null;
  const q = new URLSearchParams({
    region: M.region || "US",
    preset: M.modem_preset || "LONG_FAST",
    name: M.channel_name || "",
    slot: String(M.frequency_slot ?? 0),
  });
  const { ok, d } = await jget(MB + "/freq?" + q.toString());
  return ok ? d : null;
}

// Update the frequency readout row after a field edit. `refetch` is true when
// region/preset/name changed (the slot grid or auto-slot may move, so we must
// re-ask the server); false when only the slot number changed, where we can
// recompute the band edges locally from the cached descriptor's grid. Repaints
// via the shell's redraw (build is refused mid-edit).
function mtRefreshFreq(freqRow, refetch) {
  const shell = window.__shell;
  if (!refetch && MF && MF.num_slots) {
    // Slot-only change: recompute edges from the known grid (freq_start + bw).
    const slot = M.frequency_slot | 0;
    const bwMhz = MF.bandwidth_khz / 1000;
    const resolved = slot || MF.resolved_slot;  // 0 keeps the auto slot
    const F = Object.assign({}, MF, {
      configured_slot: slot, auto: !slot, resolved_slot: resolved,
    });
    if (resolved >= 1 && resolved <= MF.num_slots) {
      const centre = MF.freq_start_mhz + bwMhz / 2 + (resolved - 1) * bwMhz;
      F.low_mhz = centre - bwMhz / 2;
      F.high_mhz = centre + bwMhz / 2;
    } else {
      F.low_mhz = F.high_mhz = null;
    }
    freqRow.html = mtFreqLine(F);
    shell && shell.redraw();
    return;
  }
  // region/preset/name change: re-ask the server, then repaint.
  mtFetchFreq().then((F) => {
    if (F) MF = F;
    freqRow.html = mtFreqLine(F);
    shell && shell.redraw();
  });
}

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
    dynamic: 5000,
    async build() {
      const [{ ok: sok, d: st }, { ok, d: tak }, mcNew, rnsNew] = await Promise.all([
        jget("/api/v1/status"), jget("/api/v1/tak/status"), mcHasUnread(), rnsHasUnread()]);
      const items = [
        { type: "nav", label: "MESH CONNECTIONS", to: "monitor" },
        { type: "nav", label: "MESSAGING", to: "messaging" },
        { type: "nav", label: "VOICE (PTT)", to: "voice" },
        { type: "nav", label: `${mcNew ? "● " : ""}MESHTASTIC`, to: "meshtastic" },
        { type: "nav", label: "INTERFACES AND SERVICES", to: "system" },
        { type: "nav", label: "RADIOS (RF ON/OFF)", to: "radios" },
        { type: "nav", label: `${rnsNew ? "● " : ""}RETICULUM`, to: "reticulum" },
        { type: "nav", label: "TAILSCALE (VPN)", to: "tailscale" },
        { type: "nav", label: "RADIO CONFIGURATION", to: "config" },
      ];
      // HaLow is a plug-in USB radio: show its config entry only when the
      // halow0 interface is present (radios.halow.present from live status).
      if (sok && st.radios && st.radios.halow && st.radios.halow.present) {
        items.push({ type: "nav", label: "HALOW", to: "halow" });
      }
      // TAK Server is optional; show the menu entry only when it's installed.
      if (ok && tak.installed) {
        const label = tak.variant === "opentakserver" ? "OPENTAKSERVER" : "TAK SERVER";
        items.push({ type: "nav", label, to: "tak" });
      }
      items.push({ type: "nav", label: "SYSTEM UPDATE", to: "update" });
      items.push({ type: "nav", label: "NODE SETTINGS", to: "node" });
      return { items };
    },
  },

  // Who's connected: direct peers per radio + whole-mesh routes. Diagnostic detail
  // (interfaces, services) lives on the SYSTEM page.
  monitor: {
    title: "Monitor",
    dynamic: 5000,
    async build() {
      const { d: st } = await jget("/api/v1/status");
      const meshNodes = st.mesh_nodes || [];
      const radios = st.radios || {};
      const meshOff = !!(radios.mesh && radios.mesh.on === false);
      const loraRxOnly = !!(radios.meshtastic && radios.meshtastic.receive_only === true);
      // Radio-off alerts lead the page, with a selectable jump to the RADIOS
      // page where they are switched back on.
      const items = [];
      const alerts = radioOffAlerts(radios);
      if (alerts.length) {
        items.push({ type: "content", html: `<div class="content">` +
          alerts.map((a) => `<div class="rf-alert">${a}</div>`).join("") + `</div>` });
        items.push({ type: "nav", label: "» OPEN RADIOS (RF ON/OFF)", to: "radios" });
      }
      let h = "";

      // Direct peers per radio: each table lists only nodes linked over THAT
      // interface (Babel neighbour + iw station dump), so a HaLow link never
      // reads as a Wi-Fi one. A node on both radios appears in both tables.
      h += `<div class="content"><div class="page-title" style="padding-left:0">Wifi mesh peers</div>`;
      if (meshOff) {
        h += `<div class="warn">Wi-Fi mesh is turned off — no nodes can be seen over wlan1</div>`;
      } else {
        h += linkPeersTable(st.wifi_peers || [], "no Wi-Fi peers — this node sees no other node over wlan1");
      }
      h += `</div>`;

      // HaLow (802.11ah) — only when the halow0 radio is present.
      const halowRadio = (st.radios && st.radios.halow) || {};
      if (halowRadio.present) {
        h += `<div class="content"><div class="page-title" style="padding-left:0">HaLow mesh peers</div>`;
        h += linkPeersTable(st.halow_peers || [], "no HaLow peers — this node sees no other node over halow0");
        h += `</div>`;
      }

      // Whole-mesh routes (direct + multi-hop) over whichever radio Babel
      // selected. Not a per-radio view — Iface shows the radio actually used.
      h += `<div class="content"><div class="page-title" style="padding-left:0">Mesh routes</div>`;
      if (!meshNodes.length) {
        h += `<div class="off">no routes — this node cannot reach any other mesh node</div>`;
      } else {
        h += `<table><tr><th>Node</th><th>Via</th><th>Iface</th><th>Cost</th><th>Seen</th></tr>`;
        meshNodes.forEach((r) => {
          // Lost nodes keep their row (with age) until dropped after 15 min.
          const via = !r.reachable ? "—" : r.direct ? "direct" : esc(r.via || "—");
          const iface = r.reachable ? (r.iface || "—") : "—";
          const cost = r.reachable ? (r.metric / 256).toFixed(1) : "—";
          const seen = r.reachable ? "now" : ago(r.last_seen);
          h += `<tr><td>${esc(r.node)}</td><td>${via}</td>` +
            `<td>${esc(iface)}</td><td>${esc(cost)}</td>` +
            `<td class="${r.reachable ? "ok" : "warn"}">${esc(seen)}</td></tr>`;
        });
        h += `</table>`;
      }
      h += `</div>`;

      // LoRa (Meshtastic) nodes heard over RF.
      const { d: md } = await jget(MB + "/nodes");
      const nodes = md.nodes || [];
      h += `<div class="content"><div class="page-title" style="padding-left:0">Meshtastic Nodes</div>`;
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
      if (loraRxOnly) {
        h += `<div class="hint" style="padding-left:0">receive-only — this node ` +
          `hears LoRa nodes but does not transmit</div>`;
      }
      h += `</div>`;

      items.push({ type: "content", html: h });
      items.push({ type: "nav", label: "» LINK TEST (ping / throughput)", to: "linktest" });
      return { items };
    },
  },

  // On-demand link tests to one peer: ping + iperf3 over wlan1/halow0,
  // traceroute over Meshtastic LoRa, rnprobe over Reticulum. Nothing runs until
  // a test button is pressed (see nucleusd/linktest.py).
  linktest: {
    title: "Link Test",
    dynamic: 5000,
    async build() {
      const [{ d: st }, mc, rc] = await Promise.all([
        jget("/api/v1/status"), jget(MC + "/nodes"),
        jget("/api/v1/messaging/rns/contacts")]);
      const items = [{ type: "fselect", label: "Link", value: LT.link,
        options: Object.keys(LT_LINKS), onChange: (v) => { LT.link = v;
          setTimeout(() => window.__shell.reload(), 0); } }];
      const pick = (label, opts, key) => {
        const names = Object.keys(opts);
        if (!names.includes(LT[key])) LT[key] = names[0] || "";
        if (!names.length) return items.push({ type: "content", html:
          `<div class="content"><div class="off">no ${label.toLowerCase()}s known</div></div>` });
        items.push({ type: "fselect", label, value: LT[key], options: names,
          onChange: (v) => LT[key] = v });
      };
      if (LT.link === "wifi" || LT.link === "halow") {
        pick("Peer", ltMeshPeers(st), "mesh");
        items.push({ type: "button", label: "» PING", onEnter: (S) => ltRun(S, "ping") });
        items.push({ type: "button", label: "» THROUGHPUT SEND (this node → peer)",
          onEnter: (S) => ltRun(S, "iperf", false) });
        items.push({ type: "button", label: "» THROUGHPUT RECEIVE (peer → this node)",
          onEnter: (S) => ltRun(S, "iperf", true) });
      } else if (LT.link === "lora") {
        const nodes = (mc.ok && mc.d.nodes) || [];
        const opts = {};
        nodes.forEach((n) => { opts[`${mcNodeName(n)} ${n.id}`] = n; });
        pick("Node", opts, "lora");
        items.push({ type: "button", label: "» TRACEROUTE",
          onEnter: (S) => ltRun(S, "traceroute", opts[LT.lora]) });
        const n = opts[LT.lora];
        if (n && n.traceroute) items.push({ type: "content", html:
          `<div class="content">${ltTraceHtml(n.traceroute)}</div>` });
      } else {
        const opts = {};
        ((rc.ok && rc.d.contacts) || []).forEach((c) => {
          opts[`${rnsName(c)} <${c.lxmf_hash.slice(0, 8)}>`] = c.lxmf_hash; });
        pick("Contact", opts, "rns");
        items.push({ type: "button", label: "» PROBE (3 packets)",
          onEnter: (S) => ltRun(S, "rnprobe", opts[LT.rns]) });
      }
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="hint" style="padding-left:0">${LT_LINKS[LT.link]}</div>` +
        // The 5 s refresh clears the shell message; keep "running" visible here.
        (LT.busy ? `<div class="warn">test running…</div>` : "") +
        (LT.result ? `<div class="page-title" style="padding-left:0">Last result</div>` +
          LT.result : "") + `</div>` });
      return { items };
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

  // Reticulum landing page (NomadNet-style): who I am (name + address, with
  // rename + announce), then my SAVED contacts — newest conversation first, each
  // with a new-message marker, path state and an inline
  // REQ PATH. Heard announces are NOT listed here (an internet-connected rnsd
  // could flood them); they live on the Network page. rnsd status + interfaces
  // are on the Node page.
  reticulum: {
    title: "Reticulum",
    dynamic: 5000,
    onEnter: (S) => msgWsOpen(S),
    onLeave: () => msgWsClose(),
    async build() {
      const items = [];
      const [rs, ms] = await Promise.all([
        jget("/api/v1/reticulum/status"), jget("/api/v1/messaging/rns/status")]);
      const rnsdUp = rs.ok && rs.d.running;
      const m = ms.ok ? ms.d : {};
      if (!rnsdUp) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="warn">rnsd is not running — messages can't be sent</div></div>` });
      }
      if (!m.enabled) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="off">Reticulum messaging is disabled</div>` +
          `<div class="hint" style="padding-left:0">Enable messaging.rns.enabled ` +
          `in the config, then apply.</div></div>` });
        items.push({ type: "nav", label: "» NODE STATUS & INTERFACES", to: "rns_node" });
        return { items };
      }

      // ── me ───────────────────────────────────────────────────
      const me = m.name || "—";
      items.push({ type: "button", label: `me: ${me}  ·  rename`,
        onEnter: (S) => rnsSetMyName(S, me),
        action: { label: "announce", onRun: rnsAnnounce } });
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="hint" style="padding-left:0;word-break:break-all">` +
        (m.address ? `address <b>${esc(m.address)}</b>` : `address not ready — lane starting`) +
        ` · announce ${esc(m.announce_mode || "manual")}</div></div>` });

      // ── contacts ─────────────────────────────────────────────
      const [cr, lr] = await Promise.all([
        jget("/api/v1/messaging/rns/contacts"), jget("/api/v1/messaging/rns/latest")]);
      const contacts = (cr.ok && cr.d.contacts) ? cr.d.contacts : [];
      const latest = (lr.ok && lr.d.latest) ? lr.d.latest : {};
      const act = (c) => {
        const l = latest[c.lxmf_hash];
        return (l && l.ts) || c.last_seen || c.added || 0;
      };
      contacts.sort((a, b) => act(b) - act(a));
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="page-title" style="padding-left:0">Contacts</div>` +
        (contacts.length ? "" : `<div class="off">no contacts yet — use Add contact below</div>`) +
        `</div>` });
      // Path reads are parallel and emit no RNS traffic; only REQ PATH transmits.
      const paths = await Promise.all(contacts.map((c) =>
        jget("/api/v1/messaging/rns/contacts/" + encodeURIComponent(c.lxmf_hash) + "/path")));
      const read = rnsReadMap();
      contacts.forEach((c, i) => {
        const who = rnsName(c);
        const l = latest[c.lxmf_hash];
        const unread = l && l.direction === "in" && l.ts > (read[c.lxmf_hash] || 0);
        const pstr = c.pubkey ? rnsPathStr(paths[i], c.lxmf_hash) : "no key yet";
        items.push({
          type: "nav",
          label: `${unread ? "● " : ""}${who}  ·  ${pstr}`,
          to: "rns_chat",
          onEnter: (S) => S.go("rns_chat", { hash: c.lxmf_hash, host: who }),
          action: { label: "req path", onRun: (S) => rnsRequestPath(S, c.lxmf_hash) },
        });
      });

      // ── elsewhere ────────────────────────────────────────────
      const hr = await jget("/api/v1/messaging/rns/heard");
      const hn = (hr.ok && hr.d.heard) ? hr.d.heard.length : 0;
      items.push({ type: "nav", label: "» ADD CONTACT", to: "rns_add" });
      items.push({ type: "nav", label: `» NETWORK (${hn} heard)`, to: "rns_heard" });
      items.push({ type: "nav", label: "» NODE STATUS & INTERFACES", to: "rns_node" });
      return { items };
    },
  },

  // Reticulum node status: rnsd up/uptime/path count, per-interface traffic +
  // client counts, and the radio config pages. Data from the shared-instance
  // control socket via /api/v1/reticulum.
  rns_node: {
    title: "Reticulum Node",
    dynamic: 5000,
    async build() {
      const { ok, d: s } = await jget("/api/v1/reticulum/status");
      const items = [];
      if (!ok || !s.running) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="off">rnsd is not running — no Reticulum status available</div></div>` });
      } else {
        const t = s.transport || {};
        // Hide the internal Shared Instance row.
        const ifaces = (s.interfaces || []).filter((i) =>
          !(i.short_name || i.name || "").startsWith("Shared Instance"));
        let h = `<div class="content">` +
          `<div class="kv"><span>rnsd <b class="ok">up</b></span>` +
          `<span>uptime <b>${t.uptime != null ? fmtDur(t.uptime) : "—"}</b></span>` +
          `<span>paths <b>${s.path_count}</b></span></div>` +
          `<div class="page-title" style="padding-left:0">Interfaces</div>`;
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
      }
      items.push({ type: "nav", label: "» CONFIGURE RNODE (LoRa radio)", to: "rns_rnode" });
      items.push({ type: "nav", label: "» MODEM73 (soundmodem)", to: "rns_modem73" });
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

  // modem73: RECEIVE, TRANSMIT, SETUP (readings then settings), one SAVE +
  // APPLY for all of them, then frames heard. Gains/TX level change at once
  // (until modem73 restarts); the rest takes effect on SAVE + APPLY. Data via
  // /api/v1/modem73 (WS, poll fallback).
  rns_modem73: {
    title: "modem73",
    dynamic: 3000,
    onEnter: (S) => m73WsOpen(S),
    onLeave: () => { m73WsClose(); M73_EDIT = null; },
    async build() {
      if (!M73_VIEW || !M73_WS || M73_WS_POLL) {
        const r = await jget("/api/v1/modem73/status");
        if (r.ok) M73_VIEW = r.d;
      }
      const v = M73_VIEW;
      if (!v) return { items: [{ type: "content", html:
        `<div class="content"><div class="off">modem73 status unavailable</div></div>` }] };
      if (!M73_EDIT) {
        const cur = (k) => (v.live[k] != null ? v.live[k] : v.saved[k]);
        M73_EDIT = { tx_level: cur("tx_level"), rx_gain: cur("rx_gain"),
                     tx_gain: cur("tx_gain"), mode: cur("mode"),
                     enabled: v.saved.enabled, callsign: v.saved.callsign || "" };
      }
      const modes = v.modes || {};
      const modeLabel = (k) => modes[k] ? `${k} (${(modes[k].bitrate / 1000).toFixed(2)} kbps)` : k;
      const labelToKey = {};
      Object.keys(modes).forEach((k) => { labelToKey[modeLabel(k)] = k; });
      const S = m73Sections(v);
      const html = (h) => ({ type: "content", html: h });
      const items = [];
      if (S.top) items.push(html(`<div class="content">${S.top}</div>`));
      items.push(html(m73Head("Receive") + `<div class="content">${S.rx}</div>`));
      items.push({ type: "fnum", label: "RX gain (0-35)", value: M73_EDIT.rx_gain,
        min: 0, max: 35, onChange: (x) => { M73_EDIT.rx_gain = x; m73Live("rx_gain", x); } });
      items.push(html(m73Head("Transmit") + `<div class="content">${S.tx}</div>`));
      items.push({ type: "fselect", label: "Mode", options: Object.keys(modes).map(modeLabel),
        value: modeLabel(M73_EDIT.mode),
        onChange: (x) => M73_EDIT.mode = labelToKey[x] || M73_EDIT.mode });
      items.push({ type: "fnum", label: "TX level (%)", value: M73_EDIT.tx_level,
        min: 5, max: 100, onChange: (x) => { M73_EDIT.tx_level = x; m73Live("tx_level", x); } });
      items.push({ type: "fnum", label: "TX gain (0-37)", value: M73_EDIT.tx_gain,
        min: 0, max: 37, onChange: (x) => { M73_EDIT.tx_gain = x; m73Live("tx_gain", x); } });
      items.push({ type: "button", label: "» SEND TEST FRAME", onEnter: m73Test });
      items.push(html(m73Head("Setup")));
      items.push({ type: "fselect", label: "modem73", options: ["on", "off"],
        value: M73_EDIT.enabled ? "on" : "off", onChange: (x) => M73_EDIT.enabled = (x === "on") });
      items.push({ type: "ftext", label: "Callsign", max: 9, value: M73_EDIT.callsign,
        placeholder: v.callsign || "node id", onChange: (x) => M73_EDIT.callsign = x });
      items.push({ type: "button", label: "» SAVE + APPLY", onEnter: m73Save });
      items.push(html(m73Head("Frames heard")));
      if (S.table) {
        items.push(html(`<div class="content">${S.table}</div>`));
        items.push({ type: "button", label: "» CLEAR", onEnter: m73Clear });
      } else {
        items.push(html(`<div class="content"><div class="off">none</div></div>`));
      }
      return { items };
    },
  },

  // Meshtastic: a menu of sub-pages. Each setting group is its own page so the
  // operator sees one thing at a time (Enter to open, Back to return), instead
  // of several collapsible sections stacked on one screen.
  meshtastic: {
    title: "Meshtastic Radio",
    dynamic: 5000,
    async build() {
      const [{ d: s }, mcNew] = await Promise.all([jget(MB + "/status"), mcHasUnread()]);
      const statusLine =
        `radio: ${s.radio_detected ? "detected" : "not detected"} · ` +
        `bridge: ${s.service_active ? "running" : "stopped"}` +
        (s.bridge_enabled ? " (enabled)" : " (disabled)");
      return {
        items: [
          { type: "content", html: `<div class="content"><div class="kv">` +
            `<span>${esc(statusLine)}</span></div></div>` },
          { type: "nav", label: `${mcNew ? "● " : ""}CHATS`, to: "mc_chats" },
          { type: "nav", label: "NODES", to: "mc_nodes" },
          { type: "nav", label: "POSITIONS", to: "mc_positions" },
          { type: "nav", label: "RADIO CONFIGURATION", to: "mt_radio" },
          { type: "nav", label: "SET CHANNEL", to: "mt_channel" },
          { type: "nav", label: "PRESENCE HEARTBEAT", to: "mt_heartbeat" },
          { type: "nav", label: "CoT TX RATE LIMIT", to: "mt_txrate" },
        ],
      };
    },
  },

  // ── Meshtastic client ─────────────────────────────────────────
  // Chat list: every channel, then DMs (newest first), with unread counts.
  mc_chats: {
    title: "Chats",
    dynamic: 5000,
    async build() {
      const { ok, d } = await jget(MC);
      const items = [{ type: "content", html: `<div class="content">` +
        (ok ? mcStatusLine(d.status) : `<div class="warn">unavailable</div>`) + `</div>` }];
      ((ok && d.chats) || []).forEach((c) => {
        const dm = !c.chat.startsWith("ch");
        const unread = c.unread ? `● ${c.unread}  ` : "";
        const when = c.last_ts ? `  · ${ago(c.last_ts)}` : "";
        items.push({ type: "nav", label: `${unread}${dm ? "@" : "#"}${c.title}${when}`,
          onEnter: (S) => S.go("mc_chat", { chat: c.chat, title: c.title }) });
      });
      return { items };
    },
  },

  // One conversation (channel or DM). Reply/react via » REPLY / REACT.
  mc_chat: {
    title: "Chat",
    stickBottom: true,
    dynamic: 3000,
    async build(params) {
      // The shell's Back drops params, so remember the open chat.
      const p = MC_CUR = params || MC_CUR || {};
      const { ok, d } = await jget(MC + "/messages?chat=" + encodeURIComponent(p.chat));
      const msgs = (ok && d.messages) || [];
      MC_LAST = { chat: p.chat, messages: msgs };
      const dm = !String(p.chat).startsWith("ch");
      const byId = {};
      msgs.forEach((m) => { if (m.pkt_id != null) byId[m.pkt_id] = m; });
      let h = `<div class="content"><div class="page-title" style="padding-left:0">` +
        `${dm ? "@" : "#"}${esc(p.title || p.chat)}</div>`;
      if (!ok) h += `<div class="warn">${esc(d.detail || "unavailable")}</div>`;
      else if (!msgs.length) h += `<div class="off">no messages yet</div>`;
      else {
        h += `<table><tr><th>When</th><th>From</th><th>Message</th>${dm ? "<th>State</th>" : ""}</tr>`;
        msgs.forEach((m) => {
          const re = m.reply_id && byId[m.reply_id];
          const quote = re ? `<span class="off">↪ ${esc(re.from)}: ${esc(re.text.slice(0, 40))}</span><br>` : "";
          const reacts = (m.reactions || []).map((r) => `${esc(r.emoji)} ${esc(r.from)}`).join("  ");
          h += `<tr><td>${ago(m.ts)}</td><td class="${m.mine ? "ok" : ""}">${esc(m.from)}</td>` +
            `<td>${quote}${esc(m.text)}${reacts ? `<br><span class="off">${reacts}</span>` : ""}</td>` +
            (dm ? `<td class="${m.state === "failed" ? "warn" : ""}">${esc(m.mine ? m.state || "" : "")}</td>` : "") +
            `</tr>`;
        });
        h += `</table>`;
      }
      h += `</div>`;
      const items = [{ type: "content", html: h }];
      if (MC_REPLY && MC_REPLY.chat === p.chat) {
        items.push({ type: "button", label: `✕ replying to ${MC_REPLY.from}: ${MC_REPLY.text.slice(0, 30)}`,
          onEnter: (S) => { MC_REPLY = null; S.reload(); } });
      }
      items.push({ type: "nav", label: "» REPLY / REACT", onEnter: (S) => S.go("mc_pick") });
      if (dm) items.push({ type: "nav", label: "» NODE INFO", onEnter: (S) => S.go("mc_node", { id: p.chat }) });
      items.push({ type: "compose", key: "mc_text", value: MC_DRAFT, max: 200,
        placeholder: "Type a message", sendLabel: "Send",
        onChange: (v) => MC_DRAFT = v, onSubmit: (S, text) => mcSend(S, p.chat, text) });
      return { items };
    },
  },

  // Pick a message: Enter = reply to it, "react" = choose an emoji.
  mc_pick: {
    title: "Reply / React",
    async build() {
      const p = MC_CUR || {};   // the open chat (Back from React drops params)
      const msgs = (MC_LAST.chat === p.chat ? MC_LAST.messages : [])
        .filter((m) => m.pkt_id != null).slice(-30).reverse();
      const items = [{ type: "content", html: `<div class="content"><div class="hint" ` +
        `style="padding-left:0">Enter = reply · react = add an emoji</div>` +
        (msgs.length ? "" : `<div class="off">no messages</div>`) + `</div>` }];
      msgs.forEach((m) => items.push({ type: "button",
        label: `${m.from}: ${m.text.slice(0, 60)}`,
        onEnter: (S) => {
          MC_REPLY = { chat: p.chat, pkt_id: m.pkt_id, from: m.from, text: m.text };
          S.back();
        },
        action: { label: "react", onRun: (S) => S.go("mc_react", { chat: p.chat, m }) } }));
      return { items };
    },
  },

  mc_react: {
    title: "React",
    async build(params) {
      const p = params || {};
      const items = [{ type: "content", html: `<div class="content"><div class="kv">` +
        `<span>${esc(p.m.from)}: ${esc(p.m.text.slice(0, 80))}</span></div></div>` }];
      MC_EMOJI.forEach((e) => items.push({ type: "button", label: e,
        onEnter: (S) => mcReact(S, p.chat, p.m.pkt_id, e) }));
      return { items };
    },
  },

  // Node list: filter (name/id), sort, show all / favorites / ignored.
  mc_nodes: {
    title: "Nodes",
    dynamic: 10000,
    async build() {
      const { ok, d } = await jget(MC + "/nodes");
      const all = (ok && d.nodes) || [];
      const f = MC_FILTER.trim().toLowerCase();
      const list = all.filter((n) =>
        (MC_SHOW === "favorites" ? n.favorite : MC_SHOW === "ignored" ? n.ignored : !n.ignored) &&
        (!f || mcNodeName(n).toLowerCase().includes(f) || n.short_name.toLowerCase().includes(f) ||
         n.id.includes(f)));
      const key = {
        heard: (n) => -(n.last_heard || 0),
        name: (n) => mcNodeName(n).toLowerCase(),
        hops: (n) => n.hops_away ?? 99,
        snr: (n) => -(n.snr ?? -99),
        distance: (n) => n.distance_m ?? 1e12,
      }[MC_SORT];
      list.sort((a, b) => (a.favorite !== b.favorite ? (a.favorite ? -1 : 1) :
        key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
      const items = [
        { type: "ftext", key: "mc_filter", label: "Filter", value: MC_FILTER,
          placeholder: "name or !id", max: 40,
          // Rebuild after the edit commits (build is refused mid-edit).
          onChange: (v) => { MC_FILTER = v; setTimeout(() => window.__shell.reload(), 0); } },
        { type: "fselect", key: "mc_sort", label: "Sort", value: MC_SORT,
          options: ["heard", "name", "hops", "snr", "distance"],
          onChange: (v) => MC_SORT = v },
        { type: "fselect", key: "mc_show", label: "Show", value: MC_SHOW,
          options: ["all", "favorites", "ignored"], onChange: (v) => MC_SHOW = v },
        { type: "content", html: `<div class="content">` +
          (!ok || !d.bridge_running ? `<div class="warn">cot-bridge not running</div>` : "") +
          `<div class="hint" style="padding-left:0">${list.length} of ${all.length} nodes</div></div>` },
      ];
      list.forEach((n) => {
        const bits = [ago(n.last_heard),
          n.hops_away != null ? n.hops_away + "h" : null,
          n.snr != null ? n.snr + "dB" : null,
          n.battery != null ? n.battery + "%" : null,
          n.distance_m != null ? mcDist(n.distance_m) : null].filter(Boolean);
        items.push({ type: "nav", label: `${n.favorite ? "★ " : ""}${mcNodeName(n)}  ·  ${bits.join(" · ")}`,
          onEnter: (S) => S.go("mc_node", { id: n.id }) });
      });
      return { items };
    },
  },

  // Node detail + actions.
  mc_node: {
    title: "Node",
    dynamic: 5000,
    async build(params) {
      MC_NODE = (params && params.id) || MC_NODE;
      const { ok, d } = await jget(MC + "/nodes");
      const n = ((ok && d.nodes) || []).find((x) => x.id === MC_NODE);
      if (!n) return { items: [{ type: "content", html:
        `<div class="content"><div class="off">node ${esc(MC_NODE)} not in node list</div></div>` }] };
      const kv = (k, v) => `<div class="kv"><span class="k">${k}</span><span>${esc(v ?? "—")}</span></div>`;
      const pos = n.position;
      let h = `<div class="content">` +
        kv("Name", `${n.long_name} (${n.short_name})`) + kv("ID", n.id) +
        kv("Hardware", n.hw_model) + kv("Role", n.role) +
        kv("Last heard", ago(n.last_heard)) +
        kv("Hops / SNR", `${n.hops_away ?? "—"} / ${n.snr ?? "—"} dB`) +
        kv("Battery", n.battery != null ? `${n.battery}%` +
          (n.voltage ? ` (${n.voltage.toFixed(2)} V)` : "") : null) +
        kv("Ch util / air TX", n.channel_util != null ?
          `${n.channel_util.toFixed(1)}% / ${(n.air_util_tx || 0).toFixed(1)}%` : null) +
        kv("Uptime", n.uptime != null ? fmtDur(n.uptime) : null) +
        kv("Position", pos ? `${pos.lat.toFixed(5)}, ${pos.lon.toFixed(5)}` +
          (pos.alt != null ? ` · ${pos.alt} m` : "") : null) +
        kv("Distance", n.distance_m != null ? `${mcDist(n.distance_m)} · ${n.bearing}°` : null);
      const tr = n.traceroute;
      if (tr) {
        let t = tr.pending ? "waiting for reply…" : tr.error ? `failed: ${tr.error}` : "";
        if (tr.route) {
          const hop = (ids, snr) => ids.map((x, i) => (i ? `${snr[i - 1] ?? "?"}dB → ` : "") + x).join(" ");
          t = hop(tr.route.towards, tr.route.snr_towards) +
            (tr.route.back.length ? ` | back: ${hop(tr.route.back, tr.route.snr_back)}` : "");
        }
        h += kv(`Traceroute (${ago(tr.ts)})`, t);
      }
      h += `</div>`;
      return { items: [
        { type: "content", html: h },
        { type: "nav", label: "» DIRECT MESSAGE",
          onEnter: (S) => S.go("mc_chat", { chat: n.id, title: mcNodeName(n) }) },
        { type: "button", label: "» TRACEROUTE", onEnter: (S) => mcAction(S, n.id, "traceroute") },
        { type: "button", label: "» REQUEST POSITION", onEnter: (S) => mcAction(S, n.id, "request_position") },
        { type: "button", label: "» REQUEST TELEMETRY", onEnter: (S) => mcAction(S, n.id, "request_telemetry") },
        { type: "button", label: n.favorite ? "» UNFAVORITE" : "» FAVORITE",
          onEnter: (S) => mcFlag(S, n.id, "favorite", !n.favorite) },
        { type: "button", label: n.ignored ? "» UNIGNORE" : "» IGNORE",
          onEnter: (S) => mcFlag(S, n.id, "ignored", !n.ignored) },
      ] };
    },
  },

  // Distance + bearing to every node with a position (nearest first).
  mc_positions: {
    title: "Positions",
    dynamic: 10000,
    async build() {
      const { ok, d } = await jget(MC + "/nodes");
      const me = ok ? d.me : null;
      const list = ((ok && d.nodes) || []).filter((n) => n.position && !n.ignored)
        .sort((a, b) => (a.distance_m ?? 1e12) - (b.distance_m ?? 1e12));
      let h = `<div class="content">`;
      h += me && me.position
        ? `<div class="kv"><span>me: ${me.position.lat.toFixed(5)}, ${me.position.lon.toFixed(5)}</span></div>`
        : `<div class="warn">this node has no position — distance/bearing unavailable</div>`;
      if (!list.length) h += `<div class="off">no nodes with a position</div>`;
      h += `</div>`;
      const items = [{ type: "content", html: h }];
      list.forEach((n) => items.push({ type: "nav",
        label: `${mcNodeName(n)}  ·  ${mcDist(n.distance_m)}` +
          (n.bearing != null ? ` · ${n.bearing}°` : "") +
          `  ·  ${ago(n.position.time || n.last_heard)}`,
        onEnter: (S) => S.go("mc_node", { id: n.id }) }));
      return { items };
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
        // Frequency readout row, updated live from the fields below. Load its
        // initial descriptor (MF) once per build from the pure /freq endpoint.
        MF = await mtFetchFreq();
        const freqRow = { type: "content", html: mtFreqLine(MF), _freq: true };
        // Slot max follows the region/preset grid (MF.num_slots), so narrow
        // regions and 125 kHz presets are bounded correctly instead of a fixed
        // guess. Fall back to 104 until the first descriptor loads.
        const slotMax = (MF && MF.num_slots) || 104;
        items.push(
          { type: "ftext", key: "owner", label: "Long name", value: M.owner, max: 39, onChange: (v) => M.owner = v },
          { type: "ftext", key: "owner_short", label: "Short name", value: M.owner_short, max: 4, onChange: (v) => M.owner_short = v },
          { type: "fselect", key: "modem_preset", label: "Modem preset", options: MODEMS, value: M.modem_preset,
            onChange: (v) => { M.modem_preset = v; mtRefreshFreq(freqRow, true); } },
          { type: "fnum", key: "frequency_slot", label: "Freq slot (0=auto)", value: M.frequency_slot, min: 0, max: slotMax,
            onChange: (v) => { M.frequency_slot = v; mtRefreshFreq(freqRow, false); } },
          freqRow,
          { type: "ftext", key: "channel_name", label: "Channel name", value: M.channel_name, max: 11, color: "accent",
            onChange: (v) => { M.channel_name = v; mtRefreshFreq(freqRow, true); } },
          { type: "fselect", key: "psk_mode", label: "Encryption key", options: PSK_MODES, value: M.psk_mode, color: "accent", onChange: (v) => M.psk_mode = v },
          { type: "fselect", key: "region", label: "Region", options: REGIONS, value: M.region, color: "accent",
            onChange: (v) => { M.region = v; mtRefreshFreq(freqRow, true); } },
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
        TXR = mt.tx_min_interval_secs ?? 2;
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
        if (v.install_pending) {
          // New code is in the checkout but the venv was never reinstalled, so
          // the running version differs from the repo even when behind === 0.
          statusHtml = `<span class="warn">installed ${esc(installed)} ≠ ` +
            `repo ${esc(v.checkout || "?")} — reinstall needed</span>`;
          canUpdate = true;
        } else if (behind === 0) {
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
  // Node settings: per-device UI preferences (display theme) plus the power
  // actions. Theme is a browser-local preference only — it never touches the
  // config pipeline or the API, so it refreshes instantly and per-client.
  node: {
    title: "Node Settings",
    async build() {
      return {
        items: [
          { type: "content", html: `<div class="content"><div class="page-title" ` +
            `style="padding-left:0">Display</div></div>` },
          { type: "fselect", key: "theme", label: "Theme", options: THEMES,
            value: getTheme(), onChange: (v) => setTheme(v) },
          { type: "content", html: `<div class="content"><div class="hint" ` +
            `style="padding-left:0">Classic: neon green terminal. htop: muted ` +
            `ANSI colours and the Terminus console font. Day: dark on light ` +
            `for sunlight. Dark: low-glare grey. Night: red on black, turn ` +
            `brightness down. Day/dark/night use the Atkinson Hyperlegible ` +
            `font. Saved in this browser only.</div></div>` },
          { type: "content", html: `<div class="content"><div class="page-title" ` +
            `style="padding-left:0">Power</div></div>` },
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

  // Network (NomadNet-style): lxmf.delivery announces this node has heard
  // on-air (in-memory, cleared on daemon restart). Kept off the landing page so
  // a busy/internet-connected network can't flood the contact list. Each row
  // can be added to contacts or ignored; neither transmits anything.
  rns_heard: {
    title: "Network",
    dynamic: 5000,
    async build() {
      const items = [];
      const r = await jget("/api/v1/messaging/rns/heard");
      const heard = (r.ok && r.d.heard) ? r.d.heard : [];
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="hint" style="padding-left:0">LXMF announces heard on-air, newest first. ` +
        `Not saved — cleared when the daemon restarts. Tap to add as a contact.</div>` +
        (heard.length ? "" : `<div class="off">nothing heard yet</div>`) + `</div>` });
      heard.sort((a, b) => (b.last_heard || 0) - (a.last_heard || 0));
      heard.forEach((h) => {
        const who = (h.announced_name || "").trim() || (h.lxmf_hash || "").slice(0, 10);
        const key = h.pubkey ? "" : " · no key";
        items.push({ type: "button",
          label: `+ ${who}  ·  ${(h.lxmf_hash || "").slice(0, 10)}${key}  ·  ` +
            `${h.count || 1}×, ${ago(h.last_heard)} ago`,
          onEnter: (S) => rnsHeardAdd(S, h.lxmf_hash, who),
          action: { label: "ignore", onRun: (S) => rnsHeardDelete(S, h.lxmf_hash, who) } });
      });
      return { items };
    },
  },

  // Add Contact: by lxmf.delivery destination hash (the standard address any
  // LXMF client shows) + optional nickname, or one-tap from Nucleus nodes found
  // on the WiFi mesh. My own address is on the Reticulum landing page.
  rns_add: {
    title: "Add Contact",
    async build() {
      const st = await jget("/api/v1/messaging/rns/status");
      const items = [];
      const s = st.ok ? st.d : {};
      if (!s.enabled) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="off">Reticulum messaging is disabled</div></div>` });
        return { items };
      }
      // ── add by destination hash (keyless, transmits nothing) ──
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="page-title" style="padding-left:0">By address</div>` +
        `<div class="hint" style="padding-left:0">Their lxmf.delivery address (32 hex) from ` +
        `Sideband, MeshChat, NomadNet or another node. Saved with no key yet — ` +
        `<b>req path</b> fetches it. Nothing is sent.</div></div>` });
      items.push({ type: "ftext", label: "Address", value: RNS_ADD_HASH, max: 64,
        onChange: (v) => RNS_ADD_HASH = v });
      items.push({ type: "ftext", label: "Nickname", value: RNS_ADD_NAME, max: 40,
        placeholder: "optional", onChange: (v) => RNS_ADD_NAME = v });
      items.push({ type: "button", label: "» Add contact", onEnter: rnsAddHash });

      // ── Nucleus nodes on the WiFi mesh (pull their cards) ────
      const [r, mp] = await Promise.all([
        jget("/api/v1/messaging/rns/contacts"), jget("/api/v1/messaging/rns/mesh-peers")]);
      const contacts = (r.ok && r.d.contacts) ? r.d.contacts : [];
      const mesh = (mp.ok && mp.d.peers) ? mp.d.peers : [];
      const known = new Set(contacts.map((c) => c.lxmf_hash));
      const addable = mesh.filter((p) => p.card && !known.has(p.card.lxmf_hash));
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="page-title" style="padding-left:0">On the WiFi mesh</div>` +
        (addable.length ? "" : `<div class="off">no new nodes found on the mesh</div>`) + `</div>` });
      addable.forEach((p) => {
        const who = p.card.name || String(p.card.id || p.ip);
        items.push({ type: "button", label: `+ ${who} (${p.ip})`,
          onEnter: (S) => rnsAddMesh(S, p.card) });
      });
      return { items };
    },
  },

  // Reticulum/LXMF conversation. params: {hash, host}. The log, then a contact
  // bar (name · path · REQ PATH) pinned just above the compose box; tapping the
  // bar opens the contact page (rename / remove / full address). Opening the
  // conversation marks it read.
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
      const [r, pr] = await Promise.all([
        jget("/api/v1/messaging/rns/messages?peer=" + encodeURIComponent(peer)),
        jget("/api/v1/messaging/rns/contacts/" + encodeURIComponent(peer) + "/path")]);
      if (r.ok) (r.d.messages || []).forEach(rnsUpsert);
      const list = RNS_CACHE[peer] || [];
      if (list.length) rnsMarkRead(peer, list[list.length - 1].ts);
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
      if (list.length) items.push({ type: "button", label: "» Clear chat log",
        onEnter: (S) => rnsClearChat(S, peer, p.host) });
      items.push({ type: "nav",
        label: `${p.host || peer.slice(0, 10)}  ·  ${rnsPathStr(pr, peer)}  ·  contact »`,
        to: "rns_contact",
        onEnter: (S) => S.go("rns_contact", { hash: peer }),
        action: { label: "req path", onRun: (S) => rnsRequestPath(S, peer) } });
      items.push({ type: "compose", key: "rns_text", value: RNS_DRAFT, max: 200,
        placeholder: "Type a message",
        sendLabel: "Send", onChange: (v) => RNS_DRAFT = v,
        onSubmit: (S, text) => rnsSend(S, peer, text) });
      return { items };
    },
  },

  // Contact page: everything about one saved contact — full address, names,
  // key/path state — and its admin actions (rename, req path, remove). Rename
  // and remove transmit nothing; req path emits one path request.
  rns_contact: {
    title: "Contact",
    dynamic: 5000,
    async build(params) {
      const peer = (params || {}).hash;
      const items = [];
      const [cr, pr] = await Promise.all([
        jget("/api/v1/messaging/rns/contacts"),
        jget("/api/v1/messaging/rns/contacts/" + encodeURIComponent(peer || "") + "/path")]);
      const c = ((cr.ok && cr.d.contacts) || []).find((x) => x.lxmf_hash === peer);
      if (!c) {
        items.push({ type: "content", html: `<div class="content">` +
          `<div class="off">contact not found (removed?)</div></div>` });
        return { items };
      }
      const who = rnsName(c);
      items.push({ type: "content", html: `<div class="content">` +
        `<div class="page-title" style="padding-left:0">${esc(who)}</div>` +
        `<div class="kv"><span class="k">address</span><span style="word-break:break-all">` +
        `<b>${esc(c.lxmf_hash)}</b></span></div>` +
        `<div class="kv"><span class="k">nickname</span><span>${esc(c.name || "—")}</span></div>` +
        `<div class="kv"><span class="k">announced name</span><span>${esc(c.announced_name || "—")}</span></div>` +
        `<div class="kv"><span class="k">key</span><span class="${c.pubkey ? "ok" : "warn"}">` +
        `${c.pubkey ? "known" : "not yet — req path"}</span></div>` +
        `<div class="kv"><span class="k">path</span><span>${esc(rnsPathStr(pr, peer))}</span></div>` +
        `<div class="kv"><span class="k">added via</span><span>${esc(c.source || "—")}` +
        `${c.added ? ", " + ago(c.added) + " ago" : ""}</span></div>` +
        `<div class="kv"><span class="k">last heard</span><span>` +
        `${c.last_seen ? ago(c.last_seen) + " ago" : "never"}</span></div></div>` });
      items.push({ type: "button", label: "» Message",
        onEnter: (S) => S.go("rns_chat", { hash: peer, host: who }) });
      items.push({ type: "button", label: "» Rename (nickname)",
        onEnter: (S) => rnsRename(S, peer, c.name || "") });
      items.push({ type: "button", label: "» Request path",
        onEnter: (S) => rnsRequestPath(S, peer) });
      items.push({ type: "button", label: "» Remove contact",
        onEnter: (S) => rnsRemove(S, peer, who) });
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

// ── Meshtastic client actions (relayed to cot-bridge) ──────────
async function mcSend(S, chat, text) {
  const reply_id = MC_REPLY && MC_REPLY.chat === chat ? MC_REPLY.pkt_id : null;
  const { ok, d } = await jsend("POST", MC + "/messages", { chat, text, reply_id });
  if (!ok) return S.msg("send failed: " + (d.detail || "error"), false);
  MC_DRAFT = "";
  MC_REPLY = null;
  S.msg("sent");
  return S.reload();
}

async function mcReact(S, chat, reply_id, emoji) {
  const { ok, d } = await jsend("POST", MC + "/react", { chat, reply_id, emoji });
  if (!ok) return S.msg("react failed: " + (d.detail || "error"), false);
  return S.back();
}

async function mcAction(S, id, action) {
  const { ok, d } = await jsend("POST", `${MC}/nodes/${encodeURIComponent(id)}/${action}`);
  if (!ok) return S.msg(action.replace("_", " ") + " failed: " + (d.detail || "error"), false);
  S.msg(action.replace("_", " ") + " sent — the reply updates this page");
  return S.reload();
}

// Run one link test from the LINK TEST page; the result lands in LT.result.
async function ltRun(S, kind, arg) {
  if (LT.busy) return S.msg("a test is already running", false);
  let url, body, label;
  if (kind === "ping" || kind === "iperf") {
    const peer = ltMeshPeers((await jget("/api/v1/status")).d || {})[LT.mesh];
    if (!peer) return S.msg("pick a peer", false);
    if (kind === "ping") {
      url = "/api/v1/linktest/ping"; body = { link: LT.link, peer_id: peer, count: 5 };
      label = "ping";
    } else {
      url = "/api/v1/linktest/iperf";
      body = { link: LT.link, peer_id: peer, secs: 5, reverse: !!arg };
      label = "throughput test (5 s)";
    }
  } else if (kind === "traceroute") {
    if (!arg) return S.msg("pick a node", false);
    return mcAction(S, arg.id, "traceroute");
  } else {
    if (!arg) return S.msg("pick a contact", false);
    url = "/api/v1/linktest/rnprobe"; body = { dest: arg }; label = "rnprobe";
  }
  LT.busy = true;
  S.msg(label + " running…");
  try {
    const { ok, d } = await jsend("POST", url, body);
    if (!ok) {
      LT.result = `<div class="warn">${esc(label)} failed: ` +
        `${esc(typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail || "error"))}</div>`;
      S.msg(label + " failed", false);
    } else {
      LT.result = ltResultHtml(kind, d);
      S.msg(label + " done");
    }
  } finally {
    LT.busy = false;
  }
  return S.reload();
}

async function mcFlag(S, id, kind, on) {
  const { ok, d } = await jsend("PUT", `${MC}/nodes/${encodeURIComponent(id)}/${kind}`, { on });
  if (!ok) return S.msg("failed: " + (d.detail || "error"), false);
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

// Clear one conversation's chat log after confirmation. The contact is kept.
// Transmits nothing.
async function rnsClearChat(S, peer, who) {
  if (typeof confirm === "function" &&
      !confirm("Clear chat log with " + (who || peer) + "?")) return;
  const { ok, d } = await jsend("DELETE",
    "/api/v1/messaging/rns/messages?peer=" + encodeURIComponent(peer));
  if (!ok) return S.msg("clear failed: " + (d.detail || "error"), false);
  delete RNS_CACHE[peer];
  S.msg("chat log cleared");
  return S.reload();
}

// Remove a contact after confirmation. Transmits nothing.
async function rnsRemove(S, peer, who) {
  if (typeof confirm === "function" &&
      !confirm("Remove contact " + (who || peer) + "?")) return;
  const { ok, d } = await jsend("DELETE",
    "/api/v1/messaging/rns/contacts/" + encodeURIComponent(peer));
  if (!ok) return S.msg("remove failed: " + (d.detail || "error"), false);
  // The contact page no longer has anything to show — leave it.
  S.msg("removed");
  return S.back();
}

// Set this node's name (WiFi/LoRa chat sender + LXMF announce display name).
// Persists messaging.display_name and live-updates the daemon; blank resets to
// the hostname. Transmits nothing — peers see it on the next announce.
async function rnsSetMyName(S, cur) {
  const name = (typeof prompt === "function")
    ? prompt("This node's name (chat + Reticulum announces). Blank = hostname:", cur || "") : null;
  if (name === null) return;
  const { ok, d } = await jsend("PUT", "/api/v1/messaging/name", { name: name.trim() });
  if (!ok) return S.msg("rename failed: " + (d.detail || "error"), false);
  S.msg(`name set to ${d.name} — announce to publish it` + (d.live ? "" : " (daemon not running)"));
  return S.reload();
}

// Promote a heard announce to a saved contact (keyed when the key was
// recovered from the announce). Transmits nothing; drops it from the buffer.
async function rnsHeardAdd(S, peer, who) {
  const { ok, d } = await jsend("POST",
    "/api/v1/messaging/rns/heard/" + encodeURIComponent(peer) + "/add", {});
  if (!ok) return S.msg("add failed: " + (d.detail || "error"), false);
  S.msg("added " + (who || "contact") + " to contacts");
  return S.reload();
}

// Dismiss a heard announce from the buffer. Transmits nothing.
async function rnsHeardDelete(S, peer, who) {
  const { ok, d } = await jsend("DELETE",
    "/api/v1/messaging/rns/heard/" + encodeURIComponent(peer));
  if (!ok) return S.msg("dismiss failed: " + (d.detail || "error"), false);
  S.msg("dismissed");
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

// ── modem73 page ──────────────────────────────────────────
// Targets: RX level -10..-30 dBFS, SNR >= 15 dB, BER < 1 %.
function m73Cls(kind, x) {
  if (x == null) return "";
  if (kind === "level") return (x === 0 || x > -6 || x < -40) ? "off" : "ok";
  if (kind === "snr") return x >= 15 ? "ok" : x >= 10 ? "warn" : "off";
  if (kind === "ber") return x < 1 ? "ok" : x < 3 ? "warn" : "off";
  return "";
}
const m73Num = (x, d = 1) => (x == null ? "—" : Number(x).toFixed(d));
const m73Val = (kind, x, unit, d = 1) =>
  `<b class="${m73Cls(kind, x)}">${x == null ? "—" : m73Num(x, d) + unit}</b>`;
// Non-selectable section header, styled like a section row.
const m73Head = (t) =>
  `<div class="row row-section"><span class="cur"></span><span class="label">${t}</span></div>`;

// Reading blocks (HTML); the page places the settings after each.
function m73Sections(v) {
  const st = v.status || {};
  const lv = v.live || {};
  // Callsign-less entries are noise (hiss false syncs/decodes).
  const att = (v.attempts || []).filter((a) => a.callsign && a.ok != null);
  const frames = (v.frames || []).filter((f) => f.callsign);
  const a = att[0];
  const f = frames[0];
  let top = "";
  if (!v.online) top += `<div class="off">modem73 offline: ${esc(v.error || "unknown")}</div>`;
  if (v.status && !st.audio_connected) top += `<div class="off">sound card disconnected</div>`;

  const last10 = att.slice(0, 10);
  const nOk = last10.filter((x) => x.ok).length;
  let rx = a
    ? `<div class="kv"><span>from <b>${esc(a.callsign)}</b></span>` +
      `<span>mode <b>${esc(`${a.modulation ?? "?"} ${a.code_rate ?? ""}`)}</b></span>` +
      `<span>last ${last10.length}: <b class="${nOk === last10.length ? "ok" : "off"}">` +
      `${nOk} OK / ${last10.length - nOk} FAIL</b></span></div>`
    : `<div class="kv"><span class="off">nothing heard</span></div>`;
  rx += `<div class="kv"><span>level ${m73Val("level", f ? f.level_db : null, " dBFS")}</span>` +
    `<span>SNR ${m73Val("snr", a ? a.snr : null, " dB")}</span>` +
    `<span>BER ${m73Val("ber", a && a.ok ? a.ber_pct : null, "%", 2)}</span></div>`;

  const tx = `<div class="kv"><span>mode <b>${esc(lv.mode || lv.modulation || "—")}</b></span></div>`;

  let table = "";
  if (att.length) {
    table = `<table><tr><th>When</th><th>From</th><th>Mode</th><th>SNR</th><th>Result</th></tr>`;
    att.forEach((x) => {
      table += `<tr><td>${x.time ? ago(x.time) : "?"}</td><td>${esc(x.callsign)}</td>` +
        `<td>${esc(`${x.modulation ?? "?"} ${x.code_rate ?? ""}`)}</td>` +
        `<td class="${m73Cls("snr", x.snr)}">${m73Num(x.snr)}</td>` +
        `<td class="${x.ok ? "ok" : "off"}">${x.ok ? `OK BER ${m73Num(x.ber_pct, 2)}%` : "CRC FAIL"}</td></tr>`;
    });
    table += `</table>`;
  }
  return { top, rx, tx, table };
}

async function m73Test(S) {
  const { ok, d } = await jsend("POST", "/api/v1/modem73/test");
  if (!ok) return S.msg("test frame failed: " + (d.detail || "error"), false);
  S.msg(`test frame #${d.seq} queued — check the far node's modem73 page`);
}

async function m73Clear(S) {
  const { ok, d } = await jsend("POST", "/api/v1/modem73/clear");
  if (!ok) return S.msg("clear failed: " + (d.detail || "error"), false);
  S.msg("frames cleared");
}

// Live control changes are debounced so holding ▲▼ sends one request.
const M73_TIMERS = {};
function m73Live(key, value) {
  clearTimeout(M73_TIMERS[key]);
  M73_TIMERS[key] = setTimeout(async () => {
    const S = window.__shell;
    const r = key === "tx_level"
      ? await jsend("PUT", "/api/v1/modem73/tx_level", { tx_level: value })
      : await jsend("PUT", "/api/v1/modem73/mixer", { [key]: value });
    if (S) S.msg(r.ok ? `${key} = ${value} (live, not saved)` :
      `${key} failed: ` + (r.d.detail || "error"), r.ok);
  }, 300);
}

async function m73Save(S) {
  if (!M73_EDIT) return S.msg("nothing to save", false);
  S.msg("saving + applying…");
  const body = {
    mode: M73_EDIT.mode,
    tx_level: parseInt(M73_EDIT.tx_level, 10),
    rx_gain: parseInt(M73_EDIT.rx_gain, 10),
    tx_gain: parseInt(M73_EDIT.tx_gain, 10),
    enabled: M73_EDIT.enabled,
    callsign: (M73_EDIT.callsign || "").trim(),  // blank -> node id
  };
  const { ok, d } = await jsend("POST", "/api/v1/modem73/save", body);
  if (!ok) return S.msg("save failed: " + JSON.stringify(d.detail || "error"), false);
  S.msg(d.changed.length ? "saved — restarted: " + (d.units_restarted.join(", ") || "none")
    : "saved — no changes");
  M73_EDIT = null;
  return S.reload();
}

function m73WsOpen(S) {
  m73WsClose();
  M73_WS_POLL = false;
  let ws;
  try {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + "/api/v1/modem73/ws");
  } catch (e) { M73_WS_POLL = true; return; }
  M73_WS = ws;
  ws.onmessage = (ev) => {
    let obj;
    try { obj = JSON.parse(ev.data); } catch (e) { return; }
    if (obj.event !== "modem73") return;
    M73_VIEW = obj.view;
    if (S) S.reload();
  };
  ws.onclose = () => { if (M73_WS === ws) { M73_WS = null; M73_WS_POLL = true; } };
  ws.onerror = () => { M73_WS_POLL = true; };
}

function m73WsClose() {
  if (M73_WS) {
    try { M73_WS.onclose = null; M73_WS.close(); } catch (e) {}
    M73_WS = null;
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

