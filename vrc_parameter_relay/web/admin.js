"use strict";

// Admin panel — a browser recreation of the desktop window.
// Same operations, same gestures: grip-drag cards between and inside
// categories, grip-drag categories to reorder, drag parameters in from the
// dock, ⋯ menus for edit/colour/copy/delete. Every edit is one "op" message
// against the same core the desktop UI drives.

const token = new URLSearchParams(location.search).get("k") || "";
const $ = (id) => document.getElementById(id);

let ws = null, retryMs = 1000;
let S = null;                 // latest admin_state snapshot
let values = {};              // live parameter values
let filter = "";
let typing = null;            // field being edited — don't redraw it
let drag = null;              // {kind:"card"|"cat"|"param", ...}

const COLORS = { green: "#4af58c", blue: "#7aa7ff", cyan: "#56d9f2",
                 yellow: "#ffb454", red: "#ff5561", purple: "#d78cff" };
const KINDS = { toggle: "Toggle switch", button: "Hold button",
                slider: "Slider", int: "Number stepper" };
const KIND_FOR = { Bool: ["toggle", "button"], Int: ["int", "slider", "toggle", "button"],
                   Float: ["slider"] };
// Int toggles/buttons send a value; the labels say which moment it belongs to
const SWITCH_LABELS = { toggle: ["Value when ON", "Value when OFF"],
                        button: ["Value while held", "Value on release"] };
const isIntSwitch = (c) => c.ptype === "Int" && (c.kind === "toggle" || c.kind === "button");
// shown state: Bool = value XOR invert; Int = parameter equals the ON value
function switchOn(c, v) {
  if (c.ptype === "Int") return v != null && Number(v) === Number(c.on ?? 1);
  return c.invert ? !v : !!v;
}
// lowest ON value no other toggle/button on this Int uses (Outfit = 1, 2, 3 …)
function nextOnValue(c) {
  const used = new Set(((S && S.board.controls) || [])
    .filter((x) => x.id !== c.id && x.param === c.param && isIntSwitch(x))
    .map((x) => Number(x.on ?? 1)));
  for (let v = 1; v <= 255; v++) if (!used.has(v)) return v;
  return 1;
}

const esc = (s) => String(s == null ? "" : s)
  .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = (v) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
function fmt(v) {
  if (v === true) return "true"; if (v === false) return "false";
  if (v === null || v === undefined) return "–";
  const n = Number(v);
  return Number.isFinite(n) ? (Number.isInteger(n) ? String(n) : n.toFixed(3)) : String(v);
}
function toast(msg) {
  const el = $("toast"); el.textContent = msg; el.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.remove("show"), 3200);
}
let outbox = [];               // edits made while the socket is down
function send(op, extra) {
  const msg = JSON.stringify({ op, ...extra });
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(msg);
  else {
    // a reconnect is normal (sleep, wifi, tunnel restart) — never drop the
    // user's action silently, replay it once we are back
    outbox.push(msg);
    if (outbox.length > 50) outbox.shift();
    $("statusText").textContent = `reconnecting… (${outbox.length} pending)`;
  }
}
const cats = () => (S && S.board.categories) || [];
const controlsIn = (catId) =>
  ((S && S.board.controls) || []).filter((c) => c.cat === catId);

