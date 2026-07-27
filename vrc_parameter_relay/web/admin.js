"use strict";

// Admin panel: a browser front-end over the same core the desktop UI drives.
// Every edit is one "op" message; the server answers with a fresh snapshot,
// so the panel never guesses at state.

const token = new URLSearchParams(location.search).get("k") || "";
const $ = (id) => document.getElementById(id);
const dotEl = $("statusDot"), statusEl = $("statusText");

let ws = null;
let retryMs = 1000;
let S = null;              // last admin_state snapshot
let values = {};           // live parameter values
let filter = "";
let editingField = null;   // don't redraw the field being typed in

const COLORS = {
  green: "#4af58c", blue: "#7aa7ff", cyan: "#56d9f2",
  yellow: "#ffb454", red: "#ff5561", purple: "#d78cff",
};

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function fmt(v) {
  if (v === true) return "on"; if (v === false) return "off";
  const n = Number(v); return Number.isFinite(n) ? (Number.isInteger(n) ? n : n.toFixed(2)) : "–";
}
function toast(msg) {
  const el = $("toast"); el.textContent = msg; el.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.remove("show"), 3200);
}
function send(op, extra) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ op, ...extra }));
}

// -- connection -----------------------------------------------------------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?admin=1&k=${encodeURIComponent(token)}`);
  ws.onopen = () => { retryMs = 1000; setStatus(true, "connected"); };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.t === "admin_state") {
      // Every snapshot is built when it is sent, and each change triggers one,
      // so the newest message always wins — just apply them in order. (Never
      // gate this on "waiting for my own reply": one lost reply would freeze
      // the panel for good.)
      S = msg; applyStyle(msg.style); render();
    }
    else if (msg.t === "param") { values[msg.name] = msg.value; paintValues(); }
    else if (msg.t === "error") toast(msg.msg);
    else if (msg.t === "denied") {
      $("overlayText").textContent =
        "This admin link is invalid or has been reset. Open the app and copy the current one.";
      $("overlay").classList.remove("hidden");
      ws.onclose = null; ws.close();
    }
  };
  ws.onclose = () => {
    setStatus(false, "reconnecting…");
    setTimeout(connect, retryMs); retryMs = Math.min(retryMs * 1.6, 15000);
  };
}
function setStatus(ok, text) { dotEl.classList.toggle("ok", ok); statusEl.textContent = text; }
function applyStyle(style) {
  if (!style) return;
  document.body.dataset.theme = style.theme || "neon";
  if (style.font) document.documentElement.style.setProperty("--ui-font", '"' + style.font + '"');
}

// -- render ---------------------------------------------------------------
function render() {
  if (!S) return;
  values = { ...S.values };

  if (editingField !== "avatarName") $("avatarName").value = S.avatar_name || "";
  const badge = $("liveBadge");
  badge.textContent = S.avatar_id ? (S.is_live ? "● LIVE" : "✎ OFFLINE") : "no avatar";
  badge.className = "badge " + (S.avatar_id ? (S.is_live ? "live" : "offline") : "");

  fillSelect($("avatarPick"), (S.avatars || []).map((a) => ({
    v: a.avatar_id, t: (a.avatar_id === S.avatar_id ? "● " : "") + a.name })), S.avatar_id);
  fillSelect($("presetPick"), (S.presets || []).map((p) => ({ v: p.id, t: p.name })), S.preset_id);

  $("guestChip").textContent = `guests: ${S.guests}`;
  $("yoloBtn").classList.toggle("on", !!S.yolo);
  const share = $("shareBtn");
  share.textContent = S.sharing ? "sharing: on" : "sharing: paused";
  share.classList.toggle("on", !!S.sharing);

  renderCats();
  renderParams();
}

function fillSelect(sel, items, current) {
  if (document.activeElement === sel) return;
  sel.innerHTML = items.map((i) =>
    `<option value="${esc(i.v)}"${i.v === current ? " selected" : ""}>${esc(i.t)}</option>`).join("");
  sel.disabled = items.length === 0;
}

