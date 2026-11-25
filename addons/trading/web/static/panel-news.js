/* News and market condition for the selected symbol: the economic calendar, the crypto market,
   headlines sorted into topics (influencer, technology, AI, blockchain, ...), and what the MT4
   price itself says. Loaded when the section is opened, then every five minutes while it is. */
(() => {
  "use strict";

  const T = window.TradePanel;
  const { $, S, h } = T;
  let timer = 0;
  let loadedFor = "";

  const toneClass = (t) => (t > 0.15 ? "pos" : t < -0.15 ? "neg" : "");
  const toneWord = (t) => (t > 0.15 ? "positif" : t < -0.15 ? "negatif" : "netral");
  const ago = (min) => (min === null || min === undefined ? "" : min < 60 ? `${min} mnt lalu` : `${Math.round(min / 60)} jam lalu`);
  const until = (min) => (min < 0 ? `${-min} mnt lalu` : min < 60 ? `${min} mnt lagi` : `${Math.floor(min / 60)} j ${min % 60} m lagi`);

  async function load(refresh) {
    if (!$("news-pane").open) return;
    const symbol = S.symbol || "";
    $("news-pill").textContent = "memuat…";
    try {
      const r = await T.api(`/api/quant/news?symbol=${encodeURIComponent(symbol)}${refresh ? "&refresh=true" : ""}`);
      loadedFor = symbol;
      render(r);
    } catch (e) {
      $("news-pill").textContent = "gagal";
      $("news-body").replaceChildren(h("p", { class: "note-warn" }, e.message));
    }
  }

  function render(r) {
    const mood = r.mood || { label: "netral", score: 0 };
    const pill = $("news-pill");
    pill.textContent = mood.label;
    pill.className = "pill " + (mood.label === "risk-on" ? "buyp" : mood.label === "risk-off" ? "sellp" : "off");
    const okSources = (r.sources || []).filter((x) => x.ok).length;
    $("news-age").textContent = `${okSources}/${(r.sources || []).length} sumber · ${r.all_headlines || 0} judul`;

    const parts = [];
    const cond = r.condition;
    if (cond) {
      parts.push(h("div", { class: "newsrow" }, h("b", null, "Kondisi dari MT4 "),
        cond.state === "tidak terhubung" ? h("span", { class: "hint" }, "terminal belum terhubung")
          : h("span", null, `${cond.state} · volatilitas ${cond.volatility_ratio}× · gerak 30 bar ${cond.change_30_bars_pct}% · spread ${Math.round((cond.spread_vs_range || 0) * 100)}% dari range bar`),
        cond.warning ? h("div", { class: "note-warn" }, cond.warning) : ""));
    }
    const cal = r.calendar || {};
    if (cal.next_high) {
      const e = cal.next_high;
      parts.push(h("div", { class: cal.near_high ? "note-warn" : "newsrow" }, h("b", null, `Berita besar ${e.currency}: `),
        `${e.title} · ${until(e.in_min)}`, e.forecast ? h("span", { class: "hint" }, ` (perkiraan ${e.forecast}, sebelumnya ${e.previous || "–"})`) : ""));
    } else if (cal.currencies && cal.currencies.length) {
      parts.push(h("div", { class: "hint" }, `Tidak ada berita berdampak besar untuk ${cal.currencies.join(", ")} minggu ini.`));
    }
    const crypto = r.crypto || {};
    if (crypto.fear_greed || crypto.market_cap_change_24h !== undefined) {
      parts.push(h("div", { class: "newsrow" }, h("b", null, "Kripto "),
        crypto.fear_greed ? `Fear & Greed ${crypto.fear_greed.value} (${crypto.fear_greed.label}) · ` : "",
        crypto.market_cap_change_24h !== undefined ? h("span", { class: toneClass(crypto.market_cap_change_24h / 3) }, `market cap 24j ${crypto.market_cap_change_24h}%`) : "",
        crypto.btc_24h !== undefined ? ` · BTC ${crypto.btc_24h}% · ETH ${crypto.eth_24h}%` : "",
        crypto.trending && crypto.trending.length ? h("div", { class: "hint" }, "Trending: " + crypto.trending.slice(0, 5).map((c) => `${c.symbol}${c.change_24h !== null ? ` ${c.change_24h > 0 ? "+" : ""}${c.change_24h}%` : ""}`).join(", ")) : ""));
    }
    const relevant = new Set(r.relevant || []);
    const topics = Object.entries(r.topics || {}).filter(([, t]) => t.count).sort((a, b) => (relevant.has(b[0]) - relevant.has(a[0])) || b[1].count - a[1].count);
    if (topics.length) {
      parts.push(h("div", { class: "chips newschips" }, topics.map(([key, t]) => h("span", {
        class: "chip " + (relevant.has(key) ? "on " : "") + toneClass(t.tone),
        title: `${t.count} judul, nada ${toneWord(t.tone)} (${t.tone})${t.top.length ? "\n" + t.top.join("\n") : ""}`,
      }, `${t.label} ${t.count}`))));
    }
    const rows = (r.headlines || []).slice(0, 10);
    if (rows.length) {
      parts.push(h("ul", { class: "headlines" }, rows.map((x) => {
        const title = x.link ? h("a", { href: x.link, target: "_blank", rel: "noopener noreferrer" }, x.title) : h("span", null, x.title);
        return h("li", null, title, h("div", { class: "hint" },
          h("span", { class: toneClass(x.tone) }, "● "), `${x.source} · ${ago(x.age_min)}`, x.topics.length ? ` · ${x.topics.join(", ")}` : ""));
      })));
    }
    const bad = (r.sources || []).filter((x) => !x.ok);
    if (bad.length) parts.push(h("div", { class: "hint" }, "Sumber gagal: " + bad.map((x) => `${x.name} (${x.error || "kosong"})`).join("; ")));
    parts.push(h("p", { class: "hint" }, r.note || ""));
    $("news-body").replaceChildren(...parts);
  }

  T.newsSymbolChanged = () => { if (loadedFor !== (S.symbol || "")) load(false); };

  T.initNews = () => {
    $("news-pane").addEventListener("toggle", () => {
      clearInterval(timer);
      if (!$("news-pane").open) return;
      load(false);
      timer = setInterval(() => { if (!document.hidden) load(false); }, 300000);
    });
    $("news-refresh").onclick = () => load(true);
  };
})();
