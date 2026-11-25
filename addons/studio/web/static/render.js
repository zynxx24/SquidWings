/* Studio renderer: draws one frame of a motion.json at time t. Shared by the preview and the export,
 * so what the editor shows is what the MP4 gets. The rules match addons/studio/studio_doc.py and the
 * C engine (engine/sqengine.c): easing, transitions, colour, chroma key, LUT and masks use the same
 * arithmetic in both. */
(() => {
  'use strict';

  const EASE = {
    linear: p => p,
    in: p => p * p * p,
    out: p => 1 - (1 - p) ** 3,
    inOut: p => (p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2),
    outBack: p => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2; },
    outElastic: p => (p === 0 || p === 1 ? p : 2 ** (-10 * p) * Math.sin((p * 10 - 0.75) * (2 * Math.PI / 3)) + 1),
    hold: () => 0,
    inBack: p => { const c1 = 1.70158, c3 = c1 + 1; return c3 * p * p * p - c1 * p * p; },
    outBounce: p => {
      const n1 = 7.5625, d1 = 2.75;
      if (p < 1 / d1) return n1 * p * p;
      if (p < 2 / d1) { p -= 1.5 / d1; return n1 * p * p + 0.75; }
      if (p < 2.5 / d1) { p -= 2.25 / d1; return n1 * p * p + 0.9375; }
      p -= 2.625 / d1; return n1 * p * p + 0.984375;
    },
    // smooth: between keys a curve through all of them (valueAt); on its own it moves like inOut.
    smooth: p => (p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2),
  };
  const EASE_NAMES = { linear: 'Linear', in: 'Pelan di awal', out: 'Pelan di akhir', inOut: 'Pelan di awal & akhir',
    outBack: 'Memantul lewat', inBack: 'Ambil ancang-ancang', outElastic: 'Elastis', outBounce: 'Memantul jatuh',
    smooth: 'Halus', hold: 'Tahan (lompat)' };
  // The curve editor starts a named ease from its closest cubic-bezier.
  const BEZIER_OF = { linear: [0, 0, 1, 1], in: [0.55, 0.055, 0.675, 0.19], out: [0.215, 0.61, 0.355, 1],
    inOut: [0.645, 0.045, 0.355, 1], outBack: [0.175, 0.885, 0.32, 1.275], inBack: [0.6, -0.28, 0.735, 0.045] };
  const COLOR_PROPS = new Set(['fill', 'stroke']);

  /* cubic-bezier(x1,y1,x2,y2), solved like the C engine: Newton steps, then bisection. */
  const bezAt = (t, p1, p2) => { const u = 1 - t; return 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t; };
  const bezSlope = (t, p1, p2) => { const u = 1 - t; return 3 * u * u * p1 + 6 * u * t * (p2 - p1) + 3 * t * t * (1 - p2); };
  function bezier(x1, y1, x2, y2) {
    return x => {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      let t = x;
      for (let i = 0; i < 8; i++) {
        const err = bezAt(t, x1, x2) - x;
        if (Math.abs(err) < 1e-7 && t >= 0 && t <= 1) return bezAt(t, y1, y2);
        const d = bezSlope(t, x1, x2);
        if (Math.abs(d) < 1e-6) break;
        t -= err / d;
      }
      let lo = 0, hi = 1;
      t = x;
      for (let i = 0; i < 60; i++) {
        const v = bezAt(t, x1, x2);
        if (Math.abs(v - x) < 1e-7) break;
        if (v < x) lo = t; else hi = t;
        t = (lo + hi) / 2;
      }
      return bezAt(t, y1, y2);
    };
  }
  const BEZ_RE = /^cubic-bezier\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)$/;
  const bezCache = new Map();
  /** The function for an ease name or a cubic-bezier(...) string. */
  function easeFn(name) {
    if (EASE[name]) return EASE[name];
    if (typeof name !== 'string') return EASE.linear;
    let fn = bezCache.get(name);
    if (!fn) {
      const m = BEZ_RE.exec(name);
      fn = m ? bezier(+m[1], +m[2], +m[3], +m[4]) : EASE.linear;
      if (bezCache.size > 500) bezCache.clear();
      bezCache.set(name, fn);
    }
    return fn;
  }
  /** [x1, y1, x2, y2] for an ease, or null when it has no bezier form (elastic, bounce, hold). */
  function bezierOf(name) {
    const m = typeof name === 'string' && BEZ_RE.exec(name);
    if (m) return [+m[1], +m[2], +m[3], +m[4]];
    return BEZIER_OF[name] ? [...BEZIER_OF[name]] : null;
  }

  function hexToRgba(hex) {
    let h = String(hex || '').replace('#', '');
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    const n = parseInt(h.slice(0, 6), 16);
    const a = h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1;
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, a];
  }
  const rgbaToHex = ([r, g, b, a]) => '#' + [r, g, b].map(v => Math.round(v).toString(16).padStart(2, '0')).join('')
    + (a < 1 ? Math.round(a * 255).toString(16).padStart(2, '0') : '');

  function lerp(a, b, p, color) {
    if (color) {
      if (!a || !b) return p < 0.5 ? a : b;
      const x = hexToRgba(a), y = hexToRgba(b);
      return rgbaToHex(x.map((v, i) => v + (y[i] - v) * p));
    }
    return a + (b - a) * p;
  }

  /** The value of one property at time t: its keys when it has them, else the static value. */
  function valueAt(keys, prop, t, fallback) {
    const frames = keys && keys[prop];
    if (!frames || !frames.length) return fallback;
    if (t <= frames[0].t) return frames[0].v;
    const last = frames[frames.length - 1];
    if (t >= last.t) return last.v;
    for (let i = 0; i < frames.length - 1; i++) {
      const a = frames[i], b = frames[i + 1];
      if (t >= a.t && t < b.t) {
        const span = b.t - a.t || 1;
        if (a.ease === 'smooth' && typeof a.v === 'number' && typeof b.v === 'number') {
          // A curve through the keys (Catmull-Rom tangents in time): no stop at each key. Same as the engine.
          const pa = frames[i - 1], nb = frames[i + 2];
          const m0 = pa && b.t - pa.t > 1e-9 ? (b.v - pa.v) / (b.t - pa.t) : 0;
          const m1 = nb && nb.t - a.t > 1e-9 ? (nb.v - a.v) / (nb.t - a.t) : 0;
          const s = (t - a.t) / span, s2 = s * s, s3 = s2 * s;
          return (2 * s3 - 3 * s2 + 1) * a.v + (s3 - 2 * s2 + s) * span * m0 + (-2 * s3 + 3 * s2) * b.v + (s3 - s2) * span * m1;
        }
        const p = easeFn(a.ease)((t - a.t) / span);
        return lerp(a.v, b.v, p, COLOR_PROPS.has(prop));
      }
    }
    return last.v;
  }

  const TRANSFORM_DEFAULTS = { x: 0, y: 0, scale: 1, scaleX: 1, scaleY: 1, rotation: 0, opacity: 1, blur: 0, draw: 1,
    brightness: 0, contrast: 0, saturation: 0, hue: 0, temperature: 0, fade: 0 };
  // The order the engine takes them in (color_ops in sqengine.c).
  const COLOR_ADJUST = ['brightness', 'contrast', 'saturation', 'hue', 'temperature', 'fade'];

  /* ── transitions ────────────────────────────────── */
  const TRANSITIONS = ['none', 'fade', 'slide-left', 'slide-right', 'slide-up', 'slide-down', 'zoom', 'blur',
    'wipe-left', 'wipe-right', 'wipe-up', 'wipe-down', 'iris'];
  const TRANSITION_NAMES = { none: 'Tanpa', fade: 'Pudar', 'slide-left': 'Geser ke kiri', 'slide-right': 'Geser ke kanan',
    'slide-up': 'Geser ke atas', 'slide-down': 'Geser ke bawah', zoom: 'Zoom', blur: 'Blur', 'wipe-left': 'Sapu ke kiri',
    'wipe-right': 'Sapu ke kanan', 'wipe-up': 'Sapu ke atas', 'wipe-down': 'Sapu ke bawah', iris: 'Lingkaran' };
  const WIPES = new Set(['wipe-left', 'wipe-right', 'wipe-up', 'wipe-down', 'iris']);

  /** How much (0 shown .. 1 gone) a transition takes away at time t. The ease shapes the way in;
   *  the way out is its mirror, so one ease reads naturally at both ends. */
  function transAmount(tr, t, start, end, out) {
    if (!tr || !tr.type || tr.type === 'none' || !(tr.duration > 0)) return 0;
    let p = out ? (end - t) / tr.duration : (t - start) / tr.duration;
    if (p >= 1) return 0;
    if (p < 0) p = 0;
    return 1 - easeFn(tr.ease || 'out')(p);
  }

  function applyTransitions(layer, s, t, doc) {
    s.wipes = [];
    const W = doc.width || 1000, H = doc.height || 1000, start = layer.start || 0, end = layer.end ?? Infinity;
    for (const [tr, out] of [[layer.transIn, false], [layer.transOut, true]]) {
      const a = transAmount(tr, t, start, end, out);
      if (!a) continue;
      const sign = out ? -1 : 1;
      switch (tr.type) {
        case 'fade': s.opacity *= 1 - a; break;
        case 'slide-left': s.x += sign * a * W; break;
        case 'slide-right': s.x -= sign * a * W; break;
        case 'slide-up': s.y += sign * a * H; break;
        case 'slide-down': s.y -= sign * a * H; break;
        case 'zoom': s.scale *= out ? 1 + 0.3 * a : 1 - 0.3 * a; s.opacity *= 1 - a; break;
        case 'blur': s.blur += a * 0.03 * Math.min(W, H); s.opacity *= 1 - a; break;
        default:
          if (WIPES.has(tr.type) && a > 0 && s.wipes.length < 2) s.wipes.push({ type: tr.type, out, a: Math.min(a, 1) });
      }
    }
  }

  /** Every drawable property of a layer at time t. With the document, its transitions too (drawing);
   *  without, the layer's own values (editing: handles, dragging). */
  function layerState(layer, t, doc) {
    const p = layer.props || {};
    const k = layer.keys || {};
    const s = {};
    for (const name of Object.keys(TRANSFORM_DEFAULTS)) s[name] = valueAt(k, name, t, p[name] ?? TRANSFORM_DEFAULTS[name]);
    for (const name of ['w', 'h', 'radius', 'size', 'strokeWidth']) s[name] = valueAt(k, name, t, layer[name] ?? 0);
    s.fill = valueAt(k, 'fill', t, layer.fill ?? null);
    s.stroke = valueAt(k, 'stroke', t, layer.stroke ?? null);
    if (doc) applyTransitions(layer, s, t, doc); else s.wipes = [];
    return s;
  }

  const visible = (layer, t) => !layer.hidden && t >= (layer.start || 0) && t < (layer.end ?? Infinity);
  /** The track a layer or clip sits on, if any (hidden, locked and muted apply to everything on it). */
  const trackOf = (doc, item) => (item && item.track ? (doc.tracks || []).find(tr => tr.id === item.track) || null : null);

  /* ── geometry ───────────────────────────────────── */
  const pathLengths = new Map();
  function pathLength(d) {
    if (pathLengths.has(d)) return pathLengths.get(d);
    let length = 1000;
    try {
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      svg.setAttribute('d', d);
      length = svg.getTotalLength() || length;
    } catch (_) { /* invalid path: a guess is fine for the dash */ }
    pathLengths.set(d, length);
    return length;
  }

  function polygonPoints(sides, radius, star, inner) {
    const n = Math.max(3, Math.min(24, Math.round(sides || 5)));
    const pts = [];
    const count = star ? n * 2 : n;
    for (let i = 0; i < count; i++) {
      const r = star && i % 2 ? radius * (inner ?? 0.5) : radius;
      const a = -Math.PI / 2 + (i * Math.PI * 2) / count;
      pts.push([Math.cos(a) * r, Math.sin(a) * r]);
    }
    return pts;
  }

  /** The layer's own box (centred on 0,0), for hit tests and handles. */
  function size(layer, s, ctx) {
    switch (layer.type) {
      case 'text': {
        const fontSize = s.size || layer.size || 48;
        let lines = String(layer.text || '').split('\n');
        let width = layer.width || 0;
        if (ctx) {
          ctx.save();
          ctx.font = fontOf(layer, fontSize);
          if (width) lines = wrap(ctx, layer.text || '', width);   // wrapped lines, as drawn (and as the engine counts)
          else width = Math.max(...lines.map(l => ctx.measureText(l).width), 10);
          ctx.restore();
        }
        return { w: width || fontSize * 4, h: lines.length * fontSize * (layer.lineHeight || 1.15) };
      }
      case 'polygon': { const r = s.radius || layer.radius || 100; return { w: r * 2, h: r * 2 }; }
      default: return { w: s.w || layer.w || 100, h: s.h || layer.h || 100 };
    }
  }

  const fontOf = (layer, px) => `${layer.weight || 400} ${px}px "${layer.font || 'sans-serif'}", system-ui, sans-serif`;

  function wrap(ctx, text, width) {
    if (!width) return String(text).split('\n');
    const out = [];
    for (const para of String(text).split('\n')) {
      let line = '';
      for (const word of para.split(/\s+/)) {
        const next = line ? `${line} ${word}` : word;
        if (ctx.measureText(next).width > width && line) { out.push(line); line = word; } else line = next;
      }
      out.push(line);
    }
    return out;
  }

  function paint(ctx, s, length) {
    if (s.fill) { ctx.fillStyle = s.fill; ctx.fill(); }
    // draw 0 means nothing drawn yet: a zero-length dash with round caps would still leave a dot.
    if (s.stroke && s.strokeWidth > 0 && s.draw > 0.001) {
      ctx.lineWidth = s.strokeWidth;
      ctx.strokeStyle = s.stroke;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      if (s.draw < 1) { ctx.setLineDash([length * s.draw, length]); }
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  function drawLayer(ctx, layer, s, assets, gscale = 1) {
    ctx.save();
    ctx.translate(s.x, s.y);
    if (s.rotation) ctx.rotate((s.rotation * Math.PI) / 180);
    ctx.scale(s.scale * s.scaleX, s.scale * s.scaleY);
    ctx.globalAlpha *= Math.max(0, Math.min(1, s.opacity));
    // Blur is in video pixels (as in the engine); canvas filters ignore the transform, so scale it here.
    if (s.blur > 0) ctx.filter = `blur(${s.blur * gscale}px)`;
    const box = size(layer, s, ctx);
    switch (layer.type) {
      case 'rect': {
        ctx.beginPath();
        const r = Math.max(0, Math.min(s.radius || 0, box.w / 2, box.h / 2));
        if (ctx.roundRect) ctx.roundRect(-box.w / 2, -box.h / 2, box.w, box.h, r);
        else ctx.rect(-box.w / 2, -box.h / 2, box.w, box.h);
        paint(ctx, s, 2 * (box.w + box.h));
        break;
      }
      case 'ellipse': {
        ctx.beginPath();
        const a = box.w / 2, b = box.h / 2;
        ctx.ellipse(0, 0, Math.abs(a), Math.abs(b), 0, 0, Math.PI * 2);
        paint(ctx, s, Math.PI * (3 * (a + b) - Math.sqrt((3 * a + b) * (a + 3 * b))));
        break;
      }
      case 'polygon': {
        const pts = polygonPoints(layer.sides, s.radius || layer.radius || 100, layer.star, layer.inner);
        ctx.beginPath();
        pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.closePath();
        let length = 0;
        pts.forEach(([x, y], i) => { const [nx, ny] = pts[(i + 1) % pts.length]; length += Math.hypot(nx - x, ny - y); });
        paint(ctx, s, length);
        break;
      }
      case 'path': {
        if (!layer.d) break;
        let path;
        try { path = new Path2D(layer.d); } catch (_) { break; }
        ctx.translate(-box.w / 2, -box.h / 2);
        if (s.fill) { ctx.fillStyle = s.fill; ctx.fill(path); }
        if (s.stroke && s.strokeWidth > 0 && s.draw > 0.001) {
          ctx.lineWidth = s.strokeWidth; ctx.strokeStyle = s.stroke; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
          const length = pathLength(layer.d);
          if (s.draw < 1) ctx.setLineDash([length * s.draw, length]);
          ctx.stroke(path);
          ctx.setLineDash([]);
        }
        break;
      }
      case 'video': {
        const v = assets && assets.videos && assets.videos[layer.src];
        if (!v || v.readyState < 2 || !v.videoWidth) {
          ctx.fillStyle = 'rgba(0,0,0,.35)'; ctx.fillRect(-box.w / 2, -box.h / 2, box.w, box.h);
          break;
        }
        const scale = (layer.fit === 'cover' ? Math.max : Math.min)(box.w / v.videoWidth, box.h / v.videoHeight);
        const dw = v.videoWidth * scale, dh = v.videoHeight * scale;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(-box.w / 2, -box.h / 2, box.w, box.h, Math.max(0, s.radius || 0)); else ctx.rect(-box.w / 2, -box.h / 2, box.w, box.h);
        ctx.clip();
        ctx.drawImage(v, -dw / 2, -dh / 2, dw, dh);
        break;
      }
      case 'image': {
        const img = assets && assets.images && assets.images[layer.src];
        if (!img || !img.complete || !img.naturalWidth) {
          ctx.strokeStyle = 'rgba(128,128,128,.6)'; ctx.setLineDash([8, 6]); ctx.strokeRect(-box.w / 2, -box.h / 2, box.w, box.h);
          break;
        }
        const scale = (layer.fit === 'cover' ? Math.max : Math.min)(box.w / img.naturalWidth, box.h / img.naturalHeight);
        const dw = img.naturalWidth * scale, dh = img.naturalHeight * scale;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(-box.w / 2, -box.h / 2, box.w, box.h, Math.max(0, s.radius || 0)); else ctx.rect(-box.w / 2, -box.h / 2, box.w, box.h);
        ctx.clip();
        ctx.drawImage(img, -dw / 2, -dh / 2, dw, dh);
        break;
      }
      case 'text': {
        const px = s.size || layer.size || 48;
        ctx.font = fontOf(layer, px);
        ctx.textBaseline = 'middle';
        const lines = wrap(ctx, layer.text || '', layer.width || 0);
        const lh = px * (layer.lineHeight || 1.15);
        const widths = lines.map(l => ctx.measureText(l).width);
        const blockW = layer.width || Math.max(...widths, 0);
        const align = layer.align || 'center';
        // `draw` on text types it in, letter by letter.
        let budget = s.draw < 1 ? Math.round(lines.join('').length * s.draw) : Infinity;
        lines.forEach((line, i) => {
          if (budget <= 0) return;
          const shown = line.slice(0, budget);
          budget -= line.length;
          const y = -((lines.length - 1) * lh) / 2 + i * lh;
          const x = align === 'left' ? -blockW / 2 : align === 'right' ? blockW / 2 - widths[i] : -widths[i] / 2;
          if (s.stroke && s.strokeWidth > 0) {
            ctx.lineWidth = s.strokeWidth; ctx.strokeStyle = s.stroke; ctx.lineJoin = 'round';
            ctx.strokeText(shown, x, y);
          }
          if (s.fill) { ctx.fillStyle = s.fill; ctx.fillText(shown, x, y); }
        });
        break;
      }
      default: break;
    }
    ctx.restore();
  }

  /* ── effects: colour, chroma key, LUT, mask, wipes ─ */
  // The same arithmetic as effect_pixel() and coverage() in the C engine. Values are straight (not
  // premultiplied) 0..1; the order is key, LUT, temperature, brightness, contrast, saturation, hue.

  /** A .cube 3D LUT as {n, data, dmin, dmax}, or null when it is not one. */
  function parseCube(text) {
    let n = 0, data = null, count = 0;
    const dmin = [0, 0, 0], dmax = [1, 1, 1];
    for (const raw of String(text).split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line[0] === '#') continue;
      if (line.startsWith('LUT_3D_SIZE')) {
        n = parseInt(line.slice(11), 10);
        if (!(n >= 2 && n <= 256) || data) return null;
        data = new Float32Array(n * n * n * 3);
        continue;
      }
      if (line.startsWith('DOMAIN_MIN')) { line.slice(10).trim().split(/\s+/).slice(0, 3).forEach((v, i) => { dmin[i] = +v; }); continue; }
      if (line.startsWith('DOMAIN_MAX')) { line.slice(10).trim().split(/\s+/).slice(0, 3).forEach((v, i) => { dmax[i] = +v; }); continue; }
      if (/^[A-Za-z]/.test(line)) continue;
      if (!data || count >= n * n * n) continue;
      const v = line.split(/\s+/).slice(0, 3).map(Number);
      if (v.length === 3 && v.every(Number.isFinite)) { data.set(v, count * 3); count++; }
    }
    return data && count === n * n * n ? { n, data, dmin, dmax } : null;
  }

  function lutSample(lut, rgb, out) {
    const n = lut.n, i0 = [0, 0, 0], fr = [0, 0, 0];
    for (let k = 0; k < 3; k++) {
      const span = lut.dmax[k] - lut.dmin[k];
      let c = span > 0 ? (rgb[k] - lut.dmin[k]) / span : rgb[k];
      c = Math.min(1, Math.max(0, c)) * (n - 1);
      let i = Math.floor(c);
      if (i > n - 2) i = n - 2;
      i0[k] = i; fr[k] = c - i;
    }
    out[0] = out[1] = out[2] = 0;
    for (let corner = 0; corner < 8; corner++) {
      const dr = corner & 1, dg = (corner >> 1) & 1, db = (corner >> 2) & 1;
      const w = (dr ? fr[0] : 1 - fr[0]) * (dg ? fr[1] : 1 - fr[1]) * (db ? fr[2] : 1 - fr[2]);
      if (!w) continue;
      const o = 3 * ((i0[0] + dr) + (i0[1] + dg) * n + (i0[2] + db) * n * n);
      out[0] += lut.data[o] * w; out[1] += lut.data[o + 1] * w; out[2] += lut.data[o + 2] * w;
    }
    return out;
  }

  const clamp01 = v => (v < 0 ? 0 : v > 1 ? 1 : v);

  /** What effects a layer has at this moment, or null when it draws as it is. */
  function effectsOf(layer, s, assets) {
    const ch = layer.chroma && layer.chroma.color ? layer.chroma : null;
    const lut = layer.lut && layer.lut.src && (layer.lut.mix ?? 1) > 0 && assets && assets.luts ? assets.luts[layer.lut.src] : null;
    const mask = layer.mask && (layer.mask.shape === 'rect' || layer.mask.shape === 'ellipse') ? layer.mask : null;
    const color = COLOR_ADJUST.map(name => s[name] || 0);
    if (!ch && !lut && !mask && !s.wipes.length && !color.some(Boolean)) return null;
    const fx = { color, lut, lutMix: lut ? clamp01(layer.lut.mix ?? 1) : 0, mask, wipes: s.wipes, chroma: null };
    if (ch) {
      const [r, g, b] = hexToRgba(ch.color).map((v, i) => (i < 3 ? v / 255 : v));
      const y = 0.299 * r + 0.587 * g + 0.114 * b;
      fx.chroma = { kcb: (b - y) * 0.564, kcr: (r - y) * 0.713, sim: ch.similarity ?? 0.25, soft: ch.smoothness ?? 0.08, spill: ch.spill ?? 0.5 };
    }
    return fx;
  }

  /** One pixel through the effects: rgb (0..1, changed in place) and alpha in, alpha out. */
  const lutOut = [0, 0, 0];
  function effectPixel(fx, rgb, alpha) {
    let [r, g, b] = rgb;
    const k = fx.chroma;
    if (k) {
      const y = 0.299 * r + 0.587 * g + 0.114 * b;
      let cb = (b - y) * 0.564, cr = (r - y) * 0.713;
      const d = Math.hypot(cb - k.kcb, cr - k.kcr);
      alpha *= clamp01((d - k.sim * 0.5) / Math.max(1e-4, k.soft * 0.5));
      const klen = Math.hypot(k.kcb, k.kcr);
      if (k.spill > 0 && klen > 1e-6) {
        const proj = (cb * k.kcb + cr * k.kcr) / klen;
        if (proj > 0) {
          cb -= k.spill * proj * k.kcb / klen;
          cr -= k.spill * proj * k.kcr / klen;
          r = y + cr / 0.713; b = y + cb / 0.564; g = (y - 0.299 * r - 0.114 * b) / 0.587;
        }
      }
    }
    if (fx.lut && fx.lutMix > 0) {
      lutSample(fx.lut, [clamp01(r), clamp01(g), clamp01(b)], lutOut);
      r += (lutOut[0] - r) * fx.lutMix; g += (lutOut[1] - g) * fx.lutMix; b += (lutOut[2] - b) * fx.lutMix;
    }
    rgb[0] = r; rgb[1] = g; rgb[2] = b;
    colorOps(fx.color, rgb);
    return alpha;
  }

  /** Colour adjustments in place, then 0..1: temperature, brightness, contrast, saturation, hue, fade.
   *  c = [brightness, contrast, saturation, hue, temperature, fade] (color_ops in the engine). */
  function colorOps(c, rgb) {
    let [r, g, b] = rgb;
    const [bright, contrast, sat, hue, temp, fade] = c;
    if (temp) { r += temp * 0.1; b -= temp * 0.1; }
    if (bright) { const m = 1 + bright; r *= m; g *= m; b *= m; }
    if (contrast) { const m = 1 + contrast; r = (r - 0.5) * m + 0.5; g = (g - 0.5) * m + 0.5; b = (b - 0.5) * m + 0.5; }
    if (sat) { const l = 0.2126 * r + 0.7152 * g + 0.0722 * b, m = 1 + sat; r = l + (r - l) * m; g = l + (g - l) * m; b = l + (b - l) * m; }
    if (hue) {
      const a = hue * Math.PI / 180, co = Math.cos(a), si = Math.sin(a);
      const nr = r * (0.213 + co * 0.787 - si * 0.213) + g * (0.715 - co * 0.715 - si * 0.715) + b * (0.072 - co * 0.072 + si * 0.928);
      const ng = r * (0.213 - co * 0.213 + si * 0.143) + g * (0.715 + co * 0.285 + si * 0.140) + b * (0.072 - co * 0.072 - si * 0.283);
      const nb = r * (0.213 - co * 0.213 - si * 0.787) + g * (0.715 - co * 0.715 + si * 0.715) + b * (0.072 + co * 0.928 + si * 0.072);
      r = nr; g = ng; b = nb;
    }
    if (fade) { const k = 0.22 * fade; r = r * (1 - k) + k; g = g * (1 - k) + k; b = b * (1 - k) + k; }
    rgb[0] = clamp01(r); rgb[1] = clamp01(g); rgb[2] = clamp01(b);
  }

  /* ── the video's filter (doc.grade), over the finished frame ── */
  const GRADE_KEYS = [...COLOR_ADJUST, 'vignette'];
  /** [brightness, contrast, saturation, hue, temperature, fade, vignette], or null when the video has no filter. */
  function gradeOf(doc) {
    const g = doc && doc.grade;
    if (!g) return null;
    const values = GRADE_KEYS.map(k => +g[k] || 0);
    return values.some(Boolean) ? values : null;
  }
  function vignetteAt(v, x, y, w, h) {
    const u = ((x + 0.5) / w - 0.5) * 2, q = ((y + 0.5) / h - 0.5) * 2;
    const k = clamp01((Math.sqrt((u * u + q * q) / 2) - 0.35) / 0.65);
    return 1 - v * 0.8 * k * k * (3 - 2 * k);
  }
  function applyGrade(ctx, grade) {
    const W = ctx.canvas.width, H = ctx.canvas.height;
    const img = ctx.getImageData(0, 0, W, H), d = img.data, rgb = [0, 0, 0], v = grade[6];
    for (let y = 0, i = 0; y < H; y++) {
      for (let x = 0; x < W; x++, i += 4) {
        const a = d[i + 3];
        if (!a) continue;
        rgb[0] = d[i] / 255; rgb[1] = d[i + 1] / 255; rgb[2] = d[i + 2] / 255;
        colorOps(grade, rgb);
        const k = v > 0 ? vignetteAt(v, x, y, W, H) : 1;
        d[i] = rgb[0] * k * 255 + 0.5; d[i + 1] = rgb[1] * k * 255 + 0.5; d[i + 2] = rgb[2] * k * 255 + 0.5;
      }
    }
    ctx.putImageData(img, 0, 0);
  }

  function sdRoundRect(px, py, hw, hh, r) {
    r = Math.min(r, Math.min(hw, hh));
    const qx = Math.abs(px) - hw + r, qy = Math.abs(py) - hh + r;
    return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r;
  }

  /** How much of the layer stays at layer-space point (x, y): mask times wipes, 0..1. s = device px per unit. */
  function coverage(fx, box, x, y, s) {
    let cov = 1;
    const m = fx.mask;
    if (m) {
      const hw = Math.abs(m.w ?? box.w) / 2, hh = Math.abs(m.h ?? box.h) / 2;
      const px = x - (m.x || 0), py = y - (m.y || 0);
      const d = m.shape === 'ellipse' ? (Math.hypot(px / Math.max(hw, 1e-3), py / Math.max(hh, 1e-3)) - 1) * Math.min(hw, hh)
        : sdRoundRect(px, py, hw, hh, Math.max(0, m.radius || 0));
      const c = clamp01(0.5 - d / (Math.max(0, m.feather || 0) + 1 / s));
      cov *= m.invert ? 1 - c : c;
    }
    for (const wipe of fx.wipes) {
      const { a, out } = wipe, w = box.w, h = box.h;
      let d;
      switch (wipe.type) {
        case 'wipe-right': d = out ? (-w / 2 + a * w) - x : x - (-w / 2 + (1 - a) * w); break;
        case 'wipe-left': d = out ? x - (w / 2 - a * w) : (w / 2 - (1 - a) * w) - x; break;
        case 'wipe-down': d = out ? (-h / 2 + a * h) - y : y - (-h / 2 + (1 - a) * h); break;
        case 'wipe-up': d = out ? y - (h / 2 - a * h) : (h / 2 - (1 - a) * h) - y; break;
        default: d = Math.hypot(x, y) - (1 - a) * Math.hypot(w, h) / 2;
      }
      cov *= clamp01(0.5 - d * s);
    }
    return cov;
  }

  let scratch = null;
  /** A layer with effects: drawn alone on a scratch canvas, its pixels run through the effects, then
   *  blurred (if it is) and composited, the same order as the engine. */
  function drawIsolated(ctx, layer, s, assets, fx, gscale) {
    const W = ctx.canvas.width, H = ctx.canvas.height;
    if (!scratch) scratch = document.createElement('canvas');
    if (scratch.width !== W || scratch.height !== H) { scratch.width = W; scratch.height = H; }
    const o = scratch.getContext('2d', { willReadFrequently: true });
    o.setTransform(1, 0, 0, 1, 0, 0);
    o.clearRect(0, 0, W, H);
    const base = ctx.getTransform();
    o.setTransform(base);
    drawLayer(o, layer, { ...s, blur: 0 }, assets);
    const m = base.translate(s.x, s.y).rotate(s.rotation || 0).scale(s.scale * s.scaleX, s.scale * s.scaleY);
    const det = Math.abs(m.a * m.d - m.b * m.c);
    if (det < 1e-12) return;
    const inv = m.inverse(), unit = Math.sqrt(det);
    const box = size(layer, s, o);
    // Where the layer can be: its box (plus its stroke) on the canvas; a path may draw outside its box.
    let x0 = 0, y0 = 0, x1 = W, y1 = H;
    if (layer.type !== 'path') {
      const pad = (s.strokeWidth || 0) + 2 / unit;
      const pts = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sx, sy]) => m.transformPoint({ x: sx * (box.w / 2 + pad), y: sy * (box.h / 2 + pad) }));
      x0 = Math.max(0, Math.floor(Math.min(...pts.map(p => p.x))) - 2); x1 = Math.min(W, Math.ceil(Math.max(...pts.map(p => p.x))) + 2);
      y0 = Math.max(0, Math.floor(Math.min(...pts.map(p => p.y))) - 2); y1 = Math.min(H, Math.ceil(Math.max(...pts.map(p => p.y))) + 2);
    }
    if (x1 > x0 && y1 > y0) {
      const img = o.getImageData(x0, y0, x1 - x0, y1 - y0);
      const d = img.data, rgb = [0, 0, 0], masked = !!(fx.mask || fx.wipes.length);
      const rowW = x1 - x0;
      for (let i = 0, n = d.length; i < n; i += 4) {
        const a0 = d[i + 3];
        if (!a0) continue;
        rgb[0] = d[i] / 255; rgb[1] = d[i + 1] / 255; rgb[2] = d[i + 2] / 255;
        let a = effectPixel(fx, rgb, a0 / 255);
        if (masked) {
          const px = x0 + ((i >> 2) % rowW) + 0.5, py = y0 + Math.floor((i >> 2) / rowW) + 0.5;
          a *= coverage(fx, box, inv.a * px + inv.c * py + inv.e, inv.b * px + inv.d * py + inv.f, unit);
        }
        d[i] = rgb[0] * 255 + 0.5; d[i + 1] = rgb[1] * 255 + 0.5; d[i + 2] = rgb[2] * 255 + 0.5; d[i + 3] = a * 255 + 0.5;
      }
      o.putImageData(img, x0, y0);
    }
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    if (s.blur > 0) ctx.filter = `blur(${s.blur * gscale}px)`;
    ctx.drawImage(scratch, 0, 0);
    ctx.restore();
  }

  /** One whole frame. ctx is already scaled to video pixels. */
  function drawFrame(ctx, doc, t, assets) {
    ctx.save();
    ctx.fillStyle = doc.background || '#000000';
    ctx.fillRect(0, 0, doc.width, doc.height);
    const g = ctx.getTransform(), gscale = Math.sqrt(Math.abs(g.a * g.d - g.b * g.c)) || 1;
    for (const layer of doc.layers || []) {
      if (!visible(layer, t)) continue;
      const track = trackOf(doc, layer);
      if (track && track.hidden) continue;
      const s = layerState(layer, t, doc);
      const fx = effectsOf(layer, s, assets);
      if (fx) drawIsolated(ctx, layer, s, assets, fx, gscale); else drawLayer(ctx, layer, s, assets, gscale);
    }
    ctx.restore();
    const grade = gradeOf(doc);
    if (grade) applyGrade(ctx, grade);
  }

  /** The topmost visible layer under a point in video pixels, or null. */
  function hitTest(ctx, doc, t, x, y) {
    const layers = doc.layers || [];
    for (let i = layers.length - 1; i >= 0; i--) {
      const layer = layers[i];
      const track = trackOf(doc, layer);
      if (!visible(layer, t) || layer.locked || (track && (track.hidden || track.locked))) continue;
      const s = layerState(layer, t);
      const box = size(layer, s, ctx);
      const a = (-(s.rotation || 0) * Math.PI) / 180;
      const dx = x - s.x, dy = y - s.y;
      const lx = (dx * Math.cos(a) - dy * Math.sin(a)) / (s.scale * s.scaleX || 1);
      const ly = (dx * Math.sin(a) + dy * Math.cos(a)) / (s.scale * s.scaleY || 1);
      if (Math.abs(lx) <= box.w / 2 + 6 && Math.abs(ly) <= box.h / 2 + 6) return layer;
    }
    return null;
  }

  /* ── video clips ────────────────────────────────── */
  /** Where in its file a video layer is at timeline time t. */
  const clipTime = (layer, t) => (layer.clipStart || 0) + Math.max(0, t - (layer.start || 0)) * (layer.speed || 1);

  /** Put every visible clip on its exact frame and wait until each can be drawn (export, stills, scrubbing). */
  async function seekVideos(doc, t, videos) {
    const waits = [];
    for (const layer of doc.layers || []) {
      if (layer.type !== 'video' || !visible(layer, t)) continue;
      const v = videos[layer.src];
      if (!v) continue;
      const target = Math.min(Math.max(0, clipTime(layer, t)), Math.max(0, (v.duration || Infinity) - 0.001));
      if (Math.abs(v.currentTime - target) < 0.0005 && v.readyState >= 2) continue;
      waits.push(new Promise(resolve => {
        const done = () => { clearTimeout(timer); v.removeEventListener('seeked', done); resolve(); };
        const timer = setTimeout(done, 3000);
        v.addEventListener('seeked', done);
        v.currentTime = target;
      }));
    }
    await Promise.all(waits);
  }

  /* ── audio ──────────────────────────────────────── */
  /** Schedule every clip on an AudioContext (live or offline) from timeline time `from`. Returns a stop function. */
  function scheduleAudio(actx, doc, buffers, from, destination) {
    const nodes = [];
    const now = actx.currentTime;
    // A video clip's own sound plays like an audio clip that starts and ends with the layer.
    const silent = item => { const tr = trackOf(doc, item); return !!(tr && (tr.muted || tr.hidden)); };
    const fromVideos = (doc.layers || []).filter(l => l.type === 'video' && !l.muted && !l.hidden && !silent(l) && (l.volume ?? 1) > 0).map(l => ({
      src: l.src, start: l.start || 0, offset: l.clipStart || 0, rate: l.speed || 1, volume: l.volume ?? 1, keys: l.keys,
      duration: (Math.min(l.end ?? doc.duration, doc.duration) - (l.start || 0)) * (l.speed || 1), fadeIn: 0, fadeOut: 0,
    }));
    for (const clip of [...(doc.audio || []), ...fromVideos]) {
      const buffer = buffers[clip.src];
      if (!buffer || clip.muted || silent(clip)) continue;
      const rate = clip.rate || 1;
      const length = (clip.duration ?? Math.max(0, buffer.duration - (clip.offset || 0))) / rate;
      const clipStart = clip.start || 0, clipEnd = Math.min(clipStart + length, doc.duration);
      if (clipEnd <= from) continue;
      const startIn = Math.max(0, clipStart - from);
      const skip = Math.max(0, from - clipStart);
      const src = actx.createBufferSource();
      src.buffer = buffer;
      src.playbackRate.value = rate;
      const gain = actx.createGain();
      // Volume, fades and volume keys, sampled every 1/50 s so any curve is followed.
      const step = 0.02;
      for (let tt = Math.max(clipStart, from); tt <= clipEnd + step; tt += step) {
        const local = tt - clipStart;
        let v = valueAt(clip.keys, 'volume', tt, clip.volume ?? 1);
        if (clip.fadeIn > 0 && local < clip.fadeIn) v *= local / clip.fadeIn;
        if (clip.fadeOut > 0 && clipEnd - tt < clip.fadeOut) v *= Math.max(0, (clipEnd - tt) / clip.fadeOut);
        gain.gain.setValueAtTime(Math.max(0, v), now + (tt - from));
      }
      src.connect(gain).connect(destination || actx.destination);
      src.start(now + startIn, (clip.offset || 0) + skip * rate, Math.max(0, clipEnd - Math.max(clipStart, from)) * rate);
      nodes.push(src);
    }
    return () => nodes.forEach(n => { try { n.stop(); } catch (_) { /* already ended */ } });
  }

  window.StudioRender = {
    EASE, EASE_NAMES, easeFn, bezierOf, TRANSITIONS, TRANSITION_NAMES, COLOR_ADJUST, valueAt, layerState, drawFrame, hitTest,
    size, scheduleAudio, visible, trackOf, hexToRgba, seekVideos, clipTime, parseCube, lutSample, effectsOf, effectPixel,
    coverage, transAmount, colorOps, gradeOf, vignetteAt,
  };
})();
