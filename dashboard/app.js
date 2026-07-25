/* Dictator dashboard — vanilla view over the pywebview Python bridge.
   Sidebar + paged layout. Structured as small render fns per page/section so
   this could be lifted into a React component later (each render* becomes a
   component, `api` becomes a hook) without reworking the Python side. */

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
  if (cfg.highlight_color) root.setProperty("--highlight", cfg.highlight_color);
  else root.removeProperty("--highlight");
}

// ---- config write helper ----------------------------------------------
async function patch(p, { rerender } = {}) {
  Object.assign(S.config, p);
  await api.set_config(p);
  applyTheme(S.config);
  if (rerender) rerender();
}

// ---- small components ---------------------------------------------------
function toggleSwitch(on, onclick) {
  return el("button", { class: "switch" + (on ? " on" : ""), type: "button", onclick }, el("span", { class: "knob" }));
}
function fieldRow(label, control, { desc = null, stacked = false } = {}) {
  return el("div", { class: "field-row" + (stacked ? " stacked" : "") },
    el("div", { class: "field-info" }, el("div", { class: "label" }, label), desc ? el("div", { class: "desc" }, desc) : null),
    el("div", { class: "field-control" }, control));
}
function group(label, ...body) {
  return el("div", { class: "group" }, el("div", { class: "group-label" }, label), el("div", { class: "group-body" }, ...body));
}
function category(label) {
  return el("div", { class: "category-hd" }, label);
}
function btn(text, onclick, { variant = "", active = false } = {}) {
  return el("button", { class: "hud-btn " + variant + (active ? " active" : "") , type: "button", onclick }, text);
}

// tag/chip list: type + Enter to add, click × to remove, collapses past `limit`
const _tagExpanded = {};
function tagInput(key, items, onChange, placeholder, rerender, { limit = 8 } = {}) {
  const input = el("input", { class: "hud-text", type: "text", placeholder,
    onkeydown: (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const val = e.target.value.trim();
      if (!val || items.includes(val)) { e.target.value = ""; return; }
      onChange([...items, val]);
    } });
  const expanded = _tagExpanded[key];
  const shown = expanded ? items : items.slice(0, limit);
  const chips = shown.map((w) => el("span", { class: "chip" }, w,
    el("button", { class: "chip-x", type: "button", title: "Remove",
      onclick: () => onChange(items.filter((x) => x !== w)) }, "×")));
  if (items.length > limit) {
    chips.push(el("button", { class: "chip chip-more", type: "button",
      onclick: () => { _tagExpanded[key] = !expanded; rerender(); } },
      expanded ? "Show less" : `+${items.length - limit} more`));
  }
  return el("div", {}, input, items.length ? el("div", { class: "chip-row" }, ...chips) : null);
}

// ---- stats -------------------------------------------------------------
function renderStats(st) {
  for (const key of ["n", "raw_w", "cln_w", "wpm", "streak"]) {
    const node = $(`[data-stat="${key}"]`);
    if (!node) continue;
    node.textContent = Number(st[key] || 0).toLocaleString();
    node.className = "val";
    if (key === "streak" && st[key] >= 3) node.classList.add(st[key] >= 7 ? "accent" : "warn");
    if (key === "wpm" && st[key] >= 120) node.classList.add("ok");
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
      class: "icon-btn" + (e.pinned ? " pinned" : ""), title: e.pinned ? "Unpin" : "Pin", type: "button",
      onclick: async () => { await api.toggle_pin(e.t); refreshLive(true); }
    }, e.pinned ? "★" : "☆");
    const copy = el("button", {
      class: "icon-btn", title: "Copy", type: "button",
      onclick: async () => { await api.copy_text(e.cleaned); toast("copied"); }
    }, "⧉");
    acts.append(pin, copy);
    box.append(el("div", { class: "rec-row" },
      el("div", { class: "stamp" }, (e.pinned ? "★ " : "") + e.t_disp),
      el("div", { class: "body" }, e.cleaned),
      acts));
  }
}

