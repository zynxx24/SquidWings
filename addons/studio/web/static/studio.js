/* Studio: edit a motion.json by hand, leave notes for the AI, and export an MP4.
 * The document format and its rules live in addons/studio/studio_doc.py; drawing lives in render.js.
 * Live updates (the AI's saves, render and proxy progress) arrive on one Server-Sent Events stream. */
(() => {
  'use strict';
  const R = window.StudioRender;
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const params = new URLSearchParams(location.search);
  let embedded = false;
  try { embedded = window.self !== window.top; } catch (_) { embedded = true; }

  // Where a property's static value lives: transform-like ones in layer.props, the rest on the layer.
  const IN_PROPS = new Set(['x', 'y', 'scale', 'scaleX', 'scaleY', 'rotation', 'opacity', 'blur', 'draw',
    'brightness', 'contrast', 'saturation', 'hue', 'temperature']);
  const ANIMATABLE = new Set(['x', 'y', 'scale', 'scaleX', 'scaleY', 'rotation', 'opacity', 'w', 'h', 'radius', 'size',
    'strokeWidth', 'draw', 'blur', 'fill', 'stroke', 'volume', 'brightness', 'contrast', 'saturation', 'hue', 'temperature']);
  const DEFAULTS = { scale: 1, scaleX: 1, scaleY: 1, rotation: 0, opacity: 1, blur: 0, draw: 1,
    brightness: 0, contrast: 0, saturation: 0, hue: 0, temperature: 0 };
  const COLOR_FIELDS = [['brightness', 'Kecerahan', 0.01], ['contrast', 'Kontras', 0.01], ['saturation', 'Saturasi', 0.01],
    ['hue', 'Rona °', 1], ['temperature', 'Suhu warna', 0.01]];
  const FONTS = ['Fraunces', 'Plus Jakarta Sans', 'DM Serif Display', 'DM Sans', 'Anton', 'Barlow', 'Bricolage Grotesque',
    'Space Grotesk', 'Playfair Display', 'Poppins', 'Montserrat', 'Bebas Neue', 'Caveat', 'Lexend', 'Syne', 'Unbounded',
    'Archivo Black', 'JetBrains Mono', 'Cormorant Garamond', 'Shrikhand', 'Abril Fatface', 'Baloo 2', 'Fredoka'];
  const TYPE_NAMES = { text: 'Teks', rect: 'Kotak', ellipse: 'Elips', polygon: 'Poligon', path: 'Path', image: 'Gambar', video: 'Video' };
  const TRANSFORM_FIELDS = [['x', 'X'], ['y', 'Y'], ['scale', 'Skala', 0.01], ['rotation', 'Rotasi °'], ['opacity', 'Opasitas', 0.01], ['blur', 'Blur'], ['draw', 'Muncul (0-1)', 0.01]];
  const STYLE_FIELDS = {
    text: [['text', 'Teks', 'textarea'], ['font', 'Font', 'font'], ['weight', 'Tebal', 'weight'], ['size', 'Ukuran', 'num'],
      ['align', 'Rata', 'align'], ['fill', 'Warna', 'color'], ['stroke', 'Garis', 'color'], ['strokeWidth', 'Tebal garis', 'num'],
      ['lineHeight', 'Spasi baris', 'num', 0.05], ['width', 'Lebar bungkus', 'num']],
    rect: [['w', 'Lebar', 'num'], ['h', 'Tinggi', 'num'], ['radius', 'Sudut', 'num'], ['fill', 'Isi', 'color'], ['stroke', 'Garis', 'color'], ['strokeWidth', 'Tebal garis', 'num']],
    ellipse: [['w', 'Lebar', 'num'], ['h', 'Tinggi', 'num'], ['fill', 'Isi', 'color'], ['stroke', 'Garis', 'color'], ['strokeWidth', 'Tebal garis', 'num']],
    polygon: [['radius', 'Jari-jari', 'num'], ['sides', 'Sisi', 'int'], ['star', 'Bintang', 'bool'], ['inner', 'Dalam (0-1)', 'num', 0.05],
      ['fill', 'Isi', 'color'], ['stroke', 'Garis', 'color'], ['strokeWidth', 'Tebal garis', 'num']],
    path: [['d', 'Path SVG', 'textarea'], ['w', 'Lebar kotak', 'num'], ['h', 'Tinggi kotak', 'num'], ['fill', 'Isi', 'color'], ['stroke', 'Garis', 'color'], ['strokeWidth', 'Tebal garis', 'num']],
    image: [['src', 'File', 'asset'], ['w', 'Lebar', 'num'], ['h', 'Tinggi', 'num'], ['fit', 'Isi kotak', 'fit'], ['radius', 'Sudut', 'num']],
    video: [['src', 'File', 'asset'], ['w', 'Lebar', 'num'], ['h', 'Tinggi', 'num'], ['fit', 'Isi kotak', 'fit'], ['radius', 'Sudut', 'num'],
      ['clipStart', 'Mulai dari klip (dtk)', 'num', 0.04], ['speed', 'Kecepatan', 'num', 0.05], ['volume', 'Volume', 'num', 0.05], ['muted', 'Bisu', 'bool']],
  };
  const LABEL_W = 150;

  const S = {
    session: params.get('session') || '', name: params.get('project') || '', doc: null, stamp: '0',
    dirty: false, saving: false, t: 0, playing: false, mode: 'select', zoom: 80,
    sel: null,              // {kind: 'layer'|'audio'|'note'|'doc', id}
    key: null,              // {layer, prop, index} for a selected keyframe
    past: [], future: [], images: {}, videos: {}, buffers: {}, peaks: {}, luts: {}, assetList: [],
    proxies: {}, useProxy: localStorage.getItem('studio-proxy') !== 'off', live: false, pendingStamp: null,
    secs: {}, renders: [],
    audioCtx: null, stopAudio: null, playStart: 0, playFrom: 0, draft: null, conflict: null,
  };

  /* ── API ───────────────────────────────────────── */
  const base = () => `/api/studio/${encodeURIComponent(S.session)}`;
  async function api(url, options = {}) {
    const response = await fetch(url, options);
    let data = {};
    try { data = await response.json(); } catch (_) { /* no body */ }
    if (!response.ok) {
      const detail = typeof data.detail === 'object' ? data.detail.message : data.detail;
      const error = new Error(detail || `Permintaan gagal (${response.status})`);
      error.status = response.status; error.data = data;
      throw error;
    }
    return data;
  }
  const json = body => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const assetUrl = src => `${base()}/project/${encodeURIComponent(S.name)}/asset/${encodeURIComponent(src.replace(/^assets\//, ''))}`;
  const proxyUrl = src => `${base()}/project/${encodeURIComponent(S.name)}/proxy/${encodeURIComponent(src.replace(/^assets\//, ''))}`;
  /** Where the editor reads a video clip from: its proxy when one is ready (and proxies are on), else the file. */
  const mediaUrl = src => (S.useProxy && S.proxies[src] && S.proxies[src].ready ? proxyUrl(src) : assetUrl(src));

  function setSave(text, kind = '') {
    if (!kind) kind = /^Tersimpan/.test(text) ? 'ok' : /Menyimpan/i.test(text) ? 'busy' : '';
    $('save-state').textContent = text; $('save-state').dataset.kind = kind;
  }
  function banner(html) { const b = $('banner'); b.innerHTML = html; b.hidden = !html; }

  /* ── loading ───────────────────────────────────── */
  async function loadProjects() {
    if (!S.session) { emptyStage('session'); return; }
    const { projects } = await api(`${base()}/projects`);
    const select = $('project-select');
    select.innerHTML = projects.length ? projects.map(p => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('')
      : '<option value="">Belum ada proyek</option>';
    if (!S.name && projects.length) S.name = projects[0].name;
    if (S.name && !projects.some(p => p.name === S.name)) S.name = projects.length ? projects[0].name : '';
    select.value = S.name;
    if (S.name) await openProject(S.name); else emptyStage('none');
  }

  /* No project to show: say why in the middle of the stage, with the one thing to do next. */
  const EMPTY = {
    session: ['Buka Studio dari sebuah chat', 'Setiap chat punya folder dan proyek videonya sendiri. Buka chat, lalu tekan Studio di sana, atau minta AI membuat video.',
      [['Ke chat', 'chat', true]]],
    none: ['Belum ada video di chat ini', 'Mulai dari kanvas kosong dengan bentuk yang kamu pilih, atau tulis di chat apa videonya, AI menyusun lapisan dan animasinya di sini.',
      [['Buat video baru', 'new', true], ['Minta AI di chat', 'chat', false]]],
    error: ['Studio tidak bisa dibuka', 'Server tidak menjawab atau proyeknya rusak. Detailnya ada di pita atas.', [['Coba lagi', 'retry', true]]],
  };
  const EDIT_CONTROLS = ['undo', 'redo', 'send-ai', 'export', 'to-start', 'prev-frame', 'play', 'next-frame', 'zoom-in', 'zoom-out', 'add-track'];
  function setEditable(on) {
    document.body.classList.toggle('st-nodoc', !on);
    EDIT_CONTROLS.forEach(id => { $(id).disabled = !on; });
    document.querySelectorAll('[data-add], [data-mode], #audio-input').forEach(el => { el.disabled = !on; });
    $('new-project').disabled = !S.session;
  }
  function goChat() {
    if (window.SquidPage && window.SquidPage.embedded) window.parent.postMessage({ type: 'sq-nav', view: 'chat' }, '*');
    else location.href = '/';
  }
  function emptyStage(kind, detail = '') {
    S.doc = null;
    const [title, text, actions] = EMPTY[kind] || EMPTY.error;
    $('stage-empty-title').textContent = title;
    $('stage-empty-text').textContent = detail || text;
    $('stage-empty-actions').replaceChildren(...actions.map(([label, act, primary]) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = `st-btn${primary ? ' primary' : ''}`; b.textContent = label;
      b.onclick = act === 'new' ? openNewDialog : act === 'chat' ? goChat : () => loadProjects().catch(e => { banner(esc(e.message)); emptyStage('error'); });
      return b;
    }));
    $('stage-empty').hidden = false;
    $('inspector').innerHTML = `<p class="st-empty">${esc(kind === 'none' ? 'Properti lapisan muncul di sini setelah ada video.' : 'Belum ada proyek terbuka.')}</p>`;
    ['layer-list', 'audio-list', 'note-list', 'tl-rows', 'ruler'].forEach(id => { $(id).innerHTML = ''; });
    $('tl-rows').innerHTML = '<p class="st-empty st-tl-empty">Lapisan dan musik tampil sebagai batang di sini, dengan keyframe ◆ di atasnya.</p>';
    $('clock').textContent = '0:00.00 / 0:00.00';
    const ctx = $('view').getContext('2d'); ctx.clearRect(0, 0, $('view').width, $('view').height);
    setEditable(false);
    if (window.SquidPage) window.SquidPage.reveal([$('stage-empty')]);
  }

  async function openProject(name, keepView = false) {
    const data = await api(`${base()}/project/${encodeURIComponent(name)}`);
    S.name = name; S.doc = data.doc; S.stamp = data.stamp; S.dirty = false;
    const wasEmpty = !$('stage-empty').hidden || document.body.classList.contains('st-nodoc');
    $('stage-empty').hidden = true;
    setEditable(true);
    if (wasEmpty && window.SquidPage) window.SquidPage.reveal([$('canvas-wrap'), ...document.querySelectorAll('.st-left .st-panel')]);
    if (!keepView) { S.past = []; S.future = []; S.sel = { kind: 'doc' }; S.key = null; S.t = 0; fitZoom(); }
    S.t = Math.min(S.t, S.doc.duration);
    setSave('Tersimpan');
    Live.open();
    // Show the project at once; fonts, pictures and clips fill in as they arrive.
    renderAll();
    await Promise.all([loadFonts(), loadAssets()]);
    renderAll();
  }

  function loadFonts() {
    const families = new Set(S.doc.fonts || []);
    (S.doc.layers || []).forEach(l => { if (l.type === 'text' && l.font) families.add(l.font); });
    if (!families.size) return Promise.resolve();
    const query = [...families].map(f => `family=${encodeURIComponent(f)}:wght@300;400;500;600;700;800;900`).join('&');
    let link = document.getElementById('doc-fonts');
    if (!link) { link = document.createElement('link'); link.id = 'doc-fonts'; link.rel = 'stylesheet'; document.head.append(link); }
    const href = `/api/fonts/css2?${query}`;
    if (link.getAttribute('href') === href) return document.fonts.ready;
    return new Promise(resolve => {
      link.onload = async () => {
        const loads = [];
        (S.doc.layers || []).filter(l => l.type === 'text').forEach(l => loads.push(document.fonts.load(`${l.weight || 400} 40px "${l.font}"`).catch(() => null)));
        await Promise.all(loads);
        renderView();
        resolve();
      };
      link.onerror = () => {
        // A family Google lacks a weight of: ask without weights.
        const plain = `/api/fonts/css2?${[...families].map(f => `family=${encodeURIComponent(f)}`).join('&')}`;
        if (link.getAttribute('href') !== plain) link.href = plain; else resolve();
      };
      link.href = href;
    });
  }

  async function loadAssets() {
    const jobs = [];
    for (const layer of S.doc.layers || []) {
      if (layer.type === 'image' && layer.src && !S.images[layer.src]) {
        const img = new Image();
        img.decoding = 'async';
        img.src = assetUrl(layer.src);
        S.images[layer.src] = img;
        jobs.push(new Promise(r => { img.onload = () => { renderView(); r(); }; img.onerror = r; }));
      }
    }
    for (const clip of S.doc.audio || []) {
      if (clip.src && !S.buffers[clip.src]) jobs.push(loadAudio(clip.src));
    }
    for (const layer of S.doc.layers || []) {
      if (layer.type === 'video' && layer.src && !S.videos[layer.src]) jobs.push(loadVideo(layer.src));
    }
    jobs.push(loadAssetList().then(() => Promise.all((S.doc.layers || []).filter(l => l.lut && l.lut.src && !S.luts[l.lut.src]).map(l => loadLut(l.lut.src)))));
    await Promise.all(jobs);
  }

  /** The project's files (for the LUT list), and its clips' proxy state. */
  async function loadAssetList() {
    try {
      const [{ assets }, { proxies }] = await Promise.all([
        api(`${base()}/project/${encodeURIComponent(S.name)}/assets`),
        api(`${base()}/project/${encodeURIComponent(S.name)}/proxies`).catch(() => ({ proxies: {} })),
      ]);
      S.assetList = assets || [];
      if (!S.live) onProxies(proxies || {});
    } catch (_) { /* the list is a convenience */ }
  }

  async function loadLut(src) {
    S.luts[src] = S.luts[src] || null;
    try {
      const text = await (await fetch(assetUrl(src))).text();
      const lut = R.parseCube(text);
      if (!lut) { banner(`LUT <b>${esc(src)}</b> bukan file .cube 3D yang bisa dibaca.`); return; }
      S.luts[src] = lut;
      renderView();
    } catch (e) { banner(`LUT <b>${esc(src)}</b> tidak bisa dibuka: ${esc(e.message)}`); }
  }

  /** A clip for drawing (muted: its sound is mixed through Web Audio with the music, so it stays in sync). */
  function loadVideo(src) {
    const v = document.createElement('video');
    v.muted = true; v.playsInline = true; v.preload = 'auto'; v.crossOrigin = 'anonymous';
    v.dataset.url = mediaUrl(src);
    v.src = v.dataset.url;
    S.videos[src] = v;
    v.addEventListener('seeked', () => { if (!S.exporting) renderView(); });
    // Its soundtrack, if it has one (from the proxy when there is one: a smaller download); a clip without sound has none.
    if (!S.buffers[src]) loadAudio(src, true, v.dataset.url);
    // A clip that stalls must not hold the editor: give up waiting after a while (it still loads later).
    return new Promise(r => { const t = setTimeout(r, 8000); v.onloadeddata = () => { clearTimeout(t); renderView(); r(); }; v.onerror = () => { clearTimeout(t); r(); }; });
  }

  /** Clips follow the playhead: playing along while the video plays, on the exact frame when paused. */
  function syncVideos() {
    for (const layer of S.doc.layers || []) {
      if (layer.type !== 'video') continue;
      const v = S.videos[layer.src];
      if (!v || v.readyState < 1) continue;
      const on = R.visible(layer, S.t);
      const target = Math.min(R.clipTime(layer, S.t), Math.max(0, (v.duration || 1e9) - 0.01));
      if (S.playing && on) {
        v.playbackRate = layer.speed || 1;
        if (Math.abs(v.currentTime - target) > 0.25) v.currentTime = target;
        if (v.paused) v.play().catch(() => {});
      } else {
        if (!v.paused) v.pause();
        if (on && Math.abs(v.currentTime - target) > 0.02) v.currentTime = target;
      }
    }
  }

  /** Point every clip at its proxy or its file, as the setting and the ready proxies say. */
  async function refreshVideos() {
    const swaps = [];
    for (const [src, v] of Object.entries(S.videos)) {
      if (v.dataset.url === mediaUrl(src)) continue;
      const at = v.currentTime;
      v.dataset.url = mediaUrl(src);
      v.src = v.dataset.url;
      swaps.push(new Promise(r => {
        const t = setTimeout(r, 8000);
        v.addEventListener('loadeddata', () => { clearTimeout(t); v.currentTime = at; r(); }, { once: true });
        v.addEventListener('error', () => { clearTimeout(t); r(); }, { once: true });
      }));
    }
    await Promise.all(swaps);
    renderView();
  }

  async function loadAudio(src, quiet = false, url = '') {
    try {
      const data = await (await fetch(url || assetUrl(src))).arrayBuffer();
      S.audioCtx = S.audioCtx || new AudioContext();
      const buffer = await S.audioCtx.decodeAudioData(data);
      S.buffers[src] = buffer;
      S.peaks[src] = peaks(buffer);
      renderTimeline();
    } catch (e) { if (!quiet) banner(`Audio <b>${esc(src)}</b> tidak bisa dibuka: ${esc(e.message)}`); }
  }

  function peaks(buffer, perSecond = 80) {
    const data = buffer.getChannelData(0);
    const count = Math.ceil(buffer.duration * perSecond);
    const step = Math.max(1, Math.floor(data.length / count));
    const out = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      let max = 0;
      for (let j = i * step; j < Math.min(data.length, (i + 1) * step); j += 4) max = Math.max(max, Math.abs(data[j]));
      out[i] = max;
    }
    return { perSecond, values: out };
  }

  /* ── editing core ──────────────────────────────── */
  const snap = t => Math.max(0, Math.min(S.doc.duration, Math.round(t * S.doc.fps) / S.doc.fps));
  const frameEps = () => 0.5 / S.doc.fps;
  const layerById = id => (S.doc.layers || []).find(l => l.id === id);
  const clipById = id => (S.doc.audio || []).find(c => c.id === id);
  const trackById = id => (S.doc.tracks || []).find(t => t.id === id);
  const noteByN = n => (S.doc.notes || []).find(x => x.n === n);
  const selLayer = () => (S.sel && S.sel.kind === 'layer' ? layerById(S.sel.id) : null);

  function checkpoint() {
    S.past.push(JSON.stringify(S.doc));
    if (S.past.length > 120) S.past.shift();
    S.future = [];
  }
  /** Change the document: one undo step, a save soon, everything redrawn. */
  function mutate(fn, { record = true, light = false } = {}) {
    if (!S.doc) return;
    if (record) checkpoint();
    fn(S.doc);
    S.dirty = true;
    setSave('Belum disimpan…');
    scheduleSave();
    if (light) { renderView(); renderTimeline(); } else renderAll();
  }
  function undo() { if (!S.past.length) return; S.future.push(JSON.stringify(S.doc)); S.doc = JSON.parse(S.past.pop()); S.dirty = true; scheduleSave(); renderAll(); }
  function redo() { if (!S.future.length) return; S.past.push(JSON.stringify(S.doc)); S.doc = JSON.parse(S.future.pop()); S.dirty = true; scheduleSave(); renderAll(); }

  function staticValue(layer, prop) {
    if (IN_PROPS.has(prop)) return (layer.props || {})[prop] ?? DEFAULTS[prop] ?? 0;
    return layer[prop];
  }
  function currentValue(layer, prop) { return R.valueAt(layer.keys, prop, S.t, staticValue(layer, prop)); }
  function keyIndexAt(frames, t) { return (frames || []).findIndex(k => Math.abs(k.t - t) < frameEps()); }

  /** Set a property at the playhead: a keyframe when the property is animated, else its value. */
  function setValue(layer, prop, value) {
    const frames = layer.keys && layer.keys[prop];
    if (ANIMATABLE.has(prop) && frames && frames.length) {
      const t = snap(S.t);
      const i = keyIndexAt(frames, t);
      if (i >= 0) frames[i].v = value; else { frames.push({ t, v: value, ease: 'inOut' }); frames.sort((a, b) => a.t - b.t); }
    } else if (IN_PROPS.has(prop)) {
      layer.props = layer.props || {}; layer.props[prop] = value;
    } else layer[prop] = value;
  }

  function toggleKey(layer, prop) {
    mutate(() => {
      layer.keys = layer.keys || {};
      const t = snap(S.t);
      const frames = layer.keys[prop];
      const value = currentValue(layer, prop);
      if (!frames || !frames.length) { layer.keys[prop] = [{ t, v: value, ease: 'inOut' }]; return; }
      const i = keyIndexAt(frames, t);
      if (i >= 0) {
        frames.splice(i, 1);
        if (!frames.length) {
          delete layer.keys[prop];
          if (IN_PROPS.has(prop)) { layer.props[prop] = value; } else layer[prop] = value;
        }
      } else { frames.push({ t, v: value, ease: 'inOut' }); frames.sort((a, b) => a.t - b.t); }
    });
  }

  let saveTimer = 0;
  function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(save, 900); }
  async function save() {
    if (!S.dirty || S.saving || !S.doc) return;
    S.saving = true;
    setSave('Menyimpan…');
    const sent = JSON.stringify(S.doc);
    try {
      const data = await api(`${base()}/project/${encodeURIComponent(S.name)}`, { method: 'PUT', ...json({ doc: S.doc, base: S.stamp }) });
      S.stamp = data.stamp;
      if (JSON.stringify(S.doc) === sent) { S.dirty = false; setSave('Tersimpan'); } else scheduleSave();
      banner('');
      S.pendingStamp = S.pendingStamp === data.stamp ? null : S.pendingStamp;
    } catch (e) {
      if (e.status === 409) {
        S.conflict = true;
        setSave('Konflik', 'warn');
        banner('AI atau tab lain baru saja mengubah video ini. <button class="st-btn" id="take-theirs">Muat versi terbaru</button> <button class="st-btn" id="keep-mine">Timpa dengan versi saya</button>');
        $('take-theirs').onclick = async () => { S.conflict = null; banner(''); await openProject(S.name, true); };
        $('keep-mine').onclick = async () => { S.conflict = null; S.stamp = e.data.detail.stamp; banner(''); S.saving = false; await save(); };
      } else { setSave('Gagal menyimpan', 'warn'); banner(esc(e.message)); }
    } finally { S.saving = false; settlePending(); }
  }

  // The AI edits the same file: pick its change up when nothing here is unsaved. The stamp arrives on
  // the live stream; a save or playback in progress puts it aside until it is done.
  async function onStamp(stamp) {
    if (!S.doc || !S.name || stamp === S.stamp || stamp === '0') return;
    if (S.saving || S.playing) { S.pendingStamp = stamp; return; }
    if (S.dirty) {
      if (!S.conflict) { S.conflict = true; banner('Video ini diubah AI sementara kamu mengedit. <button class="st-btn" id="take-theirs">Muat versi AI</button> (perubahanmu yang belum tersimpan hilang)'); $('take-theirs').onclick = async () => { S.conflict = null; banner(''); await openProject(S.name, true); }; }
      return;
    }
    try {
      await openProject(S.name, true);
      setSave('Diperbarui oleh AI');
    } catch (_) { /* offline for a moment: the next stamp tries again */ }
  }
  function settlePending() { const p = S.pendingStamp; S.pendingStamp = null; if (p) onStamp(p); }

  /* One Server-Sent Events stream per open project: doc stamps, renders, proxies. The browser
     reconnects by itself after a dropped connection (a phone waking up); while it is down, a slow
     poll of the stamp stands in. */
  const Live = (() => {
    let es = null, key = '';
    function open() {
      if (!S.session || !S.name || !window.EventSource) return;
      const want = `${S.session}|${S.name}`;
      if (es && key === want && es.readyState !== EventSource.CLOSED) return;
      close();
      key = want;
      es = new EventSource(`${base()}/events?project=${encodeURIComponent(S.name)}`);
      es.onopen = () => { S.live = true; };
      es.onerror = () => { S.live = !!es && es.readyState === EventSource.OPEN; };
      es.addEventListener('doc', e => { try { onStamp(JSON.parse(e.data).stamp); } catch (_) { /* bad frame */ } });
      es.addEventListener('render', e => { try { onRenders(JSON.parse(e.data)); } catch (_) { /* bad frame */ } });
      es.addEventListener('proxy', e => { try { onProxies(JSON.parse(e.data)); } catch (_) { /* bad frame */ } });
    }
    function close() { if (es) es.close(); es = null; key = ''; S.live = false; }
    return { open, close };
  })();

  async function poll() {
    if (S.live || !S.doc || !S.name || document.hidden) return;
    try {
      const { stamp } = await api(`${base()}/project/${encodeURIComponent(S.name)}/stamp`);
      onStamp(stamp);
    } catch (_) { /* offline for a moment */ }
  }
  setInterval(poll, 4000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && S.doc) { Live.open(); poll(); } });

  /** Proxy state from the server: swap clips to a proxy as soon as it is ready. */
  function onProxies(map) {
    const before = JSON.stringify(S.proxies);
    S.proxies = map || {};
    if (JSON.stringify(S.proxies) === before) return;
    refreshVideos();
    const l = selLayer();
    if (l && l.type === 'video' && !drag && !document.activeElement?.closest?.('#inspector')) renderInspector();
  }

  /* ── preview ───────────────────────────────────── */
  let viewScale = 1;
  function fitCanvas() {
    if (!S.doc) return;
    const wrap = $('canvas-wrap');
    const pad = 24;
    const availW = Math.max(50, wrap.clientWidth - pad), availH = Math.max(50, wrap.clientHeight - pad);
    const k = Math.min(availW / S.doc.width, availH / S.doc.height);
    const cssW = Math.round(S.doc.width * k), cssH = Math.round(S.doc.height * k);
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    for (const c of [$('view'), $('overlay')]) {
      c.style.width = cssW + 'px'; c.style.height = cssH + 'px';
      c.width = Math.round(cssW * dpr); c.height = Math.round(cssH * dpr);
    }
    viewScale = (cssW * dpr) / S.doc.width;
  }
  new ResizeObserver(() => { fitCanvas(); renderView(); }).observe($('canvas-wrap'));

  function renderView() {
    if (!S.doc) return;
    const c = $('view');
    const ctx = c.getContext('2d');
    ctx.setTransform(viewScale, 0, 0, viewScale, 0, 0);
    if (!S.exporting) syncVideos();
    R.drawFrame(ctx, S.doc, S.t, { images: S.images, videos: S.videos, luts: S.luts });
    renderOverlay();
    $('clock').textContent = `${clock(S.t)} / ${clock(S.doc.duration)}`;
    const x = LABEL_W + S.t * S.zoom;
    $('playhead').style.left = (x - LABEL_W) + 'px';
  }

  function renderOverlay() {
    const c = $('overlay');
    const ctx = c.getContext('2d');
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.setTransform(viewScale, 0, 0, viewScale, 0, 0);
    const px = 1 / viewScale;
    // Notes near the playhead, as numbered boxes or pins.
    for (const note of S.doc.notes || []) {
      const near = note.end != null ? S.t >= note.t - 0.05 && S.t <= note.end + 0.05 : Math.abs(S.t - note.t) < 0.6;
      if (!near || (!note.box && !note.point)) continue;
      ctx.strokeStyle = note.status === 'done' ? '#8d877c' : '#e07a00';
      ctx.fillStyle = ctx.strokeStyle;
      ctx.lineWidth = 2.5 * px;
      if (note.box) { const [x, y, w, h] = note.box; ctx.setLineDash([8 * px, 5 * px]); ctx.strokeRect(x, y, w, h); ctx.setLineDash([]); label(ctx, note.n, x, y, px); }
      else { const [x, y] = note.point; ctx.beginPath(); ctx.arc(x, y, 9 * px, 0, Math.PI * 2); ctx.fill(); label(ctx, note.n, x + 10 * px, y - 10 * px, px); }
    }
    if (S.draft) {
      ctx.strokeStyle = '#e07a00'; ctx.lineWidth = 2 * px; ctx.setLineDash([6 * px, 4 * px]);
      ctx.strokeRect(S.draft.x, S.draft.y, S.draft.w, S.draft.h); ctx.setLineDash([]);
    }
    const layer = selLayer();
    if (layer && R.visible(layer, S.t)) {
      const s = R.layerState(layer, S.t);
      const box = R.size(layer, s, $('view').getContext('2d'));
      ctx.save();
      ctx.translate(s.x, s.y); ctx.rotate((s.rotation || 0) * Math.PI / 180); ctx.scale(s.scale * s.scaleX, s.scale * s.scaleY);
      const k = 1 / Math.abs(s.scale * s.scaleX || 1);
      ctx.strokeStyle = '#2f80ed'; ctx.lineWidth = 1.5 * px * k;
      ctx.strokeRect(-box.w / 2, -box.h / 2, box.w, box.h);
      ctx.fillStyle = '#fff';
      const hs = 6 * px * k;
      for (const [hx, hy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
        ctx.fillRect(hx * box.w / 2 - hs, hy * box.h / 2 - hs, hs * 2, hs * 2);
        ctx.strokeRect(hx * box.w / 2 - hs, hy * box.h / 2 - hs, hs * 2, hs * 2);
      }
      ctx.beginPath(); ctx.moveTo(0, -box.h / 2); ctx.lineTo(0, -box.h / 2 - 30 * px * k); ctx.stroke();
      ctx.beginPath(); ctx.arc(0, -box.h / 2 - 30 * px * k, hs, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.restore();
    }
  }
  function label(ctx, n, x, y, px) {
    ctx.save();
    ctx.font = `600 ${13 * px}px system-ui`;
    const w = ctx.measureText(String(n)).width + 10 * px;
    ctx.fillStyle = '#e07a00'; ctx.fillRect(x, y - 18 * px, w, 18 * px);
    ctx.fillStyle = '#fff'; ctx.fillText(String(n), x + 5 * px, y - 5 * px);
    ctx.restore();
  }
  const clock = t => { const m = Math.floor(t / 60); return `${m}:${(t - m * 60).toFixed(2).padStart(5, '0')}`; };

  /* ── canvas interaction ────────────────────────── */
  function toDoc(e) {
    const r = $('overlay').getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * S.doc.width, y: ((e.clientY - r.top) / r.height) * S.doc.height };
  }
  function handleAt(layer, p) {
    const s = R.layerState(layer, S.t);
    const box = R.size(layer, s, $('view').getContext('2d'));
    const a = -(s.rotation || 0) * Math.PI / 180;
    const dx = p.x - s.x, dy = p.y - s.y;
    const sx = s.scale * s.scaleX || 1, sy = s.scale * s.scaleY || 1;
    const lx = (dx * Math.cos(a) - dy * Math.sin(a)) / sx, ly = (dx * Math.sin(a) + dy * Math.cos(a)) / sy;
    const tol = 14 / viewScale / Math.abs(sx);
    if (Math.hypot(lx, ly - (-box.h / 2 - 30 / viewScale / Math.abs(sy))) < tol) return 'rotate';
    for (const [hx, hy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) if (Math.hypot(lx - hx * box.w / 2, ly - hy * box.h / 2) < tol) return 'scale';
    return null;
  }

  const overlay = $('overlay');
  overlay.addEventListener('pointerdown', e => {
    if (!S.doc) return;
    const p = toDoc(e);
    overlay.setPointerCapture(e.pointerId);
    if (S.mode === 'note') { S.draft = { x: p.x, y: p.y, w: 0, h: 0, x0: p.x, y0: p.y }; return; }
    let layer = selLayer();
    let action = layer && R.visible(layer, S.t) ? handleAt(layer, p) : null;
    if (!action) {
      const hit = R.hitTest($('view').getContext('2d'), S.doc, S.t, p.x, p.y);
      if (hit) { layer = hit; S.sel = { kind: 'layer', id: hit.id }; action = 'move'; renderAll(); }
      else { S.sel = { kind: 'doc' }; S.key = null; renderAll(); return; }
    }
    const track = layer.track && trackById(layer.track);
    if (track && track.locked) { setSave('Track ini dikunci', 'warn'); return; }
    checkpoint();
    const s = R.layerState(layer, S.t);
    drag = { action, layer, start: p, s, angle0: Math.atan2(p.y - s.y, p.x - s.x), dist0: Math.hypot(p.x - s.x, p.y - s.y) || 1 };
  });
  let drag = null;
  overlay.addEventListener('pointermove', e => {
    if (!S.doc) return;
    const p = toDoc(e);
    if (S.draft) {
      S.draft.x = Math.min(p.x, S.draft.x0); S.draft.y = Math.min(p.y, S.draft.y0);
      S.draft.w = Math.abs(p.x - S.draft.x0); S.draft.h = Math.abs(p.y - S.draft.y0);
      renderOverlay(); return;
    }
    if (!drag) {
      const layer = selLayer();
      const over = layer && R.visible(layer, S.t) ? handleAt(layer, p) : null;
      overlay.style.cursor = S.mode === 'note' ? 'crosshair' : over === 'rotate' ? 'grab' : over === 'scale' ? 'nwse-resize' : 'default';
      return;
    }
    const { layer, s } = drag;
    if (drag.action === 'move') {
      let nx = s.x + p.x - drag.start.x, ny = s.y + p.y - drag.start.y;
      if (e.shiftKey) { if (Math.abs(p.x - drag.start.x) > Math.abs(p.y - drag.start.y)) ny = s.y; else nx = s.x; }
      setValue(layer, 'x', Math.round(nx)); setValue(layer, 'y', Math.round(ny));
    } else if (drag.action === 'scale') {
      const d = Math.hypot(p.x - s.x, p.y - s.y);
      setValue(layer, 'scale', Math.max(0.01, +(s.scale * d / drag.dist0).toFixed(3)));
    } else if (drag.action === 'rotate') {
      let deg = s.rotation + (Math.atan2(p.y - s.y, p.x - s.x) - drag.angle0) * 180 / Math.PI;
      if (e.shiftKey) deg = Math.round(deg / 15) * 15;
      setValue(layer, 'rotation', Math.round(deg * 10) / 10);
    }
    S.dirty = true; scheduleSave(); setSave('Belum disimpan…');
    renderView(); renderTimeline(); renderInspector();
  });
  overlay.addEventListener('pointerup', e => {
    if (S.draft) {
      const d = S.draft; S.draft = null;
      const point = d.w < 8 && d.h < 8;
      addNote(point ? { point: [Math.round(d.x0), Math.round(d.y0)] } : { box: [d.x, d.y, d.w, d.h].map(Math.round) }, e.clientX, e.clientY);
      return;
    }
    drag = null;
    if (overlay.hasPointerCapture(e.pointerId)) overlay.releasePointerCapture(e.pointerId);
  });

  /* ── notes ─────────────────────────────────────── */
  /** A note where the user pointed: a box or a point on the frame, or a stretch of time. Asked in a small popover. */
  function addNote(where, clientX, clientY) {
    const pop = $('note-pop');
    const range = where.end != null;
    $('note-pop-where').textContent = range ? `Rentang ${clock(where.t)}–${clock(where.end)}`
      : `${where.box ? 'Kotak' : 'Titik'} di ${clock(snap(S.t))}`;
    $('note-pop-text').value = '';
    pop.hidden = false;
    const w = 290, h = 170;
    pop.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, (clientX || window.innerWidth / 2) - w / 2)) + 'px';
    pop.style.top = Math.max(8, Math.min(window.innerHeight - h - 8, (clientY || window.innerHeight / 2) + 12)) + 'px';
    $('note-pop-text').focus();
    const close = () => { pop.hidden = true; S.draft = null; S.rangeDraft = null; renderOverlay(); renderTimeline(); };
    $('note-pop-cancel').onclick = close;
    $('note-pop-save').onclick = () => {
      const text = $('note-pop-text').value.trim();
      if (!text) { $('note-pop-text').focus(); return; }
      const hit = range ? null : where.point ? R.hitTest($('view').getContext('2d'), S.doc, S.t, ...where.point)
        : R.hitTest($('view').getContext('2d'), S.doc, S.t, where.box[0] + where.box[2] / 2, where.box[1] + where.box[3] / 2);
      mutate(doc => {
        doc.notes = doc.notes || [];
        const n = Math.max(0, ...doc.notes.map(x => x.n || 0)) + 1;
        const item = range ? { t: +where.t.toFixed(3), end: +where.end.toFixed(3) } : { t: +snap(S.t).toFixed(3), ...where };
        doc.notes.push({ n, ...item, layer: hit ? hit.id : (selLayer() && range ? selLayer().id : null), text, status: 'open', by: 'user' });
        S.sel = { kind: 'note', id: n };
      });
      close();
    };
    $('note-pop-text').onkeydown = e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('note-pop-save').click(); }
      if (e.key === 'Escape') { e.preventDefault(); close(); }
    };
  }

  function sendNotesToAI() {
    if (!S.doc) return;
    const open = (S.doc.notes || []).filter(n => n.status !== 'done');
    if (!open.length) { setSave('Tidak ada catatan terbuka', 'warn'); return; }
    const lines = open.map(n => {
      const where = n.box ? `kotak x${n.box[0]} y${n.box[1]} ${n.box[2]}×${n.box[3]}` : n.point ? `titik x${n.point[0]} y${n.point[1]}` : 'seluruh frame';
      const when = n.end != null ? `${clock(n.t)}–${clock(n.end)}` : clock(n.t);
      return `#${n.n} [${when}, ${where}${n.layer ? `, lapisan ${n.layer}` : ''}] ${n.text}`;
    });
    save();
    showTab('chat');
    Chat.send(`Perbaiki video ini sesuai catatan:\n${lines.join('\n')}\n\nSetelah tiap catatan beres, tandai selesai dengan studio_note_done beserta penjelasan singkat.`);
  }

  /* ── chat with the AI, inside the editor ──────── */
  // The same chat as the main page (same session), sent with the editor's context: the server runs it
  // as a Studio turn (see utils/studio_context.py), and the video reloads when the AI has saved.
  const Chat = (() => {
    const log = $('chat-log');
    let ws = null, busy = false, loadedFor = '', bubble = null, text = '', steps = [];
    const html = t => (window.marked && window.DOMPurify ? window.DOMPurify.sanitize(window.marked.parse(t || '')) : esc(t).replace(/\n/g, '<br>'));
    function add(kind, inner) {
      const div = document.createElement('div');
      div.className = `st-msg ${kind}`;
      div.innerHTML = inner;
      log.append(div);
      log.scrollTop = log.scrollHeight;
      return div;
    }
    function context() {
      const layer = selLayer();
      return { project: S.name, t: +snap(S.t).toFixed(3), ...(layer ? { layer: layer.id } : {}), ...(S.pointBox ? { box: S.pointBox } : {}) };
    }
    function renderContext() {
      if (!S.doc) return;
      const layer = selLayer();
      $('chat-context').textContent = `Konteks: ${layer ? `lapisan "${layer.name || layer.id}"` : 'seluruh video'} · ${clock(snap(S.t))}`;
    }
    async function open() {
      renderContext();
      const key = `${S.session}|${S.name}`;
      if (loadedFor === key) return;
      loadedFor = key;
      log.innerHTML = '';
      try {
        const data = await api(`/api/session/${encodeURIComponent(S.session)}/messages`);
        const recent = (data.messages || []).filter(m => typeof m.content === 'string' && m.content.trim()).slice(-14);
        recent.forEach(m => (m.role === 'user' ? add('user', esc(m.content.slice(0, 1500))) : m.role === 'assistant' && add('ai', html(m.content))));
        if (!recent.length) add('ai', html('Tulis revisi untuk bagian video ini. Pilih lapisan atau geser playhead dulu: AI tahu bagian mana yang kamu maksud.'));
      } catch (_) { add('ai', html('Tulis revisi untuk bagian video ini.')); }
    }
    function connect() {
      if (ws && ws.readyState <= WebSocket.OPEN) return Promise.resolve();
      return new Promise((resolve, reject) => {
        ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`);
        ws.onopen = () => resolve();
        ws.onerror = () => reject(new Error('Tidak bisa tersambung ke chat'));
        ws.onclose = () => { if (busy) finish('Sambungan terputus.'); ws = null; };
        ws.onmessage = e => {
          const m = JSON.parse(e.data);
          if (m.type === 'content') { text += m.content; paint(); }
          else if (m.type === 'reset') { text = ''; paint(); }
          else if (m.type === 'progress' && m.kind === 'tool' && m.phase === 'start') { steps.push(m.name); paint(); }
          else if (m.type === 'progress' && m.kind === 'status' && m.text) { steps.push(m.text); paint(); }
          else if (m.type === 'done') finish();
          else if (m.type === 'stopped') finish('Dihentikan.');
          else if (m.type === 'error') finish(m.content || 'Gagal.');
        };
      });
    }
    function paint() {
      if (!bubble) return;
      const trail = steps.length ? `<div class="st-steps">${esc(steps.slice(-4).map(x => `↳ ${x}`).join('  '))}</div>` : '';
      bubble.innerHTML = (text ? html(text) : '<span class="st-hint">AI sedang membaca video…</span>') + trail;
      log.scrollTop = log.scrollHeight;
    }
    async function finish(problem) {
      busy = false;
      $('chat-send').disabled = false;
      if (problem && bubble) { bubble.classList.add(text ? 'ai' : 'err'); if (!text) bubble.textContent = problem; }
      bubble = null;
      if (S.tab !== 'chat') $('chat-dot').hidden = false;
      // The AI saved through its tools: show its version now instead of waiting for the next poll.
      if (S.name && !S.dirty) await openProject(S.name, true).catch(() => null);
    }
    async function send(message) {
      const body = (message || '').trim();
      if (!body || busy || !S.session || !S.name) return;
      if (S.dirty) await save();
      busy = true; text = ''; steps = [];
      $('chat-send').disabled = true;
      add('user', esc(body));
      bubble = add('ai', '');
      paint();
      try {
        await connect();
        ws.send(JSON.stringify({ message: body, session_id: S.session, studio: context() }));
      } catch (err) { finish(err.message); }
    }
    $('chat-form').onsubmit = e => { e.preventDefault(); const v = $('chat-input').value; $('chat-input').value = ''; send(v); };
    $('chat-input').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('chat-form').requestSubmit(); } };
    return { open, send, renderContext };
  })();

  /* ── layers and audio ──────────────────────────── */
  function newId(prefix) {
    const used = new Set((S.doc.layers || []).map(l => l.id).concat((S.doc.audio || []).map(a => a.id)));
    let i = 1; while (used.has(`${prefix}${i}`)) i++;
    return `${prefix}${i}`;
  }

  async function addLayer(kind) {
    if (!S.doc) return;
    const W = S.doc.width, H = S.doc.height, t = snap(S.t);
    const props = { x: Math.round(W / 2), y: Math.round(H / 2), scale: 1, rotation: 0, opacity: 1 };
    const base = { start: t, end: S.doc.duration, props, keys: {} };
    let layer;
    if (kind === 'text') layer = { ...base, type: 'text', text: 'Teks baru', font: (S.doc.fonts || [])[0] || 'Plus Jakarta Sans', weight: 700, size: Math.round(W / 12), fill: '#111111', align: 'center' };
    else if (kind === 'rect') layer = { ...base, type: 'rect', w: Math.round(W * 0.4), h: Math.round(W * 0.25), radius: 16, fill: '#E07A5F' };
    else if (kind === 'ellipse') layer = { ...base, type: 'ellipse', w: Math.round(W * 0.3), h: Math.round(W * 0.3), fill: '#3D8BFD' };
    else if (kind === 'polygon') layer = { ...base, type: 'polygon', sides: 6, radius: Math.round(W * 0.15), fill: '#F2C14E' };
    else if (kind === 'star') layer = { ...base, type: 'polygon', sides: 5, star: true, inner: 0.45, radius: Math.round(W * 0.15), fill: '#F2C14E' };
    else if (kind === 'path') layer = { ...base, type: 'path', d: 'M0 60 C 40 0, 80 0, 120 60 S 200 120, 240 60', w: 240, h: 120, fill: null, stroke: '#111111', strokeWidth: 8 };
    else if (kind === 'image') { $('image-input').click(); return; }
    else if (kind === 'video') { $('video-input').click(); return; }
    const name = TYPE_NAMES[layer.type] || kind;
    mutate(doc => {
      layer.id = newId(kind === 'star' ? 'star' : layer.type);
      layer.name = `${kind === 'star' ? 'Bintang' : name} ${layer.id.replace(/\D/g, '')}`;
      doc.layers.push(layer);
      S.sel = { kind: 'layer', id: layer.id };
    });
    if (layer.type === 'text') loadFonts();
  }

  async function upload(file) {
    const res = await api(`${base()}/project/${encodeURIComponent(S.name)}/asset?file=${encodeURIComponent(file.name)}`, { method: 'POST', body: file });
    // A big clip starts its proxy on the server right away; the live stream reports when it is ready.
    if (res.proxy) S.proxies = { ...S.proxies, [res.src]: { ready: false, state: res.proxy.state, progress: 0, error: '' } };
    return res.src;
  }

  $('image-input').onchange = async e => {
    const file = e.target.files[0]; e.target.value = '';
    if (!file || !S.doc) return;
    setSave('Mengunggah…');
    try {
      const src = await upload(file);
      const img = new Image(); img.src = assetUrl(src); S.images[src] = img;
      await new Promise(r => { img.onload = r; img.onerror = r; });
      const maxW = S.doc.width * 0.6;
      const k = img.naturalWidth ? Math.min(1, maxW / img.naturalWidth) : 1;
      mutate(doc => {
        const id = newId('image');
        doc.layers.push({ id, name: file.name.replace(/\.[^.]+$/, ''), type: 'image', src, fit: 'contain', radius: 0,
          w: Math.round((img.naturalWidth || 400) * k), h: Math.round((img.naturalHeight || 400) * k), start: snap(S.t), end: doc.duration,
          props: { x: Math.round(doc.width / 2), y: Math.round(doc.height / 2), scale: 1, rotation: 0, opacity: 1 }, keys: {} });
        S.sel = { kind: 'layer', id };
      });
    } catch (err) { banner(esc(err.message)); setSave('Gagal mengunggah', 'warn'); }
  };

  $('video-input').onchange = async e => {
    const file = e.target.files[0]; e.target.value = '';
    if (!file || !S.doc) return;
    setSave('Mengunggah video…');
    try {
      const src = await upload(file);
      await loadVideo(src);
      const v = S.videos[src];
      const k = v.videoWidth ? Math.min(1, (S.doc.width * 0.8) / v.videoWidth, (S.doc.height * 0.8) / v.videoHeight) : 1;
      const start = snap(S.t);
      mutate(doc => {
        const id = newId('video');
        doc.layers.push({ id, name: file.name.replace(/\.[^.]+$/, ''), type: 'video', src, fit: 'cover', radius: 0,
          w: Math.round((v.videoWidth || 1280) * k), h: Math.round((v.videoHeight || 720) * k), clipStart: 0, speed: 1, volume: 1,
          start, end: Math.min(doc.duration, +(start + (v.duration || doc.duration)).toFixed(3)),
          props: { x: Math.round(doc.width / 2), y: Math.round(doc.height / 2), scale: 1, rotation: 0, opacity: 1 }, keys: {} });
        S.sel = { kind: 'layer', id };
      });
    } catch (err) { banner(esc(err.message)); setSave('Gagal mengunggah', 'warn'); }
  };

  $('lut-input').onchange = async e => {
    const file = e.target.files[0]; e.target.value = '';
    const l = selLayer();
    if (!file || !S.doc || !l) return;
    setSave('Mengunggah LUT…');
    try {
      const src = await upload(file);
      await loadAssetList();
      await loadLut(src);
      mutate(() => { l.lut = { mix: 1, ...(l.lut || {}), src }; });
    } catch (err) { banner(esc(err.message)); setSave('Gagal mengunggah', 'warn'); }
  };

  async function startProxy(src) {
    try {
      const job = await api(`${base()}/project/${encodeURIComponent(S.name)}/proxy`, { method: 'POST', ...json({ src }) });
      S.proxies = { ...S.proxies, [src]: { ready: false, state: job.state, progress: job.progress || 0, error: '' } };
      renderInspector();
    } catch (err) { banner(esc(err.message)); }
  }

  $('audio-input').onchange = async e => {
    const file = e.target.files[0]; e.target.value = '';
    if (!file || !S.doc) return;
    setSave('Mengunggah musik…');
    try {
      const src = await upload(file);
      await loadAudio(src);
      mutate(doc => {
        doc.audio = doc.audio || [];
        const id = newId('audio');
        doc.audio.push({ id, name: file.name.replace(/\.[^.]+$/, ''), src, start: 0, offset: 0, volume: 0.8, fadeIn: 0.3, fadeOut: 1.2, keys: {} });
        S.sel = { kind: 'audio', id };
      });
    } catch (err) { banner(esc(err.message)); setSave('Gagal mengunggah', 'warn'); }
  };

  /* ── side lists ────────────────────────────────── */
  function renderLists() {
    const layers = [...(S.doc.layers || [])].reverse();
    $('layer-list').innerHTML = layers.map(l => `<li data-id="${esc(l.id)}" class="${S.sel?.kind === 'layer' && S.sel.id === l.id ? 'on' : ''}">
      <button class="eye" data-eye="${esc(l.id)}" type="button" title="${l.hidden ? 'Tampilkan' : 'Sembunyikan'}" aria-label="${l.hidden ? 'Tampilkan' : 'Sembunyikan'} ${esc(l.name || l.id)}">${l.hidden ? '◌' : '●'}</button>
      <span class="name">${esc(l.name || l.id)}</span><span class="kind">${esc(TYPE_NAMES[l.type] || l.type)}</span></li>`).join('')
      || '<li class="st-hint">Belum ada lapisan</li>';
    $('audio-list').innerHTML = (S.doc.audio || []).map(a => `<li data-audio="${esc(a.id)}" class="${S.sel?.kind === 'audio' && S.sel.id === a.id ? 'on' : ''}"><span class="name">♪ ${esc(a.name || a.src)}</span></li>`).join('')
      || '<li class="st-hint">Belum ada musik</li>';
    const notes = S.doc.notes || [];
    $('note-count').textContent = notes.filter(n => n.status !== 'done').length || '';
    $('note-list').innerHTML = notes.map(n => `<li data-note="${n.n}" class="${n.status === 'done' ? 'done' : ''} ${S.sel?.kind === 'note' && S.sel.id === n.n ? 'on' : ''}">
      <span class="kind">#${n.n} ${clock(n.t)}</span><span class="name">${esc(n.text)}</span>${n.status === 'done' ? '<span class="kind">✓</span>' : ''}</li>`).join('')
      || '<li class="st-hint">Belum ada catatan</li>';
  }
  $('layer-list').addEventListener('click', e => {
    const eye = e.target.closest('[data-eye]');
    if (eye) { const l = layerById(eye.dataset.eye); mutate(() => { l.hidden = !l.hidden; }); return; }
    const li = e.target.closest('li[data-id]');
    if (li) { S.sel = { kind: 'layer', id: li.dataset.id }; S.key = null; renderAll(); }
  });
  $('audio-list').addEventListener('click', e => { const li = e.target.closest('li[data-audio]'); if (li) { S.sel = { kind: 'audio', id: li.dataset.audio }; renderAll(); } });
  $('note-list').addEventListener('click', e => {
    const li = e.target.closest('li[data-note]');
    if (!li) return;
    const note = noteByN(+li.dataset.note);
    S.sel = { kind: 'note', id: note.n }; seek(note.t); renderAll();
  });
  document.querySelectorAll('[data-add]').forEach(b => { b.onclick = () => addLayer(b.dataset.add); });

  /* ── inspector ─────────────────────────────────── */
  function field(label, control, keyButton = '') { return `<label class="st-field"><span>${esc(label)}</span>${control}${keyButton || '<span></span>'}</label>`; }
  function keyButton(layer, prop) {
    if (!ANIMATABLE.has(prop)) return '<button class="st-key" disabled></button>';
    const frames = layer.keys && layer.keys[prop];
    const on = frames && keyIndexAt(frames, snap(S.t)) >= 0;
    const title = on ? 'Hapus keyframe di sini' : frames && frames.length ? 'Tambah keyframe di sini' : 'Mulai animasi: keyframe di sini';
    return `<button type="button" class="st-key ${frames && frames.length ? 'has' : ''} ${on ? 'on' : ''}" data-key="${prop}" title="${title}" aria-label="${title} (${prop})">${on ? '◆' : '◇'}</button>`;
  }
  function control(layer, prop, kind, step, value) {
    const v = value ?? '';
    if (kind === 'textarea') return `<textarea data-prop="${prop}" rows="${prop === 'd' ? 4 : 2}">${esc(v)}</textarea>`;
    if (kind === 'color') return `<input type="color" data-prop="${prop}" value="${esc(String(v || '#000000').slice(0, 7))}" ${v ? '' : 'data-empty="1"'}>`;
    if (kind === 'bool') return `<input type="checkbox" data-prop="${prop}" ${v ? 'checked' : ''}>`;
    if (kind === 'font') return `<select data-prop="${prop}">${[...new Set([v, ...(S.doc.fonts || []), ...FONTS].filter(Boolean))].map(f => `<option ${f === v ? 'selected' : ''}>${esc(f)}</option>`).join('')}</select>`;
    if (kind === 'weight') return `<select data-prop="${prop}">${[300, 400, 500, 600, 700, 800, 900].map(w => `<option ${+v === w ? 'selected' : ''}>${w}</option>`).join('')}</select>`;
    if (kind === 'align') return `<select data-prop="${prop}">${[['left', 'Kiri'], ['center', 'Tengah'], ['right', 'Kanan']].map(([a, n]) => `<option value="${a}" ${v === a ? 'selected' : ''}>${n}</option>`).join('')}</select>`;
    if (kind === 'fit') return `<select data-prop="${prop}"><option value="contain" ${v !== 'cover' ? 'selected' : ''}>Muat utuh</option><option value="cover" ${v === 'cover' ? 'selected' : ''}>Penuhi</option></select>`;
    if (kind === 'asset') return `<select data-prop="${prop}">${Object.keys(layer.type === 'video' ? S.videos : S.images).map(s => `<option ${s === v ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>`;
    const n = typeof v === 'number' ? +v.toFixed(3) : v;
    return `<input type="number" data-prop="${prop}" step="${step || (kind === 'int' ? 1 : 1)}" value="${esc(n)}">`;
  }

  /* ── inspector sections: track, transitions, colour, effects, proxy, curve ── */
  function details(id, title, body, openByDefault) {
    const open = S.secs[id] ?? openByDefault;
    return `<details class="st-sec" data-sec="${id}" ${open ? 'open' : ''}><summary>${esc(title)}</summary><div class="st-sec-body">${body}</div></details>`;
  }

  function trackField(item, kind) {
    const tracks = (S.doc.tracks || []).filter(t => (t.kind || 'video') === kind);
    return field('Track', `<select data-track="${kind}"><option value="">Tanpa track</option>${tracks.map(t => `<option value="${esc(t.id)}" ${item.track === t.id ? 'selected' : ''}>${esc(t.name || t.id)}</option>`).join('')}<option value="+">+ Track baru</option></select>`);
  }

  function newTrack(kind) {
    S.doc.tracks = S.doc.tracks || [];
    const used = new Set(S.doc.tracks.map(t => t.id)), prefix = kind === 'audio' ? 'a' : 'v';
    let i = 1; while (used.has(`${prefix}${i}`)) i++;
    const count = S.doc.tracks.filter(t => (t.kind || 'video') === kind).length + 1;
    const track = { id: `${prefix}${i}`, name: `${kind === 'audio' ? 'Audio' : 'Video'} ${count}`, kind };
    S.doc.tracks.push(track);
    return track;
  }

  const easeOptions = value => Object.entries(R.EASE_NAMES).map(([k, n]) => `<option value="${k}" ${value === k ? 'selected' : ''}>${n}</option>`).join('')
    + (String(value).startsWith('cubic-bezier') ? `<option value="${esc(value)}" selected>Kurva sendiri</option>` : '');

  function transBlock(l, key, label) {
    const tr = l[key] || {};
    const type = tr.type || 'none';
    return `<div class="st-trans"><span class="st-trans-label">${label}</span>
      <select data-fx="${key}.type" aria-label="Transisi ${label.toLowerCase()}">${R.TRANSITIONS.map(t => `<option value="${t}" ${t === type ? 'selected' : ''}>${esc(R.TRANSITION_NAMES[t])}</option>`).join('')}</select>
      ${type !== 'none' ? `<input type="number" min="0.05" step="0.05" data-fx="${key}.duration" value="${tr.duration ?? 0.5}" aria-label="Durasi transisi ${label.toLowerCase()} (detik)" title="Durasi (detik)">
      <select data-fx="${key}.ease" aria-label="Gerak transisi ${label.toLowerCase()}">${easeOptions(tr.ease || 'out')}</select>` : ''}</div>`;
  }

  const sectionTransitions = l => details('trans', 'Transisi', transBlock(l, 'transIn', 'Masuk') + transBlock(l, 'transOut', 'Keluar')
    + '<p class="st-hint">Silang antar klip: tumpuk ujung klip ini dengan awal klip berikutnya, beri keduanya Pudar.</p>', !!(l.transIn || l.transOut));

  const sectionColor = l => details('color', 'Warna',
    COLOR_FIELDS.map(([p, label, step]) => field(label, control(l, p, 'num', step, currentValue(l, p)), keyButton(l, p))).join('')
    + '<div class="st-row"><button type="button" class="st-btn" data-act="color-reset">Kembalikan warna asli</button></div>',
    l.type === 'image' || l.type === 'video' || COLOR_FIELDS.some(([p]) => currentValue(l, p)));

  function sectionEffects(l) {
    const ch = l.chroma, lut = l.lut, m = l.mask;
    const luts = S.assetList.filter(a => a.kind === 'lut').map(a => a.src);
    if (lut && lut.src && !luts.includes(lut.src)) luts.push(lut.src);
    const num = (path, value, step, min, max) => `<input type="number" step="${step}" ${min != null ? `min="${min}"` : ''} ${max != null ? `max="${max}"` : ''} data-fx="${path}" value="${esc(+(+value).toFixed(3))}">`;
    const chroma = field('Chroma key', `<input type="checkbox" data-fx-toggle="chroma" ${ch ? 'checked' : ''}>`)
      + (ch ? field('Warna kunci', `<input type="color" data-fx="chroma.color" value="${esc(String(ch.color || '#00FF00').slice(0, 7))}">`)
        + field('Kemiripan', num('chroma.similarity', ch.similarity ?? 0.25, 0.01, 0, 1))
        + field('Kehalusan tepi', num('chroma.smoothness', ch.smoothness ?? 0.08, 0.01, 0, 1))
        + field('Buang pantulan', num('chroma.spill', ch.spill ?? 0.5, 0.05, 0, 1)) : '');
    const lutBlock = field('LUT (.cube)', `<select data-fx="lut.src"><option value="">Tanpa LUT</option>${luts.map(src => `<option value="${esc(src)}" ${lut && lut.src === src ? 'selected' : ''}>${esc(src.replace(/^assets\//, ''))}</option>`).join('')}</select>`)
      + (lut ? field('Kekuatan LUT', num('lut.mix', lut.mix ?? 1, 0.05, 0, 1)) : '')
      + '<div class="st-row"><button type="button" class="st-btn" data-act="lut-upload">Unggah .cube</button></div>';
    const maskBlock = field('Mask', `<select data-fx="mask.shape"><option value="">Tanpa mask</option><option value="rect" ${m?.shape === 'rect' ? 'selected' : ''}>Kotak</option><option value="ellipse" ${m?.shape === 'ellipse' ? 'selected' : ''}>Elips</option></select>`)
      + (m ? [['x', 'Geser X'], ['y', 'Geser Y'], ['w', 'Lebar'], ['h', 'Tinggi'], ['radius', 'Sudut'], ['feather', 'Lembut tepi']]
        .map(([k, label]) => field(label, num(`mask.${k}`, m[k] ?? 0, 1, k === 'x' || k === 'y' ? null : 0))).join('')
        + field('Balik (lubangi)', `<input type="checkbox" data-fx="mask.invert" ${m.invert ? 'checked' : ''}>`) : '');
    return details('fx', 'Efek: chroma key, LUT, mask', chroma + lutBlock + maskBlock, !!(ch || lut || m));
  }

  function proxyInfo(l) {
    const p = S.proxies[l.src];
    const busy = p && (p.state === 'running' || p.state === 'queued');
    const state = !p ? 'belum ada' : p.ready ? (S.useProxy ? 'siap, dipakai di editor' : 'siap, tapi Proxy dimatikan')
      : busy ? `sedang dibuat ${Math.round((p.progress || 0) * 100)}%` : p.state === 'error' ? `gagal (${p.error})` : 'belum ada';
    return `<p class="st-hint">Proxy: ${esc(state)}. Ekspor selalu memakai file asli.</p>`
      + (!busy && !(p && p.ready) ? '<div class="st-row"><button type="button" class="st-btn" data-act="make-proxy">Buat proxy</button></div>' : '');
  }

  /* The curve editor: the selected keyframe's ease as a graph, with two handles for cubic-bezier. */
  const CURVE = { w: 220, h: 220, pad: 18, ymin: -0.4, ymax: 1.4 };
  const curveX = x => CURVE.pad + x * (CURVE.w - 2 * CURVE.pad);
  const curveY = y => CURVE.h - CURVE.pad - ((y - CURVE.ymin) / (CURVE.ymax - CURVE.ymin)) * (CURVE.h - 2 * CURVE.pad);
  function curvePath(ease) {
    const f = R.easeFn(ease);
    let d = '';
    for (let i = 0; i <= 80; i++) {
      const x = i / 80, y = ease === 'hold' ? (i < 80 ? 0 : 1) : f(x);
      d += `${i ? 'L' : 'M'}${curveX(x).toFixed(1)} ${curveY(Math.max(CURVE.ymin, Math.min(CURVE.ymax, y))).toFixed(1)}`;
    }
    return d;
  }
  function curveEditor(ease) {
    const b = R.bezierOf(ease) || R.bezierOf('out');
    const own = !!R.bezierOf(ease);
    return `<div class="st-curve" data-curve>
      <svg viewBox="0 0 ${CURVE.w} ${CURVE.h}" role="img" aria-label="Kurva gerak: seret dua titik bulat untuk mengubahnya">
        <rect class="frame" x="${curveX(0)}" y="${curveY(1)}" width="${curveX(1) - curveX(0)}" height="${curveY(0) - curveY(1)}"></rect>
        <line class="arm" data-arm="1" x1="${curveX(0)}" y1="${curveY(0)}" x2="${curveX(b[0])}" y2="${curveY(b[1])}"></line>
        <line class="arm" data-arm="2" x1="${curveX(1)}" y1="${curveY(1)}" x2="${curveX(b[2])}" y2="${curveY(b[3])}"></line>
        <path class="curve" d="${curvePath(ease)}"></path>
        <circle class="handle" data-h="1" cx="${curveX(b[0])}" cy="${curveY(b[1])}" r="8"></circle>
        <circle class="handle" data-h="2" cx="${curveX(b[2])}" cy="${curveY(b[3])}" r="8"></circle>
      </svg>
      <p class="st-hint">${own ? 'Seret titik bulat untuk mengubah gerak. Kiri bawah = keyframe ini, kanan atas = berikutnya.' : 'Gerak ini bukan kurva bezier. Seret titik untuk membuat kurva sendiri.'}</p>
    </div>`;
  }

  let curveDrag = null;
  $('inspector').addEventListener('pointerdown', e => {
    const h = e.target.closest('.st-curve circle.handle');
    const l = selLayer();
    if (!h || !l || !S.key) return;
    const frame = (l.keys[S.key.prop] || [])[S.key.index];
    if (!frame) return;
    e.preventDefault();
    h.setPointerCapture(e.pointerId);
    curveDrag = { h: +h.dataset.h, el: h, svg: h.ownerSVGElement, b: R.bezierOf(frame.ease) || R.bezierOf('out'), frame };
  });
  $('inspector').addEventListener('pointermove', e => {
    if (!curveDrag) return;
    const pt = curveDrag.svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const p = pt.matrixTransform(curveDrag.svg.getScreenCTM().inverse());
    const x = Math.max(0, Math.min(1, (p.x - CURVE.pad) / (CURVE.w - 2 * CURVE.pad)));
    const y = Math.max(CURVE.ymin, Math.min(CURVE.ymax, CURVE.ymin + ((CURVE.h - CURVE.pad - p.y) / (CURVE.h - 2 * CURVE.pad)) * (CURVE.ymax - CURVE.ymin)));
    const i = curveDrag.h === 1 ? 0 : 2;
    curveDrag.b[i] = +x.toFixed(3); curveDrag.b[i + 1] = +y.toFixed(3);
    const svg = curveDrag.svg, ease = `cubic-bezier(${curveDrag.b.join(',')})`;
    curveDrag.el.setAttribute('cx', curveX(x)); curveDrag.el.setAttribute('cy', curveY(y));
    const arm = svg.querySelector(`[data-arm="${curveDrag.h}"]`);
    arm.setAttribute('x2', curveX(x)); arm.setAttribute('y2', curveY(y));
    svg.querySelector('.curve').setAttribute('d', curvePath(ease));
  });
  $('inspector').addEventListener('pointerup', () => {
    if (!curveDrag) return;
    const { frame, b } = curveDrag;
    curveDrag = null;
    mutate(() => { frame.ease = `cubic-bezier(${b.join(',')})`; });
  });
  // Open and closed sections stay as they were while the inspector redraws.
  $('inspector').addEventListener('toggle', e => { const d = e.target.closest && e.target.closest('details[data-sec]'); if (d) S.secs[d.dataset.sec] = d.open; }, true);

  /** Change a transition, chroma key, LUT or mask setting (data-fx="group.key"). */
  function setFx(layer, path, value) {
    const [group, key] = path.split('.');
    if (group === 'transIn' || group === 'transOut') {
      if (key === 'type') {
        if (value === 'none') delete layer[group];
        else layer[group] = { duration: 0.5, ease: 'out', ...(layer[group] || {}), type: value };
      } else if (layer[group]) layer[group][key] = key === 'duration' ? Math.max(0.05, +value || 0.5) : value;
    } else if (group === 'lut') {
      if (key === 'src') {
        if (!value) delete layer.lut;
        else { layer.lut = { mix: 1, ...(layer.lut || {}), src: value }; if (!S.luts[value]) loadLut(value); }
      } else if (layer.lut) layer.lut.mix = Math.max(0, Math.min(1, +value));
    } else if (group === 'mask') {
      if (key === 'shape') {
        if (!value) delete layer.mask;
        else {
          const box = R.size(layer, R.layerState(layer, S.t), $('view').getContext('2d'));
          layer.mask = { x: 0, y: 0, w: Math.round(box.w * 0.8), h: Math.round(box.h * 0.8), radius: 0, feather: 0, invert: false, ...(layer.mask || {}), shape: value };
        }
      } else if (layer.mask) layer.mask[key] = key === 'invert' ? !!value : (+value || 0);
    } else if (group === 'chroma' && layer.chroma) {
      layer.chroma[key] = key === 'color' ? value : Math.max(0, Math.min(1, +value));
    }
  }

  function renderInspector() {
    const box = $('inspector');
    if (!S.doc) return;
    Chat.renderContext();
    const sel = S.sel || { kind: 'doc' };
    if (sel.kind === 'layer' && selLayer()) {
      const l = selLayer();
      const style = STYLE_FIELDS[l.type] || [];
      const keyInfo = S.key && S.key.layer === l.id ? (l.keys[S.key.prop] || [])[S.key.index] : null;
      box.innerHTML = `<h2>${esc(TYPE_NAMES[l.type] || l.type)}</h2>
        ${field('Nama', `<input type="text" data-meta="name" value="${esc(l.name || '')}">`)}
        ${field('Mulai (dtk)', `<input type="number" step="${(1 / S.doc.fps).toFixed(3)}" data-meta="start" value="${l.start ?? 0}">`)}
        ${field('Selesai (dtk)', `<input type="number" step="${(1 / S.doc.fps).toFixed(3)}" data-meta="end" value="${l.end ?? S.doc.duration}">`)}
        <p class="st-sub">Posisi dan gerak <span class="st-hint">◇ = buat keyframe di waktu ini</span></p>
        ${TRANSFORM_FIELDS.map(([p, label, step]) => field(label, control(l, p, 'num', step, currentValue(l, p)), keyButton(l, p))).join('')}
        <p class="st-sub">Tampilan</p>
        ${style.map(([p, label, kind, step]) => field(label, control(l, p, kind, step, ANIMATABLE.has(p) ? currentValue(l, p) : l[p]), keyButton(l, p))).join('')}
        ${l.type === 'video' ? proxyInfo(l) : ''}
        ${keyInfo ? `<p class="st-sub">Keyframe ${esc(S.key.prop)} di ${clock(keyInfo.t)}</p>
          ${field('Gerak ke berikutnya', `<select data-ease>${easeOptions(keyInfo.ease)}</select>`)}
          ${curveEditor(keyInfo.ease)}
          <div class="st-row"><button type="button" class="st-btn" data-act="del-key">Hapus keyframe</button></div>` : ''}
        ${trackField(l, 'video')}
        ${sectionTransitions(l)}
        ${sectionColor(l)}
        ${sectionEffects(l)}
        <div class="st-row">
          <button type="button" class="st-btn" data-act="up" title="Naikkan lapisan">Naik</button>
          <button type="button" class="st-btn" data-act="down" title="Turunkan lapisan">Turun</button>
          <button type="button" class="st-btn" data-act="dup">Duplikat</button>
          <button type="button" class="st-btn" data-act="split" title="Potong lapisan ini di playhead (S)">Potong</button>
          <button type="button" class="st-btn" data-act="del">Hapus</button>
        </div>`;
    } else if (sel.kind === 'audio' && clipById(sel.id)) {
      const a = clipById(sel.id);
      const length = S.buffers[a.src] ? S.buffers[a.src].duration : null;
      const vol = R.valueAt(a.keys, 'volume', S.t, a.volume ?? 1);
      const frames = a.keys && a.keys.volume;
      const on = frames && keyIndexAt(frames, snap(S.t)) >= 0;
      box.innerHTML = `<h2>Musik</h2>
        ${field('Nama', `<input type="text" data-ameta="name" value="${esc(a.name || '')}">`)}
        <p class="st-hint">${esc(a.src)}${length ? ` · ${length.toFixed(1)} dtk` : ''}</p>
        ${field('Mulai di video', `<input type="number" step="0.05" data-ameta="start" value="${a.start ?? 0}">`)}
        ${field('Mulai dari lagu', `<input type="number" step="0.05" data-ameta="offset" value="${a.offset ?? 0}">`)}
        ${field('Durasi', `<input type="number" step="0.05" data-ameta="duration" value="${a.duration ?? ''}" placeholder="sampai habis">`)}
        ${field('Volume', `<input type="number" step="0.05" min="0" max="4" data-ameta="volume" value="${(+vol).toFixed(2)}">`,
          `<button type="button" class="st-key ${frames && frames.length ? 'has' : ''} ${on ? 'on' : ''}" data-akey="volume" title="Keyframe volume" aria-label="Keyframe volume">${on ? '◆' : '◇'}</button>`)}
        ${field('Fade in', `<input type="number" step="0.1" min="0" data-ameta="fadeIn" value="${a.fadeIn ?? 0}">`)}
        ${field('Fade out', `<input type="number" step="0.1" min="0" data-ameta="fadeOut" value="${a.fadeOut ?? 0}">`)}
        ${field('Bisu', `<input type="checkbox" data-ameta="muted" ${a.muted ? 'checked' : ''}>`)}
        ${trackField(a, 'audio')}
        <div class="st-row"><button type="button" class="st-btn" data-act="del-audio">Hapus musik</button></div>`;
    } else if (sel.kind === 'track' && trackById(sel.id)) {
      const tr = trackById(sel.id);
      const audio = tr.kind === 'audio';
      const count = (audio ? S.doc.audio || [] : S.doc.layers || []).filter(x => x.track === tr.id).length;
      box.innerHTML = `<h2>Track ${audio ? 'audio' : 'video'}</h2>
        ${field('Nama', `<input type="text" data-tmeta="name" value="${esc(tr.name || '')}">`)}
        ${audio ? field('Bisu', `<input type="checkbox" data-tmeta="muted" ${tr.muted ? 'checked' : ''}>`)
          : field('Sembunyikan', `<input type="checkbox" data-tmeta="hidden" ${tr.hidden ? 'checked' : ''}>`) + field('Bisukan suara klip', `<input type="checkbox" data-tmeta="muted" ${tr.muted ? 'checked' : ''}>`)}
        ${field('Kunci', `<input type="checkbox" data-tmeta="locked" ${tr.locked ? 'checked' : ''}>`)}
        <p class="st-hint">${count} ${audio ? 'klip' : 'lapisan'} di track ini. ${audio ? '' : 'Urutan gambar (siapa di atas) tetap mengikuti daftar Lapisan.'}</p>
        <div class="st-row"><button type="button" class="st-btn" data-act="track-del">Hapus track</button></div>`;
    } else if (sel.kind === 'note' && noteByN(sel.id)) {
      const n = noteByN(sel.id);
      box.innerHTML = `<h2>Catatan #${n.n}</h2>
        ${field('Isi', `<textarea data-nmeta="text" rows="4">${esc(n.text)}</textarea>`)}
        ${field('Waktu (dtk)', `<input type="number" step="0.05" data-nmeta="t" value="${n.t}">`)}
        ${field('Sampai (dtk)', `<input type="number" step="0.05" data-nmeta="end" value="${n.end ?? ''}" placeholder="satu titik waktu">`)}
        <p class="st-hint">Status: ${n.status === 'done' ? 'selesai' : 'terbuka'}${n.layer ? ` · lapisan ${esc(n.layer)}` : ''}</p>
        ${n.reply ? `<p class="st-sub">Jawaban AI</p><div class="st-reply">${esc(n.reply)}</div>` : ''}
        <div class="st-row">
          <button type="button" class="st-btn" data-act="note-toggle">${n.status === 'done' ? 'Buka lagi' : 'Tandai selesai'}</button>
          <button type="button" class="st-btn" data-act="note-del">Hapus</button>
        </div>`;
    } else {
      const d = S.doc;
      box.innerHTML = `<h2>Video</h2>
        ${field('Lebar', `<input type="number" data-dmeta="width" value="${d.width}">`)}
        ${field('Tinggi', `<input type="number" data-dmeta="height" value="${d.height}">`)}
        ${field('FPS', `<input type="number" data-dmeta="fps" value="${d.fps}">`)}
        ${field('Durasi (dtk)', `<input type="number" step="0.1" data-dmeta="duration" value="${d.duration}">`)}
        ${field('Latar', `<input type="color" data-dmeta="background" value="${esc(String(d.background || '#000000').slice(0, 7))}">`)}
        ${field('Font', `<input type="text" data-dmeta="fonts" value="${esc((d.fonts || []).join(', '))}" placeholder="Fraunces, Plus Jakarta Sans">`)}
        <p class="st-hint">Klik objek di video atau di daftar untuk mengeditnya. Spasi putar, ← → per frame, K keyframe posisi, Delete hapus, Ctrl+Z urungkan.</p>`;
    }
  }

  function readInput(el) {
    if (el.type === 'checkbox') return el.checked;
    if (el.type === 'number') return el.value === '' ? null : +el.value;
    return el.value;
  }

  $('inspector').addEventListener('change', e => {
    const el = e.target;
    const l = selLayer();
    if (el.dataset.track) {
      const item = l || (S.sel?.kind === 'audio' ? clipById(S.sel.id) : null);
      if (!item) return;
      mutate(() => {
        if (el.value === '+') item.track = newTrack(el.dataset.track).id;
        else if (el.value) item.track = el.value;
        else delete item.track;
      });
      return;
    }
    if (el.dataset.fx && l) { mutate(() => setFx(l, el.dataset.fx, readInput(el))); return; }
    if (el.dataset.fxToggle === 'chroma' && l) {
      mutate(() => { if (el.checked) l.chroma = { color: '#00FF00', similarity: 0.25, smoothness: 0.08, spill: 0.5 }; else delete l.chroma; });
      return;
    }
    if (el.dataset.tmeta && S.sel?.kind === 'track') {
      const tr = trackById(S.sel.id);
      mutate(() => { tr[el.dataset.tmeta] = readInput(el); });
      return;
    }
    if (el.dataset.prop && l) {
      mutate(() => {
        let v = readInput(el);
        if (el.dataset.prop === 'sides' && v != null) v = Math.round(v);
        if (v === null && ANIMATABLE.has(el.dataset.prop)) return;
        setValue(l, el.dataset.prop, v);
      });
      if (el.dataset.prop === 'font' || el.dataset.prop === 'weight') loadFonts();
    } else if (el.dataset.meta && l) {
      mutate(() => {
        const v = readInput(el);
        if (el.dataset.meta === 'start' || el.dataset.meta === 'end') l[el.dataset.meta] = Math.max(0, Math.min(S.doc.duration, +v));
        else l[el.dataset.meta] = v;
      });
    } else if (el.dataset.ease !== undefined && l && S.key) {
      mutate(() => { l.keys[S.key.prop][S.key.index].ease = el.value; });
    } else if (el.dataset.ameta) {
      const a = clipById(S.sel.id);
      mutate(() => {
        const v = readInput(el);
        const k = el.dataset.ameta;
        if (k === 'volume' && a.keys && a.keys.volume && a.keys.volume.length) {
          const i = keyIndexAt(a.keys.volume, snap(S.t));
          if (i >= 0) a.keys.volume[i].v = v; else { a.keys.volume.push({ t: snap(S.t), v, ease: 'inOut' }); a.keys.volume.sort((x, y) => x.t - y.t); }
        } else if (k === 'duration' && v === null) delete a.duration;
        else a[k] = v;
      });
    } else if (el.dataset.nmeta) {
      const n = noteByN(S.sel.id);
      mutate(() => {
        const v = readInput(el);
        if (el.dataset.nmeta === 'end' && v === null) delete n.end; else n[el.dataset.nmeta] = v;
      });
    } else if (el.dataset.dmeta) {
      mutate(doc => {
        const k = el.dataset.dmeta;
        const v = readInput(el);
        if (k === 'fonts') doc.fonts = String(v).split(',').map(s => s.trim()).filter(Boolean);
        else if (k === 'width' || k === 'height') doc[k] = Math.max(16, Math.round(v / 2) * 2);
        else doc[k] = v;
      });
      if (el.dataset.dmeta === 'fonts') loadFonts();
      fitCanvas(); fitZoom();
    }
  });
  $('inspector').addEventListener('click', e => {
    const key = e.target.closest('[data-key]');
    if (key && selLayer()) { toggleKey(selLayer(), key.dataset.key); return; }
    const akey = e.target.closest('[data-akey]');
    if (akey) {
      const a = clipById(S.sel.id);
      mutate(() => {
        a.keys = a.keys || {};
        const frames = a.keys.volume || (a.keys.volume = []);
        const i = keyIndexAt(frames, snap(S.t));
        if (i >= 0) { frames.splice(i, 1); if (!frames.length) delete a.keys.volume; }
        else { frames.push({ t: snap(S.t), v: R.valueAt(a.keys, 'volume', S.t, a.volume ?? 1), ease: 'inOut' }); frames.sort((x, y) => x.t - y.t); }
      });
      return;
    }
    const act = e.target.closest('[data-act]');
    if (!act) return;
    const l = selLayer();
    const layers = S.doc.layers;
    const i = l ? layers.indexOf(l) : -1;
    switch (act.dataset.act) {
      case 'up': if (i >= 0 && i < layers.length - 1) mutate(() => { layers.splice(i, 1); layers.splice(i + 1, 0, l); }); break;
      case 'down': if (i > 0) mutate(() => { layers.splice(i, 1); layers.splice(i - 1, 0, l); }); break;
      case 'dup': if (l) mutate(() => { const c = JSON.parse(JSON.stringify(l)); c.id = newId(l.type); c.name = `${l.name || l.id} salinan`; c.props.x += 30; c.props.y += 30; layers.splice(i + 1, 0, c); S.sel = { kind: 'layer', id: c.id }; }); break;
      case 'del': if (l) mutate(() => { layers.splice(i, 1); S.sel = { kind: 'doc' }; S.key = null; }); break;
      case 'split': if (l) splitAt(l, i); break;
      case 'del-key': if (l && S.key) mutate(() => { const f = l.keys[S.key.prop]; const v = f[S.key.index].v; f.splice(S.key.index, 1); if (!f.length) { delete l.keys[S.key.prop]; if (IN_PROPS.has(S.key.prop)) l.props[S.key.prop] = v; else l[S.key.prop] = v; } S.key = null; }); break;
      case 'del-audio': mutate(doc => { doc.audio = doc.audio.filter(a => a.id !== S.sel.id); S.sel = { kind: 'doc' }; }); break;
      case 'note-toggle': mutate(() => { const n = noteByN(S.sel.id); n.status = n.status === 'done' ? 'open' : 'done'; }); break;
      case 'note-del': mutate(doc => { doc.notes = doc.notes.filter(n => n.n !== S.sel.id); S.sel = { kind: 'doc' }; }); break;
      case 'color-reset': if (l) mutate(() => { COLOR_FIELDS.forEach(([p]) => { if (l.props) delete l.props[p]; if (l.keys) delete l.keys[p]; }); }); break;
      case 'lut-upload': $('lut-input').click(); break;
      case 'make-proxy': if (l && l.src) startProxy(l.src); break;
      case 'track-del': mutate(doc => {
        const id = S.sel.id;
        doc.tracks = (doc.tracks || []).filter(t => t.id !== id);
        [...(doc.layers || []), ...(doc.audio || [])].forEach(x => { if (x.track === id) delete x.track; });
        S.sel = { kind: 'doc' };
      }); break;
      default: break;
    }
  });

  /* ── timeline ──────────────────────────────────── */
  function fitZoom() {
    if (!S.doc) return;
    const width = Math.max(200, $('tl-scroll').clientWidth - LABEL_W - 20);
    S.zoom = Math.max(10, Math.min(400, width / S.doc.duration));
  }
  const tx = t => t * S.zoom;
  const fromX = x => x / S.zoom;

  function renderTimeline() {
    if (!S.doc) return;
    const d = S.doc;
    const width = tx(d.duration) + 40;
    $('tl-inner').style.width = (LABEL_W + width) + 'px';
    const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60];
    const step = steps.find(s => s * S.zoom >= 64) || 60;
    let ruler = '';
    for (let t = 0; t <= d.duration + 1e-6; t += step) ruler += `<span class="tick" style="left:${tx(t)}px">${t < 60 ? (+t.toFixed(2)) + 's' : clock(t)}</span>`;
    for (const n of d.notes || []) {
      if (n.end != null) ruler += `<span class="range-mark" style="left:${tx(n.t)}px;width:${Math.max(2, tx(n.end - n.t))}px"></span>`;
      ruler += `<span class="note-mark ${n.status === 'done' ? 'done' : ''}" data-note="${n.n}" style="left:${tx(n.t)}px" title="${esc(n.text)}">${n.n}</span>`;
    }
    if (S.rangeDraft) {
      const a = Math.min(S.rangeDraft.t0, S.rangeDraft.t1), b = Math.max(S.rangeDraft.t0, S.rangeDraft.t1);
      ruler += `<span class="range-draft" style="left:${tx(a)}px;width:${tx(b - a)}px"></span>`;
    }
    $('ruler').innerHTML = ruler;
    $('ruler').style.width = width + 'px';

    // Rows: video tracks (top track first), then layers on no track, then audio tracks and loose clips.
    // A track row holds every bar on it; a layer's own row holds just its bar.
    const rows = [];
    const selected = l => S.sel?.kind === 'layer' && S.sel.id === l.id;
    const keyRows = l => Object.entries(l.keys || {}).map(([prop, frames]) => `<div class="st-tl-row sub"><div class="st-tl-label">${esc(prop)}</div><div class="st-tl-track">
        ${frames.map((k, i) => `<span class="st-diamond ${S.key && S.key.layer === l.id && S.key.prop === prop && S.key.index === i ? 'sel' : ''}" style="left:${tx(k.t)}px" data-kl="${esc(l.id)}" data-kp="${esc(prop)}" data-ki="${i}" title="${esc(prop)} = ${esc(typeof k.v === 'number' ? +k.v.toFixed(2) : k.v)} · ${esc(R.EASE_NAMES[k.ease] || 'kurva sendiri')}"></span>`).join('')}
      </div></div>`);
    const summary = l => (selected(l) ? '' : [...new Set(Object.values(l.keys || {}).flat().map(k => +k.t.toFixed(4)))]
      .map(t => `<span class="st-diamond" style="left:${tx(t)}px" data-sum="${esc(l.id)}" data-t="${t}"></span>`).join(''));
    const transMark = (l, key, side) => {
      const tr = l[key];
      return tr && tr.type && tr.type !== 'none' ? `<span class="st-trmark ${side}" style="width:${Math.max(3, tx(tr.duration || 0))}px" title="${esc(R.TRANSITION_NAMES[tr.type] || tr.type)} ${tr.duration || 0} dtk"></span>` : '';
    };
    const layerBar = (l, named) => `<div class="st-bar ${selected(l) ? 'on' : ''}" data-bar="${esc(l.id)}" style="left:${tx(l.start || 0)}px;width:${Math.max(4, tx((l.end ?? d.duration) - (l.start || 0)))}px">
        ${transMark(l, 'transIn', 'in')}${transMark(l, 'transOut', 'out')}${named ? `<span class="st-bar-name">${esc(l.name || l.id)}</span>` : ''}
        <span class="edge l" data-edge="l"></span><span class="edge r" data-edge="r"></span></div>`;
    const audioBar = (a, named) => {
      const buffer = S.buffers[a.src];
      const length = a.duration ?? (buffer ? buffer.duration - (a.offset || 0) : d.duration - (a.start || 0));
      return `<div class="st-bar audio" data-abar="${esc(a.id)}" style="left:${tx(a.start || 0)}px;width:${Math.max(4, tx(Math.max(0.05, length)))}px">
        <canvas data-wave="${esc(a.id)}"></canvas>${named ? `<span class="st-bar-name">♪ ${esc(a.name || a.src)}</span>` : ''}<span class="edge r" data-edge="r"></span></div>
        ${Object.values(a.keys || {}).flat().map(k => `<span class="st-diamond" style="left:${tx(k.t)}px"></span>`).join('')}`;
    };
    const ICON = {
      eye: '<svg viewBox="0 0 24 24" aria-hidden="true" class="st-ico"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
      eyeOff: '<svg viewBox="0 0 24 24" aria-hidden="true" class="st-ico"><path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.2M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.6 9.6 0 0 0 4.4-1"/></svg>',
      lock: '<svg viewBox="0 0 24 24" aria-hidden="true" class="st-ico"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
      unlock: '<svg viewBox="0 0 24 24" aria-hidden="true" class="st-ico"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.5-2"/></svg>',
      sound: '<svg viewBox="0 0 24 24" aria-hidden="true" class="st-ico"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 0 1 0 6"/></svg>',
      mute: '<svg viewBox="0 0 24 24" aria-hidden="true" class="st-ico"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="m17 9 5 6M22 9l-5 6"/></svg>',
    };
    const trackLabel = tr => {
      const audio = tr.kind === 'audio', name = esc(tr.name || tr.id);
      const flag = audio ? `<button type="button" class="st-tbtn" data-tflag="muted" data-tid="${esc(tr.id)}" aria-pressed="${!!tr.muted}" aria-label="${tr.muted ? 'Bunyikan' : 'Bisukan'} ${name}" title="${tr.muted ? 'Bunyikan' : 'Bisukan'}">${tr.muted ? ICON.mute : ICON.sound}</button>`
        : `<button type="button" class="st-tbtn" data-tflag="hidden" data-tid="${esc(tr.id)}" aria-pressed="${!!tr.hidden}" aria-label="${tr.hidden ? 'Tampilkan' : 'Sembunyikan'} ${name}" title="${tr.hidden ? 'Tampilkan' : 'Sembunyikan'}">${tr.hidden ? ICON.eyeOff : ICON.eye}</button>`;
      return `<div class="st-tl-label st-track-label" data-tpick="${esc(tr.id)}"><span class="name">${name}</span>${flag}<button type="button" class="st-tbtn" data-tflag="locked" data-tid="${esc(tr.id)}" aria-pressed="${!!tr.locked}" aria-label="${tr.locked ? 'Buka kunci' : 'Kunci'} ${name}" title="${tr.locked ? 'Buka kunci' : 'Kunci'}">${tr.locked ? ICON.lock : ICON.unlock}</button></div>`;
    };
    const tracks = d.tracks || [];
    for (const tr of [...tracks].filter(t => t.kind !== 'audio').reverse()) {
      const items = (d.layers || []).filter(l => l.track === tr.id);
      const on = S.sel?.kind === 'track' && S.sel.id === tr.id;
      rows.push(`<div class="st-tl-row track ${on ? 'on' : ''} ${tr.hidden ? 'is-off' : ''} ${tr.locked ? 'is-locked' : ''}">${trackLabel(tr)}
        <div class="st-tl-track">${items.map(l => layerBar(l, true) + summary(l)).join('')}</div></div>`);
      const pick = items.find(selected);
      if (pick) rows.push(...keyRows(pick));
    }
    for (const l of [...(d.layers || [])].reverse()) {
      if (l.track && trackById(l.track)) continue;
      const on = selected(l);
      rows.push(`<div class="st-tl-row ${on ? 'on' : ''}" data-row="${esc(l.id)}"><div class="st-tl-label" data-pick="${esc(l.id)}">${esc(l.name || l.id)}</div>
        <div class="st-tl-track">${layerBar(l, false)}${summary(l)}</div></div>`);
      if (on) rows.push(...keyRows(l));
    }
    for (const tr of tracks.filter(t => t.kind === 'audio')) {
      const on = S.sel?.kind === 'track' && S.sel.id === tr.id;
      rows.push(`<div class="st-tl-row track ${on ? 'on' : ''} ${tr.muted ? 'is-off' : ''} ${tr.locked ? 'is-locked' : ''}">${trackLabel(tr)}
        <div class="st-tl-track">${(d.audio || []).filter(a => a.track === tr.id).map(a => audioBar(a, true)).join('')}</div></div>`);
    }
    for (const a of d.audio || []) {
      if (a.track && trackById(a.track)) continue;
      const on = S.sel?.kind === 'audio' && S.sel.id === a.id;
      rows.push(`<div class="st-tl-row ${on ? 'on' : ''}"><div class="st-tl-label" data-apick="${esc(a.id)}">♪ ${esc(a.name || a.src)}</div>
        <div class="st-tl-track">${audioBar(a, false)}</div></div>`);
    }
    $('tl-rows').innerHTML = rows.join('') || '<p class="st-empty">Tambahkan lapisan atau musik.</p>';
    $('tl-rows').querySelectorAll('canvas[data-wave]').forEach(drawWave);
    $('playhead').style.left = tx(S.t) + 'px';
  }

  function drawWave(canvas) {
    const a = clipById(canvas.dataset.wave);
    const pk = a && S.peaks[a.src];
    const bar = canvas.parentElement;
    canvas.width = bar.clientWidth; canvas.height = bar.clientHeight;
    if (!pk) return;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--warn') || '#9a5800';
    const mid = canvas.height / 2;
    for (let x = 0; x < canvas.width; x++) {
      const t = (a.offset || 0) + fromX(x);
      const v = pk.values[Math.floor(t * pk.perSecond)] || 0;
      const h = Math.max(1, v * mid * 0.95);
      ctx.fillRect(x, mid - h, 1, h * 2);
    }
  }

  function seek(t) {
    if (!S.doc) return;
    S.t = Math.max(0, Math.min(S.doc.duration, t));
    if (S.playing) restartAudio();
    renderView();
    if (S.sel?.kind === 'layer' || S.sel?.kind === 'audio') renderInspector();
  }

  const clipLength = a => a.duration ?? (S.buffers[a.src] ? S.buffers[a.src].duration - (a.offset || 0) : S.doc.duration - (a.start || 0));
  /** Times a dragged bar snaps to: the playhead, the video's ends, and the edges of bars on its track. */
  function snapTargets(item, audio) {
    const out = [S.t, 0, S.doc.duration];
    if (item.track) {
      for (const o of audio ? S.doc.audio || [] : S.doc.layers || []) {
        if (o === item || o.track !== item.track) continue;
        out.push(o.start || 0, audio ? (o.start || 0) + clipLength(o) : o.end ?? S.doc.duration);
      }
    }
    return out;
  }
  function snapTo(t, targets) {
    let best = t, gap = 8 / S.zoom;
    for (const x of targets) { const dd = Math.abs(x - t); if (dd < gap) { gap = dd; best = x; } }
    return best;
  }

  // Click on the ruler or an empty track: move the playhead. Drag on a bar, an edge or a diamond: edit time.
  let tdrag = null;
  $('tl-inner').addEventListener('pointerdown', e => {
    if (!S.doc) return;
    const rect = $('tl-inner').getBoundingClientRect();
    const xAt = ev => ev.clientX - rect.left - LABEL_W;
    const target = e.target;
    const note = target.closest('.note-mark');
    if (note) { const n = noteByN(+note.dataset.note); S.sel = { kind: 'note', id: n.n }; seek(n.t); renderAll(); return; }
    const flag = target.closest('[data-tflag]');
    if (flag) { const tr = trackById(flag.dataset.tid); if (tr) mutate(() => { tr[flag.dataset.tflag] = !tr[flag.dataset.tflag]; }); return; }
    const tpick = target.closest('[data-tpick]');
    if (tpick) { S.sel = { kind: 'track', id: tpick.dataset.tpick }; S.key = null; renderAll(); return; }
    const pick = target.closest('[data-pick]');
    if (pick) { S.sel = { kind: 'layer', id: pick.dataset.pick }; S.key = null; renderAll(); return; }
    const apick = target.closest('[data-apick]');
    if (apick) { S.sel = { kind: 'audio', id: apick.dataset.apick }; renderAll(); return; }
    const diamond = target.closest('[data-kl]');
    const bar = target.closest('[data-bar], [data-abar]');
    $('tl-inner').setPointerCapture(e.pointerId);
    if (diamond) {
      const l = layerById(diamond.dataset.kl);
      S.key = { layer: l.id, prop: diamond.dataset.kp, index: +diamond.dataset.ki };
      checkpoint();
      const frame = l.keys[S.key.prop][S.key.index];
      tdrag = { kind: 'key', l, frame, x0: xAt(e), t0: frame.t };
      seek(frame.t); renderInspector(); renderTimeline();
      return;
    }
    if (bar) {
      const edge = target.closest('[data-edge]');
      const item = bar.dataset.bar ? layerById(bar.dataset.bar) : clipById(bar.dataset.abar);
      S.sel = bar.dataset.bar ? { kind: 'layer', id: item.id } : { kind: 'audio', id: item.id };
      S.key = null;
      const tr = item.track && trackById(item.track);
      if (tr && tr.locked) { setSave('Track ini dikunci', 'warn'); renderAll(); tdrag = null; return; }   // locked: select, never move
      checkpoint();
      const audio = !!bar.dataset.abar;
      tdrag = { kind: edge ? `edge-${edge.dataset.edge}` : 'bar', audio, item, x0: xAt(e),
        start: item.start || 0, end: audio ? (item.start || 0) + clipLength(item) : item.end ?? S.doc.duration, duration: item.duration,
        keys: JSON.parse(JSON.stringify(item.keys || {})) };
      renderAll();
      return;
    }
    if (e.shiftKey && target.closest('#ruler')) {
      // Shift+drag on the ruler: a note about a stretch of time.
      S.rangeDraft = { t0: snap(fromX(xAt(e))), t1: snap(fromX(xAt(e))) };
      tdrag = { kind: 'range' };
      renderTimeline();
      return;
    }
    tdrag = { kind: 'seek' };
    seek(fromX(xAt(e)));
  });
  $('tl-inner').addEventListener('pointermove', e => {
    if (!tdrag || !S.doc) return;
    const rect = $('tl-inner').getBoundingClientRect();
    const x = e.clientX - rect.left - LABEL_W;
    if (tdrag.kind === 'seek') { seek(fromX(x)); return; }
    if (tdrag.kind === 'range') { S.rangeDraft.t1 = snap(fromX(x)); renderTimeline(); return; }
    const dt = fromX(x - tdrag.x0);
    if (tdrag.kind === 'key') {
      tdrag.frame.t = snap(tdrag.t0 + dt);
      const frames = tdrag.l.keys[S.key.prop];
      frames.sort((a, b) => a.t - b.t);
      S.key.index = frames.indexOf(tdrag.frame);
      S.t = tdrag.frame.t;
    } else if (tdrag.kind === 'bar') {
      let shift = snap(Math.max(-tdrag.start, dt));
      // Snap the bar's start or end to a neighbour's edge, the playhead, or the video's ends (Alt: no snap).
      if (!e.altKey) {
        const targets = snapTargets(tdrag.item, tdrag.audio);
        const a = snapTo(tdrag.start + shift, targets), b = snapTo(tdrag.end + shift, targets);
        if (a !== tdrag.start + shift) shift = a - tdrag.start;
        else if (b !== tdrag.end + shift) shift = b - tdrag.end;
        shift = Math.max(-tdrag.start, shift);
      }
      tdrag.item.start = +(tdrag.start + shift).toFixed(4);
      if (!tdrag.audio) {
        tdrag.item.end = +Math.min(S.doc.duration, tdrag.end + shift).toFixed(4);
        // Moving a layer moves its animation with it.
        for (const [prop, frames] of Object.entries(tdrag.keys)) tdrag.item.keys[prop] = frames.map(k => ({ ...k, t: +Math.max(0, k.t + shift).toFixed(4) }));
      }
    } else if (tdrag.kind === 'edge-l') {
      const t = e.altKey ? snap(tdrag.start + dt) : snapTo(snap(tdrag.start + dt), snapTargets(tdrag.item, false));
      tdrag.item.start = Math.max(0, Math.min(t, (tdrag.item.end ?? S.doc.duration) - 1 / S.doc.fps));
    } else if (tdrag.kind === 'edge-r') {
      if (tdrag.audio) {
        const buffer = S.buffers[tdrag.item.src];
        const full = buffer ? buffer.duration - (tdrag.item.offset || 0) : S.doc.duration;
        const was = tdrag.duration ?? full;
        tdrag.item.duration = Math.max(0.1, Math.min(full, +(was + dt).toFixed(3)));
      } else {
        const t = e.altKey ? snap(tdrag.end + dt) : snapTo(snap(tdrag.end + dt), snapTargets(tdrag.item, false));
        tdrag.item.end = Math.max(t, tdrag.start + 1 / S.doc.fps);
      }
    }
    S.dirty = true; scheduleSave(); setSave('Belum disimpan…');
    renderView(); renderTimeline(); renderInspector();
  });
  $('tl-inner').addEventListener('pointerup', e => {
    if (tdrag && tdrag.kind === 'range' && S.rangeDraft) {
      const a = Math.min(S.rangeDraft.t0, S.rangeDraft.t1), b = Math.max(S.rangeDraft.t0, S.rangeDraft.t1);
      if (b - a >= 1 / S.doc.fps) addNote({ t: a, end: b }, e.clientX, e.clientY - 200);
      else { S.rangeDraft = null; renderTimeline(); }
    }
    tdrag = null;
  });
  $('tl-scroll').addEventListener('wheel', e => {
    if (!e.ctrlKey || !S.doc) return;
    e.preventDefault();
    S.zoom = Math.max(10, Math.min(600, S.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    renderTimeline();
  }, { passive: false });
  $('zoom-in').onclick = () => { S.zoom = Math.min(600, S.zoom * 1.4); renderTimeline(); };
  $('add-track').onclick = () => {
    if (!S.doc) return;
    mutate(() => {
      const tr = newTrack('video');
      const l = selLayer();
      if (l && !l.track) l.track = tr.id;   // the selected layer moves onto the new track
      S.sel = l ? S.sel : { kind: 'track', id: tr.id };
    });
  };
  $('use-proxy').checked = S.useProxy;
  $('use-proxy').onchange = () => {
    S.useProxy = $('use-proxy').checked;
    localStorage.setItem('studio-proxy', S.useProxy ? 'on' : 'off');
    refreshVideos();
    renderInspector();
  };
  $('zoom-out').onclick = () => { S.zoom = Math.max(10, S.zoom / 1.4); renderTimeline(); };

  /* ── playback ──────────────────────────────────── */
  function restartAudio() {
    if (S.stopAudio) { S.stopAudio(); S.stopAudio = null; }
    if (!S.playing || !(S.doc.audio || []).length) return;
    S.audioCtx = S.audioCtx || new AudioContext();
    S.audioCtx.resume();
    S.stopAudio = R.scheduleAudio(S.audioCtx, S.doc, S.buffers, S.t);
    S.playStart = performance.now(); S.playFrom = S.t;
  }
  function play() {
    if (!S.doc) return;
    if (S.t >= S.doc.duration - 1e-3) S.t = 0;
    S.playing = true; $('play').classList.add('is-playing'); $('play').setAttribute('aria-label', 'Jeda');
    S.playStart = performance.now(); S.playFrom = S.t;
    restartAudio();
    requestAnimationFrame(tick);
  }
  function pause() {
    S.playing = false; $('play').classList.remove('is-playing'); $('play').setAttribute('aria-label', 'Putar');
    if (S.stopAudio) { S.stopAudio(); S.stopAudio = null; }
    S.t = snap(S.t); renderView(); renderInspector();
    settlePending();
  }
  function tick() {
    if (!S.playing) return;
    let t = S.playFrom + (performance.now() - S.playStart) / 1000;
    if (t >= S.doc.duration) {
      if ($('loop').checked) { S.t = 0; S.playFrom = 0; S.playStart = performance.now(); restartAudio(); t = 0; }
      else { S.t = S.doc.duration; pause(); return; }
    }
    S.t = t;
    renderView();
    const x = tx(t);
    const scroll = $('tl-scroll');
    if (x + LABEL_W > scroll.scrollLeft + scroll.clientWidth - 40 || x + LABEL_W < scroll.scrollLeft) scroll.scrollLeft = x;
    requestAnimationFrame(tick);
  }
  $('play').onclick = () => (S.playing ? pause() : play());
  $('to-start').onclick = () => seek(0);
  $('prev-frame').onclick = () => seek(snap(S.t) - 1 / S.doc.fps);
  $('next-frame').onclick = () => seek(snap(S.t) + 1 / S.doc.fps);
  document.querySelectorAll('[data-mode]').forEach(b => {
    b.onclick = () => {
      S.mode = b.dataset.mode;
      document.querySelectorAll('[data-mode]').forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-checked', String(x === b)); });
      overlay.classList.toggle('note-mode', S.mode === 'note');
    };
  });

  /* ── keyboard ──────────────────────────────────── */
  document.addEventListener('keydown', e => {
    const inField = e.target instanceof Element && e.target.closest('input, textarea, select');
    if (e.key === '?' && !inField) { e.preventDefault(); $('keys-dialog').showModal(); return; }
    if (!S.doc) return;
    const typing = e.target instanceof Element && e.target.closest('input, textarea, select');
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { if (typing) return; e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return; }
    if (typing) return;
    if (e.key === ' ') { e.preventDefault(); S.playing ? pause() : play(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); seek(snap(S.t) - (e.shiftKey ? 1 : 1 / S.doc.fps)); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); seek(snap(S.t) + (e.shiftKey ? 1 : 1 / S.doc.fps)); }
    else if (e.key === 'Home') seek(0);
    else if (e.key === 'End') seek(S.doc.duration);
    else if (e.key.toLowerCase() === 'k' && selLayer()) { const l = selLayer(); toggleKey(l, 'x'); toggleKey(l, 'y'); }
    else if (e.key.toLowerCase() === 'n') document.querySelector('[data-mode="note"]').click();
    else if (e.key.toLowerCase() === 's' && !e.ctrlKey && !e.metaKey && selLayer()) { e.preventDefault(); splitAt(selLayer(), S.doc.layers.indexOf(selLayer())); }
    else if (e.key.toLowerCase() === 'v') document.querySelector('[data-mode="select"]').click();
    else if ((e.key === 'Delete' || e.key === 'Backspace') && S.key && selLayer()) document.querySelector('[data-act="del-key"]')?.click();
    else if ((e.key === 'Delete' || e.key === 'Backspace') && selLayer()) document.querySelector('[data-act="del"]')?.click();
    else if (e.key === 'Escape') { S.sel = { kind: 'doc' }; S.key = null; renderAll(); }
  });

  /* ── projects ──────────────────────────────────── */
  $('project-select').onchange = async e => { if (S.dirty) await save(); S.name = e.target.value; if (S.name) openProject(S.name); };
  /* New video: a dialog with the shapes drawn to scale, instead of two prompt() boxes. */
  function openNewDialog() {
    if (!S.session) { goChat(); return; }
    $('new-error').textContent = '';
    $('new-dialog').showModal();
    $('new-name').select();
  }
  $('new-project').onclick = openNewDialog;
  $('new-cancel').onclick = () => $('new-dialog').close();
  $('new-form').onsubmit = async e => {
    e.preventDefault();
    const name = $('new-name').value.trim();
    if (!name) { $('new-error').textContent = 'Beri nama dulu, misalnya promo-kopi.'; $('new-name').focus(); return; }
    const [width, height] = (new FormData($('new-form')).get('fmt') || '1080x1920').split('x').map(Number);
    const duration = Math.min(600, Math.max(1, Number($('new-duration').value) || 8));
    $('new-create').disabled = true;
    try {
      if (S.dirty) await save();
      const created = await api(`${base()}/projects`, { method: 'POST', ...json({ name, width, height, fps: 30, duration, title: name }) });
      S.name = created.name;
      $('new-dialog').close();
      await loadProjects();
    } catch (err) {
      $('new-error').textContent = err.message;
    } finally { $('new-create').disabled = false; }
  };
  $('show-keys').onclick = () => $('keys-dialog').showModal();
  $('tl-keys').onclick = () => $('keys-dialog').showModal();
  $('undo').onclick = undo;
  $('redo').onclick = redo;
  $('send-ai').onclick = sendNotesToAI;

  /* ── right panel tabs ──────────────────────────── */
  function showTab(name) {
    S.tab = name;
    document.querySelectorAll('[data-tab]').forEach(b => { const on = b.dataset.tab === name; b.classList.toggle('on', on); b.setAttribute('aria-selected', String(on)); });
    $('inspector').hidden = name !== 'props';
    $('chat-panel').hidden = name !== 'chat';
    $('code-panel').hidden = name !== 'code';
    if (name === 'chat') { $('chat-dot').hidden = true; Chat.open(); }
    if (name === 'code') fillCode();
  }
  document.querySelectorAll('[data-tab]').forEach(b => { b.onclick = () => showTab(b.dataset.tab); });

  /* ── code: the motion.json the AI reads and edits ─ */
  function fillCode() { if (S.doc) { $('code-text').value = JSON.stringify(S.doc, null, 2); $('code-status').textContent = ''; } }
  $('code-reload').onclick = fillCode;
  $('code-apply').onclick = async () => {
    let parsed;
    try { parsed = JSON.parse($('code-text').value); } catch (e) { $('code-status').textContent = `JSON tidak valid: ${e.message}`; return; }
    checkpoint();
    S.doc = parsed;
    S.dirty = true;
    $('code-status').textContent = 'Menyimpan…';
    renderAll();
    await save();
    $('code-status').textContent = S.dirty ? 'Belum tersimpan, lihat pesan di atas.' : 'Diterapkan dan tersimpan.';
    if (!S.dirty) fillCode();
  };

  /* ── split ─────────────────────────────────────── */
  // Cut a layer in two at the playhead. Keys are on the video's timeline, so both halves keep moving
  // exactly as before; a video clip's second half starts further into its file.
  function splitAt(layer, index) {
    const t = snap(S.t), start = layer.start || 0, end = layer.end ?? S.doc.duration, frame = 1 / S.doc.fps;
    if (t <= start + frame / 2 || t >= end - frame / 2) { setSave('Taruh playhead di tengah lapisan untuk memotong', 'warn'); return; }
    mutate(doc => {
      const second = JSON.parse(JSON.stringify(layer));
      second.id = newId(layer.type);
      second.name = `${layer.name || layer.id} (2)`;
      second.start = +t.toFixed(4);
      if (layer.type === 'video') second.clipStart = +((layer.clipStart || 0) + (t - start) * (layer.speed || 1)).toFixed(4);
      layer.end = +t.toFixed(4);
      doc.layers.splice(index + 1, 0, second);
      S.sel = { kind: 'layer', id: second.id };
      S.key = null;
    });
  }

  /* ── export ────────────────────────────────────── */
  $('export').onclick = async () => {
    if (!S.doc) return;
    $('export-status').textContent = ''; $('export-progress').hidden = true;
    $('export-dialog').showModal();
    await engineInfo();
    // A render this project started earlier (from this device or another) is still going: follow it.
    try {
      const { jobs } = await api(`${base()}/renders?project=${encodeURIComponent(S.name)}`);
      const running = (jobs || []).find(j => j.state === 'running' || j.state === 'queued');
      if (running) followRender(running.id);
    } catch (_) { /* no list: nothing to follow */ }
  };
  $('export-where').onchange = () => { $('export-gpu-row').hidden = $('export-where').value !== 'server'; };
  $('export-start').onclick = () => {
    const scale = +$('export-scale').value, quality = +$('export-quality').value;
    const run = $('export-where').value === 'server' && S.engine && S.engine.engine
      ? exportOnServer(scale, quality, $('export-gpu').value) : exportVideo(scale, quality);
    run.catch(e => {
      S.exporting = false;
      $('export-status').textContent = `Gagal: ${e.message}`; $('export-start').disabled = false;
    });
  };
  $('export-cancel').onclick = async () => {
    if (!S.renderJob) return;
    try { await api(`${base()}/render/${S.renderJob}/cancel`, { method: 'POST' }); } catch (e) { $('export-status').textContent = e.message; }
  };

  /* Server render: the C engine on the machine that serves the editor does the work (threads, memory cap,
     GPU encode when there is one). The page only starts it and watches, so closing it changes nothing. */
  async function engineInfo() {
    if (!S.engine) { try { S.engine = await api('/api/studio/engine'); } catch (_) { S.engine = { engine: false, error: 'tidak terjangkau' }; } }
    const e = S.engine, where = $('export-where');
    where.querySelector('[value=server]').disabled = !e.engine;
    if (!e.engine) where.value = 'browser';
    $('export-gpu-row').hidden = where.value !== 'server';
    $('export-engine').textContent = e.engine
      ? `Engine C siap · ${e.threads} thread · batas memori ${e.mem_mb} MB · GPU: ${(e.gpus || []).join(', ') || 'tidak ada, pakai CPU'}`
      : `Engine C belum tersedia (${e.error || 'belum dikompilasi'}). Ekspor memakai browser ini.`;
  }

  async function exportOnServer(scale, quality) {
    if (S.dirty) await save();
    const gpu = $('export-gpu').value;
    const level = quality >= 0.2 ? 'high' : 'normal';
    $('export-start').disabled = true;
    const job = await api(`${base()}/project/${encodeURIComponent(S.name)}/render`, { method: 'POST', ...json({ scale, quality: level, gpu }) });
    followRender(job.id);
  }

  /* Render progress comes on the live stream; without it (or for the final link) the job is asked directly. */
  function followRender(id) {
    S.renderJob = id;
    $('export-progress').hidden = false;
    $('export-start').disabled = true;
    $('export-cancel').hidden = false;
    clearInterval(S.renderTimer);
    const tick = async () => {
      if (S.live && S.renderJob !== id) { clearInterval(S.renderTimer); return; }
      if (S.live) return;   // the stream is carrying it
      try { showRender(await api(`${base()}/render/${id}`)); } catch (e) { $('export-status').textContent = e.message; }
    };
    tick();
    S.renderTimer = setInterval(tick, 1000);
    const known = S.renders.find(j => j.id === id);
    if (known) showRender(known);
  }

  function onRenders(jobs) {
    S.renders = jobs || [];
    const job = S.renderJob && S.renders.find(j => j.id === S.renderJob);
    if (job) showRender(job);
  }

  async function showRender(job) {
    if (!job || job.id !== S.renderJob) return;
    $('export-progress').value = job.progress || 0;
    $('export-status').textContent = job.message || 'Menunggu…';
    if (!['done', 'error', 'cancelled'].includes(job.state)) return;
    clearInterval(S.renderTimer);
    S.renderJob = null;
    $('export-cancel').hidden = true;
    $('export-start').disabled = false;
    if (job.state === 'done') {
      let full = job;
      if (!full.url) { try { full = await api(`${base()}/render/${job.id}`); } catch (_) { /* the file is in the chat folder anyway */ } }
      $('export-status').innerHTML = `Selesai dalam ${full.seconds} dtk (${esc(full.encoder)}): ${full.url ? `<a href="${esc(full.url)}" target="_blank" rel="noopener">${esc(full.file.split('/').pop())}</a>` : esc(full.file)}. File ini juga bisa dibagikan di chat.`;
    } else {
      $('export-status').textContent = job.state === 'cancelled' ? 'Render dibatalkan.' : `Gagal: ${job.error}`;
    }
  }

  const even = n => Math.max(2, Math.round(n / 2) * 2);
  async function pickEncoder(width, height, fps, bitrate) {
    if (!('VideoEncoder' in window)) return null;
    for (const codec of ['avc1.640034', 'avc1.640028', 'avc1.4d0028', 'avc1.42e01f']) {
      const config = { codec, width, height, bitrate, framerate: fps, avc: { format: 'annexb' } };
      try { const s = await VideoEncoder.isConfigSupported(config); if (s.supported) return config; } catch (_) { /* next */ }
    }
    return null;
  }

  function wav(buffer) {
    const channels = buffer.numberOfChannels, rate = buffer.sampleRate, frames = buffer.length;
    const out = new DataView(new ArrayBuffer(44 + frames * channels * 2));
    const str = (o, s) => [...s].forEach((c, i) => out.setUint8(o + i, c.charCodeAt(0)));
    str(0, 'RIFF'); out.setUint32(4, 36 + frames * channels * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
    out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, channels, true); out.setUint32(24, rate, true);
    out.setUint32(28, rate * channels * 2, true); out.setUint16(32, channels * 2, true); out.setUint16(34, 16, true);
    str(36, 'data'); out.setUint32(40, frames * channels * 2, true);
    const data = [...Array(channels)].map((_, c) => buffer.getChannelData(c));
    let o = 44;
    for (let i = 0; i < frames; i++) for (let c = 0; c < channels; c++) { const v = Math.max(-1, Math.min(1, data[c][i])); out.setInt16(o, v < 0 ? v * 0x8000 : v * 0x7fff, true); o += 2; }
    return new Blob([out.buffer], { type: 'audio/wav' });
  }

  async function exportVideo(scale, quality) {
    if (S.dirty) await save();
    pause();
    // A browser export draws from the clips themselves, never from their small proxies.
    const hadProxy = S.useProxy;
    if (hadProxy) { S.useProxy = false; await refreshVideos(); }
    try { await exportInBrowser(scale, quality); } finally { if (hadProxy) { S.useProxy = true; refreshVideos(); } }
  }

  async function exportInBrowser(scale, quality) {
    const doc = JSON.parse(JSON.stringify(S.doc));
    const status = t => { $('export-status').textContent = t; };
    const bar = $('export-progress');
    $('export-start').disabled = true; bar.hidden = false; bar.value = 0;
    status('Menyiapkan font dan gambar…');
    await loadFonts(); await loadAssets();
    const W = even(doc.width * scale), H = even(doc.height * scale);
    const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext('2d');
    const frames = Math.max(1, Math.round(doc.duration * doc.fps));
    const job = Array.from(crypto.getRandomValues(new Uint8Array(10)), b => b.toString(16).padStart(2, '0')).join('');
    const hasVideo = (doc.layers || []).some(l => l.type === 'video');
    Object.values(S.videos).forEach(v => v.pause());
    S.exporting = true;
    const draw = async i => {
      if (hasVideo) await R.seekVideos(doc, i / doc.fps, S.videos);
      ctx.setTransform(W / doc.width, 0, 0, H / doc.height, 0, 0);
      R.drawFrame(ctx, doc, i / doc.fps, { images: S.images, videos: S.videos, luts: S.luts });
    };
    let video = null, format = 'h264';
    const config = await pickEncoder(W, H, doc.fps, Math.round(W * H * doc.fps * quality));
    if (config) {
      const chunks = [];
      let failure = null;
      const encoder = new VideoEncoder({ output: chunk => { const b = new Uint8Array(chunk.byteLength); chunk.copyTo(b); chunks.push(b); }, error: e => { failure = e; } });
      encoder.configure(config);
      for (let i = 0; i < frames; i++) {
        if (failure) throw failure;
        await draw(i);
        const frame = new VideoFrame(canvas, { timestamp: Math.round((i * 1e6) / doc.fps), duration: Math.round(1e6 / doc.fps) });
        encoder.encode(frame, { keyFrame: i % (doc.fps * 2) === 0 });
        frame.close();
        while (encoder.encodeQueueSize > 4) await new Promise(r => setTimeout(r, 4));
        if (i % 5 === 0) { bar.value = (i / frames) * 0.85; status(`Merender frame ${i + 1} dari ${frames}…`); await new Promise(r => setTimeout(r, 0)); }
      }
      await encoder.flush(); encoder.close();
      if (failure) throw failure;
      video = new Blob(chunks, { type: 'video/h264' });
    } else {
      // No WebCodecs H.264 here: record the canvas in real time instead (slower, still exact frames per time).
      format = 'webm';
      status('Browser ini tanpa encoder H.264, merekam secara real time…');
      const stream = canvas.captureStream(doc.fps);
      const mime = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'].find(m => MediaRecorder.isTypeSupported(m));
      const recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: Math.round(W * H * doc.fps * quality) });
      const parts = [];
      recorder.ondataavailable = e => { if (e.data.size) parts.push(e.data); };
      const done = new Promise(r => { recorder.onstop = r; });
      recorder.start();
      const started = performance.now();
      await new Promise(resolve => {
        const step = () => {
          const t = (performance.now() - started) / 1000;
          if (t >= doc.duration) { resolve(); return; }
          draw(Math.round(t * doc.fps)).then(() => { bar.value = (t / doc.duration) * 0.85; requestAnimationFrame(step); });
        };
        step();
      });
      recorder.stop(); await done;
      video = new Blob(parts, { type: 'video/webm' });
    }
    let audio = null;
    if ((doc.audio || []).some(a => S.buffers[a.src] && !a.muted)) {
      status('Mencampur audio…');
      const rate = 48000;
      const offline = new OfflineAudioContext(2, Math.ceil(doc.duration * rate), rate);
      R.scheduleAudio(offline, doc, S.buffers, 0);
      audio = wav(await offline.startRendering());
    }
    const parts = `${base()}/project/${encodeURIComponent(S.name)}/export`;
    status('Mengunggah…'); bar.value = 0.9;
    await api(`${parts}/part?job=${job}&kind=video&format=${format}`, { method: 'POST', body: video });
    if (audio) await api(`${parts}/part?job=${job}&kind=audio&format=wav`, { method: 'POST', body: audio });
    status('Menggabungkan video dan audio…');
    const result = await api(`${parts}/finish?job=${job}&fps=${doc.fps}&duration=${doc.duration}`, { method: 'POST' });
    bar.value = 1;
    S.exporting = false;
    $('export-status').innerHTML = `Selesai: <a href="${esc(result.url)}" target="_blank" rel="noopener">${esc(result.file.split('/').pop())}</a> (${(result.bytes / 1048576).toFixed(1)} MB). File ini juga bisa dibagikan di chat.`;
    $('export-start').disabled = false;
  }

  /* ── render everything ─────────────────────────── */
  function renderAll() {
    if (!S.doc) return;
    fitCanvas(); renderView(); renderLists(); renderInspector(); renderTimeline();
  }

  // The chat page opens a project (or another chat's Studio) through a message.
  window.addEventListener('message', e => {
    if (e.source !== window.parent || !e.data || e.data.type !== 'sq-studio-open') return;
    const changed = e.data.session && e.data.session !== S.session;
    if (changed) { S.session = e.data.session; S.name = ''; S.images = {}; S.buffers = {}; S.peaks = {}; }
    if (e.data.project) S.name = e.data.project;
    if (changed || e.data.project) loadProjects().catch(err => banner(esc(err.message)));
  });
  window.addEventListener('beforeunload', e => { if (S.dirty) { save(); e.preventDefault(); } });

  loadProjects().catch(e => { banner(esc(e.message)); emptyStage('error'); });
})();
