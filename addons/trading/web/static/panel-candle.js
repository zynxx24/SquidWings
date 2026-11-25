/* Model candle (AI): the transformer trained on the M5 bars.

   The top shows its training: running or not, the epoch, the data it learns from, and the exam of its best version
   (how close it lands per candle next to a guess without a brain). The buttons start it (carrying on from the
   checkpoint), stop it (it saves first), or start it from zero.

   Below: its forecast of the next 12 M5 candles from the newest bar, drawn on the chart as hollow candles inside the
   10-90% band, and the scenarios that come from it: BUY if the price falls to A, SELL if it rises to B, a trend entry
   when the model leans. The live simulation trades these scenarios as its "Model candle" rule and the LLM Analyst
   reads them; "Isi tiket" copies one into the order ticket, where Preview and confirmation still apply. */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;
  let statusTimer = 0;
  let fcTimer = 0;
  let lastFc = null;

  const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "–" : Number(v).toFixed(d));
  const pct = (v, d = 1) => (v === null || v === undefined ? "–" : `${Number(v).toFixed(d)}%`);
  const sgn = (v, d = 2) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}`);
  const clock = (t) => (t ? new Date(t * 1000).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" }) : "–");
  const KIND = { reversal_buy: "A · bawah pita", reversal_sell: "B · atas pita", trend: "Trend" };
  try { $("cl-draw").checked = localStorage.getItem("trade:cl-draw") !== "0"; } catch { /* the default stays */ }

  // ── training ────────────────────────────────────────────────────────────
  async function loadStatus() {
    clearTimeout(statusTimer);
    let st;
    try {
      st = await T.api(`/api/quant/candle/status?symbol=${encodeURIComponent(S.symbol || "XAUUSD")}`);
    } catch (e) {
      $("cl-train").replaceChildren(h("p", { class: "note-warn" }, /Not Found/i.test(e.message)
        ? "Server trading belum punya fitur ini: restart server trading." : e.message));
      statusTimer = setTimeout(loadStatus, 30000);
      return;
    }
    renderStatus(st);
    statusTimer = setTimeout(loadStatus, st.running ? 10000 : 30000);
  }

  function renderStatus(st) {
    const pill = $("cl-pill");
    if (st.running && st.waiting) { pill.textContent = "tunggu data baru"; pill.className = "pill warn"; }
    else if (st.running) { pill.textContent = "melatih"; pill.className = "pill demo"; }
    else if (st.has_model) { pill.textContent = "siap"; pill.className = "pill off"; }
    else { pill.textContent = "belum ada model"; pill.className = "pill off"; }
    const exam = st.best_exam || {};
    const data = st.data || {};
    const stat = (label, value, cls) => h("span", { class: `anstat ${cls || ""}` }, h("small", null, label), h("b", null, String(value ?? "–")));
    const score = st.best_score;
    const parts = [h("div", { class: "anstats" },
      stat("Status", st.running ? (st.waiting ? "menunggu bar baru" : "melatih") : "berhenti"),
      stat("Epoch", st.epoch ?? "–"),
      stat("Error vs tanpa otak", score ? pct(score * 100, 2) : "–", score && score < 0.99 ? "pos" : ""),
      stat("Ukuran", st.params ? `${st.size} · ${(st.params / 1e6).toFixed(1)}M` : st.size),
      stat("Data M5", data.bars ? `${Number(data.bars).toLocaleString("id-ID")} bar` : "–"),
      stat("Rentang", data.days ? `${Math.round(data.days)} hari` : "–"))];
    if (exam.verdict) parts.push(h("p", { class: exam.edge ? "pos" : "hint" }, exam.verdict));
    if (st.running && st.waiting) {
      parts.push(h("p", { class: "hint" }, "Sudah mentok di data yang ada. Ia lanjut sendiri begitu ada satu hari bar M5 baru."));
    }
    $("cl-train").replaceChildren(...parts);
    $("cl-start").hidden = st.running;
    $("cl-stop").hidden = !st.running;
    $("cl-fresh").disabled = st.running;
    if (st.size && !st.running) $("cl-size").value = st.size;
    renderExam(exam.per_candle || []);
  }

  function renderExam(rows) {
    if (!rows.length) { $("cl-exam").replaceChildren(h("p", { class: "hint" }, "Belum ada ujian.")); return; }
    const head = ["Candle", "Dekat", "Tanpa otak", "Selisih", "Error $", "Arah", "Mayoritas", "Pita 80%"];
    $("cl-exam").replaceChildren(h("table", null,
      h("thead", null, h("tr", null, head.map((x) => h("th", null, x)))),
      h("tbody", null, rows.map((c) => h("tr", null,
        h("td", null, `+${c.candle}`), h("td", null, pct(c.closeness)), h("td", null, pct(c.closeness_no_brain)),
        h("td", { class: c.gain_points > 0 ? "pos" : c.gain_points < 0 ? "neg" : "" }, sgn(c.gain_points)),
        h("td", null, num(c.error_usd_close)), h("td", null, pct(c.direction_hit)), h("td", null, pct(c.direction_majority)),
        h("td", null, pct(c.band_80_hit)))))),
    h("p", { class: "hint" }, "Dekat = 100% − error dalam ATR (0% kalau meleset 1 ATR atau lebih). Yang penting kolom Selisih: " +
      "seberapa jauh model lebih dekat dari tebakan tanpa otak. Pita 80% dekat 80 berarti rentangnya jujur."));
  }

  async function train(action, fresh) {
    if (fresh && !confirm("Mulai dari nol? Model ukuran ini (best.pt, log, status) dihapus, lalu belajar dari awal.")) return;
    try {
      const body = { action, size: $("cl-size").value, fresh: !!fresh, symbol: S.symbol || "XAUUSD" };
      const r = await T.api("/api/quant/candle/train", { method: "POST", body });
      T.setBanner(action === "stop"
        ? (r.still_running && r.still_running.length ? "Training belum berhenti, coba lagi sebentar." : "Training berhenti dan tersimpan.")
        : `Training jalan di latar (PID ${r.pid}). Progres: candle_lm.log.`);
    } catch (e) { T.setBanner(e.message); }
    setTimeout(loadStatus, 1500);
  }

  // ── forecast and scenarios ──────────────────────────────────────────────
  async function loadForecast() {
    clearTimeout(fcTimer);
    if (!S.symbol) return;
    const asked = S.symbol;
    let fc;
    try {
      fc = await T.api(`/api/quant/candle/forecast?symbol=${encodeURIComponent(asked)}`);
    } catch (e) {
      fc = { ok: false, reason: /Not Found/i.test(e.message) ? "Server trading belum punya fitur ini: restart server trading." : e.message };
    }
    if (asked !== S.symbol) return;
    lastFc = fc;
    renderForecast(fc);
    draw();
    fcTimer = setTimeout(loadForecast, 60000);
  }

  function renderForecast(fc) {
    if (!fc.ok) {
      $("cl-fc").replaceChildren(h("p", { class: "hint" }, fc.reason || "Belum ada tebakan."));
      $("cl-scen").replaceChildren();
      return;
    }
    const d = T.digits();
    const exam = fc.exam || {};
    const pick = new Set([1, 2, 3, 6, 9, 12]);
    $("cl-fc").replaceChildren(
      h("p", { class: "hint" }, `Dari bar ${clock(fc.last_bar_time)} · close ${T.fmtPx(fc.close, d)} · ATR ${num(fc.atr_price)} · model ${fc.size} epoch ${fc.epoch ?? "–"}`),
      exam.edge ? h("p", { class: "pos" }, "Ujian lulus: lebih dekat dari tebakan tanpa otak.")
        : h("p", { class: "note-warn" }, "Belum lebih dekat dari tebakan tanpa otak: pakai pitanya untuk ruang SL/TP, jangan arahnya."),
      h("div", { class: "simtable" }, h("table", null,
        h("thead", null, h("tr", null, ["Candle", "Jam", "Close tengah", "10% – 90%", "P naik"].map((x) => h("th", null, x)))),
        h("tbody", null, fc.candles.filter((c) => pick.has(c.candle)).map((c) => h("tr", null,
          h("td", null, `+${c.candle}`), h("td", null, clock(c.time)), h("td", null, T.fmtPx(c.close, d)),
          h("td", null, `${T.fmtPx(c.close_p10_p90[0], d)} – ${T.fmtPx(c.close_p10_p90[1], d)}`),
          h("td", { class: c.p_up > 0.55 ? "pos" : c.p_up < 0.45 ? "neg" : "" }, `${Math.round(c.p_up * 100)}%`))))),
      ),
    );
    const scen = fc.scenarios || [];
    if (!scen.length) { $("cl-scen").replaceChildren(h("p", { class: "hint" }, "Tidak ada skenario untuk bar ini.")); return; }
    $("cl-scen").replaceChildren(h("strong", null, "Skenario"), ...scen.map((s) => {
      const fill = h("button", { class: "btn mini", type: "button" }, "Isi tiket");
      fill.onclick = () => fillTicket(s);
      return h("div", { class: "clscen-row" },
        h("div", { class: "row" },
          h("span", { class: `pill ${s.side === "buy" ? "buyp" : "sellp"}` }, s.side.toUpperCase()),
          h("b", null, KIND[s.kind] || s.kind),
          h("span", { class: "hint" }, s.order === "limit" ? `limit di ${T.fmtPx(s.entry, d)}` : "sekarang (pasar)"),
          fill),
        h("p", { class: "hint" }, `SL ${T.fmtPx(s.stop, d)} · TP ${T.fmtPx(s.target, d)} · R:R ${num(s.rr)} · berlaku s/d ${clock(s.valid_until)}`),
        h("p", { class: "hint" }, s.why));
    }));
  }

  function fillTicket(s) {
    if (!lastFc || lastFc.symbol !== S.symbol) { T.setBanner("Tebakan ini untuk simbol lain. Tebak ulang dulu."); return; }
    $("sl").value = s.stop;
    $("tp").value = s.target;
    $("comment").value = `candle ${s.kind}`.slice(0, 31);
    T.setSide(s.side);
    if (T.updateLines) T.updateLines();
    if (T.scheduleRisk) T.scheduleRisk();
    T.setBanner(s.order === "limit"
      ? `Tiket terisi (${s.side.toUpperCase()}, SL, TP). Tiket ini order pasar: skenario ini baru berlaku kalau harga sampai ${s.entry}. Belum ada order yang dikirim.`
      : "Tiket terisi dari skenario trend. Periksa lot, SL, dan TP, lalu Preview. Belum ada order yang dikirim.");
  }

  function draw() {
    if (!T.chart) return;
    const fc = lastFc;
    if (!$("cl-draw").checked || !fc || !fc.ok || fc.symbol !== S.symbol) { T.chart.setForecast(null); return; }
    const levels = (fc.scenarios || []).filter((s) => s.order === "limit")
      .map((s) => ({ price: s.entry, label: s.kind === "reversal_buy" ? "A buy" : "B sell", color: s.side === "buy" ? "buy" : "sell" }));
    T.chart.setForecast({
      step: 300,
      candles: fc.candles.map((c) => ({ t: c.time, o: c.open, h: c.high, l: c.low, c: c.close,
        c10: c.close_p10_p90[0], c90: c.close_p10_p90[1] })),
      levels,
    });
  }

  // ── wiring ──────────────────────────────────────────────────────────────
  $("cl-start").onclick = () => train("start", false);
  $("cl-stop").onclick = () => train("stop", false);
  $("cl-fresh").onclick = () => train("start", true);
  $("cl-refresh").onclick = () => loadForecast();
  $("cl-draw").onchange = () => {
    try { localStorage.setItem("trade:cl-draw", $("cl-draw").checked ? "1" : "0"); } catch { /* per-viewer convenience only */ }
    draw();
  };
  T.candleSymbolChanged = () => {
    lastFc = null;
    draw();
    loadForecast();
    loadStatus();
  };
  loadStatus();
})();
