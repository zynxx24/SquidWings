(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = v => { const d = document.createElement('div'); d.textContent = String(v ?? ''); return d.innerHTML; };
  const md = t => window.marked ? window.marked.parse(t || '') : esc(t).replace(/\n/g, '<br>');
  const done = s => ['completed', 'failed', 'cancelled'].includes(s);

  const S = {
    models: [], byId: {}, current: null, session: null, ws: null,
    attachments: [], busy: false, features: {},
    teams: [], team: null, members: [], lead: null, selected: null, run: null, events: null,
  };

  function toast(msg) {
    const t = $('toast'); t.textContent = msg; t.classList.remove('hidden');
    clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add('hidden'), 3500);
  }

  /* ---------- Layout (mobile drawers & desktop collapse) ---------- */
  function closeDrawers() {
    document.querySelectorAll('.team-panel, .inspector-panel, .session-panel').forEach(p => p.classList.remove('open'));
    $('scrim').classList.add('hidden');
  }
  function setDrawer(which, open) {
    const panel = which ? $(which + '-panel') : null;
    if (panel) panel.classList.toggle('open', open);
    $('scrim').classList.toggle('hidden', !document.querySelector('.team-panel.open, .inspector-panel.open, .session-panel.open'));
  }

  function toggleDesktopSidebar() {
    if (window.innerWidth < 1024) {
      setDrawer('session', !$('session-panel').classList.contains('open'));
    } else {
      const panel = $('session-panel');
      const isCollapsed = panel.classList.toggle('desktop-collapsed');
      localStorage.setItem('sidebar_collapsed', isCollapsed ? '1' : '0');
    }
  }

  $('scrim').onclick = closeDrawers;
  if ($('toggle-sidebar')) $('toggle-sidebar').onclick = toggleDesktopSidebar;
  $('open-teams').onclick = () => setDrawer('team', true);
  $('close-teams').onclick = closeDrawers;
  $('close-inspector').onclick = closeDrawers;
  $('open-sessions').onclick = () => setDrawer('session', true);
  $('close-sessions').onclick = closeDrawers;
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeDrawers();
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
      e.preventDefault();
      toggleDesktopSidebar();
    }
  });

  async function j(url, opts = {}) {
    const r = await fetch(url, opts);
    let d = {};
    try { d = await r.json(); } catch (_) { /* no body */ }
    if (!r.ok) throw new Error(Array.isArray(d.detail) ? d.detail.map(e => e.msg).join(', ') : (d.detail || `Request failed (${r.status})`));
    return d;
  }
  const post = (url, body) => j(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  /* ---------- Tabs ---------- */
  function tabAvailable(name) {
    const btn = document.querySelector(`.tab[data-tab="${name}"]`);
    if (!btn) return false;
    return !btn.dataset.feature || !S.features || S.features[btn.dataset.feature] !== false;
  }

  function showTab(name) {
    closeDrawers();
    if (!tabAvailable(name)) name = 'chat';
    document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
    $('view-' + name).classList.remove('hidden');
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    localStorage.setItem('tab', name);
    if (name === 'canvas') loadTeams();
  }
  document.querySelectorAll('.tab').forEach(b => b.onclick = () => showTab(b.dataset.tab));

  /* ---------- Features ---------- */
  function applyFeatures(features) {
    S.features = features || {};
    document.querySelectorAll('.tab[data-feature]').forEach(b => {
      const on = S.features[b.dataset.feature] !== false;
      b.classList.toggle('hidden', !on);
    });
    const canvas = $('view-canvas');
    if (canvas && S.features.teams === false) canvas.classList.add('hidden');
  }

  /* ---------- Models ---------- */
  async function loadModels(refresh = false) {
    const data = refresh ? (await post('/api/models/refresh', {}), await j('/api/models')) : await j('/api/models');
    S.models = []; S.byId = {};
    const sel = $('model-select'); sel.innerHTML = '';
    for (const [provider, list] of Object.entries(data.models)) {
      const g = document.createElement('optgroup'); g.label = provider;
      for (const m of list) {
        S.models.push(m); S.byId[m.id] = m;
        const o = document.createElement('option'); o.value = m.id; o.textContent = `${m.display_name} (${m.context_window_display})`;
        g.appendChild(o);
      }
      sel.appendChild(g);
    }
    const saved = localStorage.getItem('model');
    S.current = S.byId[saved] ? saved : data.default_model;
    sel.value = S.current;
    if (S.session) await post('/api/model/select', { session_id: S.session, model_id: S.current }).catch(() => {});
    updateHint();
    if (S.team !== null) renderInspector();
    toast(`${data.total} models loaded`);
  }

  $('model-select').onchange = async e => {
    S.current = e.target.value; localStorage.setItem('model', S.current);
    try { await post('/api/model/select', { session_id: S.session, model_id: S.current }); } catch (err) { toast(err.message); }
    updateHint();
  };
  $('refresh-models').onclick = () => loadModels(true).catch(e => toast(e.message));

  function updateHint() {
    const m = S.byId[S.current];
    const native = m ? m.uploads.filter(u => u !== 'text') : [];
    $('upload-hint').textContent = 'Documents become Markdown. Audio and video are transcribed. Images are sent to vision models.'
      + (native.length ? ` Natively supports: ${native.join(', ')}.` : '');
  }

  /* ---------- Attachments ---------- */
  function renderChips() {
    $('chips').innerHTML = S.attachments.map((a, i) =>
      `<span class="chip">${esc(a.name)} · ${a.chars.toLocaleString()} chars${a.truncated ? ' (cut)' : ''}<button data-rm="${i}">×</button></span>`).join('');
    document.querySelectorAll('[data-rm]').forEach(b => b.onclick = () => { S.attachments.splice(+b.dataset.rm, 1); renderChips(); });
  }
  $('file-input').onchange = async e => {
    for (const f of e.target.files) {
      try {
        const r = await fetch('/api/upload?name=' + encodeURIComponent(f.name), { method: 'POST', body: f });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'Upload failed');
        S.attachments.push(d);
      } catch (err) { toast(`${f.name}: ${err.message}`); }
    }
    e.target.value = ''; renderChips();
  };

  /* ---------- Code Blocks Enhancement ---------- */
  function enhanceCodeBlocks(container) {
    if (!container) return;
    container.querySelectorAll('pre code').forEach(codeBlock => {
      if (window.hljs) window.hljs.highlightElement(codeBlock);
      const pre = codeBlock.parentElement;
      if (pre && !pre.closest('.code-wrapper')) {
        const wrapper = document.createElement('div');
        wrapper.className = 'code-wrapper';
        const langMatch = (codeBlock.className || '').match(/language-([a-z0-9_-]+)/i);
        const lang = langMatch ? langMatch[1] : 'code';
        const header = document.createElement('div');
        header.className = 'code-header';
        header.innerHTML = `<span>${esc(lang)}</span><button class="code-copy-btn" type="button">Copy</button>`;
        header.querySelector('.code-copy-btn').onclick = async (e) => {
          try {
            await navigator.clipboard.writeText(codeBlock.innerText);
            e.target.textContent = 'Copied!';
            setTimeout(() => { e.target.textContent = 'Copy'; }, 2000);
          } catch (_) {}
        };
        pre.parentNode.insertBefore(wrapper, pre);
        wrapper.appendChild(header);
        wrapper.appendChild(pre);
      }
    });
  }

  /* ---------- Chat UI ---------- */
  function addMsg(role) {
    $('empty').classList.add('hidden');
    const el = document.createElement('div');
    el.className = 'msg ' + (role === 'user' ? 'msg-user-row' : 'msg-assistant-row');
    if (role === 'user') {
      el.innerHTML = `
        <div class="flex items-start justify-end gap-2 max-w-full">
          <div class="msg-user max-w-[85%] sm:max-w-[78%] leading-relaxed text-sm"></div>
          <div class="shrink-0 w-6 h-6 sm:w-7 sm:h-7 rounded-lg bg-stone-200 text-stone-700 flex items-center justify-center font-bold text-[10px] sm:text-xs select-none mt-0.5">You</div>
        </div>`;
    } else {
      el.innerHTML = `
        <div class="flex items-start gap-2 sm:gap-3 w-full max-w-full min-w-0">
          <div class="assistant-avatar shrink-0 w-6 h-6 sm:w-7 sm:h-7 rounded-lg bg-stone-900 text-stone-100 flex items-center justify-center font-black text-[10px] sm:text-xs select-none shadow-2xs mt-0.5">S</div>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-1.5 sm:gap-2 mb-1">
              <span class="font-semibold text-xs text-stone-800">SquidWings</span>
              <span class="meta-tag text-[10px] sm:text-[11px] text-stone-400 font-mono truncate"></span>
            </div>
            <div class="process-card mb-2.5 sm:mb-3 p-2.5 sm:p-3 rounded-xl border border-stone-200 bg-stone-50/80 transition-all max-w-full">
              <div class="flex items-center justify-between gap-1.5">
                <div class="flex items-center gap-1.5 min-w-0">
                  <span class="relative flex h-2 w-2 shrink-0">
                    <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
                    <span class="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
                  </span>
                  <span class="process-status text-[11px] sm:text-xs font-medium text-stone-700 truncate">Preparing context & routing...</span>
                </div>
                <span class="process-timer font-mono text-[10px] sm:text-xs text-stone-500 bg-white px-1.5 sm:px-2 py-0.5 rounded-full border border-stone-200 shrink-0">0.0s</span>
              </div>
              <div class="process-shimmer mt-2 sm:mt-2.5 flex items-center gap-1.5">
                <div class="h-1.5 w-10 sm:w-12 rounded-full bg-stone-200 animate-pulse"></div>
                <div class="h-1.5 w-16 sm:w-24 rounded-full bg-stone-200 animate-pulse"></div>
                <div class="h-1.5 w-12 sm:w-16 rounded-full bg-stone-200 animate-pulse"></div>
              </div>
              <details class="think-details mt-2 sm:mt-2.5 border-t border-stone-200/60 pt-2 hidden" open>
                <summary class="cursor-pointer text-[11px] font-medium text-stone-500 hover:text-stone-800 select-none flex items-center justify-between">
                  <span class="flex items-center gap-1.5 truncate">
                    <span class="toggle-icon inline-block text-[10px]">▶</span>
                    <span>Reasoning Steps</span>
                  </span>
                  <span class="think-badge text-[10px] text-stone-400 font-mono shrink-0">streaming</span>
                </summary>
                <pre class="think-pre text-[11px] text-stone-600 font-mono bg-white p-2 sm:p-2.5 rounded-lg border border-stone-200 mt-2 max-h-48 sm:max-h-56 overflow-y-auto whitespace-pre-wrap leading-relaxed select-text"></pre>
              </details>
            </div>
            <div class="prose-out text-stone-900 leading-relaxed text-sm"></div>
            <div class="footer-meta flex flex-wrap items-center gap-1.5 sm:gap-2 mt-2.5 pt-2 border-t border-stone-100 text-[10px] sm:text-xs text-stone-400 hidden">
              <span class="model-badge px-1.5 py-0.5 rounded-md bg-stone-100 text-stone-600 font-mono text-[10px] sm:text-[11px] truncate max-w-[140px] sm:max-w-none"></span>
              <span class="token-badge font-mono text-[10px] sm:text-[11px]"></span>
              <span class="latency-badge font-mono text-[10px] sm:text-[11px]"></span>
              <button class="copy-resp-btn ml-auto text-[11px] text-stone-500 hover:text-stone-800 flex items-center gap-1 px-1.5 py-0.5 rounded-md hover:bg-stone-100 transition-colors cursor-pointer" type="button" title="Copy response">
                <svg class="w-3 h-3 sm:w-3.5 sm:h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                <span>Copy</span>
              </button>
            </div>
          </div>
        </div>`;
    }
    $('thread').appendChild(el);
    $('messages').scrollTop = $('messages').scrollHeight;
    return el;
  }

  function clearThread() {
    $('thread').innerHTML = '';
    $('empty').classList.remove('hidden');
  }

  function renderHistory(messages) {
    clearThread();
    for (const m of messages || []) {
      if (m.role === 'user') {
        addMsg('user').querySelector('.msg-user').textContent = m.content;
      } else if (m.role === 'assistant') {
        const el = addMsg('assistant');
        const out = el.querySelector('.prose-out');
        const proc = el.querySelector('.process-card');
        if (proc) proc.classList.add('hidden');
        out.innerHTML = md(m.content);
        enhanceCodeBlocks(out);
        const footer = el.querySelector('.footer-meta');
        if (footer) {
          footer.classList.remove('hidden');
          const modelBadge = footer.querySelector('.model-badge');
          if (modelBadge) modelBadge.textContent = m.model_used || S.current;
          const tokenBadge = footer.querySelector('.token-badge');
          if (tokenBadge && m.token_count) tokenBadge.textContent = `${m.token_count.toLocaleString()} tokens`;
          const copyBtn = footer.querySelector('.copy-resp-btn');
          if (copyBtn) {
            copyBtn.onclick = async () => {
              try {
                await navigator.clipboard.writeText(m.content || '');
                copyBtn.innerHTML = '<span>✓ Copied</span>';
                setTimeout(() => {
                  copyBtn.innerHTML = '<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg><span>Copy</span>';
                }, 2000);
              } catch (_) {}
            };
          }
        }
      }
    }
    $('messages').scrollTop = $('messages').scrollHeight;
  }

  function fmtWhen(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000), now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    return sameDay
      ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  async function loadSessions() {
    let sessions = [];
    try { sessions = (await j('/api/sessions')).sessions || []; } catch (e) { toast(e.message); }
    const list = $('session-list');
    list.innerHTML = sessions.length ? '' : '<p class="px-2 py-3 text-xs text-stone-400">No previous chats yet.</p>';
    for (const s of sessions) {
      const item = document.createElement('div');
      item.className = 'sess-item' + (s.session_id === S.session ? ' active' : '');
      item.innerHTML = `<button class="sess-open cursor-pointer" title="${esc(s.title)}">
          <div class="sess-title">${esc(s.title || 'Chat')}</div>
          <div class="sess-meta">${esc(fmtWhen(s.updated_at))} · ${s.message_count} msg</div>
        </button>
        <button class="sess-del cursor-pointer" title="Delete chat" aria-label="Delete chat">×</button>`;
      item.querySelector('.sess-open').onclick = () => openSession(s.session_id);
      item.querySelector('.sess-del').onclick = async ev => {
        ev.stopPropagation();
        if (!confirm(`Delete chat "${s.title}"? This removes the saved history.`)) return;
        try {
          await post(`/api/session/${encodeURIComponent(s.session_id)}/delete`, {});
          if (s.session_id === S.session) await newChat(false);
          else await loadSessions();
        } catch (e) { toast(e.message); }
      };
      list.appendChild(item);
    }
  }

  async function openSession(id) {
    if (S.busy) { toast('Wait for the current reply to finish.'); return; }
    try {
      const d = await j(`/api/session/${encodeURIComponent(id)}/messages`);
      S.session = id;
      localStorage.setItem('session', id);
      $('chat-title').textContent = d.title || 'Chat';
      renderHistory(d.messages);
      if (d.model_used && S.byId[d.model_used]) {
        S.current = d.model_used;
        $('model-select').value = d.model_used;
        await post('/api/model/select', { session_id: id, model_id: d.model_used }).catch(() => {});
      }
      if (window.innerWidth < 1024) closeDrawers();
      await loadSessions();
    } catch (e) { toast(e.message); }
  }

  async function newChat(reload = true) {
    if (S.busy) { toast('Wait for the current reply to finish.'); return; }
    try {
      const s = await j('/api/session/new');
      S.session = s.session_id;
      localStorage.setItem('session', s.session_id);
      $('chat-title').textContent = 'New chat';
      clearThread();
      if (S.current) await post('/api/model/select', { session_id: S.session, model_id: S.current }).catch(() => {});
      if (reload) await loadSessions();
    } catch (e) { toast(e.message); }
  }

  $('new-chat').onclick = () => newChat();

  function connectWS() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/chat');
      ws.onopen = () => resolve(ws);
      ws.onerror = () => reject(new Error('WebSocket connection failed'));
      ws.onclose = () => { S.ws = null; };
      S.ws = ws;
    });
  }

  /* Prompt suggestions */
  document.querySelectorAll('.prompt-suggestion').forEach(btn => {
    btn.onclick = () => {
      const p = btn.dataset.prompt || btn.innerText.trim();
      $('input').value = p;
      autosize();
      send();
    };
  });

  async function send() {
    const text = $('input').value.trim();
    if ((!text && !S.attachments.length) || S.busy) return;
    S.busy = true; $('send').disabled = true;
    const files = S.attachments.map(a => `<file name="${a.name}">\n${a.markdown}\n</file>`).join('\n\n');
    const full = files ? `${files}\n\n${text}` : text;
    const label = (S.attachments.length ? S.attachments.map(a => `[${a.name}] `).join('') : '') + text;
    const images = S.attachments.map(a => a.image_data_url).filter(Boolean);
    S.attachments = []; renderChips(); $('input').value = ''; autosize();

    addMsg('user').querySelector('.msg-user').textContent = label;
    if ($('chat-title').textContent === 'New chat') {
      const t = label.replace(/\s+/g, ' ').trim();
      $('chat-title').textContent = t.length > 60 ? t.slice(0, 60) + '…' : t;
    }

    const el = addMsg('assistant');
    const out = el.querySelector('.prose-out');
    const processCard = el.querySelector('.process-card');
    const timerEl = el.querySelector('.process-timer');
    const statusEl = el.querySelector('.process-status');
    const shimmerEl = el.querySelector('.process-shimmer');
    const thinkDetails = el.querySelector('.think-details');
    const thinkPre = el.querySelector('.think-pre');
    const thinkBadge = el.querySelector('.think-badge');
    const metaTag = el.querySelector('.meta-tag');
    const footerMeta = el.querySelector('.footer-meta');

    let content = '', hint = '', thinkDuration = 0;
    const startTime = performance.now();
    const timerInterval = setInterval(() => {
      const elapsed = ((performance.now() - startTime) / 1000).toFixed(1);
      if (timerEl) timerEl.textContent = elapsed + 's';
    }, 100);

    try {
      if (!S.ws || S.ws.readyState !== 1) await connectWS();
      S.ws.onmessage = ev => {
        const m = JSON.parse(ev.data);
        if (m.type === 'meta' && m.event === 'classified') {
          hint = `${m.task_type} · ${m.difficulty}${m.continuation ? ' · lanjutan' : ''}`;
          if (metaTag) metaTag.textContent = hint;
          const shortModel = (m.model || S.current || '').split('/').pop();
          if (statusEl) statusEl.textContent = `Routing: ${shortModel} (${m.difficulty})...`;
        }
        else if (m.type === 'thinking') {
          if (shimmerEl) shimmerEl.classList.add('hidden');
          if (statusEl) statusEl.textContent = 'Reasoning process...';
          if (thinkDetails) thinkDetails.classList.remove('hidden');
          if (thinkPre) {
            thinkPre.textContent += m.content;
            thinkPre.scrollTop = thinkPre.scrollHeight;
          }
        }
        else if (m.type === 'content') {
          if (shimmerEl) shimmerEl.classList.add('hidden');
          if (statusEl) statusEl.textContent = 'Generating final answer...';
          if (thinkDetails && thinkDetails.open && !thinkDuration) {
            thinkDuration = ((performance.now() - startTime) / 1000).toFixed(1);
            if (thinkBadge) thinkBadge.textContent = `${thinkDuration}s`;
            thinkDetails.open = false;
          }
          content += m.content;
          out.innerHTML = md(content);
          enhanceCodeBlocks(out);
        }
        else if (m.type === 'error') {
          clearInterval(timerInterval);
          if (processCard) processCard.classList.add('hidden');
          out.innerHTML = `<div class="p-3 rounded-xl border border-red-200 bg-red-50 text-red-700 text-xs">${esc(m.content)}</div>`;
          finish();
        }
        else if (m.type === 'done') {
          clearInterval(timerInterval);
          const totalSec = (m.latency_ms / 1000).toFixed(1);
          if (processCard) {
            if (thinkPre && thinkPre.textContent.trim()) {
              if (statusEl) statusEl.textContent = `Thought process completed (${thinkDuration || totalSec}s)`;
              if (shimmerEl) shimmerEl.classList.add('hidden');
            } else {
              processCard.classList.add('hidden');
            }
          }
          if (footerMeta) {
            footerMeta.classList.remove('hidden');
            const modelBadge = footerMeta.querySelector('.model-badge');
            if (modelBadge) modelBadge.textContent = m.model_used;
            const tokenBadge = footerMeta.querySelector('.token-badge');
            if (tokenBadge) tokenBadge.textContent = `${(m.total_tokens || 0).toLocaleString()} tokens`;
            const latBadge = footerMeta.querySelector('.latency-badge');
            if (latBadge) latBadge.textContent = `${totalSec}s`;
            const copyBtn = footerMeta.querySelector('.copy-resp-btn');
            if (copyBtn) {
              copyBtn.onclick = async () => {
                try {
                  await navigator.clipboard.writeText(content);
                  copyBtn.innerHTML = '<span>✓ Copied</span>';
                  setTimeout(() => {
                    copyBtn.innerHTML = '<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg><span>Copy</span>';
                  }, 2000);
                } catch (_) {}
              };
            }
          }
          enhanceCodeBlocks(out);
          finish();
          loadSessions();
        }
        $('messages').scrollTop = $('messages').scrollHeight;
      };
      S.ws.send(JSON.stringify({ message: full, session_id: S.session, images }));
    } catch (err) {
      clearInterval(timerInterval);
      if (processCard) processCard.classList.add('hidden');
      out.innerHTML = `<div class="p-3 rounded-xl border border-red-200 bg-red-50 text-red-700 text-xs">${esc(err.message)}</div>`;
      finish();
    }
  }

  function finish() {
    S.busy = false;
    $('send').disabled = false;
    $('input').focus();
  }

  function autosize() {
    const t = $('input');
    t.style.height = 'auto';
    t.style.height = Math.min(t.scrollHeight, 180) + 'px';
  }
  $('input').oninput = autosize;
  $('input').onkeydown = e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };
  $('send').onclick = send;

  /* ---------- Canvas ---------- */
  const NODE_W = 200, NODE_H = 78;
  const posKey = (tid, mid) => `pos:${tid}:${mid}`;
  const newId = () => 'a' + Math.random().toString(36).slice(2, 9);

  async function loadTeams() {
    if (S.features && S.features.teams === false) return;
    try {
      S.teams = (await j('/api/teams')).teams;
      renderTeamList();
      if (S.team === null && !S.members.length) newTeam();
    } catch (e) { toast(e.message); }
  }

  function renderTeamList() {
    $('team-list').innerHTML = S.teams.map(t =>
      `<button data-t="${esc(t.id)}" class="w-full text-left px-2.5 py-2 rounded-md hover:bg-stone-100 ${t.id === S.team ? 'bg-stone-100 font-medium' : ''}">${esc(t.name)}<div class="text-xs text-stone-400 font-normal">${t.members.length} agents</div></button>`).join('')
      || '<p class="px-2 py-1 text-stone-400 text-xs">No saved teams.</p>';
    document.querySelectorAll('[data-t]').forEach(b => b.onclick = () => openTeam(b.dataset.t));
  }

  function layout(members, tid) {
    members.forEach((m, i) => {
      const saved = tid && localStorage.getItem(posKey(tid, m.id));
      if (saved) { const [x, y] = saved.split(',').map(Number); m.x = x; m.y = y; }
      else if (m.x === undefined) { m.x = 80 + (i % 4) * 260; m.y = 80 + Math.floor(i / 4) * 170; }
    });
  }

  function openTeam(id) {
    const t = S.teams.find(x => x.id === id); if (!t) return;
    stopEvents(); S.run = null;
    S.team = t.id; S.lead = t.lead_id; S.selected = null;
    S.members = t.members.map(m => ({ ...m }));
    layout(S.members, t.id);
    $('team-name').value = t.name;
    $('result').classList.add('hidden'); $('run-info').textContent = '';
    renderTeamList(); renderBoard(); renderInspector();
  }

  function newTeam() {
    stopEvents(); S.run = null; S.team = null;
    const mid = S.current || S.models[0]?.id || '';
    S.members = [
      { id: newId(), name: 'Lead', role: 'Plans the work and writes the final answer', model_id: mid, instructions: '' },
      { id: newId(), name: 'Researcher', role: 'Finds facts and sources', model_id: mid, instructions: '' },
    ];
    S.lead = S.members[0].id; S.selected = S.members[0].id;
    layout(S.members, null);
    $('team-name').value = '';
    $('result').classList.add('hidden'); $('run-info').textContent = '';
    renderTeamList(); renderBoard(); renderInspector();
  }

  function renderBoard() {
    $('nodes').innerHTML = S.members.map(m => `
      <div class="node ${m.id === S.selected ? 'selected' : ''}" data-id="${esc(m.id)}" style="left:${m.x}px;top:${m.y}px">
        <div class="handle"><div class="font-medium truncate text-xs">${esc(m.name || 'Unnamed')}${m.id === S.lead ? ' <span class="text-[10px] text-stone-400 font-normal">lead</span>' : ''}</div>
        <div class="text-[11px] text-stone-500 truncate">${esc(m.role)}</div></div>
        <div class="px-2.5 pb-2 pt-1 text-[11px] text-stone-400 truncate">${esc(S.byId[m.model_id]?.display_name || m.model_id)}</div>
        <div class="px-2.5 pb-2 state text-[11px]"></div>
      </div>`).join('');
    document.querySelectorAll('.node').forEach(el => {
      el.onpointerdown = e => startDrag(e, el);
    });
    drawEdges(); paintRun();
  }

  function drawEdges() {
    const lead = S.members.find(m => m.id === S.lead);
    $('edges').innerHTML = lead ? S.members.filter(m => m.id !== lead.id).map(m =>
      `<line x1="${m.x + NODE_W / 2}" y1="${m.y + NODE_H / 2}" x2="${lead.x + NODE_W / 2}" y2="${lead.y + NODE_H / 2}"/>`).join('') : '';
  }

  function startDrag(e, el) {
    const m = S.members.find(x => x.id === el.dataset.id);
    const ox = e.clientX - m.x, oy = e.clientY - m.y;
    let moved = false;
    if (S.selected !== m.id) {
      S.selected = m.id;
      document.querySelectorAll('.node').forEach(n => n.classList.toggle('selected', n === el));
      renderInspector();
      if (window.matchMedia('(max-width: 1023px)').matches) setDrawer('inspector', true);
    }
    el.setPointerCapture(e.pointerId);
    el.onpointermove = ev => {
      moved = true;
      m.x = Math.max(0, Math.min(2400 - NODE_W, ev.clientX - ox));
      m.y = Math.max(0, Math.min(1600 - NODE_H, ev.clientY - oy));
      el.style.left = m.x + 'px'; el.style.top = m.y + 'px'; drawEdges();
    };
    el.onpointerup = () => {
      el.onpointermove = el.onpointerup = null;
      if (moved && S.team) localStorage.setItem(posKey(S.team, m.id), `${m.x},${m.y}`);
    };
  }

  function renderInspector() {
    const m = S.members.find(x => x.id === S.selected);
    const box = $('inspector');
    if (!m) { box.className = 'p-4 text-stone-400 text-xs'; box.textContent = 'Select an agent on the canvas.'; return; }
    box.className = 'p-4 space-y-3';
    const opts = Object.entries(S.models.reduce((a, x) => ((a[x.provider] ||= []).push(x), a), {}))
      .map(([p, l]) => `<optgroup label="${esc(p)}">${l.map(x => `<option value="${esc(x.id)}" ${x.id === m.model_id ? 'selected' : ''}>${esc(x.display_name)}</option>`).join('')}</optgroup>`).join('');
    box.innerHTML = `
      <div class="field"><label>Name</label><input data-f="name" value="${esc(m.name)}" maxlength="60"></div>
      <div class="field"><label>Role</label><input data-f="role" value="${esc(m.role)}" maxlength="120"></div>
      <div class="field"><label>Model</label><select data-f="model_id">${opts}</select></div>
      <div class="field"><label>Instructions</label><textarea data-f="instructions" rows="6" maxlength="4000">${esc(m.instructions)}</textarea></div>
      <label class="flex items-center gap-2 text-xs"><input type="radio" name="lead" ${m.id === S.lead ? 'checked' : ''} id="set-lead"> Lead agent</label>
      <button id="remove-node" class="border border-stone-300 rounded-md px-3 py-1.5 text-red-700 hover:bg-red-50 w-full text-xs cursor-pointer">Remove agent</button>`;
    box.querySelectorAll('[data-f]').forEach(inp => inp.oninput = inp.onchange = () => {
      m[inp.dataset.f] = inp.value;
      const node = document.querySelector(`.node[data-id="${m.id}"]`);
      if (node && inp.dataset.f !== 'instructions') renderBoardKeep();
    });
    $('set-lead').onchange = () => { S.lead = m.id; renderBoardKeep(); };
    $('remove-node').onclick = () => {
      if (S.members.length <= 2) return toast('A team needs at least 2 agents');
      S.members = S.members.filter(x => x.id !== m.id);
      if (S.lead === m.id) S.lead = S.members[0].id;
      S.selected = null; renderBoard(); renderInspector();
    };
  }
  function renderBoardKeep() { renderBoard(); }

  $('add-node').onclick = () => {
    if (S.members.length >= 6) return toast('Maximum 6 agents');
    const i = S.members.length;
    const m = { id: newId(), name: `Agent ${i + 1}`, role: 'Specialist', model_id: S.current || S.models[0]?.id || '', instructions: '', x: 80 + (i % 4) * 260, y: 80 + Math.floor(i / 4) * 170 };
    S.members.push(m); S.selected = m.id; renderBoard(); renderInspector();
  };
  $('new-team').onclick = newTeam;

  function payload() {
    return {
      name: $('team-name').value.trim(),
      description: '',
      members: S.members.map(({ id, name, role, model_id, instructions }) => ({ id, name, role, model_id, instructions })),
      lead_id: S.lead,
    };
  }

  async function saveTeam() {
    const p = payload();
    if (!p.name) { $('team-name').focus(); throw new Error('Give the team a name'); }
    const t = S.team
      ? await j('/api/teams/' + S.team, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) })
      : await post('/api/teams', p);
    const old = S.members;
    S.team = t.id; S.lead = t.lead_id;
    S.members = t.members.map((m, i) => ({ ...m, x: old[i]?.x, y: old[i]?.y }));
    S.selected = S.members.find((_, i) => old[i]?.id === S.selected)?.id || null;
    S.members.forEach(m => localStorage.setItem(posKey(t.id, m.id), `${m.x},${m.y}`));
    S.teams = (await j('/api/teams')).teams;
    renderTeamList(); renderBoard(); renderInspector();
    return t;
  }
  $('save-team').onclick = () => saveTeam().then(() => toast('Saved')).catch(e => toast(e.message));

  $('delete-team').onclick = async () => {
    if (!S.team) return newTeam();
    if (!confirm('Delete this team and its run history?')) return;
    try { await j('/api/teams/' + S.team, { method: 'DELETE' }); S.teams = S.teams.filter(t => t.id !== S.team); newTeam(); } catch (e) { toast(e.message); }
  };

  /* ---------- Run ---------- */
  function paintRun() {
    document.querySelectorAll('.node').forEach(n => {
      const a = S.run?.agents?.[n.dataset.id];
      n.dataset.status = a?.status || '';
      n.querySelector('.state').textContent = a ? a.status + (a.error ? ' · ' + a.error : '') : '';
    });
  }

  function showRun(run) {
    S.run = run; paintRun();
    const active = !done(run.status);
    $('run-info').textContent = `${run.status} · ${(run.total_tokens || 0).toLocaleString()} tokens`;
    $('cancel').classList.toggle('hidden', !active);
    $('run').disabled = active;
    const r = $('result');
    if (run.result || run.error) {
      r.classList.remove('hidden');
      r.innerHTML = run.error ? `<p class="text-red-700">${esc(run.error)}</p>` : md(run.result);
      enhanceCodeBlocks(r);
    } else r.classList.add('hidden');
  }

  function stopEvents() { if (S.events) { S.events.close(); S.events = null; } }

  $('run').onclick = async () => {
    const task = $('task').value.trim();
    if (!task) return $('task').focus();
    $('run').disabled = true;
    try {
      await saveTeam();
      const run = await post(`/api/teams/${S.team}/runs`, { task });
      showRun(run); stopEvents();
      S.events = new EventSource(`/api/teams/runs/${run.id}/events`);
      S.events.onmessage = ev => {
        const d = JSON.parse(ev.data);
        if (!S.run || d.run.id !== S.run.id) return;
        showRun(d.run);
        if (d.type === 'run_finished') stopEvents();
      };
      S.events.onerror = () => { if (S.run && done(S.run.status)) stopEvents(); };
    } catch (e) { toast(e.message); $('run').disabled = false; }
  };
  $('task').onkeydown = e => { if (e.key === 'Enter') $('run').click(); };
  $('cancel').onclick = async () => {
    if (!S.run) return;
    try { showRun(await j(`/api/teams/runs/${S.run.id}/cancel`, { method: 'POST' })); } catch (e) { toast(e.message); }
  };

  /* ---------- Init ---------- */
  (async () => {
    let defaultModel = null;
    try {
      const cfg = await j('/api/config').catch(() => ({}));
      applyFeatures(cfg.features);
      const s = await j('/api/session/new'); S.session = s.session_id; defaultModel = s.default_model;
      await loadModels();
      if (S.current !== defaultModel) await post('/api/model/select', { session_id: S.session, model_id: S.current }).catch(() => {});
    } catch (e) { toast(e.message); }
    showTab(localStorage.getItem('tab') || 'chat');

    // Restore desktop sidebar collapsed state
    if (window.innerWidth >= 1024 && localStorage.getItem('sidebar_collapsed') === '1') {
      $('session-panel').classList.add('desktop-collapsed');
    }

    // Restore the last opened chat, otherwise show the empty new session.
    const last = localStorage.getItem('session');
    if (last) {
      try {
        const d = await j(`/api/session/${encodeURIComponent(last)}/messages`);
        if (d.messages && d.messages.length) { await openSession(last); }
      } catch (_) { localStorage.removeItem('session'); }
    }
    loadSessions();
  })();
})();
