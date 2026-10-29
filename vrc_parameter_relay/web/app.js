"use strict";

const token = new URLSearchParams(location.search).get("k") || "";
const boardEl = document.getElementById("board");
const dotEl = document.getElementById("statusDot");
const statusEl = document.getElementById("statusText");
const nameEl = document.getElementById("boardName");
const overlayEl = document.getElementById("overlay");
const overlayText = document.getElementById("overlayText");

let ws = null;
let board = { name: "", controls: [] };
let values = {};
let retryMs = 1000;
let paused = false;
let yoloOn = false;
let allParams = {};       // name -> {ptype, value}   (YOLO mode only)
let yoloFilter = "";
const cards = new Map();     // control id -> updater fn
const paramRows = new Map(); // param name -> updater fn (YOLO list)

// The name survives in localStorage AND in the page URL (?n=), so it comes
// back even when the browser blocks storage — and a bookmarked link keeps it.
const urlName = (new URLSearchParams(location.search).get("n") || "")
  .trim().slice(0, 24);
let guestName = "";
try { guestName = localStorage.getItem("vrcpr_name") || ""; } catch (e) {}
if (urlName) guestName = urlName;

function rememberName() {
  try { localStorage.setItem("vrcpr_name", guestName); } catch (e) {}
  try {
    const u = new URL(location.href);
    if (guestName) u.searchParams.set("n", guestName);
    else u.searchParams.delete("n");
    history.replaceState(null, "", u);
  } catch (e) {}
}
if (guestName) rememberName();

function bouncedOnce() {
  try {
    if (sessionStorage.getItem("vrcpr_bounced")) return true;
    sessionStorage.setItem("vrcpr_bounced", "1");
    return false;
  } catch (e) { return true; }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  // The token rides in the URL only on a one-click link (the server plants
  // a cookie and bounces the browser to the bare address on arrival). A
  // signed-in browser sends its cookie by itself, so nothing is appended.
  ws = new WebSocket(`${proto}://${location.host}/ws?n=${encodeURIComponent(guestName)}`
    + (token ? `&k=${encodeURIComponent(token)}` : ""));

  ws.onopen = () => { retryMs = 1000; setStatus(true, "connected"); };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.t) {
      case "style":
        applyStyle(msg);
        break;
      case "hello":
      case "resumed":
      case "avatar":
      case "board":
        board = msg.board;
        values = msg.values || {};
        if (msg.style) applyStyle(msg.style);
        if (msg.t === "hello" || msg.t === "resumed") {
          yoloOn = !!msg.yolo;
          setPaused(!!msg.paused);
          try { sessionStorage.removeItem("vrcpr_bounced"); } catch (e) {}
        }
        if (msg.t === "resumed") setPaused(false);
        if (msg.need_name) showNameGate();
        if (msg.params) allParams = msg.params;
        render();
        break;
      case "need_name":
        if (msg.enabled === false) hideNameGate();
        else showNameGate();
        break;
      case "yolo":
        yoloOn = !!msg.enabled;
        allParams = msg.params || {};
        render();
        break;
      case "params":
        allParams = msg.params || {};
        render();
        break;
      case "param":
        applyParam(msg.name, msg.value, msg.ptype);
        break;
      case "param_batch":  // 50ms server-side batches
        for (const [name, [value, ptype]] of Object.entries(msg.params)) {
          applyParam(name, value, ptype);
        }
        break;
      case "paused":
        setPaused(true);
        break;
      case "denied":
        ws.onclose = null;
        ws.close();
        // A stale cookie (the host rotated the token) or a dead one-click
        // link: send the browser to the bare address, where the password
        // door lives. Once only — a full board also refuses, and that must
        // not bounce forever.
        if (!bouncedOnce()) { location.replace("/"); return; }
        showOverlay("This remote link is invalid or has been revoked by the host — "
          + "or the board is full. Ask the host for the current password.");
        return;
    }
  };

  ws.onclose = () => {
    setStatus(false, "reconnecting…");
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 1.6, 15000);
  };
}

