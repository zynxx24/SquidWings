/* The order ticket, SL/TP changes, the quant proposal, the backtest, and EA pairing. */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;

  T.setSide = (side) => {
    S.side = side;
    document.querySelectorAll(".side").forEach((x) => x.classList.toggle("on", x.dataset.side === side));
    const button = $("preview");
    button.textContent = `Preview ${side.toUpperCase()}`;
    button.className = `btn action ${side}`;
    T.scheduleRisk();
  };

  T.stepVolume = (direction) => {
    const step = S.spec ? Number(S.spec.volume_step) : 0.01;
    const low = S.spec ? Number(S.spec.volume_min) : 0.01;
    const next = Math.max(low, Number($("vol").value || 0) + direction * step);
    $("vol").value = next.toFixed(2);
    T.scheduleRisk();
  };

  // A field value of 0 or less counts as empty: the same as "no stop" or "no target".
  const numOf = (id) => { const v = $(id).value.trim(); if (v === "") return null; const n = Number(v); return n > 0 ? n : null; };
  T.numOf = numOf;

  function rrText(entry, sl, tp) {
    if (entry === null || entry === undefined || !sl || !tp) return "—";
    const risk = Math.abs(entry - sl), reward = Math.abs(tp - entry);
    return risk > 0 ? "1:" + (reward / risk).toFixed(2) : "—";
  }

  function orderFromForm() {
    const value = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
    return {
      symbol: S.symbol, side: S.side, volume: Number($("vol").value),
      sl: value("sl"), tp: value("tp"), comment: $("comment").value.trim(),
      reason: S.proposal && S.proposal.side === S.side ? S.proposal.reasons.join("; ") : "",
    };
  }

  const riskText = (risk) => (risk
    ? `Jika SL kena: rugi ${T.fmtMoney(risk.money)} (${risk.pct_of_equity ?? "—"}% ekuitas)`
    : "Tanpa SL: kerugian tidak dibatasi.");

  T.previewOrder = async () => {
    const box = $("previewbox");
    box.hidden = true;
    try {
      const r = await T.api("/api/mt5/preview", { method: "POST", body: orderFromForm() });
      const o = r.order;
      T.openModal("Konfirmasi order", [
        `${o.side.toUpperCase()} ${o.volume} lot ${o.symbol}`,
        `Harga perkiraan: ${T.fmtPx(r.price)}`,
        `SL: ${o.sl ?? "—"}   TP: ${o.tp ?? "—"}   R:R ${rrText(r.price, o.sl, o.tp)}`,
        riskText(r.risk),
        `Margin: ${T.fmtMoney(r.check.margin)}`,
        S.live ? "⚠ Akun riil. Uang sungguhan." : "Akun demo/simulasi.",
      ], S.live, () => T.api("/api/mt5/order/confirm", { method: "POST", body: { preview_id: r.preview_id } }),
      Date.now() + r.expires_in * 1000);
    } catch (e) {
      box.hidden = false;
      box.className = "previewbox bad";
      box.replaceChildren(...T.problemList("Order tidak bisa dikirim", e.message));
    }
  };

  T.scheduleRisk = () => {
    clearTimeout(S.riskTimer);
    S.riskTimer = setTimeout(updateRisk, 300);
    T.updateLines();
  };

  async function updateRisk() {
    const box = $("riskline");
    const vol = Number($("vol").value), sl = numOf("sl");
    if (!S.connected || !S.symbol || !(vol > 0) || !sl) { box.hidden = true; box.textContent = ""; return; }
    try {
      const r = await T.api(`/api/mt5/risk?symbol=${encodeURIComponent(S.symbol)}&side=${S.side}&volume=${vol}&sl=${sl}`);
      const entry = S.tick ? (S.side === "buy" ? S.tick.ask : S.tick.bid) : null;
      const parts = [], warn = [];
      if (r.risk) {
        parts.push(`Risiko ${T.fmtMoney(r.risk.money)} (${r.risk.pct_of_equity ?? "—"}%)`);
        if (r.risk.pct_of_equity > 2) warn.push("di atas 2% ekuitas");
      }
      parts.push(`R:R ${rrText(entry, sl, numOf("tp"))}`);
      if (r.stops_level && r.stop_distance < r.stops_level) warn.push(`SL terlalu dekat, minimal ${r.stops_level}`);
      box.hidden = false;
      box.textContent = parts.join(" · ") + (warn.length ? " · ⚠ " + warn.join("; ") : "");
      box.className = "riskline" + (warn.length ? " warn" : "");
    } catch {
      box.hidden = true;
    }
  }

  T.openModify = (ticket) => {
    const p = S.positions.find((x) => x.ticket === ticket);
    if (!p) { T.setBanner("Posisi itu sudah tidak terbuka."); return; }
    S.modifyTicket = ticket;
    $("mod-info").textContent = `${p.side.toUpperCase()} ${p.volume} lot ${p.symbol} · buka ${T.fmtPx(p.open_price)} · ticket ${p.ticket}`;
    $("mod-sl").value = p.sl ?? "";
    $("mod-tp").value = p.tp ?? "";
    $("mod-problems").hidden = true;
    $("modifymodal").hidden = false;
    $("mod-sl").focus();
  };

  T.closeModify = () => {
    $("modifymodal").hidden = true;
    S.modifyTicket = null;
  };

  T.previewModify = async () => {
    if (!S.modifyTicket) return;
    const box = $("mod-problems");
    box.hidden = true;
    try {
      const r = await T.api("/api/mt5/modify/preview", { method: "POST", body: { ticket: S.modifyTicket, sl: numOf("mod-sl"), tp: numOf("mod-tp") } });
      T.closeModify();
      const p = r.position, c = r.change;
      T.openModal("Ubah SL / TP", [
        `${p.side.toUpperCase()} ${p.volume} lot ${p.symbol} (ticket ${p.ticket})`,
        `SL: ${p.sl ?? "—"} → ${c.sl ?? "dihapus"}`,
        `TP: ${p.tp ?? "—"} → ${c.tp ?? "dihapus"}`,
        riskText(r.risk),
        S.live ? "⚠ Akun riil." : "",
      ], S.live, () => T.api("/api/mt5/modify/confirm", { method: "POST", body: { preview_id: r.preview_id } }),
      Date.now() + r.expires_in * 1000);
    } catch (e) {
      box.hidden = false;
      box.replaceChildren(...T.problemList("Tidak bisa diubah", e.message));
    }
  };

  T.previewCancel = async (ticket) => {
    try {
      const r = await T.api("/api/mt5/cancel/preview", { method: "POST", body: { ticket } });
      const o = r.order;
      T.openModal("Batalkan order tertunda", [
        `${o.side.toUpperCase()} ${o.volume} lot ${o.symbol} (ticket ${o.ticket})`,
        `Harga order: ${T.fmtPx(o.price)}`,
        "Order akan dihapus. Tidak ada posisi yang ditutup.",
        S.live ? "⚠ Akun riil." : "",
      ], S.live, () => T.api("/api/mt5/cancel/confirm", { method: "POST", body: { preview_id: r.preview_id } }),
      Date.now() + r.expires_in * 1000);
    } catch (e) { T.setBanner(e.message); }
  };

  T.showProposal = async () => {
    const box = $("proposal");
    box.hidden = false;
    box.className = "previewbox";
    box.textContent = "Menghitung usulan...";
    try {
      const r = await T.api("/api/quant/propose?symbol=" + encodeURIComponent(S.symbol));
      const p = r.proposal || {};
      S.proposal = r.proposal ? { ...r.proposal, lots: r.lots, ok: r.ok } : null;
      const lines = [`${r.symbol} · bar ${r.bar_time || "-"} · rezim ${p.regime || "-"} (P trend ${T.fmt2(p.p_trend)})`];
      if (r.ok && p.side) {
        lines.push(`${p.side.toUpperCase()} · lot ${r.lots} · risiko ${r.risk_money}`);
        lines.push(`Entry ${T.fmtPx(p.entry)} · SL ${T.fmtPx(p.sl)} · TP ${T.fmtPx(p.tp)}`);
        lines.push("Alasan: " + (p.reasons || []).join("; "));
      } else {
        lines.push("Tidak ada usulan: " + (r.blocked || (r.risk_reasons || []).join("; ") || p.blocked || "-"));
        S.proposal = null;
      }
      if (r.notes && r.notes.length) lines.push("Catatan: " + r.notes.join("; "));
      $("fill").disabled = !S.proposal;
      box.className = "previewbox " + (S.proposal ? "ok" : "bad");
      box.replaceChildren(...lines.map((line) => h("div", null, line)));
    } catch (e) {
      box.className = "previewbox bad";
      box.textContent = e.message;
      S.proposal = null;
      $("fill").disabled = true;
    }
  };

  T.fillFromProposal = () => {
    const p = S.proposal;
    if (!p) return;
    $("vol").value = p.lots;
    $("sl").value = p.sl;
    $("tp").value = p.tp;
    $("comment").value = ("quant " + p.side).slice(0, 31);
    T.setSide(p.side);
  };

  const percent = (v) => (v === null || v === undefined ? "-" : (v * 100).toFixed(1) + "%");

  function backtestSummary(x) {
    const mc = x.monte_carlo || {};
    const rows = [["Trade", x.trades ?? 0], ["Win rate", percent(x.win_rate)], ["Expectancy", `${x.expectancy_r ?? "-"} R`],
      ["Profit factor", x.profit_factor ?? "-"], ["Kalah beruntun", x.max_losing_streak ?? "-"], ["DD p95 (MC)", `${mc.max_drawdown_r_p95 ?? "-"} R`]];
    return h("div", { class: "kvgrid" }, rows.map(([label, value]) => [h("div", null, label), h("b", null, value)]));
  }

  T.runBacktest = async () => {
    if (!S.symbol) { T.setBanner("Pilih simbol dulu."); return; }
    const box = $("btresult");
    box.hidden = false;
    box.className = "previewbox";
    box.textContent = "Menjalankan backtest...";
    $("backtest").disabled = true;
    try {
      const r = await T.api(`/api/quant/backtest?symbol=${encodeURIComponent(S.symbol)}&count=${$("bt-count").value}`);
      const x = r.summary || {};
      box.className = "previewbox ok";
      box.replaceChildren(backtestSummary(x),
        x.note ? h("div", { class: "hint" }, x.note) : "",
        h("div", { class: "hint" }, `${r.bars} bar M5 · spread ${T.fmtPx(r.spread_used)} · komisi ${r.commission_r} R`),
        h("div", { class: "note-warn" }, r.warning));
    } catch (e) {
      box.className = "previewbox bad";
      box.textContent = e.message;
    } finally {
      $("backtest").disabled = !S.connected;
    }
  };

  function scalpRow(label, x) {
    const pct = (v) => (v === null || v === undefined ? "-" : (v * 100).toFixed(1) + "%");
    return [h("div", null, label), h("b", null, pct(x.win_rate)), h("b", null, pct(x.breakeven_win_rate)),
      h("b", { class: T.moneyClass(x.total_r ?? 0) }, `${x.total_r ?? "-"} R`), h("b", null, x.baskets ?? 0)];
  }

  T.runScalp = async () => {
    if (!S.symbol) { T.setBanner("Pilih simbol dulu."); return; }
    const box = $("scalpresult");
    const llm = $("scalp-llm").checked;
    box.hidden = false;
    box.className = "previewbox";
    box.textContent = llm ? "Menjalankan backtest, LLM ikut mengatur..." : "Menjalankan backtest...";
    $("scalp").disabled = true;
    try {
      const r = await T.api(`/api/quant/scalp/backtest?symbol=${encodeURIComponent(S.symbol)}&llm=${llm}`);
      const x = r.summary;
      const profitable = (x.expectancy_r ?? 0) > 0;
      const reviews = r.reviews.map((v) => h("div", { class: "hint" }, `Setelah ${v.after}: ${JSON.stringify(v.changed)} ${v.why}`));
      box.className = "previewbox " + (profitable ? "ok" : "bad");
      box.replaceChildren(
        h("div", { class: "scalpgrid" }, ["", "Menang", "Impas", "Total", "Basket"].map((text) => h("div", { class: "hint" }, text)),
          scalpRow("Ensemble", x), scalpRow("Entry acak", r.random_entries)),
        h("div", { class: "hint" }, `${r.bars} bar · ${r.source} · spread ${T.fmtPx(x.spread)}` + (r.llm.used ? ` · LLM ${r.llm.llm_calls} kali, gagal ${r.llm.llm_failures}` : "")),
        S.backend === "sim" ? h("div", { class: "note-warn" }, "Harga simulator buatan, bukan pasar. Angka ini tidak berarti apa-apa.") : "",
        x.resolution_warning ? h("div", { class: "note-warn" }, x.resolution_warning) : "",
        ...reviews, h("div", { class: "note-warn" }, r.warning));
    } catch (e) {
      box.className = "previewbox bad";
      box.textContent = e.message;
    } finally {
      $("scalp").disabled = !S.connected;
    }
  };

  let pairData = null;

  function setEaPill(text, cls, title) {
    const el = $("ea");
    el.textContent = text;
    el.className = "pill " + cls;
    el.title = title || "";
  }

  T.refreshBridge = async () => {
    if (S.status && S.status.token_required && !T.token()) { setEaPill("EA: isi token", "off", "Isi token dulu"); return; }
    try {
      const b = await T.api("/api/quant/bridge");
      if (!b.paired) setEaPill("EA: belum dipasang", "off", "Buka Koneksi lalu Pasang EA");
      else if (b.ea_online && !b.trading_allowed) setEaPill("Trading mati di EA", "warn", "AllowTrading di chart masih mati. Tekan F7 di chart MT4, tab Inputs.");
      else if (b.ea_outdated) setEaPill("EA versi lama", "warn", "Lepas Mt4Bridge dari chart lalu pasang lagi");
      else if (b.ea_online) setEaPill("EA online", b.live ? "live" : "demo", `Akun ${b.account ?? "-"} · pesan terakhir ${b.last_message_s_ago} s lalu`);
      else setEaPill("EA offline", "warn", "Sudah dipasang, tapi tidak ada pesan dari EA dalam 15 detik");
    } catch (e) {
      setEaPill("EA: error", "warn", e.message);
    }
  };

  T.openPair = async (rotate = false) => {
    if (S.status && S.status.token_required && !T.token()) { T.setBanner("Isi token dulu."); return; }
    $("pairmodal").hidden = false;
    $("pair-msg").textContent = rotate ? "Membuat secret baru..." : "Menyiapkan...";
    try {
      pairData = await T.api("/api/quant/bridge/pair" + (rotate ? "?rotate=true" : ""), { method: "POST" });
      $("pair-steps").replaceChildren(...pairData.steps.map((step) => h("li", null, step)));
      $("pair-msg").textContent = rotate ? "Secret baru dibuat. EA lama harus diganti presetnya." : "Secret sudah dibuat dan disimpan di server.";
    } catch (e) {
      $("pair-msg").textContent = e.message;
    }
  };

  T.downloadPreset = () => {
    if (!pairData) return;
    const a = h("a", { href: URL.createObjectURL(new Blob([pairData.preset], { type: "text/plain" })), download: pairData.preset_name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  };

  T.copySecret = async () => {
    if (!pairData) return;
    try {
      await navigator.clipboard.writeText(pairData.secret);
      $("pair-msg").textContent = "Secret disalin.";
    } catch {
      $("pair-msg").textContent = "Tidak bisa menyalin otomatis; unduh preset saja.";
    }
  };
})();