// -- connection -----------------------------------------------------------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?admin=1&k=${encodeURIComponent(token)}`);
  ws.onopen = () => {
    retryMs = 1000;
    $("statusText").textContent = "connected";
    outbox.splice(0).forEach((m) => ws.send(m));   // replay what was queued
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.t === "admin_state") {
      // snapshots are built when sent and every change produces one, so the
      // newest simply wins — never gate this, a lost reply would freeze us
      S = msg; applyStyle(msg.style); render();
    } else if (msg.t === "param_batch") {  // 50ms server-side batches
      Object.entries(msg.params).forEach(([name, [value]]) => {
        values[name] = value; queuePaint(name);
      });
    } else if (msg.t === "param") {
      values[msg.name] = msg.value; queuePaint(msg.name);
    } else if (msg.t === "error") {
      toast(msg.msg);
    } else if (msg.t === "denied") {
      $("overlayText").textContent =
        "This admin link is invalid or has been reset. Open the app (Share → Admin panel) for the current one.";
      $("overlay").classList.remove("hidden");
      ws.onclose = null; ws.close();
    }
  };
  ws.onclose = () => {
    $("statusText").textContent = "reconnecting…";
    setTimeout(connect, retryMs); retryMs = Math.min(retryMs * 1.6, 15000);
  };
}
let appliedStyle = "";
function applyStyle(style) {
  if (!style) return;
  const key = (style.theme || "neon") + "|" + (style.font || "");
  if (key === appliedStyle) return;  // snapshots repeat it; only real changes may recalc styles
  appliedStyle = key;
  document.body.dataset.theme = style.theme || "neon";
  if (style.font) document.documentElement.style.setProperty("--ui-font", '"' + style.font + '"');
}

// -- header ---------------------------------------------------------------
function renderHead() {
  if (typing !== "avatarName") $("avatarName").value = S.avatar_name || "";
  const badge = $("liveBadge");
  if (!S.avatar_id) { badge.className = "live-badge"; badge.textContent = ""; }
  else { badge.className = "live-badge " + (S.is_live ? "live" : "offline");
         badge.textContent = S.is_live ? "● LIVE" : "✎ OFFLINE"; }
  $("avatarId").innerHTML = S.avatar_id
    ? (S.is_live ? `<span style="color:${COLORS.green}">●</span> ${esc(S.avatar_id)}`
                 : `<span style="color:#c9525f">${esc(S.avatar_id)}</span>`)
    : "waiting for VRChat…";

  const pick = $("presetPick");
  if (document.activeElement !== pick) {
    pick.innerHTML = (S.presets || []).map((p) =>
      `<option value="${esc(p.id)}"${p.id === S.preset_id ? " selected" : ""}>${esc(p.name)}</option>`).join("");
  }
  const vrc = $("vrcChip");
  vrc.textContent = S.is_live ? "VRChat: connected" : "VRChat: searching";
  vrc.dataset.state = S.is_live ? "ok" : "";
  $("guestChip").textContent = `guests: ${S.guests}`;
  $("yoloBtn").classList.toggle("on", !!S.yolo);
  const stop = $("stopBtn");
  stop.textContent = S.sharing ? "■ STOP" : "▶ RESUME";
  stop.className = "btn stop " + (S.sharing ? "active" : "paused");
}

// -- board ----------------------------------------------------------------
function render() {
  if (!S) return;
  // Snapshot values are authoritative: they are built server-side at send
  // time, so they can never be older than a delta we already received.
  // (Letting the local copy win here made every toggle click render its OLD
  // value for a beat — the button visibly flipped back and forth.) The
  // params snapshot seeds values the board doesn't carry, for the dock list.
  const seed = {};
  Object.entries(S.params || {}).forEach(([n, p]) => { seed[n] = p.value; });
  values = { ...seed, ...values, ...S.values };
  renderHead();
  renderBoard();
  renderParams();
  indexValues();
}

function renderBoard() {
  const board = $("board");
  const colCount = window.innerWidth <= 820 ? 1 : 2;
  board.innerHTML = Array.from({ length: colCount }, () => '<div class="col"></div>').join("");
  const cols = [...board.querySelectorAll(".col")];
  cats().forEach((cat, i) => cols[i % colCount].appendChild(catBox(cat)));
}

