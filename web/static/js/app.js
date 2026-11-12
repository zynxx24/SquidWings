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
    chevron: '<path d="m9 6 6 6-6 6"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    router: '<rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 17h.01M11 17h.01M12 14V9M8.5 6.5a5 5 0 0 1 7 0M6 4a8.5 8.5 0 0 1 12 0"/>',
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
  const md = text => {
    if (!(window.marked && window.DOMPurify)) return esc(text).replace(/\n/g, '<br>');
    const clean = window.DOMPurify.sanitize(window.marked.parse(text || ''));
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

  /* ── Models ───────────────────────────────────────── */
  function fillModelOptions(select, selected, withAuto) {
    // One group per provider and upstream ("Default · kg"), from the server, so a new router groups itself.
    const groups = {};
    S.models.forEach(m => { (groups[m.group_label || m.provider] ||= []).push(m); });
    const options = Object.entries(groups).sort(([a], [b]) => a.localeCompare(b)).map(([label, list]) =>
      `<optgroup label="${esc(label)}">${list.map(m =>
        `<option value="${esc(m.id)}" ${m.id === selected ? 'selected' : ''}>${esc(m.display_name)}</option>`).join('')}</optgroup>`).join('');
    select.innerHTML = (withAuto ? `<option value="auto" ${selected === 'auto' ? 'selected' : ''}>Auto (pilih sesuai tugas)</option>` : '') + options;
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
    const shown = S.sessions.filter(s => !query || (s.title || '').toLowerCase().includes(query));
    if (!shown.length) {
      list.innerHTML = `<p class="session-none">${query ? 'Tidak ada yang cocok.' : 'Belum ada chat.'}</p>`;
      return;
    }
    let html = '';
    let group = '';
    for (const s of shown) {
      const label = groupLabel(s.updated_at);
      if (label !== group) { html += `<div class="session-group">${label}</div>`; group = label; }
      html += `<div class="session ${s.session_id === S.session ? 'active' : ''}" data-id="${esc(s.session_id)}">
        <button class="session-open" title="${esc(s.title)}">${esc(s.title || 'Chat')}</button>
        <button class="session-del" aria-label="Hapus chat ${esc(s.title)}">${icon('trash', 14)}</button>
      </div>`;
    }
    list.innerHTML = html;
    list.querySelectorAll('.session').forEach(item => {
      const id = item.dataset.id;
      item.querySelector('.session-open').onclick = () => openSession(id);
      item.querySelector('.session-del').onclick = () => deleteSession(id);
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
      $('chat-title').textContent = data.title || 'Chat';
      clearThread();
      for (const message of data.messages || []) {
        if (message.role === 'user') addUser(userLabel(message.content));
        else if (message.role === 'assistant') addHistoryAnswer(message);
      }
      scrollDown(true);
      if (!S.booting) showView('chat');
      closeDrawers();
      renderSessions();
      loadStack();
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
    $('thread').appendChild(row);
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
  }

  function enhanceCode(container) {
    enhanceMedia(container);
    container.querySelectorAll('pre > code').forEach(code => {
      const pre = code.parentElement;
      if (pre.parentElement.classList.contains('code')) return;
      if (window.hljs) window.hljs.highlightElement(code);
      const language = (code.className.match(/language-([\w-]+)/) || [])[1] || 'code';
      const wrap = document.createElement('div');
      wrap.className = 'code';
      wrap.innerHTML = `<div class="code-head"><span>${esc(language)}</span><button class="code-copy" type="button">Salin</button></div>`;
      wrap.querySelector('button').onclick = e => copyText(code.textContent, e.currentTarget);
      pre.replaceWith(wrap);
      wrap.appendChild(pre);
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
          setStatus(`Menunggu ${shortModel(event.model)}`, true);
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
        foot.innerHTML = `${bits.join('<span class="sep">·</span>')}<button class="link-btn" type="button">Salin</button>`;
        foot.querySelector('button').onclick = e => copyText(text || content, e.currentTarget);
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
    const label = S.attachments.map(a => `[${a.name}] `).join('') + text;
    const images = S.attachments.map(a => a.image_data_url).filter(Boolean);
    S.attachments = [];
    renderChips();
    input.value = '';
    autosize();

    addUser(label);
    if ($('chat-title').textContent === 'Chat baru') $('chat-title').textContent = label.replace(/\s+/g, ' ').slice(0, 70);
    const turn = createTurn();
    scrollDown(true);
    setBusy(true);

    const settle = () => {
      setBusy(false);
      S.stopTurn = null;
      if (S.ws) { S.ws.onmessage = null; S.ws.onclose = null; }
      loadSessions();
      loadStack();
      // The code graph rebuilds in the background after files change.
      setTimeout(loadStack, 5000);
    };

    try {
      const ws = await connect();
      S.stopTurn = () => ws.send(JSON.stringify({ type: 'stop' }));
      ws.onclose = () => { S.ws = null; turn.error('Koneksi terputus.'); turn.finish(); settle(); };
      ws.onmessage = event => {
        const m = JSON.parse(event.data);
        if (m.type === 'content') turn.content(m.content);
        else if (m.type === 'thinking') turn.thinking(m.content);
        else if (m.type === 'reset') turn.reset();
        else if (m.type === 'progress') turn.progress(m);
        else if (m.type === 'meta' && m.event === 'title' && m.session_id === S.session) $('chat-title').textContent = m.title;
        else if (m.type === 'done') {
          turn.finish();
          turn.foot({ model: m.model_used, tokens: m.total_tokens, ms: m.latency_ms, harness: m.harness });
          settle();
        } else if (m.type === 'stopped') { turn.finish(); turn.stopped(); settle(); }
        else if (m.type === 'error') { turn.finish(); turn.error(m.content); settle(); }
      };
      ws.send(JSON.stringify({ message: full, session_id: S.session, images }));
    } catch (err) {
      turn.finish();
      turn.error(err.message);
      settle();
    }
  }

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
  }
  $('file-input').onchange = async e => {
    for (const file of e.target.files) {
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
    e.target.value = '';
    renderChips();
  };

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
  // Alt+1 chat, Alt+2 team, Alt+3 trading, Alt+4 router.
  window.addEventListener('keydown', e => {
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    const name = { 1: 'chat', 2: 'team', 3: 'trading', 4: 'router' }[e.key];
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

  /* ── Start ────────────────────────────────────────── */
  // /trading and /router open straight on that page; the chat loads behind it.
  S.booting = true;
  if (viewFromPath()) showView(viewFromPath(), { replace: true });
  (async () => {
    try {
      const config = await api('/api/config').catch(() => ({ features: {} }));
      S.features = config.features || {};
      $('nav-team').classList.toggle('hidden', S.features.teams === false);
      // Trading is the Enterprise module: without it the link goes, and /trading shows what it is.
      $('nav-trading').classList.toggle('hidden', S.features.trading === false);
      await loadModels();
      await loadSessions();
      const last = store.get('session');
      if (last && S.sessions.some(s => s.session_id === last)) await openSession(last);
      else await newChat();
    } catch (e) {
      toast(e.message);
    } finally {
      showView(viewFromPath() || (store.get('view') === 'team' ? 'team' : 'chat'), { replace: true });
      S.booting = false;
      checkUpdate();
    }
  })();
})();