function applyParam(name, value, ptype) {
  values[name] = value;
  for (const c of board.controls) {
    if (c.param === name && cards.has(c.id)) cards.get(c.id)(value);
  }
  if (allParams[name]) {
    allParams[name].value = value;
  } else if (yoloOn && ptype) {
    allParams[name] = { ptype: ptype, value: value };
  }
  if (paramRows.has(name)) paramRows.get(name)(value);
}

// mirror the host's theme + font (CSS handles the rest via variables)
function applyStyle(style) {
  if (!style) return;
  document.body.dataset.theme = style.theme || "neon";
  if (style.font) {
    document.documentElement.style.setProperty("--ui-font", '"' + style.font + '"');
  }
}

function setPaused(on) {
  paused = on;
  document.body.classList.toggle("paused", on);
  setStatus(!on, on ? "paused by host" : "connected");
}

function setStatus(ok, text) {
  dotEl.classList.toggle("ok", ok);
  statusEl.textContent = text;
}

function showOverlay(text) {
  overlayText.textContent = text;
  overlayEl.classList.remove("hidden");
}

function hideOverlay() {
  overlayEl.classList.add("hidden");
}

function send(id, value) {
  if (!paused && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ t: "set", id, value }));
  }
}

function render() {
  nameEl.textContent = board.name || "Avatar Remote";
  document.title = (board.name || "Avatar") + " — Remote";
  boardEl.innerHTML = "";
  cards.clear();

  if (!board.controls.length) {
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = "The host hasn't added any controls for this avatar yet.";
    boardEl.appendChild(div);
    return;
  }

  const categories = (board.categories && board.categories.length)
    ? board.categories
    : [{ id: null, name: "", locked: false }];
  const firstId = categories[0].id;
  const known = new Set(categories.map((c) => c.id));

  // two independent stacks, like the desktop app — no grid-row coupling
  const colCount = window.innerWidth <= 700 ? 1 : 2;
  lastColCount = colCount;
  const colWrap = document.createElement("div");
  colWrap.className = "cols";
  const cols = [];
  for (let i = 0; i < colCount; i++) {
    const col = document.createElement("div");
    col.className = "col";
    cols.push(col);
    colWrap.appendChild(col);
  }

  for (const [index, cat] of categories.entries()) {
    const controls = board.controls.filter(
      (c) => c.cat === cat.id || (cat.id === firstId && !known.has(c.cat)));
    if (!controls.length) continue; // hide empty categories from guests

    const section = document.createElement("section");
    section.className = "category" + (cat.locked ? " locked" : "");
    section.dataset.color = cat.color || "green";  // tints border/header/cards

    const header = document.createElement("div");
    header.className = "cat-header";
    const title = document.createElement("span");
    title.textContent = cat.name || "Controls";
    header.appendChild(title);
    if (cat.locked) {
      const lock = document.createElement("span");
      lock.className = "lock";
      lock.textContent = "🔒";
      lock.title = "Locked by the host";
      header.appendChild(lock);
    }
    section.appendChild(header);

    const grid = document.createElement("div");
    grid.className = "cat-grid";
    for (const control of controls) {
      grid.appendChild(buildCard(control, cat.locked));
    }
    section.appendChild(grid);
    // column by absolute index — the parity must match the desktop window
    // and admin panel, which round-robin ALL categories, empty ones included
    cols[index % colCount].appendChild(section);
  }
  boardEl.appendChild(colWrap);

  if (yoloOn) boardEl.appendChild(buildYoloSection());
}

let lastColCount = 0;
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    const c = window.innerWidth <= 700 ? 1 : 2;
    if (c !== lastColCount && board.controls.length) render();
  }, 150);
});

function sendP(name, value) {
  if (!paused && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ t: "setp", name, value }));
  }
}

