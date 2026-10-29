/* AI Teams: independent module, no globals leaked into the chat application. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = { teams: [], selected: null, editing: null, models: [], members: [], run: null, events: null };
  const escape = value => { const el = document.createElement('div'); el.textContent = String(value ?? ''); return el.innerHTML; };
  const markdown = text => window.marked ? window.marked.parse(text || '') : escape(text).replace(/\n/g, '<br>');
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function api(path, options = {}) {
    const response = await fetch('/api/teams' + path, { headers: { 'Content-Type': 'application/json' }, ...options });
    let data = {};
    try { data = await response.json(); } catch (_) { /* empty response */ }
    if (!response.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map(e => e.msg).join(', ') : (data.detail || `Request failed (${response.status})`));
    return data;
  }
  function notify(message) { const el = $('toast-message'); if (el) { el.textContent = message; $('toast').classList.remove('hide'); setTimeout(() => $('toast').classList.add('hide'), 4000); } }
  function modelOptions(selected) { return state.models.map(m => `<option value="${escape(m.id)}" ${m.id === selected ? 'selected' : ''}>${escape(m.name)} · ${escape(m.provider)}</option>`).join(''); }

  async function open() {
    $('team-overlay').classList.add('active'); $('team-overlay').setAttribute('aria-hidden', 'false');
    $('sidebar').classList.remove('open'); $('sidebar-backdrop').classList.remove('active');
    try {
      const [teamData, modelResponse] = await Promise.all([api(''), fetch('/api/models').then(r => r.json())]);
      state.teams = teamData.teams; state.models = modelResponse.models || [];
      if (state.selected && !state.teams.some(t => t.id === state.selected)) { state.selected = null; state.run = null; }
      renderTeams();
      if (state.selected) await selectTeam(state.selected);
    } catch (error) { $('team-list').innerHTML = `<p class="team-error">${escape(error.message)}</p>`; }
  }
  function close() { disconnect(); $('team-overlay').classList.remove('active'); $('team-overlay').setAttribute('aria-hidden', 'true'); }
  function renderTeams() {
    $('team-list').innerHTML = state.teams.length ? state.teams.map(t => `<button class="team-list-item ${t.id === state.selected ? 'active' : ''}" data-team="${escape(t.id)}"><strong>${escape(t.name)}</strong><small>${t.members.length} members · ${escape(t.members.find(m => m.id === t.lead_id)?.name || 'Lead')}</small></button>`).join('') : '<p class="team-muted">No teams yet. Build your first one!</p>';
    document.querySelectorAll('[data-team]').forEach(el => el.onclick = () => selectTeam(el.dataset.team));
  }
  async function selectTeam(id) {
    disconnect(); state.selected = id; state.run = null; renderTeams(); renderTeam();
    try { const data = await api(`/${encodeURIComponent(id)}/runs`); if (state.selected === id && data.runs.length) showRun(data.runs[0], true); } catch (error) { notify(error.message); }
  }
  function currentTeam() { return state.teams.find(t => t.id === state.selected); }
  function renderTeam() {
    const team = currentTeam(); if (!team) return;
    $('team-main').innerHTML = `<div class="team-detail-header"><div><h3>${escape(team.name)}</h3><p>${escape(team.description || 'A collaborative team of AI specialists.')}</p></div><div class="team-actions"><button class="team-secondary-btn" id="edit-team">Edit team</button><button class="team-secondary-btn team-danger" id="delete-team">Delete</button></div></div><div class="team-task-box"><label for="team-task">What should the team accomplish?</label><textarea id="team-task" maxlength="50000" placeholder="Example: Research the target users, design the onboarding flow, and identify implementation risks."></textarea><button class="team-primary-btn" id="start-team">▶ Run team</button></div><div id="team-run-area"></div><details class="team-runs" id="team-runs"><summary>Recent runs</summary><div id="team-run-history"></div></details>`;
    $('edit-team').onclick = () => openEditor(team); $('delete-team').onclick = deleteTeam; $('start-team').onclick = startRun;
    $('team-task').onkeydown = e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') startRun(); };
    api(`/${encodeURIComponent(team.id)}/runs`).then(data => {
      if (state.selected !== team.id) return;
      $('team-run-history').innerHTML = data.runs.length ? data.runs.slice(0, 20).map(r => `<button class="team-history-item" data-run="${r.id}"><span class="team-status ${r.status}">${escape(r.status)}</span> ${escape(r.task.slice(0, 85))}<small>${new Date(r.created_at * 1000).toLocaleString()}</small></button>`).join('') : '<p class="team-muted">No runs yet.</p>';
      document.querySelectorAll('[data-run]').forEach(el => el.onclick = async () => { disconnect(); showRun(await api(`/runs/${el.dataset.run}`), true); });
    }).catch(() => {});
  }
  function openEditor(team = null) {
    state.editing = team?.id || null; state.members = team ? team.members.map(m => ({...m})) : [{name:'Alex',role:'Team lead & reviewer',model_id:state.models[0]?.id || '',instructions:''},{name:'Sam',role:'Research specialist',model_id:state.models[0]?.id || '',instructions:''}];
    $('editor-title').textContent = team ? 'Edit team' : 'Create a team'; $('team-name').value = team?.name || ''; $('team-description').value = team?.description || ''; $('team-form-error').textContent = '';
    renderMembers(team?.lead_id); $('team-editor-overlay').classList.add('active'); $('team-editor-overlay').setAttribute('aria-hidden', 'false'); $('team-name').focus();
  }
  function closeEditor() { $('team-editor-overlay').classList.remove('active'); $('team-editor-overlay').setAttribute('aria-hidden', 'true'); }
  function renderMembers(leadId) {
    if (!state.members.some(m => m.id === leadId)) leadId = state.members[0]?.id;
    $('team-members').innerHTML = state.members.map((m, i) => `<section class="team-member-card" data-index="${i}"><div class="team-member-top"><strong>Member ${i + 1}</strong><button type="button" class="team-icon-btn remove-member" aria-label="Remove member" ${state.members.length <= 2 ? 'disabled' : ''}>×</button></div><div class="team-member-grid"><label>Name<input class="member-name" required maxlength="60" value="${escape(m.name)}" placeholder="Agent name"></label><label>Role<input class="member-role" required maxlength="120" value="${escape(m.role)}" placeholder="e.g. Security reviewer"></label></div><label>Model<select class="member-model" required>${modelOptions(m.model_id)}</select></label><label>Custom instructions <span class="team-muted">(optional)</span><textarea class="member-instructions" maxlength="4000" placeholder="Specific focus, output format, or constraints">${escape(m.instructions)}</textarea></label></section>`).join('');
    document.querySelectorAll('.remove-member').forEach(el => el.onclick = () => { collectMembers(); state.members.splice(Number(el.closest('[data-index]').dataset.index), 1); renderMembers(); });
    const updateLead = () => { collectMembers(); const old = $('team-lead').value; $('team-lead').innerHTML = state.members.map((m, i) => `<option value="${i}">${escape(m.name || `Member ${i + 1}`)}</option>`).join(''); $('team-lead').value = String(Math.max(0, state.members.findIndex(m => m.id === old))); };
    document.querySelectorAll('.member-name').forEach(el => el.onchange = updateLead);
    $('team-lead').innerHTML = state.members.map((m, i) => `<option value="${i}" ${m.id === leadId ? 'selected' : ''}>${escape(m.name)}</option>`).join(''); $('member-count').textContent = `${state.members.length} / 6`; $('add-member').disabled = state.members.length >= 6;
  }
  function collectMembers() { state.members = [...document.querySelectorAll('[data-index]')].map((card, i) => ({ id: state.members[i]?.id || null, name: card.querySelector('.member-name').value, role: card.querySelector('.member-role').value, model_id: card.querySelector('.member-model').value, instructions: card.querySelector('.member-instructions').value })); }
  async function saveTeam(event) {
    event.preventDefault(); collectMembers(); $('team-form-error').textContent = ''; $('save-team').disabled = true;
    try {
      const leadIndex = Number($('team-lead').value); const body = JSON.stringify({ name: $('team-name').value, description: $('team-description').value, members: state.members, lead_id: state.members[leadIndex]?.id });
      const team = await api(state.editing ? `/${state.editing}` : '', { method: state.editing ? 'PUT' : 'POST', body });
      const existing = state.teams.findIndex(t => t.id === team.id); if (existing >= 0) state.teams[existing] = team; else state.teams.unshift(team);
      state.selected = team.id; closeEditor(); renderTeams(); await selectTeam(team.id); notify('Team saved successfully.');
    } catch (error) { $('team-form-error').textContent = error.message; } finally { $('save-team').disabled = false; }
  }
  async function deleteTeam() { const team = currentTeam(); if (!team || !confirm(`Delete “${team.name}” and its run history?`)) return; try { await api(`/${team.id}`, {method:'DELETE'}); state.teams = state.teams.filter(t => t.id !== team.id); state.selected = null; state.run = null; renderTeams(); $('team-main').innerHTML = '<div class="team-welcome"><span>🤝</span><h3>Select or create a team</h3><p>Your collaborative workspace is ready.</p></div>'; } catch (error) { notify(error.message); } }
  async function startRun() {
    const task = $('team-task').value.trim(); if (!task) { $('team-task').focus(); return; }
    $('start-team').disabled = true;
    try { const run = await api(`/${state.selected}/runs`, {method:'POST',body:JSON.stringify({task})}); showRun(run); connect(run.id); }
    catch (error) { notify(error.message); } finally { if ($('start-team')) $('start-team').disabled = false; }
  }
  function showRun(run, historical = false) {
    state.run = run; const area = $('team-run-area'); if (!area) return;
    const active = !['completed','failed','cancelled'].includes(run.status);
    area.innerHTML = `<div class="team-run-status"><span class="team-status ${run.status}" id="run-status">${escape(run.status)}</span><span id="run-tokens">${run.total_tokens.toLocaleString()} tokens</span>${active ? '<button class="team-secondary-btn" id="cancel-run">Cancel run</button>' : ''}</div><div class="team-agents">${Object.entries(run.agents).map(([id,a]) => `<article class="team-agent" id="agent-${escape(id)}" data-status="${a.status}"><strong>${escape(a.name)} ${a.is_lead ? '⭐' : ''}</strong><small>${escape(a.role)}</small><small>${escape(a.model_id)}</small><div class="agent-state">${escape(a.status)}${a.error ? ' · '+escape(a.error) : ''}</div></article>`).join('')}</div><div class="team-result" id="team-result">${run.result ? markdown(run.result) : (active ? '<span class="team-muted">Specialists are preparing their reports…</span>' : '')}</div>${run.error ? `<p class="team-error">${escape(run.error)}</p>` : ''}`;
    if (active && $('cancel-run')) $('cancel-run').onclick = async () => { try { showRun(await api(`/runs/${run.id}/cancel`, {method:'POST'})); } catch (e) { notify(e.message); } };
  }
  function connect(id) {
    disconnect(); const source = new EventSource(`/api/teams/runs/${id}/events`); state.events = source;
    source.onmessage = event => { const data = JSON.parse(event.data); if (!state.run || data.run.id !== state.run.id) return; showRun(data.run); if (data.type === 'run_finished') { disconnect(); notify(data.run.status === 'completed' ? 'Team run completed.' : `Run ${data.run.status}.`); } };
    source.onerror = () => { if (state.run && ['completed','failed','cancelled'].includes(state.run.status)) disconnect(); };
  }
  function disconnect() { if (state.events) { state.events.close(); state.events = null; } }
  $('open-teams').onclick = open; $('header-teams').onclick = open; $('close-teams').onclick = close; $('new-team').onclick = () => openEditor(); $('create-first-team').onclick = () => openEditor(); $('welcome-create').onclick = () => openEditor(); $('close-team-editor').onclick = closeEditor; $('cancel-team-editor').onclick = closeEditor; $('team-form').onsubmit = saveTeam;
  $('add-member').onclick = () => { collectMembers(); state.members.push({name:'',role:'',model_id:state.models[0]?.id || '',instructions:''}); renderMembers(); };
  document.addEventListener('keydown', e => { if (e.key !== 'Escape') return; if ($('team-editor-overlay').classList.contains('active')) closeEditor(); else if ($('team-overlay').classList.contains('active')) close(); });
})();
