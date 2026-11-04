/* The bottom tables and the confirm dialog. */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;

  const th = (text, num) => h("th", { class: num ? "num" : null }, text);
  const num = (value, cls) => h("td", { class: "num" + (cls ? " " + cls : "") }, value);
  const empty = (columns, text) => h("tr", null, h("td", { class: "empty", colspan: columns }, text));
  const sideCell = (side) => h("td", { class: T.sideClass(side) }, side.toUpperCase());

  function positionRow(p) {
    const d = p.symbol === S.symbol ? T.digits() : undefined;
    return h("tr", { "data-symbol": p.symbol },
      h("td", null, p.symbol), sideCell(p.side), num(p.volume), num(T.fmtPx(p.open_price, d)), num(T.fmtPx(p.price, d)),
      num(T.fmtPx(p.sl, d)), num(T.fmtPx(p.tp, d)), num(T.fmtMoney(p.profit), T.moneyClass(p.profit)),
      h("td", null, T.fmtTime(p.time)),
      h("td", { class: "actions" },
        h("input", { class: "vol-in", type: "number", step: S.spec ? S.spec.volume_step : 0.01, min: S.spec ? S.spec.volume_min : 0.01,
          max: p.volume, value: p.volume, "data-vol-of": p.ticket, title: "Volume yang ditutup (lot)" }),
        h("button", { class: "btn mini", "data-close": p.ticket }, "Tutup"),
        h("button", { class: "btn mini", "data-modify": p.ticket }, "SL/TP")));
  }

  function orderRow(o) {
    return h("tr", { "data-symbol": o.symbol },
      h("td", null, o.symbol), sideCell(o.side), num(o.volume), num(T.fmtPx(o.price)), num(T.fmtPx(o.sl)), num(T.fmtPx(o.tp)),
      h("td", null, T.fmtTime(o.time)),
      h("td", null, h("button", { class: "btn mini", "data-cancel": o.ticket }, "Batalkan")));
  }

  function dealRow(d) {
    return h("tr", { "data-symbol": d.symbol },
      h("td", null, d.symbol), sideCell(d.side), num(d.volume), num(T.fmtPx(d.price)),
      num(T.fmtMoney(d.profit), T.moneyClass(d.profit)), h("td", null, T.fmtTime(d.time)), h("td", null, d.comment));
  }

  const TABLES = {
    positions: { heads: [["Simbol"], ["Sisi"], ["Volume", 1], ["Harga buka", 1], ["Harga", 1], ["SL", 1], ["TP", 1], ["Profit", 1], ["Waktu"], ["Aksi"]],
      rows: () => S.positions, row: positionRow, none: "Tidak ada posisi terbuka." },
    orders: { heads: [["Simbol"], ["Sisi"], ["Volume", 1], ["Harga", 1], ["SL", 1], ["TP", 1], ["Waktu"], [""]],
      rows: () => S.orders, row: orderRow, none: "Tidak ada order tertunda." },
    history: { heads: [["Simbol"], ["Sisi"], ["Volume", 1], ["Harga", 1], ["Profit", 1], ["Waktu"], ["Komentar"]],
      rows: () => S.deals, row: dealRow, none: "Belum ada riwayat." },
  };

  T.renderTable = () => {
    const table = $("table");
    // A refresh must not wipe a partial-close volume while it is being typed.
    if (document.activeElement && document.activeElement.matches("#table input.vol-in")) return;
    const count = (n) => (n ? ` (${n})` : "");
    document.querySelector('[data-tab="positions"]').textContent = "Posisi" + count(S.connected ? S.positions.length : 0);
    document.querySelector('[data-tab="orders"]').textContent = "Order tertunda" + count(S.connected ? S.orders.length : 0);
    const key = JSON.stringify([S.tab, S.connected, S.positions, S.orders, S.tab === "history" ? S.deals : null, S.symbol]);
    if (key === S.tableKey) return;      // rebuilding drops hover and focus, so skip when nothing changed
    S.tableKey = key;
    const spec = TABLES[S.tab];
    const heads = h("thead", null, h("tr", null, spec.heads.map(([text, right]) => th(text, right))));
    const rows = S.connected ? spec.rows() : [];
    const body = rows.length ? rows.map(spec.row) : empty(spec.heads.length, S.connected ? spec.none : "Belum terhubung.");
    table.replaceChildren(heads, h("tbody", null, body));
  };

  T.openModal = (title, lines, live, onOk, expires) => {
    S.pending = onOk;
    S.pendingExpires = expires;
    $("m-title").textContent = title;
    $("m-body").replaceChildren(...lines.filter(Boolean).map((line) => h("div", null, line)));
    $("m-ok").disabled = false;
    $("modal").querySelector(".dialog").classList.toggle("live", !!live);
    $("modal").hidden = false;
    T.tickModal();
  };

  T.closeModal = () => {
    $("modal").hidden = true;
    S.pending = null;
  };

  T.tickModal = () => {
    if ($("modal").hidden) return;
    const left = Math.max(0, Math.ceil((S.pendingExpires - Date.now()) / 1000));
    $("m-timer").textContent = left > 0 ? `Berlaku ${left} detik lagi.` : "Preview sudah kedaluwarsa. Tutup lalu preview ulang.";
    $("m-ok").disabled = left <= 0;
  };

  T.confirmPending = async () => {
    if (!S.pending) return;
    const fn = S.pending;
    $("m-ok").disabled = true;
    try {
      const r = await fn();
      T.closeModal();
      const res = r.result || {};
      const retcode = res.retcode ?? "-";
      T.setBanner(res.ok ? `Berhasil: ${res.comment || "terkirim"} (retcode ${retcode})` : `Ditolak: ${res.comment || "gagal"} (retcode ${retcode})`, res.ok ? "" : "live");
      $("previewbox").hidden = true;
      await T.refreshAll();
    } catch (e) {
      $("m-body").replaceChildren(h("div", { class: "neg" }, e.message));
      $("m-ok").disabled = false;
    }
  };

  async function previewClose(ticket, volume) {
    if (!(volume > 0)) { T.setBanner("Isi volume yang akan ditutup."); return; }
    try {
      const r = await T.api("/api/mt5/close/preview", { method: "POST", body: { ticket, volume } });
      const p = r.position;
      T.openModal("Tutup posisi", [
        `Tutup ${r.volume} lot ${p.side.toUpperCase()} ${p.symbol} (ticket ${p.ticket})`,
        `Profit saat ini: ${T.fmtMoney(p.profit)}`,
        S.live ? "⚠ Akun riil." : "",
      ], S.live, () => T.api("/api/mt5/close/confirm", { method: "POST", body: { preview_id: r.preview_id } }),
      Date.now() + r.expires_in * 1000);
    } catch (e) { T.setBanner(e.message); }
  }

  T.onTableClick = (e) => {
    const close = e.target.closest("button[data-close]");
    const modify = e.target.closest("button[data-modify]");
    const cancel = e.target.closest("button[data-cancel]");
    if (close) {
      const ticket = close.dataset.close;
      const input = $("table").querySelector(`input[data-vol-of="${ticket}"]`);
      previewClose(Number(ticket), Number(input ? input.value : 0));
    } else if (modify) {
      T.openModify(Number(modify.dataset.modify));
    } else if (cancel) {
      T.previewCancel(Number(cancel.dataset.cancel));
    } else if (!e.target.closest("input")) {
      const row = e.target.closest("tr[data-symbol]");
      if (row && row.dataset.symbol !== S.symbol) T.selectSymbol(row.dataset.symbol);
    }
  };
})();