function catBox(cat) {
  const box = document.createElement("div");
  box.className = "category" + (cat.locked ? " locked" : "");
  box.dataset.cat = cat.id;
  box.dataset.color = cat.color || "green";

  const items = controlsIn(cat.id);
  box.innerHTML = `
    <div class="cat-header">
      <span class="cat-grip" draggable="true" title="Drag to reorder this category">⠿</span>
      <input class="cat-name" value="${esc(cat.name)}">
      <button class="cat-lock" title="${cat.locked ? "Locked: guests can't use it" : "Guests may use it"}">${cat.locked ? "🔒" : "🔓"}</button>
      <button class="cat-menu" title="Colour, copy, delete">⋯</button>
    </div>
    <div class="cat-grid"></div>
    ${items.length ? "" : '<div class="cat-hint">Drop controls here</div>'}`;
  const grid = box.querySelector(".cat-grid");
  items.forEach((c) => grid.appendChild(cardEl(c)));
  return box;
}

function cardEl(c) {
  const el = document.createElement("div");
  el.className = "card";
  el.dataset.id = c.id;
  el.innerHTML = `
    <div class="card-top">
      <span class="card-grip" draggable="true" title="Drag to move this control">⠿</span>
      <span class="label" title="${esc(c.param)}">${esc(c.label || c.param)}</span>
      ${c.invert ? '<span class="card-inv" title="Inverted">⇄</span>' : ""}
      ${isIntSwitch(c) ? `<span class="card-val" title="Sets ${esc(c.param)} to ${esc(c.on ?? 1)}, back to ${esc(c.off ?? 0)}">=${esc(c.on ?? 1)}</span>` : ""}
      <button class="card-menu" title="Edit or remove">⋯</button>
    </div>`;
  el.appendChild(widget(c));
  return el;
}

/* the actual control — same markup/classes as the guest page */
function widget(c) {
  const val = values[c.param];
  if (c.kind === "toggle") {
    const b = document.createElement("button");
    b.className = "switch" + (switchOn(c, val) ? " on" : "");
    b.dataset.set = c.id;
    b.dataset.param = c.param;
    return b;
  }
  if (c.kind === "button") {
    const b = document.createElement("button");
    b.className = "push"; b.textContent = "Hold"; b.dataset.hold = c.id;
    return b;
  }
  if (c.kind === "int") {
    const min = num(c.min), max = c.max == null ? 255 : num(c.max);
    const wrap = document.createElement("div");
    wrap.className = "stepper";
    wrap.innerHTML = `<button data-step="-1" data-id="${esc(c.id)}">−</button>
      <div class="num" data-param="${esc(c.param)}" data-min="${min}" data-max="${max}">${fmt(val)}</div>
      <button data-step="1" data-id="${esc(c.id)}">+</button>`;
    return wrap;
  }
  const min = num(c.min), max = c.max == null ? 1 : num(c.max);
  const row = document.createElement("div");
  row.className = "slide-row";
  const pos = max === min ? 0 : Math.round(((num(val) - min) / (max - min)) * 1000);
  row.innerHTML = `<input type="range" min="0" max="1000" value="${pos}"
      data-slide="${esc(c.id)}" data-min="${min}" data-max="${max}">
    <div class="value" data-param="${esc(c.param)}">${fmt(val)}</div>`;
  return row;
}

function renderParams() {
  const used = new Set(((S.board && S.board.controls) || []).map((c) => c.param));
  const names = Object.keys(S.params || {}).sort((a, b) => a.localeCompare(b));
  $("params").innerHTML = names
    .filter((n) => !filter || n.toLowerCase().includes(filter))
    .map((n) => `<div class="prow" draggable="true" data-param="${esc(n)}"
          data-ptype="${esc(S.params[n].ptype)}" title="${esc(n)}">
        <span class="pname"${used.has(n) ? ' style="color:var(--accent)"' : ""}>${esc(n)}</span>
        <span class="ptype">${esc(S.params[n].ptype)}</span>
        <span class="pval" data-param="${esc(n)}">${fmt(values[n])}</span>
      </div>`).join("");
}