// ---- health / storage lines --------------------------------------------
function healthLines(h) {
  if (!h) return "Checking local services…";
  const dot = (ok) => `<span class="${ok ? "g" : "r"}">●</span>`;
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

// ---- status bar (persistent chrome) -------------------------------------
function renderStatusBar(h) {
  const bar = $("#statusbar");
  bar.innerHTML = "";
  const item = (ok, label, val) => el("div", { class: "sb-item" },
    el("span", { class: "dot " + (ok ? "ok" : "") }), el("span", {}, label + " "), el("span", { class: "k" }, val));
  const left = el("div", { class: "sb-left" });
  if (h) {
    left.append(
      item(h.enabled === "on", "Enabled", h.enabled),
      item(h.loaded === "ready", "Whisper", h.whisper),
      item(h.ollama !== "not reachable", "Ollama", h.ollama),
      item(h.mic !== "Unavailable", "Mic", h.mic));
  } else {
    left.append(el("div", { class: "sb-item" }, "Checking local services…"));
  }
  bar.append(left, el("div", { class: "hud-badge" }, el("span", { class: "dot accent" }), "LOCAL"));
}

// ---- Home page --------------------------------------------------------
function renderHome() {
  const box = $("#home-body");
  box.innerHTML = "";
  box.append(group("Quick actions",
    el("div", { class: "btn-row" },
      btn("Undo last", async () => { await api.undo_last(); toast("undone"); }),
      btn("Clean clipboard", async () => { toast("cleaning…"); await api.clean_clipboard(); toast("cleaned"); }))));
}

// ---- Insights page --------------------------------------------------------
function heroStat(value, label) {
  return el("div", { class: "stat-mini" }, el("div", { class: "stat-mini-val" }, String(value)), el("div", { class: "stat-mini-cap" }, label));
}

function gaugePane(value, best) {
  const max = Math.max(200, best || 0, value || 0, 1);
  const r = 46, cx = 60, cy = 58;
  const frac = Math.min((value || 0) / max, 1);
  const arcLen = Math.PI * r;
  const path = `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`;
  const svg = `<svg viewBox="0 0 120 64" style="width:120px;height:64px;display:block">` +
    `<path d="${path}" fill="none" stroke="var(--border2)" stroke-width="9" stroke-linecap="round"/>` +
    `<path d="${path}" fill="none" stroke="var(--accent)" stroke-width="9" stroke-linecap="round" ` +
    `stroke-dasharray="${arcLen.toFixed(1)}" stroke-dashoffset="${(arcLen * (1 - frac)).toFixed(1)}"/></svg>`;
  return el("div", { class: "gauge-pane" },
    el("div", { html: svg }),
    el("div", { class: "stat-mini-val" }, String(value || 0)), el("div", { class: "stat-mini-cap" }, "avg WPM"),
    best ? el("div", { class: "gauge-best" }, `personal best ${best} wpm`) : null);
}

function appBar(a) {
  const name = a.app === "Unknown" ? "Unknown app" : a.app.replace(/\.exe$/i, "").replace(/^\w/, (c) => c.toUpperCase());
  return el("div", { class: "bar-row" },
    el("div", { class: "bar-name" }, name),
    el("div", { class: "bar-track" }, el("div", { class: "bar-fill", style: `width:${a.pct}%` })),
    el("div", { class: "bar-pct" }, a.count));
}

function toneBar(t, max) {
  const label = t.tone === "default" ? "No override" : t.tone[0].toUpperCase() + t.tone.slice(1);
  const pct = Math.round((t.count / max) * 100);
  return el("div", { class: "bar-row" },
    el("div", { class: "bar-name" }, label),
    el("div", { class: "bar-track" }, el("div", { class: "bar-fill", style: `width:${pct}%` })),
    el("div", { class: "bar-pct" }, t.count));
}

function hourlyChart(hourly) {
  const max = Math.max(...hourly, 1);
  const bars = hourly.map((v, h) => el("div", { class: "hour-bar", style: `height:${v ? Math.max(v / max * 100, 6) : 2}%`, title: `${h}:00 — ${v} dictation${v === 1 ? "" : "s"}` }));
  return el("div", { class: "hour-chart" }, ...bars);
}

function calendarGrid(cells, monthLabels) {
  const weeks = Math.max(1, Math.round(cells.length / 7));
  const months = el("div", { class: "cal-months", style: `grid-template-columns:repeat(${weeks},12px)` });
  for (const m of monthLabels || []) months.append(el("span", { style: `grid-column:${m.week + 1}` }, m.label));
  const dayLabels = el("div", { class: "cal-daylabels" }, ...["", "Mon", "", "Wed", "", "Fri", ""].map((d) => el("span", {}, d)));
  const grid = el("div", { class: "cal-grid", style: `grid-template-columns:repeat(${weeks},12px)` });
  for (const c of cells) {
    const future = c.level < 0;
    grid.append(el("div", {
      class: `cal-cell${future ? " future" : ` lvl-${c.level}`}`,
      title: future ? "" : `${c.date}: ${c.count} dictation${c.count === 1 ? "" : "s"}`,
    }));
  }
  return el("div", { class: "cal-wrap" }, months, el("div", { class: "cal-body" }, dayLabels, grid));
}

async function renderInsights() {
  const box = $("#insights-body");
  const data = await api.get_insights();
  S.insights = data;
  const stats = S.stats || {};
  box.innerHTML = "";

  box.append(group("Overview", el("div", { class: "overview-body" },
    gaugePane(stats.wpm || 0, data.best_wpm),
    el("div", { class: "stat-mini-grid" },
      heroStat((stats.n || 0).toLocaleString(), "Dictations"),
      heroStat((stats.raw_w || 0).toLocaleString(), "Words dictated"),
      heroStat(`${stats.streak || 0} / ${data.longest_streak}`, "Streak — current / longest"),
      heroStat(data.refined, "Refined by cleanup")))));

  box.append(group("Desktop usage",
    el("div", { class: "subfield" }, data.apps.length
      ? el("div", {}, ...data.apps.map(appBar))
      : el("div", { class: "desc" }, "No app data yet — recorded from your next dictation onward."))));

  box.append(group("Streak calendar",
    el("div", { class: "subfield" },
      calendarGrid(data.calendar, data.month_labels),
      el("div", { class: "cal-legend" }, el("span", {}, "Less"),
        el("span", { class: "cal-cell lvl-0" }), el("span", { class: "cal-cell lvl-1" }), el("span", { class: "cal-cell lvl-2" }),
        el("span", { class: "cal-cell lvl-3" }), el("span", { class: "cal-cell lvl-4" }), el("span", {}, "More")))));

  const toneMax = Math.max(...data.tones.map((t) => t.count), 1);
  box.append(group("Tone & timing", el("div", { class: "split-row" },
    el("div", {}, el("div", { class: "split-label" }, "Tone used"), ...data.tones.map((t) => toneBar(t, toneMax))),
    el("div", {}, el("div", { class: "split-label" }, "Busiest hours"), hourlyChart(data.hourly)))));
}

// ---- Settings page -----------------------------------------------------
function renderSettings() {
  const c = S.config;
  const box = $("#settings-body");
  box.innerHTML = "";

  // ============================================================ Voice & Recognition
  box.append(category("Voice & Recognition"));

  const mic = el("select", { class: "hud-select inline",
    onchange: (e) => patch({ input_device: e.target.value === "" ? null : Number(e.target.value) }) });
  for (const m of S.mics) {
    const o = el("option", { value: m.index === null ? "" : m.index }, m.name);
    if (m.index === c.input_device || (m.index === null && c.input_device == null)) o.selected = true;
    mic.append(o);
  }
  box.append(group("Microphone", fieldRow("Input device", mic)));

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
    renderSettings();
  });
  box.append(group("Hotkey",
    fieldRow("Shortcut", el("div", { class: "field-control", style: "gap:8px" }, hk, capBtn), { stacked: true }),
    fieldRow("Mode", el("div", { class: "field-control" },
      btn("Hold to talk", () => patch({ hotkey_mode: "hold" }, { rerender: renderSettings }), { active: (c.hotkey_mode || "hold") === "hold" }),
      btn("Tap to toggle", () => patch({ hotkey_mode: "toggle" }, { rerender: renderSettings }), { active: c.hotkey_mode === "toggle" })))));

  const models = el("div", { class: "model-grid" });
  for (const [size, title, note] of [["base.en", "Base", "fast"], ["small.en", "Small", "balanced"], ["medium.en", "Medium", "accurate"]]) {
    models.append(el("div", {
      class: "model-card" + (c.model_size === size ? " active" : ""),
      onclick: () => { patch({ model_size: size }); renderSettings(); toast("model → " + title.toLowerCase()); },
    }, el("div", { class: "title" }, title), el("div", { class: "note" }, note)));
  }
  box.append(group("Whisper model", models));

  const langSel = el("select", { class: "hud-select inline",
    onchange: (e) => patch({ language: e.target.value }) });
  for (const [code, label] of (S.languages || [["en", "English"]])) {
    const o = el("option", { value: code }, label);
    if ((c.language || "en") === code) o.selected = true;
    langSel.append(o);
  }
  box.append(group("Language", fieldRow("Spoken language", langSel, { desc: "Auto-detect works for any language Whisper supports" })));

  const silencePresets = S.silence_presets || [];
  const curThresh = c.silence_threshold ?? 0.02, curDur = c.silence_duration_s ?? 1.5;
  const silSel = el("select", { class: "hud-select inline",
    onchange: (e) => { const p = silencePresets.find(([k]) => k === e.target.value); if (p) patch({ silence_threshold: p[2], silence_duration_s: p[3] }); } });
  let silMatch = false;
  for (const [key, label, t, d] of silencePresets) {
    const o = el("option", { value: key }, label);
    if (t === curThresh && d === curDur) { o.selected = true; silMatch = true; }
    silSel.append(o);
  }
  if (!silMatch) { const o = el("option", { value: "" }, "Custom"); o.selected = true; o.disabled = true; silSel.prepend(o); }
  box.append(group("Silence auto-stop",
    fieldRow("Auto-stop hands-free recording", toggleSwitch(!!c.silence_auto_stop, () => patch({ silence_auto_stop: !c.silence_auto_stop }, { rerender: renderSettings })), { desc: "Stop listening automatically after trailing silence (toggle/hands-free mode only)" }),
    fieldRow("Sensitivity", silSel, { desc: "How much silence before it stops" })));

  // ============================================================ Personalization
  box.append(category("Personalization"));

  box.append(group("Custom vocabulary", el("div", { class: "subfield" },
    tagInput("vocab", c.vocabulary || [], (list) => patch({ vocabulary: list }, { rerender: renderSettings }),
      "add word, press Enter…", renderSettings))));

  const tone = S.config.tone_overrides || {};
  const toneWrap = el("div", { class: "subfield" });
  for (const [key, cap] of [["casual", "Casual"], ["formal", "Formal"], ["verbatim", "Verbatim"]]) {
    toneWrap.append(el("div", { class: "subfield-label" }, cap),
      el("input", { class: "hud-text", type: "text", value: (tone[key] || []).join(", "),
        placeholder: "exe names, comma-separated", style: "margin-bottom:10px",
        onchange: (e) => { const t = { ...(S.config.tone_overrides || {}) }; t[key] = splitCsv(e.target.value.toLowerCase()); patch({ tone_overrides: t }); } }));
  }
  box.append(group("Per-app tone overrides", toneWrap));

  const snip = Object.entries(c.snippets || {}).map(([k, v]) => `${k} => ${v}`).join("\n");
  const area = el("textarea", { class: "hud-area", rows: 4, placeholder: "omw => on my way",
    onchange: (e) => patch({ snippets: parseSnippets(e.target.value) }) }, snip);
  box.append(group("Snippets — trigger => expansion", el("div", { class: "subfield" }, area)));

  box.append(group("Voice commands",
    el("div", { class: "subfield" }, el("div", { class: "desc" },
      "Say ", el("b", {}, "\"…make it formal\""), " or ", el("b", {}, "\"…make it casual\""),
      " at the end of a dictation to override the tone for that one dictation, regardless of which app you're in."))));

  // ============================================================ Behavior
  box.append(category("Behavior"));

  box.append(group("Typing safety",
    fieldRow("Review long dictations", toggleSwitch(!!c.review_before_typing, () => patch({ review_before_typing: !c.review_before_typing }, { rerender: renderSettings })), { desc: "Pop up a review window before typing text over 1000 characters" }),
    fieldRow("Auto-punctuate", toggleSwitch(c.auto_punctuate !== false, () => patch({ auto_punctuate: c.auto_punctuate === false ? true : false }, { rerender: renderSettings })), { desc: "Capitalize and add terminal punctuation to raw/fallback text" })));

  box.append(group("Sound cues",
    fieldRow("Start/stop chime", toggleSwitch(!!c.sound_enabled, () => patch({ sound_enabled: !c.sound_enabled }, { rerender: renderSettings })), { desc: "Short system sound when recording starts and when text is typed" })));

  // ============================================================ Data & Privacy
  box.append(category("Data & Privacy"));

  const healthBox = el("div", { class: "status-box" },
    el("div", { class: "bar" }), el("div", { class: "lines", id: "health-lines", html: healthLines(S.health) }));
  box.append(group("Health",
    el("div", { class: "btn-row" }, btn("Retry", async () => { await api.retry_health(); refreshLive(true); toast("checking…"); })),
    healthBox));

  const storageBox = el("div", { class: "status-box" },
    el("div", { class: "bar dim" }), el("div", { class: "lines", id: "storage-lines", html: storageLines(S.storage) }));
  box.append(group("Storage", storageBox,
    el("div", { class: "btn-row" },
      btn("Clear Whisper cache", async () => { if (await confirmDlg("Clear the local Whisper cache?")) { await api.clear_whisper_cache(); refreshLive(true); } }),
      btn("Clear Ollama models", async () => { if (await confirmDlg("Delete the local cleanup model (~4.7 GB)?")) { await api.clear_ollama_models(); refreshLive(true); } }, { variant: "hud-btn-danger" })),
    el("div", { class: "btn-row" },
      btn("Backup settings…", async () => { const r = await api.backup_settings(); if (r) toast("saved"); }),
      btn("Restore settings…", async () => { const r = await api.restore_settings(); if (r) { S.config = await api.get_config(); applyTheme(S.config); renderSettings(); toast("restored"); } }))));

  box.append(group("History & logging",
    fieldRow("Log history to disk", toggleSwitch(!!c.log_history, () => patch({ log_history: !c.log_history }, { rerender: renderSettings })), { desc: "Off keeps new dictations in memory only" }),
    el("div", { class: "btn-row" },
      btn("Choose folder…", async () => { const r = await api.pick_folder(); if (r) { S.config.history_dir = r; refreshLive(true); renderSettings(); } }),
      btn("Open folder", () => api.open_folder()),
      btn("Export history…", async () => { const r = await api.export_history(); if (r) toast("exported " + r + " entries"); }),
      btn("Purge history…", async () => { if (await confirmDlg("Permanently delete ALL history and stats? No undo.")) { await api.purge_history(); refreshLive(true); } }, { variant: "hud-btn-danger" })),
    el("div", { class: "log-note", id: "log-note" }, logNote())));

  box.append(group("Redaction", el("div", { class: "subfield" },
    el("div", { class: "desc", style: "margin-bottom:10px" }, "Words or phrases replaced with [redacted] before they're ever written to history.jsonl — the raw text is still typed, only what's stored on disk is scrubbed."),
    tagInput("redact", c.redact_patterns || [], (list) => patch({ redact_patterns: list }, { rerender: renderSettings }),
      "add word or phrase, press Enter…", renderSettings))));

  box.append(group("Privacy",
    el("div", { class: "btn-row" }, el("span", { class: "about-badge" }, el("span", { class: "dot accent" }), "No cloud. No network calls except a local Ollama probe."))));
  box.append(group("Files",
    fieldRow("History log", el("span", { class: "hud-label" }, S ? S.history_path : ""), { stacked: true })));

  // ============================================================ Appearance
  box.append(category("Appearance"));

  const accentSwatch = el("input", {
    type: "color", class: "swatch", value: c.accent_color || (c.theme === "light" ? "#101010" : "#f2f1ee"),
    oninput: (e) => patch({ accent_color: e.target.value }),
  });
  const highlightSwatch = el("input", {
    type: "color", class: "swatch", value: c.highlight_color || "#22c55e",
    oninput: (e) => patch({ highlight_color: e.target.value }),
  });
  box.append(group("Appearance",
    fieldRow("Dark theme", toggleSwitch(c.theme !== "light", () => patch({ theme: c.theme === "dark" ? "light" : "dark" }, { rerender: renderSettings })), { desc: "Switch between dark and light" }),
    fieldRow("Accent color", el("div", { class: "field-control" }, accentSwatch,
      c.accent_color ? btn("Reset", () => patch({ accent_color: null }, { rerender: renderSettings })) : null), { desc: "Used for buttons, nav, and controls" }),
    fieldRow("Highlight color", el("div", { class: "field-control" }, highlightSwatch,
      c.highlight_color ? btn("Reset", () => patch({ highlight_color: null }, { rerender: renderSettings })) : null), { desc: "Used for streaks and positive/updated states" }),
    fieldRow("Auto 7pm–7am", toggleSwitch(!!c.auto_theme, () => patch({ auto_theme: !c.auto_theme }, { rerender: renderSettings })), { desc: "Automatically switch theme by time of day" })));

  // ============================================================ System
  box.append(category("System"));

  box.append(group("Startup",
    fieldRow("Start Dictator on login", toggleSwitch(!!c.start_on_login, async (e) => {
      const on = !c.start_on_login;
      const ok = await api.set_start_on_login(on);
      if (ok) { S.config.start_on_login = on; renderSettings(); toast(on ? "will start on login" : "removed from startup"); }
      else toast("failed — check permissions");
    }), { desc: "Launch automatically when you sign in to Windows" })));

  const profiles = c.profiles || {};
  const profileRows = el("div", {});
  for (const [name, p] of Object.entries(profiles)) {
    profileRows.append(fieldRow(name,
      el("div", { class: "field-control" },
        c.active_profile === name ? el("span", { class: "hud-badge" }, "active") :
          btn("Apply", () => patch({ hotkey_mods: p.hotkey_mods, hotkey_mode: p.hotkey_mode, model_size: p.model_size, language: p.language, active_profile: name }, { rerender: renderSettings })),
        btn("Delete", () => { const np = { ...profiles }; delete np[name]; patch({ profiles: np, active_profile: c.active_profile === name ? null : c.active_profile }, { rerender: renderSettings }); }, { variant: "hud-btn-danger" }))));
  }
  const newProfileName = el("input", { class: "hud-text", type: "text", placeholder: "profile name…", style: "max-width:220px" });
  box.append(group("Hotkey profiles",
    Object.keys(profiles).length ? profileRows : null,
    el("div", { class: "btn-row" }, newProfileName,
      btn("Save current as profile", () => {
        const name = newProfileName.value.trim();
        if (!name) return;
        const np = { ...profiles, [name]: { hotkey_mods: c.hotkey_mods, hotkey_mode: c.hotkey_mode, model_size: c.model_size, language: c.language } };
        patch({ profiles: np, active_profile: name }, { rerender: renderSettings });
        toast("profile saved");
      }))));
}

