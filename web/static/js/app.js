(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const esc = value => {
    const box = document.createElement('div');
    box.textContent = String(value ?? '');
    return box.innerHTML;
  };
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* private window */ } },
    remove(key) { try { localStorage.removeItem(key); } catch { /* private window */ } },
  };

  const ICONS = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    clip: '<path d="m21 11-9.2 9.2a6 6 0 0 1-8.5-8.5l8.6-8.5a4 4 0 0 1 5.7 5.6l-8.6 8.5a2 2 0 0 1-2.8-2.8l8.5-8.5"/>',
    send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/>',
    team: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 5a3.2 3.2 0 0 1 0 6M21 20a6 6 0 0 0-4-5.6"/>',
    chat: '<path d="M4 5h16v11H9l-5 4z"/>',
    chart: '<path d="M4 19V5M4 19h16M8 15l4-5 3 3 5-6"/>',
    theme: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
    terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>',
    film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    router: '<rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 17h.01M11 17h.01M12 14V9M8.5 6.5a5 5 0 0 1 7 0M6 4a8.5 8.5 0 0 1 12 0"/>',
    code: '<path d="m8 7-5 5 5 5M16 7l5 5-5 5M13.5 4l-3 16"/>',
    layout: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11"/>',
    bug: '<rect x="7" y="8" width="10" height="12" rx="5"/><path d="M12 8v12M9 5l1.5 2M15 5l-1.5 2M3 13h4M17 13h4M4 19l3.5-2M20 19l-3.5-2M4 7l3.5 2M20 7l-3.5 2"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/>',
    edit: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    retry: '<path d="M4 12a8 8 0 0 1 14-5.3L20 9"/><path d="M20 4v5h-5"/><path d="M20 12a8 8 0 0 1-14 5.3L4 15"/><path d="M4 20v-5h5"/>',
    speak: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/>',
    pin: '<path d="M9 4h6l-1 6 4 3v2H6v-2l4-3z"/><path d="M12 15v6"/>',
    download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    command: '<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"/>',
    keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8"/>',
    bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
    zip: '<path d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M12 10v1M12 13v1M12 16v1"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    wave: '<path d="M3 12h2M7 8v8M11 5v14M15 9v6M19 11v2"/>',
    pdf: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M8.5 17v-4H10a1.2 1.2 0 0 1 0 2.5H8.5M13 17v-4h1a2 2 0 0 1 0 4z"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
  };
  const icon = (name, size = 18) =>
    `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
  document.querySelectorAll('[data-icon]').forEach(el => { el.insertAdjacentHTML('afterbegin', icon(el.dataset.icon)); });

  if (window.DOMPurify) {
    window.DOMPurify.addHook('afterSanitizeAttributes', node => {
      if (node.tagName === 'A') { node.setAttribute('target', '_blank'); node.setAttribute('rel', 'noopener noreferrer'); }
    });
  }
  // Model output can carry text from web pages, so it never reaches innerHTML unsanitized.
  // An image written as a relative path (![chart](plot.png)) is a file in this chat's folder,
  // so it is loaded through the session file endpoint. Absolute and data: URLs are left alone.
  const RELATIVE_SRC = /^(?![a-z][a-z0-9+.-]*:|\/|#)/i;

  // Math: $$..$$ and \[..\] as blocks, $..$ and \(..\) inline. Formulas are taken out before marked
  // runs (it would eat the backslashes and underscores) and put back as KaTeX after sanitizing.
  // KaTeX escapes its input and runs with trust off, so \href and raw HTML never come through.
  // Code spans and fences are skipped. "$5 dan $10" stays text: the closing $ may not touch a digit.
  const MATH_RE = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<![\\$\w])\$(?![\s$])((?:\\\$|[^$\n])+?)(?<![\s\\])\$(?![\d$])/g;
  const CODE_RE = /(^|\n) {0,3}(```+|~~~+)[\s\S]*?(?:\n {0,3}\2[ \t]*(?=\n|$)|$)|(`+)(?!`)[\s\S]*?[^`]\3(?!`)/g;
  const takeMath = text => {
    const found = [];
    const swap = chunk => chunk.replace(MATH_RE, (whole, block, bracket, paren, inline) => {
      const tex = block ?? bracket ?? paren ?? inline;
      if (!tex || !tex.trim()) return whole;
      found.push({ tex: tex.trim(), display: block !== undefined || bracket !== undefined, raw: whole });
      return `KTXMATH${found.length - 1}END`;
    });
    let out = '';
    let last = 0;
    for (const m of text.matchAll(CODE_RE)) {
      out += swap(text.slice(last, m.index)) + m[0];
      last = m.index + m[0].length;
    }
    out += swap(text.slice(last));
    return { text: out, found };
  };
  const putMath = (html, found) => html.replace(/KTXMATH(\d+)END/g, (whole, i) => {
    const item = found[Number(i)];
    if (!item) return whole;
    if (!window.katex) return esc(item.raw);
    try {
      return window.katex.renderToString(item.tex, {
        displayMode: item.display, throwOnError: false, strict: 'ignore', trust: false, output: 'htmlAndMathml',
      });
    } catch (_) {
      return `<code>${esc(item.raw)}</code>`;
    }
  });

  const md = raw => {
    if (!(window.marked && window.DOMPurify)) return esc(raw).replace(/\n/g, '<br>');
    const { text, found } = takeMath(raw || '');
    const clean = putMath(window.DOMPurify.sanitize(window.marked.parse(text)), found);
    if (!clean.includes('<img')) return clean;
    const doc = new DOMParser().parseFromString(`<body>${clean}</body>`, 'text/html');
    doc.querySelectorAll('img').forEach(img => {
      const src = img.getAttribute('src') || '';
      img.setAttribute('loading', 'lazy');
      img.setAttribute('decoding', 'async');
      if (S.session && src && RELATIVE_SRC.test(src)) {
        img.setAttribute('src', `/api/session/${encodeURIComponent(S.session)}/file?path=${encodeURIComponent(src)}`);
      }
    });
    return doc.body.innerHTML;
  };

  // The trading and router pages open inside this page, so the chat keeps running behind them.
  // The EA talks to the trading server on the default port, so the live data is there. Only a
  // local chat can reach it; through a tunnel the panel comes from this server.
  const LOCAL = ['127.0.0.1', 'localhost'].includes(location.hostname);
  const APPS = {
    trading: {
      title: 'Trading', path: '/trading', loading: 'Membuka panel trading',
      src: () => (LOCAL && !APPS.trading.here ? `http://${location.hostname}/trading?embed=1` : '/trading?embed=1'),
      newTab: () => (LOCAL ? `http://${location.hostname}/trading` : '/trading'),
    },
    router: { title: 'Router & token', path: '/router', loading: 'Membuka router model', src: () => '/router?embed=1', newTab: () => '/router' },
    ide: {
      title: 'IDE', path: '/ide', loading: 'Membuka IDE',
      src: () => {
        const want = S.ideOpen || { session: S.session || '' };
        S.ideOpen = null;
        const query = new URLSearchParams({ embed: '1', ...Object.fromEntries(Object.entries(want).filter(([, v]) => v)) });
        return `/ide?${query}`;
      },
      newTab: () => '/ide',
    },
    studio: {
      title: 'Studio video', path: '/studio', loading: 'Membuka studio video',
      src: () => {
        const code = S.studioCode;
        S.studioCode = '';
        return code || `/studio?embed=1&session=${encodeURIComponent(S.studioSession || S.session || '')}${S.studioProject ? `&project=${encodeURIComponent(S.studioProject)}` : ''}`;
      },
      newTab: () => '/studio',
    },
  };
  const viewFromPath = () => Object.keys(APPS).find(name => APPS[name].path === location.pathname.replace(/\/+$/, '')) || '';
  $('nav-trading').href = APPS.trading.newTab();

  const S = {
    models: [], byId: {}, model: 'auto', session: null, ws: null, busy: false,
    attachments: [], features: {}, sessions: [], stopTurn: null,
    teams: [], team: null, members: [], lead: null, reviewer: null, selected: null, run: null, events: null, templates: [],
  };

  /* ── Helpers ──────────────────────────────────────── */
  function toast(message) {
    const el = $('toast');
    el.textContent = message;
    el.classList.remove('hidden');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => el.classList.add('hidden'), 3500);
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    let data = {};
    try { data = await response.json(); } catch { /* no body */ }
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(e => e.msg).join(', ') : data.detail;
      throw new Error(detail || `Permintaan gagal (${response.status})`);
    }
    return data;
  }
  const post = (url, body = {}) => api(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });

  const shortModel = id => String(id || '').split('/').pop();
  // "[web] Title" -> ['web', 'Title']: the tag says where a chat started; people read the title.
  const splitTag = title => {
    const m = /^\[(web|terminal|team)\]\s*/i.exec(title || '');
    return m ? [m[1].toLowerCase(), title.slice(m[0].length)] : ['', title || ''];
  };
  const showTitle = title => { $('chat-title').textContent = splitTag(title)[1] || 'Chat'; };
  const formatTokens = n => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n || 0));
  const formatSeconds = ms => {
    const seconds = Math.round((ms || 0) / 1000);
    if (seconds < 1) return '<1d';
    return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}d` : `${seconds}d`;
  };

  /* ── Drawers and theme ────────────────────────────── */
  function closeDrawers() {
    $('rail').classList.remove('open');
    $('inspector').classList.remove('open');
    $('scrim').classList.add('hidden');
  }
  function openDrawer(id) {
    $(id).classList.add('open');
    $('scrim').classList.remove('hidden');
  }
  const isNarrow = () => window.matchMedia('(max-width: 820px)').matches;
  const isMedium = () => window.matchMedia('(max-width: 1180px)').matches;
  function togglePanel(storeKey, shellClass) {
    const collapsed = $('shell').classList.toggle(shellClass);
    store.set(storeKey, collapsed ? '1' : '0');
  }
  if (store.get('rail-collapsed') === '1') $('shell').classList.add('no-rail');
  if (store.get('panel-collapsed') === '1') $('shell').classList.add('no-panel');
  $('open-rail').onclick = () => (isNarrow() ? openDrawer('rail') : togglePanel('rail-collapsed', 'no-rail'));
  $('close-rail').onclick = closeDrawers;
  $('open-inspector').onclick = () => {
    if (isMedium()) openDrawer('inspector');
    else togglePanel('panel-collapsed', 'no-panel');
    loadStack();
  };
  $('close-inspector').onclick = () => {
    if (isMedium()) closeDrawers();
    else togglePanel('panel-collapsed', 'no-panel');
  };
  $('scrim').onclick = closeDrawers;
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeDrawers();
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
      e.preventDefault();
      $('open-rail').click();
    }
  });

  const THEMES = { system: 'Tema: ikuti sistem', light: 'Tema: terang', dark: 'Tema: gelap' };
  function applyTheme(name) {
    if (name === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.dataset.theme = name;
    $('theme-label').textContent = THEMES[name];
    document.querySelectorAll('#app-frames iframe').forEach(frame => tellFrame(frame, { type: 'sq-theme', theme: name }));
  }
  const tellFrame = (frame, message) => { try { frame.contentWindow.postMessage(message, '*'); } catch { /* not loaded */ } };
  let theme = THEMES[store.get('theme')] ? store.get('theme') : 'system';
  applyTheme(theme);
  $('toggle-theme').onclick = () => {
    const order = Object.keys(THEMES);
    theme = order[(order.indexOf(theme) + 1) % order.length];
    store.set('theme', theme);
    applyTheme(theme);
  };

  /* Palette: the colour set from tokens.css, shared with the pages in the frames. */
  function showPalette() {
    const id = window.SquidPage ? SquidPage.getPalette() : 'laut';
    const item = window.SquidPage ? SquidPage.paletteList().find(p => p.id === id) : null;
    $('palette-label').textContent = `Palet: ${item ? item.label : id}`;
    $('palette-swatch').dataset.palette = id;
    document.querySelectorAll('#app-frames iframe').forEach(frame => tellFrame(frame, { type: 'sq-palette', palette: id }));
  }
  if (window.SquidPage && SquidPage.paletteMenu) SquidPage.paletteMenu($('pick-palette'), showPalette);
  showPalette();

  /* The empty chat greets by the time of day. */
  (() => {
    const hour = new Date().getHours();
    $('hero-greet').textContent = hour < 4 ? 'Masih begadang?' : hour < 11 ? 'Selamat pagi' : hour < 15 ? 'Selamat siang'
      : hour < 19 ? 'Selamat sore' : 'Selamat malam';
  })();

  /* ── Models ───────────────────────────────────────── */
  function fillModelOptions(select, selected, withAuto) {
    // One group per provider and upstream ("Default · kg"), from the server, so a new router groups itself.
    const groups = {};
    S.models.forEach(m => { (groups[m.group_label || m.provider] ||= []).push(m); });
    const options = Object.entries(groups).sort(([a], [b]) => a.localeCompare(b)).map(([label, list]) =>
      `<optgroup label="${esc(label)}">${list.map(m =>
        `<option value="${esc(m.id)}" ${m.id === selected ? 'selected' : ''}>${esc(m.display_name)}</option>`).join('')}</optgroup>`).join('');
    select.innerHTML = (withAuto ? `<option value="auto" ${selected === 'auto' ? 'selected' : ''}>Auto (hemat: model termurah yang andal)</option>` : '') + options;
  }

  async function loadModels(retries = 3) {
    const data = await api('/api/models');
    S.models = Object.values(data.models).flat();
    S.byId = Object.fromEntries(S.models.map(m => [m.id, m]));
    const saved = store.get('model');
    S.model = saved === 'auto' || S.byId[saved] ? saved : data.default_model;
    fillModelOptions($('model-select'), S.model, true);

    S.modelsAt = Date.now();

    // If the registry was empty (API still starting), retry after a short delay.
    if (!S.models.length && retries > 0) {
      setTimeout(() => loadModels(retries - 1).catch(() => {}), 2500);
    }
  }

  // Providers change on the router page while this page stays open, so the list is fetched
  // again when the router says so, when the chat comes back into view, and when the tab returns.
  async function refreshModels(announce = false) {
    if (S.booting || S.refreshingModels) return;
    S.refreshingModels = true;
    const before = new Set(S.models.map(m => m.id));
    const chosen = S.model;
    try {
      await loadModels(0);
      if (S.model !== chosen && S.session) {
        post('/api/model/select', { session_id: S.session, model_id: S.model }).catch(() => {});
        if (chosen !== 'auto') toast(`Model ${chosen} tidak tersedia lagi. Chat memakai ${S.model}.`);
      }
      const added = S.models.filter(m => !before.has(m.id)).length;
      const gone = [...before].filter(id => !S.byId[id]).length;
      if (announce && (added || gone)) {
        toast(`Daftar model diperbarui: ${S.models.length} model${added ? `, ${added} baru` : ''}${gone ? `, ${gone} hilang` : ''}.`);
      }
      // Only when the list really changed: a re-render would drop what is being typed there.
      if ((added || gone) && S.view === 'team' && window.SQTeam) window.SQTeam.modelsChanged();
    } catch { /* the next trigger tries again */ } finally {
      S.refreshingModels = false;
    }
  }
  try {
    // A router page open in another tab says so here.
    S.channel = new BroadcastChannel('squidwings');
    S.channel.onmessage = e => { if (e.data && e.data.type === 'sq-models') refreshModels(true); };
  } catch { /* old browser: focus and view changes still refresh */ }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - (S.modelsAt || 0) > 30000) refreshModels(true);
  });

  $('model-select').onchange = async e => {
    S.model = e.target.value;
    store.set('model', S.model);
    try { await post('/api/model/select', { session_id: S.session, model_id: S.model }); } catch (err) { toast(err.message); }
  };

  /* ── Sessions ─────────────────────────────────────── */
  function groupLabel(timestamp) {
    const days = Math.floor((Date.now() - timestamp * 1000) / 86400000);
    if (days < 1) return 'Hari ini';
    if (days < 2) return 'Kemarin';
    if (days < 7) return '7 hari terakhir';
    return 'Lebih lama';
  }

  function renderSessions() {
    const query = $('session-search').value.trim().toLowerCase();
    const list = $('session-list');
    const pins = pinned();
    const shown = S.sessions.filter(s => !query || (s.title || '').toLowerCase().includes(query))
      .sort((a, b) => Number(pins.has(b.session_id)) - Number(pins.has(a.session_id)));
    if (!shown.length) {
      list.innerHTML = `<p class="session-none">${query ? 'Tidak ada yang cocok.' : 'Belum ada chat.'}</p>`;
      return;
    }
    let html = '';
    let group = '';
    let n = 0;
    for (const s of shown) {
      const label = pins.has(s.session_id) ? 'Disematkan' : groupLabel(s.updated_at);
      if (label !== group) { html += `<div class="session-group">${label}</div>`; group = label; }
      // "[web] Title": the place a chat started in becomes a small tag, the title stays readable.
      const m = /^\[(web|terminal|team)\]\s*/i.exec(s.title || '');
      const where = m ? m[1].toLowerCase() : '';
      let title = (m ? (s.title || '').slice(m[0].length) : s.title) || 'Chat';
      if (/^\[?\s*no answer\b/i.test(title)) title = 'Chat tanpa judul';   // an old title made from a failed reply
      const WHERE = { web: ['web', 'web'], team: ['tim agent', 'team'], terminal: ['terminal', 'cli'], telegram: ['Telegram', 'tg'] };
      const [whereTitle, whereShort] = WHERE[where] || [where, where];
      const tag = where ? `<span class="session-tag t-${esc(where)}" title="Dimulai dari ${esc(whereTitle)}">${esc(whereShort)}</span>` : '';
      html += `<div class="session ${s.session_id === S.session ? 'active' : ''}" data-id="${esc(s.session_id)}" style="--i:${Math.min(n++, 14)}">
        <button class="session-open" title="${esc(s.title)} · klik kanan untuk menu">${pins.has(s.session_id) ? `<span class="session-pin" aria-label="disematkan">${icon('pin', 12)}</span>` : ''}${tag}<span class="session-name">${esc(title)}</span></button>
        <button class="session-del" aria-label="Hapus chat ${esc(s.title)}">${icon('trash', 14)}</button>
      </div>`;
    }
    list.innerHTML = html;
    list.querySelectorAll('.session').forEach(item => {
      const id = item.dataset.id;
      item.querySelector('.session-open').onclick = () => openSession(id);
      item.querySelector('.session-del').onclick = () => deleteSession(id);
      if (window.SQMenu) window.SQMenu.on(item, () => sessionMenu(id));
    });
  }
  $('session-search').oninput = renderSessions;

  async function loadSessions() {
    try { S.sessions = (await api('/api/sessions')).sessions || []; } catch (e) { toast(e.message); }
    renderSessions();
  }

  async function deleteSession(id) {
    const target = S.sessions.find(s => s.session_id === id);
    if (!confirm(`Hapus "${target?.title || 'chat ini'}"? Riwayat, folder, vault, dan graph chat ini ikut terhapus.`)) return;
    try {
      await post(`/api/session/${encodeURIComponent(id)}/delete`);
      if (id === S.session) await newChat();
      await loadSessions();
    } catch (e) { toast(e.message); }
  }

  function setEmpty(empty) {
    $('view-chat').classList.toggle('is-empty', empty);
  }

  function clearThread() {
    $('thread').innerHTML = '';
    setEmpty(true);
    updateScrollUi();
  }

  async function newChat() {
    if (S.busy) { toast('Tunggu balasan selesai, atau tekan stop.'); return; }
    const data = await api('/api/session/new');
    S.session = data.session_id;
    store.set('session', S.session);
    if (window.SQ.terminal) window.SQ.terminal.sessionChanged();
    if (frames.studio) { S.studioProject = ''; S.studioSession = ''; tellFrame(frames.studio.frame, { type: 'sq-studio-open', session: S.session }); }
    $('chat-title').textContent = 'Chat baru';
    clearThread();
    await post('/api/model/select', { session_id: S.session, model_id: S.model }).catch(() => {});
    if (!S.booting) showView('chat');
    closeDrawers();
    renderSessions();
    loadStack();
    $('input').focus();
  }
  $('new-chat').onclick = () => newChat().catch(e => toast(e.message));

  async function openSession(id) {
    if (S.busy) { toast('Tunggu balasan selesai, atau tekan stop.'); return; }
    try {
      const data = await api(`/api/session/${encodeURIComponent(id)}/messages`);
      S.session = id;
      store.set('session', id);
      if (window.SQ.terminal) window.SQ.terminal.sessionChanged();
      if (frames.studio) { S.studioProject = ''; S.studioSession = ''; tellFrame(frames.studio.frame, { type: 'sq-studio-open', session: id }); }
      showTitle(data.title || 'Chat');
      clearThread();
      const answers = [];
      for (const message of data.messages || []) {
        if (message.role === 'user') addUser(userLabel(message.content));
        else if (message.role === 'assistant') answers.push(addHistoryAnswer(message));
      }
      attachFileCards(id, answers);
      scrollDown(true);
      if (!S.booting) showView('chat');
      closeDrawers();
      renderSessions();
      loadStack();
      attachLive(id).catch(() => {});   // an answer still being written there (another tab, a phone that slept)
    } catch (e) { toast(e.message); }
  }

  /* ── Thread ───────────────────────────────────────── */
  const scroller = $('scroller');
  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
  const scrollDown = force => { if (force || nearBottom()) scroller.scrollTop = scroller.scrollHeight; };
  function updateScrollUi() {
    $('to-bottom').classList.toggle('hidden', nearBottom());
    $('topbar').classList.toggle('scrolled', scroller.scrollTop > 4);
  }
  scroller.addEventListener('scroll', updateScrollUi, { passive: true });
  $('to-bottom').onclick = () => scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' });

  // A sent message carries its attachments as <file name="..."> blocks; the bubble shows only their names.
  const FILE_BLOCK = /<file name="([^"]*)"[^>]*>[\s\S]*?<\/file>\s*/g;
  function userLabel(content) {
    const names = [];
    const typed = String(content || '').replace(FILE_BLOCK, (_, name) => { names.push(`[${name}] `); return ''; });
    return names.join('') + typed.trim();
  }

  function addUser(text) {
    setEmpty(false);
    const row = document.createElement('div');
    row.className = 'turn-user';
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;
    row.appendChild(bubble);
    // Like the big chat apps: copy the message, or put it back in the box to change and send again.
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    actions.innerHTML = `<button type="button" class="msg-act" data-act="copy" title="Salin" aria-label="Salin pesan">${icon('copy', 15)}</button>
      <button type="button" class="msg-act" data-act="edit" title="Edit lalu kirim ulang" aria-label="Edit pesan">${icon('edit', 15)}</button>`;
    actions.querySelector('[data-act="copy"]').onclick = e => copyText(text, e.currentTarget);
    actions.querySelector('[data-act="edit"]').onclick = () => {
      const input = $('input');
      input.value = text.replace(/^(\[[^\]]+\] )+/, '');
      autosize();
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      $('composer').classList.remove('sq-flash'); void $('composer').offsetWidth; $('composer').classList.add('sq-flash');
    };
    row.appendChild(actions);
    $('thread').appendChild(row);
    return row;
  }

  /* The prompt that produced an answer: the user message right before it. */
  function promptBefore(turnEl) {
    let el = turnEl.previousElementSibling;
    while (el && !el.classList.contains('turn-user')) el = el.previousElementSibling;
    const bubble = el && el.querySelector('.bubble');
    return bubble ? bubble.textContent : '';
  }

  /* Read an answer aloud with the browser's voice (Indonesian when the system has one). */
  function speak(text, button) {
    const synth = window.speechSynthesis;
    if (!synth) { toast('Browser ini tidak punya suara bawaan.'); return; }
    if (synth.speaking) { synth.cancel(); document.querySelectorAll('.msg-act.speaking').forEach(b => b.classList.remove('speaking')); return; }
    const plain = String(text || '').replace(/```[\s\S]*?```/g, ' (kode) ').replace(/[#*_`>|]/g, ' ').replace(/\s+/g, ' ').trim();
    const say = new SpeechSynthesisUtterance(plain.slice(0, 6000));
    const voice = synth.getVoices().find(v => /^id/i.test(v.lang));
    if (voice) say.voice = voice;
    say.lang = voice ? voice.lang : 'id-ID';
    say.onend = say.onerror = () => button.classList.remove('speaking');
    button.classList.add('speaking');
    synth.speak(say);
  }

  /* Files the agent shared come as Markdown images or links. Videos and audio need their own players. */
  // A relative link (![video](renders/share.mp4)) goes through the session file endpoint instead;
  // its file name is in the ?path= query, so the extension is read from there.
  const SHARED_FILE = /^\/api\/session\/[^/]+\/files\//;
  const SESSION_FILE = /^\/api\/session\/[^/]+\/file\?/;
  const mediaExt = src => {
    let name = src.split('?')[0];
    if (SESSION_FILE.test(src)) {
      try { name = new URLSearchParams(src.split('?')[1]).get('path') || ''; } catch { name = ''; }
    }
    return (name.match(/\.(\w+)$/) || [])[1]?.toLowerCase();
  };
  function enhanceMedia(container) {
    container.querySelectorAll('img').forEach(img => {
      const src = img.getAttribute('src') || '';
      const shared = SHARED_FILE.test(src);
      if (!shared && !SESSION_FILE.test(src)) return;
      const ext = mediaExt(src);
      // An ordinary image from the session folder is already fine as an <img>.
      if (!shared && !['mp4', 'm4v', 'webm', 'mov', 'mp3', 'wav', 'ogg', 'm4a'].includes(ext)) return;
      const label = img.getAttribute('alt') || '';
      let player = null;
      if (['mp4', 'm4v', 'webm', 'mov'].includes(ext)) {
        player = document.createElement('video');
        player.controls = true;
        player.playsInline = true;
        player.preload = 'metadata';
      } else if (['mp3', 'wav', 'ogg', 'm4a'].includes(ext)) {
        player = document.createElement('audio');
        player.controls = true;
      }
      const figure = document.createElement('figure');
      figure.className = 'shared-file';
      if (player) {
        player.src = src;
        figure.appendChild(player);
      } else {
        img.loading = 'lazy';
        figure.appendChild(img.cloneNode());
      }
      const caption = document.createElement('figcaption');
      const download = document.createElement('a');
      download.href = src;
      download.download = '';
      download.textContent = 'Unduh';
      caption.append(label ? `${label} · ` : '', download);
      figure.appendChild(caption);
      (img.closest('p') && img.closest('p').childNodes.length === 1 ? img.closest('p') : img).replaceWith(figure);
    });
    container.querySelectorAll('a[href]').forEach(link => {
      if (SHARED_FILE.test(link.getAttribute('href'))) { link.setAttribute('download', ''); link.removeAttribute('target'); }
    });
    fileChips(container);
  }

  /* A shared file on a line of its own becomes a card: its type, its name, download, and open in the IDE.
     Cards next to each other sit together in one row that wraps. */
  function fileChips(container) {
    container.querySelectorAll('p').forEach(par => {
      const links = par.querySelectorAll('a[href]');
      if (links.length !== 1 || par.children.length !== 1) return;
      const link = links[0];
      const href = link.getAttribute('href') || '';
      if (!SHARED_FILE.test(href) || par.textContent.trim() !== link.textContent.trim()) return;
      const [, session, rest] = href.match(/^\/api\/session\/([^/]+)\/files\/([^?#]+)/) || [];
      if (!rest) return;
      let relative = '';
      try { relative = rest.split('/').map(decodeURIComponent).join('/'); } catch { relative = rest; }
      const name = relative.split('/').pop();
      const ext = ((name.match(/\.([^.]+)$/) || [])[1] || 'file').slice(0, 4).toUpperCase();
      const chip = document.createElement('div');
      chip.className = 'file-chip';
      chip.innerHTML = `<span class="fx">${esc(ext)}</span><span class="fn" title="${esc(relative)}">${esc(name)}</span>
        <a class="fa" href="${esc(href)}" download>Unduh</a>${S.features.ide ? '<button type="button" class="fa" data-ide>IDE</button>' : ''}`;
      const ide = chip.querySelector('[data-ide]');
      if (ide) ide.onclick = () => openIde({ session: decodeURIComponent(session), path: relative });
      par.replaceWith(chip);
    });
    container.querySelectorAll('.file-chip').forEach(chip => {
      if (chip.parentElement.classList.contains('file-chips')) return;
      const group = document.createElement('div');
      group.className = 'file-chips';
      chip.before(group);
      let next = chip;
      while (next && next.classList && next.classList.contains('file-chip')) {
        const after = next.nextElementSibling;
        group.append(next);
        next = after;
      }
    });
  }

  // Money and quantities line up on the right, so a laba rugi or neraca reads down its columns.
  const NUMERIC_CELL = /^\(?[-+−]?\s*(Rp\.?\s*)?[\d.,]+(\s?%|\s?(unit|pcs|kg|jt|rb))?\)?$/i;
  function enhanceTables(container) {
    container.querySelectorAll('.prose table, table').forEach(table => {
      if (table.dataset.enhanced) return;
      table.dataset.enhanced = '1';
      table.querySelectorAll('td').forEach(cell => {
        if (NUMERIC_CELL.test(cell.textContent.trim())) cell.classList.add('num');
      });
    });
  }

  function enhanceCode(container) {
    enhanceMedia(container);
    enhanceTables(container);
    container.querySelectorAll('pre > code').forEach(code => {
      const pre = code.parentElement;
      if (pre.parentElement.classList.contains('code')) return;
      if (window.hljs) window.hljs.highlightElement(code);
      const language = (code.className.match(/language-([\w-]+)/) || [])[1] || 'code';
      const lines = code.textContent.replace(/\n$/, '').split('\n').length;
      const wrap = document.createElement('div');
      wrap.className = 'code';
      wrap.innerHTML = `<div class="code-head"><span class="code-lang">${esc(language)}</span><span class="code-lines">${lines} baris</span>
        <span class="code-acts"><button class="code-copy code-wrap" type="button" aria-pressed="false" title="Bungkus baris panjang">Bungkus</button>
        <button class="code-copy" type="button" data-copy>Salin</button></span></div>`;
      wrap.querySelector('[data-copy]').onclick = e => copyText(code.textContent, e.currentTarget);
      wrap.querySelector('.code-wrap').onclick = e => {
        const on = wrap.classList.toggle('wrapped');
        e.currentTarget.setAttribute('aria-pressed', String(on));
      };
      pre.replaceWith(wrap);
      wrap.appendChild(pre);
      // A long block shows its start; the rest is one click away, so the answer around it stays readable.
      if (lines > 30) {
        wrap.classList.add('folded');
        const more = document.createElement('button');
        more.type = 'button';
        more.className = 'code-expand';
        more.textContent = `Tampilkan semua ${lines} baris`;
        more.onclick = () => {
          const open = wrap.classList.toggle('folded');
          more.textContent = open ? `Tampilkan semua ${lines} baris` : 'Ringkas lagi';
          if (open) wrap.scrollIntoView({ block: 'nearest' });
        };
        wrap.appendChild(more);
      }
    });
  }

  async function copyText(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      const before = button.textContent;
      button.textContent = 'Tersalin';
      setTimeout(() => { button.textContent = before; }, 1600);
    } catch { toast('Tidak bisa menyalin.'); }
  }

  /* What the agent is doing, in words a person would use. */
  const TOOLS = {
    read_file: ['Membaca', 'read', 'path'], list_directory: ['Melihat folder', 'read', 'path'],
    file_search: ['Mencari di file', 'search', 'quote'], find_symbol: ['Mencari simbol', 'search', 'plain'],
    code_outline: ['Melihat struktur', 'read', 'path'], project_overview: ['Mempelajari proyek', 'read', 'plain'],
    file_write: ['Menulis', 'edit', 'path'], file_patch: ['Mengedit', 'edit', 'path'],
    file_move: ['Memindahkan', 'edit', 'path'], file_delete: ['Menghapus', 'edit', 'path'],
    dir_create: ['Membuat folder', 'edit', 'path'], execute_command: ['Menjalankan', 'run', 'plain'],
    terminal_run: ['Di terminal', 'run', 'plain'],
    run_checks: ['Menjalankan tes', 'run', 'plain'], aislop_scan: ['Memeriksa kualitas kode', 'run', 'plain'],
    web_research: ['Mencari dan membaca web', 'web', 'quote'], web_search: ['Mencari di web', 'web', 'quote'],
    web_read: ['Membuka', 'web', 'url'], git_status: ['Melihat status git', 'read', 'plain'],
    git_diff: ['Melihat perubahan', 'read', 'path'], code_graph: ['Menelusuri graph kode', 'search', 'quote'],
    load_skill: ['Membuka panduan', 'read', 'plain'], share_file: ['Mengirim file', 'write', 'plain'], vault_search: ['Mencari catatan', 'search', 'quote'],
    vault_read: ['Membaca catatan', 'read', 'path'], vault_write: ['Menulis catatan', 'edit', 'path'],
    vault_recall: ['Mengingat catatan', 'read', 'quote'], memory_recall: ['Mengingat', 'read', 'quote'],
    memory_remember: ['Menyimpan ke memori', 'edit', 'plain'], memory_forget: ['Menghapus dari memori', 'edit', 'plain'],
    time_now: ['Melihat waktu', 'read', 'plain'],
  };
  const TRACE_SPLIT = new RegExp(`; (?=(?:${Object.keys(TOOLS).join('|')})(?: |$))`);
  const GROUPS = { read: 'membaca', edit: 'mengubah', run: 'menjalankan', search: 'mencari', web: 'dari web' };

  function describeTool(name, detail) {
    const [verb, group, style] = TOOLS[name] || [`Menjalankan ${name}`, 'run', 'plain'];
    let target = detail || '';
    if (target && style === 'path') target = target.split('/').filter(Boolean).pop() || target;
    if (target && style === 'quote') target = `“${target}”`;
    if (target && style === 'url') { try { target = new URL(target).hostname.replace(/^www\./, ''); } catch { /* not a url yet */ } }
    return { verb, group, target };
  }

  function stepRow(name, detail) {
    const { verb, target } = describeTool(name, detail);
    const li = document.createElement('li');
    li.className = 'step';
    li.title = detail || '';
    li.dataset.tool = name;
    li.innerHTML = `<span class="step-mark running"></span><span class="step-verb">${esc(verb)}</span><code class="step-target">${esc(target)}</code><span class="step-meta"></span><span class="step-ms"></span>`;
    return li;
  }

  function finishRow(li, ok, ms, meta, error) {
    const mark = li.querySelector('.step-mark');
    mark.classList.remove('running');
    mark.textContent = ok ? '✓' : '✗';
    mark.classList.add(ok ? 'ok' : 'bad');
    li.querySelector('.step-meta').textContent = meta || '';
    if (ms !== undefined) li.querySelector('.step-ms').textContent = ms >= 1000 ? `${(ms / 1000).toFixed(1)}d` : `${ms}ms`;
    if (!ok && error) li.insertAdjacentHTML('afterend', `<li class="step-error">${esc(error)}</li>`);
  }

  function addHistoryAnswer(message) {
    const turn = createTurn();
    turn.setContent(message.content);
    turn.history(message.tool_trace);
    turn.finish();
    turn.foot({ model: message.model_used, tokens: message.token_count, text: message.content });
    turn.row.dataset.ts = String(message.timestamp || 0);
    return turn;
  }

  /* Each answer that changed files gets its card back: the turn belongs to the first answer written after it began. */
  async function attachFileCards(session, answers) {
    let data;
    try { data = await api(`/api/session/${encodeURIComponent(session)}/changes`); } catch { return; }
    if (session !== S.session) return;
    for (const change of (data.turns || []).filter(x => x.by === 'ai')) {
      const owner = answers.find(a => Number(a.row.dataset.ts) >= change.started);
      if (owner && !owner.row.querySelector('.files-card')) owner.files(change);
    }
  }

  function createTurn() {
    setEmpty(false);
    const row = document.createElement('article');
    row.className = 'turn-agent';
    row.innerHTML = `
      <div class="working"><span class="pulse"></span><span class="working-text">Menyiapkan</span><span class="working-time"></span></div>
      <div class="think-line hidden"></div>
      <details class="steps hidden" open><summary><span class="chev">${icon('chevron', 12)}</span><span class="steps-title"></span></summary>
        <ul class="step-list"></ul><details class="think-box hidden"><summary>Proses berpikir</summary><pre class="thinking"></pre></details></details>
      <div class="prose"></div>
      <div class="turn-foot hidden"></div>`;
    $('thread').appendChild(row);

    const out = row.querySelector('.prose');
    const steps = row.querySelector('.steps');
    const stepList = row.querySelector('.step-list');
    const thinking = row.querySelector('.thinking');
    const thinkBox = row.querySelector('.think-box');
    const thinkLine = row.querySelector('.think-line');
    const working = row.querySelector('.working');
    const workingText = row.querySelector('.working-text');
    const workingTime = row.querySelector('.working-time');
    const started = Date.now();
    let lastEvent = started;
    let waitingModel = false;
    let waitLabel = 'Menunggu model';
    const running = new Map();
    const preparing = new Map();
    const counts = {};
    let content = '';
    let pendingRender = 0;

    // The clock always moves, so a slow model never looks like a frozen page.
    const timer = setInterval(() => {
      const seconds = Math.round((Date.now() - started) / 1000);
      workingTime.textContent = `${seconds}d`;
      if (waitingModel && Date.now() - lastEvent > 20000) workingText.textContent = `${waitLabel}, masih memproses`;
    }, 1000);

    const setStatus = (text, isWaiting = false) => {
      working.classList.remove('hidden');
      workingText.textContent = text;
      waitingModel = isWaiting;
      if (isWaiting) waitLabel = text;
      lastEvent = Date.now();
    };

    const render = () => {
      pendingRender = 0;
      const stick = nearBottom();
      out.innerHTML = md(content);
      if (stick) scrollDown(true);
      updateScrollUi();
    };

    const count = group => { counts[group] = (counts[group] || 0) + 1; };

    const updateTitle = () => {
      const parts = Object.entries(GROUPS).filter(([key]) => counts[key]).map(([key, label]) => `${counts[key]} ${label}`);
      const elapsed = Math.round((Date.now() - started) / 1000);
      if (!parts.length && thinking.textContent) parts.push('proses berpikir');
      row.querySelector('.steps-title').textContent = parts.length ? parts.join(' · ') + (elapsed ? ` · ${elapsed}d` : '') : '';
      steps.classList.toggle('hidden', !parts.length);
    };

    const stopClock = () => {
      clearInterval(timer);
      // Calls that never ran, and spinners left by a stop or an error.
      preparing.forEach(li => li.remove());
      preparing.clear();
      stepList.querySelectorAll('.step-mark.running').forEach(mark => mark.classList.remove('running'));
      working.classList.add('hidden');
      thinkLine.classList.add('hidden');
      out.classList.remove('streaming');
    };

    return {
      row,
      thinking(chunk) {
        thinking.textContent += chunk;
        thinkBox.classList.remove('hidden');
        // A live glimpse of the reasoning: the clearest sign that work is happening.
        const lines = thinking.textContent.trim().split('\n');
        thinkLine.textContent = lines[lines.length - 1].slice(-140);
        thinkLine.classList.remove('hidden');
        setStatus('Berpikir', true);
        updateTitle();
      },
      content(chunk) {
        working.classList.add('hidden');
        thinkLine.classList.add('hidden');
        waitingModel = false;
        out.classList.add('streaming');
        content += chunk;
        if (!pendingRender) pendingRender = requestAnimationFrame(render);
      },
      setContent(text) {
        stopClock();
        content = text;
        render();
        enhanceCode(out);
      },
      reset() {
        content = '';
        out.innerHTML = '';
        out.classList.remove('streaming');
        setStatus('Memperbaiki hasil', true);
      },
      history(trace) {
        // Commands contain "; " too, so only a known tool name starts a new step.
        (trace || '').split(TRACE_SPLIT).filter(Boolean).forEach(item => {
          const failed = item.endsWith(' (failed)');
          const clean = failed ? item.slice(0, -9) : item;
          const [name, ...rest] = clean.split(' ');
          const li = stepRow(name, rest.join(' '));
          stepList.appendChild(li);
          finishRow(li, !failed);
          count(describeTool(name).group);
        });
        steps.open = false;
        updateTitle();
      },
      progress(event) {
        if (event.kind === 'route') {
          setStatus(`Menunggu ${event.locked ? '🔒 ' : ''}${shortModel(event.model)}`, true);
          if (event.reason) {
            // Auto: why the autopilot picked this model (tier, success rate, cost).
            stepList.insertAdjacentHTML('beforeend',
              `<li class="step step-note">Auto memilih ${esc(shortModel(event.model))}: ${esc(String(event.reason).replace(/^Auto:\s*/, ''))}</li>`);
            steps.classList.remove('hidden');
          }
        } else if (event.kind === 'status') {
          if (event.note) {
            stepList.insertAdjacentHTML('beforeend', `<li class="step step-note">${esc(event.text)}</li>`);
            steps.classList.remove('hidden');
          }
          setStatus(event.text || 'Bekerja', true);
          scrollDown();
        } else if (event.kind === 'remembered') {
          (event.items || []).forEach(item => toast(`Diingat: ${item}`));
        } else if (event.kind === 'tool' && event.phase === 'prepare') {
          // The model is still writing this call. A big file can take a while.
          let li = preparing.get(event.index);
          if (!li) {
            li = stepRow(event.name, event.detail);
            stepList.appendChild(li);
            preparing.set(event.index, li);
            row.querySelector('.steps-title').textContent ||= 'Menyiapkan langkah';
            steps.classList.remove('hidden');
            scrollDown();
          }
          const { verb, target } = describeTool(event.name, event.detail);
          li.querySelector('.step-target').textContent = target;
          if (event.chars > 2000) li.querySelector('.step-meta').textContent = `${(event.chars / 1000).toFixed(1)} rb karakter`;
          setStatus(`${verb} ${target}`.trim(), true);
        } else if (event.kind === 'tool' && event.phase === 'start') {
          // The agent working in the Terminal panel: the panel stays as the user left it. The Terminal
          // button gets a dot, and a sudo command (which may want a password) gets one short notice.
          if (window.SQ.terminal && (event.name === 'terminal_run' || (event.name === 'execute_command' && /(^|[\s;&|(])sudo\s/.test(event.detail || '')))) {
            window.SQ.terminal.notify(/(^|[\s;&|(])sudo\s/.test(event.detail || ''));
          }
          const { verb, group, target } = describeTool(event.name, event.detail);
          const li = stepRow(event.name, event.detail);
          const prepared = [...preparing.entries()].find(([, item]) => item.dataset.tool === event.name);
          if (prepared) {
            preparing.delete(prepared[0]);
            prepared[1].replaceWith(li);
          } else {
            stepList.appendChild(li);
          }
          running.set(`${event.name}:${event.detail || ''}`, li);
          count(group);
          steps.open = true;
          updateTitle();
          setStatus(`${verb} ${target}`.trim());
          scrollDown();
        } else if (event.kind === 'tool') {
          const key = `${event.name}:${event.detail || ''}`;
          const li = running.get(key);
          running.delete(key);
          if (li) finishRow(li, event.ok, event.ms, event.meta, event.error);
          if (!running.size) setStatus('Menunggu model', true);
          scrollDown();
        }
      },
      finish() {
        if (pendingRender) { cancelAnimationFrame(pendingRender); render(); }
        stopClock();
        updateTitle();
        steps.open = false;
        enhanceCode(out);
      },
      error(message) {
        stopClock();
        out.insertAdjacentHTML('beforeend', `<div class="notice">${esc(message)}</div>`);
      },
      stopped() {
        stopClock();
        out.insertAdjacentHTML('beforeend', '<div class="notice soft">Dihentikan.</div>');
      },
      /* What this turn did to the chat's folder: every file the AI added, changed or removed, whatever wrote it. */
      files(change) {
        if (!change || !change.changes || !change.changes.length) return;
        row.querySelector('.files-card')?.remove();
        const MARK = { added: ['A', 'baru'], modified: ['M', 'diubah'], deleted: ['D', 'dihapus'] };
        const list = change.changes;
        const shown = list.slice(0, 6);
        const card = document.createElement('section');
        card.className = 'files-card';
        card.setAttribute('aria-label', 'File yang diubah AI');
        const counts = ['added', 'modified', 'deleted'].map(k => [k, list.filter(c => c.status === k).length]).filter(([, n]) => n);
        const fileRow = c => `<li><span class="fc-mark ${c.status}" title="${MARK[c.status][1]}">${MARK[c.status][0]}</span>
            <button type="button" class="fc-path" data-path="${esc(c.path)}" title="${S.features.ide ? 'Lihat perubahan di IDE' : esc(c.path)}">${esc(c.path)}</button>
            <span class="fc-n">${c.binary ? 'biner' : `<span class="fc-plus">+${c.added}</span> <span class="fc-minus">−${c.removed}</span>`}</span></li>`;
        card.innerHTML = `
          <header class="fc-head">
            <span class="fc-ico">${icon('code', 15)}</span>
            <b>${list.length} file diubah AI</b>
            <span class="fc-sum">${counts.map(([k, n]) => `${n} ${MARK[k][1]}`).join(' · ')}</span>
            <span class="fc-stat"><span class="fc-plus">+${change.added || 0}</span> <span class="fc-minus">−${change.removed || 0}</span></span>
          </header>
          <ul class="fc-list">${shown.map(fileRow).join('')}</ul>
          ${list.length > shown.length ? `<button type="button" class="fc-more">Tampilkan ${list.length - shown.length} file lagi</button>` : ''}
          <footer class="fc-foot">
            ${S.features.ide ? '<button type="button" class="fc-btn primary" data-act="ide">Buka di IDE</button>' : ''}
            <button type="button" class="fc-btn" data-act="undo" title="Kembalikan semua file ini ke sebelum jawaban ini">Urungkan perubahan</button>
          </footer>`;
        const session = S.session;
        card.querySelectorAll('.fc-path').forEach(b => {
          b.onclick = () => {
            const item = list.find(c => c.path === b.dataset.path);
            if (S.features.ide) openIde({ session, turn: change.id, diff: b.dataset.path });
            else if (item && item.status !== 'deleted') toast('Nyalakan add-on IDE untuk melihat isi dan diff-nya: python main.py addon install ide');
          };
        });
        const more = card.querySelector('.fc-more');
        if (more) more.onclick = () => { card.querySelector('.fc-list').innerHTML = list.map(fileRow).join(''); more.remove(); card.querySelectorAll('.fc-path').forEach(b => { b.onclick = () => openIde({ session, turn: change.id, diff: b.dataset.path }); }); };
        const ide = card.querySelector('[data-act="ide"]');
        if (ide) ide.onclick = () => openIde({ session, turn: change.id, path: (list.find(c => c.status !== 'deleted') || {}).path || '' });
        card.querySelector('[data-act="undo"]').onclick = async e => {
          const button = e.currentTarget;
          if (!confirm(`Kembalikan ${list.length} file ke keadaan sebelum jawaban ini? Pengembaliannya juga tercatat.`)) return;
          button.disabled = true;
          try {
            await api(`/api/session/${encodeURIComponent(session)}/changes/restore`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ turn: change.id }) });
            card.classList.add('undone');
            button.textContent = 'Sudah dikembalikan';
            toast('File dikembalikan ke sebelum jawaban ini.');
          } catch (err) { button.disabled = false; toast(err.message); }
        };
        row.insertBefore(card, row.querySelector('.turn-foot'));
      },
      foot({ model, tokens, ms, harness, text }) {
        const foot = row.querySelector('.turn-foot');
        const bits = [];
        const stats = harness || {};
        if (stats.unresolved && stats.unresolved.length) bits.push('<span class="badge warn">Belum terverifikasi</span>');
        else if (stats.verified && stats.verified.length) bits.push(`<span class="badge ok" title="${esc(stats.verified.join(', '))}">✓ Terverifikasi</span>`);
        if (model) bits.push(`<span>${esc(shortModel(model))}</span>`);
        if (stats.escalated_to) bits.push(`<span>naik ke ${esc(shortModel(stats.escalated_to))}</span>`);
        if (tokens) bits.push(`<span>${formatTokens(tokens)} token</span>`);
        if (ms) bits.push(`<span>${formatSeconds(ms)}</span>`);
        if (stats.skills_used && stats.skills_used.length) bits.push(`<span>skill ${esc(stats.skills_used.join(', '))}</span>`);
        foot.innerHTML = `<span class="foot-acts">
            <button type="button" class="msg-act" data-act="copy" title="Salin jawaban" aria-label="Salin jawaban">${icon('copy', 15)}</button>
            <button type="button" class="msg-act" data-act="retry" title="Coba lagi dengan pertanyaan yang sama" aria-label="Coba lagi">${icon('retry', 15)}</button>
            <button type="button" class="msg-act" data-act="speak" title="Bacakan" aria-label="Bacakan">${icon('speak', 15)}</button>
          </span><span class="foot-meta">${bits.join('<span class="sep">·</span>')}</span>`;
        foot.querySelector('[data-act="copy"]').onclick = e => {
          const b = e.currentTarget;
          navigator.clipboard.writeText(text || content).then(() => { b.classList.add('done'); setTimeout(() => b.classList.remove('done'), 1400); })
            .catch(() => toast('Tidak bisa menyalin.'));
        };
        foot.querySelector('[data-act="retry"]').onclick = () => {
          const prompt = promptBefore(row);
          if (!prompt) { toast('Pertanyaannya tidak ketemu.'); return; }
          if (S.busy) { toast('Tunggu balasan selesai, atau tekan stop.'); return; }
          $('input').value = prompt.replace(/^(\[[^\]]+\] )+/, '');
          autosize();
          sendMessage();
        };
        foot.querySelector('[data-act="speak"]').onclick = e => speak(text || content, e.currentTarget);
        foot.classList.remove('hidden');
      },
    };
  }

  /* ── Sending ──────────────────────────────────────── */
  function connect() {
    return new Promise((resolve, reject) => {
      if (S.ws && S.ws.readyState === WebSocket.OPEN) { resolve(S.ws); return; }
      const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
      const ws = new WebSocket(`${scheme}${location.host}/ws/chat`);
      ws.onopen = () => { S.ws = ws; resolve(ws); };
      ws.onerror = () => reject(new Error('Tidak bisa terhubung ke server.'));
    });
  }

  function setBusy(busy) {
    S.busy = busy;
    const send = $('send');
    send.classList.toggle('stop', busy);
    send.setAttribute('aria-label', busy ? 'Hentikan' : 'Kirim');
    send.innerHTML = icon(busy ? 'stop' : 'send');
  }

  async function sendMessage() {
    const input = $('input');
    const text = input.value.trim();
    if ((!text && !S.attachments.length) || S.busy) return;

    const files = S.attachments.map(a => {
      // A quote in a name would end the attribute early, and the server could no longer find the block.
      const attr = value => String(value).replace(/"/g, "'");
      const saved = a.saved ? ` saved="${attr(a.saved)}"` : '';
      return `<file name="${attr(a.name)}"${saved}>\n${a.markdown}\n</file>`;
    }).join('\n\n');
    const full = files ? `${files}\n\n${text}` : text;
    const label = S.quickLabel || (S.attachments.map(a => `[${a.name}] `).join('') + text);
    S.quickLabel = '';
    const images = S.attachments.map(a => a.image_data_url).filter(Boolean);
    S.attachments = [];
    renderChips();
    input.value = '';
    autosize();
    quickClose();

    S.liveUserRow = addUser(label);
    if ($('chat-title').textContent === 'Chat baru') $('chat-title').textContent = label.replace(/\s+/g, ' ').slice(0, 70);
    const turn = createTurn();
    scrollDown(true);
    setBusy(true);

    try {
      const ws = await connect();
      followTurn(ws, turn);
      ws.send(JSON.stringify({ message: full, session_id: S.session, images }));
    } catch (err) {
      turn.finish();
      turn.error(err.message);
      settleTurn();
    }
  }

  function settleTurn() {
    setBusy(false);
    S.stopTurn = null;
    S.liveDrop = null;
    if (S.ws) { S.ws.onmessage = null; S.ws.onclose = null; }
    loadSessions();
    loadStack();
    // The code graph rebuilds in the background after files change.
    setTimeout(loadStack, 5000);
  }

  // Stream one turn from this socket into `turn`. The answer is written by the server whether or not
  // this page stays connected: a dropped connection (phone screen off, app in the background) only
  // means following it again later, never losing it.
  function followTurn(ws, turn) {
    S.stopTurn = () => ws.send(JSON.stringify({ type: 'stop' }));
    ws.onclose = () => { S.ws = null; dropped(turn); };
    ws.onmessage = event => {
      const m = JSON.parse(event.data);
      if (m.type === 'content') turn.content(m.content);
      else if (m.type === 'thinking') turn.thinking(m.content);
      else if (m.type === 'reset') turn.reset();
      else if (m.type === 'progress') turn.progress(m);
      else if (m.type === 'meta' && m.event === 'title' && m.session_id === S.session) showTitle(m.title);
      else if (m.type === 'files' && m.turn) turn.files(m.turn);
      else if (m.type === 'done') {
        turn.finish();
        turn.foot({ model: m.model_used, tokens: m.total_tokens, ms: m.latency_ms, harness: m.harness });
        settleTurn();
      } else if (m.type === 'stopped') { turn.finish(); turn.stopped(); settleTurn(); }
      else if (m.type === 'error') { turn.finish(); turn.error(m.content); settleTurn(); }
    };
  }

  function dropped(turn) {
    S.liveDrop = { session: S.session, turn };
    turn.progress({ kind: 'status', note: true, text: 'Koneksi terputus. Jawaban tetap dikerjakan di server; menyambung lagi...' });
    reattach();
  }

  // Follow the answer still being written in `session` (after a drop, or when the chat is opened).
  async function attachLive(session, afterDrop = false) {
    if (!session) return false;
    const ws = await connect();
    return new Promise(resolve => {
      ws.onmessage = event => {
        const m = JSON.parse(event.data);
        if (m.type !== 'attach') return;
        if (!m.running) { ws.onmessage = null; resolve(false); return; }
        if (afterDrop && S.liveDrop) {
          // The record replays from the start: drop the half-shown copy first.
          S.liveDrop.turn.row.remove();
          if (S.liveUserRow) S.liveUserRow.remove();
        }
        S.session = session;
        store.set('session', session);
        S.liveUserRow = addUser(userLabel(m.message));
        const turn = createTurn();
        setBusy(true);
        followTurn(ws, turn);
        scrollDown(true);
        resolve(true);
      };
      ws.send(JSON.stringify({ type: 'attach', session_id: session, include_finished: afterDrop }));
    });
  }

  let reattaching = false;
  async function reattach() {
    if (reattaching || !S.liveDrop) return;
    if (document.hidden) return;   // tried again when the page is visible (visibilitychange)
    reattaching = true;
    try {
      const { session, turn } = S.liveDrop;
      const following = await attachLive(session, true);
      if (!following && S.liveDrop) {
        // Finished while away and already saved: show the chat as it is now.
        turn.finish();
        settleTurn();
        if (session === S.session) await openSession(session);
      }
    } catch {
      setTimeout(() => { reattaching = false; reattach(); }, 3000);
      return;
    }
    reattaching = false;
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) reattach(); });
  window.addEventListener('online', () => reattach());

  function autosize() {
    const input = $('input');
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
    input.style.overflowY = input.scrollHeight > 220 ? 'auto' : 'hidden';
  }
  $('input').oninput = autosize;
  $('input').onkeydown = e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendMessage();
    }
  };
  $('composer').onsubmit = e => {
    e.preventDefault();
    if (S.busy) { if (S.stopTurn) S.stopTurn(); return; }
    sendMessage();
  };
  document.querySelectorAll('.suggestion').forEach(button => {
    button.onclick = () => { $('input').value = button.dataset.prompt; autosize(); $('input').focus(); };
  });

  /* ── Attachments ──────────────────────────────────── */
  function renderChips() {
    const chips = $('chips');
    chips.innerHTML = S.attachments.map((a, i) =>
      `<span class="chip"><span class="chip-name">${esc(a.name)}</span><button type="button" data-remove="${i}" aria-label="Hapus ${esc(a.name)}">${icon('x', 12)}</button></span>`).join('');
    chips.querySelectorAll('[data-remove]').forEach(button => {
      button.onclick = () => { S.attachments.splice(Number(button.dataset.remove), 1); renderChips(); };
    });
    quickFilesChanged();
  }
  $('file-input').onchange = async e => { await attachFiles(e.target.files); e.target.value = ''; };
  async function attachFiles(fileList) {
    for (const file of fileList) {
      try {
        const query = `name=${encodeURIComponent(file.name)}&session=${encodeURIComponent(S.session || '')}`;
        const response = await fetch(`/api/upload?${query}`, { method: 'POST', body: file });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Unggah gagal');
        S.attachments.push(data);
        if (data.ocr_failed) toast(`${file.name}: ${data.ocr_failed} gambar belum terbaca.`);
        else if (data.images) toast(`${file.name}: ${data.images} gambar diekstrak, ${data.ocr_read} berisi teks.`);
        else if (data.truncated) toast(`${file.name} panjang: teks lengkap tersimpan, AI membukanya saat perlu.`);
      } catch (err) { toast(`${file.name}: ${err.message}`); }
    }
    renderChips();
  }

  /* ── Menu cepat ───────────────────────────────────── */
  // Ready-made jobs (web/quick_api.py, brain/utils/quick_actions.py). The auto menu offers the ones that fit what
  // is typed or attached; the panel lists them all. A video download runs on the server without a model (a
  // "quick" turn on the chat socket); the other actions send one clear instruction to the chat.
  const QM = { actions: [], action: null, preset: {}, choice: '', infoUrl: '', infoTimer: 0, suggestTimer: 0, suggested: [] };
  const quickSuffix = name => (/\.[^./\\]+$/.exec(String(name || '').toLowerCase()) || [''])[0];

  async function quickCatalog() {
    if (!QM.actions.length) QM.actions = (await api('/api/quick/actions')).actions || [];
    return QM.actions;
  }

  async function quickOpen(actionId = '', preset = {}) {
    try { await quickCatalog(); } catch (err) { toast(`Menu cepat tidak bisa dibuka: ${err.message}`); return; }
    const panel = $('quick-panel');
    panel.hidden = false;
    $('composer').classList.add('qm-open');
    $('quick-suggest').innerHTML = '';   // the panel shows the same choices
    $('quick-btn').setAttribute('aria-expanded', 'true');
    const action = QM.actions.find(a => a.id === actionId);
    if (action) quickForm(action, preset); else quickList();
    quickPlace();
  }

  // Open towards the side with room: down while the composer sits high (an empty chat), up above it otherwise.
  function quickPlace() {
    const panel = $('quick-panel');
    if (!panel || panel.hidden) return;
    const box = $('composer').getBoundingClientRect();
    const above = box.top - 16;
    const below = innerHeight - box.bottom - 16;
    const down = above < 360 && below > above;
    panel.classList.toggle('opens-down', down);
    panel.style.maxHeight = `${Math.round(Math.max(220, Math.min(560, down ? below : above)))}px`;
  }

  function quickClose() {
    const panel = $('quick-panel');
    if (!panel || panel.hidden) { quickSuggestSoon(); return; }
    panel.hidden = true;
    $('composer').classList.remove('qm-open');
    QM.action = null;
    $('quick-btn').setAttribute('aria-expanded', 'false');
    quickSuggestSoon();
  }

  function quickList() {
    QM.action = null;
    $('quick-panel').innerHTML = `
      <div class="qm-head">
        <span class="qm-head-ico">${icon('bolt', 16)}</span>
        <h3 id="quick-title">Menu cepat</h3>
        <button type="button" class="icon-btn icon-small qm-close" aria-label="Tutup menu cepat">${icon('x', 16)}</button>
      </div>
      <p class="qm-lead">Tempel link atau lampirkan file, menu yang cocok muncul sendiri di atas kotak ketik.</p>
      <div class="qm-grid">${QM.actions.map(a => `
        <button type="button" class="qm-card" data-action="${esc(a.id)}">
          <span class="qm-ico">${icon(a.icon, 20)}</span>
          <span class="qm-text"><b>${esc(a.title)}</b><small>${esc(a.blurb)}</small></span>
          <span class="qm-mode ${a.mode === 'direct' ? 'is-direct' : ''}">${a.mode === 'direct' ? 'Tanpa AI · cepat' : 'Dikerjakan AI'}</span>
        </button>`).join('')}
      </div>`;
    $('quick-panel').querySelector('.qm-close').onclick = quickClose;
    $('quick-panel').querySelectorAll('[data-action]').forEach(b => {
      b.onclick = () => quickForm(QM.actions.find(a => a.id === b.dataset.action), {});
    });
    $('quick-panel').querySelector('.qm-card')?.focus();
  }

  function quickMatches(action) {
    return S.attachments.filter(a => action.files.includes(quickSuffix(a.name)));
  }

  function quickFilesHtml(action) {
    const found = quickMatches(action);
    const picked = QM.preset.file && found.some(a => a.name === QM.preset.file) ? QM.preset.file : (found[0] || {}).name;
    const rows = found.map((a, i) => `
      <label class="qm-file"><input type="radio" name="qm-file" value="${i}" ${a.name === picked ? 'checked' : ''}>
        <span>${icon('doc', 15)} ${esc(a.name)}</span></label>`).join('');
    return `${rows || `<p class="qm-muted">Belum ada file yang cocok (${esc(action.files.slice(0, 6).join(', '))}${action.files.length > 6 ? ', ...' : ''}).</p>`}
      <button type="button" class="btn btn-ghost qm-attach">${icon('clip', 15)} Lampirkan file</button>`;
  }

  function quickChoicesHtml(choices) {
    if (!choices.length) return '';
    if (!choices.some(c => c.id === QM.choice)) QM.choice = (choices.find(c => c.id === '720') || choices[0]).id;
    return choices.map(c => `<button type="button" role="radio" class="qm-choice" aria-checked="${c.id === QM.choice}" data-choice="${esc(c.id)}">${esc(c.label)}</button>`).join('');
  }

  function quickForm(action, preset) {
    QM.action = action;
    QM.preset = preset || {};
    QM.choice = action.choices.length ? action.choices[0].id : '';
    if (action.id === 'video') QM.choice = '720';
    const needsUrl = action.needs === 'url';
    const fileWithUrl = action.also_url;
    $('quick-panel').innerHTML = `
      <div class="qm-head">
        <button type="button" class="icon-btn icon-small qm-back" aria-label="Kembali ke daftar menu">${icon('back', 16)}</button>
        <span class="qm-head-ico">${icon(action.icon, 16)}</span>
        <h3 id="quick-title">${esc(action.title)}</h3>
        <button type="button" class="icon-btn icon-small qm-close" aria-label="Tutup menu cepat">${icon('x', 16)}</button>
      </div>
      <p class="qm-lead">${esc(action.blurb)}</p>
      ${needsUrl || fileWithUrl ? `<label class="qm-field"><span>${fileWithUrl ? 'Link video (atau pilih file di bawah)' : 'Link'}</span>
        <input id="qm-url" type="url" inputmode="url" autocomplete="off" placeholder="https://" value="${esc(QM.preset.url || '')}"></label>
        <p id="qm-info" class="qm-info" aria-live="polite"></p>` : ''}
      ${action.needs === 'file' ? `<div class="qm-field"><span>File</span><div id="qm-files" class="qm-files">${quickFilesHtml(action)}</div></div>` : ''}
      ${action.choices.length ? `<div class="qm-field"><span>Pilihan</span><div id="qm-choices" class="qm-choices" role="radiogroup" aria-label="Pilihan">${quickChoicesHtml(action.choices)}</div></div>` : ''}
      ${action.note ? `<label class="qm-field"><span>${esc(action.note)}</span><textarea id="qm-note" rows="2"></textarea></label>` : ''}
      <p id="qm-error" class="qm-error" role="alert"></p>
      <div class="qm-foot">
        <span class="qm-mode ${action.mode === 'direct' ? 'is-direct' : ''}">${action.mode === 'direct' ? 'Tanpa AI · hasil langsung di chat' : 'Dikerjakan AI di chat ini'}</span>
        <button type="button" class="btn btn-primary qm-run">${icon(action.mode === 'direct' ? 'download' : 'send', 15)} Jalankan</button>
      </div>`;
    const panel = $('quick-panel');
    panel.querySelector('.qm-back').onclick = quickList;
    panel.querySelector('.qm-close').onclick = quickClose;
    panel.querySelector('.qm-run').onclick = quickRun;
    quickWireChoices();
    quickWireFiles();
    const url = $('qm-url');
    if (url) {
      url.oninput = () => { clearTimeout(QM.infoTimer); QM.infoTimer = setTimeout(quickVideoInfo, 600); };
      if (url.value) quickVideoInfo();
      (url.value ? panel.querySelector('.qm-run') : url).focus();
    } else panel.querySelector('.qm-run').focus();
  }

  function quickWireChoices() {
    $('quick-panel').querySelectorAll('[data-choice]').forEach(b => {
      b.onclick = () => {
        QM.choice = b.dataset.choice;
        $('quick-panel').querySelectorAll('[data-choice]').forEach(x => x.setAttribute('aria-checked', String(x === b)));
      };
    });
  }

  function quickWireFiles() {
    const attach = $('quick-panel').querySelector('.qm-attach');
    if (attach) attach.onclick = () => $('file-input').click();
  }

  function quickFilesChanged() {
    const box = $('qm-files');
    if (box && QM.action) { box.innerHTML = quickFilesHtml(QM.action); quickWireFiles(); }
    quickSuggestSoon();
  }

  async function quickVideoInfo() {
    const url = ($('qm-url') || {}).value?.trim() || '';
    const info = $('qm-info');
    if (!info || QM.action?.id !== 'video') return;
    if (!/^https?:\/\/\S+$/i.test(url)) { info.textContent = ''; return; }
    if (url === QM.infoUrl) return;
    QM.infoUrl = url;
    info.dataset.kind = 'busy';
    info.textContent = 'Membaca info video...';
    try {
      const data = await post('/api/quick/video-info', { url });
      if (QM.infoUrl !== url) return;
      const minutes = data.duration_s ? ` · ${Math.floor(data.duration_s / 60)}:${String(Math.round(data.duration_s % 60)).padStart(2, '0')}` : '';
      info.dataset.kind = 'ok';
      info.innerHTML = `<b>${esc(data.title)}</b><span>${esc(data.channel || data.site || '')}${minutes}</span>`;
      const box = $('qm-choices');
      if (box && data.choices?.length) { box.innerHTML = quickChoicesHtml(data.choices); quickWireChoices(); }
    } catch (err) {
      if (QM.infoUrl !== url) return;
      info.dataset.kind = 'error';
      info.textContent = err.message;
    }
  }

  async function quickRun() {
    const action = QM.action;
    if (!action) return;
    const error = $('qm-error');
    error.textContent = '';
    if (S.busy) { error.textContent = 'Tunggu balasan yang sedang berjalan selesai dulu.'; return; }
    const url = ($('qm-url') || {}).value?.trim() || '';
    const picked = document.querySelector('#qm-files input[name="qm-file"]:checked');
    const file = picked ? quickMatches(action)[Number(picked.value)] : null;
    const inputs = { choice: QM.choice, note: ($('qm-note') || {}).value?.trim() || '' };
    if (url) inputs.url = url;
    // The name the person sent (a PDF stays a PDF); the attached <file> block carries where its text is saved.
    if (file) inputs.file = file.name;
    if (action.needs === 'url' && !/^https?:\/\/\S+$/i.test(url)) { error.textContent = 'Isi link yang diawali http:// atau https://.'; $('qm-url').focus(); return; }
    if (action.needs === 'file' && !file && !(action.also_url && url)) { error.textContent = 'Lampirkan filenya dulu.'; return; }
    if (!S.session) await newChat();
    if (action.mode === 'direct') {
      const label = `Menu cepat · ${action.title} · ${url} (${(action.choices.find(c => c.id === QM.choice) || {}).label || QM.choice})`;
      quickClose();
      $('input').value = '';
      autosize();
      await quickDirect({ url, choice: QM.choice }, label);
      return;
    }
    try {
      const made = await post('/api/quick/prompt', { action: action.id, inputs });
      // Only the file this action is about goes along; other attachments stay for the next message.
      if (file) {
        S.keptAttachments = S.attachments.filter(a => a !== file);
        S.attachments = [file];
      }
      S.quickLabel = made.label;
      $('input').value = made.message;
      await sendMessage();
      if (S.keptAttachments) { S.attachments = S.keptAttachments; S.keptAttachments = null; renderChips(); }
    } catch (err) { error.textContent = err.message; }
  }

  async function quickDirect(inputs, label) {
    S.liveUserRow = addUser(label);
    if ($('chat-title').textContent === 'Chat baru') $('chat-title').textContent = label.slice(0, 70);
    const turn = createTurn();
    scrollDown(true);
    setBusy(true);
    try {
      const ws = await connect();
      followTurn(ws, turn);
      ws.send(JSON.stringify({ type: 'quick', action: 'video', session_id: S.session, inputs }));
    } catch (err) {
      turn.finish();
      turn.error(err.message);
      settleTurn();
    }
  }

  function quickSuggestSoon() {
    clearTimeout(QM.suggestTimer);
    QM.suggestTimer = setTimeout(quickSuggest, 350);
  }

  async function quickSuggest() {
    const box = $('quick-suggest');
    if (!box) return;
    const text = $('input').value;
    const files = S.attachments.map(a => a.name);
    const panelOpen = !$('quick-panel').hidden;
    if (panelOpen || (!/https?:\/\//i.test(text) && !files.length)) { box.innerHTML = ''; QM.suggested = []; return; }
    let found = [];
    try { found = (await post('/api/quick/suggest', { text, files })).suggestions || []; } catch { found = []; }
    QM.suggested = found.slice(0, 4);
    box.innerHTML = QM.suggested.length ? `<span class="qs-label">${icon('bolt', 13)} Menu cepat</span>` + QM.suggested.map((s, i) => {
      const target = s.file || (s.url ? new URL(s.url).hostname.replace(/^www\./, '') : '');
      return `<button type="button" class="qs-chip" data-suggest="${i}">${icon(s.icon, 14)}<span>${esc(s.title)}</span>${target ? `<small>${esc(target)}</small>` : ''}</button>`;
    }).join('') : '';
    box.querySelectorAll('[data-suggest]').forEach(b => {
      b.onclick = () => { const s = QM.suggested[Number(b.dataset.suggest)]; quickOpen(s.action, { url: s.url, file: s.file }); };
    });
  }

  $('quick-btn').onclick = () => ($('quick-panel').hidden ? quickOpen() : quickClose());
  window.addEventListener('resize', quickPlace);
  $('input').addEventListener('input', quickSuggestSoon);
  $('quick-panel').addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); quickClose(); $('quick-btn').focus(); } });

  /* ── Session panel ────────────────────────────────── */
  const openCards = new Set((store.get('open-cards') || 'ponytail,graphify,aislop').split(',').filter(Boolean));

  function card(key, title, summary, dot, body) {
    return `<details class="card" data-card="${key}" ${openCards.has(key) ? 'open' : ''}>
      <summary class="card-head">
        ${dot === null ? '' : `<span class="status-dot ${dot}"></span>`}
        <span class="card-title">${title}</span>
        <span class="card-summary">${summary}</span>
        <span class="card-chev">${icon('chevron', 14)}</span>
      </summary>
      <div class="card-body">${body}</div>
    </details>`;
  }

  function pathRow(path) {
    return `<div class="path"><code title="${esc(path)}">${esc(path)}</code><button class="icon-btn icon-small" type="button" data-copy="${esc(path)}" aria-label="Salin path">${icon('copy', 14)}</button></div>`;
  }

  function renderStack(info, findings) {
    const graph = info.graphify;
    const slop = info.aislop;
    const last = slop.last;
    const memory = info.memory;
    const box = $('stack');
    const toggle = (key, on, label) =>
      `<div class="row"><span>Aktif di chat ini</span><button class="switch" role="switch" aria-checked="${on}" aria-label="${label}" data-toggle="${key}"></button></div>`;
    const hasScore = last && last.score !== null && last.score !== undefined;
    const scoreTone = score => (score >= 80 ? 'good' : score >= 50 ? 'mid' : 'low');
    const sandboxOk = info.sandbox.backend && info.sandbox.backend !== 'none';

    box.innerHTML = `
      <div class="stack-group">Cara kerja agent</div>
      ${card('ponytail', 'Ponytail', esc(info.ponytail.mode), null, `
        <p class="card-text">Seberapa keras agent menahan diri dari kode berlebihan.</p>
        <div class="segmented" role="group" aria-label="Mode Ponytail">
          ${info.ponytail.modes.map(mode => `<button data-ponytail="${esc(mode)}" aria-pressed="${mode === info.ponytail.mode}">${esc(mode)}</button>`).join('')}
        </div>`)}
      ${card('graphify', 'Graphify', !graph.installed ? 'belum terpasang' : graph.built ? `${graph.nodes ?? 0} node` : 'belum ada', graph.installed && graph.built ? 'on' : '', !graph.installed
        ? '<p class="card-text">Pasang dengan <code>uv tool install graphifyy</code></p>'
        : `${graph.built
            ? `<div class="stats"><div class="stat"><strong>${graph.nodes ?? 0}</strong><span>node</span></div><div class="stat"><strong>${graph.edges ?? 0}</strong><span>relasi</span></div></div>`
            : '<p class="card-text">Dibuat otomatis setelah ada file yang berubah.</p>'}
           ${toggle('graphify', graph.enabled, 'Graphify untuk chat ini')}
           <button class="btn btn-ghost btn-small btn-block" data-action="graph">${graph.built ? 'Bangun ulang' : 'Bangun sekarang'}</button>`)}
      ${card('aislop', 'aislop', !slop.installed ? 'belum terpasang' : hasScore ? `skor ${last.score}` : 'belum dipindai', slop.installed && hasScore ? 'on' : '', !slop.installed
        ? '<p class="card-text">Pasang dengan <code>npm install -g aislop</code></p>'
        : `${hasScore
            ? `<div class="score-row"><div class="ring ${scoreTone(last.score)}" style="--value:${last.score}"><span>${last.score}</span></div>
                 <p class="card-text"><strong>${esc(last.label || '')}</strong><br>${last.errors ?? 0} error, ${last.warnings ?? 0} peringatan</p></div>`
            : '<p class="card-text">File yang diubah agent diperiksa otomatis.</p>'}
           ${toggle('aislop', slop.enabled, 'aislop untuk chat ini')}
           <button class="btn btn-ghost btn-small btn-block" data-action="aislop">Pindai sekarang</button>
           ${findings && findings.length ? `<ul class="findings">${findings.map(f => `<li>${esc(f)}</li>`).join('')}</ul>` : ''}`)}

      <div class="stack-group">Yang diingat</div>
      ${card('memory', 'Memori proyek', `${memory.decisions + memory.facts} catatan`, null, `
        <div class="stats">
          <div class="stat"><strong>${memory.decisions}</strong><span>keputusan</span></div>
          <div class="stat"><strong>${memory.facts}</strong><span>fakta</span></div>
          <div class="stat"><strong>${memory.files}</strong><span>file</span></div>
        </div>
        <p class="card-text">${info.profile.entries} hal tentang kamu diingat di semua chat.</p>`)}
      ${card('vault', 'Vault Obsidian', `${info.vault.notes} catatan`, info.vault.notes ? 'on' : '', `
        <p class="card-text">Buka folder ini di Obsidian untuk membaca catatan chat ini.</p>
        ${pathRow(info.vault.path)}`)}

      <div class="stack-group">Keamanan</div>
      ${card('sandbox', 'Sandbox', info.sandbox.error ? 'tidak tersedia' : sandboxOk ? esc(info.sandbox.backend) : 'mati', sandboxOk ? 'on' : 'bad', `
        <p class="card-text">${info.sandbox.error
          ? 'Perintah agent ditolak. Jalankan <code>python main.py doctor</code>.'
          : sandboxOk
            ? `Perintah hanya bisa menulis ke folder chat ini. Internet ${info.sandbox.network ? 'nyala' : 'mati'}.`
            : 'Perintah berjalan langsung di mesin ini.'}</p>
        ${pathRow(info.folder)}`)}`;

    box.querySelectorAll('details.card').forEach(item => {
      item.ontoggle = () => {
        if (item.open) openCards.add(item.dataset.card); else openCards.delete(item.dataset.card);
        store.set('open-cards', [...openCards].join(','));
      };
    });
    box.querySelectorAll('[data-ponytail]').forEach(button => {
      button.onclick = () => updateStack({ ponytail: button.dataset.ponytail });
    });
    box.querySelectorAll('[data-toggle]').forEach(button => {
      button.onclick = () => updateStack({ [button.dataset.toggle]: button.getAttribute('aria-checked') !== 'true' });
    });
    box.querySelectorAll('[data-action]').forEach(button => {
      button.onclick = () => runStackAction(button);
    });
    box.querySelectorAll('[data-copy]').forEach(button => {
      button.onclick = async () => {
        try { await navigator.clipboard.writeText(button.dataset.copy); toast('Path disalin.'); } catch { toast('Tidak bisa menyalin.'); }
      };
    });
  }

  async function loadStack() {
    if (!S.session) return;
    try { renderStack(await api(`/api/session/${encodeURIComponent(S.session)}/stack`)); } catch (e) { toast(e.message); }
  }
  async function updateStack(changes) {
    try { renderStack(await post(`/api/session/${encodeURIComponent(S.session)}/stack`, changes)); } catch (e) { toast(e.message); }
  }
  async function runStackAction(button) {
    const action = button.dataset.action;
    const before = button.textContent;
    button.disabled = true;
    button.textContent = 'Berjalan…';
    try {
      const data = await post(`/api/session/${encodeURIComponent(S.session)}/${action}`);
      if (data.result && data.result.ok === false) toast(data.result.error);
      renderStack(data.stack, action === 'aislop' ? data.result.findings : null);
    } catch (e) {
      toast(e.message);
      button.disabled = false;
      button.textContent = before;
    }
  }

  /* ── Views ────────────────────────────────────────── */
  // chat and team live in this page; trading and router are pages of their own, shown in a frame
  // that stays alive once opened, so switching back and forth is instant and nothing reloads.
  const READY_WAIT_MS = 2500;   // after the frame's load event, for its page to say it is ready
  const OPEN_TIMEOUT_MS = 20000;
  const frames = {};

  function sessionTitle() {
    const current = S.sessions.find(s => s.session_id === S.session);
    return current ? current.title : 'Chat baru';
  }

  function showView(name, opts = {}) {
    if (name === 'team' && S.features.teams === false) name = 'chat';
    if (!['chat', 'team'].includes(name) && !APPS[name]) name = 'chat';
    const app = APPS[name];
    const was = S.view;
    S.view = name;
    $('view-chat').classList.toggle('hidden', name !== 'chat');
    $('view-team').classList.toggle('hidden', name !== 'team');
    $('view-app').classList.toggle('hidden', !app);
    $('nav-chat').classList.toggle('hidden', name === 'chat');
    $('open-inspector').classList.toggle('hidden', name !== 'chat');
    $('app-actions').classList.toggle('hidden', !app);
    $('shell').classList.toggle('wide-mode', name !== 'chat');
    document.querySelectorAll('[data-view]').forEach(el => {
      const on = el.dataset.view === name;
      el.classList.toggle('active', on);
      if (on) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
    });
    $('chat-title').textContent = name === 'team' ? 'Tim agent' : app ? app.title : sessionTitle();
    document.title = app ? `${app.title} · SquidWings` : 'SquidWings';
    if (name === 'team' && window.SQTeam) window.SQTeam.open();
    if (was && was !== name && !app && Date.now() - (S.modelsAt || 0) > 3000) refreshModels(true);
    if (name !== 'chat') closeDrawers();
    if (app) openApp(name);
    Object.entries(frames).forEach(([key, f]) => {
      f.frame.classList.toggle('hidden', key !== name);
      tellFrame(f.frame, { type: 'sq-visible', visible: key === name });
    });
    if (!app) SquidPage.hold('app', false);
    if (name !== was && was) {
      const view = $('view-' + (app ? 'app' : name));
      view.classList.remove('view-enter');
      void view.offsetWidth;   // restart the animation
      view.classList.add('view-enter');
    }
    if (!app) store.set('view', name);
    const path = app ? app.path : '/';
    if (!opts.fromHistory && location.pathname !== path) {
      history[S.booting || opts.replace ? 'replaceState' : 'pushState']({ view: name }, '', path + location.search);
    }
  }

  function openApp(name) {
    const app = APPS[name];
    // Ask once where the trading panel lives: the port-80 server holds the EA's data, and when it
    // is not running the panel comes from this server instead of an error page.
    if (name === 'trading' && app.here === undefined) {
      app.here = !LOCAL;
      if (LOCAL) {
        if (S.view === name) {
          $('app-loader').classList.remove('hidden');
          $('app-error').classList.add('hidden');
          $('app-loader-text').textContent = app.loading;
          SquidPage.hold('app', true);
        }
        api('/api/trading/panel')
          .then(r => { app.here = !r.external; app.hint = r.hint || ''; })
          .catch(() => { app.here = true; })
          .finally(() => openApp(name));
        return;
      }
    }
    const old = frames[name];
    if (old && old.state !== 'failed') { showAppState(old); return; }
    // A reload gets a fresh frame: a new src on the old one would add a step to the Back button.
    if (old) { clearTimeout(old.timer); old.frame.remove(); }
    const frame = document.createElement('iframe');
    frame.className = 'app-frame';
    frame.title = app.title;
    frame.setAttribute('allow', 'clipboard-write');
    frame.classList.toggle('hidden', S.view !== name);
    const f = frames[name] = { frame, name, state: 'loading', started: Date.now(), timer: 0 };
    frame.addEventListener('load', () => {
      if (f.state !== 'loading') return;
      // A refused connection also fires load, with the browser's error page. Only our page says ready.
      clearTimeout(f.timer);
      f.timer = setTimeout(() => failApp(f), READY_WAIT_MS);
    });
    f.timer = setTimeout(() => failApp(f), OPEN_TIMEOUT_MS);
    frame.src = app.src();
    $('app-frames').append(frame);
    showAppState(f);
  }

  function showAppState(f) {
    if (S.view !== f.name) return;
    const app = APPS[f.name];
    $('app-loader').classList.toggle('hidden', f.state !== 'loading');
    $('app-loader-text').textContent = app.loading;
    $('app-error').classList.toggle('hidden', f.state !== 'failed');
    f.frame.classList.toggle('is-ready', f.state === 'ready');
    $('app-open').href = app.newTab();
    SquidPage.hold('app', f.state === 'loading');
  }

  function readyApp(f) {
    if (f.state === 'ready') return;
    clearTimeout(f.timer);
    // A loader that blinks for 40 ms reads as a glitch; let it show for a moment.
    const wait = Math.max(0, 280 - (Date.now() - f.started));
    setTimeout(() => {
      f.state = 'ready';
      showAppState(f);
      tellFrame(f.frame, { type: 'sq-theme', theme });
      if (window.SquidPage) tellFrame(f.frame, { type: 'sq-palette', palette: SquidPage.getPalette() });
      tellFrame(f.frame, { type: 'sq-visible', visible: S.view === f.name });
      const app = APPS[f.name];
      if (app.here && app.hint && !app.hintShown && S.view === f.name) { app.hintShown = true; toast(app.hint); }
    }, wait);
  }

  function failApp(f) {
    if (f.state !== 'loading') return;
    const app = APPS[f.name];
    if (f.name === 'trading' && !app.here) {
      // The port-80 server stopped after the check: use this server's panel rather than an error.
      app.here = true;
      app.hint = app.hint || 'Server trading port 80 tidak menjawab, jadi panel diambil dari server web. Data EA MT4 ada di server port 80.';
      f.state = 'failed';
      openApp(f.name);
      return;
    }
    f.state = 'failed';
    $('app-error-title').textContent = `${app.title} belum bisa dibuka`;
    $('app-error-text').textContent = 'Halamannya tidak menjawab. Cek server web masih jalan, lalu tekan Coba lagi.';
    showAppState(f);
  }

  window.addEventListener('message', e => {
    const f = Object.values(frames).find(x => x.frame.contentWindow === e.source);
    if (!f || !e.data || typeof e.data !== 'object') return;
    if (e.data.type === 'sq-ready') readyApp(f);
    else if (e.data.type === 'sq-models') refreshModels(true);
    else if (e.data.type === 'sq-nav' && (e.data.view === 'chat' || APPS[e.data.view])) showView(e.data.view);
    else if (e.data.type === 'sq-open-ide' && typeof e.data.path === 'string') openIde({ session: String(e.data.session || ''), path: e.data.path });
    else if (e.data.type === 'sq-chat' && typeof e.data.text === 'string' && e.data.text.trim()) {
      // The Studio hands its notes to the agent, the IDE a question about a file: they go into a chat as a message.
      // The IDE names the chat its project belongs to, so the AI works in that same folder.
      const wanted = typeof e.data.session === 'string' ? e.data.session : '';
      if (wanted && wanted !== S.session && S.sessions.some(x => x.session_id === wanted)) {
        if (S.busy) { toast('Tunggu balasan selesai dulu, lalu kirim lagi dari IDE.'); return; }
        openSession(wanted).then(() => { $('input').value = e.data.text; sendMessage(); });
        return;
      }
      showView('chat');
      if (S.busy) { $('input').value = e.data.text; toast('Pesan dari Studio siap dikirim setelah balasan ini selesai.'); return; }
      $('input').value = e.data.text;
      sendMessage();
    }
  });

  /** Open the Studio on a project: from a "see your video" button in a chat answer or a team result.
   *  A team run keeps its video in the run's own folder, so the link names that session. */
  function openStudio(project, session) {
    S.studioProject = project || S.studioProject || '';
    S.studioSession = session || '';
    if (frames.studio) tellFrame(frames.studio.frame, { type: 'sq-studio-open', session: S.studioSession || S.session, project: S.studioProject });
    showView('studio');
  }
  window.SQ_openStudio = openStudio;

  /** Open the Studio's Video Editor for code projects (a "Video Editor" button in a team result or a chat answer). */
  function openCodeEditor(query) {
    const q = new URLSearchParams({ session: query.get('session') || S.session || '', project: query.get('project') || '' });
    S.studioCode = `/studio/code?${q}`;
    if (frames.studio) { frames.studio.frame.src = S.studioCode; S.studioCode = ''; }
    showView('studio');
  }

  /** Open the IDE on this chat's folder, at a file or at the diff of one of the AI's turns. */
  function openIde(where = {}) {
    if (!S.features.ide) { toast('Add-on IDE belum aktif: python main.py addon install ide, lalu jalankan ulang server.'); return; }
    const want = { session: S.session || '', ...where };
    if (frames.ide && frames.ide.state !== 'failed') tellFrame(frames.ide.frame, { type: 'sq-ide-open', ...want });
    else S.ideOpen = want;
    showView('ide');
  }
  window.SQ_openIde = openIde;
  document.addEventListener('click', e => {
    const link = e.target instanceof Element ? e.target.closest('a[href]') : null;
    if (!link || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const url = new URL(link.getAttribute('href'), location.href);
    if (url.origin !== location.origin || link.closest('.rail-foot')) return;
    const where = url.pathname.replace(/\/+$/, '');
    if (where === '/studio/code') { e.preventDefault(); openCodeEditor(url.searchParams); return; }
    if (where === '/ide' && url.searchParams.get('path')) {
      e.preventDefault();
      openIde({ session: url.searchParams.get('session') || '', path: url.searchParams.get('path') || '' });
      return;
    }
    if (where !== '/studio') return;
    e.preventDefault();
    openStudio(url.searchParams.get('project') || '', url.searchParams.get('session') || '');
  });
  window.addEventListener('popstate', () => showView(viewFromPath() || 'chat', { fromHistory: true }));

  $('app-retry').onclick = () => {
    if (S.view === 'trading') delete APPS.trading.here;
    openApp(S.view);
  };
  $('app-reload').onclick = () => {
    const f = frames[S.view];
    if (!f) return;
    if (S.view === 'trading') delete APPS.trading.here;   // the port-80 server may have started since
    f.state = 'failed';
    openApp(S.view);
  };
  $('nav-team').onclick = () => showView('team');
  $('nav-chat').onclick = () => showView('chat');
  document.querySelectorAll('a[data-view]').forEach(link => link.addEventListener('click', e => {
    // Ctrl/Cmd/Shift/middle click still opens the page in its own tab.
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    showView(link.dataset.view);
  }));
  // Alt+1 chat, Alt+2 team, Alt+3 trading, Alt+4 router, Alt+5 studio, Alt+6 IDE.
  window.addEventListener('keydown', e => {
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    const name = { 1: 'chat', 2: 'team', 3: 'trading', 4: 'router', 5: 'studio', 6: 'ide' }[e.key];
    if (name) { e.preventDefault(); showView(name); }
  });

  /* ── Team editor (team.js) ─────────────────────────── */
  // The flow editor lives in team.js and borrows these helpers.
  window.SQ = { $, esc, icon, api, post, toast, md, store, S, fillModelOptions, shortModel, formatTokens, formatSeconds, describeTool, enhanceCode };

  /* ── Updates from GitHub ──────────────────────────── */
  // The server pulls new versions by itself; the new code runs after a restart. Said once per version.
  async function checkUpdate() {
    const info = await api('/api/update').catch(() => null);
    if (!info || !info.restart_needed) return;
    const version = String(info.new || 'baru');
    if (store.get('update-shown') === version) return;
    store.set('update-shown', version);
    toast(`Versi baru (${version}) sudah diunduh dari GitHub. Jalankan ulang server untuk memakainya.`);
  }
  setInterval(checkUpdate, 10 * 60 * 1000);

  /* ── Chat comforts: pins, rename, export, command palette, drop, shortcuts ── */
  // Pins are a per-browser convenience (localStorage): which chats stay at the top of this list.
  function pinned() {
    try { return new Set(JSON.parse(store.get('pinned-chats') || '[]')); } catch { return new Set(); }
  }
  function togglePin(id) {
    const set = pinned();
    if (set.has(id)) set.delete(id); else set.add(id);
    store.set('pinned-chats', JSON.stringify([...set]));
    renderSessions();
  }
  const titleOf = id => (S.sessions.find(s => s.session_id === id) || {}).title || 'Chat';

  async function renameSession(id, title) {
    let clean = String(title || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    if (!clean) return;
    const [tag] = splitTag(titleOf(id));
    if (tag && !splitTag(clean)[0]) clean = `[${tag}] ${clean}`;     // keep where it started
    await post(`/api/session/${encodeURIComponent(id)}/title`, { title: clean });
    const s = S.sessions.find(x => x.session_id === id);
    if (s) s.title = clean;
    if (id === S.session) showTitle(clean);
    renderSessions();
    toast('Nama chat diganti.');
  }

  /* The title in the top bar is editable in place: click it, type, Enter to keep, Esc to cancel. */
  function editTitle(id = S.session) {
    if (!id || S.view !== 'chat') return;
    const h1 = $('chat-title');
    if (h1.querySelector('input')) return;
    const before = h1.textContent;
    const input = document.createElement('input');
    input.className = 'title-edit';
    input.value = before === 'Chat baru' ? '' : before;
    input.placeholder = 'Nama chat';
    input.maxLength = 80;
    input.setAttribute('aria-label', 'Nama chat');
    h1.replaceChildren(input);
    input.focus();
    input.select();
    let done = false;
    const finish = keep => {
      if (done) return;
      done = true;
      const next = input.value.trim();
      h1.textContent = keep && next ? next : before;
      if (keep && next && next !== before) renameSession(id, next).catch(e => { h1.textContent = before; toast(e.message); });
    };
    input.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); finish(true); } else if (e.key === 'Escape') finish(false); };
    input.onblur = () => finish(true);
  }
  $('chat-title').title = 'Klik untuk ganti nama';
  $('chat-title').onclick = () => editTitle();

  async function exportChat(id = S.session) {
    try {
      const data = await api(`/api/session/${encodeURIComponent(id)}/messages`);
      const title = data.title || titleOf(id);
      const lines = [`# ${title}`, '', `_Diekspor dari SquidWings, ${new Date().toLocaleString('id-ID')}_`, ''];
      for (const m of data.messages || []) {
        if (m.role === 'user') lines.push('## Kamu', '', userLabel(m.content), '');
        else if (m.role === 'assistant') lines.push(`## Agent${m.model_used ? ` (${shortModel(m.model_used)})` : ''}`, '', m.content || '', '');
      }
      const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${title.replace(/[^\w\- ]+/g, '').trim().replace(/\s+/g, '-').slice(0, 60) || 'chat'}.md`;
      document.body.append(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    } catch (e) { toast(e.message); }
  }

  function sessionMenu(id) {
    const isPinned = pinned().has(id);
    return [
      { heading: titleOf(id).slice(0, 40) },
      { label: 'Buka', run: () => openSession(id) },
      { label: 'Ganti nama', hint: 'klik judul', run: async () => { if (id !== S.session) await openSession(id); editTitle(id); } },
      { label: isPinned ? 'Lepas sematan' : 'Sematkan di atas', run: () => togglePin(id) },
      { label: 'Ekspor Markdown', run: () => exportChat(id) },
      { separator: true },
      { label: 'Hapus chat', danger: true, run: () => deleteSession(id) },
    ];
  }

  /* Command palette (Ctrl+K): every chat and every action, found by typing. */
  const palette = { el: null, items: [], index: 0 };
  function commands() {
    const go = view => () => showView(view);
    const list = [
      { group: 'Aksi', label: 'Chat baru', hint: 'Ctrl+Shift+O', icon: 'plus', run: () => newChat().catch(e => toast(e.message)) },
      { group: 'Aksi', label: 'Ganti nama chat ini', icon: 'edit', run: () => { showView('chat'); setTimeout(() => editTitle(), 60); } },
      { group: 'Aksi', label: pinned().has(S.session) ? 'Lepas sematan chat ini' : 'Sematkan chat ini', icon: 'pin', run: () => togglePin(S.session) },
      { group: 'Aksi', label: 'Ekspor chat ini (Markdown)', icon: 'download', run: () => exportChat() },
      { group: 'Aksi', label: 'Lampirkan file', icon: 'clip', run: () => $('file-input').click() },
      { group: 'Aksi', label: 'Buka terminal', hint: 'Ctrl+`', icon: 'terminal', run: () => $('open-terminal').click() },
      { group: 'Aksi', label: 'Ganti tema', icon: 'theme', run: () => $('toggle-theme').click() },
      { group: 'Aksi', label: 'Pintasan keyboard', hint: 'Ctrl+/', icon: 'keyboard', run: () => showShortcuts() },
      { group: 'Halaman', label: 'Chat', hint: 'Alt+1', icon: 'chat', run: go('chat') },
      { group: 'Halaman', label: 'Tim agent', hint: 'Alt+2', icon: 'team', run: go('team') },
      { group: 'Halaman', label: 'Trading', hint: 'Alt+3', icon: 'chart', run: go('trading') },
      { group: 'Halaman', label: 'Router & token', hint: 'Alt+4', icon: 'router', run: go('router') },
      { group: 'Halaman', label: 'Studio video', hint: 'Alt+5', icon: 'film', run: go('studio') },
      ...(S.features.ide ? [{ group: 'Halaman', label: 'IDE: file dan proyek buatan AI', hint: 'Alt+6', icon: 'code', run: go('ide') }] : []),
    ];
    if (window.SquidPage) for (const p of SquidPage.paletteList()) {
      list.push({ group: 'Palet warna', label: `Palet ${p.label}`, icon: 'theme', run: () => { SquidPage.applyPalette(p.id, true); showPalette(); } });
    }
    for (const s of S.sessions) list.push({ group: 'Chat', label: s.title || 'Chat', icon: pinned().has(s.session_id) ? 'pin' : 'chat', run: () => openSession(s.session_id) });
    return list;
  }
  function openPalette() {
    if (palette.el) { closePalette(); return; }
    const wrap = document.createElement('div');
    wrap.className = 'cmdk';
    wrap.innerHTML = `<div class="cmdk-box" role="dialog" aria-label="Palet perintah">
        <div class="cmdk-search">${icon('search', 18)}<input type="text" placeholder="Cari chat, halaman, atau aksi…" aria-label="Cari perintah" autocomplete="off"><kbd>Esc</kbd></div>
        <div class="cmdk-list" role="listbox"></div>
        <div class="cmdk-foot"><span><kbd>↑</kbd><kbd>↓</kbd> pilih</span><span><kbd>Enter</kbd> buka</span><span><kbd>Ctrl</kbd><kbd>K</kbd> tutup</span></div>
      </div>`;
    document.body.append(wrap);
    palette.el = wrap;
    const input = wrap.querySelector('input');
    const all = commands();
    const render = () => {
      const q = input.value.trim().toLowerCase();
      const words = q.split(/\s+/).filter(Boolean);
      palette.items = all.filter(c => words.every(w => `${c.label} ${c.group}`.toLowerCase().includes(w))).slice(0, 60);
      palette.index = Math.min(palette.index, Math.max(0, palette.items.length - 1));
      let group = '';
      const listEl = wrap.querySelector('.cmdk-list');
      listEl.innerHTML = palette.items.length ? palette.items.map((c, i) => {
        const head = c.group !== group ? `<div class="cmdk-group">${esc((group = c.group))}</div>` : '';
        return `${head}<button type="button" class="cmdk-item${i === palette.index ? ' on' : ''}" data-i="${i}" role="option" aria-selected="${i === palette.index}">
          ${icon(c.icon || 'chevron', 16)}<span>${esc(c.label)}</span>${c.hint ? `<kbd>${esc(c.hint)}</kbd>` : ''}</button>`;
      }).join('') : '<p class="cmdk-none">Tidak ada yang cocok.</p>';
      listEl.querySelectorAll('.cmdk-item').forEach(b => {
        b.onclick = () => runPalette(Number(b.dataset.i));
        b.onmousemove = () => { if (palette.index !== Number(b.dataset.i)) { palette.index = Number(b.dataset.i); mark(); } };
      });
    };
    const mark = () => wrap.querySelectorAll('.cmdk-item').forEach(b => {
      const on = Number(b.dataset.i) === palette.index;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', String(on));
      if (on) b.scrollIntoView({ block: 'nearest' });
    });
    input.oninput = () => { palette.index = 0; render(); };
    input.onkeydown = e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); palette.index = Math.min(palette.items.length - 1, palette.index + 1); mark(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); palette.index = Math.max(0, palette.index - 1); mark(); }
      else if (e.key === 'Enter') { e.preventDefault(); runPalette(palette.index); }
      else if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
    };
    wrap.onmousedown = e => { if (e.target === wrap) closePalette(); };
    palette.index = 0;
    render();
    input.focus();
  }
  function closePalette() { if (palette.el) { palette.el.remove(); palette.el = null; } }
  function runPalette(i) {
    const c = palette.items[i];
    closePalette();
    if (c) c.run();
  }

  function showShortcuts() {
    if (document.querySelector('.kbd-sheet')) return;
    const rows = [
      ['Ctrl+K', 'Palet perintah: cari chat dan aksi'], ['Ctrl+Shift+O', 'Chat baru'], ['Ctrl+B', 'Tampilkan/sembunyikan daftar chat'],
      ['Ctrl+`', 'Terminal'], ['Alt+1 … Alt+6', 'Chat, tim, trading, router, studio, IDE'], ['Enter', 'Kirim'], ['Shift+Enter', 'Baris baru'],
      ['Esc', 'Tutup panel atau menu'], ['Ctrl+/', 'Daftar ini'], ['Klik kanan chat', 'Ganti nama, sematkan, ekspor, hapus'],
    ];
    const wrap = document.createElement('div');
    wrap.className = 'cmdk kbd-sheet';
    wrap.innerHTML = `<div class="cmdk-box" role="dialog" aria-label="Pintasan keyboard"><h2>Pintasan keyboard</h2>
      <dl>${rows.map(([k, v]) => `<div><dt>${k.split(/(\s…\s|\+)/).map(x => (x === '+' || x.includes('…') ? esc(x) : `<kbd>${esc(x)}</kbd>`)).join('')}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
      <button type="button" class="btn btn-primary">Mengerti</button></div>`;
    const close = () => { wrap.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
    wrap.querySelector('button').onclick = close;
    wrap.onmousedown = e => { if (e.target === wrap) close(); };
    document.addEventListener('keydown', onKey, true);
    document.body.append(wrap);
    wrap.querySelector('button').focus();
  }

  $('open-cmdk').onclick = () => openPalette();
  window.addEventListener('keydown', e => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
    else if (mod && e.key === '/') { e.preventDefault(); showShortcuts(); }
    else if (mod && e.shiftKey && e.key.toLowerCase() === 'o') { e.preventDefault(); newChat().catch(err => toast(err.message)); }
    else if (e.key === 'Escape' && palette.el) closePalette();
  });

  /* Drop files anywhere on the chat to attach them. */
  (() => {
    const view = $('view-chat');
    let depth = 0;
    const hasFiles = e => [...(e.dataTransfer && e.dataTransfer.types || [])].includes('Files');
    view.addEventListener('dragenter', e => { if (!hasFiles(e)) return; e.preventDefault(); depth += 1; view.classList.add('drop-on'); });
    view.addEventListener('dragover', e => { if (hasFiles(e)) e.preventDefault(); });
    view.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) view.classList.remove('drop-on'); });
    view.addEventListener('drop', e => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      view.classList.remove('drop-on');
      attachFiles(e.dataTransfer.files);
    });
  })();

  /* ── Start ────────────────────────────────────────── */
  // /trading and /router open straight on that page; the chat loads behind it.
  S.booting = true;
  if (viewFromPath()) showView(viewFromPath(), { replace: true });
  (async () => {
    try {
      const config = await api('/api/config').catch(() => ({ features: {} }));
      S.features = config.features || {};
      $('nav-team').classList.toggle('hidden', S.features.teams === false);
      // Trading is an optional module: without it the link goes, and /trading shows what it is.
      $('nav-trading').classList.toggle('hidden', S.features.trading === false);
      $('nav-ide').classList.toggle('hidden', !S.features.ide);
      await loadModels();
      await loadSessions();
      const last = store.get('session');
      if (last && S.sessions.some(s => s.session_id === last)) await openSession(last);
      else {
        await newChat();
        // Its first answer may still be running on the server: follow it instead of losing it.
        if (last) await attachLive(last).catch(() => false);
      }
    } catch (e) {
      toast(e.message);
    } finally {
      showView(viewFromPath() || (store.get('view') === 'team' ? 'team' : 'chat'), { replace: true });
      S.booting = false;
      checkUpdate();
    }
  })();
})();