// VRChat streams tracking parameters many times a second; scanning the whole
// document per delta pinned a CPU core. Instead the DOM is indexed once per
// render and deltas repaint only their own nodes, coalesced to one frame.
let valueIndex = new Map();  // param name -> [{kind, el, c}]
function indexValues() {
  valueIndex = new Map();
  const add = (p, entry) => {
    let list = valueIndex.get(p);
    if (!list) valueIndex.set(p, list = []);
    list.push(entry);
  };
  const ctrls = (S && S.board.controls) || [];
  const byId = new Map(ctrls.map((c) => [c.id, c]));
  document.querySelectorAll(".pval, .value, .num").forEach((el) => {
    if (el.dataset.param) add(el.dataset.param, { kind: "text", el });
  });
  document.querySelectorAll(".switch[data-set]").forEach((el) => {
    const c = byId.get(el.dataset.set);
    if (c) add(c.param, { kind: "switch", el, c });
  });
  document.querySelectorAll("input[data-slide]").forEach((el) => {
    const c = byId.get(el.dataset.slide);
    if (c) add(c.param, { kind: "slide", el });
  });
}

const dirtyParams = new Set();
let paintPending = false;
function queuePaint(name) {
  dirtyParams.add(name);
  if (paintPending) return;
  paintPending = true;
  requestAnimationFrame(() => {
    paintPending = false;
    const names = [...dirtyParams];
    dirtyParams.clear();
    names.forEach(paintParam);
  });
}

function paintParam(name) {
  const v = values[name];
  (valueIndex.get(name) || []).forEach(({ kind, el, c }) => {
    if (kind === "text") el.textContent = fmt(v);
    else if (kind === "switch") el.classList.toggle("on", switchOn(c, v));
    else if (kind === "slide" && document.activeElement !== el) {
      const min = num(el.dataset.min), max = num(el.dataset.max);
      el.value = max === min ? 0 : Math.round(((num(v) - min) / (max - min)) * 1000);
    }
  });
}

// -- drag & drop ----------------------------------------------------------
function clearGhosts() {
  document.querySelectorAll(".ghost-card, .ghost-cat").forEach((g) => g.remove());
}
function cardGhost() {
  const g = document.createElement("div");
  g.className = "ghost-card"; return g;
}
function insertIndex(grid, x, y) {
  const cards = [...grid.querySelectorAll(".card")];
  for (let i = 0; i < cards.length; i++) {
    const r = cards[i].getBoundingClientRect();
    if (y < r.top + r.height / 2 || (y < r.bottom && x < r.left + r.width / 2)) return i;
  }
  return cards.length;
}

document.addEventListener("dragstart", (e) => {
  const t = e.target;
  if (t.classList && t.classList.contains("card-grip")) {
    const card = t.closest(".card");
    drag = { kind: "card", id: card.dataset.id };
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", card.dataset.id);
  } else if (t.classList && t.classList.contains("cat-grip")) {
    const box = t.closest(".category");
    drag = { kind: "cat", id: box.dataset.cat };
    box.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", box.dataset.cat);
  } else if (t.classList && t.classList.contains("prow")) {
    drag = { kind: "param", param: t.dataset.param, ptype: t.dataset.ptype };
    e.dataTransfer.effectAllowed = "copy";
    e.dataTransfer.setData("text/plain", t.dataset.param);
  }
});

document.addEventListener("dragend", () => {
  clearGhosts();
  document.querySelectorAll(".dragging").forEach((el) => el.classList.remove("dragging"));
  drag = null;
});

