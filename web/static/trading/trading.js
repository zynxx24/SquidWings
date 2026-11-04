/* MT5 trading panel. Talks only to /api/mt5/*. Orders go through a server-side preview
   and a confirm dialog; nothing is sent from this page without the user pressing Kirim. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TOKEN_KEY = "mt5-trade-token";
  const S = {
    status: null, connected: false, live: false, backend: null,
    symbol: null, spec: null, tick: null, tf: "H1", bars: [],
    side: "buy", positions: [], orders: [], deals: [], tab: "positions",
    pending: null, pendingExpires: 0, proposal: null,
    hover: null, riskTimer: null, modifyTicket: null,
  };

  // ---------------------------------------------------------------- helpers
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtPx = (v, d = 5) => (v == null || isNaN(v) ? "—" : Number(v).toFixed(d ?? 5));
  const fmtMoney = (v) => (v == null ? "—" : Number(v).toLocaleString("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const fmtTime = (t) => (t ? new Date(t * 1000).toLocaleString("id-ID", { hour12: false }) : "—");
  const digits = () => (S.spec ? S.spec.digits : 5);
  const fmtBarTime = (t) => (t ? new Date(t * 1000).toLocaleString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : "");

  function showOhlc(b) {
    const el = $("ohlc");
    if (!b) { el.textContent = ""; return; }
    const d = digits();
    el.textContent = `O ${fmtPx(b.o, d)}  H ${fmtPx(b.h, d)}  L ${fmtPx(b.l, d)}  C ${fmtPx(b.c, d)}  · ${fmtBarTime(b.t)}`;
  }

  function setBanner(text, cls = "") {
    const b = $("banner");
    b.textContent = text || "";
    b.className = "banner " + cls;
    b.hidden = !text;
  }

  function setMode(label, cls) {
    const m = $("mode");
    m.textContent = label;
    m.className = "pill " + cls;
  }

  function token() {
    return $("token").value.trim();
  }

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    if (opts.auth !== false && token()) headers["X-MT5-Token"] = token();
    let res;
    try {
      res = await fetch(path, { method: opts.method || "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    } catch (e) {
      throw new Error("Server tidak bisa dijangkau.");
    }
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) {
      const d = data && data.detail;
      if (d && typeof d === "object" && d.problems) throw new Error(d.problems.join("\n"));
      throw new Error(typeof d === "string" ? d : `Error ${res.status}`);
    }
    return data;
  }

  // ---------------------------------------------------------------- status and login
  async function loadStatus() {
    S.status = await api("/api/mt5/status", { auth: false });
    const s = S.status;
    if (!s.trading_enabled) {
      setBanner("Trading tidak aktif untuk akses ini. Di komputer sendiri tidak perlu token; lewat tunnel, set MT5_TRADE_TOKEN di .env lalu restart server.");
    } else if (!$("token").value && sessionStorage.getItem(TOKEN_KEY)) {
      $("token").value = sessionStorage.getItem(TOKEN_KEY);
    }
    if (s.connected) {
      S.connected = true; S.live = s.live; S.backend = s.backend;
      renderConn();
      refreshAll();
    }
    $("preview").disabled = !s.connected;
  }

  function renderConn() {
    $("propose").disabled = !S.connected;
    $("backtest").disabled = !S.connected;
    $("connect").disabled = S.connected;
    $("disconnect").disabled = !S.connected;
    $("preview").disabled = !S.connected || !S.symbol;
    if (!S.connected) {
      setMode("Terputus", "off");
      $("acct").textContent = "";
      $("acctdl").innerHTML = "";
      return;
    }
    if (S.live) {
      setMode("LIVE — UANG RIIL", "live");
      setBanner("AKUN RIIL. Setiap order memakai uang sungguhan.", "live");
    } else if (S.backend === "sim") {
      setMode("SIMULASI", "sim");
      setBanner("");
    } else {
      setMode("DEMO", "demo");
      setBanner("");
    }
  }

  async function connect() {
    if (S.status && S.status.token_required && !token()) { setBanner("Isi token dulu (MT5_TRADE_TOKEN)."); return; }
    if (token()) sessionStorage.setItem(TOKEN_KEY, token());
    const body = {
      backend: $("backend").value,
      login: Number($("login").value || 0),
      password: $("password").value,
      server: $("server").value.trim(),
      path: $("path").value.trim() || null,
    };
    $("connect").disabled = true;
    try {
      const r = await api("/api/mt5/connect", { method: "POST", body });
      $("password").value = "";
      S.connected = true; S.live = r.live; S.backend = r.backend;
      renderConn();
      await loadSymbols("");
      await refreshAll();
    } catch (e) {
      setBanner(e.message);
      $("connect").disabled = false;
    }
  }

  async function disconnect() {
    try { await api("/api/mt5/disconnect", { method: "POST" }); } catch (_) { /* already gone */ }
    S.connected = false; S.live = false; S.backend = null; S.symbol = null; S.spec = null; S.bars = [];
    S.positions = []; S.orders = []; S.deals = [];
    renderConn(); renderTable(); drawChart();
  }

  // ---------------------------------------------------------------- symbols and chart
  async function loadSymbols(q) {
    const r = await api("/api/mt5/symbols?q=" + encodeURIComponent(q));
    const ul = $("symlist");
    ul.innerHTML = r.symbols.map((s) => `<li data-s="${esc(s)}" class="${s === S.symbol ? "on" : ""}">${esc(s)}</li>`).join("");
    if (!S.symbol && r.symbols.length) selectSymbol(r.symbols[0]);
  }

  async function selectSymbol(sym) {
    S.symbol = sym;
    document.querySelectorAll("#symlist li").forEach((li) => li.classList.toggle("on", li.dataset.s === sym));
    $("sym").textContent = sym;
    $("preview").disabled = !S.connected;
    try {
      const r = await api("/api/mt5/symbol?symbol=" + encodeURIComponent(sym));
      S.spec = r.spec; S.tick = r.tick;
      $("vol").step = r.spec.volume_step; $("vol").min = r.spec.volume_min;
      if (!$("vol").value || Number($("vol").value) < r.spec.volume_min) $("vol").value = r.spec.volume_min;
      updateBidAsk();
      scheduleRisk();
    } catch (e) { setBanner(e.message); }
    await loadBars();
  }

  async function loadBars() {
    if (!S.symbol) return;
    try {
      const r = await api(`/api/mt5/rates?symbol=${encodeURIComponent(S.symbol)}&tf=${S.tf}&count=300`);
      S.bars = r.bars;
    } catch (e) { S.bars = []; setBanner(e.message); }
    drawChart();
  }

  function updateBidAsk() {
    if (!S.tick) return;
    const d = digits();
    $("bidask").textContent = `Bid ${fmtPx(S.tick.bid, d)}   Ask ${fmtPx(S.tick.ask, d)}`;
  }

  function drawChart() {
    const cv = $("chart");
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    cv.width = w * dpr; cv.height = h * dpr;
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const css = getComputedStyle(document.body);
    const col = (v) => css.getPropertyValue(v).trim();
    const bg = col("--panel"), grid = col("--line"), text = col("--muted");
    const buy = col("--buy"), sell = col("--sell"), accent = col("--accent");
    ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);

    const bars = S.bars;
    if (!bars.length) {
      ctx.fillStyle = text; ctx.font = "14px system-ui";
      ctx.fillText("Belum ada data. Login lalu pilih symbol.", 16, 28);
      showOhlc(null);
      return;
    }
    const pad = { l: 8, r: 72, t: 10, b: 22 };
    const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
    let lo = Math.min(...bars.map((b) => b.l)), hi = Math.max(...bars.map((b) => b.h));
    if (S.tick) { lo = Math.min(lo, S.tick.bid); hi = Math.max(hi, S.tick.ask); }
    const margin = (hi - lo) * 0.05 || hi * 0.001;
    lo -= margin; hi += margin;
    const y = (p) => pad.t + (hi - p) / (hi - lo) * ph;
    const step = pw / bars.length;
    const d = digits();

    ctx.strokeStyle = grid; ctx.lineWidth = 1;
    ctx.fillStyle = text; ctx.font = "11px system-ui";
    for (let i = 0; i <= 5; i++) {
      const p = lo + (hi - lo) * i / 5, yy = y(p);
      ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke();
      ctx.fillText(fmtPx(p, d), w - pad.r + 6, yy + 4);
    }
    bars.forEach((b, i) => {
      const x = pad.l + i * step + step / 2;
      const up = b.c >= b.o;
      ctx.strokeStyle = up ? buy : sell; ctx.fillStyle = up ? buy : sell;
      ctx.beginPath(); ctx.moveTo(x, y(b.h)); ctx.lineTo(x, y(b.l)); ctx.stroke();
      const top = y(Math.max(b.o, b.c)), bot = y(Math.min(b.o, b.c));
      ctx.fillRect(x - Math.max(1, step * 0.35), top, Math.max(2, step * 0.7), Math.max(1, bot - top));
    });

    // open positions on this symbol: entry, stop loss and take profit lines
    S.positions.filter((p) => p.symbol === S.symbol).forEach((p) => {
      const line = (price, color, dash, label) => {
        if (!price) return;
        ctx.save(); ctx.setLineDash(dash); ctx.strokeStyle = color; ctx.beginPath();
        ctx.moveTo(pad.l, y(price)); ctx.lineTo(w - pad.r, y(price)); ctx.stroke(); ctx.restore();
        ctx.fillStyle = color; ctx.fillText(label + " " + fmtPx(price, d), pad.l + 4, y(price) - 3);
      };
      line(p.open_price, accent, [], "entry");
      line(p.sl, sell, [4, 4], "SL");
      line(p.tp, buy, [4, 4], "TP");
    });
    if (S.tick) {
      ctx.strokeStyle = accent; ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(pad.l, y(S.tick.bid)); ctx.lineTo(w - pad.r, y(S.tick.bid)); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = accent; ctx.fillRect(w - pad.r, y(S.tick.bid) - 8, pad.r - 2, 16);
      ctx.fillStyle = "#fff"; ctx.fillText(fmtPx(S.tick.bid, d), w - pad.r + 4, y(S.tick.bid) + 4);
    }

    // time axis: about six labels along the bottom edge
    ctx.fillStyle = text; ctx.font = "11px system-ui";
    const every = Math.max(1, Math.ceil(bars.length / 6));
    bars.forEach((b, i) => {
      if (i % every === 0 && i < bars.length - every / 2) ctx.fillText(fmtBarTime(b.t), pad.l + i * step + 2, h - 6);
    });

    // crosshair: follows the mouse and reads the bar under it
    const hv = S.hover;
    if (hv && hv.x >= pad.l && hv.x <= w - pad.r && hv.y >= pad.t && hv.y <= h - pad.b) {
      const i = Math.min(bars.length - 1, Math.max(0, Math.floor((hv.x - pad.l) / step)));
      const cx = pad.l + i * step + step / 2;
      ctx.save(); ctx.setLineDash([3, 3]); ctx.strokeStyle = text; ctx.beginPath();
      ctx.moveTo(cx, pad.t); ctx.lineTo(cx, h - pad.b);
      ctx.moveTo(pad.l, hv.y); ctx.lineTo(w - pad.r, hv.y); ctx.stroke(); ctx.restore();
      const price = hi - (hv.y - pad.t) / ph * (hi - lo);
      ctx.fillStyle = text; ctx.fillRect(w - pad.r, hv.y - 8, pad.r - 2, 16);
      ctx.fillStyle = bg; ctx.fillText(fmtPx(price, d), w - pad.r + 4, hv.y + 4);
      showOhlc(bars[i]);
    } else {
      showOhlc(bars[bars.length - 1]);
    }
  }

  // ---------------------------------------------------------------- live refresh
  async function tickLoop() {
    if (!S.connected || !S.symbol) return;
    try {
      const r = await api("/api/mt5/symbol?symbol=" + encodeURIComponent(S.symbol));
      S.tick = r.tick;
      updateBidAsk();
      const last = S.bars[S.bars.length - 1];
      if (last) { last.c = S.tick.bid; last.h = Math.max(last.h, S.tick.bid); last.l = Math.min(last.l, S.tick.bid); }
      drawChart();
    } catch (_) { /* keep the last price on screen */ }
  }

  async function refreshAll() {
    if (!S.connected) return;
    try {
      const [acct, pos, ord] = await Promise.all([
        api("/api/mt5/account"), api("/api/mt5/positions"), api("/api/mt5/orders"),
      ]);
      S.positions = pos.positions; S.orders = ord.orders;
      renderAccount(acct);
    } catch (e) { setBanner(e.message); }
    if (S.tab === "history") await loadHistory();
    renderTable();
    drawChart();
  }

  function renderAccount(a) {
    $("acct").textContent = `${a.login} · ${a.server} · Saldo ${fmtMoney(a.balance)} ${a.currency} · Ekuitas ${fmtMoney(a.equity)}`;
    const rows = [["Saldo", fmtMoney(a.balance)], ["Ekuitas", fmtMoney(a.equity)], ["Margin", fmtMoney(a.margin)],
      ["Margin bebas", fmtMoney(a.free_margin)], ["Profit mengambang", fmtMoney(a.profit)], ["Leverage", "1:" + a.leverage]];
    $("acctdl").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  }

  async function loadHistory() {
    const days = Math.max(1, Math.min(365, Number($("days").value || 7)));
    try {
      const r = await api(`/api/mt5/history?days=${days}`);
      S.deals = r.deals;
    } catch (e) { setBanner(e.message); }
  }

  // ---------------------------------------------------------------- tables
  function renderTable() {
    const t = $("table");
    // Do not rebuild the table while a partial-close volume is being typed: the refresh would wipe it.
    const typing = document.activeElement && document.activeElement.matches && document.activeElement.matches("#table input.vol-in");
    if (typing) return;
    const count = (n) => (n ? ` (${n})` : "");
    document.querySelector('[data-tab="positions"]').textContent = "Posisi" + count(S.connected ? S.positions.length : 0);
    document.querySelector('[data-tab="orders"]').textContent = "Order tertunda" + count(S.connected ? S.orders.length : 0);
    if (!S.connected) { t.innerHTML = "<tr><td>Belum login.</td></tr>"; return; }
    if (S.tab === "positions") {
      t.innerHTML = `<tr><th>Ticket</th><th>Symbol</th><th>Sisi</th><th class="num">Volume</th><th class="num">Harga buka</th>
        <th class="num">Harga</th><th class="num">SL</th><th class="num">TP</th><th class="num">Profit</th><th>Waktu</th><th></th></tr>` +
        (S.positions.length ? S.positions.map((p) => `<tr>
          <td>${p.ticket}</td><td>${esc(p.symbol)}</td><td class="${p.side}-t">${p.side.toUpperCase()}</td>
          <td class="num">${p.volume}</td><td class="num">${fmtPx(p.open_price)}</td><td class="num">${fmtPx(p.price)}</td>
          <td class="num">${fmtPx(p.sl)}</td><td class="num">${fmtPx(p.tp)}</td>
          <td class="num ${p.profit >= 0 ? "pos" : "neg"}">${fmtMoney(p.profit)}</td><td>${fmtTime(p.time)}</td>
          <td class="actions">
            <input class="mini vol-in" type="number" step="${S.spec ? S.spec.volume_step : 0.01}" min="${S.spec ? S.spec.volume_min : 0.01}" max="${p.volume}" value="${p.volume}" data-vol-of="${p.ticket}" title="Volume yang ditutup (lot)">
            <button class="btn mini" data-close="${p.ticket}">Tutup</button>
            <button class="btn mini" data-modify="${p.ticket}">SL/TP</button>
          </td></tr>`).join("")
          : `<tr><td colspan="11">Tidak ada posisi terbuka.</td></tr>`);
    } else if (S.tab === "orders") {
      t.innerHTML = `<tr><th>Ticket</th><th>Symbol</th><th>Sisi</th><th class="num">Volume</th><th class="num">Harga</th><th class="num">SL</th><th class="num">TP</th><th>Waktu</th><th></th></tr>` +
        (S.orders.length ? S.orders.map((o) => `<tr><td>${o.ticket}</td><td>${esc(o.symbol)}</td><td class="${o.side}-t">${o.side.toUpperCase()}</td>
          <td class="num">${o.volume}</td><td class="num">${fmtPx(o.price)}</td><td class="num">${fmtPx(o.sl)}</td><td class="num">${fmtPx(o.tp)}</td><td>${fmtTime(o.time)}</td>
          <td><button class="btn mini" data-cancel="${o.ticket}">Batalkan</button></td></tr>`).join("")
          : `<tr><td colspan="9">Tidak ada order tertunda.</td></tr>`);
    } else {
      t.innerHTML = `<tr><th>Ticket</th><th>Symbol</th><th>Sisi</th><th class="num">Volume</th><th class="num">Harga</th><th class="num">Profit</th><th>Waktu</th><th>Komentar</th></tr>` +
        (S.deals.length ? S.deals.map((d) => `<tr><td>${d.ticket}</td><td>${esc(d.symbol)}</td><td class="${d.side}-t">${d.side.toUpperCase()}</td>
          <td class="num">${d.volume}</td><td class="num">${fmtPx(d.price)}</td><td class="num ${d.profit >= 0 ? "pos" : "neg"}">${fmtMoney(d.profit)}</td>
          <td>${fmtTime(d.time)}</td><td>${esc(d.comment)}</td></tr>`).join("")
          : `<tr><td colspan="8">Belum ada riwayat.</td></tr>`);
    }
  }

  // ---------------------------------------------------------------- confirm dialog
  function openModal(title, lines, live, onOk, expires) {
    S.pending = onOk;
    S.pendingExpires = expires;
    $("m-title").textContent = title;
    $("m-body").innerHTML = lines.map((l) => `<div>${esc(l)}</div>`).join("");
    $("m-ok").disabled = false;
    $("m-ok").textContent = "Kirim";
    $("modal").querySelector(".dialog").classList.toggle("live", !!live);
    $("modal").hidden = false;
    tickModal();
  }

  function closeModal() {
    $("modal").hidden = true;
    S.pending = null;
  }

  function tickModal() {
    if ($("modal").hidden) return;
    const left = Math.max(0, Math.ceil((S.pendingExpires - Date.now()) / 1000));
    $("m-timer").textContent = left > 0 ? `Berlaku ${left} detik lagi.` : "Preview sudah kedaluwarsa. Tutup lalu preview ulang.";
    $("m-ok").disabled = left <= 0;
  }

  async function confirmPending() {
    if (!S.pending) return;
    const fn = S.pending;
    $("m-ok").disabled = true;
    try {
      const r = await fn();
      closeModal();
      const res = r.result || {};
      setBanner(res.ok ? `Berhasil: ${res.comment || "terkirim"} (retcode ${res.retcode ?? "-"})` : `Ditolak: ${res.comment || "gagal"} (retcode ${res.retcode ?? "-"})`, res.ok ? "" : "live");
      $("previewbox").hidden = true;
      await refreshAll();
    } catch (e) {
      $("m-body").innerHTML = `<div class="neg">${esc(e.message)}</div>`;
      $("m-ok").disabled = false;
    }
  }

  // ---------------------------------------------------------------- order ticket
  function orderFromForm() {
    const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
    return {
      symbol: S.symbol, side: S.side, volume: Number($("vol").value),
      sl: num("sl"), tp: num("tp"), comment: $("comment").value.trim(),
      reason: S.proposal && S.proposal.side === S.side ? S.proposal.reasons.join("; ") : "",
    };
  }

  async function previewOrder() {
    const order = orderFromForm();
    const box = $("previewbox");
    box.hidden = true;
    try {
      const r = await api("/api/mt5/preview", { method: "POST", body: order });
      const o = r.order;
      const lines = [
        `${o.side.toUpperCase()} ${o.volume} lot ${o.symbol}`,
        `Harga perkiraan: ${fmtPx(r.price, digits())}`,
        `SL: ${o.sl ?? "—"}   TP: ${o.tp ?? "—"}   R:R ${rrText(r.price, o.side, o.sl, o.tp)}`,
        r.risk ? `Jika SL kena: rugi ${fmtMoney(r.risk.money)} (${r.risk.pct_of_equity ?? "—"}% ekuitas)` : "Tanpa SL: kerugian tidak dibatasi.",
        `Margin: ${fmtMoney(r.check.margin)}`,
        S.live ? "⚠ Akun riil. Uang sungguhan." : "Akun demo/simulasi.",
      ];
      openModal("Konfirmasi order", lines, S.live, async () => {
        return api("/api/mt5/order/confirm", { method: "POST", body: { preview_id: r.preview_id } });
      }, Date.now() + r.expires_in * 1000);
    } catch (e) {
      box.hidden = false;
      box.className = "previewbox bad";
      box.innerHTML = "<strong>Order tidak bisa dikirim</strong><ul>" + e.message.split("\n").map((p) => `<li>${esc(p)}</li>`).join("") + "</ul>";
    }
  }

  async function previewClose(ticket, volume) {
    if (!(volume > 0)) { setBanner("Isi volume yang akan ditutup."); return; }
    try {
      const r = await api("/api/mt5/close/preview", { method: "POST", body: { ticket, volume } });
      const p = r.position;
      openModal("Tutup posisi", [
        `Tutup ${r.volume} lot ${p.side.toUpperCase()} ${p.symbol} (ticket ${p.ticket})`,
        `Profit saat ini: ${fmtMoney(p.profit)}`,
        S.live ? "⚠ Akun riil." : "",
      ], S.live, async () => api("/api/mt5/close/confirm", { method: "POST", body: { preview_id: r.preview_id } }),
      Date.now() + r.expires_in * 1000);
    } catch (e) { setBanner(e.message); }
  }

  // ---------------------------------------------------------------- quant proposal (suggestion only)
  async function showProposal() {
    const box = $("proposal");
    box.hidden = false;
    box.className = "previewbox";
    box.textContent = "Menghitung usulan...";
    try {
      const r = await api("/api/quant/propose?symbol=" + encodeURIComponent(S.symbol));
      S.proposal = r.proposal ? { ...r.proposal, lots: r.lots, ok: r.ok } : null;
      const p = r.proposal || {};
      const lines = [`Simbol ${r.symbol} · bar ${r.bar_time || "-"} · rezim ${p.regime || "-"} (P trend ${fmt2(p.p_trend)})`];
      if (r.ok && p.side) {
        lines.push(`${p.side.toUpperCase()} · lot ${r.lots} · risiko ${r.risk_money} ${S.spec ? "" : ""}`.trim());
        lines.push(`Entry ${fmtPx(p.entry, digits())} · SL ${fmtPx(p.sl, digits())} · TP ${fmtPx(p.tp, digits())}`);
        lines.push("Alasan: " + (p.reasons || []).join("; "));
        $("fill").disabled = false;
        box.className = "previewbox ok";
      } else {
        lines.push("Tidak ada usulan: " + (r.blocked || (r.risk_reasons || []).join("; ") || p.blocked || "-"));
        S.proposal = null;
        $("fill").disabled = true;
        box.className = "previewbox bad";
      }
      if (r.notes && r.notes.length) lines.push("Catatan: " + r.notes.join("; "));
      box.innerHTML = lines.map((l) => `<div>${esc(l)}</div>`).join("");
    } catch (e) {
      box.className = "previewbox bad";
      box.textContent = e.message;
      S.proposal = null;
      $("fill").disabled = true;
    }
  }

  function fillFromProposal() {
    const p = S.proposal;
    if (!p) return;
    S.side = p.side;
    document.querySelectorAll(".side").forEach((x) => x.classList.toggle("on", x.dataset.side === p.side));
    $("vol").value = p.lots;
    $("sl").value = p.sl;
    $("tp").value = p.tp;
    $("comment").value = ("quant " + p.side).slice(0, 31);
    $("preview").disabled = !S.connected;
    scheduleRisk();
  }

  const fmt2 = (v) => (v == null || isNaN(v) ? "-" : Number(v).toFixed(2));

  // ---------------------------------------------------------------- EA pairing and status
  let pairData = null;

  function setEaPill(text, cls, title) {
    const el = $("ea");
    el.textContent = text;
    el.className = "pill " + cls;
    el.title = title || "";
  }

  async function refreshBridge() {
    if (S.status && S.status.token_required && !token()) { setEaPill("EA: isi token", "off", "Isi token panel dulu"); return; }
    try {
      const b = await api("/api/quant/bridge");
      if (!b.paired) { setEaPill("EA: belum dipasang", "off", "Tekan Hubungkan EA"); }
      else if (b.ea_online) { setEaPill("EA online · " + (b.account || ""), b.live ? "live" : "demo", "Pesan terakhir " + b.last_message_s_ago + " s lalu"); }
      else { setEaPill("EA offline", "warn", "Sudah dipasang, tapi tidak ada pesan dari EA dalam 15 detik"); }
      return b;
    } catch (e) {
      setEaPill("EA: error", "warn", e.message);
      return null;
    }
  }

  function renderPairSteps(steps) {
    $("pair-steps").innerHTML = steps.map((x) => `<li>${esc(x)}</li>`).join("");
  }

  async function openPair(rotate = false) {
    if (S.status && S.status.token_required && !token()) { setBanner("Isi token panel dulu."); return; }
    $("pairmodal").hidden = false;
    $("pair-msg").textContent = rotate ? "Membuat secret baru..." : "Menyiapkan...";
    try {
      pairData = await api("/api/quant/bridge/pair" + (rotate ? "?rotate=true" : ""), { method: "POST" });
      renderPairSteps(pairData.steps);
      $("pair-msg").textContent = rotate ? "Secret baru dibuat. EA lama harus diganti presetnya." : "Secret sudah dibuat dan disimpan di server.";
    } catch (e) {
      $("pair-msg").textContent = e.message;
    }
  }

  async function downloadPreset() {
    if (!pairData) return;
    const blob = new Blob([pairData.preset], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = pairData.preset_name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  // ---------------------------------------------------------------- backtest (history only, no orders)
  async function runBacktest() {
    if (!S.symbol) { setBanner("Pilih symbol dulu."); return; }
    const box = $("btresult");
    box.hidden = false;
    box.className = "previewbox";
    box.textContent = "Menjalankan backtest... (bisa beberapa detik)";
    $("backtest").disabled = true;
    try {
      const count = $("bt-count").value;
      const r = await api(`/api/quant/backtest?symbol=${encodeURIComponent(S.symbol)}&count=${count}`);
      const x = r.summary || {};
      const pct = (v) => (v == null ? "-" : (v * 100).toFixed(1) + "%");
      const mc = x.monte_carlo || {};
      let html = `<div class="kvgrid">
        <div>Trade</div><b>${x.trades ?? 0}</b>
        <div>Win rate</div><b>${pct(x.win_rate)}</b>
        <div>Expectancy</div><b>${x.expectancy_r ?? "-"} R</b>
        <div>Profit factor</div><b>${x.profit_factor ?? "-"}</b>
        <div>Kalah beruntun (maks)</div><b>${x.max_losing_streak ?? "-"}</b>
        <div>DD p95 (MC)</div><b>${mc.max_drawdown_r_p95 ?? "-"} R</b>
      </div>`;
      if (x.note) html += `<div class="hint">${esc(x.note)}</div>`;
      html += `<div class="hint">${r.bars} bar M5 · spread ${fmtPx(r.spread_used, digits())} · komisi ${r.commission_r} R</div>`;
      if ((r.last_trades || []).length) {
        html += `<div class="tablewrap" style="max-height:160px"><table><tr><th>Waktu</th><th>Sisi</th><th class="num">R</th><th>Hasil</th></tr>` +
          r.last_trades.slice(-15).reverse().map((t) => `<tr><td>${esc(t.time)}</td><td class="${t.side}-t">${t.side.toUpperCase()}</td><td class="num ${t.r >= 0 ? "pos" : "neg"}">${t.r}</td><td>${esc(t.outcome)}</td></tr>`).join("") +
          `</table></div>`;
      }
      html += `<div class="note-warn">${esc(r.warning)}</div>`;
      box.className = "previewbox ok";
      box.innerHTML = html;
    } catch (e) {
      box.className = "previewbox bad";
      box.textContent = e.message;
    } finally {
      $("backtest").disabled = !S.connected;
    }
  }

  // ---------------------------------------------------------------- risk readout on the ticket
  // A field value of 0 or less counts as empty: the same as "no stop" or "no target".
  const numOf = (id) => { const v = $(id).value.trim(); if (v === "") return null; const n = Number(v); return n > 0 ? n : null; };

  function rrText(entry, side, sl, tp) {
    if (entry == null || !sl || !tp) return "—";
    const risk = Math.abs(entry - sl), reward = Math.abs(tp - entry);
    return risk > 0 ? "1:" + (reward / risk).toFixed(2) : "—";
  }

  function scheduleRisk() {
    clearTimeout(S.riskTimer);
    S.riskTimer = setTimeout(updateRisk, 300);
  }

  async function updateRisk() {
    const box = $("riskline");
    const vol = Number($("vol").value), sl = numOf("sl");
    if (!S.connected || !S.symbol || !(vol > 0) || !sl) { box.hidden = true; box.textContent = ""; return; }
    try {
      const r = await api(`/api/mt5/risk?symbol=${encodeURIComponent(S.symbol)}&side=${S.side}&volume=${vol}&sl=${sl}`);
      const entry = S.tick ? (S.side === "buy" ? S.tick.ask : S.tick.bid) : null;
      const parts = [];
      const warn = [];
      if (r.risk) {
        parts.push(`Risiko SL: ${fmtMoney(r.risk.money)} (${r.risk.pct_of_equity ?? "—"}% ekuitas)`);
        if (r.risk.pct_of_equity > 2) warn.push("di atas 2% ekuitas");
      }
      parts.push(`R:R ${rrText(entry, S.side, sl, numOf("tp"))}`);
      if (r.stops_level && r.stop_distance < r.stops_level) warn.push(`SL terlalu dekat, minimal ${r.stops_level}`);
      box.hidden = false;
      box.textContent = parts.join(" · ") + (warn.length ? " · ⚠ " + warn.join("; ") : "");
      box.className = "riskline" + (warn.length ? " warn" : "");
    } catch (_) {
      box.hidden = true;
    }
  }

  // ---------------------------------------------------------------- change SL/TP and cancel pending
  function openModify(ticket) {
    const p = S.positions.find((x) => x.ticket === ticket);
    if (!p) { setBanner("Posisi itu sudah tidak terbuka."); return; }
    S.modifyTicket = ticket;
    $("mod-info").textContent = `${p.side.toUpperCase()} ${p.volume} lot ${p.symbol} · buka ${fmtPx(p.open_price, digits())} · ticket ${p.ticket}`;
    $("mod-sl").value = p.sl ?? "";
    $("mod-tp").value = p.tp ?? "";
    $("mod-problems").hidden = true;
    $("modifymodal").hidden = false;
    $("mod-sl").focus();
  }

  function closeModify() {
    $("modifymodal").hidden = true;
    S.modifyTicket = null;
  }

  async function previewModify() {
    if (!S.modifyTicket) return;
    const body = { ticket: S.modifyTicket, sl: numOf("mod-sl"), tp: numOf("mod-tp") };
    const box = $("mod-problems");
    box.hidden = true;
    try {
      const r = await api("/api/mt5/modify/preview", { method: "POST", body });
      closeModify();
      const p = r.position, c = r.change;
      openModal("Ubah SL / TP", [
        `${p.side.toUpperCase()} ${p.volume} lot ${p.symbol} (ticket ${p.ticket})`,
        `SL: ${p.sl ?? "—"} → ${c.sl ?? "dihapus"}`,
        `TP: ${p.tp ?? "—"} → ${c.tp ?? "dihapus"}`,
        r.risk ? `Jika SL kena: rugi ${fmtMoney(r.risk.money)} (${r.risk.pct_of_equity ?? "—"}% ekuitas)` : "Tanpa SL: kerugian tidak dibatasi.",
        S.live ? "⚠ Akun riil." : "",
      ], S.live, async () => api("/api/mt5/modify/confirm", { method: "POST", body: { preview_id: r.preview_id } }),
      Date.now() + r.expires_in * 1000);
    } catch (e) {
      box.hidden = false;
      box.innerHTML = "<strong>Tidak bisa diubah</strong><ul>" + e.message.split("\n").map((p) => `<li>${esc(p)}</li>`).join("") + "</ul>";
    }
  }

  async function previewCancel(ticket) {
    try {
      const r = await api("/api/mt5/cancel/preview", { method: "POST", body: { ticket } });
      const o = r.order;
      openModal("Batalkan order tertunda", [
        `${o.side.toUpperCase()} ${o.volume} lot ${o.symbol} (ticket ${o.ticket})`,
        `Harga order: ${fmtPx(o.price, digits())}`,
        "Order akan dihapus. Tidak ada posisi yang ditutup.",
        S.live ? "⚠ Akun riil." : "",
      ], S.live, async () => api("/api/mt5/cancel/confirm", { method: "POST", body: { preview_id: r.preview_id } }),
      Date.now() + r.expires_in * 1000);
    } catch (e) { setBanner(e.message); }
  }

  // ---------------------------------------------------------------- wiring
  function wire() {
    $("connect").onclick = connect;
    $("disconnect").onclick = disconnect;
    $("token").onchange = () => sessionStorage.setItem(TOKEN_KEY, token());
    $("symq").oninput = (() => { let t; return () => { clearTimeout(t); t = setTimeout(() => loadSymbols($("symq").value.trim()).catch((e) => setBanner(e.message)), 250); }; })();
    $("symlist").onclick = (e) => { const li = e.target.closest("li"); if (li) selectSymbol(li.dataset.s); };
    document.querySelectorAll("#tfs button").forEach((b) => b.onclick = () => {
      S.tf = b.dataset.tf;
      document.querySelectorAll("#tfs button").forEach((x) => x.classList.toggle("on", x === b));
      loadBars();
    });
    document.querySelectorAll(".side").forEach((b) => b.onclick = () => {
      S.side = b.dataset.side;
      document.querySelectorAll(".side").forEach((x) => x.classList.toggle("on", x === b));
      scheduleRisk();
    });
    $("preview").onclick = previewOrder;
    $("propose").onclick = showProposal;
    $("pairbtn").onclick = () => openPair(false);
    $("pair-download").onclick = downloadPreset;
    $("pair-copy").onclick = async () => {
      if (!pairData) return;
      try { await navigator.clipboard.writeText(pairData.secret); $("pair-msg").textContent = "Secret disalin."; }
      catch (_) { $("pair-msg").textContent = "Tidak bisa menyalin otomatis; unduh preset saja."; }
    };
    $("pair-rotate").onclick = () => { if (confirm("Buat secret baru? EA yang sekarang akan putus sampai presetnya diganti.")) openPair(true); };
    $("pair-close").onclick = () => { $("pairmodal").hidden = true; refreshBridge(); };
    $("backtest").onclick = runBacktest;
    $("fill").onclick = fillFromProposal;
    document.querySelectorAll(".tabs > button[data-tab]").forEach((b) => b.onclick = async () => {
      S.tab = b.dataset.tab;
      document.querySelectorAll(".tabs > button[data-tab]").forEach((x) => x.classList.toggle("on", x === b));
      if (S.tab === "history") await loadHistory();
      renderTable();
    });
    $("days").onchange = async () => { await loadHistory(); renderTable(); };
    $("table").onclick = (e) => {
      const close = e.target.closest("button[data-close]");
      const mod = e.target.closest("button[data-modify]");
      const cancel = e.target.closest("button[data-cancel]");
      if (close) {
        const ticket = Number(close.dataset.close);
        const vin = $("table").querySelector(`input[data-vol-of="${ticket}"]`);
        previewClose(ticket, Number(vin ? vin.value : 0));
      } else if (mod) {
        openModify(Number(mod.dataset.modify));
      } else if (cancel) {
        previewCancel(Number(cancel.dataset.cancel));
      }
    };
    $("mod-cancel").onclick = closeModify;
    $("mod-ok").onclick = previewModify;
    ["vol", "sl", "tp"].forEach((id) => $(id).addEventListener("input", scheduleRisk));
    const cv = $("chart");
    cv.addEventListener("mousemove", (e) => {
      const r = cv.getBoundingClientRect();
      S.hover = { x: e.clientX - r.left, y: e.clientY - r.top };
      drawChart();
    });
    cv.addEventListener("mouseleave", () => { S.hover = null; drawChart(); });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!$("modal").hidden) closeModal();
      if (!$("modifymodal").hidden) closeModify();
    });
    $("m-cancel").onclick = closeModal;
    $("m-ok").onclick = confirmPending;
    window.addEventListener("resize", drawChart);
    setInterval(tickModal, 500);
    setInterval(tickLoop, 2000);
    setInterval(() => { if (S.connected) refreshAll(); }, 5000);
    setInterval(refreshBridge, 5000);
  }

  wire();
  loadStatus().then(refreshBridge).catch((e) => setBanner(e.message));
})();
