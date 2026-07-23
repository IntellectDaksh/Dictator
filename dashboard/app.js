/* Dictator dashboard — vanilla view over the pywebview Python bridge.
   Structured as small render fns per section so this could be lifted into a
   React component later (each render* becomes a component, `api` becomes a
   hook) without reworking the Python side. */

let api = null;
let S = null;          // last full state
let query = "";        // active search filter
let capturing = false;

const $ = (s, r = document) => r.querySelector(s);
const HK_MODS_LABEL = (m) => m.map(x => x === "win" ? "Win" : x[0].toUpperCase() + x.slice(1)).join(" + ");

function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c.nodeType ? c : document.createTextNode(c));
  return n;
}

let toastT;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastT);
  toastT = setTimeout(() => t.classList.remove("show"), 1400);
}

// ---- theme -------------------------------------------------------------
function applyTheme(cfg) {
  document.documentElement.dataset.theme = cfg.theme === "light" ? "light" : "dark";
  const root = document.documentElement.style;
  if (cfg.accent_color) root.setProperty("--accent", cfg.accent_color);
  else root.removeProperty("--accent");
}

// ---- config write helper ----------------------------------------------
async function patch(p, { rerender = false } = {}) {
  Object.assign(S.config, p);
  await api.set_config(p);
  applyTheme(S.config);
  if (rerender) renderControls();
}

// ---- stats -------------------------------------------------------------
function renderStats(st) {
  for (const key of ["n", "raw_w", "cln_w", "wpm", "streak"]) {
    const node = $(`[data-stat="${key}"]`);
    if (!node) continue;
    node.textContent = Number(st[key] || 0).toLocaleString();
    node.className = "val";
    if (key === "streak") node.classList.add(st[key] >= 7 ? "accent" : st[key] >= 3 ? "warn" : "");
    if (key === "wpm") node.classList.add(st[key] >= 120 ? "ok" : "");
  }
  renderSpark(st.spark || []);
}
function renderSpark(vals) {
  const svg = $("#spark");
  if (!svg) return;
  if (!vals.length) { svg.innerHTML = ""; return; }
  const top = Math.max(...vals) || 1;
  const step = 100 / (vals.length - 1);
  const pts = vals.map((v, i) => [i * step, 20 - (v / top) * 16]);
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const last = pts[pts.length - 1];
  svg.innerHTML =
    `<path d="${d}" fill="none" stroke="var(--accent)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>` +
    `<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="1.6" fill="var(--accent)"/>`;
}

// ---- recent ------------------------------------------------------------
function renderRecent(items) {
  const box = $("#recent");
  box.innerHTML = "";
  if (!items.length) {
    box.append(el("div", { class: "empty" }, query ? "No matches." : "No dictations yet."));
    return;
  }
  box.classList.add("hud-stagger");
  for (const e of items) {
    const acts = el("div", { class: "acts" });
    const pin = el("button", {
      class: "icon-btn" + (e.pinned ? " pinned" : ""), title: e.pinned ? "Unpin" : "Pin",
      onclick: async () => { await api.toggle_pin(e.t); refreshLive(true); }
    }, e.pinned ? "★" : "☆");
    const copy = el("button", {
      class: "icon-btn", title: "Copy",
      onclick: async () => { await api.copy_text(e.cleaned); toast("copied"); }
    }, "⧉");
    acts.append(pin, copy);
    box.append(el("div", { class: "rec-row hud-row" },
      el("div", { class: "stamp" }, (e.pinned ? "★ " : "") + e.t_disp),
      el("div", { class: "body" }, e.cleaned),
      acts));
  }
}

// ---- health / storage --------------------------------------------------
function healthLines(h) {
  if (!h) return "Checking local services…";
  const dot = (ok) => `<span class="${ok ? "g" : "r"}">${ok ? "●" : "●"}</span>`;
  return [
    `${dot(h.enabled === "on")} <span class="k">Enabled</span> ${h.enabled}`,
    `${dot(h.loaded === "ready")} <span class="k">Whisper</span> ${h.whisper} (${h.loaded})`,
    `${dot(h.ollama !== "not reachable")} <span class="k">Ollama</span> ${h.ollama}`,
    `${dot(h.mic !== "Unavailable")} <span class="k">Mic</span> ${h.mic}`,
    `<span class="k">Review</span> ${h.review}`,
  ].join("<br>");
}
function storageLines(s) {
  if (!s || !s.length) return "Calculating sizes…";
  return s.map(x => `<span class="k">${x.name}</span> ${x.size} / ${x.files.toLocaleString()} files`).join("<br>");
}

// ---- controls ----------------------------------------------------------
function section(label, ...kids) {
  return el("div", { class: "sect" }, el("div", { class: "hud-label" }, label), ...kids);
}
function btn(text, onclick, { variant = "", active = false } = {}) {
  return el("button", { class: "hud-btn " + variant + (active ? " active" : ""), onclick }, text);
}