document.addEventListener("dragover", (e) => {
  if (!drag) return;
  const box = e.target.closest && e.target.closest(".category");
  if (!box) return;
  e.preventDefault();

  if (drag.kind === "cat") {
    if (box.dataset.cat === drag.id) return;
    clearGhosts();
    const g = document.createElement("div");
    g.className = "ghost-cat";
    // move_category lands the box AFTER the target when dragged from an
    // earlier slot and BEFORE it otherwise — the preview must match that
    const ids = cats().map((c) => c.id);
    const after = ids.indexOf(drag.id) < ids.indexOf(box.dataset.cat);
    box.parentNode.insertBefore(g, after ? box.nextSibling : box);
    return;
  }
  // card / parameter: preview the slot inside this category
  const grid = box.querySelector(".cat-grid");
  const idx = insertIndex(grid, e.clientX, e.clientY);
  const existing = grid.querySelector(".ghost-card");
  const at = [...grid.children].indexOf(existing);
  if (existing && at === idx) return;      // already previewing this slot
  clearGhosts();
  const g = cardGhost();
  grid.insertBefore(g, grid.children[idx] || null);
});

document.addEventListener("drop", (e) => {
  if (!drag) return;
  const box = e.target.closest && e.target.closest(".category");
  if (!box) { clearGhosts(); return; }
  e.preventDefault();
  const catId = box.dataset.cat;

  if (drag.kind === "cat") {
    if (catId !== drag.id) send("move_category", { cat: drag.id, target: catId });
  } else {
    const grid = box.querySelector(".cat-grid");
    const ghost = grid.querySelector(".ghost-card");
    let index = ghost ? [...grid.children].filter((n) => !n.classList.contains("ghost-card"))
                          .findIndex((n) => n.compareDocumentPosition(ghost)
                            & Node.DOCUMENT_POSITION_PRECEDING) : -1;
    if (index < 0) index = insertIndex(grid, e.clientX, e.clientY);
    if (drag.kind === "card") send("move_control", { id: drag.id, cat: catId, index });
    else send("add_control", { param: drag.param, cat: catId, index });
  }
  clearGhosts();
  drag = null;
});

// -- popup menus ----------------------------------------------------------
function popup(items, x, y) {
  const m = $("menu");
  m.innerHTML = items.map((it) => {
    if (it.sep) return '<div class="sep"></div>';
    if (it.head) return `<div class="head">${esc(it.head)}</div>`;
    const sw = it.color ? `<span class="swatch" style="background:${it.color}"></span>` : "";
    return `<div class="item${it.disabled ? " disabled" : ""}" data-i="${it.i}">${sw}${esc(it.text)}</div>`;
  }).join("");
  m.classList.remove("hidden");
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, window.innerWidth - r.width - 8) + "px";
  m.style.top = Math.min(y, window.innerHeight - r.height - 8) + "px";
  m._items = items;
}
function closeMenu() { $("menu").classList.add("hidden"); }
$("menu").addEventListener("click", (e) => {
  const el = e.target.closest(".item");
  if (!el || el.classList.contains("disabled")) return;
  const it = ($("menu")._items || [])[Number(el.dataset.i)];
  closeMenu();
  if (it && it.run) it.run();
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#menu")) closeMenu();
}, true);