function renderCats() {
  const host = $("cats");
  const cats = S.board.categories || [], controls = S.board.controls || [];
  const catOpts = cats.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  host.innerHTML = cats.map((cat) => {
    const mine = controls.filter((c) => c.cat === cat.id);
    const sw = Object.keys(COLORS).map((k) =>
      `<span class="sw${(cat.color || "green") === k ? " sel" : ""}" data-cat="${esc(cat.id)}"
             data-color="${k}" style="background:${COLORS[k]}" title="${k}"></span>`).join("");
    return `<div class="acat" data-color="${esc(cat.color || "green")}">
      <div class="acat-head">
        <input class="cat-name" data-cat="${esc(cat.id)}" value="${esc(cat.name)}">
        <span class="swatches">${sw}</span>
        <button class="mini cat-lock" data-cat="${esc(cat.id)}"
                title="${cat.locked ? "Locked for guests" : "Guests may use it"}">${cat.locked ? "🔒" : "🔓"}</button>
        <button class="mini cat-up" data-cat="${esc(cat.id)}" title="Move up">↑</button>
        <button class="mini cat-del danger" data-cat="${esc(cat.id)}" title="Delete category">🗑</button>
      </div>
      ${mine.length ? `<div class="ctrl-grid">${mine.map((c) => ctrlCard(c, catOpts)).join("")}</div>`
                    : `<div class="empty-cat">No controls yet — click a parameter on the right.</div>`}
    </div>`;
  }).join("");
}

function ctrlCard(c, catOpts) {
  const kinds = { toggle: "Toggle", button: "Hold", slider: "Slider", int: "Number" };
  const kindOpts = Object.entries(kinds).map(([k, t]) =>
    `<option value="${k}"${c.kind === k ? " selected" : ""}>${t}</option>`).join("");
  const ranged = c.kind === "slider" || c.kind === "int";
  return `<div class="actrl" data-id="${esc(c.id)}">
    <div class="actrl-top">
      <input class="lbl" data-id="${esc(c.id)}" value="${esc(c.label || c.param)}">
      <button class="mini ctrl-del danger" data-id="${esc(c.id)}" title="Remove">✕</button>
    </div>
    <div class="param" title="${esc(c.param)}">${esc(c.param)}</div>
    <div class="actrl-row">
      <select class="ctrl-kind" data-id="${esc(c.id)}">${kindOpts}</select>
      <select class="ctrl-cat" data-id="${esc(c.id)}">${catOpts}</select>
    </div>
    ${ranged ? `<div class="actrl-row" style="margin-top:5px">
        <input type="number" class="ctrl-min" data-id="${esc(c.id)}" value="${c.min ?? 0}" step="any" title="min">
        <input type="number" class="ctrl-max" data-id="${esc(c.id)}" value="${c.max ?? 1}" step="any" title="max">
      </div>` : ""}
    ${c.kind === "toggle" ? `<div class="actrl-row" style="margin-top:5px">
        <label class="hint"><input type="checkbox" class="ctrl-inv" data-id="${esc(c.id)}"
          ${c.invert ? "checked" : ""}> invert</label></div>` : ""}
    <div class="live hint" data-value="${esc(c.param)}">= ${fmt(values[c.param])}</div>
  </div>`;
}

function renderParams() {
  const used = new Set((S.board.controls || []).map((c) => c.param));
  const names = Object.keys(S.params || {}).sort((a, b) => a.localeCompare(b));
  const shown = names.filter((n) => !filter || n.toLowerCase().includes(filter));
  $("paramCount").textContent = `${shown.length}/${names.length}`;
  const cats = S.board.categories || [];
  const opts = cats.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  $("params").innerHTML = shown.map((n) => {
    const p = S.params[n];
    return `<div class="prow2${used.has(n) ? " used" : ""}">
      <span class="pn" data-add="${esc(n)}" title="Add ${esc(n)}">${esc(n)}</span>
      <span class="pt">${esc(p.ptype)}</span>
      <span class="pv" data-value="${esc(n)}">${fmt(values[n])}</span>
      <select class="add-to" data-param="${esc(n)}" title="Add to…"><option value="">▾</option>${opts}</select>
    </div>`;
  }).join("");
}

function paintValues() {
  document.querySelectorAll("[data-value]").forEach((el) => {
    const name = el.getAttribute("data-value");
    el.textContent = el.classList.contains("live") ? `= ${fmt(values[name])}` : fmt(values[name]);
  });
}