function renderControls() {
  const c = S.config;
  const box = $("#controls");
  box.innerHTML = "";

  // Appearance
  const accent = el("input", {
    type: "color", class: "swatch", value: c.accent_color || "#d71921",
    oninput: (e) => patch({ accent_color: e.target.value }),
  });
  const appearance = el("div", { class: "btn-row" },
    btn(c.theme === "dark" ? "Light theme" : "Dark theme",
        () => patch({ theme: c.theme === "dark" ? "light" : "dark" }, { rerender: true })),
    accent,
    c.accent_color ? btn("Reset accent", () => patch({ accent_color: null }, { rerender: true })) : null,
    btn("Auto 7pm–7am", () => patch({ auto_theme: !c.auto_theme }, { rerender: true }),
        { active: !!c.auto_theme }));
  box.append(section("Appearance", appearance));

  // Microphone
  const mic = el("select", { class: "hud-select",
    onchange: (e) => patch({ input_device: e.target.value === "" ? null : Number(e.target.value) }) });
  for (const m of S.mics) {
    const o = el("option", { value: m.index === null ? "" : m.index }, m.name);
    if (m.index === c.input_device || (m.index === null && c.input_device == null)) o.selected = true;
    mic.append(o);
  }
  box.append(section("Microphone", mic));

  // Hotkey
  const curMods = c.hotkey_mods || ["ctrl", "win"];
  const presetMatch = S.hotkey_presets.find(([, m]) => JSON.stringify(m) === JSON.stringify(curMods));
  const hk = el("select", { class: "hud-select",
    onchange: (e) => { const p = S.hotkey_presets[Number(e.target.value)]; if (p) patch({ hotkey_mods: p[1] }); } });
  S.hotkey_presets.forEach(([label, m], i) => {
    const o = el("option", { value: i }, label);
    if (presetMatch && presetMatch[0] === label) o.selected = true;
    hk.append(o);
  });
  if (!presetMatch) { const o = el("option", { value: -1 }, "Custom (" + HK_MODS_LABEL(curMods) + ")"); o.selected = true; o.disabled = true; hk.prepend(o); }
  const capBtn = btn("Capture…", async () => {
    if (capturing) return;
    capturing = true; capBtn.textContent = "Press keys…"; capBtn.classList.add("active");
    const mods = await api.capture_hotkey();
    capturing = false;
    if (mods && mods.length) { S.config.hotkey_mods = mods; }
    renderControls();
  });
  const mode = el("div", { class: "btn-row", style: "margin-top:8px" },
    btn("Hold to talk", () => patch({ hotkey_mode: "hold" }, { rerender: true }), { active: (c.hotkey_mode || "hold") === "hold" }),
    btn("Tap to toggle", () => patch({ hotkey_mode: "toggle" }, { rerender: true }), { active: c.hotkey_mode === "toggle" }));
  box.append(section("Hotkey", el("div", { class: "hk-row" }, hk, capBtn), mode));

  // Whisper model
  const models = el("div", { class: "model-grid" });
  for (const [size, title, note] of [["base.en", "Base", "fast"], ["small.en", "Small", "balanced"], ["medium.en", "Medium", "accurate"]]) {
    models.append(el("div", {
      class: "model-card" + (c.model_size === size ? " active" : ""),
      onclick: () => { patch({ model_size: size }); renderControls(); toast("model → " + title.toLowerCase()); },
    }, el("div", { class: "title" }, title), el("div", { class: "note" }, note)));
  }
  box.append(section("Whisper model", models));

  // Vocabulary
  const vocab = el("input", { class: "hud-text", type: "text", value: (c.vocabulary || []).join(", "),
    placeholder: "names, brand words…", onchange: (e) => patch({ vocabulary: splitCsv(e.target.value) }) });
  box.append(section("Custom vocabulary", vocab));

  // Tone overrides
  const tone = S.config.tone_overrides || {};
  const toneWrap = el("div", {});
  for (const [key, cap] of [["casual", "Casual"], ["formal", "Formal"], ["verbatim", "Verbatim"]]) {
    toneWrap.append(el("div", { class: "field-label" }, cap),
      el("input", { class: "hud-text", type: "text", value: (tone[key] || []).join(", "),
        placeholder: "exe names, comma-separated",
        onchange: (e) => { const t = { ...(S.config.tone_overrides || {}) }; t[key] = splitCsv(e.target.value.toLowerCase()); patch({ tone_overrides: t }); } }));
  }
  box.append(section("Per-app tone overrides", toneWrap));

  // Snippets
  const snip = Object.entries(c.snippets || {}).map(([k, v]) => `${k} => ${v}`).join("\n");
  const area = el("textarea", { class: "hud-area", rows: 4, placeholder: "omw => on my way",
    onchange: (e) => patch({ snippets: parseSnippets(e.target.value) }) }, snip);
  box.append(section("Snippets — trigger => expansion", area));

  // Typing safety
  box.append(section("Typing safety", el("div", { class: "btn-row" },
    btn("Review long", () => patch({ review_before_typing: !c.review_before_typing }, { rerender: true }), { active: !!c.review_before_typing }),
    btn("Auto-punctuate", () => patch({ auto_punctuate: c.auto_punctuate === false ? true : false }, { rerender: true }), { active: c.auto_punctuate !== false }),
    btn("Undo last", async () => { await api.undo_last(); toast("undone"); }),
    btn("Clean clipboard", async () => { toast("cleaning…"); await api.clean_clipboard(); toast("cleaned"); }))));

  // Health
  const healthBox = el("div", { class: "status-box" },
    el("div", { class: "bar" }), el("div", { class: "lines", id: "health-lines", html: healthLines(S.health) }));
  box.append(section("Health",
    el("div", { class: "btn-row", style: "margin-bottom:8px" }, btn("Retry", async () => { await api.retry_health(); refreshLive(true); toast("checking…"); })),
    healthBox));

  // Storage
  const storageBox = el("div", { class: "status-box" },
    el("div", { class: "bar dim" }), el("div", { class: "lines", id: "storage-lines", html: storageLines(S.storage) }));
  box.append(section("Storage", storageBox,
    el("div", { class: "btn-row", style: "margin-top:8px" },
      btn("Clear Whisper cache", async () => { if (await confirmDlg("Clear the local Whisper cache?")) { await api.clear_whisper_cache(); refreshLive(true); } }),
      btn("Clear Ollama models", async () => { if (await confirmDlg("Delete the local cleanup model (~4.7 GB)?")) { await api.clear_ollama_models(); refreshLive(true); } }, { variant: "hud-btn-danger" })),
    el("div", { class: "btn-row", style: "margin-top:8px" },
      btn("Backup settings…", async () => { const r = await api.backup_settings(); if (r) toast("saved"); }),
      btn("Restore settings…", async () => { const r = await api.restore_settings(); if (r) { S.config = await api.get_config(); applyTheme(S.config); renderControls(); toast("restored"); } }))));

  // History / logging
  box.append(section("History",
    el("div", { class: "btn-row" },
      btn("Copy last", async () => { await api.copy_last(); toast("copied"); }, { variant: "hud-btn-primary" }),
      btn(c.log_history ? "Logging on" : "Logging off", () => patch({ log_history: !c.log_history }, { rerender: true }), { active: !!c.log_history }),
      btn("Folder…", async () => { const r = await api.pick_folder(); if (r) { S.config.history_dir = r; refreshLive(true); renderControls(); } }),
      btn("Open folder", () => api.open_folder())),
    el("div", { class: "btn-row", style: "margin-top:8px" },
      btn("Export history…", async () => { const r = await api.export_history(); if (r) toast("exported " + r + " entries"); }),
      btn("Purge history…", async () => { if (await confirmDlg("Permanently delete ALL history and stats? No undo.")) { await api.purge_history(); refreshLive(true); } }, { variant: "hud-btn-danger" })),
    el("div", { class: "log-note", id: "log-note" }, logNote())));
}

