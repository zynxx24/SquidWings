/* Shared helpers for the trading panel: state, DOM building, formatting, and the API call.
   Text from the server only reaches the page through h(), which sets it as text, never as HTML. */
(() => {
  "use strict";

  const T = (window.TradePanel = {});

  T.$ = (id) => document.getElementById(id);
  T.TOKEN_KEY = "mt5-trade-token";
  T.SYMBOL_KEY = "mt5-trade-symbol";
  T.S = {
    status: null, connected: false, live: false, backend: null,
    symbol: null, spec: null, tick: null, tf: "M5", raw: [], loaded: "", quotes: {},
    side: "buy", positions: [], orders: [], deals: [], tab: "positions",
    pending: null, pendingExpires: 0, proposal: null,
    riskTimer: null, modifyTicket: null, tickBusy: false, tableKey: "", lastPrice: null,
  };

  T.h = (tag, attrs, ...children) => {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") el.className = value;
      else el.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined) continue;
      el.append(child instanceof Node ? child : String(child));
    }
    return el;
  };

  const LOCALE = "id-ID";
  T.fmtPx = (v, d) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d ?? T.digits()));
  T.fmtMoney = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  T.fmtTime = (t) => (t ? new Date(t * 1000).toLocaleString(LOCALE, { hour12: false }) : "—");
  T.fmt2 = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "-" : Number(v).toFixed(2));
  T.digits = () => (T.S.spec ? T.S.spec.digits : 5);
  T.sideClass = (side) => (side === "buy" ? "buy-t" : "sell-t");
  T.moneyClass = (v) => (v >= 0 ? "pos" : "neg");

  T.setBanner = (text, cls = "") => {
    const b = T.$("banner");
    b.textContent = text || "";
    b.className = "banner " + cls;
    b.hidden = !text;
  };

  T.setMode = (label, cls) => {
    const m = T.$("mode");
    m.textContent = label;
    m.className = "pill " + cls;
  };

  T.token = () => T.$("token").value.trim();

  T.api = async (path, opts = {}) => {
    const headers = { "Content-Type": "application/json" };
    if (opts.auth !== false && T.token()) headers["X-MT5-Token"] = T.token();
    let res;
    try {
      res = await fetch(path, { method: opts.method || "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    } catch {
      throw new Error("Server tidak bisa dijangkau.");
    }
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const d = data && data.detail;
      if (d && typeof d === "object" && d.problems) throw new Error(d.problems.join("\n"));
      throw new Error(typeof d === "string" ? d : `Error ${res.status}`);
    }
    return data;
  };

  T.problemList = (title, message) => [T.h("strong", null, title),
    T.h("ul", null, message.split("\n").map((line) => T.h("li", null, line)))];
})();
