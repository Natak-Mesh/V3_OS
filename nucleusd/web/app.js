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

loadCfg();
loadStatus();
