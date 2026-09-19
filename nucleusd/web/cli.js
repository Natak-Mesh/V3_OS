// Nucleus V3 — CLI/TUI shell. Owns the single interaction model for every
// page: a status header, a scrollable list, and fixed ▲ ▼ ENTER / BACK.
// Pages are declared in app.js (PAGES); this file renders their `items`
// model, drives the cursor/focus, maps keyboard + buttons, and refreshes
// dynamic pages without moving the cursor or interrupting an edit.

(function () {
  const view = document.getElementById("view");
  const cEnter = document.getElementById("c-enter");
  const cBack = document.getElementById("c-back");

  const state = {
    page: "home",     // current page key
    params: null,     // reserved for parameterised pages
    items: [],        // resolved item model for the current page
    sel: 0,           // cursor index into `items`
    editing: false,   // true while a field row is in edit mode
    stack: [],        // nav history for BACK
    timer: null,      // dynamic-refresh interval id
    building: false,  // guards overlapping builds
  };

  // Shell helpers handed to page actions.
  const S = {
    msg(text, ok = true) {
      const m = document.getElementById("shell-msg");
      if (!m) return;
      m.textContent = text || "";
      m.className = "msg " + (text ? (ok ? "ok" : "err") : "");
    },
    reload() { return build(true); },
  };
  window.__shell = S;

  const selectable = (it) => it && it.type !== "content";
  function firstSelectable() {
    for (let i = 0; i < state.items.length; i++) if (selectable(state.items[i])) return i;
    return -1;
  }

  function escHtml(s) {
    return String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }
  function escAttr(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ── Rendering ────────────────────────────────────────────────
  function render() {
    let html = `<div class="page-title">${(PAGES[state.page] || {}).title || ""}</div>`;
    html += `<div id="shell-msg" class="msg"></div>`;
    state.items.forEach((it, idx) => {
      if (it.type === "content") { html += it.html || ""; return; }
      const sel = idx === state.sel;
      const editing = sel && state.editing;
      const cls = "row" + (sel ? " sel" : "") + (editing ? " editing" : "");
      const cur = sel ? "&gt;" : "";
      let valHtml = "";
      if (it.type === "fselect" || it.type === "fnum" || it.type === "ftext") {
        if (editing && it.type === "ftext") {
          valHtml = `<span class="val"><input id="edit-input" value="${escAttr(it.value)}"` +
            (it.max ? ` maxlength="${it.max}"` : "") + `></span>`;
        } else {
          const shown = (it.value === "" || it.value == null) ? "—" : it.value;
          valHtml = `<span class="val">${escHtml(shown)}${editing ? " ◂▸" : ""}</span>`;
        }
      }
      html += `<div class="row-wrap" data-idx="${idx}"><div class="${cls}">` +
        `<span class="cur">${cur}</span>` +
        `<span class="label">${escHtml(it.label || "")}</span>${valHtml}</div></div>`;
    });
    view.innerHTML = html;

    // Row taps: select, then activate/edit (rows stay directly tappable).
    view.querySelectorAll(".row-wrap").forEach((el) => {
      const idx = parseInt(el.getAttribute("data-idx"), 10);
      el.addEventListener("click", (ev) => {
        const t = ev.target.tagName;
        if (t === "INPUT" || t === "SELECT" || t === "TEXTAREA" || t === "BUTTON") return;
        if (state.sel === idx && !state.editing) return activate();
        state.editing = false;
        state.sel = idx;
        render();
      });
    });

    if (state.editing) {
      const inp = document.getElementById("edit-input");
      if (inp) { inp.focus(); inp.select && inp.select(); }
    }
    scrollSelIntoView();
  }

  function scrollSelIntoView() {
    const rows = Array.from(view.querySelectorAll(".row-wrap"));
    const el = rows.find((r) => parseInt(r.getAttribute("data-idx"), 10) === state.sel);
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
  }

  // ── Build (fetch a page's item model) ────────────────────────
  async function build(isRefresh) {
    const page = PAGES[state.page];
    if (!page || state.building) return;
    // Never rebuild under the cursor mid-edit on a dynamic refresh.
    if (isRefresh && state.editing) return;
    state.building = true;
    try {
      const model = await page.build(state.params);
      const prevSel = state.sel;
      state.items = model.items || [];
      if (!isRefresh) {
        state.sel = firstSelectable();
        state.editing = false;
      } else {
        state.sel = selectable(state.items[prevSel]) ? prevSel : firstSelectable();
      }
      render();
    } catch (e) {
      view.innerHTML = `<div class="content off">page error: ${escHtml(e.message)}</div>`;
    } finally {
      state.building = false;
    }
  }

  function startTimer() {
    stopTimer();
    const ms = (PAGES[state.page] || {}).dynamic;
    if (ms) state.timer = setInterval(() => build(true), ms);
  }
  function stopTimer() {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  async function go(pageKey, push = true) {
    if (!PAGES[pageKey]) return;
    if (push && pageKey !== state.page) state.stack.push(state.page);
    state.page = pageKey;
    state.sel = 0;
    state.editing = false;
    stopTimer();
    await build(false);
    startTimer();
  }

  function back() {
    if (state.editing) { commitEdit(); return; }
    const prev = state.stack.pop();
    go(prev || "home", false);
  }

  // ── Cursor + activation ──────────────────────────────────────
  function move(delta) {
    if (state.editing) return adjust(delta); // in edit mode ▲▼ change value
    if (!state.items.length) return;
    let i = state.sel;
    for (let n = 0; n < state.items.length; n++) {
      i = (i + delta + state.items.length) % state.items.length;
      if (selectable(state.items[i])) { state.sel = i; break; }
    }
    render();
  }

  function activate() {
    const it = state.items[state.sel];
    if (!it) return;
    if (it.type === "nav") return go(it.to);
    if (it.type === "button") { S.msg(""); return it.onEnter && it.onEnter(S); }
    if (state.editing) return commitEdit();
    state.editing = true;
    render();
  }

  // In-edit value change for fselect (cycle) / fnum (step).
  function adjust(delta) {
    const it = state.items[state.sel];
    if (!it) return;
    if (it.type === "fselect") {
      const opts = it.options || [];
      let i = Math.max(0, opts.indexOf(it.value));
      i = (i + delta + opts.length) % opts.length;
      it.value = opts[i];
      it.onChange && it.onChange(it.value);
    } else if (it.type === "fnum") {
      let v = parseInt(it.value, 10);
      if (Number.isNaN(v)) v = it.min ?? 0;
      v += delta;
      if (it.min != null) v = Math.max(it.min, v);
      if (it.max != null) v = Math.min(it.max, v);
      it.value = v;
      it.onChange && it.onChange(v);
    }
    render();
  }

  function commitEdit() {
    const it = state.items[state.sel];
    if (it && it.type === "ftext") {
      const inp = document.getElementById("edit-input");
      if (inp) { it.value = inp.value; it.onChange && it.onChange(inp.value); }
    }
    state.editing = false;
    render();
  }

  // ── Controls: buttons + keyboard + hold-to-repeat ────────────
  cEnter.addEventListener("click", activate);
  cBack.addEventListener("click", back);

  // Hold-to-repeat on ▲ ▼ so long lists don't need many taps.
  function holdRepeat(btn, fn) {
    let t1, t2;
    const start = (e) => {
      e.preventDefault();
      fn();
      t1 = setTimeout(() => { t2 = setInterval(fn, 120); }, 400);
    };
    const stop = () => { clearTimeout(t1); clearInterval(t2); };
    btn.addEventListener("mousedown", start);
    btn.addEventListener("touchstart", start, { passive: false });
    ["mouseup", "mouseleave", "touchend", "touchcancel"].forEach((ev) =>
      btn.addEventListener(ev, stop));
  }
  holdRepeat(document.getElementById("c-up"), () => move(-1));
  holdRepeat(document.getElementById("c-down"), () => move(1));

  document.addEventListener("keydown", (e) => {
    // While typing in a text field, only Enter/Escape are shell actions.
    const t = e.target && e.target.tagName;
    const typing = t === "INPUT" || t === "TEXTAREA" || t === "SELECT";
    if (typing && e.key !== "Enter" && e.key !== "Escape") return;
    switch (e.key) {
      case "ArrowUp": e.preventDefault(); move(-1); break;
      case "ArrowDown": e.preventDefault(); move(1); break;
      case "ArrowLeft": if (state.editing) { e.preventDefault(); adjust(-1); } break;
      case "ArrowRight": if (state.editing) { e.preventDefault(); adjust(1); } break;
      case "Enter": e.preventDefault(); activate(); break;
      case "Escape": e.preventDefault(); back(); break;
    }
  });

  // ── Boot ─────────────────────────────────────────────────────
  refreshHeaderOnce();
  setInterval(refreshHeader, 5000);
  go("home", false);
})();

