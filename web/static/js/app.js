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
    stop: '<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/>',
    team: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 5a3.2 3.2 0 0 1 0 6M21 20a6 6 0 0 0-4-5.6"/>',
    chat: '<path d="M4 5h16v11H9l-5 4z"/>',
    theme: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
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
  const md = text => (window.marked && window.DOMPurify)
    ? window.DOMPurify.sanitize(window.marked.parse(text || ''))
    : esc(text).replace(/\n/g, '<br>');

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
  }
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
    const groups = {};
    S.models.forEach(m => { (groups[m.provider] ||= []).push(m); });
    const options = Object.entries(groups).map(([provider, list]) =>
      `<optgroup label="${esc(provider)}">${list.map(m =>
        `<option value="${esc(m.id)}" ${m.id === selected ? 'selected' : ''}>${esc(m.display_name)}</option>`).join('')}</optgroup>`).join('');
    select.innerHTML = (withAuto ? `<option value="auto" ${selected === 'auto' ? 'selected' : ''}>Auto (pilih sesuai tugas)</option>` : '') + options;
  }

  async function loadModels() {
    const data = await api('/api/models');
    S.models = Object.values(data.models).flat();
    S.byId = Object.fromEntries(S.models.map(m => [m.id, m]));
    const saved = store.get('model');
    S.model = saved === 'auto' || S.byId[saved] ? saved : data.default_model;
    fillModelOptions($('model-select'), S.model, true);
  }

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
    showView('chat');
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
        if (message.role === 'user') addUser(message.content);
        else if (message.role === 'assistant') addHistoryAnswer(message);
      }
      scrollDown(true);
      showView('chat');
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
  const SHARED_FILE = /^\/api\/session\/[^/]+\/files\//;
  function enhanceMedia(container) {
    container.querySelectorAll('img').forEach(img => {
      const src = img.getAttribute('src') || '';
      if (!SHARED_FILE.test(src)) return;
      const ext = (src.split('?')[0].match(/\.(\w+)$/) || [])[1]?.toLowerCase();
      const label = img.getAttribute('alt') || '';
      let player = null;
      if (['mp4', 'webm', 'mov'].includes(ext)) {
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

    const files = S.attachments.map(a => `<file name="${a.name}">\n${a.markdown}\n</file>`).join('\n\n');
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
        const response = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Unggah gagal');
        S.attachments.push(data);
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
  function showView(name) {
    const team = name === 'team' && S.features.teams !== false;
    $('view-chat').classList.toggle('hidden', team);
    $('view-team').classList.toggle('hidden', !team);
    $('nav-chat').classList.toggle('hidden', !team);
    $('open-inspector').classList.toggle('hidden', team);
    $('shell').classList.toggle('team-mode', team);
    if (team) {
      $('chat-title').textContent = 'Tim agent';
      loadTeams();
      closeDrawers();
    } else if ($('chat-title').textContent === 'Tim agent') {
      const current = S.sessions.find(s => s.session_id === S.session);
      $('chat-title').textContent = current ? current.title : 'Chat baru';
    }
    store.set('view', team ? 'team' : 'chat');
  }
  $('nav-team').onclick = () => showView('team');
  $('nav-chat').onclick = () => showView('chat');

  /* ── Team canvas ──────────────────────────────────── */
  const NODE_W = 196;
  const NODE_H = 76;
  const COLUMN_GAP = 216;
  const ROW_GAP = 140;
  const newId = () => `a${Math.random().toString(36).slice(2, 9)}`;
  const posKey = (team, member) => `pos:${team}:${member}`;
  const finished = status => ['completed', 'failed', 'cancelled'].includes(status);

  const kindOf = member => member.kind || 'agent';
  const isOutput = member => ['telegram', 'database'].includes(kindOf(member));
  const memberById = id => S.members.find(m => m.id === id);
  const parentsOf = member => (member.depends_on || []).map(memberById).filter(Boolean);
  const defaultLead = () => (S.members.every(m => kindOf(m) === 'agent') && S.members[0] ? S.members[0].id : null);
  const DAYS = ['Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab', 'Min'];
  const KIND_LABEL = {
    agent: 'Agent', delay: 'Delay', gate: 'Gate IF', switch: 'Switch', merge: 'Merge', set: 'Teks',
    http: 'HTTP', telegram: 'Telegram', database: 'Database',
  };
  const isBranching = member => ['gate', 'switch'].includes(kindOf(member));
  const branchParent = member => parentsOf(member).find(isBranching);
  const conditionsOf = config => config.conditions || (config.mode ? [config] : []);
  const CONDITION_LABEL = {
    contains: 'Berisi kata kunci', not_contains: 'Tidak berisi kata kunci', equals: 'Sama persis dengan',
    regex: 'Cocok dengan regex', empty: 'Kosong', time: 'Jam tertentu', ai: 'Ditanyakan ke AI',
  };

  /* Mirrors _after_answer on the server: nodes that run once the answer exists. */
  function afterAnswer() {
    const after = new Set(S.members.filter(m => isOutput(m) && !(m.depends_on || []).length).map(m => m.id));
    const anchors = new Set([S.lead, S.reviewer].filter(Boolean));
    let changed = true;
    while (changed) {
      changed = false;
      S.members.forEach(member => {
        if (after.has(member.id) || anchors.has(member.id)) return;
        if ((member.depends_on || []).some(id => after.has(id) || anchors.has(id))) { after.add(member.id); changed = true; }
      });
    }
    return after;
  }

  /* The row each node runs in. The order on screen is the order it runs in. */
  function flowRows() {
    const after = afterAnswer();
    const rows = new Map();
    const below = (member, seen) => {
      const parents = parentsOf(member).filter(p => !seen.has(p.id));
      return parents.length ? 1 + Math.max(...parents.map(p => rowOf(p, new Set([...seen, member.id])))) : 0;
    };
    let answerRow = -1;
    function rowOf(member, seen = new Set()) {
      if (rows.has(member.id)) return rows.get(member.id);
      let row = below(member, seen);
      if (member.id === S.lead) row = Math.max(row, answerRow + 1);
      else if (member.id === S.reviewer) row = rowOf(memberById(S.lead) || member, seen) + 1;
      else if (isOutput(member) && !(member.depends_on || []).length) row = answerRow + 1;
      rows.set(member.id, row);
      return row;
    }
    const before = S.members.filter(m => m.id !== S.lead && m.id !== S.reviewer && !after.has(m.id));
    before.forEach(m => { answerRow = Math.max(answerRow, rowOf(m)); });
    [memberById(S.lead), memberById(S.reviewer)].filter(Boolean).forEach(m => { answerRow = Math.max(answerRow, rowOf(m)); });
    S.members.forEach(m => rowOf(m));
    return rows;
  }

  /* 1, 2, 3 down the flow. Each way out of an OR gate gets a letter: 5A, 5B, then 6A. */
  function flowSteps() {
    const rows = flowRows();
    const numbers = [...new Set(rows.values())].sort((a, b) => a - b);
    const letters = new Map();
    const letterOf = (member, seen = new Set()) => {
      if (letters.has(member.id)) return letters.get(member.id);
      if (seen.has(member.id)) return '';
      const parents = parentsOf(member);
      let letter = '';
      if (member.branch && parents.some(isBranching)) letter = member.branch;
      else if (parents.length === 1) letter = letterOf(parents[0], new Set([...seen, member.id]));
      letters.set(member.id, letter);
      return letter;
    };
    const steps = new Map();
    S.members.forEach(member => steps.set(member.id, `${numbers.indexOf(rows.get(member.id)) + 1}${letterOf(member)}`));
    return { steps, first: numbers[0], rows };
  }

  function autoLayout(teamId) {
    const { rows: rowMap } = flowSteps();
    const rows = {};
    S.members.forEach(member => { (rows[rowMap.get(member.id)] ||= []).push(member); });
    const widest = Math.max(...Object.values(rows).map(list => list.length));
    const rowNumbers = Object.keys(rows).map(Number).sort((a, b) => a - b);
    S.members.forEach(member => {
      const saved = teamId && store.get(posKey(teamId, member.id));
      if (saved) {
        const [x, y] = saved.split(',').map(Number);
        member.x = x;
        member.y = y;
        return;
      }
      if (member.x !== undefined) return;
      const row = rowMap.get(member.id);
      const list = rows[row];
      // Centre each row over the widest one, so the flow reads straight down.
      const offset = ((widest - list.length) * COLUMN_GAP) / 2;
      member.x = 28 + offset + list.indexOf(member) * COLUMN_GAP;
      member.y = 28 + rowNumbers.indexOf(row) * ROW_GAP;
    });
  }

  function edges() {
    const lines = [];
    const statusOf = member => (S.run && S.run.agents && S.run.agents[member.id] ? S.run.agents[member.id].status : '');
    const add = (from, to, cls = '', label = '') => {
      if (!from || !to) return;
      // A node grows while it runs, so the line leaves from its real bottom edge.
      const fromEl = document.querySelector(`.node[data-id="${CSS.escape(from.id)}"]`);
      const x1 = from.x + NODE_W / 2;
      const y1 = from.y + (fromEl ? fromEl.offsetHeight : NODE_H);
      const x2 = to.x + NODE_W / 2;
      const y2 = to.y;
      const bend = Math.max(18, Math.abs(y2 - y1) / 2);
      const active = statusOf(to) === 'running' ? ' active' : '';
      const skipped = statusOf(to) === 'skipped' ? ' skipped' : '';
      lines.push(`<path class="edge ${cls}${active}${skipped}" d="M${x1} ${y1}C${x1} ${y1 + bend} ${x2} ${y2 - bend} ${x2} ${y2}" marker-end="url(#arrow)"/>`);
      if (label) lines.push(`<text class="edge-label" x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 + 4}">${label}</text>`);
    };
    const lead = memberById(S.lead);
    const reviewer = memberById(S.reviewer);
    const after = afterAnswer();
    const dependedOn = new Set(S.members.flatMap(m => m.depends_on || []));
    S.members.filter(m => m !== reviewer).forEach(member => {
      parentsOf(member).forEach(parent => {
        const label = isBranching(parent) && member.branch ? member.branch : '';
        add(parent, member, isBranching(parent) ? 'branch' : '', label);
      });
      if (member === lead || after.has(member.id) || dependedOn.has(member.id)) return;
      add(member, lead);
    });
    if (reviewer) add(lead, reviewer, 'review');
    // An unconnected output delivers the answer, so it hangs off whatever writes it.
    const writer = reviewer || lead;
    const ends = writer ? [writer] : S.members.filter(m => !after.has(m.id) && !dependedOn.has(m.id));
    S.members.filter(m => isOutput(m) && !(m.depends_on || []).length).forEach(output => ends.forEach(end => add(end, output)));
    $('edge-lines').innerHTML = lines.join('');
  }

  function roleLabel(member) {
    if (member.id === S.lead) return 'Lead';
    if (member.id === S.reviewer) return 'Reviewer';
    return KIND_LABEL[kindOf(member)];
  }

  function nodeDetail(member) {
    const config = member.config || {};
    switch (kindOf(member)) {
      case 'delay': return `Tunggu ${config.seconds || 0} detik`;
      case 'gate': {
        const list = conditionsOf(config);
        if (list.length > 1) return `${list.length} syarat, ${(config.combine || 'and').toUpperCase()}`;
        const only = list[0] || {};
        if (only.mode === 'time') return `Jam ${only.start || '--:--'} sampai ${only.end || '--:--'}`;
        return `${CONDITION_LABEL[only.mode] || 'Syarat'}${only.value ? `: ${only.value}` : ''}`;
      }
      case 'switch': return `${(config.rules || []).length} aturan, lainnya ke D`;
      case 'merge': return config.mode === 'all' ? 'Tunggu semua jalur' : 'Lanjut saat salah satu tiba';
      case 'set': return (config.text || 'belum diisi').split('\n')[0].slice(0, 40);
      case 'http': return `${config.method || 'GET'} ${(config.url || '').replace(/^https?:\/\//, '').slice(0, 30)}`;
      case 'telegram': return config.chat_id || 'Chat bawaan';
      case 'database': return `${config.database || 'sqlite'} · ${config.table || 'hasil_tim'}`;
      default: return shortModel(member.model_id);
    }
  }

  function renderBoard() {
    const { steps, rows, first } = flowSteps();
    $('nodes').innerHTML = S.members.map(member => `
      <div class="node ${member.id === S.selected ? 'selected' : ''}" data-id="${esc(member.id)}" data-kind="${kindOf(member)}" style="left:${member.x}px;top:${member.y}px" tabindex="0">
        <div class="node-head">
          <span class="node-step">${esc(steps.get(member.id))}</span>
          <span class="node-role">${roleLabel(member)}</span>
          ${rows.get(member.id) === first ? '<span class="node-start">Mulai</span>' : ''}
        </div>
        <div class="node-name">${esc(member.name || 'Tanpa nama')}</div>
        <div class="node-model">${esc(nodeDetail(member))}</div>
        <div class="node-state"></div>
      </div>`).join('');
    document.querySelectorAll('.node').forEach(el => { el.onpointerdown = e => startDrag(e, el); });
    edges();
    paintRun();
  }

  function startDrag(event, el) {
    const member = S.members.find(m => m.id === el.dataset.id);
    const offsetX = event.clientX - member.x;
    const offsetY = event.clientY - member.y;
    if (S.selected !== member.id) {
      S.selected = member.id;
      S.panel = null;
      document.querySelectorAll('.node').forEach(n => n.classList.toggle('selected', n === el));
      renderMember();
    }
    el.setPointerCapture(event.pointerId);
    el.onpointermove = e => {
      member.x = Math.max(0, Math.min(1600 - NODE_W, e.clientX - offsetX));
      member.y = Math.max(0, Math.min(2000 - NODE_H, e.clientY - offsetY));
      el.style.left = `${member.x}px`;
      el.style.top = `${member.y}px`;
      edges();
    };
    el.onpointerup = () => {
      el.onpointermove = null;
      el.onpointerup = null;
      if (S.team) store.set(posKey(S.team, member.id), `${member.x},${member.y}`);
    };
  }

  function dayChecks(selected, attribute) {
    const chosen = selected && selected.length ? selected : [0, 1, 2, 3, 4, 5, 6];
    return `<div class="day-row">${DAYS.map((day, index) =>
      `<label class="check"><input type="checkbox" ${attribute}="${index}" ${chosen.includes(index) ? 'checked' : ''}>${day}</label>`).join('')}</div>`;
  }

  function readDays(box, attribute) {
    return [...box.querySelectorAll(`[${attribute}]`)].filter(c => c.checked).map(c => Number(c.getAttribute(attribute)));
  }

  /* One condition of a gate, or one rule of a switch. */
  function conditionFields(condition, index, listKey) {
    const mode = condition.mode || 'contains';
    const attr = `data-cond="${listKey}" data-index="${index}"`;
    const options = Object.entries(CONDITION_LABEL).map(([value, label]) =>
      `<option value="${value}" ${mode === value ? 'selected' : ''}>${label}</option>`).join('');
    const prefix = listKey === 'rules' ? `Aturan ${'ABC'[index]}` : `Syarat ${index + 1}`;
    let body = '';
    if (mode === 'time') {
      body = `<div class="field-row">
          <label class="field"><span>Dari</span><input ${attr} data-key="start" type="time" value="${esc(condition.start || '07:00')}"></label>
          <label class="field"><span>Sampai</span><input ${attr} data-key="end" type="time" value="${esc(condition.end || '22:00')}"></label>
        </div>
        <div class="field" data-cond-days="${listKey}:${index}"><span>Hari</span>${dayChecks(condition.days, 'data-cday')}</div>`;
    } else if (mode !== 'empty') {
      const hint = { ai: 'Pertanyaan ya atau tidak', regex: 'Pola, misalnya ^/jadwal\\b', equals: 'Teks' }[mode] || 'Kata kunci, pisahkan dengan koma';
      body = `<label class="field"><span>${hint}</span><textarea ${attr} data-key="value" rows="2" maxlength="1000">${esc(condition.value || '')}</textarea></label>`;
    }
    return `<div class="condition">
        <div class="condition-head"><span>${prefix}</span>
          <button class="btn btn-ghost btn-small" type="button" data-cond-remove="${listKey}" data-index="${index}">Hapus</button></div>
        <label class="field"><select ${attr} data-key="mode">${options}</select></label>
        ${body}
      </div>`;
  }

  function nodeFields(member) {
    const config = member.config || {};
    switch (kindOf(member)) {
      case 'delay':
        return `<label class="field"><span>Tunggu (detik, maksimal 3600)</span><input data-config="seconds" type="number" min="1" max="3600" value="${esc(config.seconds || 30)}"></label>
          <p class="hint">Jeda ini tidak dihitung dalam batas waktu tim.</p>`;
      case 'gate': {
        const list = conditionsOf(config);
        const combine = config.combine || 'and';
        const needsModel = list.some(c => c.mode === 'ai');
        return `<label class="field"><span>Gabungkan syarat</span><select data-config="combine">
            <option value="and" ${combine === 'and' ? 'selected' : ''}>AND, semua harus terpenuhi</option>
            <option value="or" ${combine === 'or' ? 'selected' : ''}>OR, salah satu cukup</option>
          </select></label>
          ${list.map((c, i) => conditionFields(c, i, 'conditions')).join('')}
          ${list.length < 5 ? '<button class="btn btn-ghost btn-small btn-block" type="button" data-cond-add="conditions">+ Syarat</button>' : ''}
          ${needsModel ? '<label class="field"><span>Model untuk syarat AI</span><select data-field="model_id"></select></label>' : ''}
          <p class="hint">Terpenuhi ke jalur A, tidak terpenuhi ke jalur B. Syarat tanpa AI dicek dulu, jadi AI sering tidak perlu ditanya.</p>`;
      }
      case 'switch': {
        const rules = config.rules || [];
        const needsModel = rules.some(c => c.mode === 'ai');
        return `${rules.map((c, i) => conditionFields(c, i, 'rules')).join('')}
          ${rules.length < 3 ? '<button class="btn btn-ghost btn-small btn-block" type="button" data-cond-add="rules">+ Aturan</button>' : ''}
          ${needsModel ? '<label class="field"><span>Model untuk aturan AI</span><select data-field="model_id"></select></label>' : ''}
          <p class="hint">Aturan pertama yang cocok memilih jalurnya (A, B, C). Selain itu ke jalur D.</p>`;
      }
      case 'merge': {
        const mode = config.mode || 'any';
        return `<label class="field"><span>Lanjut ketika</span><select data-config="mode">
            <option value="any" ${mode === 'any' ? 'selected' : ''}>Salah satu jalur tiba</option>
            <option value="all" ${mode === 'all' ? 'selected' : ''}>Semua jalur tiba</option>
          </select></label>
          <p class="hint">Isi dari semua jalur yang tiba digabung jadi satu.</p>`;
      }
      case 'set':
        return `<label class="field"><span>Isi teks</span><textarea data-config="text" rows="8" maxlength="8000">${esc(config.text || '')}</textarea></label>
          <p class="hint">Bisa memakai {{input}}, {{task}}, {{date}}, {{time}}, {{day}}, {{team}}, dan {{node.ID}} untuk isi node lain.</p>`;
      case 'http': {
        const method = config.method || 'GET';
        return `<div class="field-row">
            <label class="field"><span>Metode</span><select data-config="method">
              <option ${method === 'GET' ? 'selected' : ''}>GET</option><option ${method === 'POST' ? 'selected' : ''}>POST</option>
            </select></label>
          </div>
          <label class="field"><span>URL</span><input data-config="url" value="${esc(config.url || '')}" placeholder="https://..." maxlength="2000"></label>
          ${method === 'POST' ? `<label class="field"><span>Isi (JSON atau teks)</span><textarea data-config="body" rows="4" maxlength="8000">${esc(config.body || '')}</textarea></label>` : ''}
          <p class="hint">Hanya alamat publik. Balasannya diteruskan ke node berikutnya. Isi bisa memakai {{input}} dan {{task}}.</p>`;
      }
      case 'telegram': {
        const telegram = (S.integrations || {}).telegram || {};
        return `<label class="field"><span>Chat ID</span><input data-config="chat_id" value="${esc(config.chat_id || '')}" placeholder="${telegram.default_chat ? 'Memakai TELEGRAM_CHAT_ID' : 'Angka atau @nama_channel'}" maxlength="70"></label>
          <p class="hint">${telegram.ready ? 'Bot sudah tersambung.' : 'Isi TELEGRAM_BOT_TOKEN di .env dulu, lalu jalankan ulang server.'}</p>
          <button id="test-telegram" class="btn btn-ghost btn-small btn-block" type="button" ${telegram.ready ? '' : 'disabled'}>Kirim pesan tes</button>`;
      }
      case 'database': {
        const options = (S.integrations || {}).databases || [{ id: 'sqlite', label: 'SQLite (file lokal)', ready: true }];
        const chosen = options.find(o => o.id === (config.database || 'sqlite')) || options[0];
        return `<label class="field"><span>Database</span><select data-config="database">${options.map(o =>
            `<option value="${esc(o.id)}" ${o.id === chosen.id ? 'selected' : ''}>${esc(o.label)}${o.ready ? '' : ' (belum siap)'}</option>`).join('')}</select></label>
          ${chosen.reason ? `<p class="hint hint-warn">${esc(chosen.reason)}</p>` : ''}
          <label class="field"><span>Nama tabel</span><input data-config="table" value="${esc(config.table || 'hasil_tim')}" maxlength="63"></label>`;
      }
      default: return '';
    }
  }

  function renderMember() {
    const box = $('member-panel');
    if (S.panel === 'schedule') { renderSchedule(); return; }
    const member = memberById(S.selected);
    if (!member) {
      box.innerHTML = '<p class="hint">Pilih node di kanvas untuk mengaturnya, atau tambahkan dari menu Tambah.</p>';
      return;
    }
    const isAgent = kindOf(member) === 'agent';
    const isWorker = member.id !== S.lead && member.id !== S.reviewer;
    const others = S.members.filter(m => m.id !== member.id && m.id !== S.reviewer);
    const fork = branchParent(member);
    box.innerHTML = `
      <label class="field"><span>Nama</span><input data-field="name" value="${esc(member.name)}" maxlength="60"></label>
      ${isAgent ? `
        <label class="field"><span>Peran</span><input data-field="role" value="${esc(member.role)}" maxlength="120"></label>
        <label class="field"><span>Model</span><select data-field="model_id"></select></label>
        <label class="field"><span>Instruksi tambahan</span><textarea data-field="instructions" rows="4" maxlength="4000">${esc(member.instructions)}</textarea></label>
        <div class="field">
          <label class="check"><input type="radio" name="position" value="worker" ${isWorker ? 'checked' : ''}>Agent biasa</label>
          <label class="check"><input type="radio" name="position" value="lead" ${member.id === S.lead ? 'checked' : ''}>Lead, menulis jawaban akhir</label>
          <label class="check"><input type="radio" name="position" value="reviewer" ${member.id === S.reviewer ? 'checked' : ''}>Reviewer, memeriksa dan meminta revisi</label>
        </div>` : nodeFields(member)}
      ${member.id !== S.reviewer && others.length ? `<div class="field"><span>Berjalan setelah</span>
        ${others.map(o => `<label class="check"><input type="checkbox" data-after="${esc(o.id)}" ${(member.depends_on || []).includes(o.id) ? 'checked' : ''}>${esc(o.name)}</label>`).join('')}
      </div>` : ''}
      ${fork ? `<div class="field"><span>Jalur dari ${esc(fork.name)}</span>
        ${(kindOf(fork) === 'switch'
          ? [['A', 'Aturan A cocok'], ['B', 'Aturan B cocok'], ['C', 'Aturan C cocok'], ['D', 'Tidak ada yang cocok']]
          : [['A', 'Syarat terpenuhi'], ['B', 'Syarat tidak terpenuhi']]
        ).map(([value, label]) => `<label class="check"><input type="radio" name="branch" value="${value}" ${member.branch === value ? 'checked' : ''}>Jalur ${value}, ${label.toLowerCase()}</label>`).join('')}
        <label class="check"><input type="radio" name="branch" value="" ${!member.branch ? 'checked' : ''}>Semua jalur</label>
      </div>` : ''}
      <button id="remove-node" class="btn btn-ghost btn-danger btn-block" type="button">Hapus node</button>`;

    const modelSelect = box.querySelector('[data-field="model_id"]');
    if (modelSelect) fillModelOptions(modelSelect, member.model_id, false);
    box.querySelectorAll('[data-field]').forEach(input => {
      input.oninput = () => {
        member[input.dataset.field] = input.value;
        if (input.dataset.field === 'name' && kindOf(member) !== 'agent') member.role = input.value;
        const node = document.querySelector(`.node[data-id="${CSS.escape(member.id)}"]`);
        if (node) {
          node.querySelector('.node-name').textContent = member.name || 'Tanpa nama';
          node.querySelector('.node-model').textContent = nodeDetail(member);
        }
      };
    });
    box.querySelectorAll('[data-config]').forEach(input => {
      const rerenders = ['mode', 'database', 'combine', 'method'].includes(input.dataset.config);
      input[rerenders ? 'onchange' : 'oninput'] = () => {
        member.config = { ...(member.config || {}), [input.dataset.config]: input.type === 'number' ? Number(input.value) : input.value };
        if (input.dataset.config === 'mode' && input.value === 'time') member.config = { start: '07:00', end: '22:00', ...member.config };
        const node = document.querySelector(`.node[data-id="${CSS.escape(member.id)}"]`);
        if (node) node.querySelector('.node-model').textContent = nodeDetail(member);
        if (rerenders) renderMember();
      };
    });
    const refreshNode = () => {
      const node = document.querySelector(`.node[data-id="${CSS.escape(member.id)}"]`);
      if (node) node.querySelector('.node-model').textContent = nodeDetail(member);
    };
    /* Conditions are edited in place, and old single-condition gates become a list on first edit. */
    const listOf = key => {
      const config = member.config || {};
      const list = key === 'conditions' ? conditionsOf(config).map(c => ({ ...c })) : (config.rules || []).map(c => ({ ...c }));
      if (key === 'conditions') { delete config.mode; delete config.value; delete config.start; delete config.end; delete config.days; }
      return list;
    };
    const saveList = (key, list) => { member.config = { ...(member.config || {}), [key]: list }; refreshNode(); };
    box.querySelectorAll('[data-cond]').forEach(input => {
      const rerenders = input.dataset.key === 'mode';
      input[rerenders ? 'onchange' : 'oninput'] = () => {
        const key = input.dataset.cond;
        const list = listOf(key);
        const index = Number(input.dataset.index);
        list[index] = { ...list[index], [input.dataset.key]: input.value };
        if (rerenders && input.value === 'time') list[index] = { start: '07:00', end: '22:00', ...list[index] };
        saveList(key, list);
        if (rerenders) renderMember();
      };
    });
    box.querySelectorAll('[data-cond-days]').forEach(wrap => {
      const [key, index] = wrap.dataset.condDays.split(':');
      wrap.querySelectorAll('[data-cday]').forEach(check => {
        check.onchange = () => {
          const list = listOf(key);
          list[Number(index)].days = readDays(wrap, 'data-cday');
          saveList(key, list);
        };
      });
    });
    box.querySelectorAll('[data-cond-add]').forEach(button => {
      button.onclick = () => {
        const key = button.dataset.condAdd;
        saveList(key, [...listOf(key), { mode: 'contains', value: '' }]);
        renderMember();
      };
    });
    box.querySelectorAll('[data-cond-remove]').forEach(button => {
      button.onclick = () => {
        const key = button.dataset.condRemove;
        const list = listOf(key);
        if (list.length <= 1) { toast('Minimal satu syarat.'); return; }
        list.splice(Number(button.dataset.index), 1);
        saveList(key, list);
        renderMember();
      };
    });
    const testButton = $('test-telegram');
    if (testButton) {
      testButton.onclick = async () => {
        testButton.disabled = true;
        try {
          await post('/api/teams/telegram/test', { chat_id: (member.config || {}).chat_id || '' });
          toast('Pesan tes terkirim.');
        } catch (e) { toast(e.message); }
        testButton.disabled = false;
      };
    }
    box.querySelectorAll('input[name="position"]').forEach(radio => {
      radio.onchange = () => {
        if (S.lead === member.id) S.lead = null;
        if (S.reviewer === member.id) S.reviewer = null;
        if (radio.value === 'lead') S.lead = member.id;
        if (radio.value === 'reviewer') { S.reviewer = member.id; member.depends_on = []; }
        if (!S.lead) S.lead = defaultLead();
        if (S.lead === S.reviewer) S.lead = S.members.find(m => m.id !== S.reviewer && kindOf(m) === 'agent')?.id || null;
        renderBoard();
        renderMember();
      };
    });
    box.querySelectorAll('[data-after]').forEach(check => {
      check.onchange = () => {
        const set = new Set(member.depends_on || []);
        if (check.checked) set.add(check.dataset.after); else set.delete(check.dataset.after);
        member.depends_on = [...set];
        if (!branchParent(member)) member.branch = '';
        renderBoard();
        renderMember();
      };
    });
    box.querySelectorAll('input[name="branch"]').forEach(radio => {
      radio.onchange = () => { member.branch = radio.value; renderBoard(); };
    });
    $('remove-node').onclick = () => {
      if (S.members.length <= 2) { toast('Tim butuh minimal 2 node.'); return; }
      S.members = S.members.filter(m => m.id !== member.id);
      S.members.forEach(m => {
        m.depends_on = (m.depends_on || []).filter(id => id !== member.id);
        if (!branchParent(m)) m.branch = '';
      });
      if (S.reviewer === member.id) S.reviewer = null;
      if (S.lead === member.id) S.lead = defaultLead();
      S.selected = null;
      renderBoard();
      renderMember();
    };
  }

  function scheduleLabel() {
    const schedule = S.schedule || { mode: 'off' };
    if (schedule.mode === 'interval') return `Jadwal: tiap ${schedule.minutes || '?'} menit`;
    if (schedule.mode === 'daily') return `Jadwal: ${(schedule.times || [schedule.time || '--:--']).join(', ')}`;
    if (schedule.mode === 'cron') return `Cron: ${schedule.cron || '?'}`;
    if (schedule.mode === 'once') return `Sekali: ${(schedule.at || '').replace('T', ' ')}`;
    if (schedule.mode === 'telegram') return `Pesan Telegram${schedule.keyword ? ` ${schedule.keyword}` : ''}`;
    return 'Jadwal';
  }

  function renderSchedule() {
    const box = $('member-panel');
    const schedule = S.schedule || { mode: 'off' };
    const mode = schedule.mode || 'off';
    const telegram = (S.integrations || {}).telegram || {};
    const option = (value, label) => `<option value="${value}" ${mode === value ? 'selected' : ''}>${label}</option>`;
    const times = (schedule.times || [schedule.time || '07:00']).join(', ');
    box.innerHTML = `
      <h3 class="panel-title">Kapan alur ini berjalan</h3>
      <label class="field"><span>Pemicu</span><select id="schedule-mode">
        ${option('off', 'Manual, saat tombol Jalankan ditekan')}${option('interval', 'Berulang tiap beberapa menit')}
        ${option('daily', 'Jam tertentu, bisa beberapa kali sehari')}${option('cron', 'Cron, untuk pola rumit')}
        ${option('once', 'Sekali, pada tanggal dan jam tertentu')}${option('telegram', 'Saat ada pesan Telegram')}
      </select></label>
      ${mode === 'interval' ? `<label class="field"><span>Tiap (menit, minimal 5)</span><input id="schedule-minutes" type="number" min="5" value="${esc(schedule.minutes || 60)}"></label>` : ''}
      ${mode === 'daily' ? `<label class="field"><span>Jam, pisahkan dengan koma</span><input id="schedule-time" value="${esc(times)}" placeholder="05:00, 12:00, 17:00"></label>
        <div class="field"><span>Hari</span>${dayChecks(schedule.days, 'data-schedule-day')}</div>` : ''}
      ${mode === 'cron' ? `<label class="field"><span>Ekspresi cron (menit jam tanggal bulan hari)</span><input id="schedule-cron" value="${esc(schedule.cron || '0 7 * * 1-5')}" maxlength="100"></label>
        <p class="hint">Contoh: <code>0 7 * * 1-5</code> jam 07:00 Senin sampai Jumat, <code>*/30 8-17 * * *</code> tiap 30 menit di jam kerja. Hari 0 dan 7 = Minggu.</p>` : ''}
      ${mode === 'once' ? `<label class="field"><span>Tanggal dan jam</span><input id="schedule-at" type="datetime-local" value="${esc((schedule.at || '').slice(0, 16))}"></label>` : ''}
      ${mode === 'telegram' ? `<label class="field"><span>Kata pemicu (kosongkan untuk semua pesan)</span><input id="schedule-keyword" value="${esc(schedule.keyword || '')}" placeholder="/jadwal" maxlength="33"></label>
        <p class="hint ${telegram.ready && telegram.default_chat ? '' : 'hint-warn'}">${telegram.ready && telegram.default_chat
          ? 'Hanya pesan dari TELEGRAM_CHAT_ID yang menjalankan alur. Isi pesan menjadi tugasnya, dan node Telegram tanpa Chat ID membalas ke chat itu.'
          : 'Isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID di .env dulu. Tanpa chat yang diizinkan, pesan diabaikan.'}</p>` : ''}
      ${mode !== 'off' ? `<label class="field"><span>${mode === 'telegram' ? 'Konteks tambahan (boleh kosong)' : 'Tugas yang dijalankan'}</span><textarea id="schedule-task" rows="4" maxlength="4000" placeholder="Misalnya: apa yang penting hari ini di Jakarta?">${esc(schedule.task || '')}</textarea></label>
        <p class="hint">Hanya berjalan selama server SquidWings menyala. Jam mengikuti waktu mesin ini. Tekan Simpan agar berlaku.</p>` : ''}
      <button id="schedule-close" class="btn btn-ghost btn-block" type="button">Selesai</button>`;
    const value = id => ($(id) ? $(id).value : undefined);
    const update = () => {
      const timeText = value('schedule-time');
      S.schedule = {
        mode: $('schedule-mode').value,
        minutes: $('schedule-minutes') ? Number($('schedule-minutes').value) : S.schedule.minutes,
        times: timeText !== undefined ? timeText.split(',').map(t => t.trim()).filter(Boolean) : S.schedule.times,
        days: box.querySelector('[data-schedule-day]') ? readDays(box, 'data-schedule-day') : S.schedule.days,
        cron: value('schedule-cron') ?? S.schedule.cron,
        at: value('schedule-at') ?? S.schedule.at,
        keyword: value('schedule-keyword') ?? S.schedule.keyword,
        task: value('schedule-task') ?? S.schedule.task,
      };
      if (S.schedule.times) S.schedule.time = S.schedule.times[0];
      $('flow-schedule').textContent = scheduleLabel();
    };
    $('schedule-mode').onchange = () => { update(); renderSchedule(); };
    box.querySelectorAll('input, textarea').forEach(input => { input.oninput = update; input.onchange = update; });
    $('schedule-close').onclick = () => { update(); S.panel = null; renderMember(); };
  }
  $('flow-schedule').onclick = () => {
    S.panel = 'schedule';
    S.selected = null;
    document.querySelectorAll('.node.selected').forEach(n => n.classList.remove('selected'));
    renderMember();
  };
  $('tidy-board').onclick = () => {
    S.members.forEach(member => {
      delete member.x;
      delete member.y;
      if (S.team) store.set(posKey(S.team, member.id), '');
    });
    autoLayout(S.team);
    renderBoard();
  };

  function renderTeamOptions() {
    $('team-select').innerHTML = `<option value="">Tim baru</option>${S.teams.map(t =>
      `<option value="${esc(t.id)}" ${t.id === S.team ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}`;
  }

  function blankTeam() {
    stopEvents();
    S.run = null;
    S.team = null;
    S.reviewer = null;
    const model = S.model !== 'auto' && S.byId[S.model] ? S.model : (S.models[0] && S.models[0].id) || '';
    S.members = [
      { id: newId(), name: 'Lead', role: 'Menyusun jawaban akhir', model_id: model, instructions: '', depends_on: [] },
      { id: newId(), name: 'Peneliti', role: 'Mencari fakta dan sumber', model_id: model, instructions: '', depends_on: [] },
    ];
    S.lead = S.members[0].id;
    S.selected = null;
    S.panel = null;
    S.schedule = { mode: 'off' };
    $('team-video').checked = false;
    $('flow-schedule').textContent = scheduleLabel();
    $('team-name').value = '';
    resetRunView();
    autoLayout(null);
    renderTeamOptions();
    renderBoard();
    renderMember();
  }

  function openTeam(id) {
    const team = S.teams.find(t => t.id === id);
    if (!team) { blankTeam(); return; }
    stopEvents();
    S.run = null;
    S.team = team.id;
    S.lead = team.lead_id || null;
    S.reviewer = team.reviewer_id || null;
    S.selected = null;
    S.panel = null;
    S.schedule = team.schedule || { mode: 'off' };
    $('team-video').checked = team.mode === 'video';
    $('flow-schedule').textContent = scheduleLabel();
    S.members = team.members.map(m => ({ ...m, depends_on: m.depends_on || [] }));
    $('team-name').value = team.name;
    resetRunView();
    autoLayout(team.id);
    renderTeamOptions();
    renderBoard();
    renderMember();
    showLatestRun(team.id);
  }

  /* A scheduled run starts with nobody watching, so opening the team shows it. */
  async function showLatestRun(teamId) {
    try {
      const { runs } = await api(`/api/teams/${teamId}/runs`);
      if (S.team !== teamId || !runs.length) return;
      const latest = runs.reduce((a, b) => (b.created_at > a.created_at ? b : a));
      showRun(latest);
      if (!finished(latest.status)) watchRun(latest);
    } catch { /* the board still works without run history */ }
  }

  function useTemplate(key) {
    const template = S.templates.find(t => t.key === key);
    if (!template) return;
    stopEvents();
    S.run = null;
    S.team = null;
    S.lead = template.lead_id || null;
    S.reviewer = template.reviewer_id || null;
    S.selected = null;
    S.panel = null;
    S.schedule = template.schedule || { mode: 'off' };
    $('team-video').checked = template.mode === 'video';
    $('flow-schedule').textContent = scheduleLabel();
    S.members = template.members.map(m => ({ ...m, depends_on: m.depends_on || [] }));
    $('team-name').value = template.name;
    resetRunView();
    autoLayout(null);
    renderTeamOptions();
    renderBoard();
    renderMember();
    toast(template.description);
  }

  async function loadTeams() {
    if (S.features.teams === false) return;
    try {
      const [teams, templates, integrations] = await Promise.all([api('/api/teams'), api('/api/teams/templates'), api('/api/teams/integrations')]);
      S.teams = teams.teams;
      S.templates = templates.templates;
      S.integrations = integrations;
      $('template-select').innerHTML = `<option value="">Template</option>${S.templates.map(t =>
        `<option value="${esc(t.key)}">${esc(t.name)}</option>`).join('')}`;
      renderTeamOptions();
      if (S.team === null && !S.members.length) blankTeam();
    } catch (e) { toast(e.message); }
  }
  $('team-select').onchange = e => openTeam(e.target.value);
  $('template-select').onchange = e => { useTemplate(e.target.value); e.target.value = ''; };
  const NODE_DEFAULTS = {
    agent: { name: 'Agent', role: 'Spesialis', config: {} },
    delay: { name: 'Jeda', config: { seconds: 30 } },
    gate: { name: 'Cek syarat', config: { combine: 'and', conditions: [{ mode: 'contains', value: '' }] } },
    switch: { name: 'Pilah jalur', config: { rules: [{ mode: 'contains', value: '' }, { mode: 'contains', value: '' }] } },
    merge: { name: 'Gabung jalur', config: { mode: 'any' } },
    set: { name: 'Teks', config: { text: 'Hari ini {{day}}, {{date}} jam {{time}}.\n\n{{input}}' } },
    http: { name: 'Permintaan HTTP', config: { method: 'GET', url: '', body: '' } },
    telegram: { name: 'Kirim ke Telegram', config: { chat_id: '' } },
    database: { name: 'Simpan ke database', config: { database: 'sqlite', table: 'hasil_tim' } },
  };
  $('add-node').onchange = e => {
    const kind = e.target.value;
    e.target.value = '';
    if (!kind) return;
    if (S.members.length >= 12) { toast('Maksimal 12 node.'); return; }
    if (kind === 'agent' && S.members.filter(m => kindOf(m) === 'agent').length >= 6) { toast('Maksimal 6 agent.'); return; }
    const preset = NODE_DEFAULTS[kind];
    const model = (S.members.find(m => m.model_id) || {}).model_id || (S.models[0] && S.models[0].id) || '';
    // Connected to the selected node, because a new node is usually the next step.
    const parent = memberById(S.selected);
    const member = {
      id: newId(), kind, name: kind === 'agent' ? `Agent ${S.members.length + 1}` : preset.name, role: preset.role || preset.name,
      model_id: model, instructions: '', branch: '', config: { ...preset.config },
      depends_on: parent && parent.id !== S.reviewer ? [parent.id] : [],
    };
    S.members.push(member);
    S.selected = member.id;
    S.panel = null;
    autoLayout(S.team);
    renderBoard();
    renderMember();
  };

  async function saveTeam() {
    const name = $('team-name').value.trim();
    if (!name) { $('team-name').focus(); throw new Error('Beri nama tim dulu.'); }
    const body = {
      name,
      description: '',
      members: S.members.map(({ id, name: memberName, role, model_id, instructions, depends_on, kind, branch, config }) =>
        ({ id, name: memberName, role: role || memberName, model_id: model_id || '', instructions: instructions || '', depends_on: depends_on || [], kind: kind || 'agent', branch: branch || '', config: config || {} })),
      lead_id: S.lead,
      reviewer_id: S.reviewer || null,
      schedule: S.schedule || { mode: 'off' },
      mode: $('team-video').checked ? 'video' : 'standard',
    };
    const saved = S.team
      ? await api(`/api/teams/${S.team}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      : await post('/api/teams', body);
    S.team = saved.id;
    S.members.forEach(m => store.set(posKey(saved.id, m.id), `${m.x},${m.y}`));
    S.teams = (await api('/api/teams')).teams;
    renderTeamOptions();
    return saved;
  }
  $('save-team').onclick = () => saveTeam().then(() => toast('Tersimpan.')).catch(e => toast(e.message));
  $('delete-team').onclick = async () => {
    if (!S.team) { blankTeam(); return; }
    if (!confirm('Hapus tim ini beserta riwayatnya?')) return;
    try {
      await api(`/api/teams/${S.team}`, { method: 'DELETE' });
      S.teams = S.teams.filter(t => t.id !== S.team);
      blankTeam();
    } catch (e) { toast(e.message); }
  };

  function resetRunView() {
    $('result').classList.add('hidden');
    $('run-info').textContent = '';
    $('cancel').classList.add('hidden');
    $('run').disabled = false;
  }

  function paintRun() {
    document.querySelectorAll('.node').forEach(node => {
      const agent = S.run && S.run.agents ? S.run.agents[node.dataset.id] : null;
      node.dataset.status = agent ? agent.status : '';
      const labels = { queued: 'Menunggu', running: 'Berjalan', completed: 'Selesai', failed: 'Gagal', cancelled: 'Dibatalkan', skipped: 'Dilewati' };
      const state = node.querySelector('.node-state');
      if (!agent) { state.textContent = ''; return; }
      const bits = [labels[agent.status] || agent.status];
      if (agent.status === 'running' && agent.activity) {
        const { verb, target } = describeTool(agent.activity.name, agent.activity.detail);
        bits[0] = `${verb} ${target}`.trim();
      }
      if (agent.branch && agent.status === 'completed') bits.push(`Jalur ${agent.branch}`);
      if (agent.note && agent.status === 'completed' && !agent.branch) bits[0] = agent.note;
      node.title = agent.note || '';
      if (agent.completed_at && agent.started_at && agent.status !== 'running') bits.push(formatSeconds((agent.completed_at - agent.started_at) * 1000));
      if (agent.tokens) bits.push(`${formatTokens(agent.tokens)} token`);
      state.textContent = bits.join(' · ') + (agent.error ? `: ${agent.error}` : '');
    });
    if ($('edge-lines').childElementCount) edges();
  }

  function showRun(run) {
    S.run = run;
    paintRun();
    const active = !finished(run.status);
    const labels = { queued: 'Menunggu', running: 'Berjalan', synthesizing: 'Lead menyusun jawaban', reviewing: 'Reviewer memeriksa', revising: 'Lead merevisi', delivering: 'Mengirim hasil', completed: 'Selesai', failed: 'Gagal', cancelled: 'Dibatalkan' };
    const reviews = (run.reviews || []).map(r => r.verdict).join(', ');
    const elapsed = run.started_at ? ` · ${formatSeconds(((run.completed_at || Date.now() / 1000) - run.started_at) * 1000)}` : '';
    $('run-info').textContent = `${labels[run.status] || run.status}${elapsed} · ${formatTokens(run.total_tokens || 0)} token${reviews ? ` · review: ${reviews}` : ''}`;
    $('cancel').classList.toggle('hidden', !active);
    $('run').disabled = active;
    const result = $('result');
    if (run.result || run.error) {
      result.classList.remove('hidden');
      result.innerHTML = run.error ? `<div class="notice">${esc(run.error)}</div>` : md(run.result);
      enhanceCode(result);
    } else result.classList.add('hidden');
  }

  function stopEvents() {
    if (S.events) { S.events.close(); S.events = null; }
  }

  function watchRun(run) {
    stopEvents();
    S.events = new EventSource(`/api/teams/runs/${run.id}/events`);
    S.events.onmessage = event => {
      const data = JSON.parse(event.data);
      if (!S.run || data.run.id !== S.run.id) return;
      showRun(data.run);
      if (data.type === 'run_finished') stopEvents();
    };
    S.events.onerror = () => { if (S.run && finished(S.run.status)) stopEvents(); };
  }

  $('run-form').onsubmit = async e => {
    e.preventDefault();
    const task = $('task').value.trim();
    if (!task) { $('task').focus(); return; }
    $('run').disabled = true;
    try {
      await saveTeam();
      const run = await post(`/api/teams/${S.team}/runs`, { task });
      showRun(run);
      watchRun(run);
    } catch (err) {
      toast(err.message);
      $('run').disabled = false;
    }
  };
  $('cancel').onclick = async () => {
    if (!S.run) return;
    try { showRun(await api(`/api/teams/runs/${S.run.id}/cancel`, { method: 'POST' })); } catch (e) { toast(e.message); }
  };

  /* ── Start ────────────────────────────────────────── */
  (async () => {
    try {
      const config = await api('/api/config').catch(() => ({ features: {} }));
      S.features = config.features || {};
      $('nav-team').classList.toggle('hidden', S.features.teams === false);
      await loadModels();
      await loadSessions();
      const last = store.get('session');
      if (last && S.sessions.some(s => s.session_id === last)) await openSession(last);
      else await newChat();
      if (store.get('view') === 'team') showView('team');
    } catch (e) {
      toast(e.message);
    }
  })();
})();