function buildYoloSection() {
  paramRows.clear();
  const section = document.createElement("section");
  section.className = "category yolo";

  const header = document.createElement("div");
  header.className = "cat-header yolo-header";
  header.innerHTML = "<span>⚡ YOLO — every parameter</span>";
  section.appendChild(header);

  const filter = document.createElement("input");
  filter.type = "search";
  filter.className = "yolo-filter";
  filter.placeholder = "Filter parameters…";
  filter.value = yoloFilter;
  section.appendChild(filter);

  const list = document.createElement("div");
  list.className = "yolo-list";
  const names = Object.keys(allParams).sort((a, b) => a.localeCompare(b));
  for (const name of names) {
    list.appendChild(buildParamRow(name, allParams[name]));
  }
  section.appendChild(list);

  const applyFilter = () => {
    yoloFilter = filter.value.trim().toLowerCase();
    for (const row of list.children) {
      row.style.display =
        !yoloFilter || row.dataset.name.includes(yoloFilter) ? "" : "none";
    }
  };
  filter.oninput = applyFilter;
  applyFilter();
  return section;
}

function buildParamRow(name, info) {
  const row = document.createElement("div");
  row.className = "prow";
  row.dataset.name = name.toLowerCase();

  const label = document.createElement("div");
  label.className = "pname";
  label.textContent = name;
  label.title = `${name} (${info.ptype})`;
  row.appendChild(label);

  if (info.ptype === "Bool") {
    const btn = document.createElement("button");
    btn.className = "switch small" + (info.value ? " on" : "");
    btn.onclick = () => {
      const on = !btn.classList.contains("on");
      btn.classList.toggle("on", on);
      sendP(name, on);
    };
    row.appendChild(btn);
    paramRows.set(name, (v) => btn.classList.toggle("on", !!v));

  } else if (info.ptype === "Int") {
    const num = document.createElement("input");
    num.type = "number";
    num.min = 0; num.max = 255; num.step = 1;
    num.className = "pint";
    num.value = Number.isFinite(Number(info.value)) ? info.value : 0;
    num.onchange = () => {
      const v = Math.max(0, Math.min(255, Number(num.value) || 0));
      num.value = v;
      sendP(name, v);
    };
    row.appendChild(num);
    paramRows.set(name, (v) => {
      if (document.activeElement !== num) num.value = v;
    });

  } else { // Float
    const range = document.createElement("input");
    range.type = "range";
    range.min = -1; range.max = 1; range.step = 0.01;
    range.className = "pfloat";
    range.value = Number.isFinite(Number(info.value)) ? info.value : 0;
    const show = document.createElement("span");
    show.className = "pval";
    show.textContent = fmt(info.value);
    let throttle = null;
    range.oninput = () => {
      show.textContent = fmt(range.value);
      if (!throttle) {
        throttle = setTimeout(() => { throttle = null; sendP(name, Number(range.value)); }, 60);
      }
    };
    range.onchange = () => sendP(name, Number(range.value));
    row.appendChild(range);
    row.appendChild(show);
    paramRows.set(name, (v) => {
      if (document.activeElement !== range) {
        range.value = v;
        show.textContent = fmt(v);
      }
    });
  }
  return row;
}

