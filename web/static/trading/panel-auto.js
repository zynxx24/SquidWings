/* Auto trade: the neural brain (and the LLM) send real orders, and a trailing stop is the exit.

   The card shows the run: the open position with its entry, the stop as it trails, the best
   price so far and whether the target has locked it, the day's P/L against its limit, the last
   decision on a closed bar, and every closed trade. Start sends real orders on the connected
   account: demo only unless "izinkan akun live" is ticked. */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;
  let timer = 0;
  let report = null;
  const SIDE = { buy: "BUY", sell: "SELL", none: "TUNGGU" };
  const MODE = { brain: "otak", llm: "LLM", agree: "otak & LLM" };
  const when = (t) => (t ? new Date(t * 1000).toLocaleString("id-ID", { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "–");
  const money = (v) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}`);
  for (const id of ["auto-tf", "auto-mode", "auto-lots", "auto-loss", "auto-trail", "auto-lock", "auto-hours"]) {
    try { const v = localStorage.getItem(`trade:${id}`); if (v !== null) $(id).value = v; } catch { /* defaults */ }
    $(id).addEventListener("change", () => { try { localStorage.setItem(`trade:${id}`, $(id).value); } catch { /* fine */ } });
  }

  function settings() {
    const clamp = (id, lo, hi, fallback) => {
      const n = Number($(id).value);
      return Math.max(lo, Math.min(hi, Number.isFinite(n) && $(id).value !== "" ? n : fallback));
    };
    return {
      symbol: S.symbol, timeframe: $("auto-tf").value, mode: $("auto-mode").value,
      lots: Math.round(clamp("auto-lots", 0.01, 1, 0.01) * 100) / 100,
      max_daily_loss_pct: clamp("auto-loss", 0.1, 20, 2), trail_atr: clamp("auto-trail", 0.2, 5, 1),
      lock_atr: clamp("auto-lock", 0, 2, 0.3), max_hours: clamp("auto-hours", 1, 168, 24),
      allow_live: $("auto-live").checked,
      llm_model: ($("llm-model") && $("llm-model").value.trim()) || null,
    };
  }

  async function start() {
    if (!S.symbol) { T.setBanner("Pilih simbol dulu."); return; }
    const body = settings();
    const what = `${body.symbol} ${body.timeframe}, ${body.lots} lot, keputusan ${MODE[body.mode]}, trailing ${body.trail_atr} ATR, ` +
      `rugi harian maks ${body.max_daily_loss_pct}%${body.allow_live ? ", AKUN LIVE DIIZINKAN" : " (demo saja)"}`;
    if (!window.confirm(`Mulai auto trade dengan order sungguhan?\n${what}`)) return;
    $("auto-start").disabled = true;
    try {
      show(await T.api("/api/quant/auto/start", { method: "POST", body }));
      T.setBanner("Auto trade berjalan: order dikirim sendiri oleh otak/LLM, SL mengikuti harga.");
    } catch (e) { T.setBanner(e.message); $("auto-start").disabled = false; }
    schedule(2000);
  }

  async function stop() {
    $("auto-stop").disabled = true;
    try { show(await T.api("/api/quant/auto/stop", { method: "POST" })); } catch (e) { T.setBanner(e.message); }
    $("auto-stop").disabled = false;
    schedule(2000);
  }

  async function refresh() {
    try { show(await T.api("/api/quant/auto/status")); } catch { /* the next poll tries again */ }
    schedule(report && report.state === "running" ? 3000 : 15000);
  }

  function schedule(ms) { clearTimeout(timer); timer = setTimeout(refresh, ms); }

  function show(r) {
    report = r;
    const running = r.state === "running";
    const pill = $("auto-pill");
    pill.textContent = running ? `jalan · ${r.symbol} ${r.timeframe}` : r.state === "finished" ? "selesai" : "mati";
    pill.className = "pill " + (running ? "live" : r.state === "finished" ? "sim" : "off");
    $("auto-start").hidden = running;
    $("auto-start").disabled = !S.symbol || running;
    $("auto-stop").hidden = !running;
    $("auto-note").textContent = running ? `${Math.floor((r.elapsed_s || 0) / 60)} menit · ${r.decisions} keputusan · ${r.orders} order · ${r.modifies} geser SL`
      : r.state === "finished" ? `berhenti: ${r.ended}` : "";
    const box = $("auto-status");
    if (r.state === "idle") { box.hidden = true; $("auto-log").replaceChildren(); return; }
    box.hidden = false;
    const parts = [];
    const stat = (label, value, cls) => h("span", { class: `anstat ${cls || ""}` }, h("small", null, label), h("b", null, String(value ?? "–")));
    parts.push(h("div", { class: "anstats" },
      stat("Ekuitas", Number(r.equity).toFixed(2)),
      stat("P/L hari ini", money(r.day_pnl), r.day_pnl > 0 ? "pos" : r.day_pnl < 0 ? "neg" : ""),
      stat("Batas rugi", `${r.max_daily_loss_pct}% = ${(r.day_start_equity * r.max_daily_loss_pct / 100).toFixed(2)}`),
      stat("Selesai", `${r.closed_count} · ${r.wins} untung`),
      stat("Total", money(r.profit), r.profit > 0 ? "pos" : r.profit < 0 ? "neg" : "")));
    const p = r.position;
    if (p) {
      const d = r.digits || T.digits();
      parts.push(h("div", { class: "anhead" }, h("span", { class: `anside ${p.side}` }, SIDE[p.side]),
        h("b", null, `#${p.ticket} · ${p.lots} lot · ${p.source === "llm" ? "LLM" : "otak"}`),
        h("span", { class: "hint" }, `buka ${when(p.opened)} · tutup paling lambat ${when(p.closes)}`)));
      parts.push(h("div", { class: "anlevels" },
        level("Entry", T.fmtPx(p.entry, d), ""),
        level(p.locked ? "SL (trailing)" : "Stop loss", T.fmtPx(p.stop, d), p.locked ? `digeser ${p.moves}×` : "asli", "sell"),
        level("Level target", T.fmtPx(p.target_level, d), p.locked ? "tersentuh: SL dikunci" : "belum tersentuh", "buy"),
        level("Terbaik", T.fmtPx(p.best, d), p.profit !== undefined ? `P/L ${money(p.profit)}` : "", p.profit > 0 ? "buy" : "")));
      if (p.why) parts.push(h("p", { class: "anwhy" }, p.why));
    } else {
      parts.push(h("p", { class: "hint" }, "Tidak ada posisi. Menunggu bar tutup berikutnya."));
    }
    const last = r.last_decision;
    if (last && last.at) {
      parts.push(h("p", { class: "hint" }, `Keputusan terakhir ${when(last.at)}: ${SIDE[last.side] || last.side}` +
        (last.confidence !== undefined && last.confidence !== null ? ` · yakin ${Math.round(last.confidence * 100)}%` : "") +
        ` · ${last.why || ""}`));
    } else if (r.brain && !r.brain.ok) {
      parts.push(h("p", { class: "note-warn" }, `Otak: ${r.brain.reason || "belum siap"}`));
    }
    if (r.closed && r.closed.length) {
      parts.push(h("div", { class: "simtable" }, h("table", { class: "simrank" },
        h("thead", null, h("tr", null, ["Waktu", "Arah", "Keluar", "P/L"].map((t, i) => h("th", { class: i > 1 ? "num" : null }, t)))),
        h("tbody", null, r.closed.slice(-12).reverse().map((c) => h("tr", { title: c.why || "" },
          h("td", null, when(c.opened)), h("td", { class: c.side === "buy" ? "pos" : "neg" }, SIDE[c.side]),
          h("td", { class: "num" }, c.why), h("td", { class: "num " + (c.profit > 0 ? "pos" : c.profit < 0 ? "neg" : "") }, money(c.profit))))))));
    }
    box.replaceChildren(...parts);
    const LOGS = { start: "mulai", decision: "keputusan", open: "buka", trail: "geser SL", close_sent: "tutup", closed: "selesai",
                   refused: "ditolak", skip: "lewat", error: "error", end: "berhenti" };
    $("auto-log").replaceChildren(h("table", { class: "simrank" }, h("tbody", null, (r.log || []).slice().reverse().map((row) =>
      h("tr", null, h("td", null, when(row.t)), h("td", null, LOGS[row.type] || row.type),
        h("td", null, row.type === "trail" ? `SL → ${row.stop} (${row.how}${row.ok ? "" : ", gagal"})`
          : row.type === "decision" ? `${SIDE[row.side] || row.side}: ${row.why || ""}`
          : row.type === "open" ? `${row.side} @ ${row.entry} SL ${row.stop} target ${row.target_level}`
          : row.type === "closed" ? `${row.why} · ${money(row.profit)}`
          : row.comment || row.why || row.ended || row.error || ""))))));
  }

  function level(label, value, sub, cls) {
    return h("div", { class: `anlevel ${cls || ""}` }, h("small", null, label), h("b", null, value), sub ? h("small", null, sub) : "");
  }

  T.autoSymbolChanged = () => { $("auto-start").disabled = !S.symbol || (report && report.state === "running"); };
  $("auto-start").onclick = start;
  $("auto-stop").onclick = stop;
  refresh();
})();
