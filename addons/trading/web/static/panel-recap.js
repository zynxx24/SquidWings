/* Daily recap: the closed trades of the last N days (the "Hari" box), one card per day.

   Each card says the day's P/L in its colour, how many trades won, a ring for the win rate, a bar per trade, and the
   best trade; "Bagikan" turns the day into a share card (panel-share.js, kind "daily"). Above the cards: the period's
   total, winning days, the best day, the current streak, and a strip of squares, one per day, green or red by P/L.
   Data: S.deals from /api/mt5/history (closed deals with their profit); nothing is sent anywhere. */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;
  const SVG = "http://www.w3.org/2000/svg";
  const DAY_MS = 86400000;

  const money = (v, cur) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toLocaleString("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${cur ? " " + cur : ""}`;
  const dayKey = (t) => { const d = new Date(t * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  const dayName = (key) => {
    const [y, m, d] = key.split("-").map(Number);
    const date = new Date(y, m - 1, d);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const diff = Math.round((today - date) / DAY_MS);
    if (diff === 0) return "Hari ini";
    if (diff === 1) return "Kemarin";
    return date.toLocaleDateString("id-ID", { weekday: "short", day: "numeric", month: "short" });
  };
  const svg = (tag, attrs) => { const el = document.createElementNS(SVG, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); return el; };

  /** Closed trades grouped per local day, newest day first, with the numbers each card shows. */
  T.recapDays = () => {
    const closed = (S.deals || []).filter((d) => d.profit !== null && d.profit !== undefined && Number(d.profit) !== 0);
    const map = new Map();
    for (const d of closed) {
      const key = dayKey(d.time);
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(d);
    }
    return [...map.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1)).map(([key, deals]) => {
      deals.sort((a, b) => a.time - b.time);
      const pnl = deals.reduce((s, d) => s + Number(d.profit), 0);
      const wins = deals.filter((d) => d.profit > 0).length;
      const best = deals.reduce((b, d) => (!b || d.profit > b.profit ? d : b), null);
      const worst = deals.reduce((b, d) => (!b || d.profit < b.profit ? d : b), null);
      const symbols = [...new Set(deals.map((d) => d.symbol))];
      let run = 0;
      const curve = [0, ...deals.map((d) => (run += Number(d.profit)))];
      return { key, name: dayName(key), deals, pnl, wins, losses: deals.length - wins, rate: deals.length ? wins / deals.length : 0, best, worst, symbols, curve };
    });
  };

  function ring(rate) {
    const r = 22, c = 2 * Math.PI * r;
    const el = svg("svg", { viewBox: "0 0 56 56", class: "rc-ring", "aria-hidden": "true" });
    el.append(svg("circle", { cx: 28, cy: 28, r, class: "rc-ring-bg" }),
      Object.assign(svg("circle", { cx: 28, cy: 28, r, class: "rc-ring-fg", "stroke-dasharray": `${c * rate} ${c}`, transform: "rotate(-90 28 28)" }), {}));
    return el;
  }

  function bars(deals) {
    const max = Math.max(...deals.map((d) => Math.abs(d.profit)), 1e-9);
    const shown = deals.slice(-24);
    const el = svg("svg", { viewBox: `0 0 ${shown.length * 10} 40`, preserveAspectRatio: "none", class: "rc-bars", "aria-hidden": "true" });
    el.append(svg("line", { x1: 0, x2: shown.length * 10, y1: 20, y2: 20, class: "rc-zero" }));
    shown.forEach((d, i) => {
      const hgt = Math.max(1.5, (Math.abs(d.profit) / max) * 18);
      el.append(svg("rect", { x: i * 10 + 2, y: d.profit >= 0 ? 20 - hgt : 20, width: 6, height: hgt, rx: 1.5, class: d.profit >= 0 ? "up" : "down",
        style: `--i:${i}` }));
    });
    return el;
  }

  function dayCard(day, cur, i) {
    const up = day.pnl >= 0;
    const share = h("button", { class: "btn mini rc-share", type: "button", title: "Kartu rekap hari ini untuk dibagikan" }, "Bagikan");
    share.onclick = () => T.shareDaily && T.shareDaily(day);
    const ringBox = h("div", { class: "rc-ringbox" }, ring(day.rate), h("b", null, `${Math.round(day.rate * 100)}%`));
    return h("article", { class: `rc-card ${up ? "up" : "down"}`, style: `--i:${i}` },
      h("header", { class: "rc-head" }, h("span", { class: "rc-day" }, day.name), h("span", { class: "rc-count" }, `${day.deals.length} trade`), share),
      h("div", { class: "rc-main" },
        h("div", null, h("small", null, "P/L tertutup"), h("b", { class: "rc-pnl" }, money(day.pnl, cur)),
          h("span", { class: "rc-sub" }, `${day.wins} menang · ${day.losses} kalah · ${day.symbols.slice(0, 3).join(", ")}${day.symbols.length > 3 ? "…" : ""}`)),
        ringBox),
      bars(day.deals),
      h("footer", { class: "rc-foot" },
        day.best ? h("span", null, "Terbaik ", h("b", { class: "pos" }, `${day.best.symbol} ${money(day.best.profit)}`)) : null,
        day.worst && day.worst.profit < 0 ? h("span", null, "Terburuk ", h("b", { class: "neg" }, `${day.worst.symbol} ${money(day.worst.profit)}`)) : null));
  }

  function summary(days, cur) {
    const total = days.reduce((s, d) => s + d.pnl, 0);
    const green = days.filter((d) => d.pnl > 0).length;
    const best = days.reduce((b, d) => (!b || d.pnl > b.pnl ? d : b), null);
    const trades = days.reduce((s, d) => s + d.deals.length, 0);
    const wins = days.reduce((s, d) => s + d.wins, 0);
    let streak = 0;
    for (const d of days) { if ((d.pnl > 0) === (days[0].pnl > 0)) streak += 1; else break; }
    const span = Math.max(1, Math.min(365, Number($("days").value || 7)));
    const byKey = new Map(days.map((d) => [d.key, d]));
    const max = Math.max(...days.map((d) => Math.abs(d.pnl)), 1e-9);
    const cells = [];
    for (let k = span - 1; k >= 0; k--) {
      const key = dayKey(Date.now() / 1000 - k * 86400);
      const d = byKey.get(key);
      const strength = d ? 0.25 + 0.75 * Math.abs(d.pnl) / max : 0;
      cells.push(h("i", { class: `rc-cell ${d ? (d.pnl >= 0 ? "up" : "down") : ""}`, style: `--a:${strength.toFixed(2)}`,
        title: d ? `${d.name}: ${money(d.pnl, cur)} · ${d.deals.length} trade` : `${dayName(key)}: tidak ada trade` }));
    }
    const stat = (label, value, cls) => h("div", { class: "rc-stat" }, h("small", null, label), h("b", { class: cls || "" }, value));
    return h("div", { class: "rc-summary" },
      stat(`P/L ${span} hari`, money(total, cur), total >= 0 ? "pos" : "neg"),
      stat("Hari hijau", `${green}/${days.length}`),
      stat("Win rate", trades ? `${Math.round((wins / trades) * 100)}%` : "–"),
      stat("Hari terbaik", best ? `${best.name} ${money(best.pnl)}` : "–", best && best.pnl >= 0 ? "pos" : ""),
      stat("Beruntun", days.length ? `${streak} hari ${days[0].pnl > 0 ? "hijau" : "merah"}` : "–", days.length ? (days[0].pnl > 0 ? "pos" : "neg") : ""),
      h("div", { class: "rc-strip", role: "img", "aria-label": `P/L per hari, ${span} hari terakhir` }, cells));
  }

  T.renderRecap = () => {
    const box = $("recap");
    if (!S.connected) { box.replaceChildren(h("p", { class: "rc-empty" }, "Hubungkan akun untuk melihat rekap harian.")); return; }
    const days = T.recapDays();
    const cur = (S.account || {}).currency || "";
    if (!days.length) {
      box.replaceChildren(h("p", { class: "rc-empty" }, `Belum ada trade tertutup dalam ${$("days").value || 7} hari terakhir. Ubah kotak Hari untuk melihat lebih jauh.`));
      return;
    }
    box.replaceChildren(summary(days, cur), h("div", { class: "rc-grid" }, days.map((d, i) => dayCard(d, cur, i))));
  };
})();