// -- events (delegated, so re-renders never lose handlers) ----------------
document.addEventListener("click", (e) => {
  const t = e.target;
  if (t.dataset.add !== undefined && t.classList.contains("pn")) {
    const cat = (S.board.categories[0] || {}).id;
    if (cat) send("add_control", { param: t.dataset.add, cat });
  } else if (t.classList.contains("sw")) {
    send("category_color", { cat: t.dataset.cat, color: t.dataset.color });
  } else if (t.classList.contains("cat-lock")) {
    const cat = S.board.categories.find((c) => c.id === t.dataset.cat);
    send("category_locked", { cat: t.dataset.cat, locked: !(cat && cat.locked) });
  } else if (t.classList.contains("cat-up")) {
    const cats = S.board.categories, i = cats.findIndex((c) => c.id === t.dataset.cat);
    if (i > 0) send("move_category", { cat: t.dataset.cat, target: cats[i - 1].id });
  } else if (t.classList.contains("cat-del")) {
    if (confirm("Delete this category? Its controls move to the first one."))
      send("remove_category", { cat: t.dataset.cat });
  } else if (t.classList.contains("ctrl-del")) {
    send("remove_control", { id: t.dataset.id });
  } else if (t.id === "addCategory") {
    send("add_category", {});
  } else if (t.id === "yoloBtn") {
    if (S.yolo || confirm("YOLO gives guests every parameter, ignoring locks. Enable?"))
      send("set_yolo", { enabled: !S.yolo });
  } else if (t.id === "shareBtn") {
    send("set_sharing", { enabled: !S.sharing });
  } else if (t.id === "presetAdd") {
    const name = prompt("New preset name:", "Preset"); if (name) send("add_preset", { name });
  } else if (t.id === "presetRename") {
    const cur = (S.presets.find((p) => p.id === S.preset_id) || {}).name || "";
    const name = prompt("Rename preset:", cur);
    if (name) send("rename_preset", { preset: S.preset_id, name });
  } else if (t.id === "presetDel") {
    if (confirm("Delete this preset and everything on it?"))
      send("remove_preset", { preset: S.preset_id });
  }
});

document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.id === "avatarPick") send("open_avatar", { avatar: t.value });
  else if (t.id === "presetPick") send("switch_preset", { preset: t.value });
  else if (t.classList.contains("add-to") && t.value) {
    send("add_control", { param: t.dataset.param, cat: t.value }); t.value = "";
  } else if (t.classList.contains("ctrl-kind")) {
    send("update_control", { id: t.dataset.id, changes: { kind: t.value } });
  } else if (t.classList.contains("ctrl-cat")) {
    send("move_control", { id: t.dataset.id, cat: t.value, index: 0 });
  } else if (t.classList.contains("ctrl-inv")) {
    send("update_control", { id: t.dataset.id, changes: { invert: t.checked } });
  } else if (t.classList.contains("ctrl-min") || t.classList.contains("ctrl-max")) {
    const key = t.classList.contains("ctrl-min") ? "min" : "max";
    send("update_control", { id: t.dataset.id, changes: { [key]: Number(t.value) } });
  }
});

// text fields commit on blur / Enter, and pause redraws while typing
document.addEventListener("focusin", (e) => {
  if (e.target.id === "avatarName") editingField = "avatarName";
});
document.addEventListener("focusout", (e) => {
  const t = e.target;
  if (t.id === "avatarName") { editingField = null; send("rename_avatar", { name: t.value }); }
  else if (t.classList.contains("cat-name")) send("rename_category", { cat: t.dataset.cat, name: t.value });
  else if (t.classList.contains("lbl")) send("update_control", { id: t.dataset.id, changes: { label: t.value } });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.matches("input[type=text], .lbl, .cat-name, .avatar-name")) e.target.blur();
});
$("paramFilter").addEventListener("input", (e) => {
  filter = e.target.value.trim().toLowerCase(); renderParams();
});

if (!token) {
  $("overlayText").textContent = "Missing admin token — open the app and copy the admin link.";
  $("overlay").classList.remove("hidden");
} else {
  connect();
}
