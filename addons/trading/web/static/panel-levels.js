/* Level, waktu & siklus: the trader's checklist the LLM reads, shown and drawn.

   One call (/api/quant/considerations) gives the whole picture for the symbol and the Analyst's
   timeframe. The chart gets the zones (bands where two or more kinds of level meet, faint lines
   for a level alone) and the session strips along its top. The pane lists the clocks and what
   comes next, the nearest zones with their distance in ATR, the Fibonacci leg and the daily
   pivot, a 24-hour strip of how wide each New York hour's bars run (coloured by direction only
   where the direction passed its test), the cycle with its verdict, and a risk calculator.

   Nothing here is a signal by itself: what did not pass its test is said, not hidden. */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;
  const LEVELS_KEY = "trade:levels";
  let timer = 0;
  let view = null;
  let riskTimer = 0;

  const tf = () => ($("an-tf") ? $("an-tf").value : "H1");
  const sign = (v, d = 2) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}`);
  const pct = (v, d = 0) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(d)}%`);
  const mins = (m) => (m >= 90 ? `${Math.floor(m / 60)} j ${m % 60} mnt` : `${m} mnt`);
  const SESSION_NAMES = { sydney: "Sydney", tokyo: "Tokyo", london: "London", newyork: "New York" };

  async function load() {
    clearTimeout(timer);
    if (!S.symbol) return;
    const asked = `${S.symbol}|${tf()}`;
    let r;
    try {
      r = await T.api(`/api/quant/considerations?symbol=${encodeURIComponent(S.symbol)}&timeframe=${tf()}`);
    } catch (e) {
      $("ck-pill").textContent = "–";
      $("ck-clock").replaceChildren(h("p", { class: "note-warn" }, /Not Found/i.test(e.message)
        ? "Server trading belum punya fitur ini: restart server trading." : e.message));
      draw(null);
      timer = setTimeout(load, 60000);
      return;
    }
    if (asked !== `${S.symbol}|${tf()}`) return;     // the symbol or timeframe changed while this was loading
    view = r;
    render(r);
    draw(r);
    timer = setTimeout(load, 60000);
  }

  // ── the chart ───────────────────────────────────────────────────────────
  function draw(r) {
    if (!T.chart) return;
    const lv = r && r.levels && r.levels.ok ? r.levels : null;
    T.chart.setZones(lv ? (lv.zones || []).map((z) => ({
      low: z.low, high: z.high, price: z.price, strength: z.strength,
      label: z.names.slice(0, 2).join(" + ") + (z.names.length > 2 ? ` +${z.names.length - 2}` : ""),
    })) : []);
    T.chart.setSessions(r ? r.session_bands || [] : []);
  }

  // ── the pane ────────────────────────────────────────────────────────────
  function render(r) {
    const clock = r.clock || {};
    const pill = $("ck-pill");
    pill.textContent = clock.phase_label || "–";
    pill.className = "pill " + (clock.phase === "rollover" ? "warn" : clock.phase === "weekend" || clock.phase === "off" ? "off" : "demo");
    renderClock(clock);
    renderZones(r.levels || {}, r.digits);
    renderFib(r.levels || {}, r.fib_time);
    renderSeason(r.seasonality || {}, clock);
    renderCycle(r.cycle || {});
  }

  function renderClock(c) {
    const cell = (city, value) => h("span", { class: "ckcity" }, h("small", null, city), h("b", null, (value || "–").split(" ").pop()));
    const parts = [h("div", { class: "ckcities" }, cell("New York", c.new_york), cell("London", c.london), cell("Tokyo", c.tokyo),
      cell("Sydney", c.sydney), cell("WIB", c.wib), cell("UTC", c.utc))];
    const now = (c.window_labels || []).length ? `Sekarang: ${c.window_labels.join(", ")}. ` : "";
    const next = (c.next || []).slice(0, 3).map((e) => `${e.label} ${mins(e.in_minutes)} lagi`).join(" · ");
    parts.push(h("div", { class: c.phase === "rollover" ? "note-warn" : "hint" }, `${c.weekday || ""} · sesi ${c.session || "–"}. ${now}${next}`));
    if (c.dst_note) parts.push(h("div", { class: "note-warn" }, c.dst_note));
    const extra = [`Rollover ${mins(c.minutes_to_rollover || 0)} lagi`, c.month_end ? "akhir bulan (arus rebalancing)" : "",
      c.moon ? `bulan: ${c.moon.name}` : ""].filter(Boolean).join(" · ");
    parts.push(h("div", { class: "hint" }, extra));
    parts.push(h("div", { class: "cklegend" }, Object.entries(SESSION_NAMES).map(([key, name]) =>
      h("span", null, h("i", { class: `ckdot ${key}` }), name)), h("span", { class: "hint" }, "pita di atas chart")));
    $("ck-clock").replaceChildren(...parts);
  }

  function renderZones(lv, digits) {
    const box = $("ck-zones");
    if (!lv.ok) { box.replaceChildren(h("p", { class: "hint" }, lv.reason || "Belum cukup bar untuk menghitung level.")); return; }
    const rows = [...(lv.zones_above || []).slice(0, 4).reverse(), ...(lv.at_zone ? [{ ...lv.at_zone, here: true }] : []),
      ...(lv.zones_below || []).slice(0, 4)];
    const dots = (n) => h("span", { class: `ckstrength s${n}`, title: `${n} dari 4 jenis level bertemu di sini` }, "●".repeat(n) + "○".repeat(4 - n));
    const body = [];
    let priceRow = !lv.at_zone;
    for (const z of rows) {
      if (priceRow && z.dist_atr < 0) {
        body.push(h("tr", { class: "ckprice" }, h("td", null, T.fmtPx(lv.price, digits)), h("td", { class: "num" }, "harga"), h("td", null, ""), h("td", null, "")));
        priceRow = false;
      }
      body.push(h("tr", { class: z.here ? "ckprice" : z.strength >= 2 ? "ckstrong" : "" },
        h("td", null, T.fmtPx(z.price, digits)), h("td", { class: "num" }, z.here ? "di sini" : `${sign(z.dist_atr, 1)} ATR`),
        h("td", null, dots(z.strength)), h("td", { class: "cknames", title: z.names.join(", ") }, z.names.join(", "))));
    }
    if (priceRow) body.push(h("tr", { class: "ckprice" }, h("td", null, T.fmtPx(lv.price, digits)), h("td", { class: "num" }, "harga"), h("td", null, ""), h("td", null, "")));
    const room = [lv.room_up_atr !== null && lv.room_up_atr !== undefined ? `ruang naik ke zona kuat ${lv.room_up_atr} ATR` : "tidak ada zona kuat di atas",
      lv.room_down_atr !== null && lv.room_down_atr !== undefined ? `ruang turun ${lv.room_down_atr} ATR` : "tidak ada zona kuat di bawah"].join(" · ");
    box.replaceChildren(
      h("table", { class: "simrank cktable" },
        h("thead", null, h("tr", null, ["Zona", "Jarak", "Kuat", "Isi"].map((t, i) => h("th", { class: i === 1 ? "num" : null }, t)))),
        h("tbody", null, body)),
      h("div", { class: "hint" }, `ATR ${T.fmtPx(lv.atr, digits)} · ${room}. Zona kuat = 2 jenis level atau lebih (pivot, Fibonacci, range kemarin/minggu lalu, angka bulat).`));
  }

  function renderFib(lv, fibTime) {
    const parts = [];
    if (lv.fib) {
      const f = lv.fib;
      parts.push(h("div", null, h("b", null, "Fibonacci "), `leg ${f.leg} ${f.from} → ${f.to} (${f.leg_atr} ATR), terkoreksi ${pct(f.retraced, 1)}: ${f.read}.`));
      parts.push(h("div", { class: "hint" }, `Target ekstensi: ${Object.entries(f.extensions).map(([k, v]) => `${k} ${v}`).join(" · ")}`));
    }
    if (fibTime) {
      parts.push(h("div", { class: "hint" }, `Waktu Fibonacci: ${fibTime.bars_since_swing} bar sejak swing terakhir` +
        (fibTime.on_count ? ` (tepat di hitungan ${fibTime.nearest_fibonacci})` : fibTime.next_count ? `, hitungan berikutnya ${fibTime.next_count}` : "") +
        ". Hitungan ini tidak diuji sendiri; otak menilainya lewat umur swing."));
    }
    if (lv.pivot) {
      const p = lv.pivot, c = p.classic;
      parts.push(h("div", null, h("b", null, "Pivot "), `P ${c.P} · R1 ${c.R1} · S1 ${c.S1} · R2 ${c.R2} · S2 ${c.S2} (harga ${p.side_of_pivot}).`));
      parts.push(h("div", { class: "hint" }, `Camarilla R3 ${p.camarilla.R3} · S3 ${p.camarilla.S3} · posisi ${sign(p.camarilla_place)} (±1 = R3/S3, ±2 = R4/S4). ` +
        `Dari high ${p.previous.high}, low ${p.previous.low}, close ${p.previous.close} hari trading sebelumnya, batas hari ${p.day_boundary}.`));
    }
    $("ck-fib").replaceChildren(...parts);
  }

  function renderSeason(s, clock) {
    const box = $("ck-season");
    if (!s.ok) { box.replaceChildren(h("p", { class: "hint" }, s.reason || "")); return; }
    const hours = new Map((s.hours || []).map((r) => [r.key, r]));
    const widest = Math.max(1, ...(s.hours || []).map((r) => r.width_ratio || 0));
    const nowKey = (s.next_hour || {}).key;
    const cells = [];
    for (let k = 0; k < 24; k++) {
      const r = hours.get(k);
      const height = r && r.width_ratio ? Math.max(8, Math.round(r.width_ratio / widest * 100)) : 4;
      const cls = !r ? "none" : r.significant ? (r.avg_move_atr > 0 ? "up" : "down") : "flat";
      const title = r ? `${r.label}: bar ${r.width_ratio}× rata-rata lebar, gerak rata-rata ${sign(r.avg_move_atr, 3)} ATR dari ${r.bars} bar` +
        (r.t_stat !== null ? `, t ${r.t_stat}, q ${r.q_value}${r.significant ? " (lolos uji)" : " (tidak lolos uji)"}` : "") : `${String(k).padStart(2, "0")}:00 NY: belum ada data`;
      cells.push(h("span", { class: `ckhour ${cls}${k === nowKey ? " now" : ""}`, title }, h("i", { style: `height:${height}%` })));
    }
    const next = s.next_hour || {};
    box.replaceChildren(
      h("div", null, h("b", null, "Kebiasaan tiap jam "), h("span", { class: "hint" }, `(jam New York, ${s.days} hari data)`)),
      h("div", { class: "ckhours" }, cells),
      h("div", { class: "ckaxis" }, ["00", "06", "12", "18", "23"].map((t) => h("span", null, t))),
      h("div", { class: "hint" }, next.bars ? `Jam ini (${next.label}): bar ${next.width_ratio}× lebar rata-rata, arah ${sign(next.avg_move_atr, 3)} ATR` +
        `${next.significant ? " (lolos uji)" : " (tidak lolos uji, jangan dipakai)"}.` : "Jam ini belum punya cukup data."),
      h("div", { class: s.significant && s.significant.length ? "pos" : "hint" }, `Tinggi batang = lebar bar (volatilitas). ${s.verdict}.`));
  }

  function renderCycle(c) {
    const box = $("ck-cycle");
    if (!c.ok) { box.replaceChildren(h("div", { class: "hint" }, `Siklus: ${c.reason || "belum cukup bar"}`)); return; }
    box.replaceChildren(
      h("div", null, h("b", null, "Siklus "), h("span", { class: c.significant ? "pos" : "hint" }, c.verdict)),
      h("div", { class: "hint" }, c.significant
        ? `Periode ${c.period_bars} bar, sedang ${c.direction} · puncak ${c.bars_to_peak} bar lagi, lembah ${c.bars_to_trough} bar lagi.`
        : `Periode terkuat ${c.period_bars} bar memegang ${pct(c.power_share)} tenaga spektrum; tanpa siklus pun rata-rata ${pct(c.expected_share_if_none)}. ` +
          `Uji Fisher p ${c.p_fisher}, uji acak-ulang p ${c.p_permutation}.`));
  }

  // ── the risk calculator ─────────────────────────────────────────────────
  async function risk() {
    const win = Number($("rk-win").value) / 100, rr = Number($("rk-rr").value);
    const riskPct = Number($("rk-risk").value), n = Math.round(Number($("rk-n").value));
    if (!(win > 0 && win < 1) || !(rr > 0) || !(riskPct > 0) || !(n > 0)) { $("rk-note").textContent = "Isi keempat angka."; return; }
    try {
      const r = await T.api(`/api/quant/riskmath?win_rate=${win}&rr=${rr}&risk_pct=${riskPct}&trades=${n}`);
      const row = (k, v, cls) => [h("div", null, k), h("b", { class: cls || "" }, v)];
      const streaks = r.losing_streak_probability;
      $("rk-out").replaceChildren(...[
        row("Win rate impas", pct(r.breakeven_win_rate, 1)),
        row("Keunggulan di atas impas", `${sign(r.edge * 100, 1)} poin`, r.edge > 0 ? "pos" : "neg"),
        row("Expectancy per trade", `${sign(r.expectancy_r, 2)} R`, r.expectancy_r > 0 ? "pos" : "neg"),
        row("Kelly penuh / setengah", r.kelly > 0 ? `${pct(r.kelly, 1)} / ${pct(r.half_kelly, 1)}` : "0 (tidak ada keunggulan)"),
        row(`Rugi beruntun 5× dalam ${n} trade`, pct(streaks["5"])),
        row(`Rugi beruntun 8× dalam ${n} trade`, pct(streaks["8"])),
        row("Rugi beruntun terpanjang (kira-kira)", `${r.typical_longest_losing_streak}× = turun ${r.drawdown_of_typical_streak_pct}%`),
        row("Peluang pernah turun 30%", pct(r.risk_of_ruin, 1), r.risk_of_ruin > 0.05 ? "neg" : ""),
      ].flat());
      $("rk-note").textContent = `${r.note} Turun 30% butuh naik ${r.recovery_needed_pct}% untuk kembali.` +
        (riskPct > 2 ? " Risiko di atas 2% per trade membuat rugi beruntun biasa saja sudah dalam." : "");
    } catch (e) { $("rk-note").textContent = e.message; }
  }

  function scheduleRisk() {
    clearTimeout(riskTimer);
    riskTimer = setTimeout(risk, 300);
  }

  // ── wiring ──────────────────────────────────────────────────────────────
  function applyToggle(on) {
    $("z-levels").classList.toggle("on", on);
    if (T.chart) T.chart.toggleLevels(on);
  }

  let levelsOn = true;
  try { levelsOn = localStorage.getItem(LEVELS_KEY) !== "0"; } catch { /* the default stays */ }
  $("z-levels").onclick = () => {
    levelsOn = !levelsOn;
    try { localStorage.setItem(LEVELS_KEY, levelsOn ? "1" : "0"); } catch { /* not remembered */ }
    applyToggle(levelsOn);
  };
  for (const id of ["rk-win", "rk-rr", "rk-risk", "rk-n"]) $(id).addEventListener("input", scheduleRisk);
  $("ck-risk-box").addEventListener("toggle", () => { if ($("ck-risk-box").open) risk(); });
  $("checklist-pane").addEventListener("toggle", () => { if ($("checklist-pane").open) load(); });
  if ($("an-tf")) $("an-tf").addEventListener("change", load);

  T.levelsSymbolChanged = () => {
    view = null;
    draw(null);
    applyToggle(levelsOn);
    load();
  };
  T.levelsView = () => view;
})();
