/* Paper simulation on the live price: every rule opens a virtual position, holds it, closes it, and
   again, until one wins often enough. Nothing here sends an order. The chosen rule's trades are
   drawn on the chart: a BUY or SELL badge at the entry, a dashed path, and a dot for the result.
   Also here: the trading LLM's settings (shared with the forward test), the math behind the
   "matematika" rule, each rule's record by session, and the last cycles' numbers. */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;
  const pct = (v) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(0)}%`);
  let report = null;
  let chosen = "";        // the rule whose trades are on the chart; "" follows the leader
  let timer = 0;

  function settings() {
    const clamp = (id, lo, hi, fallback) => {
      const n = Number($(id).value);
      return Math.max(lo, Math.min(hi, Number.isFinite(n) && $(id).value !== "" ? n : fallback));
    };
    const style = $("sim-style").value;
    const common = {
      symbol: S.symbol,
      timeframe: style,
      target: clamp("sim-target", 51, 95, 60) / 100,
      min_trades: Math.round(clamp("sim-min", 5, 1000, 20)),
      llm: $("llm-on").checked,
      llm_model: $("llm-model").value.trim() || null,
      llm_max_calls: Math.round(clamp("llm-max", 1, 2000, 120)),
    };
    if (!style) return { ...common, hold_s: clamp("sim-hold", 5, 600, 30), max_minutes: clamp("sim-max", 1, 240, 60) };
    return {
      ...common,
      hold_bars: Math.round(clamp("sim-bars", 1, 48, 4)),
      brain: $("sim-brain").checked,
      candle: $("sim-candle").checked,
      stop_atr: clamp("sim-stop-atr", 0, 10, 1.5),
      target_atr: clamp("sim-target-atr", 0, 10, 2),
      trail_atr: clamp("sim-trail-atr", 0, 5, 0),
      lock_atr: clamp("sim-lock-atr", 0, 2, 0.3),
      max_minutes: clamp("sim-hours", 1, 168, 24) * 60,
    };
  }

  /* Scalping holds seconds, where the spread is often half a bar and the LLM rightly waits.
     The intraday style decides once per closed M15/H1/H4 bar and closes at a stop, a target or the hold. */
  const STYLE_KEY = "trade:sim-style";
  function showStyle() {
    const style = $("sim-style").value;
    document.querySelectorAll(".sim-scalp").forEach((el) => { el.hidden = !!style; });
    document.querySelectorAll(".sim-day").forEach((el) => { el.hidden = !style; });
    $("sim-style-note").textContent = style
      ? `Satu keputusan per bar ${style} yang tutup. Posisi ditutup di stop, target, atau setelah tahan maks. Dijalankan di server trading, jadi biarkan servernya hidup.`
      : "Spread sering setengah gerak bar 5 detik, jadi LLM wajar sering menunggu. Pilih Harian kalau mau LLM ikut trade.";
    try { localStorage.setItem(STYLE_KEY, style); } catch { /* storage blocked: the choice just is not remembered */ }
  }
  try { $("sim-style").value = localStorage.getItem(STYLE_KEY) || ""; } catch { /* default: scalping */ }
  $("sim-style").addEventListener("change", showStyle);
  showStyle();

  const duration = (s) => (s >= 5400 ? `${(s / 3600).toFixed(s % 3600 ? 1 : 0)} jam` : s >= 120 ? `${Math.round(s / 60)} mnt` : `${Math.ceil(s)} dtk`);

  /* ── the trading LLM's settings, saved on the server and shared with the forward test ──
     The model list is what the Router-page providers serve right now, by provider, so a model
     a provider dropped (or an old DEFAULT_MODEL) cannot be picked by accident. */
  let served = [];
  let defaultProblem = "";

  function noteModel() {
    const value = $("llm-model").value;
    const note = $("llm-model-note");
    const bad = value ? (served.length && !served.includes(value) ? `${value} tidak dilayani Router: pilih model lain.` : "")
      : defaultProblem;
    note.textContent = bad || `${served.length} model dari Router`;
    note.className = bad ? "note-warn" : "hint";
  }

  async function loadLlm(refresh) {
    let cfg = {};
    try {
      cfg = await T.api("/api/quant/llm");
      $("llm-on").checked = !!cfg.sim_enabled;
      $("llm-max").value = cfg.max_calls;
      $("llm-forward").value = cfg.forward_mode;
    } catch { /* the panel works without it */ }
    const select = $("llm-model");
    try {
      const r = await T.api(`/api/quant/llm/models${refresh ? "?refresh=true" : ""}`);
      served = r.models || [];
      // A trading server started before this panel was updated sends a flat list: group it by prefix here.
      if (!r.groups) {
        const byPrefix = new Map();
        for (const m of served) {
          const prefix = m.includes("/") ? m.slice(0, m.indexOf("/") + 1) : "(tanpa prefix)";
          if (!byPrefix.has(prefix)) byPrefix.set(prefix, []);
          byPrefix.get(prefix).push(m);
        }
        r.groups = [...byPrefix].map(([prefix, models]) => ({ name: prefix, models: models.sort() }));
        r.recent = (r.used || []).filter((m) => served.includes(m));
        if (r.default && !served.includes(r.default)) {
          r.default_problem = `Model default ${r.default} tidak dilayani Router lagi. Pilih model dari daftar.`;
          r.default = "";
        }
      }
      defaultProblem = r.default ? "" : (r.default_problem || "");
      const option = (m) => h("option", { value: m }, m);
      const groups = [h("option", { value: "" }, r.default ? `Default Router: ${r.default}` : "— pilih model dari Router —")];
      if ((r.recent || []).length) groups.push(h("optgroup", { label: "Baru dipakai" }, r.recent.map(option)));
      for (const g of r.groups || []) groups.push(h("optgroup", { label: `${g.name} · ${g.models.length} model` }, g.models.map(option)));
      if (cfg.model && !served.includes(cfg.model)) {
        groups.push(h("optgroup", { label: "Tersimpan, tidak tersedia" }, h("option", { value: cfg.model }, `${cfg.model} (tidak dilayani)`)));
      }
      select.replaceChildren(...groups);
      select.value = cfg.model || "";
      noteModel();
      if ((r.errors || []).length) $("llm-model-note").textContent += ` · gagal: ${r.errors.join("; ")}`;
    } catch (e) {
      $("llm-model-note").textContent = `Daftar model tidak bisa diambil: ${e.message}`;
      $("llm-model-note").className = "note-warn";
    }
  }

  async function saveLlm() {
    try {
      await T.api("/api/quant/llm", { method: "POST", body: {
        model: $("llm-model").value.trim(), sim_enabled: $("llm-on").checked,
        max_calls: Number($("llm-max").value) || 120, forward_mode: $("llm-forward").value,
      } });
    } catch (e) { T.setBanner(e.message); }
  }

  T.startSim = async () => {
    if (!S.symbol) { T.setBanner("Pilih simbol dulu."); return; }
    try {
      chosen = "";
      const body = settings();
      const started = await T.api("/api/quant/sim/start", { method: "POST", body });
      if (body.timeframe && started.timeframe !== body.timeframe) {
        // An old server drops the style and starts a scalping run: stop it and say so.
        await T.api("/api/quant/sim/stop", { method: "POST" }).catch(() => null);
        T.checkServer && T.checkServer();
        throw new Error("Server trading masih menjalankan kode lama, jadi gaya Harian diabaikan dan simulasinya jadi scalping. " +
          "Simulasinya sudah dihentikan. Restart server trading (Ctrl+C lalu python main.py trading), lalu mulai lagi.");
      }
      show(started);
      // Holds of seconds vanish inside a 5-minute candle: show one-minute candles, close up.
      // The intraday style shows the bars it decides on.
      const tf = body.timeframe || "M1";
      if (S.tf !== tf) document.querySelector(`#tfs button[data-tf="${tf}"]`)?.click();
      setTimeout(() => { T.chart.resetView(); if (!body.timeframe) T.chart.zoom(3); }, 600);
      poll(true);
    } catch (e) { T.setBanner(e.message); }
  };

  T.stopSim = async () => {
    for (const id of ["sim-stop", "sim-chip-stop"]) $(id).disabled = true;
    try { show(await T.api("/api/quant/sim/stop", { method: "POST" })); } catch (e) { T.setBanner(e.message); }
    // The run closes its open positions first, then reports "berhenti" on the next poll.
    for (const id of ["sim-stop", "sim-chip-stop"]) $(id).disabled = false;
  };

  async function refresh() {
    try { show(await T.api("/api/quant/sim/status")); } catch { /* the next poll tries again */ }
  }

  function poll(fast) {
    clearInterval(timer);
    timer = setInterval(() => { if (!document.hidden) refresh(); }, fast ? 1000 : 5000);
  }

  function ruleRow(r, leader) {
    const target = report.target;
    const rate = r.win_rate ?? 0;
    const fill = Math.min(100, (rate / target) * 100);
    const on = (chosen || (leader && leader.name)) === r.name;
    return h("tr", { "data-rule": r.name, class: (on ? "on " : "") + (r.name === "acak" ? "coin" : "") },
      h("td", null, r.label),
      h("td", { class: "num" }, r.trades),
      h("td", { class: "num " + (rate >= target && r.trades >= report.min_trades ? "pos" : "") }, pct(r.win_rate),
        h("div", { class: "simmeter", title: `menuju target ${pct(target)}` },
          h("i", { style: `width:${fill}%`, class: rate >= target ? "hit" : "" }))),
      h("td", { class: "num hint", title: "Batas bawah 95%: win rate sebenarnya kemungkinan tidak di bawah ini" }, pct(r.wilson_low)));
  }

  function show(r) {
    report = r;
    const running = r.state === "running";
    $("sim-start").hidden = running;
    $("sim-stop").hidden = !running;
    // Always in sight on the chart, however far the panel is scrolled.
    $("sim-chip").hidden = !running;
    if (running) {
      const minutes = Math.floor((r.elapsed_s || 0) / 60);
      $("sim-chip-text").textContent = `Simulasi live · ${r.symbol} · siklus ${r.cycles} · ${minutes} mnt` +
        (r.llm ? ` · LLM ${r.llm.calls}/${r.llm.max_calls}` : "");
    }
    const pill = $("sim-pill");
    pill.textContent = { running: "berjalan", found: "ketemu", stopped: "berhenti", none: "siap" }[r.state] || r.state;
    pill.className = "pill " + (running ? "demo" : r.state === "found" ? "sim" : "off");
    if (r.state === "none") { $("sim-table").replaceChildren(); T.chart && T.chart.setMarkers([]); return; }

    // the current cycle: how long until the open positions close
    const open = r.open || [];
    $("sim-cycle").hidden = !running || !open.length;
    if (open.length) {
      const left = open[0].left_s;
      $("sim-bar").style.width = `${Math.max(0, Math.min(100, (1 - left / r.hold_s) * 100))}%`;
      $("sim-left").textContent = r.timeframe
        ? `Bar ${r.timeframe} ke-${r.cycles} · ${open.length} posisi virtual · ATR ${r.atr} · paling lambat tutup ${duration(left)} lagi`
        : `Siklus ${r.cycles} · ${open.length} posisi virtual · tutup ${duration(left)} lagi`;
    }

    const found = r.found;
    $("sim-found").hidden = !found;
    if (found) {
      const shareButton = h("button", { class: "btn mini" }, "Bagikan hasil");
      shareButton.onclick = () => T.shareSim && T.shareSim(r);
      $("sim-found").replaceChildren(
        h("b", null, `Ketemu: ${found.label} menang ${pct(found.win_rate)} dari ${found.trades} trade`),
        h("div", { class: "hint" }, `Batas bawah 95% ${pct(found.wilson_low)} · impas di ${pct(found.breakeven_win_rate)} · ${r.symbol}, ${r.timeframe ? `${r.timeframe}, tahan maks. ${duration(r.hold_s)}` : `tahan ${duration(r.hold_s)}`}`),
        r.warning ? h("div", { class: "note-warn" }, r.warning) : "",
        shareButton);
    }

    showLlm(r.llm);
    if (r.analyst_last && T.analystFromSim) T.analystFromSim(r.analyst_last);
    showMath(r.math, r.session);
    showSessions(r);
    showConditions(r);
    showCycles(r);
    $("sim-significance").hidden = !r.significance;
    $("sim-significance").textContent = r.significance || "";

    const leader = r.leader;
    $("sim-table").replaceChildren(h("table", { class: "simrank" },
      h("thead", null, h("tr", null, ["Aturan", "Trade", "Menang", "Bawah"].map((t, i) => h("th", { class: i ? "num" : null }, t)))),
      h("tbody", null, r.ranking.map((row) => ruleRow(row, leader)))));
    const elapsed = Math.round(r.elapsed_s / 60);
    $("sim-note").textContent = [
      `${r.total_trades} trade virtual dalam ${elapsed} menit`,
      r.coin && r.coin.trades ? `lempar koin ${pct(r.coin.win_rate)}` : "",
      r.ended ? `selesai: ${r.ended}` : "",
      r.last_error && r.errors ? `error: ${r.last_error}` : "",
    ].filter(Boolean).join(" · ");
    drawMarkers();
    if (!running) poll(false);
  }

  const usd = (v) => (v ? (v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`) : "$0");
  const sideWord = { buy: "BUY", sell: "SELL", none: "tunggu" };

  function showLlm(llm) {
    const box = $("sim-llm");
    box.hidden = !llm;
    if (!llm) return;
    const last = llm.last;
    box.replaceChildren(
      h("b", null, `LLM · ${llm.model}`),
      h("div", { class: "hint" }, `${llm.calls}/${llm.max_calls} panggilan · ${(llm.input_tokens + llm.output_tokens).toLocaleString("id-ID")} token · ` +
        `${llm.unpriced ? "harga model belum diisi di Router" : `≈ ${usd(llm.cost_usd)}`}` +
        `${llm.pending ? " · sedang berpikir…" : ""}${llm.late_skips ? ` · ${llm.late_skips} jawaban telat dilewati` : ""}`),
      last ? h("div", null, h("span", { class: "pill " + (last.side === "buy" ? "buyp" : last.side === "sell" ? "sellp" : "off") }, sideWord[last.side] || last.side),
        ` ${Math.round(last.confidence * 100)}% · ${last.why || ""}`) : "",
      !llm.available && llm.why_unavailable ? h("div", { class: "note-warn" }, llm.why_unavailable) : "",
      llm.last_error && llm.failures ? h("div", { class: "hint" }, `error terakhir: ${llm.last_error}`) : "");
  }

  function bar(value, cls) {
    // value -1..1, drawn from the middle
    const w = Math.min(50, Math.abs(value) * 50);
    return h("div", { class: "pushbar" }, h("i", { class: cls || (value >= 0 ? "up" : "down"),
      style: `width:${w}%;${value >= 0 ? "left:50%" : `left:${50 - w}%`}` }));
  }

  function showMath(m, session) {
    const box = $("sim-math");
    box.hidden = !m;
    if (!m) return;
    if (!m.ok) { box.replaceChildren(h("b", null, "Matematika gabungan"), h("div", { class: "hint" }, m.reason || "belum cukup data")); return; }
    const up = m.p_up;
    const strongest = Math.max(0.0001, ...m.top.map((c) => Math.abs(c.push)));
    box.replaceChildren(
      h("div", { class: "mathhead" }, h("b", null, "Matematika gabungan"),
        h("span", { class: "pill " + (m.side > 0 ? "buyp" : m.side < 0 ? "sellp" : "off") }, m.side > 0 ? "BUY" : m.side < 0 ? "SELL" : "tidak ambil arah")),
      h("div", { class: "probrow" }, h("span", null, `P(naik) ${(up * 100).toFixed(1)}%`),
        h("div", { class: "probbar" }, h("i", { style: `width:${(up * 100).toFixed(1)}%` })), h("span", null, `kejelasan ${(m.clarity * 100).toFixed(0)}%`)),
      h("div", { class: m.graded ? "hint" : "note-warn" },
        `Uji di ${m.test_samples} bar yang belum dilihat: akurasi ${(m.test_accuracy * 100).toFixed(0)}% vs tebakan mayoritas ${(m.base_rate * 100).toFixed(0)}%. ${m.graded ? "Lulus." : "Tidak lulus, jadi arahnya diabaikan."}`),
      h("table", { class: "simrank" }, h("tbody", null, m.top.map((c) => h("tr", null,
        h("td", null, c.label), h("td", { class: "num" }, c.push > 0 ? "naik" : "turun"), h("td", { class: "pushcell" }, bar(c.push / strongest)))))),
      h("div", { class: "hint" }, `Rezim: Hurst ${m.regime.hurst} (${m.regime.hurst > 0.55 ? "cenderung tren" : m.regime.hurst < 0.45 ? "cenderung balik" : "acak"}) · ` +
        `variance ratio ${m.regime.variance_ratio} · entropi ${m.regime.entropy} · autokorelasi ${m.regime.autocorr}` +
        (session ? ` · sesi ${session.label} (NY ${session.new_york})` : "")));
  }

  function showSessions(r) {
    const box = $("sim-sessions");
    const rule = chosen || (r.leader && r.leader.name) || "ensemble";
    const rows = (r.by_session || []).filter((row) => row.rule === rule);
    box.hidden = !rows.length;
    if (!rows.length) return;
    const label = (r.ranking.find((x) => x.name === rule) || {}).label || rule;
    box.replaceChildren(h("div", { class: "hint" }, `Per sesi · ${label}`), h("table", { class: "simrank" },
      h("thead", null, h("tr", null, ["Sesi", "Trade", "Menang", "Poin"].map((t, i) => h("th", { class: i ? "num" : null }, t)))),
      h("tbody", null, rows.map((row) => h("tr", null, h("td", null, row.label), h("td", { class: "num" }, row.trades),
        h("td", { class: "num" }, pct(row.win_rate)), h("td", { class: "num " + (row.net_points >= 0 ? "pos" : "neg") }, T.fmtPx(row.net_points, (r.digits || 5))))))));
  }

  function showConditions(r) {
    const box = $("sim-conditions");
    const rule = chosen || (r.leader && r.leader.name) || "ensemble";
    const rows = (r.by_condition || []).filter((row) => row.rule === rule && row.trades >= 3);
    box.hidden = !rows.length;
    if (!rows.length) return;
    const label = (r.ranking.find((x) => x.name === rule) || {}).label || rule;
    box.replaceChildren(h("div", { class: "hint" }, `Per kondisi saat masuk · ${label} (klik aturan lain di tabel untuk melihatnya)`), h("table", { class: "simrank" },
      h("thead", null, h("tr", null, ["Kondisi", "Trade", "Menang", "Poin"].map((t, i) => h("th", { class: i ? "num" : null }, t)))),
      h("tbody", null, rows.map((row) => h("tr", null, h("td", { title: `${row.name}: ${row.value}` }, row.name === "waktu" ? `waktu: ${row.value}` : row.value),
        h("td", { class: "num" }, row.trades), h("td", { class: "num", title: `batas bawah 95% ${pct(row.wilson_low)}` }, pct(row.win_rate)),
        h("td", { class: "num " + (row.net_points >= 0 ? "pos" : "neg") }, T.fmtPx(row.net_points, (r.digits || 5))))))));
  }

  function showCycles(r) {
    const box = $("sim-cycles");
    const rows = (r.cycle_log || []).slice(-10).reverse();
    box.hidden = !rows.length;
    if (!rows.length) return;
    const time = (t) => new Date(t * 1000).toLocaleTimeString("id-ID", { hour12: false });
    const vote = (v) => (v > 0 ? "▲" : v < 0 ? "▼" : "·");
    const name = r.file ? r.file.split(/[\\/]/).pop() : "";
    box.replaceChildren(
      h("div", { class: "hint" }, "Perhitungan per siklus ", name ? h("a", { href: `/api/quant/sim/logs/${encodeURIComponent(name)}`, download: name }, "unduh log") : ""),
      h("table", { class: "simrank" },
        h("thead", null, h("tr", null, ["#", "Jam", "Sesi", "P(naik)", "Mat.", "Ens.", "Spread"].map((t, i) => h("th", { class: i > 2 ? "num" : null }, t)))),
        h("tbody", null, rows.map((c) => h("tr", null, h("td", null, c.cycle), h("td", null, time(c.t)), h("td", null, c.session_label),
          h("td", { class: "num" }, c.math && c.math.ok ? `${(c.math.p_up * 100).toFixed(0)}%` : "–"),
          h("td", { class: "num" }, vote(c.votes && c.votes.matematika)), h("td", { class: "num" }, (c.ensemble_score ?? 0).toFixed(2)),
          h("td", { class: "num" }, T.fmtPx(c.spread, (r.digits || 5))))))));
  }

  function drawMarkers() {
    if (!T.chart || !report || report.state === "none") return;
    if (report.symbol !== S.symbol) { T.chart.setMarkers([]); return; }
    // Before any trade has closed there is no leader yet: show the ensemble's positions.
    const firstOpen = (report.open || [])[0];
    const rule = chosen || (report.leader && report.leader.name) || (firstOpen && firstOpen.rule) || "ensemble";
    const done = report.trades.filter((t) => t.rule === rule).slice(-40);
    const open = (report.open || []).filter((p) => p.rule === rule)
      .map((p) => ({ ...p, label: `${p.side === "buy" ? "BUY" : "SELL"} ${Math.ceil(p.left_s)}s` }));
    T.chart.setMarkers([...done, ...open]);
  }

  T.onSimTableClick = (e) => {
    const row = e.target.closest("tr[data-rule]");
    if (!row) return;
    chosen = row.dataset.rule === chosen ? "" : row.dataset.rule;
    if (report) show(report);
  };

  T.simSymbolChanged = drawMarkers;
  T.initSim = () => {
    $("sim-start").onclick = T.startSim;
    $("sim-stop").onclick = T.stopSim;
    $("sim-chip-stop").onclick = T.stopSim;
    $("sim-table").onclick = T.onSimTableClick;
    for (const id of ["llm-on", "llm-model", "llm-max", "llm-forward"]) $(id).addEventListener("change", saveLlm);
    $("llm-model").addEventListener("change", noteModel);
    $("llm-refresh").onclick = () => loadLlm(true);
    loadLlm(false);
    refresh().then(() => poll(report && report.state === "running"));
  };
})();