// -- modal (control editor) ----------------------------------------------
function editControl(c) {
  const ptype = (S.params[c.param] || {}).ptype || "Float";
  const allowed = KIND_FOR[ptype] || ["slider"];
  $("modalTitle").textContent = "Edit control";
  $("modalBody").innerHTML = `
    <div class="modal-row"><label>Parameter</label>
      <span style="flex:1;color:var(--muted);font-size:12.5px">${esc(c.param)} (${esc(ptype)})</span></div>
    <div class="modal-row"><label>Label</label>
      <input type="text" id="mLabel" value="${esc(c.label || c.param)}"></div>
    <div class="modal-row"><label>Control</label>
      <select id="mKind">${allowed.map((k) =>
        `<option value="${k}"${c.kind === k ? " selected" : ""}>${KINDS[k]}</option>`).join("")}</select></div>
    ${ptype === "Bool" ? `<div class="modal-row"><label></label>
      <label style="width:auto;color:var(--text)"><input type="checkbox" id="mInv"
        ${c.invert ? "checked" : ""}> Invert — ON sends the parameter OFF</label></div>` : ""}
    <div class="modal-row"><label>Min</label>
      <input type="number" id="mMin" step="any" value="${c.min == null ? 0 : c.min}"></div>
    <div class="modal-row"><label>Max</label>
      <input type="number" id="mMax" step="any" value="${c.max == null ? (ptype === "Float" ? 1 : 255) : c.max}"></div>
    <div class="modal-row" id="mOnRow"><label id="mOnLabel"></label>
      <input type="number" id="mOn" min="0" max="255" step="1"
        value="${c.on == null ? nextOnValue(c) : c.on}"
        title="What ${esc(c.param)} is set to. Several toggles on one number work like VRChat's menu: Outfit = 1, Outfit = 2, …"></div>
    <div class="modal-row" id="mOffRow"><label id="mOffLabel"></label>
      <input type="number" id="mOff" min="0" max="255" step="1" value="${c.off == null ? 0 : c.off}"
        title="What ${esc(c.param)} goes back to (usually 0)."></div>`;
  const modal = $("modal");
  modal.classList.remove("hidden");
  const ranged = () => ["slider", "int"].includes($("mKind").value);
  const intSwitch = () => ptype === "Int" && ["toggle", "button"].includes($("mKind").value);
  const sync = () => {
    $("mMin").disabled = !ranged(); $("mMax").disabled = !ranged();
    const sw = intSwitch();
    $("mOnRow").style.display = $("mOffRow").style.display = sw ? "" : "none";
    if (sw) [$("mOnLabel").textContent, $("mOffLabel").textContent] = SWITCH_LABELS[$("mKind").value];
  };
  $("mKind").onchange = sync; sync();
  $("modalOk").onclick = () => {
    const changes = { label: $("mLabel").value, kind: $("mKind").value };
    if (ranged()) { changes.min = Number($("mMin").value); changes.max = Number($("mMax").value); }
    if ($("mInv")) changes.invert = $("mInv").checked;
    if (intSwitch()) { changes.on = Number($("mOn").value); changes.off = Number($("mOff").value); }
    send("update_control", { id: c.id, changes });
    modal.classList.add("hidden");
  };
  $("modalCancel").onclick = () => modal.classList.add("hidden");
}

function promptModal(title, label, value, run) {
  $("modalTitle").textContent = title;
  $("modalBody").innerHTML =
    `<div class="modal-row"><label>${esc(label)}</label>
       <input type="text" id="mText" value="${esc(value)}"></div>`;
  const modal = $("modal");
  modal.classList.remove("hidden");
  $("mText").focus(); $("mText").select();
  $("modalOk").onclick = () => { run($("mText").value); modal.classList.add("hidden"); };
  $("modalCancel").onclick = () => modal.classList.add("hidden");
}

