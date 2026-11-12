/* Team flows, edited like a node-workflow editor.

   The canvas reads left to right: a trigger node, then every node in the order it runs. Drag
   from a node's output dot to another node to connect them, drop on empty space to add a node
   there, click a line and press Delete (or ×) to cut it. Right-click a node (or press Enter, the Menu key, or
   the ⋯ on the node) for its menu: Edit opens the detail view with what came in, its settings, and what it sent
   out. Right-click a line, the trigger, a run, or empty canvas for theirs. Nothing needs a double click.
   The + button opens the node list. Pan by dragging the background or scrolling, zoom with Ctrl+scroll or the buttons.

   Tabs: Editor (the canvas), Data (files and cloud tables the agents use), Eksekusi (past runs), Kredensial
   (Telegram, web search, voice, Google and database keys, saved on the server instead of in .env).

   The saved flow is the same as before: members with depends_on, branch, kind and config, a lead,
   a reviewer, and a schedule. Positions live in this browser only. */
(() => {
  'use strict';

  const { $, esc, icon, api, post, toast, md, store, S, fillModelOptions, shortModel, formatTokens, formatSeconds, describeTool, enhanceCode } = window.SQ;

  /* ── Constants ───────────────────────────────────── */
  const NODE_W = 232;
  const BASE_H = 68;
  const PORT_GAP = 24;
  const COL = 300;
  const ROW = 116;
  const ORIGIN = { x: 330, y: 300 };
  const CANVAS = { w: 6000, h: 4000 };
  const TRIGGER = '__trigger';
  const MAX_NODES = 12;
  const MAX_AGENTS = 6;
  const PIN_KEY = 'router-pin';
  const V = { x: 0, y: 0, k: 1 };

  const NODE_ICONS = {
    agent: '<path d="M12 3v3M8 7h8a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3v-6a3 3 0 0 1 3-3z"/><circle cx="9.5" cy="12.5" r="1"/><circle cx="14.5" cy="12.5" r="1"/><path d="M9.5 16h5"/>',
    gate: '<path d="M6 4v6a4 4 0 0 0 4 4h8M18 14l-3-3M18 14l-3 3M6 20v-6"/>',
    switch: '<path d="M4 12h6M10 12l8-6M10 12h8M10 12l8 6"/><circle cx="19" cy="6" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="19" cy="18" r="1"/>',
    merge: '<path d="M5 5l7 7v7M19 5l-7 7"/>',
    delay: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 3h6"/>',
    set: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
    http: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    telegram: '<path d="m21 4-18 7 6 2 2 6 3-4 5 4z"/><path d="m9 13 8-6"/>',
    database: '<ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v14c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    trigger: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  };
  const nodeIcon = (kind, size = 20) =>
    `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${NODE_ICONS[kind] || NODE_ICONS.agent}</svg>`;

  const CATALOG = [
    { key: 'ai', label: 'AI', items: [
      { kind: 'agent', label: 'AI Agent', desc: 'Model AI dengan tool: riset web, menulis file, menjalankan kode. Bisa jadi Lead atau Reviewer.' },
    ] },
    { key: 'logic', label: 'Alur & logika', items: [
      { kind: 'gate', label: 'IF', desc: 'Cek syarat (AND/OR). Benar lewat jalur atas, salah lewat jalur bawah.' },
      { kind: 'switch', label: 'Switch', desc: 'Aturan pertama yang cocok memilih jalur A, B atau C. Sisanya ke D.' },
      { kind: 'merge', label: 'Merge', desc: 'Gabungkan beberapa jalur: tunggu salah satu atau semua.' },
      { kind: 'delay', label: 'Wait', desc: 'Jeda beberapa detik sebelum lanjut.' },
    ] },
    { key: 'data', label: 'Data', items: [
      { kind: 'set', label: 'Teks / template', desc: 'Susun teks dengan {{input}}, {{date}}, {{node.ID}} dan lainnya.' },
      { kind: 'http', label: 'HTTP Request', desc: 'GET atau POST ke alamat publik. Balasannya diteruskan.' },
    ] },
    { key: 'output', label: 'Kirim & simpan', items: [
      { kind: 'telegram', label: 'Telegram', desc: 'Kirim hasil ke chat atau channel Telegram.' },
      { kind: 'database', label: 'Database', desc: 'Simpan hasil ke SQLite, PostgreSQL, MySQL atau MongoDB.' },
    ] },
  ];
  const CATEGORY = Object.fromEntries(CATALOG.flatMap(group => group.items.map(item => [item.kind, group.key])));
  const LABEL = Object.fromEntries(CATALOG.flatMap(group => group.items.map(item => [item.kind, item.label])));
  const TRIGGERS = [
    { mode: 'off', label: 'Manual', desc: 'Berjalan saat tombol Jalankan alur ditekan.' },
    { mode: 'interval', label: 'Berulang', desc: 'Tiap beberapa menit.' },
    { mode: 'daily', label: 'Jam tertentu', desc: 'Satu atau beberapa jam setiap hari yang dipilih.' },
    { mode: 'cron', label: 'Cron', desc: 'Pola jadwal lengkap, misalnya 0 7 * * 1-5.' },
    { mode: 'once', label: 'Sekali', desc: 'Pada tanggal dan jam tertentu.' },
    { mode: 'telegram', label: 'Pesan Telegram', desc: 'Saat ada pesan ke bot: dari kamu saja, atau dari pelanggan.' },
  ];
  const NODE_DEFAULTS = {
    agent: { name: 'AI Agent', role: 'Spesialis', config: {} },
    delay: { name: 'Wait', config: { seconds: 30 } },
    gate: { name: 'IF', config: { combine: 'and', conditions: [{ mode: 'contains', value: '' }] } },
    switch: { name: 'Switch', config: { rules: [{ mode: 'contains', value: '' }, { mode: 'contains', value: '' }] } },
    merge: { name: 'Merge', config: { mode: 'any' } },
    set: { name: 'Teks', config: { text: 'Hari ini {{day}}, {{date}} jam {{time}}.\n\n{{input}}' } },
    http: { name: 'HTTP Request', config: { method: 'GET', url: '', body: '' } },
    telegram: { name: 'Kirim ke Telegram', config: { chat_id: '' } },
    database: { name: 'Simpan ke database', config: { database: 'sqlite', table: 'hasil_tim' } },
  };
  const DAYS = ['Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab', 'Min'];
  const CONDITION_LABEL = {
    contains: 'Berisi kata kunci', not_contains: 'Tidak berisi kata kunci', equals: 'Sama persis dengan',
    regex: 'Cocok dengan regex', empty: 'Kosong', time: 'Jam tertentu', ai: 'Ditanyakan ke AI',
  };
  const STATUS = { queued: 'Menunggu', running: 'Berjalan', completed: 'Selesai', failed: 'Gagal', cancelled: 'Dibatalkan', skipped: 'Dilewati' };
  const RUN_STATUS = { queued: 'Menunggu', running: 'Berjalan', synthesizing: 'Lead menyusun jawaban', reviewing: 'Reviewer memeriksa', revising: 'Lead merevisi', delivering: 'Mengirim hasil', rendering: 'Merender video', completed: 'Selesai', failed: 'Gagal', cancelled: 'Dibatalkan' };

  /* ── Flow helpers (mirror the server's rules) ──────── */
  const newId = () => `a${Math.random().toString(36).slice(2, 9)}`;
  const posKey = (team, id) => `wf:${team}:${id}`;
  const finished = status => ['completed', 'failed', 'cancelled'].includes(status);
  const kindOf = member => member.kind || 'agent';
  const isOutput = member => ['telegram', 'database'].includes(kindOf(member));
  const memberById = id => S.members.find(m => m.id === id);
  const parentsOf = member => (member.depends_on || []).map(memberById).filter(Boolean);
  const isBranching = member => ['gate', 'switch'].includes(kindOf(member));
  const branchParent = member => parentsOf(member).find(isBranching);
  const conditionsOf = config => config.conditions || (config.mode ? [config] : []);
  const defaultLead = () => (S.members.every(m => kindOf(m) === 'agent') && S.members[0] ? S.members[0].id : null);
  const typing = () => document.activeElement && document.activeElement.matches('input, textarea, select, [contenteditable]');

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

  /* The column each node runs in: the order on screen is the order it runs in. */
  function flowRows() {
    const after = afterAnswer();
    const rows = new Map();
    let answerRow = -1;
    const below = (member, seen) => {
      const parents = parentsOf(member).filter(p => !seen.has(p.id));
      return parents.length ? 1 + Math.max(...parents.map(p => rowOf(p, new Set([...seen, member.id])))) : 0;
    };
    function rowOf(member, seen = new Set()) {
      if (rows.has(member.id)) return rows.get(member.id);
      let row = below(member, seen);
      if (member.id === S.lead) row = Math.max(row, answerRow + 1);
      else if (member.id === S.reviewer) row = rowOf(memberById(S.lead) || member, seen) + 1;
      else if (isOutput(member) && !(member.depends_on || []).length) row = answerRow + 1;
      rows.set(member.id, row);
      return row;
    }
    S.members.filter(m => m.id !== S.lead && m.id !== S.reviewer && !after.has(m.id)).forEach(m => { answerRow = Math.max(answerRow, rowOf(m)); });
    [memberById(S.lead), memberById(S.reviewer)].filter(Boolean).forEach(m => { answerRow = Math.max(answerRow, rowOf(m)); });
    S.members.forEach(m => rowOf(m));
    return rows;
  }

  function startNodes() {
    const rows = flowRows();
    const first = Math.min(...rows.values());
    return S.members.filter(m => rows.get(m.id) === first);
  }

  function portsOf(member) {
    if (kindOf(member) === 'gate') return [{ key: 'A', label: 'benar' }, { key: 'B', label: 'salah' }];
    if (kindOf(member) === 'switch') return [{ key: 'A', label: 'A' }, { key: 'B', label: 'B' }, { key: 'C', label: 'C' }, { key: 'D', label: 'lainnya' }];
    return [{ key: '', label: '' }];
  }
  const heightOf = member => (member === TRIGGER ? BASE_H : Math.max(BASE_H, portsOf(member).length * PORT_GAP + 16));
  const inPoint = member => ({ x: member.x, y: member.y + heightOf(member) / 2 });
  function outPoint(member, key = '') {
    if (member === TRIGGER) return { x: S.trigger.x + NODE_W, y: S.trigger.y + BASE_H / 2 };
    const ports = portsOf(member);
    const index = Math.max(0, ports.findIndex(p => p.key === key));
    const h = heightOf(member);
    return { x: member.x + NODE_W, y: member.y + ((index + 1) * h) / (ports.length + 1) };
  }

  function roleLabel(member) {
    if (member.id === S.lead) return 'Lead · jawaban akhir';
    if (member.id === S.reviewer) return 'Reviewer';
    return LABEL[kindOf(member)] || 'Node';
  }

  function nodeDetail(member) {
    const config = member.config || {};
    switch (kindOf(member)) {
      case 'delay': return `Tunggu ${config.seconds || 0} detik`;
      case 'gate': {
        const list = conditionsOf(config);
        if (list.length > 1) return `${list.length} syarat, ${(config.combine || 'and').toUpperCase()}`;
        const only = list[0] || {};
        if (only.mode === 'time') return `Jam ${only.start || '--:--'}–${only.end || '--:--'}`;
        return `${CONDITION_LABEL[only.mode] || 'Syarat'}${only.value ? `: ${only.value}` : ''}`;
      }
      case 'switch': return `${(config.rules || []).length} aturan`;
      case 'merge': return config.mode === 'all' ? 'Tunggu semua jalur' : 'Salah satu jalur';
      case 'set': return (config.text || 'belum diisi').split('\n')[0].slice(0, 40);
      case 'http': return `${config.method || 'GET'} ${(config.url || '').replace(/^https?:\/\//, '').slice(0, 30) || 'URL belum diisi'}`;
      case 'telegram': return config.chat_id || 'Chat bawaan';
      case 'database': return `${config.database || 'sqlite'} · ${config.table || 'hasil_tim'}`;
      default: return shortModel(member.model_id) || 'model belum dipilih';
    }
  }

  function triggerLabel() {
    const schedule = S.schedule || { mode: 'off' };
    switch (schedule.mode) {
      case 'interval': return `Tiap ${schedule.minutes || '?'} menit`;
      case 'daily': return `Jam ${(schedule.times || [schedule.time || '--:--']).join(', ')}`;
      case 'cron': return `Cron ${schedule.cron || '?'}`;
      case 'once': return `Sekali, ${(schedule.at || '').replace('T', ' ')}`;
      case 'telegram': return `${schedule.audience === 'public' ? 'Pesan pelanggan' : 'Pesan Telegram'}${schedule.keyword ? ` ${schedule.keyword}` : ''}`;
      default: return 'Saat Jalankan alur ditekan';
    }
  }

  /* ── Dirty state ─────────────────────────────────── */
  function markDirty(dirty = true) {
    S.dirty = dirty;
    $('wf-dirty').classList.toggle('hidden', !dirty);
  }

  /* ── Layout and view ─────────────────────────────── */
  function autoLayout(teamId, force = false) {
    const rows = flowRows();
    const columns = {};
    S.members.forEach(member => { (columns[rows.get(member.id)] ||= []).push(member); });
    const order = Object.keys(columns).map(Number).sort((a, b) => a - b);
    S.members.forEach(member => {
      const saved = !force && teamId && store.get(posKey(teamId, member.id));
      if (saved) { [member.x, member.y] = saved.split(',').map(Number); return; }
      if (!force && member.x !== undefined) return;
      const list = columns[rows.get(member.id)];
      member.x = ORIGIN.x + order.indexOf(rows.get(member.id)) * COL;
      member.y = ORIGIN.y + (list.indexOf(member) - (list.length - 1) / 2) * ROW;
    });
    const savedTrigger = !force && teamId && store.get(posKey(teamId, TRIGGER));
    if (savedTrigger) { const [x, y] = savedTrigger.split(',').map(Number); S.trigger = { x, y }; }
    else {
      const starts = startNodes();
      const y = starts.length ? starts.reduce((sum, m) => sum + m.y + heightOf(m) / 2, 0) / starts.length - BASE_H / 2 : ORIGIN.y;
      S.trigger = { x: ORIGIN.x - COL, y };
    }
  }

  function savePositions() {
    if (!S.team) return;
    S.members.forEach(m => store.set(posKey(S.team, m.id), `${Math.round(m.x)},${Math.round(m.y)}`));
    store.set(posKey(S.team, TRIGGER), `${Math.round(S.trigger.x)},${Math.round(S.trigger.y)}`);
  }

  function applyView() {
    $('canvas').style.transform = `translate(${V.x}px, ${V.y}px) scale(${V.k})`;
    const board = $('board');
    board.style.backgroundSize = `${22 * V.k}px ${22 * V.k}px`;
    board.style.backgroundPosition = `${V.x}px ${V.y}px`;
    $('zoom-level').textContent = `${Math.round(V.k * 100)}%`;
  }

  function fitView() {
    const board = $('board');
    const items = [{ x: S.trigger.x, y: S.trigger.y, h: BASE_H }, ...S.members.map(m => ({ x: m.x, y: m.y, h: heightOf(m) }))];
    const minX = Math.min(...items.map(i => i.x)) - 50;
    const maxX = Math.max(...items.map(i => i.x + NODE_W)) + 110;   // room for the + after the last nodes
    const minY = Math.min(...items.map(i => i.y)) - 70;
    const maxY = Math.max(...items.map(i => i.y + i.h)) + 90;
    const width = board.clientWidth || 900;
    const height = (board.clientHeight || 600) - 70;   // the run bar sits at the bottom
    V.k = Math.max(0.35, Math.min(1.15, Math.min(width / (maxX - minX), height / (maxY - minY))));
    V.x = (width - (maxX - minX) * V.k) / 2 - minX * V.k;
    V.y = (height - (maxY - minY) * V.k) / 2 - minY * V.k;
    applyView();
  }

  function zoomAt(factor, cx, cy) {
    const k = Math.max(0.3, Math.min(1.8, V.k * factor));
    const board = $('board').getBoundingClientRect();
    const px = cx ?? board.width / 2;
    const py = cy ?? board.height / 2;
    V.x = px - ((px - V.x) * k) / V.k;
    V.y = py - ((py - V.y) * k) / V.k;
    V.k = k;
    applyView();
  }

  const canvasPoint = event => {
    const r = $('board').getBoundingClientRect();
    return { x: (event.clientX - r.left - V.x) / V.k, y: (event.clientY - r.top - V.y) / V.k };
  };

  /* ── Rendering ───────────────────────────────────── */
  function render() {
    renderNodes();
    renderEdges();
    paintRun();
  }

  function renderNodes() {
    const childrenOf = id => S.members.filter(m => (m.depends_on || []).includes(id));
    const flowsOn = new Set(edgeList().filter(e => e.auto).map(e => e.from));
    const triggerMode = (S.schedule || {}).mode || 'off';
    const trigger = `
      <div class="node trigger ${S.selected === TRIGGER ? 'selected' : ''}" data-id="${TRIGGER}" data-cat="trigger" style="left:${S.trigger.x}px;top:${S.trigger.y}px;height:${BASE_H}px" tabindex="0" role="button" aria-label="Pemicu: ${esc(triggerLabel())}">
        <span class="node-icon">${nodeIcon('trigger')}</span>
        <span class="node-text"><span class="node-kind">Pemicu${triggerMode !== 'off' ? ' · otomatis' : ''}</span><span class="node-name">${esc(TRIGGERS.find(t => t.mode === triggerMode)?.label || 'Manual')}</span><span class="node-sub">${esc(triggerLabel())}</span></span>
        <span class="port out" data-port="" data-from="${TRIGGER}" title="Seret ke node untuk menjadikannya langkah pertama"></span>
        <button class="node-more" type="button" tabindex="-1" aria-label="Menu pemicu" title="Menu (atau klik kanan)">⋯</button>
      </div>`;
    const nodes = S.members.map(member => {
      const ports = portsOf(member);
      const h = heightOf(member);
      const kind = kindOf(member);
      const outs = ports.map((port, i) => {
        const top = ((i + 1) * h) / (ports.length + 1);
        const used = childrenOf(member.id).some(c => !isBranching(member) || c.branch === port.key || !c.branch);
        return `<span class="port out" data-port="${port.key}" data-from="${esc(member.id)}" style="top:${top}px" title="Seret ke node lain untuk menyambung${port.label ? ` (jalur ${esc(port.label)})` : ''}"></span>
          ${port.label ? `<span class="port-label" style="top:${top}px">${esc(port.label)}</span>` : ''}
          ${used ? '' : `<button class="port-add${flowsOn.has(member.id) ? ' soft' : ''}" data-add-from="${esc(member.id)}" data-port="${port.key}" style="top:${top}px" type="button" aria-label="Tambah node setelah ${esc(member.name)}">${icon('plus', 12)}</button>`}`;
      }).join('');
      const role = member.id === S.lead ? '<span class="node-badge">Lead</span>' : member.id === S.reviewer ? '<span class="node-badge">Reviewer</span>' : '';
      return `
        <div class="node ${member.id === S.selected ? 'selected' : ''}" data-id="${esc(member.id)}" data-kind="${kind}" data-cat="${CATEGORY[kind] || 'ai'}" style="left:${member.x}px;top:${member.y}px;height:${h}px" tabindex="0" role="button" aria-label="${esc(member.name)}, ${esc(roleLabel(member))}">
          <span class="port in" data-to="${esc(member.id)}"></span>
          <span class="node-icon">${nodeIcon(kind)}</span>
          <span class="node-text"><span class="node-kind">${esc(roleLabel(member))}</span><span class="node-name">${esc(member.name || 'Tanpa nama')}</span><span class="node-sub">${esc(nodeDetail(member))}</span></span>
          ${role}<span class="node-status" aria-hidden="true"></span>
          <button class="node-more" type="button" tabindex="-1" aria-label="Menu ${esc(member.name)}" title="Menu (atau klik kanan)">⋯</button>
          ${outs}
          <span class="node-state"></span>
        </div>`;
    }).join('');
    $('nodes').innerHTML = trigger + nodes;
    $('nodes').querySelectorAll('.node').forEach(el => {
      el.onpointerdown = e => nodePointerDown(e, el);
      el.onkeydown = e => { if (e.key === 'Enter' && e.target === el) openNdv(el.dataset.id); };
      const more = el.querySelector('.node-more');
      more.onpointerdown = e => e.stopPropagation();
      more.onclick = e => {
        e.stopPropagation();
        selectNode(el.dataset.id);
        const box = more.getBoundingClientRect();
        window.SQMenu.show(el.dataset.id === TRIGGER ? triggerMenu() : nodeMenu(el.dataset.id), box.left, box.bottom, el);
      };
    });
    $('nodes').querySelectorAll('.port.out').forEach(port => { port.onpointerdown = e => portPointerDown(e, port); });
    $('nodes').querySelectorAll('.port-add').forEach(button => {
      button.onpointerdown = e => e.stopPropagation();
      button.onclick = e => { e.stopPropagation(); openCreator({ from: button.dataset.addFrom, branch: button.dataset.port }); };
    });
  }

  function edgePath(a, b) {
    const dx = Math.max(50, Math.abs(b.x - a.x) / 2);
    return `M${a.x} ${a.y}C${a.x + dx} ${a.y} ${b.x - dx} ${b.y} ${b.x} ${b.y}`;
  }

  /* Every line on the board. Solid lines are connections you made and can cut; dashed ones the
     flow adds by itself (agents feed the Lead, the Lead feeds the Reviewer and the outputs). */
  function edgeList() {
    const list = [];
    const lead = memberById(S.lead);
    const reviewer = memberById(S.reviewer);
    const after = afterAnswer();
    const dependedOn = new Set(S.members.flatMap(m => m.depends_on || []));
    startNodes().forEach(member => list.push({ from: TRIGGER, to: member.id, auto: true, title: 'Langkah pertama setelah pemicu' }));
    S.members.forEach(member => {
      parentsOf(member).forEach(parent => list.push({ from: parent.id, to: member.id, port: isBranching(parent) ? (member.branch || '') : '' }));
      if (member === lead || member === reviewer || after.has(member.id) || dependedOn.has(member.id) || !lead) return;
      list.push({ from: member.id, to: lead.id, auto: true, title: 'Otomatis: hasil agent ini dikirim ke Lead' });
    });
    if (lead && reviewer) list.push({ from: lead.id, to: reviewer.id, auto: true, title: 'Otomatis: Reviewer memeriksa jawaban Lead' });
    const writer = reviewer || lead;
    const ends = writer ? [writer] : S.members.filter(m => !after.has(m.id) && !dependedOn.has(m.id));
    S.members.filter(m => isOutput(m) && !(m.depends_on || []).length).forEach(output => ends.forEach(end => {
      if (end !== output) list.push({ from: end.id, to: output.id, auto: true, title: 'Otomatis: jawaban akhir dikirim ke sini' });
    }));
    return list;
  }

  function renderEdges() {
    const statusOf = id => (S.run && S.run.agents && S.run.agents[id] ? S.run.agents[id].status : '');
    const html = [];
    const selected = S.selectedEdge;
    edgeList().forEach(edge => {
      const from = edge.from === TRIGGER ? TRIGGER : memberById(edge.from);
      const to = memberById(edge.to);
      if (!from || !to) return;
      const a = outPoint(from, edge.port || '');
      const b = inPoint(to);
      const d = edgePath(a, b);
      const isSelected = selected && selected.from === edge.from && selected.to === edge.to;
      const cls = ['edge', edge.auto ? 'auto' : '', isSelected ? 'selected' : '', statusOf(edge.to) === 'running' ? 'active' : '',
        statusOf(edge.to) === 'skipped' ? 'skipped' : '', statusOf(edge.to) === 'completed' ? 'done' : ''].filter(Boolean).join(' ');
      html.push(`<path class="${cls}" d="${d}" marker-end="url(#arrow)"/>`);
      if (edge.auto) html.push(`<path class="edge-hit auto" d="${d}"><title>${esc(edge.title || '')}</title></path>`);
      else html.push(`<path class="edge-hit" d="${d}" data-from="${esc(edge.from)}" data-to="${esc(edge.to)}"><title>Klik untuk memilih, lalu Delete untuk memutus. Klik kanan untuk menu.</title></path>`);
      if (isSelected) S.selectedEdgeMid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    });
    $('edge-lines').innerHTML = html.join('');
    $('edge-lines').querySelectorAll('.edge-hit[data-from]').forEach(hit => {
      hit.onpointerdown = e => e.stopPropagation();
      hit.onclick = e => {
        e.stopPropagation();
        S.selectedEdge = { from: hit.dataset.from, to: hit.dataset.to };
        selectNode(null);
        renderEdges();
      };
    });
    const button = $('edge-delete');
    if (selected && S.selectedEdgeMid && edgeList().some(e => !e.auto && e.from === selected.from && e.to === selected.to)) {
      button.style.left = `${S.selectedEdgeMid.x}px`;
      button.style.top = `${S.selectedEdgeMid.y}px`;
      button.classList.remove('hidden');
    } else {
      button.classList.add('hidden');
      if (selected) S.selectedEdge = null;
    }
  }

  function selectNode(id) {
    S.selected = id;
    if (id) S.selectedEdge = null;
    document.querySelectorAll('#nodes .node').forEach(n => n.classList.toggle('selected', n.dataset.id === id));
  }

  /* ── Pointer: pan, drag, connect ─────────────────── */
  function boardPointerDown(event) {
    if (event.button !== 0 || event.target.closest('.node, .port-add, #edge-delete, .wf-zoom, .wf-run, .wf-plus, .wf-empty')) return;
    selectNode(null);
    if (S.selectedEdge) { S.selectedEdge = null; renderEdges(); }
    const start = { x: event.clientX, y: event.clientY, vx: V.x, vy: V.y };
    const board = $('board');
    board.classList.add('panning');
    board.setPointerCapture(event.pointerId);
    board.onpointermove = e => { V.x = start.vx + e.clientX - start.x; V.y = start.vy + e.clientY - start.y; applyView(); };
    board.onpointerup = () => { board.onpointermove = null; board.onpointerup = null; board.classList.remove('panning'); };
  }

  function nodePointerDown(event, el) {
    if (event.button !== 0 || event.target.closest('.port, .port-add')) return;
    event.stopPropagation();
    const id = el.dataset.id;
    const item = id === TRIGGER ? S.trigger : memberById(id);
    if (!item) return;
    selectNode(id);
    if (S.selectedEdge) { S.selectedEdge = null; renderEdges(); }
    const start = canvasPoint(event);
    const origin = { x: item.x, y: item.y };
    let moved = false;
    el.setPointerCapture(event.pointerId);
    el.onpointermove = e => {
      const p = canvasPoint(e);
      if (!moved && Math.hypot(p.x - start.x, p.y - start.y) < 4 / V.k) return;
      moved = true;
      item.x = Math.max(0, Math.min(CANVAS.w - NODE_W, origin.x + p.x - start.x));
      item.y = Math.max(0, Math.min(CANVAS.h - BASE_H, origin.y + p.y - start.y));
      el.style.left = `${item.x}px`;
      el.style.top = `${item.y}px`;
      renderEdges();
    };
    el.onpointerup = () => {
      el.onpointermove = null;
      el.onpointerup = null;
      if (moved) savePositions();
    };
  }

  function portPointerDown(event, port) {
    if (event.button !== 0) return;
    event.stopPropagation();
    event.preventDefault();
    const fromId = port.dataset.from;
    const from = fromId === TRIGGER ? TRIGGER : memberById(fromId);
    const branch = port.dataset.port || '';
    const a = outPoint(from, branch);
    const draft = $('edge-draft');
    draft.classList.remove('hidden');
    $('board').classList.add('connecting');
    const move = e => {
      const p = canvasPoint(e);
      draft.setAttribute('d', edgePath(a, p));
      document.querySelectorAll('#nodes .node.drop').forEach(n => n.classList.remove('drop'));
      const over = document.elementFromPoint(e.clientX, e.clientY)?.closest('#nodes .node');
      if (over && over.dataset.id !== fromId && over.dataset.id !== TRIGGER) over.classList.add('drop');
    };
    const up = e => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      draft.classList.add('hidden');
      $('board').classList.remove('connecting');
      document.querySelectorAll('#nodes .node.drop').forEach(n => n.classList.remove('drop'));
      const over = document.elementFromPoint(e.clientX, e.clientY);
      const target = over && over.closest('#nodes .node');
      if (target && target.dataset.id !== fromId && target.dataset.id !== TRIGGER) { connect(fromId, target.dataset.id, branch); return; }
      if (over && over.closest('#board') && !over.closest('.wf-run, .wf-zoom')) {
        // Dropped on empty canvas: add the next node right there.
        openCreator({ from: fromId, branch, at: canvasPoint(e) });
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  /* ── Connections ─────────────────────────────────── */
  function isAncestor(ancestorId, member, seen = new Set()) {
    if (!member || seen.has(member.id)) return false;
    seen.add(member.id);
    return parentsOf(member).some(p => p.id === ancestorId || isAncestor(ancestorId, p, seen));
  }

  function connect(fromId, toId, branch = '') {
    const to = memberById(toId);
    if (!to) return;
    if (fromId === TRIGGER) {
      // From the trigger means "run first": a start node waits for nobody.
      if (to.id === S.reviewer) { toast('Reviewer selalu berjalan setelah Lead.'); return; }
      to.depends_on = [];
      to.branch = '';
      afterChange();
      toast(`${to.name} jadi langkah pertama.`);
      return;
    }
    const from = memberById(fromId);
    if (!from) return;
    if (to.id === S.reviewer) { toast('Reviewer tidak bisa disambung: ia selalu memeriksa jawaban Lead.'); return; }
    if (from.id === to.id || isAncestor(to.id, from)) { toast('Sambungan ini membuat putaran. Alur harus berjalan maju.'); return; }
    const deps = new Set(to.depends_on || []);
    if (deps.has(from.id) && (!isBranching(from) || to.branch === branch)) { toast('Sudah tersambung.'); return; }
    deps.add(from.id);
    to.depends_on = [...deps];
    to.branch = isBranching(from) ? branch : (branchParent(to) ? to.branch : '');
    afterChange();
  }

  function removeEdge(fromId, toId) {
    const to = memberById(toId);
    if (!to) return;
    to.depends_on = (to.depends_on || []).filter(id => id !== fromId);
    if (!branchParent(to)) to.branch = '';
    S.selectedEdge = null;
    afterChange();
  }

  function afterChange() {
    markDirty();
    render();
    if (S.ndv) renderNdv();
  }

  /* ── Adding and removing nodes ───────────────────── */
  function addNode(kind, pending = {}) {
    if (S.members.length >= MAX_NODES) { toast(`Maksimal ${MAX_NODES} node.`); return null; }
    if (kind === 'agent' && S.members.filter(m => kindOf(m) === 'agent').length >= MAX_AGENTS) { toast(`Maksimal ${MAX_AGENTS} agent.`); return null; }
    const preset = NODE_DEFAULTS[kind];
    const model = (S.members.find(m => m.model_id) || {}).model_id || (S.models[0] && S.models[0].id) || '';
    const parent = pending.from && pending.from !== TRIGGER ? memberById(pending.from) : (pending.from ? null : memberById(S.selected));
    const member = {
      id: newId(), kind, name: kind === 'agent' ? `AI Agent ${S.members.filter(m => kindOf(m) === 'agent').length + 1}` : preset.name,
      role: preset.role || preset.name, model_id: model, instructions: '', config: JSON.parse(JSON.stringify(preset.config)),
      depends_on: parent && parent.id !== S.reviewer ? [parent.id] : [],
      branch: parent && isBranching(parent) ? (pending.branch || 'A') : '',
    };
    const anchor = parent ? outPoint(parent, member.branch) : pending.from === TRIGGER ? outPoint(TRIGGER) : null;
    if (pending.at) { member.x = pending.at.x; member.y = pending.at.y - BASE_H / 2; }
    else if (anchor) { member.x = anchor.x + COL - NODE_W; member.y = anchor.y - BASE_H / 2; }
    else {
      const right = Math.max(S.trigger.x, ...S.members.map(m => m.x));
      member.x = right + COL;
      member.y = ORIGIN.y;
    }
    // Keep clear of a node already sitting there.
    while (S.members.some(m => Math.abs(m.x - member.x) < NODE_W - 20 && Math.abs(m.y - member.y) < BASE_H)) member.y += ROW;
    S.members.push(member);
    if (!S.lead && kind === 'agent') S.lead = defaultLead();
    selectNode(member.id);
    afterChange();
    savePositions();
    return member;
  }

  function removeNode(id) {
    if (id === TRIGGER) { toast('Pemicu tidak bisa dihapus. Ubah ke Manual bila tidak perlu jadwal.'); return; }
    const member = memberById(id);
    if (!member) return;
    if (S.members.length <= 2) { toast('Alur butuh minimal 2 node.'); return; }
    S.members = S.members.filter(m => m.id !== id);
    S.members.forEach(m => {
      m.depends_on = (m.depends_on || []).filter(dep => dep !== id);
      if (!branchParent(m)) m.branch = '';
    });
    if (S.reviewer === id) S.reviewer = null;
    if (S.lead === id) S.lead = defaultLead() || (S.members.find(m => kindOf(m) === 'agent') || {}).id || null;
    if (S.ndv === id) closeNdv();
    selectNode(null);
    afterChange();
  }

  /* ── Right-click menus ───────────────────────────── */
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast('Disalin.'); } catch { toast('Tidak bisa menyalin. Salin dari kotak hasilnya.'); }
  }

  function renameNode(id) {
    const member = memberById(id);
    if (!member) return;
    const name = prompt('Nama node:', member.name || '');
    if (name === null || !name.trim()) return;
    member.name = name.trim().slice(0, 60);
    if (kindOf(member) !== 'agent') member.role = member.name;
    markDirty();
    refreshNodeCard(member);
    if (S.ndv === id) renderNdv();
  }

  function duplicateNode(id) {
    const source = memberById(id);
    if (!source) return;
    if (S.members.length >= MAX_NODES) { toast(`Maksimal ${MAX_NODES} node.`); return; }
    if (kindOf(source) === 'agent' && S.members.filter(m => kindOf(m) === 'agent').length >= MAX_AGENTS) { toast(`Maksimal ${MAX_AGENTS} agent.`); return; }
    const copy = JSON.parse(JSON.stringify(source));
    copy.id = newId();
    copy.name = `${source.name || 'Node'} (salinan)`.slice(0, 60);
    if (kindOf(copy) !== 'agent') copy.role = copy.name;
    copy.x = source.x + 36;
    copy.y = source.y + ROW;
    while (S.members.some(m => Math.abs(m.x - copy.x) < NODE_W - 20 && Math.abs(m.y - copy.y) < BASE_H)) copy.y += ROW;
    S.members.push(copy);
    selectNode(copy.id);
    afterChange();
    savePositions();
  }

  /* Whether an agent is a plain worker, the lead (writes the final answer) or the reviewer. */
  function setPosition(id, value) {
    const member = memberById(id);
    if (!member) return;
    if (S.lead === id) S.lead = null;
    if (S.reviewer === id) S.reviewer = null;
    if (value === 'lead') S.lead = id;
    if (value === 'reviewer') { S.reviewer = id; member.depends_on = []; }
    if (!S.lead) S.lead = defaultLead();
    if (S.lead === S.reviewer) S.lead = S.members.find(m => m.id !== S.reviewer && kindOf(m) === 'agent')?.id || null;
    afterChange();
  }

  function nodeMenu(id) {
    const member = memberById(id);
    if (!member) return null;
    const items = [
      { label: 'Edit node…', hint: 'Enter', run: () => openNdv(id) },
      { label: 'Ganti nama…', run: () => renameNode(id) },
      { label: 'Duplikat', run: () => duplicateNode(id) },
      { separator: true },
      { label: 'Tambah node setelah ini…', run: () => openCreator({ from: id, branch: isBranching(member) ? 'A' : '' }) },
    ];
    if (kindOf(member) === 'agent') {
      items.push({ separator: true }, { heading: 'Posisi di alur' },
        { label: 'Agent biasa', disabled: id !== S.lead && id !== S.reviewer ? 'Sudah agent biasa' : false, run: () => setPosition(id, 'worker') },
        { label: 'Lead (menulis jawaban akhir)', disabled: id === S.lead ? 'Sudah Lead' : false, run: () => setPosition(id, 'lead') },
        { label: 'Reviewer (memeriksa jawaban Lead)', disabled: id === S.reviewer ? 'Sudah Reviewer' : false, run: () => setPosition(id, 'reviewer') });
    }
    const parents = parentsOf(member);
    if (parents.length) {
      items.push({ separator: true }, { heading: 'Putus sambungan' });
      parents.forEach(parent => items.push({ label: `dari ${parent.name || 'node'}`, run: () => removeEdge(parent.id, id) }));
    }
    items.push({ separator: true },
      { label: 'Hapus node', danger: true, hint: 'Del', run: () => { if (confirm(`Hapus node "${member.name}"?`)) removeNode(id); } });
    return items;
  }

  const triggerMenu = () => [
    { label: 'Atur pemicu…', hint: 'Enter', run: () => openNdv(TRIGGER) },
    { label: 'Tambah node pertama…', run: () => openCreator({ from: TRIGGER }) },
  ];

  function edgeMenu(fromId, toId) {
    const from = fromId === TRIGGER ? { name: 'Pemicu' } : memberById(fromId);
    const to = memberById(toId);
    return [
      { heading: `${from ? from.name : '?'} → ${to ? to.name : '?'}` },
      { label: 'Putus sambungan', danger: true, hint: 'Del', run: () => removeEdge(fromId, toId) },
    ];
  }

  function boardMenu(point) {
    return [
      { label: 'Tambah node di sini…', hint: 'Tab', run: () => openCreator(point ? { at: point } : {}) },
      { separator: true },
      { label: 'Muat semua node', hint: '1', run: fitView },
      { label: 'Zoom 100%', hint: '0', run: () => { V.k = 1; applyView(); } },
      { label: 'Rapikan tata letak', run: () => $('tidy-board').click() },
      { separator: true },
      { label: 'Simpan alur', hint: 'Ctrl+S', disabled: S.dirty ? false : 'Tidak ada perubahan', run: () => saveTeam().then(() => toast('Tersimpan.')).catch(e => toast(e.message)) },
      { label: 'Jalankan alur', disabled: $('run').disabled ? 'Alur sedang berjalan' : false, run: () => $('run').click() },
    ];
  }

  /* ── Node creator (the + panel) ──────────────────── */
  function openCreator(pending = {}) {
    S.pending = pending;
    $('creator').classList.remove('hidden');
    $('creator-search').value = '';
    renderCreator('');
    setTimeout(() => $('creator-search').focus(), 30);
  }
  function closeCreator() {
    $('creator').classList.add('hidden');
    S.pending = null;
  }
  function renderCreator(query) {
    const q = query.trim().toLowerCase();
    const match = item => !q || `${item.label} ${item.desc} ${item.kind}`.toLowerCase().includes(q);
    const from = S.pending && S.pending.from && S.pending.from !== TRIGGER ? memberById(S.pending.from) : null;
    const groups = CATALOG.map(group => ({ ...group, items: group.items.filter(match) })).filter(g => g.items.length);
    const triggers = TRIGGERS.filter(t => !q || `${t.label} ${t.desc} pemicu trigger jadwal`.toLowerCase().includes(q));
    $('creator-context').textContent = from ? `Disambung setelah ${from.name}${S.pending.branch ? `, jalur ${S.pending.branch}` : ''}` : 'Pilih node untuk ditambahkan';
    $('creator-list').innerHTML = groups.map(group => `
      <div class="creator-group"><h4>${esc(group.label)}</h4>
        ${group.items.map(item => `<button class="creator-item" data-kind="${item.kind}" data-cat="${group.key}" type="button">
          <span class="node-icon">${nodeIcon(item.kind, 18)}</span><span><b>${esc(item.label)}</b><small>${esc(item.desc)}</small></span></button>`).join('')}
      </div>`).join('') + (triggers.length && !from ? `
      <div class="creator-group"><h4>Pemicu</h4>
        ${triggers.map(t => `<button class="creator-item" data-trigger="${t.mode}" data-cat="trigger" type="button">
          <span class="node-icon">${nodeIcon('trigger', 18)}</span><span><b>${esc(t.label)}</b><small>${esc(t.desc)}</small></span></button>`).join('')}
      </div>` : '') || '<p class="hint">Tidak ada node yang cocok.</p>';
    $('creator-list').querySelectorAll('[data-kind]').forEach(button => {
      button.onclick = () => {
        const member = addNode(button.dataset.kind, S.pending || {});
        closeCreator();
        if (member) openNdv(member.id);
      };
    });
    $('creator-list').querySelectorAll('[data-trigger]').forEach(button => {
      button.onclick = () => {
        S.schedule = { ...(S.schedule || {}), mode: button.dataset.trigger };
        closeCreator();
        markDirty();
        render();
        openNdv(TRIGGER);
      };
    });
  }
  $('creator-search').oninput = e => renderCreator(e.target.value);
  $('creator-search').onkeydown = e => {
    if (e.key === 'Enter') { const first = $('creator-list').querySelector('.creator-item'); if (first) first.click(); }
    if (e.key === 'Escape') closeCreator();
  };
  $('creator-close').onclick = closeCreator;
  $('open-creator').onclick = () => openCreator({});

  /* ── Node detail view (Input | Parameter | Output) ─ */
  function openNdv(id) {
    if (id !== TRIGGER && !memberById(id)) return;
    S.ndv = id;
    selectNode(id);
    $('ndv').classList.remove('hidden');
    S.ndvTab = 'params';
    renderNdv();
    if (id !== TRIGGER && kindOf(memberById(id)) === 'agent') refreshSources();
  }
  function closeNdv() {
    S.ndv = null;
    $('ndv').classList.add('hidden');
    render();
  }

  function renderNdv() {
    const id = S.ndv;
    if (!id) return;
    const isTrigger = id === TRIGGER;
    const member = isTrigger ? null : memberById(id);
    if (!isTrigger && !member) { closeNdv(); return; }
    const kind = isTrigger ? 'trigger' : kindOf(member);
    $('ndv-icon').innerHTML = nodeIcon(kind, 22);
    $('ndv-icon').dataset.cat = isTrigger ? 'trigger' : CATEGORY[kind] || 'ai';
    $('ndv-kind').textContent = isTrigger ? 'Pemicu' : roleLabel(member);
    $('ndv-title').textContent = isTrigger ? 'Kapan alur ini berjalan' : member.name || 'Tanpa nama';
    $('ndv-remove').classList.toggle('hidden', isTrigger);
    $('ndv-input').innerHTML = isTrigger ? triggerInput() : inputHtml(member);
    $('ndv-output').innerHTML = isTrigger ? triggerOutput() : outputHtml(member);
    enhanceCode($('ndv-output'));
    enhanceCode($('ndv-input'));
    if (isTrigger) renderTrigger($('ndv-params'));
    else renderParams($('ndv-params'), member);
    document.querySelectorAll('#ndv-tabs button').forEach(b => b.classList.toggle('on', b.dataset.pane === S.ndvTab));
    $('ndv').dataset.pane = S.ndvTab;
  }

  const runState = id => (S.run && S.run.agents ? S.run.agents[id] : null);
  const preview = text => (text ? `<div class="io-text prose">${md(String(text).slice(0, 20000))}</div>` : '');

  function inputHtml(member) {
    if (!S.run) return '<p class="io-empty">Jalankan alur untuk melihat data yang masuk ke node ini.</p>';
    const parents = parentsOf(member);
    if (!parents.length) {
      if (member.id === S.lead) {
        const workers = S.members.filter(m => m.id !== member.id && runState(m.id) && runState(m.id).content);
        if (workers.length) return workers.map(w => `<div class="io-block"><div class="io-head">dari ${esc(w.name)}</div>${preview(runState(w.id).content)}</div>`).join('');
      }
      return `<div class="io-block"><div class="io-head">Tugas</div>${preview(S.run.task)}</div>`;
    }
    return parents.map(p => {
      const state = runState(p.id) || {};
      return `<div class="io-block"><div class="io-head">dari ${esc(p.name)} <span class="io-status" data-status="${esc(state.status || '')}">${esc(STATUS[state.status] || '')}</span></div>
        ${state.content ? preview(state.content) : '<p class="io-empty">Belum ada isi.</p>'}</div>`;
    }).join('');
  }

  function outputHtml(member) {
    const state = runState(member.id);
    if (!state) return '<p class="io-empty">Belum dijalankan. Tekan Jalankan alur di bawah kanvas.</p>';
    const facts = [
      STATUS[state.status] || state.status,
      state.branch ? `jalur ${state.branch}` : '',
      state.completed_at && state.started_at ? formatSeconds((state.completed_at - state.started_at) * 1000) : '',
      state.tokens ? `${formatTokens(state.tokens)} token` : '',
    ].filter(Boolean).join(' · ');
    return `<div class="io-head"><span class="io-status" data-status="${esc(state.status)}">${esc(facts)}</span></div>
      ${state.error ? `<div class="notice">${esc(state.error)}</div>` : ''}
      ${state.note ? `<p class="hint">${esc(state.note)}</p>` : ''}
      ${member.id === S.lead && S.run.result ? preview(S.run.result) : preview(state.content) || '<p class="io-empty">Tidak ada isi.</p>'}`;
  }

  function triggerInput() {
    return `<div class="io-block"><div class="io-head">Pemicu</div><p>${esc(triggerLabel())}</p></div>
      <p class="hint">Saat dijalankan manual, isi kotak "Tugas" di bawah kanvas menjadi input pertama.</p>`;
  }
  function triggerOutput() {
    if (!S.run) return '<p class="io-empty">Belum ada eksekusi.</p>';
    return `<div class="io-block"><div class="io-head">Tugas yang dikirim ke langkah pertama</div>${preview(S.run.task)}</div>`;
  }

  /* ── Parameters ──────────────────────────────────── */
  function dayChecks(selected, attribute) {
    const chosen = selected && selected.length ? selected : [0, 1, 2, 3, 4, 5, 6];
    return `<div class="day-row">${DAYS.map((day, index) =>
      `<label class="check"><input type="checkbox" ${attribute}="${index}" ${chosen.includes(index) ? 'checked' : ''}>${day}</label>`).join('')}</div>`;
  }
  const readDays = (box, attribute) => [...box.querySelectorAll(`[${attribute}]`)].filter(c => c.checked).map(c => Number(c.getAttribute(attribute)));

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
        <label class="field"><select ${attr} data-key="mode" aria-label="${prefix}">${options}</select></label>
        ${body}
      </div>`;
  }

  function credentialNote(ready, what) {
    return ready ? `<p class="hint ok">${what} sudah tersambung.</p>`
      : `<p class="hint hint-warn">${what} belum diatur. <button class="link-btn" type="button" data-go-creds>Buka Kredensial</button></p>`;
  }

  function kindFields(member) {
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
          <p class="hint">Terpenuhi lewat jalur <b>benar</b>, tidak terpenuhi lewat jalur <b>salah</b>. Syarat tanpa AI dicek dulu, jadi AI sering tidak perlu ditanya.</p>`;
      }
      case 'switch': {
        const rules = config.rules || [];
        const needsModel = rules.some(c => c.mode === 'ai');
        return `${rules.map((c, i) => conditionFields(c, i, 'rules')).join('')}
          ${rules.length < 3 ? '<button class="btn btn-ghost btn-small btn-block" type="button" data-cond-add="rules">+ Aturan</button>' : ''}
          ${needsModel ? '<label class="field"><span>Model untuk aturan AI</span><select data-field="model_id"></select></label>' : ''}
          <p class="hint">Aturan pertama yang cocok memilih jalurnya (A, B, C). Selain itu lewat jalur <b>lainnya</b>.</p>`;
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
          <div class="chips-row">${['input', 'task', 'date', 'time', 'day', 'team'].map(v => `<button class="chip-btn" type="button" data-insert="{{${v}}}">{{${v}}}</button>`).join('')}</div>
          <p class="hint">Klik untuk menyisipkan. {{node.ID}} mengambil isi node lain.</p>`;
      case 'http': {
        const method = config.method || 'GET';
        return `<label class="field"><span>Metode</span><select data-config="method">
              <option ${method === 'GET' ? 'selected' : ''}>GET</option><option ${method === 'POST' ? 'selected' : ''}>POST</option>
            </select></label>
          <label class="field"><span>URL</span><input data-config="url" value="${esc(config.url || '')}" placeholder="https://..." maxlength="2000"></label>
          ${method === 'POST' ? `<label class="field"><span>Isi (JSON atau teks)</span><textarea data-config="body" rows="4" maxlength="8000">${esc(config.body || '')}</textarea></label>` : ''}
          <p class="hint">Hanya alamat publik. Balasannya diteruskan ke node berikutnya. Isi bisa memakai {{input}} dan {{task}}.</p>`;
      }
      case 'telegram': {
        const telegram = (S.integrations || {}).telegram || {};
        return `${credentialNote(telegram.ready, 'Bot Telegram')}
          <label class="field"><span>Chat ID</span><input data-config="chat_id" value="${esc(config.chat_id || '')}" placeholder="${telegram.default_chat ? 'Kosong = chat dari Kredensial' : 'Angka atau @nama_channel'}" maxlength="70"></label>
          <button id="test-telegram" class="btn btn-ghost btn-small btn-block" type="button" ${telegram.ready ? '' : 'disabled'}>Kirim pesan tes</button>`;
      }
      case 'database': {
        const options = (S.integrations || {}).databases || [{ id: 'sqlite', label: 'SQLite (file lokal)', ready: true }];
        const chosen = options.find(o => o.id === (config.database || 'sqlite')) || options[0];
        return `<label class="field"><span>Database</span><select data-config="database">${options.map(o =>
            `<option value="${esc(o.id)}" ${o.id === chosen.id ? 'selected' : ''}>${esc(o.label)}${o.ready ? '' : ' (belum siap)'}</option>`).join('')}</select></label>
          ${chosen.reason ? `<p class="hint hint-warn">${esc(chosen.reason)} <button class="link-btn" type="button" data-go-creds>Buka Kredensial</button></p>` : ''}
          <label class="field"><span>Nama tabel</span><input data-config="table" value="${esc(config.table || 'hasil_tim')}" maxlength="63"></label>`;
      }
      default: return '';
    }
  }

  const ACCESS = [
    ['', 'Tidak ada akses'], ['read', 'Baca saja'], ['append', 'Tambah baris saja'],
    ['read+append', 'Baca dan tambah baris'], ['write', 'Baca dan ubah semua'],
  ];

  /* Which data sources an agent may touch, and how. Nothing is allowed until it is chosen here. */
  function grantsHtml(member) {
    const grants = (member.config || {}).data || [];
    const sources = S.dataSources || [];
    const known = new Set(sources.map(x => x.name));
    const rows = [
      ...sources.map(x => ({ name: x.name, label: x.label || x.name, online: x.online })),
      ...grants.filter(g => !known.has(g.source)).map(g => ({ name: g.source, label: g.source, missing: true })),
    ];
    if (!rows.length) return '<p class="hint">Belum ada sumber data. <button class="link-btn" type="button" data-go-data>Tambah di tab Data</button></p>';
    return `<div class="grants">${rows.map(row => {
      const grant = grants.find(g => g.source === row.name) || {};
      return `<div class="grant" data-grant="${esc(row.name)}">
        <b>${esc(row.label)}${row.missing ? ' <small>(sumber ini sudah tidak ada)</small>' : ''}</b>
        <select data-grant-access aria-label="Akses ke ${esc(row.label)}">${ACCESS.map(([value, label]) =>
          `<option value="${value}" ${(grant.access || '') === value ? 'selected' : ''}>${label}</option>`).join('')}</select>
        ${grant.access ? `<input data-grant-hide value="${esc((grant.hide || []).join(', '))}" placeholder="Kolom yang disembunyikan, pisahkan dengan koma" aria-label="Kolom yang disembunyikan dari ${esc(row.label)}" maxlength="400">` : ''}
      </div>`;
    }).join('')}</div>
      <p class="hint">Agent hanya menyentuh sumber yang diizinkan di sini. Pada bot pelanggan, hak ubah dan hapus tidak pernah berlaku, dan kolom berawalan <code>_</code> selalu tersembunyi.</p>`;
  }

  function renderParams(box, member) {
    const isAgent = kindOf(member) === 'agent';
    const isWorker = member.id !== S.lead && member.id !== S.reviewer;
    const others = S.members.filter(m => m.id !== member.id && m.id !== S.reviewer);
    const fork = branchParent(member);
    box.innerHTML = `
      <label class="field"><span>Nama</span><input data-field="name" value="${esc(member.name)}" maxlength="60"></label>
      ${isAgent ? `
        <label class="field"><span>Peran</span><input data-field="role" value="${esc(member.role)}" maxlength="120" placeholder="Misalnya: peneliti, penulis, analis data"></label>
        <label class="field"><span>Model</span><select data-field="model_id"></select></label>
        <label class="field"><span>Instruksi</span><textarea data-field="instructions" rows="5" maxlength="4000" placeholder="Apa yang harus dilakukan agent ini, dan bentuk hasilnya.">${esc(member.instructions)}</textarea></label>
        <label class="check"><input type="checkbox" data-direct ${(member.config || {}).direct ? 'checked' : ''}>Jawab langsung seperti chat, tanpa laporan Hasil dan Bukti</label>
        <div class="field"><span>Data yang boleh dipakai</span>${grantsHtml(member)}</div>
        <div class="field"><span>Posisi di alur</span>
          <div class="seg-choice">
            <label><input type="radio" name="position" value="worker" ${isWorker ? 'checked' : ''}><span>Agent</span></label>
            <label><input type="radio" name="position" value="lead" ${member.id === S.lead ? 'checked' : ''}><span>Lead</span></label>
            <label><input type="radio" name="position" value="reviewer" ${member.id === S.reviewer ? 'checked' : ''}><span>Reviewer</span></label>
          </div>
          <p class="hint">Lead menulis jawaban akhir dari hasil semua agent. Reviewer memeriksanya dan bisa meminta revisi.</p>
        </div>` : kindFields(member)}
      <details class="field more" ${member.id === S.reviewer ? '' : 'open'}>
        <summary>Sambungan</summary>
        ${member.id !== S.reviewer && others.length ? `<div class="field"><span>Masuk dari</span>
          ${others.map(o => `<label class="check"><input type="checkbox" data-after="${esc(o.id)}" ${(member.depends_on || []).includes(o.id) ? 'checked' : ''}>${esc(o.name)}</label>`).join('')}
          <p class="hint">Atau seret titik keluaran node lain ke node ini di kanvas.</p></div>` : '<p class="hint">Reviewer selalu memeriksa jawaban Lead.</p>'}
        ${fork ? `<div class="field"><span>Jalur dari ${esc(fork.name)}</span>
          ${(kindOf(fork) === 'switch'
            ? [['A', 'aturan A cocok'], ['B', 'aturan B cocok'], ['C', 'aturan C cocok'], ['D', 'tidak ada yang cocok']]
            : [['A', 'benar'], ['B', 'salah']]
          ).map(([value, label]) => `<label class="check"><input type="radio" name="branch" value="${value}" ${member.branch === value ? 'checked' : ''}>${kindOf(fork) === 'switch' ? `Jalur ${value}, ` : 'Jalur '}${label}</label>`).join('')}
          <label class="check"><input type="radio" name="branch" value="" ${!member.branch ? 'checked' : ''}>Semua jalur</label>
        </div>` : ''}
      </details>`;
    bindParams(box, member);
  }

  function refreshNodeCard(member) {
    const node = document.querySelector(`#nodes .node[data-id="${CSS.escape(member.id)}"]`);
    if (!node) return;
    node.querySelector('.node-name').textContent = member.name || 'Tanpa nama';
    node.querySelector('.node-sub').textContent = nodeDetail(member);
    if (S.ndv === member.id) $('ndv-title').textContent = member.name || 'Tanpa nama';
  }

  function bindParams(box, member) {
    box.querySelectorAll('[data-field="model_id"]').forEach(select => fillModelOptions(select, member.model_id, false));
    box.querySelectorAll('[data-field]').forEach(input => {
      input[input.tagName === 'SELECT' ? 'onchange' : 'oninput'] = () => {
        member[input.dataset.field] = input.value;
        if (input.dataset.field === 'name' && kindOf(member) !== 'agent') member.role = input.value;
        markDirty();
        refreshNodeCard(member);
      };
    });
    box.querySelectorAll('[data-config]').forEach(input => {
      const rerenders = ['mode', 'database', 'combine', 'method'].includes(input.dataset.config);
      input[rerenders ? 'onchange' : 'oninput'] = () => {
        member.config = { ...(member.config || {}), [input.dataset.config]: input.type === 'number' ? Number(input.value) : input.value };
        markDirty();
        refreshNodeCard(member);
        if (rerenders) renderParams(box, member);
      };
    });
    box.querySelectorAll('[data-insert]').forEach(button => {
      button.onclick = () => {
        const area = box.querySelector('[data-config="text"]');
        const at = area.selectionStart ?? area.value.length;
        area.value = area.value.slice(0, at) + button.dataset.insert + area.value.slice(area.selectionEnd ?? at);
        area.dispatchEvent(new Event('input'));
        area.focus();
      };
    });
    /* Conditions are edited in place; an old single-condition gate becomes a list on first edit. */
    const listOf = key => {
      const config = member.config || {};
      const list = key === 'conditions' ? conditionsOf(config).map(c => ({ ...c })) : (config.rules || []).map(c => ({ ...c }));
      if (key === 'conditions') { delete config.mode; delete config.value; delete config.start; delete config.end; delete config.days; }
      return list;
    };
    const saveList = (key, list) => { member.config = { ...(member.config || {}), [key]: list }; markDirty(); refreshNodeCard(member); };
    box.querySelectorAll('[data-cond]').forEach(input => {
      const rerenders = input.dataset.key === 'mode';
      input[rerenders ? 'onchange' : 'oninput'] = () => {
        const key = input.dataset.cond;
        const list = listOf(key);
        const index = Number(input.dataset.index);
        list[index] = { ...list[index], [input.dataset.key]: input.value };
        if (rerenders && input.value === 'time') list[index] = { start: '07:00', end: '22:00', ...list[index] };
        saveList(key, list);
        if (rerenders) renderParams(box, member);
      };
    });
    box.querySelectorAll('[data-cond-days]').forEach(wrap => {
      const [key, index] = wrap.dataset.condDays.split(':');
      wrap.querySelectorAll('[data-cday]').forEach(check => {
        check.onchange = () => { const list = listOf(key); list[Number(index)].days = readDays(wrap, 'data-cday'); saveList(key, list); };
      });
    });
    box.querySelectorAll('[data-cond-add]').forEach(button => {
      button.onclick = () => { const key = button.dataset.condAdd; saveList(key, [...listOf(key), { mode: 'contains', value: '' }]); renderParams(box, member); };
    });
    box.querySelectorAll('[data-cond-remove]').forEach(button => {
      button.onclick = () => {
        const key = button.dataset.condRemove;
        const list = listOf(key);
        if (list.length <= 1) { toast('Minimal satu syarat.'); return; }
        list.splice(Number(button.dataset.index), 1);
        saveList(key, list);
        renderParams(box, member);
      };
    });
    const testButton = box.querySelector('#test-telegram');
    if (testButton) {
      testButton.onclick = async () => {
        testButton.disabled = true;
        try { await post('/api/teams/telegram/test', { chat_id: (member.config || {}).chat_id || '' }); toast('Pesan tes terkirim.'); } catch (e) { toast(e.message); }
        testButton.disabled = false;
      };
    }
    box.querySelectorAll('input[name="position"]').forEach(radio => { radio.onchange = () => setPosition(member.id, radio.value); });
    box.querySelectorAll('[data-after]').forEach(check => {
      check.onchange = () => {
        if (check.checked) { connect(check.dataset.after, member.id, isBranching(memberById(check.dataset.after)) ? 'A' : ''); return; }
        removeEdge(check.dataset.after, member.id);
      };
    });
    box.querySelectorAll('input[name="branch"]').forEach(radio => {
      radio.onchange = () => { member.branch = radio.value; afterChange(); };
    });
    box.querySelectorAll('[data-go-creds]').forEach(button => { button.onclick = () => { closeNdv(); showTab('creds'); }; });
    box.querySelectorAll('[data-go-data]').forEach(button => { button.onclick = () => { closeNdv(); showTab('data'); }; });
    box.querySelectorAll('[data-direct]').forEach(check => {
      check.onchange = () => {
        member.config = { ...(member.config || {}) };
        if (check.checked) member.config.direct = true; else delete member.config.direct;
        markDirty();
      };
    });
    const saveGrants = () => {
      const data = [...box.querySelectorAll('[data-grant]')].map(row => ({
        source: row.dataset.grant,
        access: row.querySelector('[data-grant-access]').value,
        hide: (row.querySelector('[data-grant-hide]')?.value || '').split(',').map(x => x.trim()).filter(Boolean),
      })).filter(grant => grant.access);
      member.config = { ...(member.config || {}) };
      if (data.length) member.config.data = data; else delete member.config.data;
      markDirty();
    };
    box.querySelectorAll('[data-grant-access]').forEach(select => { select.onchange = () => { saveGrants(); renderParams(box, member); }; });
    box.querySelectorAll('[data-grant-hide]').forEach(input => { input.oninput = saveGrants; });
  }

  function renderTrigger(box) {
    const schedule = S.schedule || { mode: 'off' };
    const mode = schedule.mode || 'off';
    const telegram = (S.integrations || {}).telegram || {};
    const audience = schedule.audience === 'public' ? 'public' : 'owner';
    const times = (schedule.times || [schedule.time || '07:00']).join(', ');
    box.innerHTML = `
      <div class="trigger-grid">${TRIGGERS.map(t => `<label class="trigger-card ${mode === t.mode ? 'on' : ''}">
          <input type="radio" name="trigger-mode" value="${t.mode}" ${mode === t.mode ? 'checked' : ''}><b>${esc(t.label)}</b><small>${esc(t.desc)}</small></label>`).join('')}</div>
      ${mode === 'interval' ? `<label class="field"><span>Tiap (menit, minimal 5)</span><input id="schedule-minutes" type="number" min="5" value="${esc(schedule.minutes || 60)}"></label>` : ''}
      ${mode === 'daily' ? `<label class="field"><span>Jam, pisahkan dengan koma</span><input id="schedule-time" value="${esc(times)}" placeholder="05:00, 12:00, 17:00"></label>
        <div class="field"><span>Hari</span>${dayChecks(schedule.days, 'data-schedule-day')}</div>` : ''}
      ${mode === 'cron' ? `<label class="field"><span>Ekspresi cron (menit jam tanggal bulan hari)</span><input id="schedule-cron" value="${esc(schedule.cron || '0 7 * * 1-5')}" maxlength="100"></label>
        <p class="hint">Contoh: <code>0 7 * * 1-5</code> jam 07:00 Senin–Jumat, <code>*/30 8-17 * * *</code> tiap 30 menit di jam kerja.</p>` : ''}
      ${mode === 'once' ? `<label class="field"><span>Tanggal dan jam</span><input id="schedule-at" type="datetime-local" value="${esc((schedule.at || '').slice(0, 16))}"></label>` : ''}
      ${mode === 'telegram' ? `${credentialNote(telegram.ready && telegram.default_chat, 'Bot dan chat Telegram')}
        <div class="field"><span>Siapa yang boleh menulis ke bot</span>
          <div class="seg-choice">
            <label><input type="radio" name="tg-audience" value="owner" ${audience === 'owner' ? 'checked' : ''}><span>Hanya saya</span></label>
            <label><input type="radio" name="tg-audience" value="public" ${audience === 'public' ? 'checked' : ''}><span>Pelanggan (publik)</span></label>
          </div></div>
        <label class="field"><span>Kata pemicu (kosongkan untuk semua pesan)</span><input id="schedule-keyword" value="${esc(schedule.keyword || '')}" placeholder="${audience === 'public' ? '' : '/jadwal'}" maxlength="33"></label>
        ${audience === 'public' ? `
          <div class="trigger-grid two">
            <label class="field"><span>Riwayat percakapan (giliran per pelanggan)</span><input id="schedule-history" type="number" min="0" max="20" value="${esc(schedule.history ?? 6)}"></label>
            <label class="field"><span>Batas balasan otomatis per hari</span><input id="schedule-cap" type="number" min="1" max="5000" value="${esc(schedule.daily_cap ?? 300)}"></label>
          </div>
          <ul class="ready-list">
            <li class="${telegram.ready ? 'ok' : 'no'}">Token bot Telegram ${telegram.ready ? 'terisi' : 'belum diisi'}</li>
            <li class="${telegram.default_chat ? 'ok' : 'no'}">Chat ID kamu ${telegram.default_chat ? 'terisi, untuk kabar dan peringatan dari bot' : 'belum diisi, bot tidak bisa memberi kabar ke kamu'}</li>
            <li class="${(S.dataSources || []).length ? 'ok' : 'no'}">${(S.dataSources || []).length ? `${S.dataSources.length} sumber data siap dipakai agent` : 'Belum ada sumber data (katalog, pesanan)'}</li>
          </ul>
          <p class="hint">Hanya chat pribadi yang dilayani, dan tiap pelanggan dibatasi 6 pesan per menit. Agent yang menjawab hanya boleh membaca dan menambah baris pada data yang kamu izinkan di node-nya, tidak mengubah atau menghapus, dan hanya memakai alat yang aman. Gangguan dan permintaan yang butuh keputusanmu dikabarkan ke chat Telegram milikmu. Pesan yang menumpuk saat server mati lebih dari 15 menit diabaikan.</p>` : `
          <p class="hint">Hanya pesan dari chat di Kredensial yang menjalankan alur. Isi pesan menjadi tugasnya, dan node Telegram tanpa Chat ID membalas ke chat itu.</p>`}` : ''}
      ${mode !== 'off' ? `<label class="field"><span>${mode === 'telegram' ? 'Konteks tambahan (boleh kosong)' : 'Tugas yang dijalankan'}</span><textarea id="schedule-task" rows="4" maxlength="4000" placeholder="Misalnya: apa yang penting hari ini di Jakarta?">${esc(schedule.task || '')}</textarea></label>
        <p class="hint">Hanya berjalan selama server menyala, dengan jam mesin ini. Tekan Simpan agar berlaku.</p>` : '<p class="hint">Isi tugas di kotak bawah kanvas, lalu tekan Jalankan alur.</p>'}`;
    const value = id => (box.querySelector(`#${id}`) ? box.querySelector(`#${id}`).value : undefined);
    const update = () => {
      const timeText = value('schedule-time');
      const checked = box.querySelector('input[name="trigger-mode"]:checked');
      const before = S.schedule || {};
      S.schedule = {
        mode: checked ? checked.value : 'off',
        minutes: value('schedule-minutes') !== undefined ? Number(value('schedule-minutes')) : S.schedule.minutes,
        times: timeText !== undefined ? timeText.split(',').map(t => t.trim()).filter(Boolean) : S.schedule.times,
        days: box.querySelector('[data-schedule-day]') ? readDays(box, 'data-schedule-day') : S.schedule.days,
        cron: value('schedule-cron') ?? S.schedule.cron,
        at: value('schedule-at') ?? S.schedule.at,
        keyword: value('schedule-keyword') ?? S.schedule.keyword,
        task: value('schedule-task') ?? S.schedule.task,
      };
      if (S.schedule.times) S.schedule.time = S.schedule.times[0];
      if (S.schedule.mode === 'telegram') {
        const picked = box.querySelector('input[name="tg-audience"]:checked');
        if ((picked ? picked.value : before.audience) === 'public') {
          S.schedule.audience = 'public';
          S.schedule.history = value('schedule-history') !== undefined ? Number(value('schedule-history')) : (before.history ?? 6);
          S.schedule.daily_cap = value('schedule-cap') !== undefined ? Number(value('schedule-cap')) : (before.daily_cap ?? 300);
        }
      }
      markDirty();
      const card = document.querySelector(`#nodes .node[data-id="${TRIGGER}"]`);
      if (card) {
        card.querySelector('.node-name').textContent = TRIGGERS.find(t => t.mode === S.schedule.mode)?.label || 'Manual';
        card.querySelector('.node-sub').textContent = triggerLabel();
      }
    };
    box.querySelectorAll('input[name="trigger-mode"]').forEach(radio => { radio.onchange = () => { update(); renderTrigger(box); render(); }; });
    box.querySelectorAll('input:not([name="trigger-mode"]), textarea').forEach(input => { input.oninput = update; input.onchange = update; });
    box.querySelectorAll('input[name="tg-audience"]').forEach(radio => { radio.onchange = () => { update(); renderTrigger(box); render(); }; });
    box.querySelectorAll('[data-go-creds]').forEach(button => { button.onclick = () => { closeNdv(); showTab('creds'); }; });
  }

  $('ndv-close').onclick = closeNdv;
  $('ndv-remove').onclick = () => { if (S.ndv && confirm('Hapus node ini?')) removeNode(S.ndv); };
  $('ndv').onclick = e => { if (e.target === $('ndv')) closeNdv(); };
  document.querySelectorAll('#ndv-tabs button').forEach(button => {
    button.onclick = () => { S.ndvTab = button.dataset.pane; renderNdv(); };
  });

  /* ── Run ─────────────────────────────────────────── */
  function resetRunView() {
    $('result').classList.add('hidden');
    $('result-toggle').classList.add('hidden');
    $('run-info').textContent = '';
    $('run-info').classList.add('hidden');
    $('cancel').classList.add('hidden');
    $('run').disabled = false;
  }

  function paintRun() {
    document.querySelectorAll('#nodes .node').forEach(node => {
      if (node.dataset.id === TRIGGER) { node.dataset.status = S.run ? 'completed' : ''; return; }
      const agent = runState(node.dataset.id);
      node.dataset.status = agent ? agent.status : '';
      const state = node.querySelector('.node-state');
      if (!state) return;
      if (!agent) { state.textContent = ''; return; }
      const bits = [STATUS[agent.status] || agent.status];
      if (agent.status === 'running' && agent.activity) {
        const { verb, target } = describeTool(agent.activity.name, agent.activity.detail);
        bits[0] = `${verb} ${target}`.trim();
      }
      if (agent.branch && agent.status === 'completed') bits.push(`jalur ${agent.branch}`);
      if (agent.note && agent.status === 'completed' && !agent.branch) bits[0] = agent.note;
      if (agent.completed_at && agent.started_at && agent.status !== 'running') bits.push(formatSeconds((agent.completed_at - agent.started_at) * 1000));
      if (agent.tokens) bits.push(`${formatTokens(agent.tokens)} token`);
      state.textContent = bits.join(' · ') + (agent.error ? `: ${agent.error}` : '');
      node.title = agent.error || agent.note || '';
    });
    renderEdges();
  }

  function showRun(run) {
    S.run = run;
    paintRun();
    const active = !finished(run.status);
    const reviews = (run.reviews || []).map(r => r.verdict).join(', ');
    const elapsed = run.started_at ? ` · ${formatSeconds(((run.completed_at || Date.now() / 1000) - run.started_at) * 1000)}` : '';
    const info = $('run-info');
    info.textContent = `${RUN_STATUS[run.status] || run.status}${elapsed} · ${formatTokens(run.total_tokens || 0)} token${reviews ? ` · review: ${reviews}` : ''}`;
    info.dataset.status = run.status;
    info.classList.remove('hidden');
    $('cancel').classList.toggle('hidden', !active);
    $('run').disabled = active;
    const result = $('result');
    if (run.result || run.error) {
      result.innerHTML = run.error ? `<div class="notice" style="white-space: pre-line">${esc(run.error)}</div>${run.result ? md(run.result) : ''}` : md(run.result);
      enhanceCode(result);
      $('result-toggle').classList.remove('hidden');
      if (finished(run.status) && S.openResult !== false) result.classList.remove('hidden');
    } else {
      result.classList.add('hidden');
      $('result-toggle').classList.add('hidden');
    }
    if (S.ndv) renderNdv();
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
      if (data.type === 'run_finished') { stopEvents(); if (S.tab === 'runs') loadRuns(); }
    };
    S.events.onerror = () => { if (S.run && finished(S.run.status)) stopEvents(); };
  }

  $('run-form').onsubmit = async e => {
    e.preventDefault();
    const task = $('task').value.trim();
    if (!task) { $('task').focus(); toast('Tulis tugas atau input untuk alur ini dulu.'); return; }
    $('run').disabled = true;
    try {
      await saveTeam();
      S.openResult = true;
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
  $('result-toggle').onclick = () => {
    const hidden = $('result').classList.toggle('hidden');
    S.openResult = !hidden;
  };

  /* ── Teams: open, new, template, save, delete ────── */
  function renderTeamOptions() {
    $('team-select').innerHTML = `<option value="">+ Alur baru</option>${S.teams.map(t =>
      `<option value="${esc(t.id)}" ${t.id === S.team ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}`;
    $('team-select').value = S.team || '';
  }

  function loadInto(source, teamId) {
    stopEvents();
    S.run = null;
    S.team = teamId;
    S.lead = source.lead_id || null;
    S.reviewer = source.reviewer_id || null;
    S.selected = null;
    S.selectedEdge = null;
    S.schedule = source.schedule || { mode: 'off' };
    S.members = source.members.map(m => ({ ...m, depends_on: m.depends_on || [], config: m.config || {} }));
    $('team-video').checked = source.mode === 'video';
    $('team-name').value = source.name || '';
    if (S.ndv) closeNdv();
    resetRunView();
    autoLayout(teamId);
    renderTeamOptions();
    render();
    requestAnimationFrame(fitView);
    markDirty(false);
    if (S.tab === 'runs') loadRuns();
  }

  function blankTeam() {
    const model = S.model !== 'auto' && S.byId[S.model] ? S.model : (S.models[0] && S.models[0].id) || '';
    const lead = { id: newId(), name: 'Lead', role: 'Menyusun jawaban akhir', model_id: model, instructions: '', depends_on: [], kind: 'agent', config: {} };
    const researcher = { id: newId(), name: 'Peneliti', role: 'Mencari fakta dan sumber', model_id: model, instructions: '', depends_on: [], kind: 'agent', config: {} };
    loadInto({ name: '', members: [lead, researcher], lead_id: lead.id, schedule: { mode: 'off' } }, null);
  }

  const confirmLeave = () => !S.dirty || confirm('Perubahan alur ini belum disimpan. Tinggalkan?');

  function openTeam(id) {
    const team = S.teams.find(t => t.id === id);
    if (!team) { blankTeam(); return; }
    loadInto(team, team.id);
    showLatestRun(team.id);
  }

  async function showLatestRun(teamId) {
    try {
      const { runs } = await api(`/api/teams/${teamId}/runs`);
      if (S.team !== teamId || !runs.length) return;
      const latest = runs.reduce((a, b) => (b.created_at > a.created_at ? b : a));
      S.openResult = false;
      showRun(latest);
      if (!finished(latest.status)) watchRun(latest);
    } catch { /* the board still works without run history */ }
  }

  function useTemplate(key) {
    const template = S.templates.find(t => t.key === key);
    if (!template) return;
    loadInto(template, null);
    markDirty();
    toast(template.description);
    if (template.starter === 'shop') makeShopStarter(template);
  }

  /* A shop template needs a product list and an orders sheet. Make them if they are missing, and say so. */
  async function makeShopStarter(template) {
    try {
      const made = await credApi('/api/data/starter/shop', 'POST');
      await refreshSources();
      if (made.created.length) toast(`${template.description} Katalog dan Pesanan sudah dibuat di tab Data: isi dengan produkmu.`);
    } catch (e) { toast(`Template terpasang, tetapi data contohnya gagal dibuat: ${e.message}`); }
  }

  async function loadIntegrations() {
    try { S.integrations = await api('/api/teams/integrations'); } catch { /* the editor works without it */ }
    await refreshSources(false);
  }

  /* The data sources an agent can be given. Reloaded when the Data tab changes them. */
  async function refreshSources(rerender = true) {
    try {
      const data = await api('/api/data/sources');
      const before = JSON.stringify((S.dataSources || []).map(x => [x.name, x.label]));
      S.dataSources = data.sources;
      S.googleEmail = data.google_email || '';
      S.dataReady = data.ready || {};
      if (rerender && before !== JSON.stringify(S.dataSources.map(x => [x.name, x.label])) && S.ndv && S.ndv !== TRIGGER && !document.activeElement?.closest('#ndv-params')) renderNdv();
    } catch { /* the editor works without data sources */ }
  }

  async function loadTeams() {
    if (S.features.teams === false) return;
    try {
      const [teams, templates] = await Promise.all([api('/api/teams'), api('/api/teams/templates'), loadIntegrations()]);
      S.teams = teams.teams;
      S.templates = templates.templates;
      $('template-select').innerHTML = `<option value="">Mulai dari template…</option>${S.templates.map(t =>
        `<option value="${esc(t.key)}">${esc(t.name)}</option>`).join('')}`;
      renderTeamOptions();
      if (S.team === null && !S.members.length) blankTeam();
      else requestAnimationFrame(() => { if (S.trigger) { render(); fitView(); } });
    } catch (e) { toast(e.message); }
  }

  async function saveTeam() {
    const name = $('team-name').value.trim();
    if (!name) { $('team-name').focus(); throw new Error('Beri nama alur dulu.'); }
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
    savePositions();
    S.teams = (await api('/api/teams')).teams;
    renderTeamOptions();
    markDirty(false);
    return saved;
  }

  $('team-select').onchange = e => {
    if (!confirmLeave()) { renderTeamOptions(); return; }
    openTeam(e.target.value);
  };
  $('template-select').onchange = e => {
    const key = e.target.value;
    e.target.value = '';
    $('wf-menu').open = false;
    if (key && confirmLeave()) useTemplate(key);
  };
  $('team-name').oninput = () => markDirty();
  $('team-video').onchange = () => markDirty();
  $('save-team').onclick = () => saveTeam().then(() => toast('Tersimpan.')).catch(e => toast(e.message));
  $('new-team').onclick = () => { $('wf-menu').open = false; if (confirmLeave()) blankTeam(); };
  $('tidy-board').onclick = () => {
    $('wf-menu').open = false;
    autoLayout(S.team, true);
    savePositions();
    render();
    fitView();
  };
  $('delete-team').onclick = async () => {
    $('wf-menu').open = false;
    if (!S.team) { blankTeam(); return; }
    if (!confirm('Hapus alur ini beserta riwayatnya?')) return;
    try {
      await api(`/api/teams/${S.team}`, { method: 'DELETE' });
      S.teams = S.teams.filter(t => t.id !== S.team);
      blankTeam();
    } catch (e) { toast(e.message); }
  };

  /* ── Zoom controls and keys ──────────────────────── */
  $('zoom-in').onclick = () => zoomAt(1.2);
  $('zoom-out').onclick = () => zoomAt(1 / 1.2);
  $('zoom-fit').onclick = fitView;
  $('zoom-tidy').onclick = () => $('tidy-board').click();
  $('board').addEventListener('pointerdown', boardPointerDown);
  $('board').addEventListener('wheel', e => {
    e.preventDefault();
    const r = $('board').getBoundingClientRect();
    if (e.ctrlKey || e.metaKey) zoomAt(Math.exp(-e.deltaY * 0.0022), e.clientX - r.left, e.clientY - r.top);
    else { V.x -= e.deltaX; V.y -= e.deltaY; applyView(); }
  }, { passive: false });
  $('edge-delete').onpointerdown = e => e.stopPropagation();
  $('edge-delete').onclick = () => { if (S.selectedEdge) removeEdge(S.selectedEdge.from, S.selectedEdge.to); };
  // One menu handler for the whole canvas: it looks at what was clicked. The run bar and zoom buttons keep the
  // browser's own menu, so the task box can still paste.
  window.SQMenu.on($('board'), ({ target, event, keyboard }) => {
    if (S.tab !== 'editor' || !S.trigger) return null;
    if (target.closest && target.closest('.wf-run, .wf-zoom, .wf-plus, #edge-delete, .port-add')) return null;
    const node = target.closest && target.closest('.node');
    if (node) { selectNode(node.dataset.id); return node.dataset.id === TRIGGER ? triggerMenu() : nodeMenu(node.dataset.id); }
    const edge = target.closest && target.closest('.edge-hit[data-from]');
    if (edge) {
      S.selectedEdge = { from: edge.dataset.from, to: edge.dataset.to };
      selectNode(null);
      renderEdges();
      return edgeMenu(edge.dataset.from, edge.dataset.to);
    }
    if (keyboard && S.selected) return S.selected === TRIGGER ? triggerMenu() : nodeMenu(S.selected);
    return boardMenu(keyboard ? null : canvasPoint(event));
  });
  // A big change in size (a phone turned, a window snapped) fits the flow again; a small one keeps the view.
  let boardWidth = 0;
  window.addEventListener('resize', () => {
    if (S.view !== 'team' || S.tab !== 'editor' || !S.trigger) return;
    const width = $('board').clientWidth;
    if (boardWidth && Math.abs(width - boardWidth) / boardWidth > 0.25) fitView(); else applyView();
    boardWidth = width;
  });
  new ResizeObserver(() => { if (!boardWidth && $('board').clientWidth) boardWidth = $('board').clientWidth; }).observe($('board'));

  window.addEventListener('keydown', e => {
    if (S.view !== 'team') return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      saveTeam().then(() => toast('Tersimpan.')).catch(err => toast(err.message));
      return;
    }
    if (e.key === 'Escape') {
      if (!$('creator').classList.contains('hidden')) closeCreator();
      else if (S.ndv) closeNdv();
      return;
    }
    if (S.tab !== 'editor' || typing() || S.ndv) return;
    if ((e.key === 'Delete' || e.key === 'Backspace') && (S.selectedEdge || S.selected)) {
      e.preventDefault();
      if (S.selectedEdge) removeEdge(S.selectedEdge.from, S.selectedEdge.to);
      else if (S.selected) removeNode(S.selected);
    } else if (e.key === 'Tab' && $('creator').classList.contains('hidden')) {
      e.preventDefault();
      openCreator({});
    } else if (e.key === 'Enter' && S.selected) openNdv(S.selected);
    else if (e.key === '1') fitView();
    else if (e.key === '0') { V.k = 1; applyView(); }
    else if (e.key === '+' || e.key === '=') zoomAt(1.2);
    else if (e.key === '-') zoomAt(1 / 1.2);
  });

  /* ── Tabs ────────────────────────────────────────── */
  function showTab(name) {
    S.tab = name;
    document.querySelectorAll('#wf-tabs button').forEach(b => {
      const on = b.dataset.tab === name;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    ['editor', 'data', 'runs', 'creds'].forEach(tab => $(`wf-${tab}`).classList.toggle('hidden', tab !== name));
    if (name === 'data' && window.SQData) window.SQData.open();
    if (name === 'runs') loadRuns();
    if (name === 'creds') loadCreds();
    if (name === 'editor' && S.trigger) requestAnimationFrame(applyView);
  }
  document.querySelectorAll('#wf-tabs button').forEach(b => { b.onclick = () => showTab(b.dataset.tab); });

  /* ── Executions tab ──────────────────────────────── */
  async function loadRuns() {
    const box = $('runs-list');
    if (!S.team) { box.innerHTML = '<p class="io-empty">Simpan alur ini dulu. Setelah dijalankan, riwayatnya muncul di sini.</p>'; $('run-detail').innerHTML = ''; return; }
    try {
      const { runs } = await api(`/api/teams/${S.team}/runs`);
      S.runs = runs.sort((a, b) => b.created_at - a.created_at);
      box.innerHTML = S.runs.length ? S.runs.map(run => {
        const took = run.started_at ? formatSeconds(((run.completed_at || Date.now() / 1000) - run.started_at) * 1000) : '–';
        const when = new Date(run.created_at * 1000).toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'short' });
        const source = (run.trigger && run.trigger.source) || 'manual';
        return `<button class="run-row ${S.runDetail === run.id ? 'on' : ''}" data-run="${esc(run.id)}" type="button">
          <span class="run-dot" data-status="${esc(run.status)}"></span>
          <span class="run-main"><b>${esc(RUN_STATUS[run.status] || run.status)}</b><small>${esc(when)} · ${esc(source)}</small></span>
          <span class="run-meta">${esc(took)}<small>${formatTokens(run.total_tokens || 0)} token</small></span></button>`;
      }).join('') : '<p class="io-empty">Belum ada eksekusi. Jalankan alur dari tab Editor.</p>';
      box.querySelectorAll('[data-run]').forEach(row => { row.onclick = () => showRunDetail(row.dataset.run); });
      if (!S.runDetail && S.runs[0]) showRunDetail(S.runs[0].id);
    } catch (e) { box.innerHTML = `<p class="hint hint-warn">${esc(e.message)}</p>`; }
  }

  function showRunDetail(id) {
    const run = (S.runs || []).find(r => r.id === id);
    if (!run) return;
    S.runDetail = id;
    document.querySelectorAll('#runs-list .run-row').forEach(r => r.classList.toggle('on', r.dataset.run === id));
    const nodes = S.members.map(m => {
      const state = (run.agents || {})[m.id];
      if (!state) return '';
      return `<tr><td>${esc(m.name)}</td><td><span class="io-status" data-status="${esc(state.status)}">${esc(STATUS[state.status] || state.status)}</span></td>
        <td>${state.completed_at && state.started_at ? formatSeconds((state.completed_at - state.started_at) * 1000) : '–'}</td>
        <td>${state.tokens ? formatTokens(state.tokens) : '–'}</td><td class="muted">${esc(state.error || state.note || '')}</td></tr>`;
    }).join('');
    $('run-detail').innerHTML = `
      <div class="run-detail-head"><div><h3>${esc(RUN_STATUS[run.status] || run.status)}</h3><p class="hint">${esc(new Date(run.created_at * 1000).toLocaleString('id-ID'))}</p></div>
        <button id="run-open" class="btn btn-ghost" type="button">Lihat di kanvas</button></div>
      <div class="io-block"><div class="io-head">Tugas</div>${preview(run.task)}</div>
      ${nodes ? `<table class="run-table"><thead><tr><th>Node</th><th>Status</th><th>Waktu</th><th>Token</th><th>Catatan</th></tr></thead><tbody>${nodes}</tbody></table>` : ''}
      ${run.error ? `<div class="notice" style="white-space: pre-line">${esc(run.error)}</div>` : ''}
      ${run.result ? `<div class="io-block"><div class="io-head">Hasil</div>${preview(run.result)}</div>` : ''}`;
    enhanceCode($('run-detail'));
    $('run-open').onclick = () => openRunOnCanvas(run);
  }

  function openRunOnCanvas(run) {
    S.openResult = false;
    showRun(run);
    showTab('editor');
    if (!finished(run.status)) watchRun(run);
  }

  window.SQMenu.on($('runs-list'), ({ target }) => {
    const row = target.closest && target.closest('[data-run]');
    const run = row && (S.runs || []).find(r => r.id === row.dataset.run);
    if (!run) return null;
    return [
      { label: 'Lihat rincian', run: () => showRunDetail(run.id) },
      { label: 'Lihat di kanvas', run: () => openRunOnCanvas(run) },
      { separator: true },
      { label: 'Salin tugas', run: () => copyText(run.task || '') },
      { label: 'Salin hasil', disabled: run.result ? false : 'Eksekusi ini belum punya hasil', run: () => copyText(run.result || '') },
    ];
  });

  /* ── Credentials tab ─────────────────────────────── */
  const pinHeader = () => { try { const pin = sessionStorage.getItem(PIN_KEY); return pin ? { 'X-Router-Pin': pin } : {}; } catch { return {}; } };
  async function credApi(url, method, body) {
    const options = { method, headers: { 'Content-Type': 'application/json', ...pinHeader() }, body: body ? JSON.stringify(body) : undefined };
    try { return await api(url, options); } catch (e) {
      if (!/PIN/i.test(e.message)) throw e;
      const pin = prompt('Perubahan dari luar komputer server butuh PIN Router:');
      if (!pin) throw e;
      try { sessionStorage.setItem(PIN_KEY, pin); } catch { /* private window */ }
      return api(url, { ...options, headers: { ...options.headers, 'X-Router-Pin': pin } });
    }
  }

  async function loadCreds() {
    try { renderCreds(await api('/api/teams/credentials')); } catch (e) { $('creds-body').innerHTML = `<p class="hint hint-warn">${esc(e.message)}</p>`; }
  }

  function renderCreds(data) {
    const sourceBadge = f => (f.source === 'web' ? '<span class="cred-badge web">Dari web</span>' : f.source === 'env' ? '<span class="cred-badge env">Dari .env</span>' : f.source === 'default' ? '<span class="cred-badge none">Bawaan</span>' : '<span class="cred-badge none">Belum diisi</span>');
    $('creds-body').innerHTML = data.groups.map(group => {
      const fields = data.fields.filter(f => f.group === group.key);
      // Telegram needs its bot token; search and databases need any one of their fields.
      const ready = group.key === 'limits' ? false : group.key === 'telegram' ? fields.some(f => f.key === 'telegram_bot_token' && f.set) : fields.some(f => f.set && f.source !== 'default');
      const testable = group.key !== 'limits';
      const icon = group.key === 'telegram' ? 'telegram' : group.key === 'search' ? 'http' : group.key === 'limits' ? 'delay' : 'database';
      const dbSelect = group.key === 'database' ? `<select class="cred-db" aria-label="Database yang dites">
          <option value="sqlite">SQLite</option><option value="postgres">PostgreSQL</option><option value="mysql">MySQL</option><option value="mongodb">MongoDB</option></select>` : '';
      return `<section class="cred-card" data-group="${group.key}">
        <header><span class="node-icon" data-cat="${group.key === 'telegram' ? 'output' : group.key === 'search' ? 'ai' : group.key === 'limits' ? 'logic' : 'data'}">${nodeIcon(icon, 18)}</span>
          <div><h3>${esc(group.label)} ${ready ? '<span class="cred-ok">tersambung</span>' : ''}</h3><p class="hint">${esc(group.description)}</p></div></header>
        ${fields.map(f => `<label class="field cred-field"><span>${esc(f.label)} ${sourceBadge(f)}</span>
            <span class="cred-input">${f.multiline
    ? `<textarea data-cred="${f.key}" rows="5" autocomplete="off" spellcheck="false" placeholder="${esc(f.set ? `${f.masked}  ·  tempel kunci baru untuk mengganti` : f.example)}"></textarea>`
    : `<input data-cred="${f.key}" type="${f.secret ? 'password' : 'text'}" autocomplete="off" spellcheck="false"${group.key === 'limits' ? ' inputmode="numeric"' : ''}
              placeholder="${esc(f.set ? `${f.masked}  ·  ketik untuk mengganti` : f.example)}">`}
            ${f.source === 'web' ? `<button class="btn btn-ghost btn-small" type="button" data-cred-clear="${f.key}" title="Hapus dari web, pakai .env atau nilai bawaan lagi">Hapus</button>` : ''}</span>
            ${f.hint ? `<small class="hint">${esc(f.hint)}</small>` : ''}</label>`).join('')}
        ${group.key === 'google' && fields.some(f => f.set && f.masked) ? `<p class="hint">Bagikan tiap spreadsheet ke <b>${esc(fields.find(f => f.set).masked)}</b> sebagai Editor, lalu tambahkan di tab Data.</p>` : ''}
        <div class="cred-actions">${dbSelect}${testable ? `<button class="btn btn-ghost" type="button" data-cred-test="${group.key}">Tes</button>` : ''}
          <button class="btn btn-primary" type="button" data-cred-save="${group.key}">Simpan</button></div>
        <p class="cred-msg hint" aria-live="polite"></p>
      </section>`;
    }).join('') + '<p class="hint creds-foot">Disimpan di web/data/credentials.json. Nilai dari sini menang atas .env dan langsung berlaku, tanpa restart.</p>';
    $('creds-body').querySelectorAll('[data-cred-save]').forEach(button => {
      button.onclick = async () => {
        const card = button.closest('.cred-card');
        const values = {};
        card.querySelectorAll('[data-cred]').forEach(input => { if (input.value.trim()) values[input.dataset.cred] = input.value.trim(); });
        const msg = card.querySelector('.cred-msg');
        if (!Object.keys(values).length) { msg.textContent = 'Tidak ada yang diubah.'; return; }
        try {
          renderCreds(await credApi('/api/teams/credentials', 'PUT', { values }));
          await loadIntegrations();
          toast('Kredensial disimpan.');
        } catch (e) { msg.textContent = e.message; msg.className = 'cred-msg hint hint-warn'; }
      };
    });
    $('creds-body').querySelectorAll('[data-cred-clear]').forEach(button => {
      button.onclick = async () => {
        if (!confirm('Hapus nilai ini dari web? Bila .env punya nilainya, itu yang dipakai, kalau tidak nilai bawaan.')) return;
        try { renderCreds(await credApi('/api/teams/credentials', 'PUT', { values: { [button.dataset.credClear]: '' } })); await loadIntegrations(); } catch (e) { toast(e.message); }
      };
    });
    $('creds-body').querySelectorAll('[data-cred-test]').forEach(button => {
      button.onclick = async () => {
        const card = button.closest('.cred-card');
        const msg = card.querySelector('.cred-msg');
        button.disabled = true;
        msg.textContent = 'Mengetes…';
        msg.className = 'cred-msg hint';
        try {
          const db = card.querySelector('.cred-db');
          const r = await credApi('/api/teams/credentials/test', 'POST', { group: button.dataset.credTest, database: db ? db.value : '' });
          msg.textContent = r.message;
          msg.className = 'cred-msg hint ok';
        } catch (e) { msg.textContent = e.message; msg.className = 'cred-msg hint hint-warn'; }
        button.disabled = false;
      };
    });
  }

  /* ── Export to app.js ────────────────────────────── */
  window.SQTeam = {
    open() {
      if (!S.tab) showTab('editor');
      loadTeams();
    },
    modelsChanged() {
      if (S.ndv && S.ndv !== TRIGGER && !document.activeElement?.closest('#ndv')) renderNdv();
    },
    /* For the Data tab: saving needs the router PIN from outside this computer, same as credentials. */
    pinApi: credApi,
    showCreds() { if (S.ndv) closeNdv(); showTab('creds'); },
    sourcesChanged() { refreshSources(); },
  };
  window.addEventListener('beforeunload', e => { if (S.dirty) { e.preventDefault(); e.returnValue = ''; } });
})();
