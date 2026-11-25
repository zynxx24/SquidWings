/* Share cards, like an exchange's: one trade, the whole portfolio, or a simulation result, drawn on
   a canvas as a PNG to download, copy, or send. Everything is drawn here; nothing leaves the page
   until the user saves or shares it. */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;
  const W = 1080, H = 1350;
  const STYLES = {
    gelap: { bg: ["#0b1220", "#111c33"], glow: "#1f6feb", text: "#eef3ff", muted: "#8b98b8", line: "#22304f", up: "#22c55e", down: "#ef4444", chip: "#18233d" },
    neon: { bg: ["#12002b", "#04112b"], glow: "#ff2bd6", text: "#ffffff", muted: "#b9a7e6", line: "#3a2266", up: "#2cf5a0", down: "#ff4d6d", chip: "#24104a" },
    terang: { bg: ["#f7f4ee", "#ffffff"], glow: "#0d7a6f", text: "#16181d", muted: "#6b665d", line: "#e4e1da", up: "#0f8a4f", down: "#c2361f", chip: "#efebe3" },
    bursa: { bg: ["#0b0e11", "#181a20"], glow: "#f0b90b", text: "#eaecef", muted: "#848e9c", line: "#2b3139", up: "#0ecb81", down: "#f6465d", chip: "#1e2329" },
    biru: { bg: ["#05070d", "#0d1730"], glow: "#2f6bff", text: "#f3f6ff", muted: "#8f9bbd", line: "#1b2747", up: "#1ed98c", down: "#ff5470", chip: "#121c36" },
  };
  const STYLE_KEY = "share-card-style";
  let current = null;     // { kind, data }
  let style = (() => { try { return localStorage.getItem(STYLE_KEY) || "gelap"; } catch { return "gelap"; } })();
  let hideMoney = false;

  // ── drawing helpers ───────────────────────────────────────────────────
  function round(ctx, x, y, w, hh, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + hh, r); ctx.arcTo(x + w, y + hh, x, y + hh, r);
    ctx.arcTo(x, y + hh, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }
  const font = (size, weight = 600) => `${weight} ${size}px "Plus Jakarta Sans", Inter, "Segoe UI", system-ui, -apple-system, Roboto, sans-serif`;

  function background(ctx, st) {
    const g = ctx.createLinearGradient(0, 0, W, H);
    g.addColorStop(0, st.bg[0]); g.addColorStop(1, st.bg[1]);
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    const glow = ctx.createRadialGradient(W * 0.85, H * 0.12, 20, W * 0.85, H * 0.12, 620);
    glow.addColorStop(0, st.glow + "66"); glow.addColorStop(1, st.glow + "00");
    ctx.fillStyle = glow; ctx.fillRect(0, 0, W, H);
    const glow2 = ctx.createRadialGradient(W * 0.05, H * 0.95, 10, W * 0.05, H * 0.95, 520);
    glow2.addColorStop(0, st.glow + "33"); glow2.addColorStop(1, st.glow + "00");
    ctx.fillStyle = glow2; ctx.fillRect(0, 0, W, H);
    // a faint grid, like a chart behind glass
    ctx.strokeStyle = st.line; ctx.globalAlpha = 0.35; ctx.lineWidth = 1;
    for (let x = 0; x <= W; x += 90) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
    for (let y = 0; y <= H; y += 90) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
    ctx.globalAlpha = 1;
  }

  function brand(ctx, st, right) {
    round(ctx, 72, 72, 64, 64, 18); ctx.fillStyle = st.glow; ctx.fill();
    ctx.fillStyle = "#ffffff"; ctx.font = font(36, 800); ctx.textAlign = "center"; ctx.fillText("S", 104, 117);
    ctx.textAlign = "left"; ctx.fillStyle = st.text; ctx.font = font(34, 700); ctx.fillText("SquidWings", 156, 116);
    if (right) {
      ctx.font = font(26, 600); ctx.textAlign = "right"; ctx.fillStyle = st.muted; ctx.fillText(right, W - 72, 114); ctx.textAlign = "left";
    }
  }

  function chip(ctx, st, x, y, text, color) {
    ctx.font = font(30, 800);
    const w = ctx.measureText(text).width + 44;
    round(ctx, x, y, w, 58, 29); ctx.fillStyle = color + "26"; ctx.fill();
    ctx.fillStyle = color; ctx.fillText(text, x + 22, y + 40);
    return x + w + 16;
  }

  function sparkline(ctx, st, values, x, y, w, hh, color) {
    if (!values || values.length < 2) return;
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const px = (i) => x + (i / (values.length - 1)) * w, py = (v) => y + hh - ((v - lo) / span) * hh;
    const fill = ctx.createLinearGradient(0, y, 0, y + hh);
    fill.addColorStop(0, color + "55"); fill.addColorStop(1, color + "00");
    ctx.beginPath(); ctx.moveTo(px(0), y + hh);
    values.forEach((v, i) => ctx.lineTo(px(i), py(v)));
    ctx.lineTo(px(values.length - 1), y + hh); ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
    ctx.beginPath(); values.forEach((v, i) => (i ? ctx.lineTo(px(i), py(v)) : ctx.moveTo(px(i), py(v))));
    ctx.strokeStyle = color; ctx.lineWidth = 5; ctx.lineJoin = "round"; ctx.stroke();
    ctx.beginPath(); ctx.arc(px(values.length - 1), py(values[values.length - 1]), 10, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
  }

  function rows(ctx, st, items, y) {
    for (const [label, value] of items) {
      ctx.font = font(30, 500); ctx.fillStyle = st.muted; ctx.textAlign = "left"; ctx.fillText(label, 72, y);
      ctx.font = font(32, 700); ctx.fillStyle = st.text; ctx.textAlign = "right"; ctx.fillText(value, W - 72, y);
      ctx.textAlign = "left";
      ctx.strokeStyle = st.line; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(72, y + 28); ctx.lineTo(W - 72, y + 28); ctx.stroke();
      y += 84;
    }
    return y;
  }

  function footer(ctx, st, note) {
    const stamp = new Date().toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
    ctx.font = font(24, 500); ctx.fillStyle = st.muted;
    ctx.fillText(stamp, 72, H - 72);
    ctx.textAlign = "right"; ctx.fillText(note, W - 72, H - 72); ctx.textAlign = "left";
  }

  const money = (v, currency) => (hideMoney ? "••••" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toLocaleString("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency || ""}`.trim());
  const plain = (v) => (hideMoney ? "••••" : Number(v).toLocaleString("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const accountNote = () => (S.backend === "sim" ? "AKUN SIMULASI" : S.live ? "AKUN RIIL" : "AKUN DEMO");
  function closes(symbol, n = 90) {
    if (symbol !== S.symbol || !T.chart || !T.chart.bars.length) return null;
    return T.chart.bars.slice(-n).map((b) => b.c);
  }

  // ── the three cards ───────────────────────────────────────────────────
  function drawTrade(ctx, st, p) {
    const side = p.side === "buy" ? 1 : -1;
    const entry = Number(p.open_price ?? p.price), now = Number(p.price ?? p.open_price);
    const movePct = entry ? ((now - entry) / entry) * 100 * side : 0;
    const up = (p.profit ?? movePct) >= 0, color = up ? st.up : st.down;
    brand(ctx, st, p.closed ? "Trade selesai" : "Posisi terbuka");
    let x = chip(ctx, st, 72, 200, p.symbol, st.text);
    x = chip(ctx, st, x, 200, side > 0 ? "LONG" : "SHORT", side > 0 ? st.up : st.down);
    chip(ctx, st, x, 200, `${p.volume} lot`, st.muted);
    ctx.font = font(32, 600); ctx.fillStyle = st.muted; ctx.fillText(p.closed ? "Hasil" : "Profit mengambang", 72, 360);
    // A closed deal carries its profit but not its entry, so with amounts hidden it shows the outcome.
    const big = !hideMoney && p.profit !== undefined ? money(p.profit, p.currency)
      : p.closed ? (up ? "PROFIT" : "LOSS") : `${movePct >= 0 ? "+" : "−"}${Math.abs(movePct).toFixed(2)}%`;
    ctx.font = font(big.length > 12 ? 112 : 150, 800); ctx.fillStyle = color;
    ctx.fillText(big, 66, 510);
    ctx.font = font(40, 700); ctx.fillStyle = color;
    if (p.profit !== undefined && !hideMoney && entry && !p.closed) ctx.fillText(`${movePct >= 0 ? "+" : "−"}${Math.abs(movePct).toFixed(2)}% pergerakan harga`, 72, 580);
    sparkline(ctx, st, closes(p.symbol), 72, 640, W - 144, 230, color);
    rows(ctx, st, [
      [p.closed ? "Harga keluar" : "Harga masuk", T.fmtPx(p.closed ? now : entry, p.symbol === S.symbol ? T.digits() : undefined)],
      [p.closed ? "Waktu" : "Harga sekarang", p.closed ? T.fmtTime(p.time) : T.fmtPx(now, p.symbol === S.symbol ? T.digits() : undefined)],
      ...(p.closed ? [] : [["Dibuka", T.fmtTime(p.time)]]),
    ], 960);
    footer(ctx, st, accountNote());
  }

  function drawPortfolio(ctx, st) {
    const a = S.account || {};
    const deals = (S.deals || []).filter((d) => d.profit !== 0 && d.profit !== null && d.profit !== undefined);
    const realized = deals.reduce((sum, d) => sum + Number(d.profit || 0), 0);
    const wins = deals.filter((d) => d.profit > 0).length;
    const floating = Number(a.profit || 0);
    const color = realized + floating >= 0 ? st.up : st.down;
    brand(ctx, st, "Portofolio");
    ctx.font = font(32, 600); ctx.fillStyle = st.muted; ctx.fillText("Ekuitas", 72, 270);
    ctx.font = font(120, 800); ctx.fillStyle = st.text; ctx.fillText(hideMoney ? "••••••" : plain(a.equity ?? 0), 66, 400);
    ctx.font = font(36, 700); ctx.fillStyle = st.muted; ctx.fillText(a.currency || "", 72, 455);
    // cumulative P/L of the closed trades, oldest first
    let run = 0;
    const curve = [0, ...[...deals].sort((x, y) => x.time - y.time).map((d) => (run += Number(d.profit || 0)))];
    sparkline(ctx, st, curve.length > 1 ? curve : null, 72, 520, W - 144, 250, color);
    if (curve.length < 2) { ctx.font = font(30, 500); ctx.fillStyle = st.muted; ctx.fillText("Belum ada trade tertutup di periode ini.", 72, 660); }
    const days = Number($("days").value || 7);
    let y = rows(ctx, st, [
      [`P/L tertutup ${days} hari`, money(realized, a.currency)],
      ["P/L mengambang", money(floating, a.currency)],
      ["Trade menang", deals.length ? `${wins}/${deals.length} · ${Math.round((wins / deals.length) * 100)}%` : "–"],
      ["Posisi terbuka", String((S.positions || []).length)],
    ], 860);
    const best = deals.reduce((b, d) => (!b || d.profit > b.profit ? d : b), null);
    if (best) { ctx.font = font(28, 600); ctx.fillStyle = st.muted; ctx.fillText(`Trade terbaik: ${best.symbol} ${money(best.profit, a.currency)}`, 72, y + 10); }
    footer(ctx, st, accountNote());
  }

  function drawSim(ctx, st, r) {
    const f = r.found || r.leader || {};
    const rate = f.win_rate ?? 0, color = rate >= r.target ? st.up : st.down;
    brand(ctx, st, "Simulasi tanpa order");
    let x = chip(ctx, st, 72, 200, r.symbol, st.text);
    chip(ctx, st, x, 200, `tahan ${r.hold_s} dtk`, st.muted);
    ctx.font = font(32, 600); ctx.fillStyle = st.muted; ctx.fillText(f.label || "Aturan terbaik", 72, 360);
    ctx.font = font(170, 800); ctx.fillStyle = color; ctx.fillText(`${Math.round(rate * 100)}%`, 62, 530);
    ctx.font = font(40, 700); ctx.fillStyle = st.text; ctx.fillText(`menang dari ${f.trades ?? 0} trade virtual`, 72, 600);
    // a bar per rule: the race the simulation ran
    const ranking = (r.ranking || []).filter((x2) => x2.trades).slice(0, 7);
    let y = 680;
    for (const row of ranking) {
      const w = (W - 144 - 300) * Math.min(1, row.win_rate || 0);
      ctx.font = font(26, 600); ctx.fillStyle = st.muted; ctx.fillText(row.label.slice(0, 18), 72, y + 30);
      round(ctx, 372, y + 6, W - 144 - 300, 30, 15); ctx.fillStyle = st.chip; ctx.fill();
      round(ctx, 372, y + 6, Math.max(30, w), 30, 15); ctx.fillStyle = row.name === f.name ? color : st.muted + "88"; ctx.fill();
      ctx.font = font(26, 700); ctx.fillStyle = st.text; ctx.textAlign = "right"; ctx.fillText(`${Math.round((row.win_rate || 0) * 100)}%`, W - 72, y + 30); ctx.textAlign = "left";
      y += 56;
    }
    ctx.font = font(26, 500); ctx.fillStyle = st.muted;
    ctx.fillText(`Batas bawah 95%: ${Math.round((f.wilson_low || 0) * 100)}% · bukan jaminan profit`, 72, Math.min(y + 30, H - 130));
    footer(ctx, st, "PAPER TRADING");
  }

  /* A day's recap, laid out like an exchange's PnL card: the day, one huge number, the ROI chip, three stats,
     the day's trades as bars, and a strip of the last days. */
  function drawDaily(ctx, st, day) {
    const a = S.account || {};
    const up = day.pnl >= 0, color = up ? st.up : st.down;
    const [y0, m0, d0] = day.key.split("-").map(Number);
    const date = new Date(y0, m0 - 1, d0).toLocaleDateString("id-ID", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    brand(ctx, st, "Rekap harian");
    let x = chip(ctx, st, 72, 196, date, st.text);
    ctx.font = font(34, 600); ctx.fillStyle = st.muted; ctx.fillText("PnL hari ini", 72, 352);
    const big = hideMoney ? (up ? "PROFIT" : "LOSS") : money(day.pnl, a.currency);
    ctx.font = font(big.length > 13 ? 104 : 140, 800); ctx.fillStyle = color; ctx.fillText(big, 64, 500);
    // ROI against the balance before the day's trades, when the balance is known
    const balance = Number(a.balance || 0);
    const base = balance - day.pnl;
    if (base > 0) chip(ctx, st, 72, 540, `${up ? "▲" : "▼"} ${Math.abs((day.pnl / base) * 100).toFixed(2)}% ROI`, color);
    // three stats in a row
    const stats = [["Trade", String(day.deals.length)], ["Menang", `${day.wins}/${day.deals.length}`], ["Win rate", `${Math.round(day.rate * 100)}%`]];
    const colW = (W - 144) / 3;
    stats.forEach(([label, value], i) => {
      const sx = 72 + i * colW;
      round(ctx, sx, 640, colW - 20, 132, 22); ctx.fillStyle = st.chip; ctx.fill();
      ctx.font = font(26, 600); ctx.fillStyle = st.muted; ctx.fillText(label, sx + 26, 690);
      ctx.font = font(52, 800); ctx.fillStyle = st.text; ctx.fillText(value, sx + 26, 748);
    });
    // the day's trades: one bar each, up green, down red
    const deals = day.deals.slice(-30);
    const max = Math.max(...deals.map((d) => Math.abs(d.profit)), 1e-9);
    const top = 830, mid = 960, bw = (W - 144) / Math.max(deals.length, 1);
    ctx.strokeStyle = st.line; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(72, mid); ctx.lineTo(W - 72, mid); ctx.stroke();
    deals.forEach((d, i) => {
      const hh = Math.max(4, (Math.abs(d.profit) / max) * (mid - top - 10));
      round(ctx, 72 + i * bw + bw * 0.18, d.profit >= 0 ? mid - hh : mid, bw * 0.64, hh, Math.min(8, bw * 0.3));
      ctx.fillStyle = d.profit >= 0 ? st.up : st.down; ctx.fill();
    });
    if (day.best) {
      ctx.font = font(28, 600); ctx.fillStyle = st.muted;
      ctx.fillText(`Terbaik ${day.best.symbol} ${hideMoney ? "" : money(day.best.profit)}`.trim(), 72, 1150);
    }
    // the last seven days as squares
    const recent = (T.recapDays ? T.recapDays() : []).slice(0, 7).reverse();
    const peak = Math.max(...recent.map((d) => Math.abs(d.pnl)), 1e-9);
    recent.forEach((d, i) => {
      const sx = W - 72 - (recent.length - i) * 58;
      round(ctx, sx, 1112, 46, 46, 10);
      ctx.globalAlpha = 0.3 + 0.7 * Math.abs(d.pnl) / peak;
      ctx.fillStyle = d.pnl >= 0 ? st.up : st.down; ctx.fill(); ctx.globalAlpha = 1;
      if (d.key === day.key) { ctx.strokeStyle = st.text; ctx.lineWidth = 4; round(ctx, sx - 4, 1108, 54, 54, 13); ctx.stroke(); }
    });
    footer(ctx, st, accountNote());
  }

  // ── the dialog ────────────────────────────────────────────────────────
  function render() {
    const canvas = document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext("2d");
    const st = STYLES[style] || STYLES.gelap;
    background(ctx, st);
    if (current.kind === "trade") drawTrade(ctx, st, current.data);
    else if (current.kind === "sim") drawSim(ctx, st, current.data);
    else if (current.kind === "daily") drawDaily(ctx, st, current.data);
    else drawPortfolio(ctx, st);
    return canvas;
  }

  function refreshPreview() {
    const canvas = render();
    $("share-img").src = canvas.toDataURL("image/png");
    $("share-dialog").querySelectorAll("[data-style]").forEach((b) => b.classList.toggle("on", b.dataset.style === style));
  }

  function open(kind, data) {
    current = { kind, data };
    $("share-modal").hidden = false;
    refreshPreview();
  }

  const fileName = () => `squidwings-${current.kind}-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "")}.png`;
  const blob = () => new Promise((resolve) => render().toBlob(resolve, "image/png"));

  async function download() {
    const b = await blob();
    const a = h("a", { href: URL.createObjectURL(b), download: fileName() });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  async function copy() {
    try {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": await blob() })]);
      $("share-msg").textContent = "Gambar disalin. Tempel di chat atau media sosial.";
    } catch { $("share-msg").textContent = "Browser ini tidak bisa menyalin gambar; pakai Unduh."; }
  }

  async function send() {
    const file = new File([await blob()], fileName(), { type: "image/png" });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file], title: "SquidWings" }); } catch { /* cancelled */ }
    } else { $("share-msg").textContent = "Berbagi langsung tidak didukung di sini; pakai Unduh atau Salin."; }
  }

  T.shareSim = (r) => open("sim", r);
  T.shareDaily = (day) => open("daily", day);
  T.sharePortfolio = () => open("portfolio", null);
  T.sharePosition = (ticket) => {
    const p = (S.positions || []).find((x) => String(x.ticket) === String(ticket));
    if (p) open("trade", { ...p, currency: (S.account || {}).currency });
  };
  T.shareDeal = (index) => {
    const d = (S.deals || [])[Number(index)];
    if (d) open("trade", { ...d, closed: true, open_price: d.price, currency: (S.account || {}).currency });
  };

  T.initShare = () => {
    $("share-portfolio").onclick = T.sharePortfolio;
    $("share-close").onclick = () => { $("share-modal").hidden = true; $("share-msg").textContent = ""; };
    $("share-download").onclick = download;
    $("share-copy").onclick = copy;
    $("share-send").onclick = send;
    $("share-hide").onchange = (e) => { hideMoney = e.target.checked; refreshPreview(); };
    $("share-dialog").querySelectorAll("[data-style]").forEach((b) => {
      b.onclick = () => { style = b.dataset.style; try { localStorage.setItem(STYLE_KEY, style); } catch { /* private */ } refreshPreview(); };
    });
  };
})();