function logNote() {
  if (!S) return "";
  const state = S.config.log_history ? "on" : "off — new dictations stay in memory only";
  return "History logging: " + state + "\n" + S.history_path;
}

// ---- helpers -----------------------------------------------------------
const splitCsv = (s) => s.split(",").map(x => x.trim()).filter(Boolean);
function parseSnippets(text) {
  const out = {};
  for (const line of text.split("\n")) {
    const i = line.indexOf("=>");
    if (i < 0) continue;
    const k = line.slice(0, i).trim().toLowerCase();
    if (k) out[k] = line.slice(i + 2).trim();
  }
  return out;
}
async function confirmDlg(msg) {
  return await api.confirm(msg);
}

// ---- live refresh ------------------------------------------------------
async function refreshLive(force = false) {
  const live = await api.get_live();
  S.stats = live.stats; S.health = live.health; S.storage = live.storage;
  renderStats(live.stats);
  const hl = $("#health-lines"); if (hl) hl.innerHTML = healthLines(live.health);
  const sl = $("#storage-lines"); if (sl) sl.innerHTML = storageLines(live.storage);
  const ln = $("#log-note"); if (ln) ln.textContent = logNote();
  if (!query) renderRecent(live.recent);
}

async function runSearch() {
  if (!query) { refreshLive(); return; }
  const items = await api.search(query);
  renderRecent(items);
}

// ---- boot --------------------------------------------------------------
async function boot() {
  api = window.pywebview.api;
  S = await api.get_state();
  applyTheme(S.config);
  renderStats(S.stats);
  renderRecent(S.recent);
  renderControls();

  let searchT;
  $("#search").addEventListener("input", (e) => {
    query = e.target.value.trim().toLowerCase();
    clearTimeout(searchT);
    searchT = setTimeout(runSearch, 180);
  });

  setInterval(() => { if (!document.hidden) refreshLive(); }, 2000);
}

window.addEventListener("pywebviewready", boot);