// -- clicks ---------------------------------------------------------------
document.addEventListener("click", (e) => {
  const t = e.target;
  const box = t.closest && t.closest(".category");
  const card = t.closest && t.closest(".card");

  if (t.classList.contains("cat-lock")) {
    const cat = cats().find((c) => c.id === box.dataset.cat);
    send("category_locked", { cat: box.dataset.cat, locked: !(cat && cat.locked) });
  } else if (t.classList.contains("cat-menu")) {
    const catId = box.dataset.cat;
    const cat = cats().find((c) => c.id === catId) || {};
    const others = (S.presets || []).filter((p) => p.id !== S.preset_id);
    const items = [{ head: "Colour" }];
    Object.entries(COLORS).forEach(([k, hex]) => items.push({
      text: k[0].toUpperCase() + k.slice(1) + (cat.color === k ? "  ✓" : ""),
      color: hex, run: () => send("category_color", { cat: catId, color: k }) }));
    items.push({ sep: true });
    if (others.length) {
      items.push({ head: "Copy to preset" });
      others.forEach((p) => items.push({
        text: p.name, run: () => send("copy_category", { cat: catId, preset: p.id }) }));
      items.push({ sep: true });
    }
    items.push({ text: "Delete category", run: () => {
      if (controlsIn(catId).length &&
          !confirm("This category still has controls — they'll move to the first category. Delete it?")) return;
      send("remove_category", { cat: catId });
    }});
    items.forEach((it, i) => it.i = i);
    popup(items, e.clientX, e.clientY);
  } else if (t.classList.contains("card-menu")) {
    const c = (S.board.controls || []).find((x) => x.id === card.dataset.id);
    const items = [
      { text: "Edit…", run: () => editControl(c) },
      { sep: true },
      { text: "Remove", run: () => send("remove_control", { id: c.id }) },
    ];
    items.forEach((it, i) => it.i = i);
    popup(items, e.clientX, e.clientY);
  } else if (t.dataset && t.dataset.set) {
    const on = !t.classList.contains("on");
    t.classList.toggle("on", on);
    const c = ((S && S.board.controls) || []).find((x) => x.id === t.dataset.set);
    // raw value — a snapshot landing mid-flight must agree
    if (c) values[c.param] = isIntSwitch(c) ? Number(on ? (c.on ?? 1) : (c.off ?? 0))
                                            : (c.invert ? !on : on);
    send("set_value", { id: t.dataset.set, value: on });
  } else if (t.dataset && t.dataset.step) {
    const numEl = t.parentNode.querySelector(".num");
    const min = num(numEl.dataset.min), max = num(numEl.dataset.max);
    let v = num(numEl.textContent) + Number(t.dataset.step);
    v = Math.max(min, Math.min(max, v));
    numEl.textContent = v;
    if (numEl.dataset.param) values[numEl.dataset.param] = v;
    send("set_value", { id: t.dataset.id, value: v });
  } else if (t.id === "addCategory") {
    send("add_category", {});
  } else if (t.id === "yoloBtn") {
    if (S.yolo || confirm(
      "Everyone with the link will be able to see and change EVERY avatar parameter — " +
      "not just your board, and category locks won't apply. Enable?"))
      send("set_yolo", { enabled: !S.yolo });
  } else if (t.id === "stopBtn") {
    send("set_sharing", { enabled: !S.sharing });
  } else if (t.id === "presetMenu") {
    const cur = (S.presets || []).find((p) => p.id === S.preset_id) || {};
    const items = [
      { text: "＋ New preset", run: () => promptModal("New preset", "Preset name:", "Preset",
          (v) => v && send("add_preset", { name: v })) },
      { text: "Rename preset…", run: () => promptModal("Rename preset", "Preset name:", cur.name || "",
          (v) => v && send("rename_preset", { preset: S.preset_id, name: v })) },
      { sep: true },
      { text: "Delete preset", run: () => {
          if (confirm(`Delete preset "${cur.name}" and everything on it?`))
            send("remove_preset", { preset: S.preset_id }); } },
    ];
    items.forEach((it, i) => it.i = i);
    popup(items, e.clientX, e.clientY);
  } else if (t.id === "avatarMenu") {
    const items = (S.avatars || []).map((a) => ({
      text: (a.avatar_id === S.avatar_id ? "● " : "   ") + a.name,
      run: () => send("open_avatar", { avatar: a.avatar_id }) }));
    if (!items.length) items.push({ text: "No saved avatars yet", disabled: true });
    items.forEach((it, i) => it.i = i);
    popup(items, e.clientX, e.clientY);
  } else if (t.id === "guestChip") {
    const items = (S.guest_names && S.guest_names.length)
      ? S.guest_names.map((n) => ({ text: n, disabled: true }))
      : [{ text: "No guests connected", disabled: true }];
    items.forEach((it, i) => it.i = i);
    popup(items, e.clientX, e.clientY);
  } else if (t.id === "resyncBtn") {
    send("resync", {});
  } else if (t.id === "dockToggle") {
    const dock = $("dock");
    dock.classList.toggle("hidden");
    t.textContent = dock.classList.contains("hidden") ? "<" : ">";
  } else if (t.id === "helpBtn") {
    alert("Admin panel — the same board editor as the desktop window.\n\n"
      + "• Drag the ⠿ on a card to move it inside or between categories\n"
      + "• Drag the ⠿ in a category header to reorder categories\n"
      + "• Drag a parameter from the right, or double-click it, to add a control\n"
      + "• ⋯ on a card: edit or remove · ⋯ on a category: colour, copy, delete\n"
      + "• 🔒 blocks guests from a category; STOP pauses everyone at once");
  }
});