// ---- About page ----------------------------------------------------------
function aboutP(text) { return el("div", { class: "desc about-p" }, text); }

function renderAbout() {
  const box = $("#about-body");
  box.innerHTML = "";

  const markSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">` +
    `<rect x="1" y="1" width="22" height="22" rx="6" fill="#0e0d0c" stroke="none"/>` +
    `<path d="M3.6 12 5.7 13.7 7.8 13.47 9.9 8.79 12 8.31 14.1 13.61 16.2 15.18 18.3 12.14 20.4 12" fill="none"/></svg>`;

  box.append(group("The app", el("div", { class: "subfield" },
    el("div", { class: "about-hero" },
      el("div", { class: "brand-mark", style: "width:44px;height:44px" }, el("div", { html: markSvg })),
      el("div", {}, el("div", { class: "about-title" }, "Dictator"),
        el("div", { class: "about-tagline" }, "Local voice dictation for Windows."))),
    el("div", { style: "margin-top:18px" },
      aboutP("Hold a hotkey, speak, release — Dictator transcribes what you said locally with faster-whisper (CUDA when available, CPU fallback), runs it through a local Ollama model to strip filler words, resolve self-corrections, and fix grammar without changing your tone or adding anything you didn't say, then types the result into whatever window has focus via simulated keystrokes, never touching the clipboard; it's aware of what app you're in — verbatim for code and terminals, casual or formal elsewhere, all editable — keeps custom vocabulary spelled right, expands snippets, lets you override tone per-dictation by ending with \"…make it formal/casual,\" supports both hold-to-talk and hands-free recording with optional silence auto-stop, redacts sensitive words before anything touches disk, and turns the whole log into real Insights — WPM, app usage, a streak calendar, tone distribution, busiest hours — computed locally, with the only network call being a local probe to Ollama on localhost.")),
    el("div", { class: "about-badge", style: "margin-top:18px" }, el("span", { class: "dot accent" }), "Local-only — no cloud, no accounts, no telemetry"))));

  box.append(group("Built by", el("div", { class: "subfield" },
    el("div", { class: "about-title" }, "Daksh"),
    el("div", { style: "margin: 10px 0 18px" },
      aboutP("Dictator is one of several local-first, daily-driver tools built and actually used by Daksh, who runs Uideas — a solo digital agency doing video editing, motion graphics, brand identity, web development, and short-form content. It's not a side project made to be shown off; it's the dictation tool he reaches for himself, dozens of times a day, which is the only reason the redaction list, the per-app tone overrides, and the silence auto-stop exist at all — they got added because he personally ran into the gap, not because a feature list said to."),
      aboutP("Every tool in that stack follows the same rule: it runs entirely on the machine it's installed on. SQLite for storage, local Whisper and Ollama models for inference, no cloud dependency unless there's a specific, deliberate, stated reason for one — and even then, it's called out explicitly rather than snuck in. Meeting Memory transcribes and recalls meetings the same way, every recall answer sourced to a timestamp. Ledger runs tasks, habits, calendar, and an outreach pipeline as a self-hosted app, not a subscription. Naggy turns voice notes into tracked tasks. None of them phone home, because none of them were built to need to."),
      aboutP("The agency side runs in parallel — client work, brand identity, short-form content, and @uideasofficial building an audience one post at a time — but the tools exist because running a one-person shop means building the infrastructure nobody else is going to build for you, and it turning out useful enough to hand to other people is the part that was never the plan.")),
    el("div", { class: "btn-row" },
      btn("GitHub", () => api.open_url("https://github.com/IntellectDaksh")),
      btn("LinkedIn", () => api.open_url("https://www.linkedin.com/in/daksh-agrawal-/")),
      btn("Instagram", () => api.open_url("https://instagram.com/uideasofficial"))))));
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
let lastLiveJSON = "";
async function refreshLive() {
  const live = await api.get_live();
  const json = JSON.stringify(live);
  if (json === lastLiveJSON) return;
  lastLiveJSON = json;
  S.stats = live.stats; S.health = live.health; S.storage = live.storage;
  renderStats(live.stats);
  renderStatusBar(live.health);
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

// ---- page nav ------------------------------------------------------------
function initNav() {
  const items = document.querySelectorAll(".nav-item");
  const pages = document.querySelectorAll(".page");
  items.forEach((it) => it.addEventListener("click", () => {
    items.forEach((x) => x.classList.remove("active"));
    pages.forEach((x) => x.classList.remove("active"));
    it.classList.add("active");
    document.querySelector(`.page[data-page="${it.dataset.page}"]`).classList.add("active");
  }));
}

// ---- boot --------------------------------------------------------------
function safe(name, fn) {
  try { fn(); } catch (err) { safeError(name, err); }
}
async function safeAsync(name, fn) {
  try { await fn(); } catch (err) { safeError(name, err); }
}
function safeError(name, err) {
  console.error(name, err);
  const box = $(`#${name}-body`) || $("#app");
  if (box) box.prepend(el("div", { style: "background:#3a1414;border:1px solid #a33;color:#f88;padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:12px;white-space:pre-wrap" }, `[${name}] ${err && err.stack ? err.stack : err}`));
}

async function boot() {
  api = window.pywebview.api;
  S = await api.get_state();
  applyTheme(S.config);
  initNav();
  safe("stats", () => renderStats(S.stats));
  safe("statusbar", () => renderStatusBar(S.health));
  safe("recent", () => renderRecent(S.recent));
  safe("home", renderHome);
  safeAsync("insights", renderInsights);
  safe("settings", renderSettings);
  safe("about", renderAbout);

  let searchT;
  $("#search").addEventListener("input", (e) => {
    query = e.target.value.trim().toLowerCase();
    clearTimeout(searchT);
    searchT = setTimeout(runSearch, 180);
  });

  setInterval(() => { if (!document.hidden) refreshLive(); }, 2000);
}

window.addEventListener("pywebviewready", () => {
  boot().catch((err) => {
    console.error("boot", err);
    document.body.prepend(el("div", { style: "background:#3a1414;border:1px solid #a33;color:#f88;padding:12px 16px;font-size:12px;white-space:pre-wrap" }, `[boot] ${err && err.stack ? err.stack : err}`));
  });
});
