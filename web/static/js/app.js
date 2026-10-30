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

  function enhanceCode(container) {
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
    load_skill: ['Membuka panduan', 'read', 'plain'], vault_search: ['Mencari catatan', 'search', 'quote'],
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
  const ROW_GAP = 112;
  const newId = () => `a${Math.random().toString(36).slice(2, 9)}`;
  const posKey = (team, member) => `pos:${team}:${member}`;
  const finished = status => ['completed', 'failed', 'cancelled'].includes(status);

  function depth(member, seen = new Set()) {
    if (seen.has(member.id)) return 0;
    seen.add(member.id);
    const parents = (member.depends_on || []).map(id => S.members.find(m => m.id === id)).filter(Boolean);
    return parents.length ? 1 + Math.max(...parents.map(p => depth(p, new Set(seen)))) : 0;
  }

  function autoLayout(teamId) {
    const rows = {};
    const workers = S.members.filter(m => m.id !== S.lead && m.id !== S.reviewer);
    const lastWorker = workers.length ? Math.max(...workers.map(m => depth(m))) : -1;
    const rowOf = member => {
      if (member.id === S.lead) return lastWorker + 1;
      if (member.id === S.reviewer) return lastWorker + 2;
      return depth(member);
    };
    S.members.forEach(member => { (rows[rowOf(member)] ||= []).push(member); });
    const widest = Math.max(...Object.values(rows).map(list => list.length));
    S.members.forEach(member => {
      const saved = teamId && store.get(posKey(teamId, member.id));
      if (saved) {
        const [x, y] = saved.split(',').map(Number);
        member.x = x;
        member.y = y;
        return;
      }
      if (member.x !== undefined) return;
      const row = rowOf(member);
      const list = rows[row];
      // Centre each row over the widest one, so the flow reads straight down.
      const offset = ((widest - list.length) * COLUMN_GAP) / 2;
      member.x = 28 + offset + list.indexOf(member) * COLUMN_GAP;
      member.y = 28 + row * ROW_GAP;
    });
  }

  function edges() {
    const lines = [];
    const statusOf = member => (S.run && S.run.agents && S.run.agents[member.id] ? S.run.agents[member.id].status : '');
    const add = (from, to, cls = '') => {
      if (!from || !to) return;
      const x1 = from.x + NODE_W / 2;
      const y1 = from.y + NODE_H;
      const x2 = to.x + NODE_W / 2;
      const y2 = to.y;
      const bend = Math.max(18, Math.abs(y2 - y1) / 2);
      const active = statusOf(to) === 'running' ? ' active' : '';
      lines.push(`<path class="edge ${cls}${active}" d="M${x1} ${y1}C${x1} ${y1 + bend} ${x2} ${y2 - bend} ${x2} ${y2}" marker-end="url(#arrow)"/>`);
    };
    const lead = S.members.find(m => m.id === S.lead);
    const reviewer = S.members.find(m => m.id === S.reviewer);
    const workers = S.members.filter(m => m !== lead && m !== reviewer);
    const dependedOn = new Set(workers.flatMap(m => m.depends_on || []));
    workers.forEach(member => {
      (member.depends_on || []).forEach(id => add(S.members.find(m => m.id === id), member));
      if (!dependedOn.has(member.id)) add(member, lead);
    });
    if (reviewer) add(lead, reviewer, 'review');
    $('edge-lines').innerHTML = lines.join('');
  }

  function roleLabel(member) {
    if (member.id === S.lead) return 'Lead';
    if (member.id === S.reviewer) return 'Reviewer';
    return 'Agent';
  }

  function renderBoard() {
    $('nodes').innerHTML = S.members.map(member => `
      <div class="node ${member.id === S.selected ? 'selected' : ''}" data-id="${esc(member.id)}" style="left:${member.x}px;top:${member.y}px" tabindex="0">
        <div class="node-role">${roleLabel(member)}</div>
        <div class="node-name">${esc(member.name || 'Tanpa nama')}</div>
        <div class="node-model">${esc(shortModel(member.model_id))}</div>
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
      document.querySelectorAll('.node').forEach(n => n.classList.toggle('selected', n === el));
      renderMember();
    }
    el.setPointerCapture(event.pointerId);
    el.onpointermove = e => {
      member.x = Math.max(0, Math.min(1600 - NODE_W, e.clientX - offsetX));
      member.y = Math.max(0, Math.min(1400 - NODE_H, e.clientY - offsetY));
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

  function renderMember() {
    const box = $('member-panel');
    const member = S.members.find(m => m.id === S.selected);
    if (!member) {
      box.innerHTML = '<p class="hint">Pilih agent di kanvas untuk mengaturnya.</p>';
      return;
    }
    const others = S.members.filter(m => m.id !== member.id && m.id !== S.lead && m.id !== S.reviewer);
    const isWorker = member.id !== S.lead && member.id !== S.reviewer;
    box.innerHTML = `
      <label class="field"><span>Nama</span><input data-field="name" value="${esc(member.name)}" maxlength="60"></label>
      <label class="field"><span>Peran</span><input data-field="role" value="${esc(member.role)}" maxlength="120"></label>
      <label class="field"><span>Model</span><select data-field="model_id"></select></label>
      <label class="field"><span>Instruksi tambahan</span><textarea data-field="instructions" rows="4" maxlength="4000">${esc(member.instructions)}</textarea></label>
      <div class="field">
        <label class="check"><input type="radio" name="position" value="worker" ${isWorker ? 'checked' : ''}>Agent biasa</label>
        <label class="check"><input type="radio" name="position" value="lead" ${member.id === S.lead ? 'checked' : ''}>Lead, menulis jawaban akhir</label>
        <label class="check"><input type="radio" name="position" value="reviewer" ${member.id === S.reviewer ? 'checked' : ''}>Reviewer, memeriksa dan meminta revisi</label>
      </div>
      ${isWorker && others.length ? `<div class="field"><span>Berjalan setelah</span>
        ${others.map(o => `<label class="check"><input type="checkbox" data-after="${esc(o.id)}" ${(member.depends_on || []).includes(o.id) ? 'checked' : ''}>${esc(o.name)}</label>`).join('')}
      </div>` : ''}
      <button id="remove-node" class="btn btn-ghost btn-danger btn-block" type="button">Hapus agent</button>`;

    fillModelOptions(box.querySelector('[data-field="model_id"]'), member.model_id, false);
    box.querySelectorAll('[data-field]').forEach(input => {
      input.oninput = () => {
        member[input.dataset.field] = input.value;
        const node = document.querySelector(`.node[data-id="${CSS.escape(member.id)}"]`);
        if (node) {
          node.querySelector('.node-name').textContent = member.name || 'Tanpa nama';
          node.querySelector('.node-model').textContent = shortModel(member.model_id);
        }
      };
    });
    box.querySelectorAll('input[name="position"]').forEach(radio => {
      radio.onchange = () => {
        if (S.lead === member.id) S.lead = null;
        if (S.reviewer === member.id) S.reviewer = null;
        if (radio.value === 'lead') { S.lead = member.id; member.depends_on = []; }
        if (radio.value === 'reviewer') { S.reviewer = member.id; member.depends_on = []; }
        if (!S.lead) S.lead = (S.members.find(m => m.id !== S.reviewer) || member).id;
        renderBoard();
        renderMember();
      };
    });
    box.querySelectorAll('[data-after]').forEach(check => {
      check.onchange = () => {
        const set = new Set(member.depends_on || []);
        if (check.checked) set.add(check.dataset.after); else set.delete(check.dataset.after);
        member.depends_on = [...set];
        edges();
      };
    });
    $('remove-node').onclick = () => {
      if (S.members.length <= 2) { toast('Tim butuh minimal 2 agent.'); return; }
      S.members = S.members.filter(m => m.id !== member.id);
      S.members.forEach(m => { m.depends_on = (m.depends_on || []).filter(id => id !== member.id); });
      if (S.reviewer === member.id) S.reviewer = null;
      if (S.lead === member.id) S.lead = S.members[0].id;
      S.selected = null;
      renderBoard();
      renderMember();
    };
  }

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
    S.lead = team.lead_id;
    S.reviewer = team.reviewer_id || null;
    S.selected = null;
    S.members = team.members.map(m => ({ ...m, depends_on: m.depends_on || [] }));
    $('team-name').value = team.name;
    resetRunView();
    autoLayout(team.id);
    renderTeamOptions();
    renderBoard();
    renderMember();
  }

  function useTemplate(key) {
    const template = S.templates.find(t => t.key === key);
    if (!template) return;
    stopEvents();
    S.run = null;
    S.team = null;
    S.lead = template.lead_id;
    S.reviewer = template.reviewer_id || null;
    S.selected = null;
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
      const [teams, templates] = await Promise.all([api('/api/teams'), api('/api/teams/templates')]);
      S.teams = teams.teams;
      S.templates = templates.templates;
      $('template-select').innerHTML = `<option value="">Template</option>${S.templates.map(t =>
        `<option value="${esc(t.key)}">${esc(t.name)}</option>`).join('')}`;
      renderTeamOptions();
      if (S.team === null && !S.members.length) blankTeam();
    } catch (e) { toast(e.message); }
  }
  $('team-select').onchange = e => openTeam(e.target.value);
  $('template-select').onchange = e => { useTemplate(e.target.value); e.target.value = ''; };
  $('add-node').onclick = () => {
    if (S.members.length >= 6) { toast('Maksimal 6 agent.'); return; }
    const model = S.members[0] ? S.members[0].model_id : '';
    const member = { id: newId(), name: `Agent ${S.members.length + 1}`, role: 'Spesialis', model_id: model, instructions: '', depends_on: [] };
    S.members.push(member);
    S.selected = member.id;
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
      members: S.members.map(({ id, name: memberName, role, model_id, instructions, depends_on }) =>
        ({ id, name: memberName, role, model_id, instructions: instructions || '', depends_on: depends_on || [] })),
      lead_id: S.lead,
      reviewer_id: S.reviewer || null,
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
      const labels = { queued: 'Menunggu', running: 'Berjalan', completed: 'Selesai', failed: 'Gagal', cancelled: 'Dibatalkan' };
      const state = node.querySelector('.node-state');
      state.textContent = agent ? (labels[agent.status] || agent.status) + (agent.error ? `: ${agent.error}` : '') : '';
    });
    if ($('edge-lines').childElementCount) edges();
  }

  function showRun(run) {
    S.run = run;
    paintRun();
    const active = !finished(run.status);
    const labels = { queued: 'Menunggu', running: 'Berjalan', synthesizing: 'Lead menyusun jawaban', reviewing: 'Reviewer memeriksa', revising: 'Lead merevisi', completed: 'Selesai', failed: 'Gagal', cancelled: 'Dibatalkan' };
    const reviews = (run.reviews || []).map(r => r.verdict).join(', ');
    $('run-info').textContent = `${labels[run.status] || run.status} · ${formatTokens(run.total_tokens || 0)} token${reviews ? ` · review: ${reviews}` : ''}`;
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

  $('run-form').onsubmit = async e => {
    e.preventDefault();
    const task = $('task').value.trim();
    if (!task) { $('task').focus(); return; }
    $('run').disabled = true;
    try {
      await saveTeam();
      const run = await post(`/api/teams/${S.team}/runs`, { task });
      showRun(run);
      stopEvents();
      S.events = new EventSource(`/api/teams/runs/${run.id}/events`);
      S.events.onmessage = event => {
        const data = JSON.parse(event.data);
        if (!S.run || data.run.id !== S.run.id) return;
        showRun(data.run);
        if (data.type === 'run_finished') stopEvents();
      };
      S.events.onerror = () => { if (S.run && finished(S.run.status)) stopEvents(); };
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