// double-click a parameter to add it (like the desktop dock)
document.addEventListener("dblclick", (e) => {
  const row = e.target.closest && e.target.closest(".prow");
  if (!row) return;
  const first = cats()[0];
  if (first) send("add_control", { param: row.dataset.param, cat: first.id });
});

// sliders stream while dragging, like the desktop
let slideT = null;
document.addEventListener("input", (e) => {
  const el = e.target;
  if (el.dataset && el.dataset.slide) {
    const min = num(el.dataset.min), max = num(el.dataset.max);
    const v = min + (num(el.value) / 1000) * (max - min);
    const out = el.parentNode.querySelector(".value");
    if (out) {
      out.textContent = fmt(v);
      if (out.dataset.param) values[out.dataset.param] = v;
    }
    if (!slideT) slideT = setTimeout(() => {
      slideT = null; send("set_value", { id: el.dataset.slide, value: v });
    }, 60);
  } else if (el.id === "paramFilter") {
    filter = el.value.trim().toLowerCase(); renderParams();
  }
});
document.addEventListener("change", (e) => {
  const el = e.target;
  if (el.dataset && el.dataset.slide) {
    const min = num(el.dataset.min), max = num(el.dataset.max);
    send("set_value", { id: el.dataset.slide, value: min + (num(el.value) / 1000) * (max - min) });
  } else if (el.id === "presetPick") {
    send("switch_preset", { preset: el.value });
  }
});

// hold buttons
document.addEventListener("pointerdown", (e) => {
  if (e.target.dataset && e.target.dataset.hold) {
    e.target.classList.add("held"); send("set_value", { id: e.target.dataset.hold, value: true });
  }
});
document.addEventListener("pointerup", () => {
  document.querySelectorAll(".push.held").forEach((b) => {
    b.classList.remove("held"); send("set_value", { id: b.dataset.hold, value: false });
  });
});

// inline text edits commit on blur / Enter
document.addEventListener("focusin", (e) => {
  if (e.target.id === "avatarName") typing = "avatarName";
  if (e.target.classList.contains("cat-name")) typing = "cat";
});
// Only a real edit is sent: every op answers with a full snapshot, and a
// re-render landing between mousedown and mouseup swallowed the click that
// moved focus out of the field.
document.addEventListener("focusout", (e) => {
  const t = e.target;
  if (t.id === "avatarName") {
    typing = null;
    if (S && t.value !== (S.avatar_name || "")) send("rename_avatar", { name: t.value });
  } else if (t.classList.contains("cat-name")) {
    typing = null;
    const catId = t.closest(".category").dataset.cat;
    const cat = cats().find((c) => c.id === catId);
    if (!cat || t.value !== (cat.name || "")) send("rename_category", { cat: catId, name: t.value });
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.matches(".cat-name, .board-name")) e.target.blur();
  if (e.key === "Escape") { closeMenu(); $("modal").classList.add("hidden"); }
});

let resizeT = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeT);
  resizeT = setTimeout(() => { if (S) renderBoard(); }, 150);
});

if (!token) {
  $("overlayText").textContent = "Missing admin token — open the app (Share → Admin panel).";
  $("overlay").classList.remove("hidden");
} else {
  connect();
}
