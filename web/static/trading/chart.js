/* Candlestick chart for the trading panel, drawn on one canvas.
   Wheel zooms around the cursor, dragging pans, dragging the price axis stretches it, a double
   click resets. It resizes itself with its container. It knows nothing about the API. */
(function (root) {
  "use strict";

  const TIMEFRAME_SECONDS = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400 };
  const PAD = { top: 10, right: 76, bottom: 24 };
  const MIN_BAR = 1.5, MAX_BAR = 48, DEFAULT_BAR = 8, RIGHT_GAP = 8, VOLUME_SHARE = 0.16;
  const EMA_PERIODS = [20, 50];

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
      const css = getComputedStyle(document.documentElement);
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
      if (!this.bars.length) {
        ctx.fillStyle = this.colors.text;
        ctx.fillText("Belum ada data. Hubungkan akun lalu pilih simbol.", 16, 28);
        return;
      }
      this.layout(w, h);
      this.drawGrid();
      this.drawVolume();
      this.drawCandles();
      if (this.showEma) this.drawEmas();
      this.drawLines();
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
