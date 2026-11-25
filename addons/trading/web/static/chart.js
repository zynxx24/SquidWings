/* Candlestick chart for the trading panel, drawn on one canvas.
   Wheel zooms around the cursor, dragging pans, dragging the price axis stretches it, a double
   click resets. It resizes itself with its container. It knows nothing about the API. */
(function (root) {
  "use strict";

  const TIMEFRAME_SECONDS = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400 };
  const PAD = { top: 10, right: 76, bottom: 24 };
  const MIN_BAR = 1.5, MAX_BAR = 48, DEFAULT_BAR = 8, RIGHT_GAP = 8, VOLUME_SHARE = 0.16;
  const EMA_PERIODS = [20, 50];
  /* Session strips along the top of the plot, one row each, in the order the day reaches them. */
  const SESSION_ROWS = { sydney: 0, tokyo: 1, london: 2, newyork: 3 };
  const SESSION_COLORS = { sydney: "text", tokyo: "warn", london: "accent", newyork: "strong" };
  const STRIP = 3, STRIP_GAP = 1;

  /* Builds higher timeframes from lower ones, so a feed with only M5 still has M15 to D1. */
  function aggregate(bars, step) {
    const out = [];
    for (const b of bars) {
      const t = Math.floor(b.t / step) * step;
      const last = out[out.length - 1];
      if (last && last.t === t) {
        last.h = Math.max(last.h, b.h);
        last.l = Math.min(last.l, b.l);
        last.c = b.c;
        last.v += b.v || 0;
      } else {
        out.push({ t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v || 0 });
      }
    }
    return out;
  }

  function ema(bars, period) {
    const k = 2 / (period + 1);
    const out = [];
    let value = null;
    for (const b of bars) {
      value = value === null ? b.c : b.c * k + value * (1 - k);
      out.push(value);
    }
    return out;
  }

  /* A round grid step (1, 2 or 5 times a power of ten) that gives about `count` lines. */
  function niceStep(range, count) {
    const rough = range / Math.max(1, count);
    const power = 10 ** Math.floor(Math.log10(rough));
    const unit = rough / power;
    return (unit < 1.5 ? 1 : unit < 3.5 ? 2 : unit < 7.5 ? 5 : 10) * power;
  }

  /* The range [first, last] of bars on screen. `offset` is the gap in bars to the right of the
     newest bar: positive leaves empty space, negative means the view has scrolled into the past. */
  function visibleRange(total, plotWidth, barWidth, offset) {
    const last = Math.min(total - 1, Math.ceil(total + offset));
    const first = Math.max(0, Math.floor(total - 1 + offset - plotWidth / barWidth));
    return { first, last };
  }

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  class TradeChart {
    constructor(canvas, callbacks) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.on = callbacks || {};
      this.bars = [];
      this.lines = [];
      this.markers = [];
      this.zones = [];
      this.sessions = [];
      this.forecast = null;
      this.showLevels = true;
      this.digits = 5;
      this.price = null;
      this.hover = null;
      this.showEma = true;
      this.drag = null;
      this.queued = false;
      this.resetView();
      this.readTheme();
      this.listen();
    }

    resetView() {
      this.barWidth = DEFAULT_BAR;
      this.offset = RIGHT_GAP;
      this.yStretch = 1;
      this.notifyView();
      this.schedule();
    }

    get following() {
      return this.offset >= 0;
    }

    notifyView() {
      if (this.on.view) this.on.view(this.following);
    }

    readTheme() {
      const css = getComputedStyle(document.body || document.documentElement);   // the page's colours live on body (exchange.css)
      const get = (name) => css.getPropertyValue(name).trim();
      this.colors = {
        bg: get("--chart-bg"), grid: get("--line"), text: get("--muted"), buy: get("--buy"), sell: get("--sell"),
        accent: get("--accent"), warn: get("--warn"), strong: get("--text"), onAccent: get("--surface"),
      };
      this.schedule();
    }

    /* New bars. A new series starts at the latest bar; a refresh of the same one keeps the view. */
    setBars(bars, keepView) {
      this.bars = bars;
      this.emas = EMA_PERIODS.map((period) => ema(bars, period));
      if (!keepView) this.resetView();
      this.schedule();
    }

    setDigits(digits) {
      this.digits = digits;
      this.schedule();
    }

    setLines(lines) {
      this.lines = lines;
      this.schedule();
    }

    /* Price zones from the checklist: { low, high, price, strength (1-4), label }. A zone where two or
       more kinds of level meet is drawn as a band with its name; a lone level as a faint line. */
    setZones(zones) {
      this.zones = zones || [];
      this.schedule();
    }

    /* When each session was open: { key, start, end } in seconds. Drawn as thin strips along the top. */
    setSessions(bands) {
      this.sessions = bands || [];
      this.schedule();
    }

    /* The candle model's next candles: { step: 300, candles: [{ t, o, h, l, c, c10, c90 }], levels: [{ price, label, color }] }.
       Drawn to the right of the last bar as hollow candles inside the 10-90% band of the close, merged into the chart's
       timeframe when it is longer than the forecast's (M5), spread over several bars when it is shorter. null clears. */
    setForecast(fc) {
      this.forecast = fc && fc.candles && fc.candles.length ? fc : null;
      this.schedule();
    }

    toggleLevels(on) {
      this.showLevels = on === undefined ? !this.showLevels : !!on;
      this.schedule();
      return this.showLevels;
    }

    /* Simulated trades: { side, entry, opened, exit?, closed?, win?, label? }, times in seconds.
       Drawn the way trading apps do: a BUY badge under the candle (pointing up) or a SELL badge over
       it (pointing down), joined to the exact entry price, a dashed path to the exit, and a dot
       coloured by the result. An open position also gets its entry level across the chart and a tag. */
    setMarkers(markers) {
      this.markers = markers || [];
      this.schedule();
    }

    /* The bar a moment falls in, or null before the first one. */
    barAtTime(t) {
      const bars = this.bars;
      if (!bars.length || t < bars[0].t) return null;
      let lo = 0, hi = bars.length - 1;
      while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (bars[mid].t <= t) lo = mid; else hi = mid - 1; }
      return bars[lo];
    }

    /* The x of a moment in time, between bars when it falls inside one. */
    xAtTime(t) {
      const bars = this.bars;
      if (!bars.length || t < bars[0].t) return null;
      const step = bars.length > 1 ? bars[bars.length - 1].t - bars[bars.length - 2].t || 60 : 60;
      let lo = 0, hi = bars.length - 1;
      while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (bars[mid].t <= t) lo = mid; else hi = mid - 1; }
      return this.x(lo + (t - bars[lo].t) / step - 0.5);
    }

    toggleEma() {
      this.showEma = !this.showEma;
      this.schedule();
      return this.showEma;
    }

    /* Moves the forming bar with the live price, or opens a new one when its period has ended. */
    applyTick(price, time, step) {
      this.price = price;
      const last = this.bars[this.bars.length - 1];
      if (last && time) {
        const slot = Math.floor(time / step) * step;
        if (slot > last.t) {
          this.bars.push({ t: slot, o: last.c, h: Math.max(last.c, price), l: Math.min(last.c, price), c: price, v: 0, forming: true });
        } else if (last.forming || slot === last.t) {
          last.c = price;
          last.h = Math.max(last.h, price);
          last.l = Math.min(last.l, price);
        }
      }
      this.schedule();
    }

    zoom(factor, anchorX) {
      const plot = this.plotWidth();
      const x = anchorX ?? plot / 2;
      const before = (plot - x) / this.barWidth;
      this.barWidth = clamp(this.barWidth * factor, MIN_BAR, MAX_BAR);
      this.offset += (plot - x) / this.barWidth - before;   // the bar under the cursor stays there
      this.clampOffset();
      this.schedule();
    }

    pan(bars) {
      this.offset += bars;
      this.clampOffset();
      this.schedule();
    }

    clampOffset() {
      const onScreen = this.plotWidth() / this.barWidth;
      this.offset = clamp(this.offset, -Math.max(0, this.bars.length - 5), onScreen - 5);
      this.notifyView();
    }

    plotWidth() {
      return Math.max(10, this.canvas.clientWidth - PAD.right);
    }

    schedule() {
      if (this.queued) return;
      this.queued = true;
      requestAnimationFrame(() => { this.queued = false; this.draw(); });
    }

    listen() {
      const cv = this.canvas;
      new ResizeObserver(() => this.schedule()).observe(cv);
      cv.addEventListener("wheel", (e) => {
        e.preventDefault();
        this.zoom(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.offsetX);
      }, { passive: false });
      cv.addEventListener("pointerdown", (e) => {
        cv.setPointerCapture(e.pointerId);
        const onAxis = e.offsetX > this.plotWidth();
        this.drag = { x: e.clientX, y: e.clientY, offset: this.offset, stretch: this.yStretch, onAxis };
      });
      cv.addEventListener("pointermove", (e) => {
        this.hover = { x: e.offsetX, y: e.offsetY };
        if (this.drag && this.drag.onAxis) {
          this.yStretch = clamp(this.drag.stretch * Math.exp((e.clientY - this.drag.y) / 200), 0.2, 8);
        } else if (this.drag) {
          this.offset = this.drag.offset - (e.clientX - this.drag.x) / this.barWidth;
          this.clampOffset();
        }
        this.schedule();
      });
      cv.addEventListener("pointerup", () => { this.drag = null; });
      cv.addEventListener("pointerleave", () => { this.hover = null; if (this.on.hover) this.on.hover(null); this.schedule(); });
      cv.addEventListener("dblclick", () => this.resetView());
    }

    draw() {
      const cv = this.canvas;
      const dpr = window.devicePixelRatio || 1;
      const w = cv.clientWidth, h = cv.clientHeight;
      if (!w || !h) return;
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
        cv.width = Math.round(w * dpr);
        cv.height = Math.round(h * dpr);
      }
      const ctx = this.ctx;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = this.colors.bg;
      ctx.fillRect(0, 0, w, h);
      ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
      const wrap = this.canvas.parentElement;
      if (wrap) wrap.classList.toggle("is-empty", !this.bars.length);   // the page shows its own empty state over it
      if (!this.bars.length) return;
      this.layout(w, h);
      this.drawGrid();
      if (this.showLevels) this.drawZones();
      this.drawVolume();
      this.drawCandles();
      if (this.showLevels) this.drawSessions();
      if (this.showEma) this.drawEmas();
      this.drawForecast();
      this.drawLines();
      this.drawMarkers();
      this.drawPriceTag();
      this.drawTimeAxis();
      this.drawCrosshair();
    }

    layout(w, h) {
      const plotW = w - PAD.right, plotH = h - PAD.top - PAD.bottom;
      const { first, last } = visibleRange(this.bars.length, plotW, this.barWidth, this.offset);
      let lo = this.bars[last].l, hi = this.bars[last].h;
      for (let i = first; i <= last; i++) {
        lo = Math.min(lo, this.bars[i].l);
        hi = Math.max(hi, this.bars[i].h);
      }
      const middle = (lo + hi) / 2;
      const half = ((hi - lo) / 2 || Math.abs(hi) * 0.001 || 1) * 1.08 * this.yStretch;
      this.view = { w, h, plotW, plotH, first, last, lo: middle - half, hi: middle + half };
    }

    x(index) {
      return this.view.plotW - (this.bars.length - 1 - index + this.offset) * this.barWidth - this.barWidth / 2;
    }

    y(price) {
      const v = this.view;
      return PAD.top + (v.hi - price) / (v.hi - v.lo) * v.plotH;
    }

    priceAt(y) {
      const v = this.view;
      return v.hi - (y - PAD.top) / v.plotH * (v.hi - v.lo);
    }

    indexAt(x) {
      const v = this.view;
      const index = this.bars.length - 1 + this.offset - (v.plotW - x) / this.barWidth + 0.5;
      return clamp(Math.round(index), 0, this.bars.length - 1);
    }

    drawGrid() {
      const { ctx, view: v, colors: c } = this;
      const step = niceStep(v.hi - v.lo, Math.max(3, Math.floor(v.plotH / 70)));
      ctx.strokeStyle = c.grid;
      ctx.lineWidth = 1;
      ctx.fillStyle = c.text;
      for (let p = Math.ceil(v.lo / step) * step; p < v.hi; p += step) {
        const y = Math.round(this.y(p)) + 0.5;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(v.plotW, y); ctx.stroke();
        ctx.fillText(p.toFixed(this.digits), v.plotW + 6, y + 4);
      }
      ctx.beginPath(); ctx.moveTo(v.plotW + 0.5, 0); ctx.lineTo(v.plotW + 0.5, v.h); ctx.stroke();
    }

    drawVolume() {
      const { ctx, view: v, colors: c } = this;
      let peak = 0;
      for (let i = v.first; i <= v.last; i++) peak = Math.max(peak, this.bars[i].v || 0);
      if (!peak) return;
      const height = v.plotH * VOLUME_SHARE, bottom = PAD.top + v.plotH;
      const width = Math.max(1, this.barWidth * 0.7);
      ctx.globalAlpha = 0.28;
      for (let i = v.first; i <= v.last; i++) {
        const b = this.bars[i];
        const size = (b.v || 0) / peak * height;
        ctx.fillStyle = b.c >= b.o ? c.buy : c.sell;
        ctx.fillRect(this.x(i) - width / 2, bottom - size, width, size);
      }
      ctx.globalAlpha = 1;
    }

    drawCandles() {
      const { ctx, view: v, colors: c } = this;
      const body = Math.max(1, this.barWidth * 0.72);
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, v.plotW, v.h); ctx.clip();
      for (let i = v.first; i <= v.last; i++) {
        const b = this.bars[i];
        const color = b.c >= b.o ? c.buy : c.sell;
        const x = Math.round(this.x(i)) + 0.5;
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.beginPath(); ctx.moveTo(x, this.y(b.h)); ctx.lineTo(x, this.y(b.l)); ctx.stroke();
        const top = this.y(Math.max(b.o, b.c)), bottom = this.y(Math.min(b.o, b.c));
        ctx.fillRect(x - body / 2, top, body, Math.max(1, bottom - top));
      }
      ctx.restore();
    }

    drawEmas() {
      const { ctx, view: v, colors: c } = this;
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, v.plotW, v.h); ctx.clip();
      this.emas.forEach((series, n) => {
        ctx.strokeStyle = n === 0 ? c.accent : c.warn;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        for (let i = v.first; i <= Math.min(v.last, series.length - 1); i++) {
          if (i === v.first) ctx.moveTo(this.x(i), this.y(series[i])); else ctx.lineTo(this.x(i), this.y(series[i]));
        }
        ctx.stroke();
      });
      ctx.restore();
    }

    drawZones() {
      const { ctx, view: v, colors: c } = this;
      if (!this.zones.length) return;
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, v.plotW, v.h); ctx.clip();
      let lastLabelY = -100;
      for (const z of [...this.zones].sort((a, b) => b.price - a.price)) {
        const top = this.y(z.high), bottom = this.y(z.low);
        if (bottom < 0 || top > v.h) continue;
        const strong = z.strength >= 2;
        const color = z.strength >= 3 ? c.warn : c.accent;
        if (strong) {
          ctx.globalAlpha = z.strength >= 3 ? 0.2 : 0.13;
          ctx.fillStyle = color;
          ctx.fillRect(0, Math.min(top, bottom) - 1, v.plotW, Math.max(3, bottom - top + 2));
        }
        const y = Math.round(this.y(z.price)) + 0.5;
        ctx.globalAlpha = strong ? 0.75 : 0.3;
        ctx.strokeStyle = strong ? color : c.text;
        ctx.setLineDash(strong ? [] : [1, 5]);
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(v.plotW, y); ctx.stroke();
        ctx.setLineDash([]);
        // names only where they fit: every confluence zone, and lone levels that are not crowded
        if (z.label && (strong || this.barWidth >= 4) && Math.abs(y - lastLabelY) > 13) {
          ctx.globalAlpha = strong ? 0.95 : 0.6;
          ctx.fillStyle = strong ? color : c.text;
          const text = strong ? `${"●".repeat(z.strength)} ${z.label}` : z.label;
          ctx.fillText(text, Math.max(4, v.plotW - ctx.measureText(text).width - 6), y - 3);
          lastLabelY = y;
        }
      }
      ctx.restore();
    }

    drawSessions() {
      const { ctx, view: v, colors: c } = this;
      if (!this.sessions.length || !this.bars.length) return;
      const firstT = this.bars[v.first].t, step = this.barStep();
      const lastT = this.bars[v.last].t + step;
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, v.plotW, v.h); ctx.clip();
      for (const band of this.sessions) {
        if (band.end <= firstT || band.start >= lastT) continue;
        const row = SESSION_ROWS[band.key];
        if (row === undefined) continue;
        const x0 = this.xAtTime(Math.max(band.start, firstT)) ?? 0;
        const x1 = band.end >= lastT ? this.x(v.last) + this.barWidth / 2 : (this.xAtTime(band.end) ?? v.plotW);
        ctx.globalAlpha = 0.7;
        ctx.fillStyle = c[SESSION_COLORS[band.key]];
        ctx.fillRect(x0, 1 + row * (STRIP + STRIP_GAP), Math.max(1, x1 - x0), STRIP);
      }
      ctx.restore();
    }

    barStep() {
      const bars = this.bars;
      return bars.length > 1 ? bars[bars.length - 1].t - bars[bars.length - 2].t || 60 : 60;
    }

    /* The forecast candles in the chart's own bars: where each falls (an index past the last bar, by time) and how wide. */
    forecastBars() {
      const fc = this.forecast, bars = this.bars;
      if (!fc || bars.length < 2) return [];
      const last = bars.length - 1;
      const step = bars[last].t - bars[last - 1].t || 60;
      const fstep = fc.step || 300;
      if (step <= fstep) {
        const span = fstep / step;
        return fc.candles.map((k) => ({ ...k, index: last + (k.t - bars[last].t) / step + (span - 1) / 2, span }));
      }
      const groups = new Map();
      for (const k of fc.candles) {
        const slot = Math.floor(k.t / step) * step;
        const g = groups.get(slot);
        if (!g) groups.set(slot, { ...k, t: slot });
        else Object.assign(g, { h: Math.max(g.h, k.h), l: Math.min(g.l, k.l), c: k.c, c10: k.c10, c90: k.c90 });
      }
      return [...groups.values()].map((g) => ({ ...g, index: last + (g.t - bars[last].t) / step, span: 1 }));
    }

    drawForecast() {
      const { ctx, view: v, colors: c } = this;
      const fb = this.forecastBars();
      if (!fb.length) return;
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, v.plotW, v.h); ctx.clip();
      // the 10-90% band of the close, as a fan from the first forecast candle
      ctx.globalAlpha = 0.13;
      ctx.fillStyle = c.accent;
      ctx.beginPath();
      fb.forEach((k, i) => { const x = this.x(k.index); if (i === 0) ctx.moveTo(x, this.y(k.c90)); else ctx.lineTo(x, this.y(k.c90)); });
      [...fb].reverse().forEach((k) => ctx.lineTo(this.x(k.index), this.y(k.c10)));
      ctx.closePath();
      ctx.fill();
      // the middle path
      ctx.globalAlpha = 0.8;
      ctx.strokeStyle = c.accent;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      fb.forEach((k, i) => { const x = this.x(k.index); if (i === 0) ctx.moveTo(x, this.y(k.c)); else ctx.lineTo(x, this.y(k.c)); });
      ctx.stroke();
      ctx.setLineDash([]);
      // hollow candles
      for (const k of fb) {
        const x = Math.round(this.x(k.index)) + 0.5;
        const body = Math.max(2, this.barWidth * 0.72 * k.span);
        const color = k.c >= k.o ? c.buy : c.sell;
        ctx.globalAlpha = 0.75;
        ctx.strokeStyle = color;
        ctx.beginPath(); ctx.moveTo(x, this.y(k.h)); ctx.lineTo(x, this.y(Math.max(k.o, k.c))); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x, this.y(Math.min(k.o, k.c))); ctx.lineTo(x, this.y(k.l)); ctx.stroke();
        const top = this.y(Math.max(k.o, k.c)), bottom = this.y(Math.min(k.o, k.c));
        ctx.strokeRect(x - body / 2, top, body, Math.max(1, bottom - top));
      }
      // the scenario levels across the forecast's stretch
      const x0 = this.x(fb[0].index - fb[0].span / 2 - 0.5), x1 = this.x(fb[fb.length - 1].index + fb[fb.length - 1].span / 2);
      for (const lv of this.forecast.levels || []) {
        const y = Math.round(this.y(lv.price)) + 0.5;
        if (y < 0 || y > v.h) continue;
        const color = c[lv.color] || c.accent;
        ctx.globalAlpha = 0.9;
        ctx.strokeStyle = color;
        ctx.setLineDash([5, 3]);
        ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.fillText(lv.label, Math.min(x1 + 4, v.plotW - ctx.measureText(lv.label).width - 4), y + 4);
      }
      ctx.restore();
    }

    drawLines() {
      const { ctx, view: v } = this;
      for (const line of this.lines) {
        const y = this.y(line.price);
        if (y < 0 || y > v.h) continue;
        const color = this.colors[line.color] || line.color;
        ctx.save();
        ctx.setLineDash(line.dash || []);
        ctx.strokeStyle = color;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(v.plotW, y); ctx.stroke();
        ctx.restore();
        const width = ctx.measureText(line.label).width + 10;
        ctx.fillStyle = this.colors.bg;
        ctx.fillRect(4, y - 16, width, 15);
        ctx.fillStyle = color;
        ctx.fillText(line.label, 9, y - 5);
      }
    }

    drawMarkers() {
      const { ctx, view: v, colors: c } = this;
      if (!this.markers.length) return;
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, v.plotW, v.h); ctx.clip();
      const now = Date.now() / 1000;
      const ring = c.bg || "#000";
      const round = (x, y, w, h, r) => {
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(x, y, w, h, r);
        else { ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
      };
      const open = [], done = [];
      for (const m of this.markers) (m.exit === undefined || m.exit === null ? open : done).push(m);
      // Closed trades first, so the open ones are always on top.
      for (const m of [...done, ...open]) {
        const x0 = this.xAtTime(m.opened);
        if (x0 === null) continue;
        const y0 = this.y(m.entry);
        const buy = m.side === "buy";
        const sideColor = buy ? c.buy : c.sell;
        const isDone = m.exit !== undefined && m.exit !== null;
        const x1 = Math.max(this.xAtTime(isDone ? m.closed : now) ?? x0, x0 + 6);
        const y1 = isDone ? this.y(m.exit) : this.y(this.price ?? m.entry);
        const resultColor = isDone ? (m.win ? c.buy : c.sell) : sideColor;

        // the path from entry to exit (or to the price now)
        ctx.globalAlpha = isDone ? 0.75 : 1;
        ctx.setLineDash([4, 3]);
        ctx.strokeStyle = resultColor;
        ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();

        // an open position's entry level, across to the price axis, with its tag
        if (!isDone) {
          ctx.globalAlpha = 0.55;
          ctx.setLineDash([2, 4]);
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(v.plotW, y0); ctx.stroke();
          ctx.globalAlpha = 1;
          ctx.setLineDash([]);
          const text = m.label || (buy ? "BUY" : "SELL");
          ctx.font = "600 11px system-ui, sans-serif";
          const w = ctx.measureText(text).width + 14;
          const lx = Math.min(Math.max(x1 + 8, x0 + 14), v.plotW - w - 2);
          round(lx, y0 - 9, w, 18, 9);
          ctx.fillStyle = sideColor; ctx.fill();
          ctx.lineWidth = 2; ctx.strokeStyle = ring; ctx.stroke();
          ctx.fillStyle = "#fff";
          ctx.fillText(text, lx + 7, y0 + 4);
        }
        ctx.globalAlpha = 1;
        ctx.setLineDash([]);

        // the badge: under the candle for a buy, over it for a sell, so a candle never hides it
        const bar = this.barAtTime(m.opened);
        const gap = 9, bw = 18, bh = 17, tip = 5;
        const anchor = buy ? Math.max(y0, bar ? this.y(bar.l) : y0) + gap : Math.min(y0, bar ? this.y(bar.h) : y0) - gap;
        ctx.strokeStyle = sideColor;
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x0, anchor); ctx.stroke();
        const top = buy ? anchor + tip : anchor - tip - bh;
        ctx.beginPath();
        if (buy) { ctx.moveTo(x0, anchor); ctx.lineTo(x0 - tip, top + 1); ctx.lineTo(x0 + tip, top + 1); }
        else { ctx.moveTo(x0, anchor); ctx.lineTo(x0 - tip, top + bh - 1); ctx.lineTo(x0 + tip, top + bh - 1); }
        ctx.closePath();
        ctx.fillStyle = sideColor; ctx.fill();
        round(x0 - bw / 2, top, bw, bh, 4);
        ctx.fillStyle = sideColor; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = ring; ctx.stroke();
        ctx.fillStyle = "#fff";
        ctx.beginPath();          // the arrow inside the badge
        const cy = top + bh / 2;
        if (buy) { ctx.moveTo(x0, cy - 5); ctx.lineTo(x0 - 5, cy + 3); ctx.lineTo(x0 + 5, cy + 3); }
        else { ctx.moveTo(x0, cy + 5); ctx.lineTo(x0 - 5, cy - 3); ctx.lineTo(x0 + 5, cy - 3); }
        ctx.closePath(); ctx.fill();

        // the exact entry price, then the result
        ctx.beginPath(); ctx.arc(x0, y0, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = sideColor; ctx.fill();
        ctx.lineWidth = 1.5; ctx.strokeStyle = ring; ctx.stroke();
        if (isDone) {
          ctx.beginPath(); ctx.arc(x1, y1, 4.5, 0, Math.PI * 2);
          ctx.fillStyle = resultColor; ctx.fill();
          ctx.lineWidth = 2; ctx.strokeStyle = ring; ctx.stroke();
        }
      }
      ctx.restore();
    }

    tag(y, text, background, foreground) {
      const { ctx, view: v } = this;
      ctx.fillStyle = background;
      ctx.fillRect(v.plotW, y - 9, PAD.right, 18);
      ctx.fillStyle = foreground;
      ctx.fillText(text, v.plotW + 6, y + 4);
    }

    drawPriceTag() {
      const last = this.bars[this.bars.length - 1];
      const price = this.price ?? last.c;
      const y = this.y(price);
      if (y < 0 || y > this.view.h) return;
      const c = this.colors;
      const color = price >= last.o ? c.buy : c.sell;
      this.ctx.save();
      this.ctx.setLineDash([2, 3]);
      this.ctx.strokeStyle = color;
      this.ctx.beginPath(); this.ctx.moveTo(0, y); this.ctx.lineTo(this.view.plotW, y); this.ctx.stroke();
      this.ctx.restore();
      this.tag(y, price.toFixed(this.digits), color, c.onAccent);
    }

    drawTimeAxis() {
      const { ctx, view: v, colors: c } = this;
      const every = Math.max(1, Math.ceil(110 / this.barWidth));
      ctx.fillStyle = c.text;
      for (let i = v.first - v.first % every; i <= v.last; i += every) {
        if (i < v.first) continue;
        const x = this.x(i);
        if (x > 30 && x < v.plotW - 30) ctx.fillText(TradeChart.timeLabel(this.bars[i].t), x - 30, v.h - 8);
      }
    }

    drawCrosshair() {
      const { ctx, view: v, colors: c, hover } = this;
      if (!hover || hover.x > v.plotW) {
        if (this.on.hover) this.on.hover(this.bars[this.bars.length - 1]);
        return;
      }
      const i = this.indexAt(hover.x);
      const x = this.x(i);
      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = c.text;
      ctx.beginPath();
      ctx.moveTo(x, 0); ctx.lineTo(x, v.h - PAD.bottom);
      ctx.moveTo(0, hover.y); ctx.lineTo(v.plotW, hover.y);
      ctx.stroke();
      ctx.restore();
      this.tag(hover.y, this.priceAt(hover.y).toFixed(this.digits), c.strong, c.bg);
      const label = TradeChart.timeLabel(this.bars[i].t);
      ctx.fillStyle = c.strong;
      ctx.fillRect(x - 46, v.h - PAD.bottom + 3, 92, 18);
      ctx.fillStyle = c.bg;
      ctx.fillText(label, x - 40, v.h - 8);
      if (this.on.hover) this.on.hover(this.bars[i]);
    }

    static timeLabel(t) {
      return new Date(t * 1000).toLocaleString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
    }
  }

  TradeChart.aggregate = aggregate;
  TradeChart.ema = ema;
  TradeChart.niceStep = niceStep;
  TradeChart.visibleRange = visibleRange;
  TradeChart.TIMEFRAME_SECONDS = TIMEFRAME_SECONDS;

  if (typeof module !== "undefined" && module.exports) module.exports = TradeChart;
  else root.TradeChart = TradeChart;
})(typeof window !== "undefined" ? window : globalThis);
