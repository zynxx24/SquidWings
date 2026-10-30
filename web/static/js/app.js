(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = v => { const d = document.createElement('div'); d.textContent = String(v ?? ''); return d.innerHTML; };
  const md = t => window.marked ? window.marked.parse(t || '') : esc(t).replace(/\n/g, '<br>');
  const done = s => ['completed', 'failed', 'cancelled'].includes(s);

  const S = {
    models: [], byId: {}, current: null, session: null, ws: null,
    attachments: [], busy: false,
    teams: [], team: null, members: [], lead: null, selected: null, run: null, events: null,
  };

  function toast(msg) {
    const t = $('toast'); t.textContent = msg; t.classList.remove('hidden');
    clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add('hidden'), 3500);
  }

  /* ---------- Layout (mobile drawers) ---------- */
  function closeDrawers() {
    document.querySelectorAll('.team-panel, .inspector-panel, .session-panel').forEach(p => p.classList.remove('open'));
    $('scrim').classList.add('hidden');
  }
  function setDrawer(which, open) {
    const panel = which ? $(which + '-panel') : null;
    if (panel) panel.classList.toggle('open', open);
    $('scrim').classList.toggle('hidden', !document.querySelector('.team-panel.open, .inspector-panel.open, .session-panel.open'));
  }
  $('scrim').onclick = closeDrawers;
  $('open-teams').onclick = () => setDrawer('team', true);
  $('close-teams').onclick = closeDrawers;
  $('close-inspector').onclick = closeDrawers;
  $('open-sessions').onclick = () => setDrawer('session', true);
  $('close-sessions').onclick = closeDrawers;
  window.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawers(); });

  async function j(url, opts = {}) {
    const r = await fetch(url, opts);
    let d = {};
    try { d = await r.json(); } catch (_) { /* no body */ }
    if (!r.ok) throw new Error(Array.isArray(d.detail) ? d.detail.map(e => e.msg).join(', ') : (d.detail || `Request failed (${r.status})`));
    return d;
  }
  const post = (url, body) => j(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  /* ---------- Tabs ---------- */
  function showTab(name) {
    closeDrawers();
    document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
    $('view-' + name).classList.remove('hidden');
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    localStorage.setItem('tab', name);
    if (name === 'canvas') loadTeams();
  }
  document.querySelectorAll('.tab').forEach(b => b.onclick = () => showTab(b.dataset.tab));

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
    $('upload-hint').textContent = 'Documents become Markdown. Audio and video are transcribed (first 10 min). Images are sent to vision-capable models.'
      + (native.length ? ` This model natively supports: ${native.join(', ')}.` : '');
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

  /* ---------- Chat ---------- */
  function addMsg(role) {
    $('empty').classList.add('hidden');
    const el = document.createElement('div');
    el.className = 'msg ' + (role === 'user' ? 'msg-user-row' : 'msg-assistant-row');
    el.innerHTML = `<div class="msg-role">${role === 'user' ? 'You' : 'Assistant'}</div>` + (role === 'user'
      ? '<div class="msg-user"></div>'
      : '<details class="think hidden"><summary>Thinking</summary><pre></pre></details><div class="prose-out"></div><div class="meta text-xs text-stone-400 mt-1"></div>');
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
        out.innerHTML = md(m.content);
        out.querySelectorAll('pre code').forEach(b => window.hljs && window.hljs.highlightElement(b));
        const bits = [m.model_used, m.token_count ? `${m.token_count} tokens` : ''].filter(Boolean);
        if (bits.length) el.querySelector('.meta').textContent = bits.join(' · ');
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
      item.innerHTML = `<button class="sess-open" title="${esc(s.title)}">
          <div class="sess-title">${esc(s.title || 'Chat')}</div>
          <div class="sess-meta">${esc(fmtWhen(s.updated_at))} · ${s.message_count} msg</div>
        </button>
        <button class="sess-del" title="Delete chat" aria-label="Delete chat">x</button>`;
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
      ws.onerror = () => reject(new Error('WebSocket failed'));
      ws.onclose = () => { S.ws = null; };
      S.ws = ws;
    });
  }

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
    const out = el.querySelector('.prose-out'), think = el.querySelector('details'), tpre = think.querySelector('pre'), meta = el.querySelector('.meta');
    let content = '', hint = '';
    try {
      if (!S.ws || S.ws.readyState !== 1) await connectWS();
      S.ws.onmessage = ev => {
        const m = JSON.parse(ev.data);
        if (m.type === 'meta' && m.event === 'classified') {
          hint = `${m.task_type} · ${m.difficulty}${m.continuation ? ' · lanjutan' : ''}`;
          meta.textContent = hint;
        }
        else if (m.type === 'thinking') { think.classList.remove('hidden'); tpre.textContent += m.content; }
        else if (m.type === 'content') { content += m.content; out.innerHTML = md(content); }
        else if (m.type === 'error') { out.innerHTML = `<p class="text-red-700">${esc(m.content)}</p>`; finish(); }
        else if (m.type === 'done') {
          meta.textContent = `${hint ? hint + ' · ' : ''}${m.model_used} · ${m.total_tokens} tokens · ${(m.latency_ms / 1000).toFixed(1)}s`;
          out.querySelectorAll('pre code').forEach(b => window.hljs && window.hljs.highlightElement(b));
          finish();
          loadSessions();
        }
        $('messages').scrollTop = $('messages').scrollHeight;
      };
      S.ws.send(JSON.stringify({ message: full, session_id: S.session, images }));
    } catch (err) { out.innerHTML = `<p class="text-red-700">${esc(err.message)}</p>`; finish(); }
  }
  function finish() { S.busy = false; $('send').disabled = false; $('input').focus(); }

  function autosize() { const t = $('input'); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 160) + 'px'; }
  $('input').oninput = autosize;
  $('input').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } };
  $('send').onclick = send;

  /* ---------- Canvas ---------- */
  const NODE_W = 200, NODE_H = 78;
  const posKey = (tid, mid) => `pos:${tid}:${mid}`;
  const newId = () => 'a' + Math.random().toString(36).slice(2, 9);

  async function loadTeams() {
    try {
      S.teams = (await j('/api/teams')).teams;
      renderTeamList();
      if (S.team === null && !S.members.length) newTeam();
    } catch (e) { toast(e.message); }
  }

  function renderTeamList() {
    $('team-list').innerHTML = S.teams.map(t =>
      `<button data-t="${esc(t.id)}" class="w-full text-left px-2.5 py-2 rounded-md hover:bg-stone-100 ${t.id === S.team ? 'bg-stone-100 font-medium' : ''}">${esc(t.name)}<div class="text-xs text-stone-400 font-normal">${t.members.length} agents</div></button>`).join('')
      || '<p class="px-2 py-1 text-stone-400">No saved teams.</p>';
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
        <div class="handle"><div class="font-medium truncate">${esc(m.name || 'Unnamed')}${m.id === S.lead ? ' <span class="text-xs text-stone-400 font-normal">lead</span>' : ''}</div>
        <div class="text-xs text-stone-500 truncate">${esc(m.role)}</div></div>
        <div class="px-2.5 pb-2 pt-1 text-xs text-stone-400 truncate">${esc(S.byId[m.model_id]?.display_name || m.model_id)}</div>
        <div class="px-2.5 pb-2 state"></div>
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
    if (!m) { box.className = 'p-4 text-stone-400'; box.textContent = 'Select an agent on the canvas.'; return; }
    box.className = 'p-4 space-y-3';
    const opts = Object.entries(S.models.reduce((a, x) => ((a[x.provider] ||= []).push(x), a), {}))
      .map(([p, l]) => `<optgroup label="${esc(p)}">${l.map(x => `<option value="${esc(x.id)}" ${x.id === m.model_id ? 'selected' : ''}>${esc(x.display_name)}</option>`).join('')}</optgroup>`).join('');
    box.innerHTML = `
      <div class="field"><label>Name</label><input data-f="name" value="${esc(m.name)}" maxlength="60"></div>
      <div class="field"><label>Role</label><input data-f="role" value="${esc(m.role)}" maxlength="120"></div>
      <div class="field"><label>Model</label><select data-f="model_id">${opts}</select></div>
      <div class="field"><label>Instructions</label><textarea data-f="instructions" rows="6" maxlength="4000">${esc(m.instructions)}</textarea></div>
      <label class="flex items-center gap-2"><input type="radio" name="lead" ${m.id === S.lead ? 'checked' : ''} id="set-lead"> Lead agent</label>
      <button id="remove-node" class="border border-stone-300 rounded-md px-3 py-1.5 text-red-700 hover:bg-red-50 w-full">Remove agent</button>`;
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
    // Backend may assign its own ids; carry positions over by index.
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
      r.querySelectorAll('pre code').forEach(b => window.hljs && window.hljs.highlightElement(b));
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
      const s = await j('/api/session/new'); S.session = s.session_id; defaultModel = s.default_model;
      await loadModels();
      if (S.current !== defaultModel) await post('/api/model/select', { session_id: S.session, model_id: S.current }).catch(() => {});
    } catch (e) { toast(e.message); }
    showTab(localStorage.getItem('tab') || 'chat');

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
