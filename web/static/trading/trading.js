/* Trading panel: connection, market data, and page wiring. Talks only to /api/mt5 and /api/quant.
   Orders go through a server-side preview and a confirm dialog; nothing leaves this page until
   the user presses Kirim. */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;
  const { aggregate, TIMEFRAME_SECONDS } = window.TradeChart;
  const BOTTOM_KEY = "trade-bottom-height";
  const THEME_KEY = "theme";
  const THEME_LABELS = { system: "Tema: sistem", light: "Tema: terang", dark: "Tema: gelap" };
  const CHAT_PORT = 8080;
  const MT4_BARS = 6000, OTHER_BARS = 1000, WATCHLIST_MAX = 10;
  const FIELD_TAGS = new Set(["INPUT", "SELECT", "TEXTAREA"]);

  const chart = new window.TradeChart($("chart"), {
    hover: showOhlc,
    view: (following) => { $("z-latest").hidden = following; },
  });

  function showOhlc(b) {
    if (!b) { $("ohlc").textContent = ""; return; }
    const change = b.o ? ((b.c - b.o) / b.o) * 100 : 0;
    $("ohlc").textContent = `O ${T.fmtPx(b.o)}  H ${T.fmtPx(b.h)}  L ${T.fmtPx(b.l)}  C ${T.fmtPx(b.c)}  ${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
  }

  // ---------------------------------------------------------------- connection
  async function loadStatus() {
    S.status = await T.api("/api/mt5/status", { auth: false });
    const s = S.status;
    if (!s.trading_enabled) {
      T.setBanner("Trading tidak aktif untuk akses ini. Lewat tunnel, set MT5_TRADE_TOKEN di .env lalu restart server.");
    } else if (!$("token").value && sessionStorage.getItem(T.TOKEN_KEY)) {
      $("token").value = sessionStorage.getItem(T.TOKEN_KEY);
    }
    if (!s.connected) { renderConn(); $("connmodal").hidden = false; return; }
    S.connected = true; S.live = s.live; S.backend = s.backend;
    $("backend").value = s.backend;
    renderConn();
    await T.refreshAll();
    // A reload finds the server already connected, so the login path that loads symbols never ran.
    await loadSymbols("");
  }

  function syncTimeframes() {
    document.querySelectorAll("#tfs button").forEach((b) => b.classList.toggle("on", b.dataset.tf === S.tf));
  }

  function renderConn() {
    syncTimeframes();
    $("propose").disabled = !S.connected;
    $("backtest").disabled = !S.connected;
    $("scalp").disabled = !S.connected;
    $("connect").disabled = S.connected;
    $("disconnect").disabled = !S.connected;
    $("preview").disabled = !S.connected || !S.symbol;
    $("mt5-fields").hidden = $("backend").value !== "mt5";
    if (!S.connected) {
      T.setMode("Terputus", "off");
      $("acct").textContent = "";
      $("acctdl").replaceChildren();
      return;
    }
    if (S.live) {
      T.setMode("LIVE — UANG RIIL", "live");
      T.setBanner("AKUN RIIL. Setiap order memakai uang sungguhan.", "live");
    } else {
      T.setMode(S.backend === "sim" ? "SIMULASI" : "DEMO", S.backend === "sim" ? "sim" : "demo");
    }
  }

  async function connect() {
    if (S.status && S.status.token_required && !T.token()) { T.setBanner("Isi token dulu (MT5_TRADE_TOKEN)."); return; }
    if (T.token()) sessionStorage.setItem(T.TOKEN_KEY, T.token());
    const body = {
      backend: $("backend").value, login: Number($("login").value || 0), password: $("password").value,
      server: $("server").value.trim(), path: $("path").value.trim() || null,
    };
    $("connect").disabled = true;
    try {
      const r = await T.api("/api/mt5/connect", { method: "POST", body });
      $("password").value = "";
      S.connected = true; S.live = r.live; S.backend = r.backend;
      $("connmodal").hidden = true;
      renderConn();
      await loadSymbols("");
      await T.refreshAll();
    } catch (e) {
      T.setBanner(e.message);
      $("connect").disabled = false;
    }
  }

  async function disconnect() {
    await T.api("/api/mt5/disconnect", { method: "POST" }).catch(() => null);
    Object.assign(S, { connected: false, live: false, backend: null, symbol: null, spec: null, tick: null, raw: [], loaded: "", positions: [], orders: [], deals: [] });
    chart.setBars([], false);
    $("symlist").replaceChildren();
    $("sym").textContent = "—";
    renderConn();
    T.renderTable();
  }

  // ---------------------------------------------------------------- symbols
  async function loadSymbols(q) {
    const r = await T.api("/api/mt5/symbols?q=" + encodeURIComponent(q));
    $("symlist").replaceChildren(...r.symbols.map((s) => h("li", { "data-s": s, class: s === S.symbol ? "on" : null },
      h("span", null, s), h("span", { class: "q" }, S.quotes[s] ?? ""))));
    if (!S.symbol && r.symbols.length) {
      const wanted = [sessionStorage.getItem(T.SYMBOL_KEY), S.positions[0] && S.positions[0].symbol];
      T.selectSymbol(wanted.find((x) => r.symbols.includes(x)) || r.symbols[0]);
    }
  }

  async function refreshQuotes() {
    const rows = [...document.querySelectorAll("#symlist li")].slice(0, WATCHLIST_MAX);
    await Promise.all(rows.map(async (row) => {
      const r = await T.api("/api/mt5/symbol?symbol=" + encodeURIComponent(row.dataset.s)).catch(() => null);
      if (!r) return;
      S.quotes[row.dataset.s] = T.fmtPx(r.tick.bid, r.spec.digits);
      row.querySelector(".q").textContent = S.quotes[row.dataset.s];
    }));
  }

  T.selectSymbol = async (sym) => {
    S.symbol = sym;
    S.lastPrice = null;
    sessionStorage.setItem(T.SYMBOL_KEY, sym);
    document.querySelectorAll("#symlist li").forEach((li) => li.classList.toggle("on", li.dataset.s === sym));
    $("sym").textContent = sym;
    $("preview").disabled = !S.connected;
    try {
      const r = await T.api("/api/mt5/symbol?symbol=" + encodeURIComponent(sym));
      S.spec = r.spec; S.tick = r.tick;
      $("vol").step = r.spec.volume_step;
      $("vol").min = r.spec.volume_min;
      if (!$("vol").value || Number($("vol").value) < r.spec.volume_min) $("vol").value = r.spec.volume_min;
      chart.setDigits(r.spec.digits);
      updateQuote();
      T.scheduleRisk();
    } catch (e) { T.setBanner(e.message); }
    S.tableKey = "";
    await loadBars();
    T.renderTable();
  };

  // ---------------------------------------------------------------- market data
  async function loadBars() {
    if (!S.symbol) return;
    const mt4 = S.backend === "mt4";
    const key = `${S.symbol}/${S.tf}`;
    const tf = mt4 && S.tf !== "M1" ? "M5" : S.tf;      // the bridge streams M1 and M5, the rest is built from M5
    try {
      const r = await T.api(`/api/mt5/rates?symbol=${encodeURIComponent(S.symbol)}&tf=${tf}&count=${mt4 ? MT4_BARS : OTHER_BARS}`);
      S.raw = r.bars;
      const bars = tf !== S.tf ? aggregate(r.bars, TIMEFRAME_SECONDS[S.tf]) : r.bars;
      chart.setBars(bars, key === S.loaded);
      S.loaded = key;
    } catch (e) {
      chart.setBars([], false);
      S.loaded = "";
      T.setBanner(e.message);
    }
    updateQuote();
  }

  function dayChange(price) {
    if (!S.raw.length) return null;
    const since = S.raw[S.raw.length - 1].t - 86400;
    const reference = S.raw.find((b) => b.t >= since);
    return reference && reference.o ? ((price - reference.o) / reference.o) * 100 : null;
  }

  function updateQuote() {
    if (!S.tick) return;
    const { bid, ask } = S.tick;
    const last = $("last");
    last.textContent = T.fmtPx(bid);
    if (S.lastPrice !== null && bid !== S.lastPrice) last.className = "ticker-price " + (bid > S.lastPrice ? "up" : "down");
    S.lastPrice = bid;
    const change = dayChange(bid);
    const chg = $("chg");
    chg.textContent = change === null ? "" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}% 24j`;
    chg.className = "ticker-chg " + T.moneyClass(change ?? 0);
    $("s-bid").textContent = $("tk-bid").textContent = T.fmtPx(bid);
    $("s-ask").textContent = $("tk-ask").textContent = T.fmtPx(ask);
    $("s-spread").textContent = S.spec ? Math.round((ask - bid) / S.spec.point) : "—";
    document.title = `${T.fmtPx(bid)} ${S.symbol}`;
  }

  async function tickLoop() {
    if (!S.connected || !S.symbol || S.tickBusy) return;
    S.tickBusy = true;      // a slow server must not collect a queue of price requests
    try {
      const r = await T.api("/api/mt5/symbol?symbol=" + encodeURIComponent(S.symbol));
      S.tick = r.tick;
      updateQuote();
      chart.applyTick(S.tick.bid, S.tick.time, TIMEFRAME_SECONDS[S.tf]);
    } catch { /* keep the last price on screen */ }
    S.tickBusy = false;
  }

  T.updateLines = () => {
    const lines = [];
    for (const p of S.positions.filter((x) => x.symbol === S.symbol)) {
      lines.push({ price: p.open_price, color: "accent", label: `${p.side.toUpperCase()} ${p.volume}  ${T.fmtMoney(p.profit)}` });
      if (p.sl) lines.push({ price: p.sl, color: "sell", dash: [4, 4], label: "SL" });
      if (p.tp) lines.push({ price: p.tp, color: "buy", dash: [4, 4], label: "TP" });
    }
    const sl = T.numOf("sl"), tp = T.numOf("tp");
    if (sl) lines.push({ price: sl, color: "warn", dash: [8, 4], label: "SL tiket" });
    if (tp) lines.push({ price: tp, color: "warn", dash: [8, 4], label: "TP tiket" });
    chart.setLines(lines);
  };

  T.refreshAll = async () => {
    if (!S.connected) return;
    try {
      const [account, positions, orders] = await Promise.all([
        T.api("/api/mt5/account"), T.api("/api/mt5/positions"), T.api("/api/mt5/orders"),
      ]);
      S.positions = positions.positions;
      S.orders = orders.orders;
      renderAccount(account);
    } catch (e) { T.setBanner(e.message); }
    if (S.tab === "history") await loadHistory();
    T.renderTable();
    T.updateLines();
  };

  function renderAccount(a) {
    $("acct").textContent = `${a.login} · ${a.server} · ${a.currency}`;
    $("s-equity").textContent = T.fmtMoney(a.equity);
    $("s-free").textContent = T.fmtMoney(a.free_margin);
    $("s-pnl").textContent = T.fmtMoney(a.profit);
    $("s-pnl").className = T.moneyClass(a.profit);
    const rows = [["Saldo", T.fmtMoney(a.balance)], ["Ekuitas", T.fmtMoney(a.equity)], ["Margin", T.fmtMoney(a.margin)],
      ["Margin bebas", T.fmtMoney(a.free_margin)], ["Profit mengambang", T.fmtMoney(a.profit)], ["Leverage", "1:" + a.leverage]];
    $("acctdl").replaceChildren(...rows.flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)]));
  }

  async function loadHistory() {
    const days = Math.max(1, Math.min(365, Number($("days").value || 7)));
    try {
      S.deals = (await T.api(`/api/mt5/history?days=${days}`)).deals;
    } catch (e) { T.setBanner(e.message); }
  }

  // ---------------------------------------------------------------- layout
  function setBottomHeight(px) {
    const height = Math.max(90, Math.min(window.innerHeight * 0.7, px));
    document.documentElement.style.setProperty("--bottom", `${Math.round(height)}px`);
    return height;
  }

  function wireSplitter() {
    const bar = $("splitter");
    const saved = Number(localStorage.getItem(BOTTOM_KEY));
    if (saved) setBottomHeight(saved);
    bar.addEventListener("pointerdown", (e) => {
      bar.setPointerCapture(e.pointerId);
      bar.classList.add("drag");
    });
    bar.addEventListener("pointermove", (e) => {
      if (!bar.classList.contains("drag")) return;
      const box = $("layout").getBoundingClientRect();
      localStorage.setItem(BOTTOM_KEY, setBottomHeight(box.bottom - e.clientY));
    });
    bar.addEventListener("pointerup", () => bar.classList.remove("drag"));
  }

  function toggleFocus(force) {
    const on = $("layout").classList.toggle("focus", force);
    $("z-focus").classList.toggle("on", on);
  }

  // The same setting and order as the chat page, so the two always match.
  function setTheme(theme) {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
    $("themebtn").textContent = THEME_LABELS[theme];
    chart.readTheme();
  }

  function nextTheme() {
    const names = Object.keys(THEME_LABELS);
    const current = localStorage.getItem(THEME_KEY) || "system";
    setTheme(names[(names.indexOf(current) + 1) % names.length]);
  }

  function onKey(e) {
    if (e.key === "Escape") {
      if (!$("modal").hidden) T.closeModal();
      else if (!$("modifymodal").hidden) T.closeModify();
      else if (!$("connmodal").hidden) $("connmodal").hidden = true;
      else toggleFocus(false);
      return;
    }
    if (FIELD_TAGS.has(document.activeElement.tagName)) return;
    const actions = { f: () => toggleFocus(), "+": () => chart.zoom(1.25), "=": () => chart.zoom(1.25), "-": () => chart.zoom(0.8),
      0: () => chart.resetView(), ArrowLeft: () => chart.pan(-10), ArrowRight: () => chart.pan(10) };
    if (actions[e.key]) actions[e.key]();
  }

  function wireOrderControls() {
    $("preview").onclick = T.previewOrder;
    $("propose").onclick = T.showProposal;
    $("fill").onclick = T.fillFromProposal;
    $("backtest").onclick = T.runBacktest;
    $("scalp").onclick = T.runScalp;
    $("q-buy").onclick = () => T.setSide("buy");
    $("q-sell").onclick = () => T.setSide("sell");
    document.querySelectorAll(".side").forEach((b) => { b.onclick = () => T.setSide(b.dataset.side); });
    document.querySelectorAll(".step").forEach((b) => { b.onclick = () => T.stepVolume(Number(b.dataset.step)); });
    document.querySelectorAll("#lots button").forEach((b) => {
      b.onclick = () => { $("vol").value = b.dataset.lot; T.scheduleRisk(); };
    });
    ["vol", "sl", "tp"].forEach((id) => $(id).addEventListener("input", T.scheduleRisk));
  }

  function wireDialogs() {
    $("connbtn").onclick = () => { $("connmodal").hidden = false; };
    $("conn-close").onclick = () => { $("connmodal").hidden = true; };
    $("backend").onchange = renderConn;
    $("connect").onclick = connect;
    $("disconnect").onclick = disconnect;
    $("token").onchange = () => sessionStorage.setItem(T.TOKEN_KEY, T.token());
    $("pairbtn").onclick = () => T.openPair(false);
    $("pair-download").onclick = T.downloadPreset;
    $("pair-copy").onclick = T.copySecret;
    $("pair-rotate").onclick = () => { if (confirm("Buat secret baru? EA yang sekarang akan putus sampai presetnya diganti.")) T.openPair(true); };
    $("pair-close").onclick = () => { $("pairmodal").hidden = true; T.refreshBridge(); };
    $("mod-cancel").onclick = T.closeModify;
    $("mod-ok").onclick = T.previewModify;
    $("m-cancel").onclick = T.closeModal;
    $("m-ok").onclick = T.confirmPending;
    $("banner").onclick = () => T.setBanner("");
  }

  function wireChartControls() {
    $("z-in").onclick = () => chart.zoom(1.25);
    $("z-out").onclick = () => chart.zoom(0.8);
    $("z-latest").onclick = () => chart.resetView();
    $("z-focus").onclick = () => toggleFocus();
    $("z-ema").onclick = () => $("z-ema").classList.toggle("on", chart.toggleEma());
    $("themebtn").onclick = nextTheme;
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => chart.readTheme());
    document.querySelectorAll("#tfs button").forEach((b) => {
      b.onclick = () => { S.tf = b.dataset.tf; syncTimeframes(); loadBars(); };
    });
  }

  function wireLists() {
    let timer;
    $("symq").oninput = () => {
      clearTimeout(timer);
      timer = setTimeout(() => loadSymbols($("symq").value.trim()).catch((e) => T.setBanner(e.message)), 250);
    };
    $("symlist").onclick = (e) => { const li = e.target.closest("li"); if (li) T.selectSymbol(li.dataset.s); };
    document.querySelectorAll(".tabs > button[data-tab]").forEach((b) => {
      b.onclick = async () => {
        S.tab = b.dataset.tab;
        document.querySelectorAll(".tabs > button[data-tab]").forEach((x) => x.classList.toggle("on", x === b));
        if (S.tab === "history") await loadHistory();
        T.renderTable();
      };
    });
    $("days").onchange = async () => { await loadHistory(); T.renderTable(); };
    $("table").onclick = T.onTableClick;
  }

  function startLoops() {
    // Polling pauses while the tab is hidden and catches up when it comes back.
    const every = (ms, fn) => setInterval(() => { if (!document.hidden) fn(); }, ms);
    every(500, T.tickModal);
    every(1000, tickLoop);
    every(3000, () => { if (S.connected) refreshQuotes(); });
    every(5000, () => { T.refreshAll(); T.refreshBridge(); });
    every(30000, () => { if (S.connected && S.symbol) loadBars(); });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { tickLoop(); T.refreshAll(); T.refreshBridge(); }
    });
  }

  // This server runs apart from the chat. Its address is the same host on the chat's port.
  if (!location.port) $("to-chat").href = `http://${location.hostname}:${CHAT_PORT}/`;
  setTheme(THEME_LABELS[localStorage.getItem(THEME_KEY)] ? localStorage.getItem(THEME_KEY) : "system");
  wireOrderControls();
  wireDialogs();
  wireChartControls();
  wireLists();
  wireSplitter();
  document.addEventListener("keydown", onKey);
  startLoops();
  T.renderTable();
  loadStatus().then(T.refreshBridge).catch((e) => T.setBanner(e.message));
})();
