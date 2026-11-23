/* Lab model candle: watch the candle model learn, change how it learns, start and stop it.

   Opens over the trading panel (header button "Lab model", the candle pane's "Buka lab", or #lab in the address).
   It reads /api/quant/candle/{lab,history,log} while open and visible: every 3 s while training, slower when idle.
   Settings go to settings.json in the run folder; a running trainer reads the live ones after each epoch.

   Charts are plain SVG drawn here, coloured by the palette's chart tokens (one axis each, crosshair and tooltip,
   a table under every chart). Text from the server only reaches the page as text (T.h / textContent). */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;
  const SVG = "http://www.w3.org/2000/svg";
  const SIZE_KEY = "trade:lab-size";
  const state = { open: false, size: "", lab: null, hist: null, log: [], examPick: "best", dirty: false, timers: {},
    drawLines: true, drawBars: true, lastBest: null, logCount: 0 };
  const P = window.SquidPage || { reveal() {}, countTo(el, v, f) { el.textContent = f(v); }, calm: () => true };

  const sym = () => S.symbol || "XAUUSD";
  const fmtPct = (v, d = 2) => (v === null || v === undefined ? "–" : `${(Number(v) * 100).toFixed(d)}%`);
  const fmtNum = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "–" : Number(v).toFixed(d));
  const fmtLr = (v) => (v === null || v === undefined ? "–" : Number(v).toExponential(1).replace("e-", "e−"));
  const fmtInt = (v) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("id-ID"));
  const ago = (s) => {
    if (!s && s !== 0) return "–";
    const m = Math.floor(s / 60), hr = Math.floor(m / 60), d = Math.floor(hr / 24);
    return d ? `${d}h ${hr % 24}j` : hr ? `${hr}j ${m % 60}m` : m ? `${m}m ${Math.floor(s % 60)}d` : `${Math.floor(s)}d`;
  };
  const svg = (tag, attrs, ...kids) => {
    const el = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, v);
    for (const kid of kids.flat()) if (kid) el.append(kid);
    return el;
  };
  const savedSize = () => { try { return localStorage.getItem(SIZE_KEY) || ""; } catch { return ""; } };

  // ── settings: label, help, kind ───────────────────────────────────────────
  const FIELDS = {
    lr: ["Learning rate", "Besar langkah belajar. Kecil = pelan tapi stabil.", "any"],
    batch: ["Batch", "Jendela per langkah. Besar = langkah lebih halus, epoch lebih sedikit langkah.", "1"],
    warmup_steps: ["Pemanasan", "Langkah awal dengan LR naik pelan.", "1"],
    weight_decay: ["Weight decay", "Rem untuk bobot yang membesar: melawan hafalan.", "any"],
    dropout: ["Dropout", "Bagian neuron yang dimatikan acak saat latihan.", "any"],
    input_noise: ["Noise input", "Getaran kecil di data masuk supaya tidak hafal.", "any"],
    mirror: ["Porsi cermin", "0–1: bagian sampel yang dibalik naik↔turun.", "any"],
    ema_decay: ["EMA", "Rata-rata bobot untuk ujian. Dekat 1 = lebih halus.", "any"],
    patience: ["Kesabaran", "Epoch tanpa kemajuan sebelum kembali ke versi terbaik dengan LR setengah.", "1"],
    min_lr_scale: ["LR minimum", "Di bawah skala ini latihan menunggu satu hari data baru.", "any"],
    val_fraction: ["Porsi ujian", "Bagian bar terbaru yang dipakai ujian, tidak pernah dilatih.", "any"],
    threads: ["Thread CPU", "0 = bawaan. 2 = ringan untuk laptop.", "1"],
    hours: ["Batas jam", "Berhenti sendiri setelah sekian jam. 0 = tanpa batas.", "any"],
    epochs: ["Batas epoch", "Berhenti setelah sekian epoch run ini. 0 = tanpa batas.", "1"],
    compile: ["torch.compile", "Lebih cepat untuk large/xl, butuh waktu di awal.", "select"],
    ctx: ["Konteks (bar)", "Berapa bar M5 yang dilihat sekaligus.", "1"],
    d_model: ["Lebar", "Ukuran tiap lapisan (d_model).", "1"],
    layers: ["Lapisan", "Kedalaman transformer.", "1"],
    heads: ["Head attention", "d_model harus habis dibagi head, hasilnya genap.", "1"],
  };
  const GROUPS = [
    ["live", "Langsung berlaku", "Diambil trainer yang jalan setelah epoch ini."],
    ["restart", "Saat mulai lagi", "Bobot tetap; dipakai begitu dijalankan ulang."],
    ["run", "Proses", "Cara proses dijalankan; berlaku saat Mulai latih."],
    ["fresh", "Bentuk jaringan", "Hanya berlaku dengan Mulai dari nol."],
  ];

  // ── open and close ────────────────────────────────────────────────────────
  function open() {
    if (state.open) return;
    state.open = true;
    $("lab").hidden = false;
    document.body.classList.add("lab-on");
    if (location.hash !== "#lab") history.replaceState(null, "", "#lab");
    state.size = state.size || savedSize();
    state.drawLines = state.drawBars = true;
    state.lastBest = null;
    P.reveal([...document.querySelectorAll(".lab-head > *")]);
    refreshAll().then(() => P.reveal([...$("lab-kpis").children, ...document.querySelectorAll(".lab-card")], 3));
    $("lab-back").focus();
  }
  function close() {
    if (!state.open) return;
    state.open = false;
    $("lab").hidden = true;
    document.body.classList.remove("lab-on");
    Object.values(state.timers).forEach(clearTimeout);
    if (location.hash === "#lab") history.replaceState(null, "", location.pathname + location.search);
    hideTip();
    $("labbtn").focus();
  }

  // ── polling ───────────────────────────────────────────────────────────────
  const running = () => Boolean(state.lab && state.lab.running);
  function later(name, fn, ms) {
    clearTimeout(state.timers[name]);
    state.timers[name] = setTimeout(() => {
      if (!state.open) return;
      if (document.hidden) { later(name, fn, ms); return; }
      fn();
    }, ms);
  }
  async function refreshAll() {
    await loadLab();
    loadHistory();
    loadLog();
  }

  async function loadLab() {
    let lab;
    try {
      const q = new URLSearchParams({ symbol: sym(), size: state.size || "" });
      lab = await T.api(`/api/quant/candle/lab?${q}`);
    } catch (e) {
      note(/Not Found/i.test(e.message) ? "Server trading belum kenal lab ini: restart server trading (Ctrl+C lalu python main.py trading)." : e.message, "warn");
      later("lab", loadLab, 15000);
      return;
    }
    const sizeChanged = state.lab && state.lab.size !== lab.size;
    state.lab = lab;
    state.size = lab.size;
    renderHead(lab);
    renderSizes(lab);
    renderKpis(lab);
    renderCandles();
    if (!state.dirty || sizeChanged) renderForm(lab);
    else markFormStatus(lab);
    setHeaderDot(lab.running);
    later("lab", loadLab, lab.running ? 3000 : 15000);
  }

  async function loadHistory() {
    if (!state.lab) return;
    try {
      const q = new URLSearchParams({ symbol: sym(), size: state.lab.size, points: "700" });
      state.hist = await T.api(`/api/quant/candle/history?${q}`);
    } catch { state.hist = state.hist || { rows: [] }; }
    renderCharts();
    later("hist", loadHistory, running() ? 6000 : 30000);
  }

  async function loadLog() {
    try {
      const r = await T.api("/api/quant/candle/log?lines=400");
      state.log = r.lines || [];
    } catch { /* the log is a nice-to-have */ }
    renderLog();
    later("log", loadLog, running() ? 3000 : 20000);
  }

  // ── header, sizes, notes ──────────────────────────────────────────────────
  function note(text, cls = "") {
    const el = $("lab-note");
    el.textContent = text || "";
    el.className = `lab-note ${cls}`;
    el.hidden = !text;
  }

  function renderHead(lab) {
    const pill = $("lab-pill");
    if (lab.running && lab.waiting) { pill.textContent = "Menunggu data baru"; pill.className = "pill warn"; }
    else if (lab.running) { pill.textContent = "Melatih"; pill.className = "pill demo lab-live"; }
    else if (lab.has_model) { pill.textContent = "Berhenti · model siap"; pill.className = "pill off"; }
    else { pill.textContent = "Belum ada model"; pill.className = "pill off"; }
    const params = lab.params ? ` · ${(lab.params / 1e6).toFixed(1)}M parameter` : "";
    $("lab-sub").textContent = `${lab.instrument} · ${lab.size}${params} · desain ARCH ${lab.arch}`;
    const tog = $("lab-toggle");
    tog.textContent = lab.running ? "Hentikan" : (lab.has_model ? "Lanjut latih" : "Mulai latih");
    tog.className = lab.running ? "btn danger" : "btn primary";
    tog.disabled = Boolean(lab.other_running);
    $("lab-reset").disabled = !lab.running;
    $("lab-save").disabled = !lab.running;
    $("lab-fresh").disabled = lab.running || Boolean(lab.other_running);
    if (lab.other_running) note(`Ukuran ${lab.other_running.size} sedang dilatih (PID ${lab.other_running.pid}). Satu trainer sekaligus: hentikan dulu untuk melatih ${lab.size}.`, "warn");
    else if (lab.running && lab.waiting) note("Sudah mentok di data yang ada. Ia lanjut sendiri begitu ada satu hari bar M5 baru, atau tekan Segarkan LR.", "");
    else note("");
  }

  function renderSizes(lab) {
    const box = $("lab-sizes");
    box.replaceChildren(...lab.sizes.map((s) => {
      const score = s.current_design === false ? "desain lama" : s.has_model ? fmtPct(s.best_score ?? s.exam_score) : "kosong";
      const b = h("button", { type: "button", role: "tab", class: `lab-size${s.size === lab.size ? " on" : ""}${s.running ? " live" : ""}`,
        "aria-selected": String(s.size === lab.size), title: `ctx ${s.shape.ctx} · lebar ${s.shape.d_model} · ${s.shape.layers} lapisan` },
      h("b", null, s.size), h("small", null, s.running ? `melatih · ${score}` : score));
      b.onclick = () => {
        if (s.size === state.size) return;
        state.size = s.size;
        state.dirty = false;
        try { localStorage.setItem(SIZE_KEY, s.size); } catch { /* not remembered */ }
        state.hist = null;
        state.drawLines = state.drawBars = true;
        state.lastBest = null;
        refreshAll().then(() => P.reveal([...$("lab-kpis").children], 0));
      };
      return b;
    }));
  }

  function setHeaderDot(on) {
    $("labbtn").classList.toggle("is-live", Boolean(on));
  }

  // ── KPI tiles: built once, then updated in place so the numbers can count ────
  function kpi(key, label, value, fmt, sub, cls = "") {
    const box = $("lab-kpis");
    let tile = box.querySelector(`[data-k="${key}"]`);
    if (!tile) {
      tile = h("div", { class: "lab-kpi", "data-k": key }, h("span", { class: "lab-kpi-label" }),
        h("b", { class: "lab-kpi-value" }), h("span", { class: "lab-kpi-sub" }));
      box.append(tile);
    }
    tile.className = `lab-kpi ${cls}`;
    tile.children[0].textContent = label;
    const v = tile.children[1];
    if (typeof value === "number" && Number.isFinite(value)) P.countTo(v, value, fmt);
    else { delete v.dataset.sqValue; v.textContent = value ?? "–"; }
    tile.children[2].textContent = sub || "";
    return tile;
  }
  function renderKpis(lab) {
    const p = lab.process;
    const m = lab.machine || {};
    const best = lab.best_score;
    const edge = lab.best_exam && lab.best_exam.edge;
    const gain = best ? (1 - best) * 100 : null;
    const limit = lab.limit || {};
    const until = limit.deadline ? `berhenti ${new Date(limit.deadline * 1000).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}` :
      limit.epochs ? `batas ${fmtInt(limit.epochs)} epoch` : "tanpa batas";
    const hero = kpi("best", "Ujian terbaik", best ?? null, (v) => fmtPct(v),
      best ? `${gain >= 0 ? gain.toFixed(2) + "% lebih dekat" : (-gain).toFixed(2) + "% lebih jauh"} dari tanpa otak${lab.best_at ? ` · epoch ${fmtInt(lab.best_at.epoch)}` : ""}` : "belum ada ujian",
      `hero ${edge ? "good" : ""}`);
    if (state.lastBest !== null && best && best < state.lastBest) {   // a new record while watching
      hero.classList.remove("sq-flash"); void hero.offsetWidth; hero.classList.add("sq-flash");
    }
    state.lastBest = best ?? null;
    kpi("edge", "Lulus ujian", edge ? "Ya" : best ? "Belum" : "–", null, edge ? "arah bisa dipertimbangkan" : "pakai pitanya, bukan arahnya", edge ? "good" : "");
    kpi("epoch", "Epoch", lab.epoch ?? null, (v) => fmtInt(Math.round(v)),
      lab.seconds ? `${fmtNum(lab.seconds, 1)} detik/epoch${lab.steps_per_epoch ? ` · ${lab.steps_per_epoch} langkah` : ""}` : "–");
    kpi("last", "Ujian terakhir", lab.score ?? null, (v) => fmtPct(v), lab.train_ratio ? `latih ${fmtPct(lab.train_ratio)}` : "");
    kpi("lr", "Learning rate", fmtLr(lab.lr), null, lab.lr_scale ? `skala ×${Number(lab.lr_scale).toPrecision(3).replace(/\.?0+$/, "")}` : "");
    if (p) kpi("cpu", "Proses", p.cpu_pct, (v) => `${Math.round(v)}% CPU`,
      `${p.rss_mb ? fmtInt(Math.round(p.rss_mb)) + " MB" : "–"} · ${p.threads} thread · ${ago(p.uptime_s)} · ${until}`);
    else kpi("cpu", "Mesin", m.load ? m.load[0] : null, (v) => `beban ${v.toFixed(2)}`, `${m.cores || "–"} core · RAM bebas ${m.mem_free_gb ?? "–"} GB`);
    kpi("data", "Data M5", lab.data && lab.data.bars ? lab.data.bars : null, (v) => fmtInt(Math.round(v)),
      lab.data && lab.data.days ? `${Math.round(lab.data.days)} hari · s/d ${String(lab.data.last || "").slice(0, 10)}` : "store belum terbaca");
  }

  // ── charts ────────────────────────────────────────────────────────────────
  const tip = () => $("lab-tip");
  function showTip(x, y, rows) {
    const el = tip();
    el.replaceChildren(...rows.map(([key, value, label]) => h("div", { class: "lab-tip-row" },
      key ? h("span", { class: `lab-key ${key}` }) : null, h("b", null, value), h("span", null, label))));
    el.hidden = false;
    const box = el.getBoundingClientRect();
    const left = Math.min(window.innerWidth - box.width - 8, x + 14);
    const top = y - box.height - 12 < 8 ? y + 16 : y - box.height - 12;
    el.style.left = `${Math.max(8, left)}px`;
    el.style.top = `${top}px`;
  }
  function hideTip() { if (tip()) tip().hidden = true; }

  function niceTicks(lo, hi, count) {
    const span = hi - lo || Math.abs(hi) || 1;
    const raw = span / Math.max(1, count);
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((k) => k >= raw) || raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) out.push(+v.toFixed(10));
    return out;
  }

  /* One-axis line chart. series: [{name, cls, key, dash, values: [y|null]}]; xs: epochs. */
  function lineChart(el, { xs, series, fmt, ref, refLabel, marks, tipRows, emptyText, minSpan = 0, animate = false }) {
    const width = Math.max(240, el.clientWidth || 600);
    const height = Math.max(110, el.clientHeight || 240);
    if (!xs.length) { el.replaceChildren(h("p", { class: "lab-empty" }, emptyText || "Belum ada epoch.")); return; }
    const pad = { l: 52, r: 16, t: 12, b: 26 };
    const vals = series.flatMap((s) => s.values).filter((v) => v !== null && Number.isFinite(v));
    if (ref !== undefined) vals.push(ref);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < minSpan) { const mid = (hi + lo) / 2; lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
    const padY = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1;
    lo -= padY; hi += padY;
    const x0 = xs[0], x1 = xs[xs.length - 1] === x0 ? x0 + 1 : xs[xs.length - 1];
    const X = (x) => pad.l + ((x - x0) / (x1 - x0)) * (width - pad.l - pad.r);
    const Y = (y) => pad.t + (1 - (y - lo) / (hi - lo)) * (height - pad.t - pad.b);
    const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, class: "lab-svg", role: "img",
      "aria-label": series.map((s) => s.name).join(", ") });
    const grid = svg("g", { class: "lab-grid-lines" });
    for (const t of niceTicks(lo, hi, 4)) {
      grid.append(svg("line", { x1: pad.l, x2: width - pad.r, y1: Y(t), y2: Y(t) }),
        svg("text", { x: pad.l - 8, y: Y(t) + 4, "text-anchor": "end", class: "lab-tick" }, document.createTextNode(fmt(t))));
    }
    for (const t of niceTicks(x0, x1, Math.min(6, Math.floor(width / 90)))) {
      if (t < x0 || t > x1 || t !== Math.round(t)) continue;
      grid.append(svg("text", { x: X(t), y: height - 6, "text-anchor": "middle", class: "lab-tick" }, document.createTextNode(fmtInt(t))));
    }
    root.append(grid);
    if (ref !== undefined && ref >= lo && ref <= hi) {
      root.append(svg("line", { x1: pad.l, x2: width - pad.r, y1: Y(ref), y2: Y(ref), class: "lab-ref" }));
      if (refLabel) root.append(svg("text", { x: width - pad.r, y: Y(ref) - 6, "text-anchor": "end", class: "lab-ref-label" }, document.createTextNode(refLabel)));
    }
    for (const s of series) {
      let d = "";
      s.values.forEach((v, i) => {
        if (v === null || !Number.isFinite(v)) return;
        d += `${d && s.values[i - 1] !== null ? "L" : "M"}${X(xs[i]).toFixed(1)},${Y(v).toFixed(1)}`;
      });
      root.append(svg("path", { d, class: `lab-line ${s.cls}${s.dash ? " dash" : ""}` }));
      const lastI = s.values.map((v, i) => (v === null ? -1 : i)).filter((i) => i >= 0).pop();
      if (s.label && lastI !== undefined && series.length > 1) {
        root.append(svg("text", { x: Math.min(width - pad.r - 2, X(xs[lastI]) + 6), y: Y(s.values[lastI]) + (s.labelDy || 4), class: "lab-direct", "text-anchor": X(xs[lastI]) > width - 90 ? "end" : "start" },
          document.createTextNode(s.label)));
      }
    }
    for (const m of marks || []) {
      root.append(svg("circle", { cx: X(m.x), cy: Y(m.y), r: 3.5, class: `lab-mark ${m.cls || ""}` }));
    }
    const cross = svg("line", { y1: pad.t, y2: height - pad.b, class: "lab-cross", visibility: "hidden" });
    const dots = series.map((s) => svg("circle", { r: 4.5, class: `lab-hover ${s.cls}`, visibility: "hidden" }));
    root.append(cross, ...dots);
    const hit = svg("rect", { x: pad.l, y: pad.t, width: width - pad.l - pad.r, height: height - pad.t - pad.b, class: "lab-hit", tabindex: "0" });
    root.append(hit);
    const at = (clientX) => {
      const box = root.getBoundingClientRect();
      const px = ((clientX - box.left) / box.width) * width;
      const target = x0 + ((px - pad.l) / (width - pad.l - pad.r)) * (x1 - x0);
      let best = 0;
      for (let i = 1; i < xs.length; i++) if (Math.abs(xs[i] - target) < Math.abs(xs[best] - target)) best = i;
      return best;
    };
    const show = (i, cx, cy) => {
      cross.setAttribute("x1", X(xs[i])); cross.setAttribute("x2", X(xs[i])); cross.setAttribute("visibility", "visible");
      series.forEach((s, k) => {
        const v = s.values[i];
        if (v === null || !Number.isFinite(v)) { dots[k].setAttribute("visibility", "hidden"); return; }
        dots[k].setAttribute("cx", X(xs[i])); dots[k].setAttribute("cy", Y(v)); dots[k].setAttribute("visibility", "visible");
      });
      showTip(cx, cy, tipRows(i));
    };
    const off = () => { cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); hideTip(); };
    hit.addEventListener("pointermove", (e) => show(at(e.clientX), e.clientX, e.clientY));
    hit.addEventListener("pointerleave", off);
    let kbd = xs.length - 1;
    hit.addEventListener("focus", () => { const b = hit.getBoundingClientRect(); show(kbd, b.right - 40, b.top + 20); });
    hit.addEventListener("blur", off);
    hit.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      kbd = Math.max(0, Math.min(xs.length - 1, kbd + (e.key === "ArrowRight" ? 1 : -1)));
      const b = hit.getBoundingClientRect();
      show(kbd, b.left + ((X(xs[kbd]) - pad.l) / (width - pad.l - pad.r)) * b.width, b.top + 20);
    });
    el.replaceChildren(root);
    if (animate && !P.calm()) {
      root.querySelectorAll(".lab-line").forEach((path, k) => {
        const len = path.getTotalLength();
        if (!len) return;
        path.style.strokeDasharray = path.classList.contains("dash") ? "" : `${len}`;
        path.style.strokeDashoffset = `${len}`;
        path.animate([{ strokeDashoffset: len, opacity: 0.2 }, { strokeDashoffset: 0, opacity: 1 }],
          { duration: 1100, delay: k * 140, easing: "cubic-bezier(0.16, 1, 0.3, 1)", fill: "forwards" })
          .finished.then(() => { path.style.strokeDasharray = ""; path.style.strokeDashoffset = ""; }).catch(() => {});
      });
      root.querySelectorAll(".lab-mark, .lab-direct").forEach((m, k) => {
        m.animate([{ opacity: 0, transform: "scale(0.4)" }, { opacity: 1, transform: "none" }],
          { duration: 420, delay: 700 + k * 18, easing: "cubic-bezier(0.34, 1.56, 0.64, 1)", fill: "backwards" });
      });
    }
  }

  function legend(el, items) {
    el.replaceChildren(...items.map(([cls, label, dash]) => h("span", { class: "lab-legend-item" },
      h("span", { class: `lab-key ${cls}${dash ? " dash" : ""}` }), label)));
  }

  function renderCharts() {
    const rows = (state.hist && state.hist.rows) || [];
    const xs = rows.map((r) => r.epoch);
    const pctv = (v) => (v === null || v === undefined ? null : v * 100);
    legend($("lab-legend-score"), [["s1", "Ujian (data yang tidak dilatih)"], ["s2", "Latih", true], ["best", "Rekor baru"]]);
    lineChart($("lab-chart-score"), {
      xs, fmt: (v) => `${v.toFixed(1)}%`, ref: 100, refLabel: "tanpa otak = 100%", minSpan: 0.6,
      series: [
        { name: "Ujian", cls: "s1", label: "ujian", values: rows.map((r) => pctv(r.score)), labelDy: -6 },
        { name: "Latih", cls: "s2", label: "latih", dash: true, values: rows.map((r) => pctv(r.train_ratio)), labelDy: 12 },
      ],
      marks: rows.filter((r) => r.best && r.score).map((r) => ({ x: r.epoch, y: r.score * 100, cls: "best" })),
      tipRows: (i) => [[null, `Epoch ${fmtInt(rows[i].epoch)}`, rows[i].at || ""],
        ["s1", fmtPct(rows[i].score), "ujian"], ["s2 dash", fmtPct(rows[i].train_ratio), "latih"],
        [null, `${fmtNum(rows[i].gain_avg_points, 3)} poin`, rows[i].best ? "rekor baru ★" : "selisih kedekatan rata-rata"]],
      emptyText: state.lab && state.lab.has_model ? "Log epoch kosong untuk ukuran ini." : "Belum ada epoch: tekan Mulai latih.",
      animate: state.drawLines,
    });
    lineChart($("lab-chart-lr"), {
      xs, fmt: (v) => fmtLr(v), series: [{ name: "Learning rate", cls: "s1", values: rows.map((r) => r.lr) }], animate: state.drawLines,
      tipRows: (i) => [[null, `Epoch ${fmtInt(rows[i].epoch)}`, ""], ["s1", fmtLr(rows[i].lr), "learning rate"]],
    });
    lineChart($("lab-chart-sec"), {
      xs, fmt: (v) => `${v.toFixed(0)}d`, series: [{ name: "Detik per epoch", cls: "s1", values: rows.map((r) => r.seconds) }], animate: state.drawLines,
      tipRows: (i) => [[null, `Epoch ${fmtInt(rows[i].epoch)}`, ""], ["s1", `${fmtNum(rows[i].seconds, 1)} detik`, "per epoch"]],
    });
    if (rows.length) state.drawLines = false;
    const last = rows.slice(-30).reverse();
    $("lab-table-score").replaceChildren(last.length ? h("table", null,
      h("thead", null, h("tr", null, ["Epoch", "Ujian", "Latih", "Kedekatan", "Selisih", "LR", "Detik", "Waktu"].map((x) => h("th", null, x)))),
      h("tbody", null, last.map((r) => h("tr", { class: r.best ? "lab-best-row" : "" },
        h("td", null, `${fmtInt(r.epoch)}${r.best ? " ★" : ""}`), h("td", null, fmtPct(r.score)), h("td", null, fmtPct(r.train_ratio)),
        h("td", null, `${fmtNum(r.closeness_avg, 2)}%`), h("td", { class: r.gain_avg_points > 0 ? "pos" : r.gain_avg_points < 0 ? "neg" : "" }, fmtNum(r.gain_avg_points, 3)),
        h("td", null, fmtLr(r.lr)), h("td", null, fmtNum(r.seconds, 1)), h("td", null, (r.at || "").slice(5, 16)))))) : h("p", { class: "hint" }, "Belum ada epoch."));
  }

  /* Bars around zero, one per candle ahead: above zero the model lands closer than the brainless guess. */
  function renderCandles() {
    const lab = state.lab;
    const rows = (state.examPick === "last" ? lab.last_exam : lab.best_exam && lab.best_exam.per_candle) || [];
    const el = $("lab-chart-candle");
    document.querySelectorAll("#lab-exam-pick button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.examPick));
    if (!rows.length) {
      el.replaceChildren(h("p", { class: "lab-empty" }, "Belum ada ujian."));
      $("lab-table-candle").replaceChildren();
      return;
    }
    const width = Math.max(240, el.clientWidth || 360), height = Math.max(150, el.clientHeight || 220);
    const pad = { l: 44, r: 10, t: 14, b: 26 };
    const vals = rows.map((r) => r.gain_points);
    const span = Math.max(0.05, ...vals.map(Math.abs)) * 1.15;
    const Y = (v) => pad.t + (1 - (v + span) / (2 * span)) * (height - pad.t - pad.b);
    const bw = (width - pad.l - pad.r) / rows.length;
    const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, class: "lab-svg", role: "img", "aria-label": "Selisih kedekatan per candle" });
    const grid = svg("g", { class: "lab-grid-lines" });
    for (const t of niceTicks(-span, span, 4)) {
      grid.append(svg("line", { x1: pad.l, x2: width - pad.r, y1: Y(t), y2: Y(t) }),
        svg("text", { x: pad.l - 8, y: Y(t) + 4, "text-anchor": "end", class: "lab-tick" }, document.createTextNode(`${t > 0 ? "+" : ""}${t}`)));
    }
    root.append(grid, svg("line", { x1: pad.l, x2: width - pad.r, y1: Y(0), y2: Y(0), class: "lab-zero" }));
    rows.forEach((r, i) => {
      const x = pad.l + i * bw;
      const v = r.gain_points;
      const top = Math.min(Y(v), Y(0)), hgt = Math.max(1.5, Math.abs(Y(v) - Y(0)));
      const g = svg("g", { class: "lab-bar-g", tabindex: "0", role: "img", "aria-label": `Candle +${r.candle}: ${v} poin` });
      const w = Math.max(4, bw - 6);
      const rx = Math.min(4, w / 2, hgt / 2);
      g.append(svg("rect", { x: x, y: pad.t, width: bw, height: height - pad.t - pad.b, class: "lab-bar-hit" }),
        svg("rect", { x: x + (bw - w) / 2, y: top, width: w, height: hgt, rx, class: `lab-bar ${v >= 0 ? "up" : "down"}` }),
        svg("text", { x: x + bw / 2, y: height - 8, "text-anchor": "middle", class: "lab-tick" }, document.createTextNode(`+${r.candle}`)));
      const tipFor = (cx, cy) => showTip(cx, cy, [[null, `Candle +${r.candle}`, `${r.candle * 5} menit ke depan`],
        [v >= 0 ? "s1" : "s2", `${v > 0 ? "+" : ""}${fmtNum(v, 3)} poin`, v >= 0 ? "lebih dekat" : "lebih jauh"],
        [null, `${fmtNum(r.closeness, 1)}%`, `kedekatan (tanpa otak ${fmtNum(r.closeness_no_brain, 1)}%)`],
        [null, `$${fmtNum(r.error_usd_close, 2)}`, "error close rata-rata"],
        [null, `${fmtNum(r.direction_hit, 1)}%`, `arah benar (mayoritas ${fmtNum(r.direction_majority, 1)}%)`],
        [null, `${fmtNum(r.band_80_hit, 1)}%`, "masuk pita 80% (jujur ≈ 80)"]]);
      g.addEventListener("pointermove", (e) => tipFor(e.clientX, e.clientY));
      g.addEventListener("pointerleave", hideTip);
      g.addEventListener("focus", () => { const b = g.getBoundingClientRect(); tipFor(b.left + b.width / 2, b.top + 20); });
      g.addEventListener("blur", hideTip);
      root.append(g);
    });
    el.replaceChildren(root);
    if (state.drawBars && !P.calm()) {
      root.querySelectorAll(".lab-bar").forEach((bar, i) => {
        bar.style.transformOrigin = bar.classList.contains("up") ? "50% 100%" : "50% 0%";
        bar.style.transformBox = "fill-box";
        bar.animate([{ transform: "scaleY(0)" }, { transform: "scaleY(1)" }],
          { duration: 700, delay: 120 + i * 45, easing: "cubic-bezier(0.34, 1.4, 0.64, 1)", fill: "backwards" });
      });
    }
    state.drawBars = false;
    $("lab-table-candle").replaceChildren(h("table", null,
      h("thead", null, h("tr", null, ["Candle", "Dekat", "Tanpa otak", "Selisih", "Error $", "Arah", "Mayoritas", "Pita 80%"].map((x) => h("th", null, x)))),
      h("tbody", null, rows.map((c) => h("tr", null,
        h("td", null, `+${c.candle}`), h("td", null, `${fmtNum(c.closeness, 1)}%`), h("td", null, `${fmtNum(c.closeness_no_brain, 1)}%`),
        h("td", { class: c.gain_points > 0 ? "pos" : c.gain_points < 0 ? "neg" : "" }, fmtNum(c.gain_points, 3)),
        h("td", null, fmtNum(c.error_usd_close, 2)), h("td", null, `${fmtNum(c.direction_hit, 1)}%`),
        h("td", null, `${fmtNum(c.direction_majority, 1)}%`), h("td", null, `${fmtNum(c.band_80_hit, 1)}%`))))));
  }

  // ── settings form ─────────────────────────────────────────────────────────
  function groupOf(lab, key) {
    for (const g of ["live", "restart", "fresh", "run"]) if (lab.keys[g].includes(key)) return g;
    return "run";
  }
  function shown(lab, key) {
    if (key in lab.saved) return lab.saved[key];
    if (key in lab.config) return lab.config[key];
    if (key === "compile") return null;
    return 0;
  }
  function fmtField(v) {
    if (v === null || v === undefined) return "";
    if (typeof v === "number" && !Number.isInteger(v)) return String(+v.toPrecision(6));
    return String(v);
  }

  function renderForm(lab) {
    const form = $("lab-form");
    const blocks = GROUPS.map(([g, title, sub]) => {
      const keys = lab.keys[g];
      return h("fieldset", { class: `lab-group g-${g}` }, h("legend", null, title, h("small", null, sub)),
        h("div", { class: "lab-fields" }, keys.map((key) => {
          const [label, help, kind] = FIELDS[key] || [key, "", "any"];
          const id = `lab-f-${key}`;
          const value = shown(lab, key);
          const lim = lab.limits[key];
          let input;
          if (kind === "select") {
            input = h("select", { id, name: key },
              h("option", { value: "" }, "otomatis"), h("option", { value: "true" }, "ya"), h("option", { value: "false" }, "tidak"));
            input.value = value === true ? "true" : value === false ? "false" : "";
          } else {
            input = h("input", { id, name: key, type: "number", inputmode: "decimal", step: kind, min: lim ? lim[0] : null, max: lim ? lim[1] : null,
              value: fmtField(value), "aria-describedby": `${id}-help` });
            if (key === "threads") input.placeholder = String(lab.default_threads);
          }
          input.addEventListener("input", () => { state.dirty = true; markFormStatus(state.lab); });
          input.addEventListener("change", () => { state.dirty = true; markFormStatus(state.lab); });
          const def = key in lab.default ? lab.default[key] : null;
          return h("div", { class: "lab-field", "data-key": key },
            h("label", { for: id }, label, h("span", { class: "lab-badge", "data-badge": key })),
            input,
            h("small", { id: `${id}-help` }, help, def !== null && def !== undefined ? ` Default ${fmtField(def)}.` : ""));
        })));
    });
    form.replaceChildren(...blocks);
    markFormStatus(lab);
  }

  function formValues() {
    const out = {};
    for (const el of $("lab-form").querySelectorAll("input, select")) {
      if (el.name === "compile") out.compile = el.value === "" ? null : el.value === "true";
      else if (el.value.trim() !== "") out[el.name] = Number(el.value);
    }
    return out;
  }

  /* Each field says whether the running trainer uses its value (in use / waits for the next epoch / next start). */
  function markFormStatus(lab) {
    if (!lab) return;
    const vals = formValues();
    for (const badge of $("lab-form").querySelectorAll("[data-badge]")) {
      const key = badge.dataset.badge;
      const g = groupOf(lab, key);
      const now = lab.config[key];
      const v = vals[key];
      let text = "", cls = "";
      if (g === "fresh" && v !== undefined && now !== undefined && v !== now) { text = "dari nol"; cls = "fresh"; }
      else if (lab.running && g === "live" && v !== undefined && now !== undefined && Math.abs(v - now) > 1e-12) {
        text = state.dirty ? "belum disimpan" : "epoch berikut"; cls = "pending";
      } else if (lab.running && g === "live") { text = "dipakai"; cls = "live"; }
      badge.textContent = text;
      badge.className = `lab-badge ${cls}`;
    }
  }

  async function saveSettings() {
    const msg = $("lab-form-msg");
    msg.className = "lab-form-msg";
    try {
      const r = await T.api("/api/quant/candle/settings", { method: "POST", body: { symbol: sym(), size: state.lab.size, values: formValues() } });
      state.dirty = false;
      const parts = [];
      if (r.now.length) parts.push(`${r.now.length} langsung dipakai setelah epoch ini`);
      if (r.next_start.length) parts.push(`${r.next_start.length} saat mulai latih`);
      if (r.fresh_only.length) parts.push("bentuk jaringan hanya lewat Mulai dari nol");
      msg.textContent = `Tersimpan. ${parts.join(" · ")}.`;
      msg.classList.add("ok");
      loadLab();
    } catch (e) {
      msg.textContent = e.message;
      msg.classList.add("err");
    }
  }

  function fillDefaults() {
    const lab = state.lab;
    for (const el of $("lab-form").querySelectorAll("input, select")) {
      if (el.name === "compile") el.value = "";
      else if (el.name in lab.default) el.value = fmtField(lab.default[el.name]);
      else el.value = "0";
    }
    state.dirty = true;
    markFormStatus(lab);
    $("lab-form-msg").textContent = "Default terisi. Tekan Simpan setelan untuk memakainya.";
    $("lab-form-msg").className = "lab-form-msg";
  }

  // ── log ───────────────────────────────────────────────────────────────────
  function renderLog() {
    const pre = $("lab-log");
    const keyOnly = $("lab-log-key").checked;
    const atEnd = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
    const lines = keyOnly ? state.log.filter((l) => /★|setelan|berhenti|tersimpan|mentok|kembali|gagal|error|Traceback|data baru|parameter/i.test(l)) : state.log;
    const fresh = Math.max(0, state.log.length - state.logCount);
    state.logCount = state.log.length;
    pre.replaceChildren(...lines.map((line, i) => {
      const cls = /Traceback|Error|gagal/i.test(line) ? "err" : /★/.test(line) ? "best" : /setelan|panel/i.test(line) ? "info"
        : /mentok|kembali|berhenti/i.test(line) ? "warn" : "";
      const isNew = !keyOnly && fresh && fresh < lines.length && i >= lines.length - fresh;
      return h("span", { class: `lab-line-${cls || "plain"}${isNew ? " lab-new" : ""}` }, line + "\n");
    }));
    if (!lines.length) pre.textContent = "Log kosong.";
    if (atEnd) pre.scrollTop = pre.scrollHeight;
  }

  // ── actions ───────────────────────────────────────────────────────────────
  async function toggle() {
    const lab = state.lab;
    if (!lab) return;
    try {
      if (lab.running) {
        const r = await T.api("/api/quant/candle/train", { method: "POST", body: { action: "stop", size: lab.size, symbol: sym() } });
        note(r.still_running && r.still_running.length ? "Trainer belum berhenti (masih menyimpan). Coba lagi sebentar." : "Training berhenti; versi terbaik tersimpan.", "");
      } else {
        if (state.dirty) await saveSettings();
        const r = await T.api("/api/quant/candle/train", { method: "POST", body: { action: "start", size: lab.size, symbol: sym(), fresh: false } });
        note(`Training jalan di latar (PID ${r.pid}). Epoch pertama butuh beberapa detik.`, "");
      }
    } catch (e) { note(e.message, "warn"); }
    setTimeout(refreshAll, 1200);
  }

  async function fresh() {
    const lab = state.lab;
    if (!lab || lab.running) return;
    if (lab.has_model && !confirm(`Mulai ${lab.size} dari nol? Model ${lab.size} sekarang (best.pt, log, status) dihapus, lalu belajar dari awal dengan setelan di form.`)) return;
    try {
      await saveSettings();
      const r = await T.api("/api/quant/candle/train", { method: "POST", body: { action: "start", size: lab.size, symbol: sym(), fresh: true } });
      note(`Mulai dari nol (PID ${r.pid}).`, "");
    } catch (e) { note(e.message, "warn"); }
    setTimeout(refreshAll, 1500);
  }

  async function control(cmd) {
    try {
      await T.api("/api/quant/candle/control", { method: "POST", body: { symbol: sym(), size: state.lab.size, cmd } });
      note(cmd === "reset_lr" ? "Dikirim: learning rate kembali penuh setelah epoch ini." : "Dikirim: best.pt ditulis setelah epoch ini.", "");
    } catch (e) { note(e.message, "warn"); }
  }

  // ── wiring ────────────────────────────────────────────────────────────────
  $("labbtn").onclick = open;
  $("cl-lab").onclick = open;
  $("lab-back").onclick = close;
  $("lab-toggle").onclick = toggle;
  $("lab-fresh").onclick = fresh;
  $("lab-reset").onclick = () => control("reset_lr");
  $("lab-save").onclick = () => control("save");
  $("lab-apply").onclick = saveSettings;
  $("lab-defaults").onclick = fillDefaults;
  $("lab-form").addEventListener("submit", (e) => { e.preventDefault(); saveSettings(); });
  $("lab-log-key").onchange = renderLog;
  $("lab-exam-pick").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-v]");
    if (!b) return;
    state.examPick = b.dataset.v;
    state.drawBars = true;
    if (state.lab) renderCandles();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.open && !document.querySelector(".sq-palette-menu")) close();
  });
  window.addEventListener("hashchange", () => (location.hash === "#lab" ? open() : close()));
  document.addEventListener("visibilitychange", () => { if (state.open && !document.hidden) refreshAll(); });
  let resizeTimer = 0;
  new ResizeObserver(() => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (state.open && state.lab) { renderCharts(); renderCandles(); } }, 120);
  }).observe($("lab"));
  window.addEventListener("sq-palette", () => { /* charts use CSS classes: nothing to redraw */ });

  // The header dot says a trainer is running even with the lab closed.
  async function peek() {
    try {
      const st = await T.api(`/api/quant/candle/status?symbol=${encodeURIComponent(sym())}`);
      setHeaderDot(st.running);
    } catch { /* an old server: no dot */ }
    setTimeout(peek, 30000);
  }
  peek();
  if (location.hash === "#lab") open();
})();
