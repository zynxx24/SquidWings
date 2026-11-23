/* Studio renderer: draws one frame of a motion.json at time t. Shared by the preview and the export,
 * so what the editor shows is what the MP4 gets. The rules match utils/studio_doc.py. */
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
  };
  const EASE_NAMES = { linear: 'Linear', in: 'Pelan di awal', out: 'Pelan di akhir', inOut: 'Pelan di awal & akhir',
    outBack: 'Memantul lewat', outElastic: 'Elastis', hold: 'Tahan (lompat)' };
  const COLOR_PROPS = new Set(['fill', 'stroke']);

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
        const p = (EASE[a.ease] || EASE.linear)((t - a.t) / span);
        return lerp(a.v, b.v, p, COLOR_PROPS.has(prop));
      }
    }
    return last.v;
  }

  const TRANSFORM_DEFAULTS = { x: 0, y: 0, scale: 1, scaleX: 1, scaleY: 1, rotation: 0, opacity: 1, blur: 0, draw: 1 };

  /** Every drawable property of a layer at time t. */
  function layerState(layer, t) {
    const p = layer.props || {};
    const k = layer.keys || {};
    const s = {};
    for (const name of Object.keys(TRANSFORM_DEFAULTS)) s[name] = valueAt(k, name, t, p[name] ?? TRANSFORM_DEFAULTS[name]);
    for (const name of ['w', 'h', 'radius', 'size', 'strokeWidth']) s[name] = valueAt(k, name, t, layer[name] ?? 0);
    s.fill = valueAt(k, 'fill', t, layer.fill ?? null);
    s.stroke = valueAt(k, 'stroke', t, layer.stroke ?? null);
    return s;
  }

  const visible = (layer, t) => !layer.hidden && t >= (layer.start || 0) && t < (layer.end ?? Infinity);

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
        const lines = String(layer.text || '').split('\n');
        const fontSize = s.size || layer.size || 48;
        let width = layer.width || 0;
        if (!width && ctx) {
          ctx.save();
          ctx.font = fontOf(layer, fontSize);
          width = Math.max(...lines.map(l => ctx.measureText(l).width), 10);
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

  function drawLayer(ctx, layer, s, assets) {
    ctx.save();
    ctx.translate(s.x, s.y);
    if (s.rotation) ctx.rotate((s.rotation * Math.PI) / 180);
    ctx.scale(s.scale * s.scaleX, s.scale * s.scaleY);
    ctx.globalAlpha *= Math.max(0, Math.min(1, s.opacity));
    if (s.blur > 0) ctx.filter = `blur(${s.blur}px)`;
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

  /** One whole frame. ctx is already scaled to video pixels. */
  function drawFrame(ctx, doc, t, assets) {
    ctx.save();
    ctx.fillStyle = doc.background || '#000000';
    ctx.fillRect(0, 0, doc.width, doc.height);
    for (const layer of doc.layers || []) {
      if (visible(layer, t)) drawLayer(ctx, layer, layerState(layer, t), assets);
    }
    ctx.restore();
  }

  /** The topmost visible layer under a point in video pixels, or null. */
  function hitTest(ctx, doc, t, x, y) {
    const layers = doc.layers || [];
    for (let i = layers.length - 1; i >= 0; i--) {
      const layer = layers[i];
      if (!visible(layer, t) || layer.locked) continue;
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
    const fromVideos = (doc.layers || []).filter(l => l.type === 'video' && !l.muted && !l.hidden && (l.volume ?? 1) > 0).map(l => ({
      src: l.src, start: l.start || 0, offset: l.clipStart || 0, rate: l.speed || 1, volume: l.volume ?? 1, keys: l.keys,
      duration: (Math.min(l.end ?? doc.duration, doc.duration) - (l.start || 0)) * (l.speed || 1), fadeIn: 0, fadeOut: 0,
    }));
    for (const clip of [...(doc.audio || []), ...fromVideos]) {
      const buffer = buffers[clip.src];
      if (!buffer || clip.muted) continue;
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

  window.StudioRender = { EASE, EASE_NAMES, valueAt, layerState, drawFrame, hitTest, size, scheduleAudio, visible, hexToRgba, seekVideos, clipTime };
})();