function buildCard(c, locked) {
  const card = document.createElement("div");
  card.className = "card";
  const label = document.createElement("div");
  label.className = "label";
  label.textContent = c.label || c.param;
  label.title = c.param;
  card.appendChild(label);

  const val = values[c.param];

  if (c.kind === "toggle") {
    const btn = document.createElement("button");
    btn.className = "switch" + (switchOn(c, val) ? " on" : "");
    btn.setAttribute("aria-label", c.label);
    btn.onclick = () => {
      const on = !btn.classList.contains("on");
      btn.classList.toggle("on", on);
      send(c.id, on); // server maps it (inverted Bool, Int ON/OFF value)
    };
    card.appendChild(btn);
    cards.set(c.id, (v) => btn.classList.toggle("on", switchOn(c, v)));

  } else if (c.kind === "button") {
    const btn = document.createElement("button");
    btn.className = "push";
    btn.textContent = "Hold";
    const down = (e) => { e.preventDefault(); btn.classList.add("held"); send(c.id, true); };
    const up = () => { btn.classList.remove("held"); send(c.id, false); };
    btn.addEventListener("pointerdown", down);
    btn.addEventListener("pointerup", up);
    btn.addEventListener("pointercancel", up);
    btn.addEventListener("pointerleave", up);
    card.appendChild(btn);
    cards.set(c.id, () => {});

  } else if (c.kind === "slider") {
    const min = Number(c.min ?? 0), max = Number(c.max ?? 1);
    const range = document.createElement("input");
    range.type = "range";
    range.min = 0; range.max = 1000;
    range.value = valToStep(val, min, max);
    const show = document.createElement("div");
    show.className = "value";
    show.textContent = fmt(val);
    let throttle = null;
    range.oninput = () => {
      const v = min + (range.value / 1000) * (max - min);
      show.textContent = fmt(v);
      if (!throttle) {
        throttle = setTimeout(() => { throttle = null; send(c.id, v); }, 60);
      }
    };
    range.onchange = () => {
      const v = min + (range.value / 1000) * (max - min);
      send(c.id, v);
    };
    const row = document.createElement("div");
    row.className = "slide-row";
    row.appendChild(range);
    row.appendChild(show);
    card.appendChild(row);
    cards.set(c.id, (v) => {
      if (document.activeElement !== range) {
        range.value = valToStep(v, min, max);
        show.textContent = fmt(v);
      }
    });

  } else if (c.kind === "int") {
    const min = Number(c.min ?? 0), max = Number(c.max ?? 255);
    const wrap = document.createElement("div");
    wrap.className = "stepper";
    const minus = document.createElement("button");
    minus.textContent = "−";
    const num = document.createElement("div");
    num.className = "num";
    num.textContent = val ?? "–";
    const plus = document.createElement("button");
    plus.textContent = "+";
    const step = (d) => {
      let v = Number(num.textContent);
      if (!Number.isFinite(v)) v = min;
      v = Math.max(min, Math.min(max, v + d));
      num.textContent = v;
      send(c.id, v);
    };
    minus.onclick = () => step(-1);
    plus.onclick = () => step(1);
    wrap.append(minus, num, plus);
    card.appendChild(wrap);
    cards.set(c.id, (v) => { num.textContent = v; });
  }

  if (locked) {
    card.querySelectorAll("button, input").forEach((el) => { el.disabled = true; });
  }
  return card;
}

// Is a toggle shown ON? Bool: value XOR invert. Int (VRChat-menu style):
// ON while the parameter equals the toggle's value, so several toggles on
// one Int (Outfit = 1, 2, 3) light up one at a time.
function switchOn(c, v) {
  if (c.ptype === "Int") return v != null && Number(v) === Number(c.on ?? 1);
  return c.invert ? !v : !!v;
}

function valToStep(v, min, max) {
  const x = Number(v);
  if (!Number.isFinite(x) || max === min) return 0;
  return Math.round(((x - min) / (max - min)) * 1000);
}

function fmt(v) {
  const x = Number(v);
  return Number.isFinite(x) ? x.toFixed(2) : "–";
}

// -- name gate (host requires guests to introduce themselves) -------------
const gateEl = document.getElementById("nameGate");
const gateInput = document.getElementById("gateInput");

function showNameGate() {
  if (guestName) return; // already introduced — nothing to ask
  gateEl.classList.remove("hidden");
  gateInput.focus();
}

function hideNameGate() {
  gateEl.classList.add("hidden");
}

function submitGate() {
  const entered = gateInput.value.trim().slice(0, 24);
  if (!entered) { gateInput.focus(); return; }
  guestName = entered;
  rememberName();
  document.getElementById("nameBtn").textContent = `✎ ${guestName}`;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ t: "name", name: guestName }));
  }
  hideNameGate();
}

document.getElementById("gateBtn").onclick = submitGate;
gateInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") submitGate();
});

function askName() {
  const entered = prompt("Your name (shown to the host):", guestName);
  if (entered === null) return;
  guestName = entered.trim().slice(0, 24);
  rememberName();
  document.getElementById("nameBtn").textContent = guestName ? `✎ ${guestName}` : "✎ set name";
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ t: "name", name: guestName }));
  }
}

document.getElementById("nameBtn").onclick = askName;
document.getElementById("nameBtn").textContent = guestName ? `✎ ${guestName}` : "✎ set name";

// No token in the address is the NORMAL case now: the server already let
// this page through (password door -> cookie), and the cookie rides the
// websocket by itself.
connect();
