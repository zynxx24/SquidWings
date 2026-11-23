/* LLM Analyst Trade and the neural brain behind it.

   The card shows the newest plan for the symbol, whether it came from "Analisa sekarang" or from
   the live simulation in the intraday style: side, entry, stop, target, reward to risk, the lot
   the risk settings give, the brain's chances, and why. "Isi tiket" copies it into the order
   ticket, where Preview and confirmation still apply. Every plan is followed on the M5 bars to
   its target, stop or end of hold, and the record below the card is the result.

   "Otak neural" shows the brain: how big its memory is, its exam on the newest unseen days,
   whether it got better with each training, the plans it ranks best now, what happened after
   the most similar past moments, and how this session, hour and news state went before. */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;
  let timer = 0;
  let latest = null;
  let lots = null;
  let lastSimPlan = "";

  const tf = () => $("an-tf").value;
  const pct = (v) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`);
  const sign = (v, d = 2) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}`);
  const when = (t) => (t ? new Date(t * 1000).toLocaleString("id-ID", { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "–");
  const day = (t) => (t ? new Date(t * 1000).toLocaleDateString("id-ID", { day: "numeric", month: "short" }) : "–");
  const STATUS = { open: "berjalan", target: "kena TP", stop: "kena SL", timeout: "habis waktu", wait: "tunggu", trail: "trailing stop" };
  const SIDE = { buy: "BUY", sell: "SELL", none: "TUNGGU" };
  try { $("an-tf").value = localStorage.getItem("trade:an-tf") || "H1"; } catch { /* the default stays */ }

  async function load() {
    if (!S.symbol) return;
    const asked = `${S.symbol}|${tf()}`;
    const q = `symbol=${encodeURIComponent(S.symbol)}&timeframe=${tf()}`;
    const [plans, brain] = await Promise.all([
      T.api(`/api/quant/analyst?${q}`).catch((e) => ({ error: e.message })),
      T.api(`/api/quant/brain?${q}`).catch((e) => ({ error: e.message })),
    ]);
    if (asked !== `${S.symbol}|${tf()}`) return;     // the symbol or timeframe changed while this was loading
    if (plans.error && /Not Found/i.test(plans.error)) {
      $("an-card").hidden = false;
      $("an-card").replaceChildren(h("p", { class: "note-warn" }, "Server trading belum punya fitur ini: restart server trading."));
      return;
    }
    if (!plans.error) showPlans(plans);
    if (!brain.error) showBrain(brain);
    schedule(brain.status && brain.status.state === "training" ? 3000 : 60000);
  }

  function schedule(ms) {
    clearTimeout(timer);
    timer = setTimeout(load, ms);
  }

  // ── the card ────────────────────────────────────────────────────────────
  function showPlans(r) {
    latest = r.latest;
    lots = r.lots;
    renderCard(latest, lots);
    renderRecord(r.summary);
    renderHistory(r.plans || []);
  }

  function renderCard(plan, size) {
    const card = $("an-card");
    const pill = $("an-pill");
    if (!plan) {
      card.hidden = false;
      card.className = "ancard";
      card.replaceChildren(h("p", { class: "hint" }, "Belum ada rencana untuk simbol ini. Tekan Analisa sekarang, atau jalankan simulasi gaya Harian dengan LLM."));
      pill.textContent = "–"; pill.className = "pill off";
      setLines(null);
      return;
    }
    const now = Date.now() / 1000;
    const open = plan.status === "open";
    const stale = open && now > plan.valid_until;
    const side = plan.side || "none";
    pill.textContent = side === "none" ? "tunggu" : `${SIDE[side]} · ${STATUS[plan.status] || plan.status}`;
    pill.className = "pill " + (side === "buy" ? "buyp" : side === "sell" ? "sellp" : "off");
    card.hidden = false;
    card.className = `ancard ${side}`;
    const parts = [h("div", { class: "anhead" },
      h("span", { class: `anside ${side}` }, SIDE[side] || side),
      h("b", null, `${plan.symbol} · ${plan.timeframe}`),
      h("span", { class: "hint" }, `${plan.session_label || ""} · ${when(plan.created_at)} · ${plan.source === "sim" ? "dari simulasi" : "analisa"}`))];
    if (plan.entry !== undefined && plan.entry !== null) {
      const brain = plan.brain || {};
      parts.push(h("div", { class: "anlevels" },
        level("Entry", T.fmtPx(plan.entry), ""),
        level("Stop loss", T.fmtPx(plan.stop), `${plan.stop_atr} ATR`, "sell"),
        level("Take profit", T.fmtPx(plan.target), `${plan.target_atr} ATR`, "buy"),
        level("R:R", `1 : ${plan.rr}`, size && size.lots ? `${size.lots} lot · risiko ${size.risk_money} (${size.risk_pct}%)` : (size && size.error) || ""),
      ));
      parts.push(h("div", { class: "anodds" },
        `Otak: P(TP dulu) ${pct(brain.p_target)} · P(SL dulu) ${pct(brain.p_stop)} · EV ${sign(brain.ev_atr)} ATR` +
        `${plan.exam_passed ? "" : " · otak belum lulus ujian"} · yakin LLM ${pct(plan.confidence)}`));
      const m = plan.math || {};
      if (m.breakeven_win_rate !== undefined) {
        const cell = (label, value, cls, title) => h("span", { title: title || "" }, h("small", null, label), h("b", { class: cls || "" }, value));
        parts.push(h("div", { class: "anmath" },
          cell("TP impas", pct(m.p_target_needed ?? m.breakeven_win_rate), "", "Seberapa sering TP harus kena supaya rencana ini tidak rugi"),
          cell("Di atas impas", m.edge === undefined ? "–" : `${sign(m.edge * 100, 0)} poin`, m.edge > 0 ? "pos" : m.edge <= 0 ? "neg" : "", "P(TP) otak dikurangi titik impas. Kosong kalau SL/TP bukan salah satu rencana otak"),
          cell("EV", m.ev_r === undefined ? "–" : `${sign(m.ev_r)} R`, m.ev_r > 0 ? "pos" : m.ev_r < 0 ? "neg" : "", "Nilai harapan per trade dalam R (1 R = jarak stop)"),
          cell("½ Kelly", m.kelly_half === undefined ? "–" : pct(m.kelly_half, 1), "", "Setengah Kelly dari peluang otak. Batas risiko panel tetap berlaku dan biasanya jauh lebih kecil")));
      }
      if (plan.checks && plan.checks.length) parts.push(h("ul", { class: "anchecks" }, plan.checks.map((c) => h("li", null, c))));
      const tags = Object.entries(plan.tags || {});
      if (tags.length) parts.push(h("div", { class: "antags" }, tags.map(([k, v]) => h("span", { class: "chip", title: k }, k === "waktu" ? `waktu: ${v}` : v))));
    }
    if (plan.why) parts.push(h("p", { class: "anwhy" }, plan.why));
    if (plan.risks) parts.push(h("p", { class: "hint" }, `Risiko: ${plan.risks}`));
    if (side === "none" && plan.shadow) {
      parts.push(h("p", { class: "hint" }, `Rencana terbaik otak saat itu: ${plan.shadow.plan} (EV ${sign(plan.shadow.ev_atr)} ATR)` +
        (plan.shadow.status !== "open" ? ` → ${STATUS[plan.shadow.status] || plan.shadow.status}` : " · masih diikuti")));
    }
    const foot = [];
    if (open) {
      foot.push(h("span", { class: stale ? "note-warn" : "hint" }, stale
        ? "Batas masuk sudah lewat: harga mungkin sudah bergeser. Rencana tetap diikuti sampai TP, SL, atau habis waktu."
        : `Masih masuk akal sampai ${when(plan.valid_until)} · ditutup paling lambat ${when(plan.hold_until)}`));
      const fill = h("button", { class: "btn mini primary", type: "button" }, "Isi tiket");
      fill.onclick = () => fillTicket(plan, size);
      foot.push(fill);
    } else if (plan.status !== "wait") {
      foot.push(h("span", { class: plan.result_r > 0 ? "pos" : "neg" }, `${STATUS[plan.status]} · ${sign(plan.result_r)} R`));
    }
    if (foot.length) parts.push(h("div", { class: "row anfoot" }, ...foot));
    card.replaceChildren(...parts);
    setLines(open && plan.symbol === S.symbol ? plan : null);
  }

  function level(label, value, sub, cls) {
    return h("div", { class: `anlevel ${cls || ""}` }, h("small", null, label), h("b", null, value), sub ? h("small", null, sub) : "");
  }

  function setLines(plan) {
    T.analystLines = plan ? [
      { price: plan.entry, color: "accent", dash: [2, 3], label: `Analyst ${SIDE[plan.side]}` },
      { price: plan.stop, color: "sell", dash: [6, 3], label: "Analyst SL" },
      { price: plan.target, color: "buy", dash: [6, 3], label: "Analyst TP" },
    ] : [];
    if (T.updateLines) T.updateLines();
  }

  function fillTicket(plan, size) {
    if (plan.symbol !== S.symbol) { T.setBanner(`Rencana ini untuk ${plan.symbol}. Pilih simbol itu dulu.`); return; }
    if (size && size.lots) $("vol").value = size.lots;
    $("sl").value = plan.stop;
    $("tp").value = plan.target;
    $("comment").value = `analyst ${plan.side}`.slice(0, 31);
    T.setSide(plan.side);
    if (T.updateLines) T.updateLines();
    if (T.scheduleRisk) T.scheduleRisk();
    T.setBanner("Tiket terisi dari rencana Analyst. Periksa lot, SL, dan TP, lalu Preview. Belum ada order yang dikirim.");
  }

  function renderRecord(sum) {
    const box = $("an-record");
    if (!sum || (!sum.plans && !sum.waits && !sum.open)) { box.hidden = true; return; }
    box.hidden = false;
    const stat = (label, value, cls) => h("span", { class: `anstat ${cls || ""}` }, h("small", null, label), h("b", null, String(value ?? "–")));
    const parts = [h("div", { class: "anstats" },
      stat("Selesai", sum.plans), stat("Kena TP", sum.plans ? `${sum.targets} · ${pct(sum.target_rate)}` : "–"),
      stat("Kena SL", sum.stops), stat("Rata-rata", `${sign(sum.avg_r)} R`, sum.avg_r > 0 ? "pos" : sum.avg_r < 0 ? "neg" : ""),
      stat("Total", `${sign(sum.sum_r)} R`, sum.sum_r > 0 ? "pos" : sum.sum_r < 0 ? "neg" : ""), stat("Berjalan", sum.open))];
    if (sum.waits) {
      parts.push(h("p", { class: "hint" }, `Menunggu ${sum.waits} kali: ${sum.waits_missed_winner} ternyata melewatkan rencana yang menang, ` +
        `${sum.waits_avoided_loser} menghindari yang kalah (dinilai dari rencana terbaik otak saat itu).`));
    }
    const math = sum.math || {};
    if (math.trades) {
      parts.push(h("p", { class: math.expectancy_r > 0 && math.p_value !== null && math.p_value < 0.05 ? "pos" : "hint" },
        `Dalam R: expectancy ${sign(math.expectancy_r)} per rencana · menang ${pct(math.win_rate)} (impas ${pct(math.breakeven_win_rate)}, ` +
        `hati-hati ${pct(math.cautious_win_rate)}) · payoff ${math.payoff ?? "–"} · rugi beruntun terpanjang ${math.longest_losing_streak} · ` +
        `drawdown ${T.fmt2(math.max_drawdown_r)} R` + (math.t_stat !== null && math.t_stat !== undefined ? ` · t ${sign(math.t_stat)} (p ${math.p_value})` : "") +
        `. ${math.verdict}.`));
    }
    if (sum.by_condition && sum.by_condition.length) {
      parts.push(h("div", { class: "hint" }, "Per kondisi saat rencana dibuat (ini yang dibaca LLM sebelum mengulang kombinasi yang sama):"));
      parts.push(h("div", { class: "simtable" }, h("table", { class: "simrank" },
        h("thead", null, h("tr", null, ["Kondisi", "Rencana", "TP", "Rata R"].map((t, i) => h("th", { class: i ? "num" : null }, t)))),
        h("tbody", null, sum.by_condition.map((x) => h("tr", null, h("td", { title: `${x.name}: ${x.value}` }, x.name === "waktu" || x.name === "peringatan" ? `${x.name}: ${x.value}` : x.value),
          h("td", { class: "num" }, x.plans), h("td", { class: "num", title: `hati-hati ${pct(x.target_rate_cautious)}` }, pct(x.target_rate)),
          h("td", { class: "num " + (x.avg_r > 0 ? "pos" : "neg") }, sign(x.avg_r))))))));
    }
    if (sum.by_session && sum.by_session.length) {
      parts.push(h("div", { class: "simtable" }, h("table", { class: "simrank" },
        h("thead", null, h("tr", null, ["Sesi", "Rencana", "TP", "Rata R"].map((t, i) => h("th", { class: i ? "num" : null }, t)))),
        h("tbody", null, sum.by_session.map((x) => h("tr", null, h("td", null, x.label), h("td", { class: "num" }, x.plans),
          h("td", { class: "num" }, pct(x.target_rate)), h("td", { class: "num " + (x.avg_r > 0 ? "pos" : "neg") }, sign(x.avg_r))))))));
    }
    box.replaceChildren(...parts);
  }

  function renderHistory(plans) {
    if (!plans.length) { $("an-plans").replaceChildren(h("p", { class: "hint" }, "Belum ada.")); return; }
    $("an-plans").replaceChildren(h("table", { class: "simrank" },
      h("thead", null, h("tr", null, ["Waktu", "Arah", "SL/TP", "Hasil"].map((t, i) => h("th", { class: i > 1 ? "num" : null }, t)))),
      h("tbody", null, plans.map((p) => h("tr", { title: p.why || "" },
        h("td", null, `${when(p.created_at)} ${p.timeframe}`),
        h("td", { class: p.side === "buy" ? "pos" : p.side === "sell" ? "neg" : "" }, SIDE[p.side] || p.side),
        h("td", { class: "num" }, p.stop_atr ? `${p.stop_atr}/${p.target_atr}` : "–"),
        h("td", { class: "num " + (p.result_r > 0 ? "pos" : p.result_r < 0 ? "neg" : "") },
          p.status === "wait" ? (p.shadow && p.shadow.status !== "open" ? `tunggu · otak ${STATUS[p.shadow.status]}` : "tunggu")
            : p.status === "open" ? "berjalan" : `${STATUS[p.status]} ${sign(p.result_r)}R`))))));
  }

  // ── the brain ───────────────────────────────────────────────────────────
  function showBrain(r) {
    const st = r.status || {};
    const view = r.view;
    const pill = $("an-brain-pill");
    const exam = st.exam || {};
    pill.textContent = st.state === "training" ? "melatih…" : !st.ready ? "belum dilatih" : exam.passed ? "lulus ujian" : "belum lulus";
    pill.className = "pill " + (st.state === "training" ? "warn" : exam.passed ? "buyp" : st.ready ? "sim" : "off");
    $("an-train").disabled = st.state === "training";
    $("an-train-note").textContent = st.state === "training" ? (st.progress || "melatih") : st.error ? st.error
      : st.trained_at ? `dilatih ${when(st.trained_at)} (${st.seconds} dtk) · otomatis tiap ${st.retrain_hours} jam saat simulasi Harian jalan` : "";
    const parts = [];
    if (!st.ready) {
      parts.push(h("p", { class: "hint" }, st.error || `Belum dilatih untuk ${S.symbol} ${tf()}. Tekan Latih ulang sekarang. Butuh minimal ${st.min_rows} bar dengan hasil.`));
      $("an-brain").replaceChildren(...parts);
      return;
    }
    const info = st.info || {};
    parts.push(h("div", { class: "kvgrid" }, [
      ["Memori", `${st.rows} bar ${st.timeframe} · ${day(st.first)} – ${day(st.last)}`],
      ["Data mentah", `${st.base_bars} bar M5`],
      ["Jaringan", `${st.members} jaringan × ${(st.hidden || []).join("-")} neuron · ${st.features} ukuran · ${st.epochs} epoch` +
        (st.parameters ? ` · ${Math.round(st.parameters / 1000)}k bobot` : "")],
      ...(st.capacity ? [["Ukuran otak", `${st.capacity.tier} (maks ${(st.capacity.largest || []).join("-")}) · ${st.capacity.why}`]] : []),
      ["Ujian", `${exam.rows} bar terbaru yang tidak pernah dilihat`],
      ["Berita", (st.masked || []).some((n) => n.startsWith("news")) ? `arsip baru menutup ${pct((st.coverage || {}).news)} memori: belum dipakai jaringan (dibaca LLM langsung)` : `dipakai · ${info.news_known_rows} bar dengan berita`],
    ].map(([k, v]) => [h("div", null, k), h("b", null, v)])));
    parts.push(h("p", { class: exam.passed ? "pos" : "note-warn" }, exam.verdict || ""));
    if (st.groups && st.groups.length) {
      const tries = st.group_tries || [];
      const best = tries.find((t) => t.chosen) || {};
      parts.push(h("div", { class: "antags" }, st.groups.map((g) => h("span", { class: "chip" + (g.used ? " on pos" : ""),
        title: `${g.columns} ukuran. Dipakai hanya kalau tebakan di data validasi jadi lebih tepat dengannya` }, `${g.label}: ${g.used ? "dipakai" : "tidak membantu"}`))));
      parts.push(h("p", { class: "hint" }, `Otak dilatih ${tries.length} kali dengan susunan ukuran berbeda dan memilih yang paling tepat di data validasi` +
        (best.gain > 0 ? ` (lebih baik ${best.gain} dari ukuran dasar saja).` : " (ukuran dasar saja menang: rumus tambahan belum menambah ketepatan di data ini).") +
        " Ujian di atas diambil sesudah pilihan itu, di bar yang tidak ikut memilih."));
    }
    if (exam.trades !== undefined) {
      const tone = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
      const stat = (label, value, cls) => h("span", { class: `anstat ${cls || ""}` }, h("small", null, label), h("b", null, value));
      parts.push(h("div", { class: "anstats" },
        stat("Otak / trade", sign(exam.avg_r_atr, 3), tone(exam.avg_r_atr)),
        stat("Selalu sama", sign(exam.static_avg_r_atr, 3), tone(exam.static_avg_r_atr)),
        stat("Acak", sign(exam.random_avg_r_atr, 3), tone(exam.random_avg_r_atr)),
        stat("Trade ujian", `${exam.trades} · TP ${pct(exam.tp_rate)}`),
        stat("Skill peluang", sign(exam.skill, 3), tone(exam.skill)),
        stat("Ambang masuk", `${T.fmt2(exam.threshold_atr)} ATR`)));
      parts.push(h("p", { class: "hint" }, `"Selalu sama" = selalu ${exam.static_plan}, rencana terbaik di data latih. ` +
        `Ambang masuk dipilih di data validasi${exam.threshold_profitable === false ? "; tidak ada ambang yang untung di sana, jadi otak sangat pemilih" : ""}.`));
    }
    const history = st.history || [];
    if (history.length > 1) {
      parts.push(h("div", { class: "hint" }, "Belajar dari waktu ke waktu (setiap pelatihan):"));
      parts.push(h("div", { class: "anhistory" }, history.slice(-12).map((x) => h("span", { class: x.passed ? "pos" : x.avg_r_atr > 0 ? "" : "neg", title: `${when(x.at)} · ${x.rows} bar · ${x.trades} trade ujian` },
        `${x.rows} bar: ${sign(x.avg_r_atr, 3)}`))));
    }
    if (view && view.ok) {
      parts.push(h("div", { class: "hint" }, `Bar terakhir (${when(view.decided_at)}, ${view.session}) · ATR ${T.fmtPx(view.atr)} · ${view.take ? "otak mau ambil" : "otak menunggu"}:`));
      parts.push(h("table", { class: "simrank anplans" },
        h("thead", null, h("tr", null, ["Rencana", "P TP", "P SL", "EV ATR", "Beda"].map((t, i) => h("th", { class: i ? "num" : null }, t)))),
        h("tbody", null, view.plans.map((p) => h("tr", null, h("td", { class: p.side === "buy" ? "pos" : "neg" }, p.name),
          h("td", { class: "num" }, pct(p.p_target)), h("td", { class: "num" }, pct(p.p_stop)),
          h("td", { class: "num " + (p.ev_atr > 0 ? "pos" : "neg") }, sign(p.ev_atr)), h("td", { class: "num" }, T.fmt2(p.disagreement)))))));
      const sim = view.similar || {};
      if (sim.plans) {
        parts.push(h("div", { class: "hint" }, `${sim.count} situasi paling mirip di masa lalu (${sim.same_session} di sesi yang sama):`));
        parts.push(h("ul", { class: "ansimilar" }, sim.plans.map((p) => h("li", null,
          `${p.name}: ${p.target} kena TP, ${p.stop} kena SL, ${p.neither} habis waktu · rata-rata ${sign(p.avg_r_atr)} ATR` +
          (p.same_session_target !== undefined ? ` · sesi ini ${p.same_session_target} TP / ${p.same_session_stop} SL` : "")))));
      }
      const rg = view.regime_now || {};
      if (rg.state) {
        const q = rg.quant_rule || {};
        parts.push(h("p", { class: "hint" }, `Rezim sekarang (HMM): ${rg.label}${rg.state === "unknown" ? "" : " " + pct((rg.p || {})[rg.state])}, arah ${rg.trend_direction} · ` +
          `ADX ${rg.adx} · Kalman z ${rg.kalman_z} · TF besar ${rg.htf_bias > 0 ? "naik" : rg.htf_bias < 0 ? "turun" : "campur"} · ` +
          `skor aturan kuant BUY ${q.buy_score} / SELL ${q.sell_score}${q.setup && q.setup !== "none" ? ` · setup ${q.setup.toUpperCase()} aktif` : ""}`));
      }
      const cond = view.conditions_now || {};
      const condRows = ["session", "hour", "weekday", "news"].map((k) => cond[k]).filter((c) => c && c.rows);
      if (condRows.length) {
        parts.push(h("div", { class: "hint" }, `Kondisi sekarang, rencana acuan ${cond.reference_plan}: seberapa sering kena TP, dan rata-rata hasil dalam ATR.`));
        parts.push(h("div", { class: "simtable" }, h("table", { class: "simrank" },
          h("thead", null, h("tr", null, ["Kondisi (bar)", "BUY", "SELL"].map((t, i) => h("th", { class: i ? "num" : null }, t)))),
          h("tbody", null, condRows.map((c) => h("tr", null, h("td", { title: labelOf(c.key) }, `${labelOf(c.key)} (${c.rows})`),
            h("td", { class: "num " + (c.buy_avg_r > 0 ? "pos" : "neg") }, `${pct(c.buy_tp)} ${sign(c.buy_avg_r)}`),
            h("td", { class: "num " + (c.sell_avg_r > 0 ? "pos" : "neg") }, `${pct(c.sell_tp)} ${sign(c.sell_avg_r)}`)))))));
      }
    } else if (view && view.reason) {
      parts.push(h("p", { class: "note-warn" }, view.reason));
    }
    $("an-brain").replaceChildren(...parts);
  }

  const SESSION_WORDS = { sydney: "Sydney", tokyo: "Tokyo", london: "London", newyork: "New York", off: "di luar sesi", weekend: "akhir pekan" };
  function labelOf(key) {
    if (/^\d\d$/.test(key)) return `jam ${key}:00 UTC`;
    return key.split("+").map((k) => SESSION_WORDS[k] || k).join(" + ");
  }

  // ── actions ─────────────────────────────────────────────────────────────
  async function run() {
    if (!S.symbol) { T.setBanner("Pilih simbol dulu."); return; }
    const button = $("an-run");
    button.disabled = true;
    button.textContent = "Menganalisa…";
    try {
      const r = await T.api("/api/quant/analyst/run", { method: "POST", body: { symbol: S.symbol, timeframe: tf(), llm_model: ($("llm-model") && $("llm-model").value) || null } });
      latest = r.plan;
      renderCard(r.plan, r.lots);
      load();
    } catch (e) {
      T.setBanner(e.message);
    } finally {
      button.disabled = !S.connected;
      button.textContent = "Analisa sekarang";
    }
  }

  async function train() {
    if (!S.symbol) return;
    try {
      const r = await T.api("/api/quant/brain/train", { method: "POST", body: { symbol: S.symbol, timeframe: tf() } });
      showBrain({ status: r.status });
      schedule(2000);
    } catch (e) { T.setBanner(e.message); }
  }

  $("an-run").onclick = run;
  $("an-train").onclick = train;
  $("an-tf").onchange = () => {
    try { localStorage.setItem("trade:an-tf", tf()); } catch { /* not remembered */ }
    load();
  };
  $("an-brain-box").addEventListener("toggle", () => { if ($("an-brain-box").open) load(); });

  T.analystSymbolChanged = () => {
    $("an-run").disabled = !S.connected || !S.symbol;
    latest = null;
    setLines(null);
    load();
  };
  // The live simulation wrote a new plan: show it.
  T.analystFromSim = (plan) => {
    if (!plan || plan.id === lastSimPlan || plan.symbol !== S.symbol) return;
    lastSimPlan = plan.id;
    load();
  };
})();
