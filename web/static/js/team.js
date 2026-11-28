/* Tim agent, built like n8n.

   Overview: the list of flows (open, switch on or off, duplicate, export, delete), templates, the Tabel data tab
   and the Kredensial tab. Editor: one flow on a canvas that reads left to right, and its executions.

   A flow runs exactly along the lines drawn: the trigger starts the nodes it is connected to, each node gets what
   its incoming lines bring, and a node no line reaches does not run. There is no hidden lead or reviewer. Drag from
   an output dot to a node to connect, drop on empty canvas to add a node there, click a line to insert a node into
   it or cut it. Click a node to select it; click it again (or press Enter) to open the detail view: what came in (with its fields, click one to put it in the focused
   box), its settings, and what it sent out. Tes langkah runs that one node on the input shown.

   Saved per flow on the server: nodes, connections (depends_on and the trigger's starts), the trigger, and the
   position of every node. */
(() => {
  'use strict';

  const { $, esc, icon, api, post, toast, md, store, S, fillModelOptions, shortModel, formatTokens, formatSeconds, describeTool, enhanceCode } = window.SQ;

  /* ── Constants ───────────────────────────────────── */
  const TRIGGER = '__trigger';
  const CANVAS = { w: 8000, h: 6000 };
  const COL = 250;
  const ROW = 170;
  const ORIGIN = { x: 420, y: 700 };
  const BOX = 92;
  const AGENT_W = 176;
  const PORT_GAP = 30;
  const PIN_KEY = 'router-pin';
  const HISTORY = 100;
  const V = { x: 0, y: 0, k: 1 };

  const NODE_ICONS = {
    agent: '<path d="M12 3v3M8 7h8a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3v-6a3 3 0 0 1 3-3z"/><circle cx="9.5" cy="12.5" r="1"/><circle cx="14.5" cy="12.5" r="1"/><path d="M9.5 16h5"/>',
    gate: '<path d="M6 4v6a4 4 0 0 0 4 4h8M18 14l-3-3M18 14l-3 3M6 20v-6"/>',
    switch: '<path d="M4 12h6M10 12l8-6M10 12h8M10 12l8 6"/><circle cx="19" cy="6" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="19" cy="18" r="1"/>',
    merge: '<path d="M5 5l7 7v7M19 5l-7 7"/>',
    delay: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 3h6"/>',
    set: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>',
    http: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    telegram: '<path d="m21 4-18 7 6 2 2 6 3-4 5 4z"/><path d="m9 13 8-6"/>',
    database: '<ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v14c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    datatable: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14.5h18M9 9v11M15 9v11"/>',
    trigger: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  };
  const nodeIcon = (kind, size = 20) =>
    `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${NODE_ICONS[kind] || NODE_ICONS.agent}</svg>`;

  const CATALOG = [
    { key: 'ai', label: 'AI', items: [
      { kind: 'agent', label: 'AI Agent', desc: 'Model AI dengan tool: riset web, menulis file, menjalankan kode, membaca tabel data.' },
    ] },
    { key: 'data', label: 'Data', items: [
      { kind: 'datatable', label: 'Tabel data', desc: 'Ambil, tambah, ubah, upsert, atau hapus baris. Bisa ikut membawa baris yang berelasi.' },
      { kind: 'set', label: 'Edit teks', desc: 'Susun teks dari {{input}}, {{json.kolom}}, {{date}}, dan hasil node lain.' },
      { kind: 'http', label: 'HTTP Request', desc: 'GET atau POST ke alamat publik. Balasannya diteruskan.' },
    ] },
    { key: 'logic', label: 'Alur', items: [
      { kind: 'gate', label: 'IF', desc: 'Cek syarat (AND/OR). Benar lewat jalur atas, salah lewat jalur bawah.' },
      { kind: 'switch', label: 'Switch', desc: 'Aturan pertama yang cocok memilih jalur A, B atau C. Sisanya ke D.' },
      { kind: 'merge', label: 'Merge', desc: 'Gabungkan beberapa jalur: tunggu salah satu atau semua.' },
      { kind: 'delay', label: 'Wait', desc: 'Jeda beberapa detik sebelum lanjut.' },
    ] },
    { key: 'output', label: 'Kirim', items: [
      { kind: 'telegram', label: 'Telegram', desc: 'Kirim input ke chat atau channel Telegram.' },
      { kind: 'database', label: 'Log ke database', desc: 'Catat input ke tabel log di SQLite, PostgreSQL, MySQL atau MongoDB. Tabelnya dibuat otomatis.' },
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
    { mode: 'telegram', label: 'Pesan Telegram', desc: 'Ngobrol lewat bot: hanya kamu, atau siapa saja.' },
  ];
  const NODE_DEFAULTS = {
    agent: { name: 'AI Agent', role: 'Asisten', config: {} },
    delay: { name: 'Wait', config: { seconds: 30 } },
    gate: { name: 'IF', config: { combine: 'and', conditions: [{ mode: 'contains', value: '' }] } },
    switch: { name: 'Switch', config: { rules: [{ mode: 'contains', value: '' }, { mode: 'contains', value: '' }] } },
    merge: { name: 'Merge', config: { mode: 'any' } },
    set: { name: 'Edit teks', config: { text: '{{input}}' } },
    http: { name: 'HTTP Request', config: { method: 'GET', url: '', body: '' } },
    telegram: { name: 'Kirim ke Telegram', config: { chat_id: '' } },
    database: { name: 'Log ke database', config: { database: 'sqlite', table: 'hasil_alur' } },
    datatable: { name: 'Tabel data', config: { source: '', table: '', operation: 'get' } },
  };
  const OPERATIONS = [
    ['get', 'Ambil'], ['insert', 'Tambah'], ['update', 'Ubah'], ['upsert', 'Upsert'], ['delete', 'Hapus'],
  ];
  const OPERATION_HINT = {
    get: 'Ambil baris yang cocok dengan syarat. Tanpa syarat, semua baris diambil.',
    insert: 'Tambah satu baris baru dengan nilai di bawah.',
    update: 'Ubah setiap baris yang cocok dengan syarat.',
    upsert: 'Cari baris dengan kolom pencocok yang sama. Ada: diubah. Belum ada: ditambah.',
    delete: 'Hapus setiap baris yang cocok dengan syarat.',
  };
  const DT_OPS = [['=', 'sama dengan'], ['!=', 'tidak sama'], ['>', 'lebih dari'], ['>=', 'minimal'], ['<', 'kurang dari'], ['<=', 'maksimal'], ['contains', 'berisi'], ['startswith', 'diawali']];
  const DAYS = ['Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab', 'Min'];
  const CONDITION_LABEL = {
    contains: 'Berisi kata kunci', not_contains: 'Tidak berisi kata kunci', equals: 'Sama persis dengan',
    regex: 'Cocok dengan regex', empty: 'Kosong', time: 'Jam tertentu', ai: 'Ditanyakan ke AI',
  };
  const STATUS = { queued: 'Menunggu', running: 'Berjalan', completed: 'Selesai', failed: 'Gagal', cancelled: 'Dibatalkan', skipped: 'Tidak dijalankan' };
  const RUN_STATUS = { queued: 'Menunggu', running: 'Berjalan', synthesizing: 'Lead menyusun jawaban', reviewing: 'Reviewer memeriksa', revising: 'Lead merevisi', delivering: 'Mengirim hasil', rendering: 'Merender video', completed: 'Selesai', failed: 'Gagal', cancelled: 'Dibatalkan' };
  const MODES = { standard: 'Standar', video: 'Video render (animate)', motion: 'Studio motion (bisa diedit)', codevideo: 'Video kode: rencana, render, Video Editor' };
  const WRITES = { datatable: c => c.operation && c.operation !== 'get', telegram: () => true, http: c => c.method === 'POST' };

  /* ── Flow helpers ────────────────────────────────── */
  const newId = () => `n${Math.random().toString(36).slice(2, 9)}`;
  const legacyPosKey = (team, id) => `wf:${team}:${id}`;
  const finished = status => ['completed', 'failed', 'cancelled'].includes(status);
  const kindOf = member => member.kind || 'agent';
  const memberById = id => S.members.find(m => m.id === id);
  const parentsOf = member => (member.depends_on || []).map(memberById).filter(Boolean);
  const childrenOf = id => S.members.filter(m => (m.depends_on || []).includes(id));
  const isBranching = member => ['gate', 'switch'].includes(kindOf(member));
  const branchParent = member => parentsOf(member).find(isBranching);
  const conditionsOf = config => config.conditions || (config.mode ? [config] : []);
  const isStart = id => S.starts.includes(id);
  const typing = () => document.activeElement && document.activeElement.matches('input, textarea, select, [contenteditable]');
  const enc = encodeURIComponent;
  const clone = value => JSON.parse(JSON.stringify(value));
  const modelKnown = id => Boolean(id) && (!S.models.length || Boolean(S.byId[id]));

  function reachable() {
    const seen = new Set();
    const queue = [...S.starts];
    while (queue.length) {
      const id = queue.shift();
      if (seen.has(id) || !memberById(id)) continue;
      seen.add(id);
      childrenOf(id).forEach(child => queue.push(child.id));
    }
    return seen;
  }

  function portsOf(member) {
    if (member === TRIGGER) return [{ key: '', label: '' }];
    if (kindOf(member) === 'gate') return [{ key: 'A', label: 'benar' }, { key: 'B', label: 'salah' }];
    if (kindOf(member) === 'switch') return [{ key: 'A', label: 'A' }, { key: 'B', label: 'B' }, { key: 'C', label: 'C' }, { key: 'D', label: 'lainnya' }];
    return [{ key: '', label: '' }];
  }
  const widthOf = member => (member !== TRIGGER && kindOf(member) === 'agent' ? AGENT_W : BOX);
  const heightOf = member => Math.max(BOX, portsOf(member).length * PORT_GAP + 12);
  const posOf = member => (member === TRIGGER ? S.trigger : member);
  const inPoint = member => ({ x: member.x, y: member.y + heightOf(member) / 2 });
  function outPoint(member, key = '') {
    const pos = posOf(member);
    const ports = portsOf(member);
    const index = Math.max(0, ports.findIndex(p => p.key === key));
    const h = heightOf(member);
    return { x: pos.x + widthOf(member), y: pos.y + ((index + 1) * h) / (ports.length + 1) };
  }

  function nodeDetail(member) {
    const config = member.config || {};
    switch (kindOf(member)) {
      case 'delay': return `${config.seconds || 0} detik`;
      case 'gate': {
        const list = conditionsOf(config);
        if (list.length > 1) return `${list.length} syarat, ${(config.combine || 'and').toUpperCase()}`;
        const only = list[0] || {};
        if (only.mode === 'time') return `Jam ${only.start || '--:--'} sampai ${only.end || '--:--'}`;
        return `${CONDITION_LABEL[only.mode] || 'Syarat'}${only.value ? `: ${only.value}` : ''}`;
      }
      case 'switch': return `${(config.rules || []).length} aturan`;
      case 'merge': return config.mode === 'all' ? 'Tunggu semua jalur' : 'Salah satu jalur';
      case 'set': return (config.text || 'belum diisi').split('\n')[0].slice(0, 40);
      case 'http': return `${config.method || 'GET'} ${(config.url || '').replace(/^https?:\/\//, '').slice(0, 26) || 'URL belum diisi'}`;
      case 'telegram': return config.chat_id || 'Chat bawaan';
      case 'database': return `${config.source || config.database || 'sqlite'} · ${config.table || 'hasil_alur'}`;
      case 'datatable': {
        const op = (OPERATIONS.find(o => o[0] === (config.operation || 'get')) || OPERATIONS[0])[1];
        return config.table ? `${op} · ${config.table}` : 'Tabel belum dipilih';
      }
      default: return shortModel(member.model_id) || 'model belum dipilih';
    }
  }

  function scheduleLabel(schedule = { mode: 'off' }) {
    switch (schedule.mode) {
      case 'interval': return `Tiap ${schedule.minutes || '?'} menit`;
      case 'daily': return `Jam ${(schedule.times || [schedule.time || '--:--']).join(', ')}`;
      case 'cron': return `Cron ${schedule.cron || '?'}`;
      case 'once': return `Sekali, ${(schedule.at || '').replace('T', ' ')}`;
      case 'telegram': return `${schedule.audience === 'public' ? 'Pesan publik' : 'Pesan Telegram'}${schedule.keyword ? ` ${schedule.keyword}` : ''}`;
      default: return 'Saat Jalankan alur ditekan';
    }
  }
  const triggerLabel = () => scheduleLabel(S.schedule || { mode: 'off' });

  /* What is still missing on a node. Shown as a red mark on the canvas and in the list at the top. */
  function issuesOf(member) {
    const c = member.config || {};
    const out = [];
    switch (kindOf(member)) {
      case 'agent':
        if (!modelKnown(member.model_id) && !(c.harness && [undefined, '', 'auto'].includes(member.model_id))) out.push('Model belum dipilih');
        break;
      case 'datatable': {
        if (!c.source) out.push('Tabel data belum dipilih');
        else if (!(S.dataSources || []).some(s => s.name === c.source) && S.dataSources) out.push(`Sumber ${c.source} sudah tidak ada`);
        else if (!c.table) out.push('Tabel belum dipilih');
        const op = c.operation || 'get';
        if (['update', 'delete'].includes(op) && !(c.where || []).some(w => w.column)) out.push('Butuh minimal satu syarat');
        if (['insert', 'update', 'upsert'].includes(op) && !(c.values || []).some(v => v.column)) out.push('Isi minimal satu kolom');
        if (op === 'upsert' && !(c.match || []).length) out.push('Pilih kolom pencocok');
        break;
      }
      case 'http':
        if (!/^https?:\/\/[^\s/]+/.test(c.url || '')) out.push('URL belum diisi');
        break;
      case 'set':
        if (!(c.text || '').trim()) out.push('Isi teks masih kosong');
        break;
      case 'gate': case 'switch': {
        const list = kindOf(member) === 'gate' ? conditionsOf(c) : (c.rules || []);
        if (list.some(x => !['empty', 'time'].includes(x.mode || 'contains') && !String(x.value || '').trim())) out.push('Ada syarat yang belum diisi');
        if (list.some(x => x.mode === 'ai') && !modelKnown(member.model_id)) out.push('Model untuk syarat AI belum dipilih');
        break;
      }
      case 'telegram':
        if (S.integrations && S.integrations.telegram && !S.integrations.telegram.ready) out.push('Bot Telegram belum diatur di Kredensial');
        break;
      default: break;
    }
    return out;
  }

  function flowIssues() {
    const list = [];
    if (S.members.length && !S.starts.length) list.push({ id: TRIGGER, name: 'Pemicu', text: 'Belum tersambung ke node mana pun' });
    const mode = (S.schedule || {}).mode || 'off';
    if (!['off', 'telegram'].includes(mode) && !String((S.schedule || {}).task || '').trim()) list.push({ id: TRIGGER, name: 'Pemicu', text: 'Jadwal butuh tugas yang dijalankan' });
    S.members.forEach(m => issuesOf(m).forEach(text => list.push({ id: m.id, name: m.name, text })));
    return list;
  }

  /* ── Dirty state and history ─────────────────────── */
  function markDirty(dirty = true) {
    S.dirty = dirty;
    $('wf-dirty').classList.toggle('hidden', !dirty);
    renderIssues();
  }

  const snapshot = () => JSON.stringify({ members: S.members, starts: S.starts, schedule: S.schedule, trigger: S.trigger, loops: S.loops, folder: S.folder });
  /* Called before every change. Typing in one box within a second counts as one step. */
  function checkpoint(merge = '') {
    const now = Date.now();
    if (merge && S.lastStep && S.lastStep.key === merge && now - S.lastStep.at < 1200) { S.lastStep.at = now; return; }
    S.lastStep = { key: merge, at: now };
    S.past.push(snapshot());
    if (S.past.length > HISTORY) S.past.shift();
    S.future = [];
    paintHistory();
  }
  function restore(state) {
    const data = JSON.parse(state);
    S.members = data.members;
    S.starts = data.starts;
    S.schedule = data.schedule;
    S.trigger = data.trigger;
    S.loops = data.loops || [];
    S.folder = data.folder || 'team';
    if (S.ndv && S.ndv !== TRIGGER && !memberById(S.ndv)) closeNdv();
    markDirty();
    render();
    if (S.ndv) renderNdv();
  }
  function undo() {
    if (!S.past.length) return;
    S.future.push(snapshot());
    restore(S.past.pop());
    S.lastStep = null;
    paintHistory();
  }
  function redo() {
    if (!S.future.length) return;
    S.past.push(snapshot());
    restore(S.future.pop());
    S.lastStep = null;
    paintHistory();
  }
  function paintHistory() {
    $('wf-undo').disabled = !S.past.length;
    $('wf-redo').disabled = !S.future.length;
  }

  /* ── Layout and view ─────────────────────────────── */
  function depthMap() {
    const depth = new Map();
    const visit = (member, seen) => {
      if (depth.has(member.id)) return depth.get(member.id);
      if (seen.has(member.id)) return 0;
      seen.add(member.id);
      const parents = parentsOf(member);
      const d = parents.length ? 1 + Math.max(...parents.map(p => visit(p, seen))) : 0;
      depth.set(member.id, d);
      return d;
    };
    S.members.forEach(m => visit(m, new Set()));
    return depth;
  }

  function autoLayout(force = false) {
    const depth = depthMap();
    const columns = {};
    S.members.forEach(m => { (columns[depth.get(m.id)] ||= []).push(m); });
    S.members.forEach(m => {
      if (!force && m.x !== undefined) return;
      const list = columns[depth.get(m.id)];
      m.x = ORIGIN.x + depth.get(m.id) * COL;
      m.y = ORIGIN.y + (list.indexOf(m) - (list.length - 1) / 2) * ROW;
    });
    if (force || !S.trigger) {
      const starts = S.members.filter(m => isStart(m.id));
      const y = starts.length ? starts.reduce((sum, m) => sum + m.y + heightOf(m) / 2, 0) / starts.length - BOX / 2 : ORIGIN.y;
      S.trigger = { x: ORIGIN.x - COL, y };
    }
  }

  function layoutOut() {
    const out = { [TRIGGER]: [Math.round(S.trigger.x), Math.round(S.trigger.y)] };
    S.members.forEach(m => { out[m.id] = [Math.round(m.x), Math.round(m.y)]; });
    return out;
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
    const items = [{ x: S.trigger.x, y: S.trigger.y, w: BOX, h: BOX }, ...S.members.map(m => ({ x: m.x, y: m.y, w: widthOf(m), h: heightOf(m) }))];
    const minX = Math.min(...items.map(i => i.x)) - 70;
    const maxX = Math.max(...items.map(i => i.x + i.w)) + 120;
    const minY = Math.min(...items.map(i => i.y)) - 60;
    const maxY = Math.max(...items.map(i => i.y + i.h)) + 90;   // labels sit under the boxes
    const width = board.clientWidth || 900;
    const height = (board.clientHeight || 600) - 80;            // the run bar sits at the bottom
    V.k = Math.max(0.35, Math.min(1.2, Math.min(width / (maxX - minX), height / (maxY - minY))));
    V.x = (width - (maxX - minX) * V.k) / 2 - minX * V.k;
    V.y = (height - (maxY - minY) * V.k) / 2 - minY * V.k;
    applyView();
  }

  function zoomAt(factor, cx, cy) {
    const k = Math.max(0.3, Math.min(2, V.k * factor));
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
    renderIssues();
  }

  function outPorts(member, id) {
    const ports = portsOf(member);
    const h = heightOf(member);
    const children = id === TRIGGER ? S.starts : childrenOf(id).map(c => c.id);
    return ports.map((port, i) => {
      const top = ((i + 1) * h) / (ports.length + 1);
      const used = id === TRIGGER ? children.length > 0
        : childrenOf(id).some(c => !isBranching(member) || c.branch === port.key || !c.branch);
      return `<span class="port out" data-port="${port.key}" data-from="${esc(id)}" style="top:${top}px" title="Seret ke node lain untuk menyambung${port.label ? ` (jalur ${esc(port.label)})` : ''}"></span>
        ${port.label ? `<span class="port-label" style="top:${top}px">${esc(port.label)}</span>` : ''}
        ${used ? '' : `<button class="port-add" data-add-from="${esc(id)}" data-port="${port.key}" style="top:${top}px" type="button" aria-label="Tambah node berikutnya">${icon('plus', 12)}</button>`}`;
    }).join('');
  }

  function renderNodes() {
    const live = reachable();
    const triggerMode = (S.schedule || {}).mode || 'off';
    const triggerIssues = flowIssues().filter(i => i.id === TRIGGER).map(i => i.text);
    const trigger = `
      <div class="node trigger ${S.selected === TRIGGER ? 'selected' : ''}" data-id="${TRIGGER}" data-cat="trigger" style="left:${S.trigger.x}px;top:${S.trigger.y}px;width:${BOX}px;height:${BOX}px" tabindex="0" role="button" aria-label="Pemicu: ${esc(triggerLabel())}">
        <span class="node-icon">${nodeIcon('trigger', 30)}</span>
        <span class="node-status" aria-hidden="true"></span>
        ${triggerIssues.length ? `<span class="node-issue" title="${esc(triggerIssues.join('\n'))}">!</span>` : ''}
        <button class="node-more" type="button" tabindex="-1" aria-label="Menu pemicu" title="Menu (atau klik kanan)">⋯</button>
        ${outPorts(TRIGGER, TRIGGER)}
        <span class="node-label"><b>${esc(TRIGGERS.find(t => t.mode === triggerMode)?.label || 'Manual')}</b><small>${esc(triggerLabel())}</small><span class="node-state"></span></span>
      </div>`;
    const nodes = S.members.map(member => {
      const kind = kindOf(member);
      const issues = issuesOf(member);
      const off = !live.has(member.id);
      return `
        <div class="node ${member.id === S.selected ? 'selected' : ''} ${off ? 'off' : ''}" data-id="${esc(member.id)}" data-kind="${kind}" data-cat="${CATEGORY[kind] || 'ai'}"
          style="left:${member.x}px;top:${member.y}px;width:${widthOf(member)}px;height:${heightOf(member)}px" tabindex="0" role="button"
          aria-label="${esc(member.name)}, ${esc(LABEL[kind] || 'Node')}${off ? ', tidak tersambung' : ''}" ${off ? 'title="Tidak tersambung ke Pemicu, jadi tidak dijalankan"' : ''}>
          <span class="port in" data-to="${esc(member.id)}"></span>
          <span class="node-icon">${nodeIcon(kind, kind === 'agent' ? 26 : 30)}</span>
          ${kind === 'agent' ? `<span class="node-inner"><b>AI Agent</b><small>${esc(shortModel(member.model_id) || 'pilih model')}</small></span>` : ''}
          <span class="node-status" aria-hidden="true"></span>
          ${issues.length ? `<span class="node-issue" title="${esc(issues.join('\n'))}">!</span>` : ''}
          <button class="node-more" type="button" tabindex="-1" aria-label="Menu ${esc(member.name)}" title="Menu (atau klik kanan)">⋯</button>
          ${outPorts(member, member.id)}
          <span class="node-label"><b>${esc(member.name || 'Tanpa nama')}</b>${kind === 'agent' ? '' : `<small>${esc(nodeDetail(member))}</small>`}<span class="node-state"></span></span>
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
    const dx = Math.max(60, Math.abs(b.x - a.x) / 2);
    return `M${a.x} ${a.y}C${a.x + dx} ${a.y} ${b.x - dx} ${b.y} ${b.x} ${b.y}`;
  }

  /* Every line on the board is one you drew, and every one can be cut. */
  function edgeList() {
    const list = S.starts.filter(memberById).map(id => ({ from: TRIGGER, to: id, port: '' }));
    S.members.forEach(member => {
      parentsOf(member).forEach(parent => list.push({ from: parent.id, to: member.id, port: isBranching(parent) ? (member.branch || '') : '' }));
    });
    return list;
  }

  function renderEdges() {
    const statusOf = id => (S.run && S.run.agents && S.run.agents[id] ? S.run.agents[id].status : '');
    const html = [];
    const selected = S.selectedEdge;
    let mid = null;
    edgeList().forEach(edge => {
      const from = edge.from === TRIGGER ? TRIGGER : memberById(edge.from);
      const to = memberById(edge.to);
      if (!from || !to) return;
      const a = outPoint(from, edge.port || '');
      const b = inPoint(to);
      const d = edgePath(a, b);
      const isSelected = selected && selected.from === edge.from && selected.to === edge.to;
      const status = statusOf(edge.to);
      const cls = ['edge', isSelected ? 'selected' : '', status === 'running' ? 'active' : '', status === 'skipped' ? 'skipped' : '', status === 'completed' ? 'done' : ''].filter(Boolean).join(' ');
      html.push(`<path class="${cls}" d="${d}" marker-end="url(#arrow)"/>`);
      html.push(`<path class="edge-hit" d="${d}" data-from="${esc(edge.from)}" data-to="${esc(edge.to)}"><title>Klik untuk menyisipkan node atau memutus sambungan</title></path>`);
      if (isSelected) mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    });
    (S.loops || []).forEach((loop, index) => {
      const from = memberById(loop.from);
      const to = memberById(loop.to);
      if (!from || !to) return;
      const a = outPoint(from, loop.branch);
      const b = inPoint(to);
      // Under both nodes and back: a loop never hides behind the forward lines.
      const low = Math.max(a.y, b.y, from.y + heightOf(from), to.y + heightOf(to)) + 70;
      const midX = (a.x + b.x) / 2;
      const d = `M${a.x} ${a.y}C${a.x + 110} ${a.y} ${a.x + 110} ${low} ${midX} ${low}C${b.x - 110} ${low} ${b.x - 110} ${b.y} ${b.x} ${b.y}`;
      const isSelected = selected && selected.loop === index;
      html.push(`<path class="edge loop ${isSelected ? 'selected' : ''}" d="${d}" marker-end="url(#arrow)"/>`);
      html.push(`<text class="loop-label" x="${midX}" y="${low - 8}" text-anchor="middle">ulangi bila ${esc(branchName(from, loop.branch))} · maks ${Number(loop.max) || 3}x</text>`);
      html.push(`<path class="edge-hit" d="${d}" data-loop="${index}"><title>Garis ulangi. Klik untuk menghapus; jumlahnya diatur di node ${esc(from.name)}</title></path>`);
      if (isSelected) mid = { x: midX, y: low, loop: true };
    });
    $('edge-lines').innerHTML = html.join('');
    $('edge-lines').querySelectorAll('.edge-hit').forEach(hit => {
      hit.onpointerdown = e => edgePointerDown(e, hit);
    });
    const tools = $('edge-tools');
    if (mid) {
      tools.style.left = `${mid.x}px`;
      tools.style.top = `${mid.y}px`;
      $('edge-insert').classList.toggle('hidden', Boolean(mid.loop));
      tools.classList.remove('hidden');
    } else {
      tools.classList.add('hidden');
      if (selected) S.selectedEdge = null;
    }
  }

  function selectNode(id) {
    S.selected = id;
    if (id) S.selectedEdge = null;
    document.querySelectorAll('#nodes .node').forEach(n => n.classList.toggle('selected', n.dataset.id === id));
  }

  function renderIssues() {
    const button = $('wf-issues');
    if (!S.members) return;
    const list = flowIssues();
    button.classList.toggle('hidden', !list.length);
    button.textContent = `${list.length} perlu diisi`;
    button.title = list.map(i => `${i.name}: ${i.text}`).join('\n');
  }

  /* ── Pointer: pan, drag, connect ─────────────────── */
  function boardPointerDown(event) {
    if (event.button !== 0 || event.target.closest('.node, .port-add, #edge-tools, .wf-zoom, .wf-run, .wf-plus, .wf-note')) return;
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
    // Only a node the user clicked before opens on this click, not one selected for them (a new node is).
    const wasSelected = S.selected === id && S.clickedId === id;
    S.clickedId = id;
    selectNode(id);
    if (S.selectedEdge) { S.selectedEdge = null; renderEdges(); }
    const start = canvasPoint(event);
    const origin = { x: item.x, y: item.y };
    const before = snapshot();
    let moved = false;
    el.setPointerCapture(event.pointerId);
    el.onpointermove = e => {
      const p = canvasPoint(e);
      if (!moved && Math.hypot(p.x - start.x, p.y - start.y) < 4 / V.k) return;
      moved = true;
      item.x = Math.max(0, Math.min(CANVAS.w - BOX, origin.x + p.x - start.x));
      item.y = Math.max(0, Math.min(CANVAS.h - BOX, origin.y + p.y - start.y));
      el.style.left = `${item.x}px`;
      el.style.top = `${item.y}px`;
      renderEdges();
    };
    el.onpointerup = () => {
      el.onpointermove = null;
      el.onpointerup = null;
      // Click selects; a click on the node already selected opens it.
      if (!moved) { if (wasSelected) openNdv(id); return; }
      S.past.push(before);
      S.future = [];
      S.lastStep = null;
      paintHistory();
      markDirty();
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
      if (over && over.closest('#board') && !over.closest('.wf-run, .wf-zoom')) openCreator({ from: fromId, branch, at: canvasPoint(e) });
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  /* A click selects a line. Dragging it moves its end to another node (dropped on empty board, it stays). */
  function edgePointerDown(event, hit) {
    if (event.button !== 0) return;
    event.stopPropagation();
    const loopIndex = hit.dataset.loop !== undefined ? Number(hit.dataset.loop) : null;
    const edge = loopIndex === null
      ? { from: hit.dataset.from, to: hit.dataset.to }
      : { from: S.loops[loopIndex].from, to: S.loops[loopIndex].to, branch: S.loops[loopIndex].branch };
    const start = { x: event.clientX, y: event.clientY };
    let dragging = false;
    const draft = $('edge-draft');
    const fromItem = edge.from === TRIGGER ? TRIGGER : memberById(edge.from);
    const port = loopIndex !== null ? edge.branch
      : (edge.from !== TRIGGER && isBranching(memberById(edge.from)) ? (memberById(edge.to).branch || '') : '');
    const move = e => {
      if (!dragging && Math.hypot(e.clientX - start.x, e.clientY - start.y) < 6) return;
      if (!dragging) {
        dragging = true;
        draft.classList.remove('hidden');
        $('board').classList.add('connecting');
        hit.closest('svg').querySelectorAll(`[data-from="${CSS.escape(edge.from)}"][data-to="${CSS.escape(edge.to)}"]`).forEach(x => x.classList.add('moving'));
      }
      draft.setAttribute('d', edgePath(outPoint(fromItem, port), canvasPoint(e)));
      document.querySelectorAll('#nodes .node.drop').forEach(n => n.classList.remove('drop'));
      const over = document.elementFromPoint(e.clientX, e.clientY)?.closest('#nodes .node');
      if (over && over.dataset.id !== edge.from && over.dataset.id !== TRIGGER) over.classList.add('drop');
    };
    const up = e => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      draft.classList.add('hidden');
      $('board').classList.remove('connecting');
      document.querySelectorAll('#nodes .node.drop').forEach(n => n.classList.remove('drop'));
      if (!dragging) {
        S.selectedEdge = loopIndex === null ? edge : { loop: loopIndex };
        selectNode(null);
        renderEdges();
        return;
      }
      const target = document.elementFromPoint(e.clientX, e.clientY)?.closest('#nodes .node');
      const toId = target ? target.dataset.id : '';
      if (!toId || toId === TRIGGER || toId === edge.from || toId === edge.to) { renderEdges(); return; }
      if (loopIndex !== null) {
        if (!isAncestor(toId, memberById(edge.from))) { toast('Garis ulangi harus kembali ke node sebelum node ini.'); renderEdges(); return; }
        checkpoint();
        S.loops[loopIndex].to = toId;
        afterChange();
        return;
      }
      rewire(edge.from, edge.to, toId, port);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  /* Move a line's end from one node to another, in one undo step. */
  function rewire(fromId, oldTo, newTo, port) {
    const target = memberById(newTo);
    if (!target) return;
    if (fromId !== TRIGGER) {
      const from = memberById(fromId);
      if (newTo === fromId || isAncestor(newTo, from)) {
        if (isBranching(from) && port) {
          checkpoint();
          const old = memberById(oldTo);
          old.depends_on = (old.depends_on || []).filter(id => id !== fromId);
          if (!branchParent(old)) old.branch = '';
          S.loops = (S.loops || []).filter(l => !(l.from === fromId && l.branch === port));
          S.loops.push({ from: fromId, branch: port, to: newTo, max: 3 });
          afterChange();
          return;
        }
        toast('Garis ke belakang hanya bisa dari jalur IF atau Switch.');
        return;
      }
    }
    checkpoint();
    if (fromId === TRIGGER) {
      S.starts = [...new Set([...S.starts.filter(id => id !== oldTo), newTo])];
    } else {
      const old = memberById(oldTo);
      old.depends_on = (old.depends_on || []).filter(id => id !== fromId);
      if (!branchParent(old)) old.branch = '';
      target.depends_on = [...new Set([...(target.depends_on || []), fromId])];
      if (isBranching(memberById(fromId))) target.branch = port;
    }
    S.selectedEdge = null;
    afterChange();
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
      if (isStart(toId)) { toast('Sudah tersambung ke Pemicu.'); return; }
      checkpoint();
      S.starts = [...S.starts, toId];
      afterChange();
      return;
    }
    const from = memberById(fromId);
    if (!from) return;
    if (from.id === to.id || isAncestor(to.id, from)) {
      // Back up the flow: only a branch of IF or Switch can do that, as a loop with a limit ("ulangi").
      if (isBranching(from) && branch && from.id !== to.id) { addLoop(from.id, branch, to.id); return; }
      toast('Garis ke belakang hanya bisa dari jalur IF atau Switch, sebagai "ulangi dari node ini".');
      return;
    }
    const deps = new Set(to.depends_on || []);
    if (deps.has(from.id) && (!isBranching(from) || to.branch === branch)) { toast('Sudah tersambung.'); return; }
    checkpoint();
    deps.add(from.id);
    to.depends_on = [...deps];
    to.branch = isBranching(from) ? branch : (branchParent(to) ? to.branch : '');
    afterChange();
  }

  function addLoop(fromId, branch, toId, max = 3) {
    checkpoint();
    S.loops = (S.loops || []).filter(l => !(l.from === fromId && l.branch === branch));
    S.loops.push({ from: fromId, branch, to: toId, max });
    afterChange();
    const gate = memberById(fromId);
    toast(`Bila ${gate ? gate.name : 'node ini'} memilih jalur ${branchName(gate, branch)}, alur diulang dari ${memberById(toId).name} (maks ${max}x). Atur jumlahnya di node itu.`);
  }

  function branchName(member, branch) {
    if (member && kindOf(member) === 'gate') return branch === 'A' ? 'benar' : 'salah';
    return branch;
  }

  function removeLoop(index) {
    checkpoint();
    S.loops.splice(index, 1);
    S.selectedEdge = null;
    afterChange();
  }

  function removeEdge(fromId, toId) {
    const to = memberById(toId);
    if (!to) return;
    checkpoint();
    if (fromId === TRIGGER) S.starts = S.starts.filter(id => id !== toId);
    else {
      to.depends_on = (to.depends_on || []).filter(id => id !== fromId);
      if (!branchParent(to)) to.branch = '';
    }
    S.selectedEdge = null;
    afterChange();
  }

  function afterChange() {
    markDirty();
    render();
    if (S.ndv) renderNdv();
  }

  /* ── Adding and removing nodes ───────────────────── */
  function defaultModel() {
    const used = (S.members.find(m => kindOf(m) === 'agent' && modelKnown(m.model_id)) || {}).model_id;
    if (used) return used;
    if (S.model && S.model !== 'auto' && S.byId[S.model]) return S.model;
    return (S.models[0] && S.models[0].id) || '';
  }

  /* pending: {from, branch, at, to}. "to" inserts the node into the line from → to. Without "from", the node
     follows the selected node, or the trigger when the flow is still empty. */
  function addNode(kind, pending = {}) {
    checkpoint();
    const preset = NODE_DEFAULTS[kind];
    let fromId = 'from' in pending ? pending.from : (S.selected || (S.members.length ? null : TRIGGER));
    if (fromId && fromId !== TRIGGER && !memberById(fromId)) fromId = null;
    const parent = fromId && fromId !== TRIGGER ? memberById(fromId) : null;
    const count = S.members.filter(m => kindOf(m) === kind).length;
    const member = {
      id: newId(), kind, name: count ? `${preset.name} ${count + 1}` : preset.name,
      role: preset.role || preset.name, model_id: kind === 'agent' ? defaultModel() : '', instructions: '',
      config: clone(preset.config), depends_on: parent ? [parent.id] : [],
      branch: parent && isBranching(parent) ? (pending.branch || 'A') : '',
    };
    if (fromId === TRIGGER) S.starts = [...S.starts, member.id];
    const target = pending.to ? memberById(pending.to) : null;
    if (target) {
      if (fromId === TRIGGER) S.starts = S.starts.filter(id => id !== target.id);
      else target.depends_on = (target.depends_on || []).filter(id => id !== fromId);
      target.depends_on = [...(target.depends_on || []), member.id];
      if (parent && isBranching(parent)) { member.branch = target.branch || member.branch; target.branch = ''; }
    }
    const anchor = fromId ? outPoint(fromId === TRIGGER ? TRIGGER : parent, member.branch) : null;
    if (pending.at) { member.x = pending.at.x; member.y = pending.at.y - BOX / 2; }
    else if (target && anchor) { member.x = (anchor.x + target.x) / 2 - widthOf(member) / 2; member.y = anchor.y - heightOf(member) / 2 - ROW / 2; }
    else if (anchor) { member.x = anchor.x + COL - BOX; member.y = anchor.y - heightOf(member) / 2; }
    else {
      const right = Math.max(S.trigger.x, ...S.members.map(m => m.x));
      member.x = right + COL;
      member.y = S.trigger.y;
    }
    while (S.members.some(m => Math.abs(m.x - member.x) < widthOf(m) && Math.abs(m.y - member.y) < BOX + 40)) member.y += ROW;
    S.members.push(member);
    selectNode(member.id);
    afterChange();
    return member;
  }

  /* Like n8n: removing a node in the middle joins what came before it to what came after. */
  function removeNode(id) {
    if (id === TRIGGER) { toast('Pemicu tidak bisa dihapus. Ubah ke Manual bila tidak perlu jadwal.'); return; }
    const member = memberById(id);
    if (!member) return;
    checkpoint();
    const wasStart = isStart(id);
    const parents = member.depends_on || [];
    S.members = S.members.filter(m => m.id !== id);
    S.members.forEach(m => {
      if (!(m.depends_on || []).includes(id)) return;
      m.depends_on = [...new Set([...m.depends_on.filter(dep => dep !== id), ...parents])];
      if (wasStart && !S.starts.includes(m.id)) S.starts = [...S.starts, m.id];
      if (branchParent(m)) m.branch = m.branch || member.branch || 'A';
      else m.branch = '';
    });
    S.starts = S.starts.filter(s => s !== id);
    S.loops = (S.loops || []).filter(l => l.from !== id && l.to !== id);
    delete S.tests[id];
    if (S.ndv === id) closeNdv();
    selectNode(null);
    afterChange();
  }

  function copyNode(id) {
    const member = memberById(id);
    if (!member) return;
    S.clipboard = clone(member);
    try { navigator.clipboard.writeText(JSON.stringify({ squidwings_node: S.clipboard })); } catch { /* the in-page copy is enough */ }
    toast('Node disalin. Tempel dengan Ctrl+V.');
  }

  function pasteNode(source = S.clipboard, at = null) {
    if (!source) return;
    checkpoint();
    const copy = clone(source);
    copy.id = newId();
    copy.name = `${source.name || 'Node'} (salinan)`.slice(0, 60);
    if (kindOf(copy) !== 'agent') copy.role = copy.name;
    copy.depends_on = [];
    copy.branch = '';
    copy.x = at ? at.x : (source.x || S.trigger.x + COL) + 40;
    copy.y = at ? at.y : (source.y || S.trigger.y) + ROW;
    while (S.members.some(m => Math.abs(m.x - copy.x) < widthOf(m) && Math.abs(m.y - copy.y) < BOX + 40)) copy.y += ROW;
    S.members.push(copy);
    selectNode(copy.id);
    afterChange();
  }

  function duplicateNode(id) {
    const member = memberById(id);
    if (member) pasteNode(member);
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
    checkpoint();
    member.name = name.trim().slice(0, 60);
    if (kindOf(member) !== 'agent') member.role = member.name;
    afterChange();
  }

  function nodeMenu(id) {
    const member = memberById(id);
    if (!member) return null;
    const items = [
      { label: 'Buka', hint: 'Enter', run: () => openNdv(id) },
      ...(kindOf(member) === 'agent' ? [] : [{ label: 'Tes langkah ini', run: () => { openNdv(id); testNode(); } }]),
      { label: 'Ganti nama…', run: () => renameNode(id) },
      { label: 'Duplikat', hint: 'Ctrl+D', run: () => duplicateNode(id) },
      { label: 'Salin', hint: 'Ctrl+C', run: () => copyNode(id) },
      { separator: true },
      { label: 'Tambah node setelah ini…', run: () => openCreator({ from: id, branch: isBranching(member) ? 'A' : '' }) },
    ];
    const parents = parentsOf(member);
    if (parents.length || isStart(id)) {
      items.push({ separator: true }, { heading: 'Putus sambungan' });
      if (isStart(id)) items.push({ label: 'dari Pemicu', run: () => removeEdge(TRIGGER, id) });
      parents.forEach(parent => items.push({ label: `dari ${parent.name || 'node'}`, run: () => removeEdge(parent.id, id) }));
    }
    items.push({ separator: true }, { label: 'Hapus node', danger: true, hint: 'Del', run: () => removeNode(id) });
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
      { label: 'Sisipkan node di sini…', run: () => insertOnEdge(fromId, toId) },
      { label: 'Putus sambungan', danger: true, hint: 'Del', run: () => removeEdge(fromId, toId) },
    ];
  }

  function insertOnEdge(fromId, toId) {
    const to = memberById(toId);
    const from = fromId === TRIGGER ? null : memberById(fromId);
    openCreator({ from: fromId, to: toId, branch: from && isBranching(from) ? (to.branch || 'A') : '' });
  }

  function boardMenu(point) {
    return [
      { label: 'Tambah node di sini…', hint: 'Tab', run: () => openCreator(point ? { at: point, from: null } : {}) },
      { label: 'Tempel node', hint: 'Ctrl+V', disabled: S.clipboard ? false : 'Belum ada node yang disalin', run: () => pasteNode(S.clipboard, point) },
      { separator: true },
      { label: 'Urungkan', hint: 'Ctrl+Z', disabled: S.past.length ? false : 'Tidak ada', run: undo },
      { label: 'Ulangi', hint: 'Ctrl+Shift+Z', disabled: S.future.length ? false : 'Tidak ada', run: redo },
      { separator: true },
      { label: 'Muat semua node', hint: '1', run: fitView },
      { label: 'Zoom 100%', hint: '0', run: () => { V.k = 1; applyView(); } },
      { label: 'Rapikan tata letak', run: tidy },
      { separator: true },
      { label: 'Simpan alur', hint: 'Ctrl+S', disabled: S.dirty ? false : 'Tidak ada perubahan', run: saveAndSay },
      { label: 'Jalankan alur', disabled: $('run').disabled ? 'Alur sedang berjalan' : false, run: () => $('run-form').requestSubmit() },
    ];
  }

  function tidy() {
    checkpoint();
    autoLayout(true);
    markDirty();
    render();
    fitView();
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
    const pending = S.pending || {};
    const fromId = 'from' in pending ? pending.from : (S.selected || (S.members.length ? null : TRIGGER));
    const from = fromId === TRIGGER ? { name: 'Pemicu' } : fromId ? memberById(fromId) : null;
    // While searching, one list with name matches first, so Enter picks what was typed.
    const score = item => (item.label.toLowerCase().startsWith(q) ? 3 : item.label.toLowerCase().includes(q) ? 2 : 1);
    const groups = q
      ? [{ key: 'found', label: 'Hasil', items: CATALOG.flatMap(g => g.items.filter(match).map(item => ({ ...item, cat: g.key })))
        .sort((a, b) => score(b) - score(a)) }].filter(g => g.items.length)
      : CATALOG.map(group => ({ ...group, items: group.items.filter(match) })).filter(g => g.items.length);
    const triggers = TRIGGERS.filter(t => !q || `${t.label} ${t.desc} pemicu trigger jadwal`.toLowerCase().includes(q));
    const into = pending.to ? memberById(pending.to) : null;
    $('creator-context').textContent = into ? `Disisipkan antara ${from ? from.name : '?'} dan ${into.name}`
      : from ? `Disambung setelah ${from.name}${pending.branch ? `, jalur ${pending.branch}` : ''}` : 'Ditaruh tanpa sambungan. Sambungkan sendiri nanti.';
    $('creator-list').innerHTML = groups.map(group => `
      <div class="creator-group"><h4>${esc(group.label)}</h4>
        ${group.items.map(item => `<button class="creator-item" data-kind="${item.kind}" data-cat="${item.cat || group.key}" type="button">
          <span class="node-icon">${nodeIcon(item.kind, 18)}</span><span><b>${esc(item.label)}</b><small>${esc(item.desc)}</small></span></button>`).join('')}
      </div>`).join('') + (triggers.length && !pending.to ? `
      <div class="creator-group"><h4>Ganti pemicu</h4>
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
        checkpoint();
        S.schedule = { ...(S.schedule || {}), mode: button.dataset.trigger };
        closeCreator();
        afterChange();
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
    S.lastTpl = null;
    selectNode(id);
    $('ndv').classList.remove('hidden');
    S.ndvTab = 'params';
    renderNdv();
    const member = memberById(id);
    if (member && ['agent', 'datatable'].includes(kindOf(member))) refreshSources();
    if (member && kindOf(member) === 'datatable' && member.config.source) loadTables(member.config.source);
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
    $('ndv-kind').textContent = isTrigger ? 'Pemicu' : LABEL[kind] || 'Node';
    const title = $('ndv-title');
    if (document.activeElement !== title) title.value = isTrigger ? 'Kapan alur ini berjalan' : member.name || '';
    title.readOnly = isTrigger;
    $('ndv-remove').classList.toggle('hidden', isTrigger);
    $('ndv-test').classList.toggle('hidden', isTrigger || kind === 'agent');
    $('ndv-input').innerHTML = isTrigger ? triggerInput() : inputHtml(member);
    $('ndv-output').innerHTML = isTrigger ? triggerOutput() : outputHtml(member);
    bindIo($('ndv-input'));
    bindIo($('ndv-output'));
    if (isTrigger) renderTrigger($('ndv-params'));
    else renderParams($('ndv-params'), member);
    document.querySelectorAll('#ndv-tabs button').forEach(b => b.classList.toggle('on', b.dataset.pane === S.ndvTab));
    $('ndv').dataset.pane = S.ndvTab;
  }

  const runState = id => (S.run && S.run.agents ? S.run.agents[id] : null);
  const testState = id => S.tests[id] || null;
  /* What a node sent out most recently: its own test, else the last run. */
  function lastOutput(id) {
    const test = testState(id);
    const run = runState(id);
    const runAt = run && run.completed_at ? run.completed_at * 1000 : 0;
    if (test && (!runAt || test.at > runAt)) return { content: test.content || '', note: test.note, error: test.error, branch: test.branch, from: 'test' };
    if (run && ['completed', 'failed'].includes(run.status)) return { content: run.content || '', note: run.note, error: run.error, branch: run.branch, status: run.status, from: 'run' };
    return null;
  }

  /* The text a node would receive now: what its incoming lines carry, else the trigger's input. */
  /* Before the earlier nodes have run, a test uses the trigger's input, so one node can be tried on its own. */
  function inputFor(member) {
    const parts = parentsOf(member).map(p => lastOutput(p.id)).filter(o => o && o.content && !o.error).map(o => o.content);
    if (parts.length) return [...new Set(parts)].join('\n\n');
    return $('task').value.trim() || (S.run ? S.run.task : '');
  }

  function parseJson(text) {
    const trimmed = String(text || '').trim();
    if (!/^[[{]/.test(trimmed)) return undefined;
    try { return JSON.parse(trimmed); } catch { return undefined; }
  }

  /* The fields of a JSON input, as {{json.x}} chips to click into a box. */
  function fieldChips(text) {
    const data = parseJson(text);
    if (data === undefined) return '';
    const first = Array.isArray(data) ? data.find(x => x && typeof x === 'object') : data;
    if (!first || typeof first !== 'object') return '';
    const keys = Object.keys(first).slice(0, 40);
    return `<div class="io-fields"><span class="hint">Klik untuk memasukkan ke kotak yang aktif:</span>
      ${keys.map(k => `<button class="chip-btn" type="button" data-field-chip="{{json.${esc(k)}}}" title="${esc(String(typeof first[k] === 'object' ? JSON.stringify(first[k]) : first[k]).slice(0, 200))}">${esc(k)}</button>`).join('')}
      ${Array.isArray(data) ? `<button class="chip-btn" type="button" data-field-chip="{{json.count}}">jumlah baris</button>` : ''}
      <button class="chip-btn" type="button" data-field-chip="{{input}}">seluruh input</button></div>`;
  }

  /* JSON as a table or as text; anything else as markdown. */
  function ioView(text, key) {
    if (!text) return '';
    const data = parseJson(text);
    if (data === undefined) return `<div class="io-text prose">${md(String(text).slice(0, 20000))}</div>`;
    const rows = Array.isArray(data) ? data : [data];
    const table = rows.length && rows.every(r => r && typeof r === 'object' && !Array.isArray(r));
    const mode = S.ioMode[key] || (table ? 'table' : 'json');
    const toggle = table ? `<div class="io-toggle" role="group" aria-label="Tampilan">
        <button type="button" data-io-mode="table" data-io-key="${esc(key)}" class="${mode === 'table' ? 'on' : ''}">Tabel</button>
        <button type="button" data-io-mode="json" data-io-key="${esc(key)}" class="${mode === 'json' ? 'on' : ''}">JSON</button></div>` : '';
    if (mode === 'table' && table) {
      const columns = [...new Set(rows.flatMap(r => Object.keys(r)))].slice(0, 40);
      const cell = v => (v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v));
      return `${toggle}<div class="io-table-wrap"><table class="io-table"><thead><tr>${columns.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
        <tbody>${rows.slice(0, 200).map(r => `<tr>${columns.map(c => `<td title="${esc(cell(r[c]).slice(0, 300))}">${esc(cell(r[c]).slice(0, 120))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
        <p class="hint">${rows.length} baris${rows.length > 200 ? ', 200 pertama ditampilkan' : ''}</p>`;
    }
    return `${toggle}<pre class="io-json">${esc(JSON.stringify(data, null, 2).slice(0, 40000))}</pre>`;
  }

  function bindIo(box) {
    enhanceCode(box);
    box.querySelectorAll('[data-io-mode]').forEach(b => { b.onclick = () => { S.ioMode[b.dataset.ioKey] = b.dataset.ioMode; renderNdv(); }; });
    box.querySelectorAll('[data-field-chip]').forEach(b => {
      b.onmousedown = e => e.preventDefault();   // keep the focus in the box being filled
      b.onclick = () => insertTemplate(b.dataset.fieldChip);
    });
  }

  function insertTemplate(text) {
    const target = S.lastTpl && document.body.contains(S.lastTpl) ? S.lastTpl : $('ndv-params').querySelector('[data-tpl]');
    if (!target) { copyText(text); return; }
    const at = target.selectionStart ?? target.value.length;
    target.value = target.value.slice(0, at) + text + target.value.slice(target.selectionEnd ?? at);
    target.dispatchEvent(new Event('input', { bubbles: true }));
    target.focus();
    const caret = at + text.length;
    try { target.setSelectionRange(caret, caret); } catch { /* number inputs */ }
  }

  function inputHtml(member) {
    const parents = parentsOf(member);
    const blocks = parents.map(p => {
      const out = lastOutput(p.id);
      const status = out ? (out.error ? 'failed' : 'completed') : '';
      return `<div class="io-block"><div class="io-head">dari ${esc(p.name)} <span class="io-status" data-status="${status}">${out ? (out.from === 'test' ? 'hasil tes' : STATUS[out.status] || '') : 'belum ada'}</span></div>
        ${out && out.content ? ioView(out.content, `in-${p.id}`) : '<p class="io-empty">Belum ada isi. Jalankan alur atau tes node itu.</p>'}</div>`;
    });
    if (isStart(member.id) || !parents.length) {
      const task = $('task').value.trim() || (S.run ? S.run.task : '');
      blocks.unshift(`<div class="io-block"><div class="io-head">dari Pemicu</div>${task ? ioView(task, 'in-trigger') : `<p class="io-empty">${isStart(member.id) ? 'Isi kotak input di bawah kanvas, atau jalankan alur.' : 'Node ini belum tersambung, jadi tidak dijalankan.'}</p>`}</div>`);
    }
    return fieldChips(inputFor(member)) + blocks.join('');
  }

  function outputHtml(member) {
    const out = lastOutput(member.id);
    const run = runState(member.id);
    if (!out) {
      if (run && run.status === 'skipped') return '<p class="io-empty">Tidak dijalankan di eksekusi terakhir: tidak ada jalur yang sampai ke sini.</p>';
      if (run && run.status === 'running') return '<p class="io-empty">Sedang berjalan…</p>';
      return `<p class="io-empty">${kindOf(member) === 'agent' ? 'Belum dijalankan. Tekan Jalankan alur di bawah kanvas.' : 'Belum ada output. Tekan Tes langkah, atau Jalankan alur.'}</p>`;
    }
    const facts = [
      out.from === 'test' ? 'Hasil tes' : STATUS[out.status] || out.status,
      out.branch ? `jalur ${out.branch}` : '',
      out.from === 'run' && run && run.completed_at && run.started_at ? formatSeconds((run.completed_at - run.started_at) * 1000) : '',
      out.from === 'run' && run && run.tokens ? `${formatTokens(run.tokens)} token` : '',
    ].filter(Boolean).join(' · ');
    return `<div class="io-head"><span class="io-status" data-status="${out.error ? 'failed' : 'completed'}">${esc(facts)}</span></div>
      ${out.error ? `<div class="notice">${esc(out.error)}</div>` : ''}
      ${out.note ? `<p class="hint">${esc(out.note)}</p>` : ''}
      ${out.content ? ioView(out.content, `out-${member.id}`) : out.error ? '' : '<p class="io-empty">Tidak ada isi.</p>'}`;
  }

  function triggerInput() {
    return `<div class="io-block"><div class="io-head">Pemicu</div><p>${esc(triggerLabel())}</p></div>
      <p class="hint">Saat dijalankan manual, isi kotak input di bawah kanvas menjadi input pertama.</p>`;
  }
  function triggerOutput() {
    if (!S.run) return '<p class="io-empty">Belum ada eksekusi.</p>';
    return `<div class="io-block"><div class="io-head">Dikirim ke langkah pertama</div>${ioView(S.run.task, 'trigger-out') || '<p class="io-empty">Tanpa input.</p>'}</div>`;
  }

  /* ── Tes langkah ─────────────────────────────────── */
  function serializeMember(m) {
    return {
      id: m.id, name: m.name || LABEL[kindOf(m)] || 'Node', role: m.role || m.name || 'Node', model_id: m.model_id || '',
      instructions: m.instructions || '', depends_on: m.depends_on || [], kind: kindOf(m), branch: m.branch || '', config: m.config || {},
    };
  }

  async function testNode() {
    const member = memberById(S.ndv);
    if (!member || kindOf(member) === 'agent') return;
    const writes = WRITES[kindOf(member)];
    if (writes && writes(member.config || {}) && !confirm('Tes ini benar-benar mengubah data atau mengirim pesan. Lanjutkan?')) return;
    const button = $('ndv-test');
    button.disabled = true;
    button.textContent = 'Menjalankan…';
    try {
      const result = await post('/api/teams/node-test', {
        member: serializeMember(member), input: inputFor(member), task: $('task').value.trim(), team_name: $('team-name').value.trim(),
      });
      const borrowed = parentsOf(member).length && !parentsOf(member).some(p => lastOutput(p.id) && lastOutput(p.id).content);
      S.tests[member.id] = { ...result, note: [result.note, borrowed ? 'memakai input Pemicu, karena node sebelumnya belum punya output' : ''].filter(Boolean).join(' · '), at: Date.now() };
      if (kindOf(member) === 'datatable' && (member.config || {}).operation !== 'get') loadTables(member.config.source, true);
    } catch (e) {
      S.tests[member.id] = { content: '', error: e.message, at: Date.now() };
    }
    button.disabled = false;
    button.textContent = '▶ Tes langkah';
    S.ndvTab = 'output';
    renderNdv();
    paintRun();
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
      body = `<label class="field"><span>${hint}</span><textarea ${attr} data-key="value" data-tpl rows="2" maxlength="1000">${esc(condition.value || '')}</textarea></label>`;
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

  const TEMPLATE_CHIPS = ['input', 'json.kolom', 'task', 'date', 'time', 'day', 'team'];
  const chipsRow = () => `<div class="chips-row">${TEMPLATE_CHIPS.map(v => `<button class="chip-btn" type="button" data-insert="{{${v}}}">{{${v}}}</button>`).join('')}</div>`;

  function kindFields(member) {
    const config = member.config || {};
    switch (kindOf(member)) {
      case 'delay':
        return `<label class="field"><span>Tunggu (detik, maksimal 3600)</span><input data-config="seconds" type="number" min="1" max="3600" value="${esc(config.seconds || 30)}"></label>
          <p class="hint">Jeda ini tidak dihitung dalam batas waktu alur.</p>`;
      case 'gate': {
        const list = conditionsOf(config);
        const combine = config.combine || 'and';
        const needsModel = list.some(c => c.mode === 'ai');
        return `<label class="field"><span>Gabungkan syarat</span><select data-config="combine">
            <option value="and" ${combine === 'and' ? 'selected' : ''}>AND, semua harus terpenuhi</option>
            <option value="or" ${combine === 'or' ? 'selected' : ''}>OR, salah satu cukup</option>
          </select></label>
          ${list.map((c, i) => conditionFields(c, i, 'conditions')).join('')}
          <button class="btn btn-ghost btn-small btn-block" type="button" data-cond-add="conditions">+ Syarat</button>
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
        return `<label class="field"><span>Isi teks</span><textarea data-config="text" data-tpl rows="8" maxlength="8000">${esc(config.text || '')}</textarea></label>
          ${chipsRow()}
          <p class="hint">{{json.kolom}} mengambil satu kolom dari input JSON (misalnya hasil Tabel data). {{node.ID}} mengambil isi node lain.</p>`;
      case 'http': {
        const method = config.method || 'GET';
        return `<label class="field"><span>Metode</span><select data-config="method">
              <option ${method === 'GET' ? 'selected' : ''}>GET</option><option ${method === 'POST' ? 'selected' : ''}>POST</option>
            </select></label>
          <label class="field"><span>URL</span><input data-config="url" value="${esc(config.url || '')}" placeholder="https://..." maxlength="2000"></label>
          ${method === 'POST' ? `<label class="field"><span>Isi (JSON atau teks)</span><textarea data-config="body" data-tpl rows="4" maxlength="8000">${esc(config.body || '')}</textarea></label>${chipsRow()}` : ''}
          <p class="hint">Hanya alamat publik. Balasannya diteruskan ke node berikutnya.</p>`;
      }
      case 'telegram': return telegramFields(member);
      case 'database': {
        const options = (S.integrations || {}).databases || [{ id: 'sqlite', label: 'SQLite (file lokal)', ready: true }];
        const sources = S.dataSources || [];
        const value = config.source ? `src:${config.source}` : `db:${config.database || 'sqlite'}`;
        const chosen = config.source ? null : (options.find(o => o.id === (config.database || 'sqlite')) || options[0]);
        const missing = config.source && !sources.some(x => x.name === config.source);
        return `<div class="field"><span>Simpan ke</span><div class="db-pick">
            <select data-db-target aria-label="Simpan ke">
              <optgroup label="Database log bawaan">${options.map(o => `<option value="db:${esc(o.id)}" ${value === `db:${o.id}` ? 'selected' : ''}>${esc(o.label)}${o.ready ? '' : ' (belum siap)'}</option>`).join('')}</optgroup>
              <optgroup label="Sumber di Tabel data">${sources.map(x => `<option value="src:${esc(x.name)}" ${value === `src:${x.name}` ? 'selected' : ''}>${esc(x.label || x.name)}</option>`).join('')}
                ${missing ? `<option value="src:${esc(config.source)}" selected>${esc(config.source)} (sudah tidak ada)</option>` : ''}</optgroup>
            </select>
            <button class="btn btn-ghost btn-small" type="button" data-db-add title="Tambah sumber data: tabel bawaan, file, Google Sheets, PostgreSQL, MySQL, MongoDB">+ Sumber</button>
          </div></div>
          ${chosen && chosen.reason ? `<p class="hint hint-warn">${esc(chosen.reason)} <button class="link-btn" type="button" data-go-creds>Buka Kredensial</button></p>` : ''}
          <label class="field"><span>Nama tabel log</span><input data-config="table" value="${esc(config.table || 'hasil_alur')}" maxlength="63"></label>
          <p class="hint">Tiap eksekusi mencatat satu baris: tugas, isi, waktu. Tabelnya dibuat otomatis bila belum ada. Untuk tabel dengan kolom sendiri, pakai node Tabel data.</p>`;
      }
      default: return '';
    }
  }

  /* ── Telegram node ───────────────────────────────── */
  const PAGE_MODE = {
    own: url => `Jendela web memakai alamatmu sendiri (${url}).`,
    tunnel: url => `Jendela web memakai alamat tunnel web server (${url}).`,
    auto: url => (url ? `Jendela web aktif di ${url}. Hanya halaman hasil yang terbuka ke internet.`
      : 'Alamat publik untuk jendela web dibuat otomatis (Cloudflare Tunnel) saat pertama dipakai. Hanya halaman hasil yang terbuka ke internet.'),
  };

  function telegramFields(member) {
    const config = member.config || {};
    const telegram = (S.integrations || {}).telegram || {};
    const pages = telegram.pages || { mode: 'auto', url: '' };
    const style = config.style || 'auto';
    const buttons = config.buttons || [];
    return `${credentialNote(telegram.ready, 'Bot Telegram')}
      <label class="field"><span>Chat ID</span><input data-config="chat_id" value="${esc(config.chat_id || '')}" placeholder="${telegram.default_chat ? 'Kosong = chat yang memicu, atau chat di Kredensial' : 'Angka atau @nama_channel'}" maxlength="70"></label>
      <label class="field"><span>Isi pesan</span><textarea data-config="text" data-tpl rows="5" maxlength="8000" placeholder="Kosong: kirim input apa adanya. Contoh: Halo! Ada **{{json.count}}** pesanan baru, terbaru dari {{json.nama}}.">${esc(config.text || '')}</textarea></label>
      ${chipsRow()}
      <div class="field"><span>Tampilan</span>
        <div class="seg-choice">
          <label><input type="radio" name="tg-style" value="auto" ${style === 'auto' ? 'checked' : ''}><span>Rapi otomatis</span></label>
          <label><input type="radio" name="tg-style" value="plain" ${style === 'plain' ? 'checked' : ''}><span>Teks biasa</span></label>
        </div>
        <p class="hint">${style === 'auto' ? 'Markdown jadi huruf tebal, miring, daftar, dan kutipan. Tabel dibuat muat di layar HP. Data JSON (misalnya dari Tabel data) jadi daftar singkat yang mudah dibaca.' : 'Dikirim persis seperti tulisannya.'}</p>
      </div>
      <div class="field"><span>Tombol di bawah pesan</span>
        ${buttons.map((b, i) => `<div class="tg-button-row">
            <input data-tg-button="${i}" data-key="label" value="${esc(b.label || '')}" placeholder="Tulisan tombol" maxlength="64" aria-label="Tulisan tombol ${i + 1}">
            <select data-tg-button="${i}" data-key="kind" aria-label="Jenis tombol ${i + 1}">
              <option value="web" ${b.kind !== 'link' ? 'selected' : ''}>Buka jendela web</option>
              <option value="link" ${b.kind === 'link' ? 'selected' : ''}>Tautan</option>
            </select>
            <button class="icon-btn" type="button" data-tg-button-remove="${i}" aria-label="Hapus tombol ${i + 1}">×</button>
            ${b.kind === 'link' ? `<input class="tg-button-url" data-tg-button="${i}" data-key="url" data-tpl value="${esc(b.url || '')}" placeholder="https://… boleh pakai {{json.kode}}" aria-label="Alamat tombol ${i + 1}">` : ''}
          </div>`).join('')}
        <button class="btn btn-ghost btn-small" type="button" data-tg-button-add>+ Tombol</button>
        <p class="hint">"Buka jendela web" membuka isi lengkap (input node ini) sebagai halaman di dalam Telegram, rapi di HP: tabel untuk data, teks untuk jawaban. Di grup atau channel tombolnya jadi tautan biasa.</p>
      </div>
      ${buttons.some(b => b.kind !== 'link') || style === 'auto' ? `<label class="field"><span>Judul jendela web</span><input data-config="page_title" data-tpl value="${esc(config.page_title || '')}" placeholder="${esc(member.name || 'Hasil')}" maxlength="120"></label>` : ''}
      <label class="check"><input type="checkbox" data-flag="silent" ${config.silent ? 'checked' : ''}>Kirim tanpa bunyi notifikasi</label>
      ${style === 'auto' ? `<label class="check"><input type="checkbox" data-flag="no_summary" ${config.no_summary ? 'checked' : ''}>Pesan panjang dikirim utuh (beberapa pesan), bukan ringkasan dengan tombol Buka lengkap</label>` : ''}
      <p class="hint">${esc((PAGE_MODE[pages.mode] || PAGE_MODE.auto)(pages.url))} <button class="link-btn" type="button" data-go-creds>Atur di Kredensial</button></p>
      <div class="field"><span>Pratinjau</span><div id="tg-preview" class="tg-preview" aria-live="polite"><p class="hint">Memuat pratinjau…</p></div></div>
      <button id="test-telegram" class="btn btn-ghost btn-small btn-block" type="button" ${telegram.ready ? '' : 'disabled'}>Kirim pesan tes sederhana</button>`;
  }

  /* How the message will look in Telegram, from the input this node would get now. Nothing is sent. */
  function previewTelegram(member) {
    clearTimeout(S.tgPreviewTimer);
    S.tgPreviewTimer = setTimeout(async () => {
      const box = $('tg-preview');
      if (!box || S.ndv !== member.id) return;
      const input = inputFor(member) || 'Contoh isi dari node sebelumnya. **Tebal**, _miring_, dan daftar:\n- satu\n- dua';
      try {
        const out = await post('/api/teams/telegram/preview', { config: member.config || {}, input, team_name: $('team-name').value.trim() });
        if (!document.body.contains(box)) return;
        const allowed = { ALLOWED_TAGS: ['b', 'strong', 'i', 'em', 'u', 's', 'code', 'pre', 'a', 'blockquote', 'br'], ALLOWED_ATTR: ['href', 'class'] };
        const clean = html => (window.DOMPurify ? window.DOMPurify.sanitize(html, allowed) : esc(html));
        box.innerHTML = out.parts.map(part => `<div class="tg-bubble">${out.html ? clean(part) : esc(part)}</div>`).join('')
          + (out.buttons.length ? `<div class="tg-keys">${out.buttons.map(b => `<span class="tg-key">${b.kind === 'web' ? '▣ ' : '↗ '}${esc(b.label)}</span>`).join('')}</div>` : '')
          + (inputFor(member) ? '' : '<p class="hint">Contoh. Tes atau jalankan node sebelumnya untuk melihat pesan aslinya.</p>');
      } catch (e) {
        if (document.body.contains(box)) box.innerHTML = `<p class="hint hint-warn">${esc(e.message)}</p>`;
      }
    }, 350);
  }

  function bindTelegram(box, member) {
    const save = (changes, rerender = true, merge = '') => {
      checkpoint(merge);
      member.config = { ...(member.config || {}), ...changes };
      markDirty();
      refreshNodeCard(member);
      if (rerender) renderParams(box, member); else previewTelegram(member);
    };
    box.querySelectorAll('input[name="tg-style"]').forEach(radio => { radio.onchange = () => save({ style: radio.value === 'auto' ? undefined : radio.value }); });
    box.querySelectorAll('[data-tg-button]').forEach(el => {
      const live = el.tagName === 'INPUT';
      el[live ? 'oninput' : 'onchange'] = () => {
        const list = clone(member.config.buttons || []);
        const index = Number(el.dataset.tgButton);
        list[index] = { ...list[index], [el.dataset.key]: el.value };
        save({ buttons: list }, !live, live ? `tg-button-${member.id}-${index}-${el.dataset.key}` : '');
      };
    });
    box.querySelector('[data-tg-button-add]')?.addEventListener('click', () => save({ buttons: [...(member.config.buttons || []), { label: 'Buka lengkap', kind: 'web' }] }));
    box.querySelectorAll('[data-tg-button-remove]').forEach(b => {
      b.onclick = () => save({ buttons: (member.config.buttons || []).filter((_, i) => i !== Number(b.dataset.tgButtonRemove)) });
    });
    box.querySelectorAll('[data-config], [data-flag]').forEach(el => el.addEventListener(el.type === 'checkbox' ? 'change' : 'input', () => previewTelegram(member)));
    previewTelegram(member);
  }

  /* ── Data table node ─────────────────────────────── */
  async function loadTables(source, force = false) {
    if (!source) return;
    const cached = S.dtTables[source];
    if (cached && !force && (cached.tables || cached.loading)) return;
    S.dtTables[source] = { loading: true };
    try {
      const data = await api(`/api/data/sources/${enc(source)}/tables`);
      S.dtTables[source] = { tables: data.tables };
    } catch (e) {
      S.dtTables[source] = { error: e.message };
    }
    const member = memberById(S.ndv);
    if (member && kindOf(member) === 'datatable' && member.config.source === source && !document.activeElement?.closest('#ndv-params')) renderNdv();
  }

  function tableInfo(config) {
    const entry = S.dtTables[config.source] || {};
    return (entry.tables || []).find(t => t.name === config.table) || null;
  }

  function relationLabel(r, source, table) {
    const outgoing = r.source === source && r.table === table;
    return outgoing
      ? `${r.name}: ${r.column} → ${r.target_table}.${r.target_column} (satu baris)`
      : `${r.reverse}: ${r.table}.${r.column} → ${r.target_column} (banyak baris)`;
  }

  function datatableFields(member) {
    const c = member.config || {};
    const sources = S.dataSources || [];
    const op = c.operation || 'get';
    const entry = S.dtTables[c.source] || {};
    const info = tableInfo(c);
    const columns = info ? info.columns : [];
    const colOptions = value => `<option value="">Pilih kolom</option>${columns.map(col => `<option ${col === value ? 'selected' : ''}>${esc(col)}</option>`).join('')}${value && !columns.includes(value) ? `<option selected>${esc(value)}</option>` : ''}`;
    const sourcePick = `<label class="field"><span>Sumber</span><select data-dt="source">
        <option value="">Pilih sumber data</option>
        ${sources.map(s => `<option value="${esc(s.name)}" ${s.name === c.source ? 'selected' : ''}>${esc(s.label || s.name)}</option>`).join('')}
        ${c.source && !sources.some(s => s.name === c.source) ? `<option value="${esc(c.source)}" selected>${esc(c.source)} (tidak ada lagi)</option>` : ''}
      </select></label>`;
    let tablePick = '';
    if (c.source) {
      if (entry.loading || (!entry.tables && !entry.error)) tablePick = '<p class="hint">Memuat tabel…</p>';
      else if (entry.error) tablePick = `<p class="hint hint-warn">${esc(entry.error)} <button class="link-btn" type="button" data-dt-reload>Coba lagi</button></p>`;
      else {
        tablePick = `<label class="field"><span>Tabel</span><select data-dt="table">
            <option value="">Pilih tabel</option>
            ${entry.tables.map(t => `<option value="${esc(t.name)}" ${t.name === c.table ? 'selected' : ''}>${esc(t.name)}${t.rows !== null && t.rows !== undefined ? ` (${t.rows} baris)` : ''}</option>`).join('')}
          </select></label>`;
      }
    }
    const header = `<div class="dt-pick">${sourcePick}${tablePick}</div>
      <div class="dt-links"><button class="link-btn" type="button" data-dt-new>+ Tabel baru</button>
        ${c.table ? '<button class="link-btn" type="button" data-dt-open>Lihat isi tabel</button>' : ''}
        <button class="link-btn" type="button" data-dt-reload>Muat ulang</button></div>`;
    if (!c.source || !c.table) return `${header}<p class="hint">Pilih tabel yang mau dipakai, atau buat baru. Tabel baru disimpan di "Tabel data" milik aplikasi ini.</p>`;
    const where = c.where || [];
    const whereHtml = ['get', 'update', 'delete'].includes(op) ? `
      <div class="field"><span>Syarat${where.length > 1 ? `, <select data-dt="combine" class="inline-select" aria-label="Gabungan syarat"><option value="and" ${c.combine !== 'or' ? 'selected' : ''}>semua cocok (AND)</option><option value="or" ${c.combine === 'or' ? 'selected' : ''}>salah satu cocok (OR)</option></select>` : ''}</span>
        ${where.map((w, i) => `<div class="dt-row">
            <select data-dt-where="${i}" data-key="column" aria-label="Kolom syarat ${i + 1}">${colOptions(w.column)}</select>
            <select data-dt-where="${i}" data-key="op" aria-label="Operator syarat ${i + 1}">${DT_OPS.map(([v, l]) => `<option value="${esc(v)}" ${(w.op || '=') === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
            <input data-dt-where="${i}" data-key="value" data-tpl value="${esc(w.value || '')}" placeholder="nilai atau {{json.kolom}}" aria-label="Nilai syarat ${i + 1}">
            <button class="icon-btn" type="button" data-dt-where-remove="${i}" aria-label="Hapus syarat ${i + 1}">×</button></div>`).join('')}
        <button class="btn btn-ghost btn-small" type="button" data-dt-where-add>+ Syarat</button>
        ${op === 'get' && !where.length ? '<p class="hint">Tanpa syarat, semua baris diambil.</p>' : ''}
      </div>` : '';
    const values = c.values || [];
    const match = c.match || [];
    const valuesHtml = ['insert', 'update', 'upsert'].includes(op) ? `
      <div class="field"><span>Nilai kolom${op === 'upsert' ? ' (centang kolom pencocok)' : ''}</span>
        ${values.map((v, i) => `<div class="dt-row ${op === 'upsert' ? 'with-match' : ''}">
            ${op === 'upsert' ? `<label class="check dt-match" title="Kolom pencocok"><input type="checkbox" data-dt-match="${esc(v.column)}" ${match.includes(v.column) ? 'checked' : ''} aria-label="Pencocok ${esc(v.column)}"></label>` : ''}
            <select data-dt-value="${i}" data-key="column" aria-label="Kolom ${i + 1}">${colOptions(v.column)}</select>
            <input data-dt-value="${i}" data-key="value" data-tpl value="${esc(v.value || '')}" placeholder="nilai atau {{json.kolom}}" aria-label="Nilai kolom ${i + 1}">
            <button class="icon-btn" type="button" data-dt-value-remove="${i}" aria-label="Hapus kolom ${i + 1}">×</button></div>`).join('')}
        <div class="row-actions"><button class="btn btn-ghost btn-small" type="button" data-dt-value-add>+ Kolom</button>
          <button class="btn btn-ghost btn-small" type="button" data-dt-value-all>Isi semua kolom</button></div>
      </div>${chipsRow()}` : '';
    const relations = (S.relations || []).filter(r => (r.source === c.source && r.table === c.table) || (r.target_source === c.source && r.target_table === c.table));
    const getHtml = op === 'get' ? `
      <div class="field-row">
        <label class="field"><span>Urutkan</span><select data-dt="sort"><option value="">Urutan asli</option>${columns.map(col => `<option ${col === c.sort ? 'selected' : ''}>${esc(col)}</option>`).join('')}</select></label>
        <label class="field"><span>Arah</span><select data-dt="desc" ${c.sort ? '' : 'disabled'}><option value="">Naik</option><option value="1" ${c.desc ? 'selected' : ''}>Turun</option></select></label>
        <label class="field"><span>Batas baris</span><input data-dt="limit" type="number" min="0" value="${esc(c.limit || '')}" placeholder="semua"></label>
      </div>
      <div class="field"><span>Ikut ambil baris yang berelasi</span>
        ${relations.length ? relations.map(r => `<label class="check"><input type="checkbox" data-dt-include="${esc(r.id)}" ${(c.include || []).some(x => x === r.id || x === r.name) ? 'checked' : ''}>${esc(relationLabel(r, c.source, c.table))}</label>`).join('')
          : '<p class="hint">Tabel ini belum punya relasi.</p>'}
        <button class="link-btn" type="button" data-dt-relations>Atur relasi</button>
      </div>` : '';
    return `${header}
      <div class="field"><span>Operasi</span><div class="seg-choice dt-ops">${OPERATIONS.map(([v, l]) =>
        `<label><input type="radio" name="dt-op" value="${v}" ${op === v ? 'checked' : ''}><span>${l}</span></label>`).join('')}</div>
        <p class="hint">${OPERATION_HINT[op]}</p></div>
      ${whereHtml}${valuesHtml}${getHtml}
      <p class="hint">Hasilnya JSON, jadi node berikutnya bisa memakai {{json.kolom}}.</p>`;
  }

  function bindDatatable(box, member) {
    const save = (changes, rerender = true, merge = '') => {
      checkpoint(merge);
      member.config = { ...(member.config || {}), ...changes };
      markDirty();
      refreshNodeCard(member);
      if (rerender) renderParams(box, member);
    };
    box.querySelectorAll('[data-dt]').forEach(el => {
      const key = el.dataset.dt;
      const live = el.tagName === 'INPUT';
      el[live ? 'oninput' : 'onchange'] = () => {
        if (key === 'source') { save({ source: el.value, table: '', where: [], values: [], match: [], include: [], sort: '' }); loadTables(el.value); return; }
        if (key === 'table') { save({ table: el.value, where: [], values: [], match: [], include: [], sort: '' }); return; }
        if (key === 'limit') { save({ limit: Number(el.value) || 0 }, false, `dt-limit-${member.id}`); return; }
        if (key === 'desc') { save({ desc: Boolean(el.value) }); return; }
        save({ [key]: el.value });
      };
    });
    box.querySelectorAll('input[name="dt-op"]').forEach(radio => { radio.onchange = () => save({ operation: radio.value }); });
    const listEdit = (attr, key) => {
      box.querySelectorAll(`[data-${attr}]`).forEach(el => {
        const live = el.tagName === 'INPUT';
        el[live ? 'oninput' : 'onchange'] = () => {
          const list = clone(member.config[key] || []);
          const index = Number(el.getAttribute(`data-${attr}`));
          list[index] = { ...list[index], [el.dataset.key]: el.value };
          save({ [key]: list }, !live, live ? `${attr}-${member.id}-${index}` : '');
        };
      });
    };
    listEdit('dt-where', 'where');
    listEdit('dt-value', 'values');
    const add = (key, item) => save({ [key]: [...(member.config[key] || []), item] });
    const removeAt = (key, index) => save({ [key]: (member.config[key] || []).filter((_, i) => i !== index) });
    box.querySelector('[data-dt-where-add]')?.addEventListener('click', () => add('where', { column: '', op: '=', value: '' }));
    box.querySelector('[data-dt-value-add]')?.addEventListener('click', () => add('values', { column: '', value: '' }));
    box.querySelectorAll('[data-dt-where-remove]').forEach(b => { b.onclick = () => removeAt('where', Number(b.dataset.dtWhereRemove)); });
    box.querySelectorAll('[data-dt-value-remove]').forEach(b => {
      b.onclick = () => {
        const gone = (member.config.values || [])[Number(b.dataset.dtValueRemove)];
        save({ values: (member.config.values || []).filter((_, i) => i !== Number(b.dataset.dtValueRemove)), match: (member.config.match || []).filter(m => !gone || m !== gone.column) });
      };
    });
    box.querySelector('[data-dt-value-all]')?.addEventListener('click', () => {
      const info = tableInfo(member.config);
      if (!info) return;
      const have = new Set((member.config.values || []).map(v => v.column));
      const skip = new Set([info.key, 'id'].filter(Boolean));
      const extra = info.columns.filter(col => !have.has(col) && !skip.has(col)).map(col => ({ column: col, value: `{{json.${col}}}` }));
      save({ values: [...(member.config.values || []).filter(v => v.column), ...extra] });
    });
    box.querySelectorAll('[data-dt-match]').forEach(check => {
      check.onchange = () => {
        const set = new Set(member.config.match || []);
        if (check.checked) set.add(check.dataset.dtMatch); else set.delete(check.dataset.dtMatch);
        save({ match: [...set] });
      };
    });
    box.querySelectorAll('[data-dt-include]').forEach(check => {
      check.onchange = () => {
        const relation = (S.relations || []).find(r => r.id === check.dataset.dtInclude);
        const set = new Set(member.config.include || []);
        if (check.checked) set.add(check.dataset.dtInclude);
        else { set.delete(check.dataset.dtInclude); if (relation) set.delete(relation.name); }
        save({ include: [...set] });
      };
    });
    box.querySelectorAll('[data-dt-reload]').forEach(b => { b.onclick = () => { refreshSources(); loadTables(member.config.source, true); }; });
    box.querySelector('[data-dt-new]')?.addEventListener('click', () => newDataTable(member));
    box.querySelector('[data-dt-open]')?.addEventListener('click', () => {
      if (!confirmLeave()) return;
      closeNdv();
      showHome('data');
      if (window.SQData && window.SQData.focus) window.SQData.focus(member.config.source, member.config.table);
    });
    box.querySelector('[data-dt-relations]')?.addEventListener('click', () => {
      if (window.SQData && window.SQData.relationDialog) {
        window.SQData.relationDialog(member.config.source, member.config.table, async () => { await refreshSources(); renderNdv(); });
      }
    });
  }

  /* A new table, like n8n's data tables: in this app's own "Tabel data" unless another source is picked. */
  function newDataTable(member) {
    const dialog = window.SQData && window.SQData.dialogForm;
    if (!dialog) return;
    const sources = (S.dataSources || []).filter(s => s.kind === 'file' ? /sqlite|db/.test(s.format) : ['postgres', 'mysql', 'mongodb'].includes(s.kind));
    dialog({
      title: 'Tabel baru',
      note: 'Kolom bisa ditambah atau diganti nama nanti di tab Tabel data. Kolom id dibuat otomatis.',
      fields: [
        { name: 'table', label: 'Nama tabel', placeholder: 'pelanggan', required: true, max: 64, hint: 'Huruf, angka, dan garis bawah.' },
        { name: 'columns', label: 'Kolom', type: 'textarea', rows: 2, placeholder: 'nama, hp, alamat', hint: 'Pisahkan dengan koma.' },
        { name: 'source', label: 'Simpan di', type: 'select', value: member.config.source && sources.some(s => s.name === member.config.source) ? member.config.source : '',
          options: [{ value: '', label: 'Tabel data (bawaan aplikasi)' }, ...sources.filter(s => s.name !== (S.builtin || 'tabel')).map(s => ({ value: s.name, label: s.label || s.name }))] },
      ],
      submit: 'Buat tabel',
      onSubmit: async values => {
        const made = await credApi('/api/data/tables', 'POST', {
          table: values.table, columns: values.columns.split(',').map(c => c.trim()).filter(Boolean), source: values.source,
        });
        await refreshSources(false);
        S.dtTables[made.source.name] = { tables: made.tables };
        checkpoint();
        member.config = { ...member.config, source: made.source.name, table: made.table.name, where: [], values: [], match: [], include: [] };
        markDirty();
        refreshNodeCard(member);
        renderNdv();
        if (window.SQData && window.SQData.changed) window.SQData.changed();
        toast(`Tabel ${made.table.name} dibuat.`);
      },
    });
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
      ...sources.map(x => ({ name: x.name, label: x.label || x.name })),
      ...grants.filter(g => !known.has(g.source)).map(g => ({ name: g.source, label: g.source, missing: true })),
    ];
    if (!rows.length) return '<p class="hint">Belum ada sumber data. <button class="link-btn" type="button" data-go-data>Buat di tab Tabel data</button></p>';
    return `<div class="grants">${rows.map(row => {
      const grant = grants.find(g => g.source === row.name) || {};
      return `<div class="grant" data-grant="${esc(row.name)}">
        <b>${esc(row.label)}${row.missing ? ' <small>(sumber ini sudah tidak ada)</small>' : ''}</b>
        <select data-grant-access aria-label="Akses ke ${esc(row.label)}">${ACCESS.map(([value, label]) =>
          `<option value="${value}" ${(grant.access || '') === value ? 'selected' : ''}>${label}</option>`).join('')}</select>
        ${grant.access ? `<input data-grant-hide value="${esc((grant.hide || []).join(', '))}" placeholder="Kolom yang disembunyikan, pisahkan dengan koma" aria-label="Kolom yang disembunyikan dari ${esc(row.label)}" maxlength="400">` : ''}
      </div>`;
    }).join('')}</div>
      <p class="hint">Agent hanya menyentuh sumber yang diizinkan di sini, dan bisa mengikuti relasi antar tabel. Untuk pengirim lain di bot publik, hak ubah dan hapus tidak pernah berlaku, dan kolom berawalan <code>_</code> selalu tersembunyi.</p>`;
  }

  function renderParams(box, member) {
    const kind = kindOf(member);
    const config = member.config || {};
    const fork = branchParent(member);
    const publicBot = (S.schedule || {}).mode === 'telegram' && (S.schedule || {}).audience === 'public';
    let body;
    if (kind === 'agent') {
      body = `
        <label class="check harness-check"><input type="checkbox" data-harness ${config.harness ? 'checked' : ''}><span><b>Harness penuh</b>, seperti chat lokal: semua alat dan skill, tanpa batas langkah, ingat seluruh percakapan, bekerja di folder alur ini</span></label>
        <label class="field"><span>Model</span><select data-field="model_id"></select></label>
        <label class="field"><span>Instruksi</span><textarea data-field="instructions" data-tpl rows="8" maxlength="4000" placeholder="Apa yang dikerjakan agent ini dan bentuk hasilnya. Contoh: Ringkas berita dari input jadi 5 poin.">${esc(member.instructions)}</textarea></label>
        <label class="field"><span>Peran (opsional)</span><input data-field="role" value="${esc(member.role)}" maxlength="120" placeholder="Misalnya: peneliti, penulis, analis data"></label>
        <details class="field more"><summary>Akses tabel data${(config.data || []).length ? ` (${config.data.length})` : ''}</summary>${grantsHtml(member)}</details>
        <details class="field more"><summary>Opsi lanjutan</summary>
          <label class="check"><input type="checkbox" data-flag="report" ${config.report ? 'checked' : ''}>Tulis sebagai laporan dengan bagian Hasil, Bukti, dan Belum pasti</label>
          <label class="check"><input type="checkbox" data-flag="direct" ${config.direct ? 'checked' : ''}>Jawab seperti chat singkat untuk orang (cocok untuk bot)</label>
          ${publicBot ? `<label class="check"><input type="checkbox" data-flag="notify_owner" ${config.notify_owner ? 'checked' : ''}>Boleh mengabari saya lewat Telegram bila pengirim butuh keputusan pemilik</label>` : ''}
        </details>`;
    } else if (kind === 'datatable') body = datatableFields(member);
    else body = kindFields(member);
    if (isBranching(member)) body += loopFields(member);
    box.innerHTML = `${body}
      ${fork ? `<div class="field"><span>Jalur dari ${esc(fork.name)}</span>
        ${(kindOf(fork) === 'switch'
          ? [['A', 'aturan A cocok'], ['B', 'aturan B cocok'], ['C', 'aturan C cocok'], ['D', 'tidak ada yang cocok']]
          : [['A', 'benar'], ['B', 'salah']]
        ).map(([value, label]) => `<label class="check"><input type="radio" name="branch" value="${value}" ${member.branch === value ? 'checked' : ''}>${kindOf(fork) === 'switch' ? `Jalur ${value}, ` : 'Jalur '}${label}</label>`).join('')}
        <label class="check"><input type="radio" name="branch" value="" ${!member.branch ? 'checked' : ''}>Semua jalur</label>
      </div>` : ''}`;
    bindParams(box, member);
    if (isBranching(member)) bindLoops(box, member);
    if (kind === 'datatable') bindDatatable(box, member);
    if (kind === 'telegram') bindTelegram(box, member);
  }

  /* "Ulangi": a branch of this IF or Switch can send the work back to a node before it, a set number of times. */
  function loopFields(member) {
    const branches = kindOf(member) === 'gate' ? [['A', 'benar'], ['B', 'salah']] : [['A', 'A'], ['B', 'B'], ['C', 'C'], ['D', 'D']];
    const ancestors = S.members.filter(m => isAncestor(m.id, member));
    const mine = (S.loops || []).map((loop, index) => ({ loop, index })).filter(x => x.loop.from === member.id);
    const free = branches.filter(([b]) => !mine.some(x => x.loop.branch === b));
    const rows = mine.map(({ loop, index }) => `
      <div class="loop-row" data-loop-row="${index}">
        <span>Bila jalur <b>${esc(branchName(member, loop.branch))}</b>, ulangi dari</span>
        <select data-loop-to aria-label="Ulangi dari node">${ancestors.map(m => `<option value="${esc(m.id)}" ${m.id === loop.to ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}</select>
        <span>maks</span><input data-loop-max type="number" min="1" max="20" value="${esc(loop.max || 3)}" aria-label="Maksimal ulangan"><span>kali</span>
        <button class="icon-btn" type="button" data-loop-remove aria-label="Hapus ulangan ini" title="Hapus">×</button>
      </div>`).join('');
    return `<details class="field more" ${mine.length ? 'open' : ''}><summary>Ulangi${mine.length ? ` (${mine.length})` : ''}</summary>
      <p class="hint">Contoh: agent menulis, IF mengecek hasilnya; bila salah, agent itu mengerjakan ulang dengan alasan kenapa belum lolos. Setelah batasnya habis, alur lanjut lewat garis jalur itu (bila ada). Bisa juga dibuat dengan menarik garis dari jalur ini ke node sebelumnya.</p>
      ${rows}
      ${ancestors.length && free.length ? `<div class="loop-add"><select data-loop-branch aria-label="Jalur">${free.map(([b, label]) => `<option value="${b}">Jalur ${esc(label)}</option>`).join('')}</select>
        <button class="btn btn-ghost btn-small" type="button" data-loop-add>+ Ulangi dari node sebelumnya</button></div>`
        : (ancestors.length ? '' : '<p class="hint">Belum ada node sebelum node ini untuk diulang.</p>')}
    </details>`;
  }

  function bindLoops(box, member) {
    box.querySelectorAll('[data-loop-row]').forEach(row => {
      const index = Number(row.dataset.loopRow);
      row.querySelector('[data-loop-to]').onchange = e => { checkpoint(); S.loops[index].to = e.target.value; afterChange(); };
      row.querySelector('[data-loop-max]').oninput = e => {
        checkpoint(`loop-max-${index}`);
        S.loops[index].max = Math.max(1, Math.min(20, Number(e.target.value) || 1));
        markDirty();
        renderEdges();
      };
      row.querySelector('[data-loop-remove]').onclick = () => removeLoop(index);
    });
    const add = box.querySelector('[data-loop-add]');
    if (add) {
      add.onclick = () => {
        const target = S.members.find(m => isAncestor(m.id, member));
        if (target) addLoop(member.id, box.querySelector('[data-loop-branch]').value, target.id);
      };
    }
  }

  function refreshNodeCard(member) {
    const node = document.querySelector(`#nodes .node[data-id="${CSS.escape(member.id)}"]`);
    if (!node) return;
    node.querySelector('.node-label b').textContent = member.name || 'Tanpa nama';
    const sub = node.querySelector('.node-label small');
    if (sub) sub.textContent = nodeDetail(member);
    const inner = node.querySelector('.node-inner small');
    if (inner) inner.textContent = shortModel(member.model_id) || 'pilih model';
    const issues = issuesOf(member);
    let mark = node.querySelector('.node-issue');
    if (issues.length && !mark) { mark = document.createElement('span'); mark.className = 'node-issue'; mark.textContent = '!'; node.appendChild(mark); }
    if (mark) { if (issues.length) mark.title = issues.join('\n'); else mark.remove(); }
    renderIssues();
  }

  function bindParams(box, member) {
    box.querySelectorAll('[data-tpl]').forEach(el => { el.addEventListener('focus', () => { S.lastTpl = el; }); });
    const harness = Boolean((member.config || {}).harness);
    box.querySelectorAll('[data-field="model_id"]').forEach(select => fillModelOptions(select, member.model_id || (harness ? 'auto' : ''), harness));
    box.querySelectorAll('[data-harness]').forEach(check => {
      check.onchange = () => {
        checkpoint();
        member.config = { ...(member.config || {}) };
        if (check.checked) {
          member.config.harness = true;
          if (!modelKnown(member.model_id)) member.model_id = 'auto';
        } else {
          delete member.config.harness;
          if (member.model_id === 'auto') member.model_id = defaultModel();
        }
        markDirty();
        refreshNodeCard(member);
        renderParams(box, member);
      };
    });
    box.querySelectorAll('[data-field]').forEach(input => {
      input[input.tagName === 'SELECT' ? 'onchange' : 'oninput'] = () => {
        checkpoint(`field-${member.id}-${input.dataset.field}`);
        member[input.dataset.field] = input.value;
        markDirty();
        refreshNodeCard(member);
      };
    });
    box.querySelectorAll('[data-config]').forEach(input => {
      const rerenders = ['mode', 'database', 'combine', 'method'].includes(input.dataset.config);
      input[rerenders ? 'onchange' : 'oninput'] = () => {
        checkpoint(rerenders ? '' : `config-${member.id}-${input.dataset.config}`);
        member.config = { ...(member.config || {}), [input.dataset.config]: input.type === 'number' ? Number(input.value) : input.value };
        markDirty();
        refreshNodeCard(member);
        if (rerenders) renderParams(box, member);
      };
    });
    box.querySelectorAll('[data-insert]').forEach(button => {
      button.onmousedown = e => e.preventDefault();
      button.onclick = () => insertTemplate(button.dataset.insert);
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
        checkpoint(rerenders ? '' : `cond-${member.id}-${input.dataset.cond}-${input.dataset.index}`);
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
        check.onchange = () => { checkpoint(); const list = listOf(key); list[Number(index)].days = readDays(wrap, 'data-cday'); saveList(key, list); };
      });
    });
    box.querySelectorAll('[data-cond-add]').forEach(button => {
      button.onclick = () => { checkpoint(); const key = button.dataset.condAdd; saveList(key, [...listOf(key), { mode: 'contains', value: '' }]); renderParams(box, member); };
    });
    box.querySelectorAll('[data-cond-remove]').forEach(button => {
      button.onclick = () => {
        const key = button.dataset.condRemove;
        const list = listOf(key);
        if (list.length <= 1) { toast('Minimal satu syarat.'); return; }
        checkpoint();
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
    box.querySelectorAll('input[name="branch"]').forEach(radio => {
      radio.onchange = () => { checkpoint(); member.branch = radio.value; afterChange(); };
    });
    const dbTarget = box.querySelector('[data-db-target]');
    if (dbTarget) {
      dbTarget.onchange = () => {
        checkpoint();
        const [where, ...rest] = dbTarget.value.split(':');
        const name = rest.join(':');
        member.config = { table: (member.config || {}).table || 'hasil_alur', ...(where === 'src' ? { source: name } : { database: name }) };
        markDirty();
        refreshNodeCard(member);
        renderParams(box, member);
      };
    }
    const dbAdd = box.querySelector('[data-db-add]');
    if (dbAdd) {
      dbAdd.onclick = () => {
        if (!(window.SQData && window.SQData.addMenu)) { toast('Buka tab Tabel data untuk menambah sumber.'); return; }
        // The source made from this menu becomes this node's target as soon as it exists.
        S.pendingSource = { member: member.id, before: new Set((S.dataSources || []).map(x => x.name)) };
        const r = dbAdd.getBoundingClientRect();
        window.SQMenu.show(window.SQData.addMenu(), r.left, r.bottom, dbAdd);
      };
    }
    box.querySelectorAll('[data-go-creds]').forEach(button => { button.onclick = () => { if (confirmLeave()) { closeNdv(); showHome('creds'); } }; });
    box.querySelectorAll('[data-go-data]').forEach(button => { button.onclick = () => { if (confirmLeave()) { closeNdv(); showHome('data'); } }; });
    box.querySelectorAll('[data-flag]').forEach(check => {
      check.onchange = () => {
        checkpoint();
        member.config = { ...(member.config || {}) };
        if (check.checked) member.config[check.dataset.flag] = true; else delete member.config[check.dataset.flag];
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
    box.querySelectorAll('[data-grant-access]').forEach(select => {
      select.onchange = () => {
        checkpoint();
        saveGrants();
        renderParams(box, member);
        const details = box.querySelector('details.more');
        if (details) details.open = true;
      };
    });
    box.querySelectorAll('[data-grant-hide]').forEach(input => { input.oninput = () => { checkpoint(`hide-${member.id}`); saveGrants(); }; });
  }

  /* Other active Telegram flows that would also take a message without a trigger word. Only one of them answers
     (the one changed last), so the owner sees which and can switch the others off. */
  function rivalsNote() {
    const schedule = S.schedule || {};
    if (schedule.keyword) return '';
    const rivals = (S.teams || []).filter(t => t.id !== S.team && t.active !== false && (t.schedule || {}).mode === 'telegram'
      && !(t.schedule || {}).keyword && ((t.schedule || {}).audience === 'public') === (schedule.audience === 'public'));
    if (!rivals.length) return '';
    return `<div class="rivals"><p class="hint hint-warn">Alur Telegram aktif lain juga menangkap semua pesan. Satu pesan hanya dijawab satu alur: yang terakhir disimpan. Matikan yang tidak dipakai:</p>
      ${rivals.map(t => `<div class="rival"><span>${esc(t.name)}</span><button class="btn btn-ghost btn-small" type="button" data-rival-off="${esc(t.id)}">Matikan</button></div>`).join('')}</div>`;
  }

  /* Where the runs work. One folder per flow by default, so a chat on Telegram does not leave a folder per message. */
  function folderField() {
    const current = S.folder || 'team';
    const name = $('team-name').value.trim() || 'alur ini';
    const chats = (S.folders || []).filter(f => !f.name.startsWith('[team] '));
    const known = current === 'team' || current === 'run' || chats.some(f => f.id === current);
    return `<label class="field"><span>Folder kerja</span><select id="flow-folder">
        <option value="team" ${current === 'team' ? 'selected' : ''}>Satu folder untuk alur ini ([team] ${esc(name)})</option>
        <option value="run" ${current === 'run' ? 'selected' : ''}>Folder baru tiap run</option>
        ${chats.length ? `<optgroup label="Folder chat (berbagi dengan chat web)">${chats.map(f => `<option value="${esc(f.id)}" ${f.id === current ? 'selected' : ''}>${esc(f.name)}</option>`).join('')}</optgroup>` : ''}
        ${known ? '' : `<option value="${esc(current)}" selected>${esc(current.slice(2))}</option>`}
      </select></label>
      <p class="hint">Semua run alur ini bekerja di folder yang sama, jadi file dari pesan sebelumnya tetap ada. Pilih folder chat web supaya Telegram dan web memakai folder yang sama; dengan node Harness penuh, percakapannya juga sama.</p>`;
  }

  function bindFolder(box) {
    const select = box.querySelector('#flow-folder');
    if (select) select.onchange = () => { checkpoint(); S.folder = select.value; markDirty(); };
    if (!S.folders) {
      S.folders = [];
      api('/api/teams/folders').then(data => {
        S.folders = data.folders || [];
        if (S.ndv === TRIGGER) renderTrigger(box);
      }).catch(() => {});
    }
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
        <p class="hint">Contoh: <code>0 7 * * 1-5</code> jam 07:00 Senin sampai Jumat, <code>*/30 8-17 * * *</code> tiap 30 menit di jam kerja.</p>` : ''}
      ${mode === 'once' ? `<label class="field"><span>Tanggal dan jam</span><input id="schedule-at" type="datetime-local" value="${esc((schedule.at || '').slice(0, 16))}"></label>` : ''}
      ${mode === 'telegram' ? `${credentialNote(telegram.ready && telegram.default_chat, 'Bot dan chat Telegram')}
        <div class="field"><span>Siapa yang boleh menulis ke bot</span>
          <div class="seg-choice">
            <label><input type="radio" name="tg-audience" value="owner" ${audience === 'owner' ? 'checked' : ''}><span>Hanya saya</span></label>
            <label><input type="radio" name="tg-audience" value="public" ${audience === 'public' ? 'checked' : ''}><span>Siapa saja (publik)</span></label>
          </div></div>
        <label class="field"><span>Kata pemicu (kosongkan untuk semua pesan)</span><input id="schedule-keyword" value="${esc(schedule.keyword || '')}" placeholder="${audience === 'public' ? '' : '/jadwal'}" maxlength="33"></label>
        ${audience === 'public' ? `
          <div class="trigger-grid two">
            <label class="field"><span>Ingatan obrolan (giliran terakhir per chat)</span><input id="schedule-history" type="number" min="0" max="20" value="${esc(schedule.history ?? 6)}"></label>
            <label class="field"><span>Batas balasan otomatis per hari</span><input id="schedule-cap" type="number" min="1" max="5000" value="${esc(schedule.daily_cap ?? 300)}"></label>
          </div>
          <ul class="ready-list">
            <li class="${telegram.ready ? 'ok' : 'no'}">Token bot Telegram ${telegram.ready ? 'terisi' : 'belum diisi'}</li>
            <li class="${telegram.default_chat ? 'ok' : 'no'}">Chat ID kamu ${telegram.default_chat ? 'terisi, untuk kabar dan peringatan dari bot' : 'belum diisi, bot tidak bisa memberi kabar ke kamu'}</li>
            <li class="${(S.dataSources || []).length ? 'ok' : 'no'}">${(S.dataSources || []).length ? `${S.dataSources.length} sumber data siap dipakai agent` : 'Belum ada sumber data (katalog, pesanan)'}</li>
          </ul>
          <p class="hint">Orang lain hanya dilayani di chat pribadi, maksimal 6 pesan per menit. Agent yang menjawab mereka hanya boleh membaca dan menambah baris pada data yang kamu izinkan di node-nya, dan hanya memakai alat yang aman. Gangguan dan permintaan yang butuh keputusanmu dikabarkan ke chat Telegram milikmu. Kalau kamu sendiri yang chat ke bot ini, kamu tetap dilayani sebagai pemilik: semua alat dan data, tanpa batas tadi.</p>` : `
          <label class="field"><span>Ingatan obrolan (giliran terakhir yang diingat)</span><input id="schedule-history" type="number" min="0" max="20" value="${esc(schedule.history ?? 6)}"></label>
          <p class="hint">Seperti chat biasa: hanya chat di Kredensial yang dilayani, agent memakai semua alat, pesan berikutnya menunggu giliran, dan node Telegram tanpa Chat ID membalas ke chat itu. Isi 0 kalau tiap pesan berdiri sendiri, misalnya perintah /catat.</p>`}` : ''}
      ${mode !== 'off' ? `<label class="field"><span>${mode === 'telegram' ? 'Konteks tambahan (boleh kosong)' : 'Input yang dikirim tiap kali berjalan'}</span><textarea id="schedule-task" rows="4" maxlength="4000" placeholder="Misalnya: apa yang penting hari ini di Jakarta?">${esc(schedule.task || '')}</textarea></label>
        <p class="hint">Berjalan sendiri selama server menyala dan alur <b>Aktif</b> (saklar di kanan atas). Tekan Simpan agar berlaku.</p>` : '<p class="hint">Isi kotak input di bawah kanvas, lalu tekan Jalankan alur.</p>'}
      ${mode === 'telegram' ? rivalsNote() : ''}
      ${folderField()}
      <p class="hint">Node yang ditarik garis dari Pemicu adalah langkah pertama. Yang tidak tersambung tidak dijalankan.</p>`;
    bindFolder(box);
    box.querySelectorAll('[data-rival-off]').forEach(button => {
      button.onclick = async () => {
        try {
          const team = await credApi(`/api/teams/${encodeURIComponent(button.dataset.rivalOff)}/active`, 'PUT', { active: false });
          S.teams = (S.teams || []).map(t => (t.id === team.id ? team : t));
          toast(`${team.name} dimatikan.`);
          renderTrigger(box);
        } catch (e) { toast(e.message); }
      };
    });
    const value = id => (box.querySelector(`#${id}`) ? box.querySelector(`#${id}`).value : undefined);
    const update = () => {
      checkpoint('trigger');
      const timeText = value('schedule-time');
      const checked = box.querySelector('input[name="trigger-mode"]:checked');
      const before = S.schedule || {};
      S.schedule = {
        mode: checked ? checked.value : 'off',
        minutes: value('schedule-minutes') !== undefined ? Number(value('schedule-minutes')) : before.minutes,
        times: timeText !== undefined ? timeText.split(',').map(t => t.trim()).filter(Boolean) : before.times,
        days: box.querySelector('[data-schedule-day]') ? readDays(box, 'data-schedule-day') : before.days,
        cron: value('schedule-cron') ?? before.cron,
        at: value('schedule-at') ?? before.at,
        keyword: value('schedule-keyword') ?? before.keyword,
        task: value('schedule-task') ?? before.task,
      };
      if (S.schedule.times) S.schedule.time = S.schedule.times[0];
      if (S.schedule.mode === 'telegram') {
        const picked = box.querySelector('input[name="tg-audience"]:checked');
        S.schedule.history = value('schedule-history') !== undefined ? Number(value('schedule-history')) : (before.history ?? 6);
        if ((picked ? picked.value : before.audience) === 'public') {
          S.schedule.audience = 'public';
          S.schedule.daily_cap = value('schedule-cap') !== undefined ? Number(value('schedule-cap')) : (before.daily_cap ?? 300);
        }
      }
      markDirty();
      paintActive();
      const card = document.querySelector(`#nodes .node[data-id="${TRIGGER}"]`);
      if (card) {
        card.querySelector('.node-label b').textContent = TRIGGERS.find(t => t.mode === S.schedule.mode)?.label || 'Manual';
        card.querySelector('.node-label small').textContent = triggerLabel();
      }
    };
    box.querySelectorAll('input[name="trigger-mode"]').forEach(radio => { radio.onchange = () => { S.lastStep = null; update(); renderTrigger(box); render(); }; });
    box.querySelectorAll('input:not([name="trigger-mode"]), textarea').forEach(input => { input.oninput = update; input.onchange = update; });
    box.querySelectorAll('input[name="tg-audience"]').forEach(radio => { radio.onchange = () => { S.lastStep = null; update(); renderTrigger(box); render(); }; });
    box.querySelectorAll('[data-go-creds]').forEach(button => { button.onclick = () => { if (confirmLeave()) { closeNdv(); showHome('creds'); } }; });
  }

  $('ndv-close').onclick = closeNdv;
  $('ndv-remove').onclick = () => { if (S.ndv) removeNode(S.ndv); };
  $('ndv-test').onclick = testNode;
  $('ndv').onclick = e => { if (e.target === $('ndv')) closeNdv(); };
  $('ndv-title').oninput = () => {
    const member = memberById(S.ndv);
    if (!member) return;
    checkpoint(`name-${member.id}`);
    member.name = $('ndv-title').value.slice(0, 60);
    if (kindOf(member) !== 'agent') member.role = member.name || member.role;
    markDirty();
    refreshNodeCard(member);
  };
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
      const id = node.dataset.id;
      if (id === TRIGGER) { node.dataset.status = S.run ? 'completed' : ''; return; }
      const agent = runState(id);
      const test = testState(id);
      const state = node.querySelector('.node-state');
      const fresh = test && (!agent || !agent.completed_at || test.at > agent.completed_at * 1000);
      if (fresh) {
        node.dataset.status = test.error ? 'failed' : 'completed';
        if (state) state.textContent = test.error ? `Tes gagal: ${test.error}` : `Tes: ${test.note || 'berhasil'}`;
        return;
      }
      node.dataset.status = agent ? agent.status : '';
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
      node.title = agent.error || agent.note || node.title;
    });
    renderEdges();
  }

  function showRun(run) {
    S.run = run;
    paintRun();
    const active = !finished(run.status);
    const elapsed = run.started_at ? ` · ${formatSeconds(((run.completed_at || Date.now() / 1000) - run.started_at) * 1000)}` : '';
    const info = $('run-info');
    const from = sourceLabel(run);
    info.textContent = `${RUN_STATUS[run.status] || run.status}${elapsed} · ${formatTokens(run.total_tokens || 0)} token${from ? ` · dari ${from}` : ''}`;
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

  function sourceLabel(run) {
    const trigger = run.trigger || {};
    if (trigger.source === 'telegram') return `Telegram${trigger.sender ? ` (${trigger.sender})` : ''}`;
    if (['interval', 'daily', 'cron', 'once'].includes(trigger.source)) return 'jadwal';
    return '';
  }

  function stopTeamEvents() {
    if (S.teamEvents) { S.teamEvents.close(); S.teamEvents = null; }
  }

  /* Every run of the open flow shows on the canvas as it happens, also one a Telegram message or a schedule started. */
  function watchTeam(teamId) {
    stopTeamEvents();
    if (!teamId) return;
    const source = new EventSource(`/api/teams/${teamId}/events`);
    S.teamEvents = source;
    source.onmessage = event => {
      const data = JSON.parse(event.data);
      const run = data.run;
      if (!run || S.team !== teamId || S.teamEvents !== source) return;
      if (S.run && S.run.id === run.id) {
        showRun(run);
        if (data.type === 'run_finished' && S.tab === 'runs') loadRuns();
        return;
      }
      if (S.run && (S.run.created_at || 0) > (run.created_at || 0)) return;
      S.tests = {};
      S.openResult = false;
      showRun(run);
      const from = sourceLabel(run);
      toast(`Alur berjalan${from ? ` dari ${from}` : ''}. Ikuti di kanvas.`);
      if (S.tab === 'runs') loadRuns();
    };
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
    if (!S.starts.length) {
      toast('Tarik garis dari Pemicu ke node pertama dulu. Node yang tidak tersambung tidak dijalankan.');
      selectNode(TRIGGER);
      return;
    }
    $('run').disabled = true;
    try {
      await saveTeam();
      S.openResult = true;
      S.tests = {};
      const run = await post(`/api/teams/${S.team}/runs`, { task: $('task').value.trim() });
      showRun(run);
      watchRun(run);
    } catch (err) {
      showSaveError(err);
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

  /* ── Opening, saving, deleting ───────────────────── */
  function loadInto(source, teamId, note = '') {
    stopEvents();
    S.run = null;
    S.tests = {};
    S.past = [];
    S.future = [];
    S.lastStep = null;
    S.team = teamId;
    S.selected = null;
    S.selectedEdge = null;
    S.schedule = clone(source.schedule || { mode: 'off' });
    S.mode = ['video', 'motion', 'codevideo'].includes(source.mode) ? source.mode : 'standard';
    S.active = source.active !== false;
    S.folder = source.folder || 'team';
    S.loops = clone(source.loops || []);
    const flow = source.style === 'flow' ? source : (source.as_flow || source);
    S.members = clone(flow.members || []).map(m => ({ ...m, depends_on: m.depends_on || [], config: m.config || {} }));
    S.starts = [...(flow.starts || [])];
    const layout = source.layout || {};
    S.trigger = null;
    S.members.forEach(m => {
      const saved = layout[m.id] || (teamId && store.get(legacyPosKey(teamId, m.id)) ? store.get(legacyPosKey(teamId, m.id)).split(',').map(Number) : null);
      if (saved && saved.length === 2 && saved.every(Number.isFinite)) [m.x, m.y] = saved; else { delete m.x; delete m.y; }
    });
    const savedTrigger = layout[TRIGGER] || (teamId && store.get(legacyPosKey(teamId, TRIGGER)) ? store.get(legacyPosKey(teamId, TRIGGER)).split(',').map(Number) : null);
    if (savedTrigger && savedTrigger.every(Number.isFinite)) S.trigger = { x: savedTrigger[0], y: savedTrigger[1] };
    $('team-name').value = source.name || '';
    if (S.ndv) closeNdv();
    resetRunView();
    autoLayout();
    showNote(note);
    showPage('editor');
    watchTeam(teamId);
    render();
    requestAnimationFrame(fitView);
    markDirty(Boolean(note) || !teamId);
    paintHistory();
    paintActive();
    if (S.tab === 'runs') loadRuns();
  }

  function showNote(text) {
    const box = $('wf-note');
    box.classList.toggle('hidden', !text);
    box.innerHTML = text ? `<span>${esc(text)}</span><button class="icon-btn" type="button" aria-label="Tutup catatan">×</button>` : '';
    const close = box.querySelector('button');
    if (close) close.onclick = () => box.classList.add('hidden');
  }

  function blankFlow() {
    const agent = { id: newId(), kind: 'agent', name: 'AI Agent', role: 'Asisten', model_id: defaultModel(), instructions: '', depends_on: [], config: {} };
    loadInto({ name: '', members: [agent], starts: [agent.id], style: 'flow', schedule: { mode: 'off' } }, null);
    setTimeout(() => $('team-name').focus(), 60);
  }

  const confirmLeave = () => !S.dirty || confirm('Perubahan alur ini belum disimpan. Tinggalkan?');

  function openTeam(id) {
    const team = S.teams.find(t => t.id === id);
    if (!team) { blankFlow(); return; }
    const legacy = team.style !== 'flow';
    loadInto(team, team.id, legacy ? 'Alur ini dibuat dengan model lama (Lead dan Reviewer). Sekarang setiap sambungan terlihat di kanvas dan alur berjalan persis mengikuti garis. Periksa, lalu Simpan untuk memakai format baru.' : '');
    store.set('wf:last', team.id);
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
    loadInto({ ...template, style: 'flow' }, null, template.description);
    if (template.starter) makeStarter(template);
  }

  /* Some templates need data to work on: the shop its product list and orders, the full harness flow its notes
     and labels. Made when missing, and said so. */
  const STARTER_NOTES = {
    shop: 'Katalog dan Pesanan sudah dibuat di tab Tabel data: isi dengan produkmu.',
    harness: 'Tabel catatan dan label (berelasi) sudah dibuat di Tabel data.',
  };
  async function makeStarter(template) {
    try {
      const made = await credApi(`/api/data/starter/${encodeURIComponent(template.starter)}`, 'POST');
      await refreshSources();
      if (made.created.length) toast(`${template.description} ${STARTER_NOTES[template.starter] || ''}`.trim());
    } catch (e) { toast(`Template terpasang, tetapi data contohnya gagal dibuat: ${e.message}`); }
  }

  async function loadIntegrations() {
    try { S.integrations = await api('/api/teams/integrations'); } catch { /* the editor works without it */ }
    await refreshSources(false);
  }

  /* The data sources and relations nodes can use. Reloaded when the Tabel data tab changes them. */
  async function refreshSources(rerender = true) {
    try {
      const data = await api('/api/data/sources');
      const before = JSON.stringify([(S.dataSources || []).map(x => [x.name, x.label]), (S.relations || []).map(r => r.id)]);
      S.dataSources = data.sources;
      S.relations = data.relations || [];
      S.builtin = data.builtin || 'tabel';
      S.googleEmail = data.google_email || '';
      S.dataReady = data.ready || {};
      const after = JSON.stringify([S.dataSources.map(x => [x.name, x.label]), S.relations.map(r => r.id)]);
      if (rerender && before !== after && S.ndv && S.ndv !== TRIGGER && !document.activeElement?.closest('#ndv-params')) renderNdv();
    } catch { /* the editor works without data sources */ }
  }

  async function loadTeams() {
    if (S.features.teams === false) return;
    try {
      const [teams, templates] = await Promise.all([api('/api/teams'), api('/api/teams/templates'), loadIntegrations()]);
      S.teams = teams.teams;
      S.templates = templates.templates;
      renderHome();
      if (S.page === 'editor' && S.trigger) requestAnimationFrame(() => { render(); applyView(); });
    } catch (e) { toast(e.message); }
  }

  function saveBody() {
    return {
      name: $('team-name').value.trim(),
      description: '',
      members: S.members.map(serializeMember),
      lead_id: null,
      reviewer_id: null,
      schedule: S.schedule || { mode: 'off' },
      mode: S.mode || 'standard',
      style: 'flow',
      starts: S.starts,
      layout: layoutOut(),
      active: S.active !== false,
      folder: S.folder || 'team',
      loops: (S.loops || []).filter(l => memberById(l.from) && memberById(l.to)),
    };
  }

  async function saveTeam() {
    const body = saveBody();
    if (!body.name) { $('team-name').focus(); throw new Error('Beri nama alur dulu.'); }
    if (!body.members.length) throw new Error('Tambahkan minimal satu node.');
    const saved = S.team
      ? await api(`/api/teams/${S.team}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      : await post('/api/teams', body);
    if (S.team !== saved.id) watchTeam(saved.id);
    S.team = saved.id;
    store.set('wf:last', saved.id);
    S.teams = (await api('/api/teams')).teams;
    showNote('');
    markDirty(false);
    return saved;
  }

  /* The server names the node that is wrong: open it so the fix is one click away. */
  function showSaveError(err) {
    toast(err.message);
    const match = /^Node "(.+?)"/.exec(err.message || '');
    const member = match && S.members.find(m => m.name === match[1]);
    if (member) openNdv(member.id);
  }

  const saveAndSay = () => saveTeam().then(() => toast('Tersimpan.')).catch(showSaveError);

  async function deleteFlow(id) {
    const team = S.teams.find(t => t.id === id);
    if (!confirm(`Hapus alur "${team ? team.name : 'ini'}" beserta riwayatnya?`)) return false;
    try {
      await api(`/api/teams/${id}`, { method: 'DELETE' });
      S.teams = S.teams.filter(t => t.id !== id);
      renderHome();
      return true;
    } catch (e) { toast(e.message); return false; }
  }

  function exportFlow(source) {
    const data = source || { ...saveBody(), members: S.members.map(serializeMember) };
    const body = { squidwings_flow: 1, name: data.name, mode: data.mode, schedule: data.schedule, members: (data.style === 'flow' || !data.as_flow ? data : data.as_flow).members,
      starts: (data.style === 'flow' || !data.as_flow ? data : data.as_flow).starts || [], layout: data.layout || {} };
    const blob = new Blob([JSON.stringify(body, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${(data.name || 'alur').replace(/[^\w-]+/g, '_')}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 2000);
  }

  function importFlow() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json,application/json';
    input.onchange = async () => {
      const file = input.files[0];
      if (!file) return;
      try {
        const data = JSON.parse(await file.text());
        if (!Array.isArray(data.members) || !data.members.length) throw new Error('File ini bukan alur SquidWings.');
        loadInto({ ...data, name: `${data.name || 'Alur'} (impor)`.slice(0, 100), style: 'flow' }, null);
        toast('Alur diimpor. Periksa model tiap agent, lalu Simpan.');
      } catch (e) { toast(e.message || 'File tidak bisa dibaca.'); }
    };
    input.click();
  }

  function duplicateFlow(source) {
    const data = source || { ...saveBody(), as_flow: null };
    loadInto({ ...data, name: `${data.name || 'Alur'} (salinan)`.slice(0, 100) }, null);
  }

  async function setActive(id, active) {
    try {
      const team = await api(`/api/teams/${id}/active`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active }) });
      S.teams = S.teams.map(t => (t.id === id ? team : t));
      if (S.team === id) { S.active = team.active; paintActive(); }
      toast(active ? 'Alur aktif: jadwal dan pemicunya berjalan.' : 'Alur dinonaktifkan. Pengaturannya tetap tersimpan.');
    } catch (e) { toast(e.message); }
    renderHome();
  }

  function paintActive() {
    const manual = ((S.schedule || {}).mode || 'off') === 'off';
    $('wf-active').checked = S.active !== false && !manual;
    $('wf-active').disabled = manual;
    $('wf-active-wrap').title = manual ? 'Pemicu Manual tidak perlu diaktifkan. Ganti pemicunya ke jadwal atau Telegram.' : 'Jadwal dan pemicu Telegram berjalan sendiri bila aktif';
    $('wf-active-wrap').classList.toggle('disabled', manual);
  }

  function flowMenu() {
    return [
      { heading: 'Pengaturan' },
      ...Object.entries(MODES).map(([value, label]) => ({
        label: `Mode: ${label}`, disabled: S.mode === value ? 'Sedang dipakai' : false,
        run: () => { checkpoint(); S.mode = value; markDirty(); toast(value === 'standard' ? 'Mode standar.' : 'Tiap agent mendapat waktu sampai 30 menit untuk membuat video.'); },
      })),
      { separator: true },
      { label: 'Rapikan tata letak', run: tidy },
      { label: 'Duplikat alur', run: () => { if (confirmLeave()) duplicateFlow(); } },
      { label: 'Ekspor JSON', run: () => exportFlow() },
      { label: 'Impor JSON…', run: () => { if (confirmLeave()) importFlow(); } },
      { separator: true },
      { label: 'Hapus alur', danger: true, disabled: S.team ? false : 'Belum disimpan', run: async () => { if (await deleteFlow(S.team)) { S.dirty = false; showHome(); } } },
    ];
  }

  $('team-name').oninput = () => markDirty();
  $('save-team').onclick = saveAndSay;
  $('wf-back').onclick = () => { if (confirmLeave()) { markDirty(false); showHome(); } };
  $('wf-undo').onclick = undo;
  $('wf-redo').onclick = redo;
  $('wf-more').onclick = e => { const r = e.currentTarget.getBoundingClientRect(); window.SQMenu.show(flowMenu(), r.left - 180, r.bottom + 4, e.currentTarget); };
  $('wf-active').onchange = () => {
    S.active = $('wf-active').checked;
    if (S.team && !S.dirty) setActive(S.team, S.active);
    else markDirty();
  };
  $('wf-issues').onclick = e => {
    const list = flowIssues();
    const r = e.currentTarget.getBoundingClientRect();
    window.SQMenu.show([{ heading: 'Perlu diisi' }, ...list.map(item => ({ label: `${item.name}: ${item.text}`, run: () => openNdv(item.id) }))], r.left, r.bottom + 4, e.currentTarget);
  };

  /* ── Pages: overview and editor ──────────────────── */
  function showPage(page) {
    S.page = page;
    $('wf-home').classList.toggle('hidden', page !== 'home');
    $('wf-page').classList.toggle('hidden', page !== 'editor');
    if (page === 'editor') showTab(S.tab === 'runs' ? 'runs' : 'editor');
  }

  function showHome(tab = S.homeTab || 'flows') {
    stopEvents();
    stopTeamEvents();
    if (S.ndv) closeNdv();
    showPage('home');
    S.homeTab = tab;
    document.querySelectorAll('#home-tabs button').forEach(b => {
      const on = b.dataset.home === tab;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    $('home-flows').classList.toggle('hidden', tab !== 'flows');
    $('wf-data').classList.toggle('hidden', tab !== 'data');
    $('wf-creds').classList.toggle('hidden', tab !== 'creds');
    if (tab === 'data' && window.SQData) window.SQData.open();
    if (tab === 'creds') loadCreds();
    if (tab === 'flows') renderHome();
  }
  document.querySelectorAll('#home-tabs button').forEach(b => { b.onclick = () => showHome(b.dataset.home); });

  function ago(seconds) {
    const diff = Date.now() / 1000 - seconds;
    if (diff < 60) return 'baru saja';
    if (diff < 3600) return `${Math.floor(diff / 60)} menit lalu`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} jam lalu`;
    if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} hari lalu`;
    return new Date(seconds * 1000).toLocaleDateString('id-ID', { dateStyle: 'medium' });
  }

  function renderHome() {
    const query = ($('home-search').value || '').trim().toLowerCase();
    const teams = [...(S.teams || [])].sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))
      .filter(t => !query || t.name.toLowerCase().includes(query));
    $('home-count').textContent = S.teams && S.teams.length ? `${S.teams.length} alur` : '';
    $('flow-list').innerHTML = teams.length ? teams.map(t => {
      const manual = ((t.schedule || {}).mode || 'off') === 'off';
      const nodes = (t.style === 'flow' ? t.members : (t.as_flow || t).members).length;
      const kinds = [...new Set((t.members || []).map(kindOf))].slice(0, 5);
      return `<article class="flow-row" data-team="${esc(t.id)}" tabindex="0" role="button" aria-label="Buka ${esc(t.name)}">
        <span class="flow-kinds" aria-hidden="true">${kinds.map(k => `<span class="node-icon" data-cat="${CATEGORY[k] || 'ai'}">${nodeIcon(k, 15)}</span>`).join('')}</span>
        <span class="flow-main"><b>${esc(t.name)}</b><small>${esc(scheduleLabel(t.schedule))} · ${nodes} node · diperbarui ${esc(ago(t.updated_at || t.created_at))}${t.style === 'flow' ? '' : ' · format lama'}</small></span>
        <label class="wf-switch ${manual ? 'disabled' : ''}" title="${manual ? 'Pemicu Manual, tidak perlu diaktifkan' : 'Aktif: jadwal dan pemicu berjalan sendiri'}">
          <input type="checkbox" data-active="${esc(t.id)}" ${t.active !== false && !manual ? 'checked' : ''} ${manual ? 'disabled' : ''} aria-label="Aktifkan ${esc(t.name)}"><span class="switch-track" aria-hidden="true"></span></label>
        <button class="icon-btn" type="button" data-flow-more="${esc(t.id)}" aria-label="Menu ${esc(t.name)}">⋯</button>
      </article>`;
    }).join('') : `<div class="flow-empty">${query ? '<p>Tidak ada alur dengan nama itu.</p>'
      : '<h3>Belum ada alur</h3><p>Buat alur kosong, atau mulai dari salah satu template di bawah.</p><button class="btn btn-primary" type="button" data-empty-new>Alur baru</button>'}</div>`;
    $('flow-list').querySelectorAll('.flow-row').forEach(row => {
      row.onclick = e => { if (!e.target.closest('.wf-switch, [data-flow-more]')) openTeam(row.dataset.team); };
      row.onkeydown = e => { if (e.key === 'Enter' && e.target === row) openTeam(row.dataset.team); };
    });
    $('flow-list').querySelectorAll('[data-active]').forEach(check => { check.onchange = () => setActive(check.dataset.active, check.checked); });
    $('flow-list').querySelectorAll('[data-flow-more]').forEach(button => {
      button.onclick = e => {
        e.stopPropagation();
        const r = button.getBoundingClientRect();
        window.SQMenu.show(homeMenu(button.dataset.flowMore), r.left - 160, r.bottom + 4, button);
      };
    });
    const empty = $('flow-list').querySelector('[data-empty-new]');
    if (empty) empty.onclick = blankFlow;
    $('template-list').innerHTML = (S.templates || []).map(t => {
      const kinds = [...new Set((t.members || []).map(kindOf))].slice(0, 4);
      return `<button class="template-card" type="button" data-template="${esc(t.key)}">
        <span class="flow-kinds" aria-hidden="true">${kinds.map(k => `<span class="node-icon" data-cat="${CATEGORY[k] || 'ai'}">${nodeIcon(k, 14)}</span>`).join('')}</span>
        <b>${esc(t.name)}</b><small>${esc(t.description || '')}</small></button>`;
    }).join('') || '<p class="hint">Template muncul setelah ada model dengan dukungan tool.</p>';
    $('template-list').querySelectorAll('[data-template]').forEach(b => { b.onclick = () => useTemplate(b.dataset.template); });
  }

  function homeMenu(id) {
    const team = S.teams.find(t => t.id === id);
    if (!team) return [];
    return [
      { label: 'Buka', run: () => openTeam(id) },
      { label: 'Duplikat', run: () => duplicateFlow(team) },
      { label: 'Ekspor JSON', run: () => exportFlow(team) },
      { separator: true },
      { label: 'Hapus', danger: true, run: () => deleteFlow(id) },
    ];
  }
  window.SQMenu.on($('flow-list'), ({ target }) => {
    const row = target.closest && target.closest('[data-team]');
    return row ? homeMenu(row.dataset.team) : null;
  });
  $('home-search').oninput = renderHome;
  $('home-new').onclick = blankFlow;
  $('home-import').onclick = importFlow;

  /* ── Zoom controls and keys ──────────────────────── */
  $('zoom-in').onclick = () => zoomAt(1.2);
  $('zoom-out').onclick = () => zoomAt(1 / 1.2);
  $('zoom-fit').onclick = fitView;
  $('zoom-tidy').onclick = tidy;
  $('board').addEventListener('pointerdown', boardPointerDown);
  $('board').addEventListener('wheel', e => {
    if (e.target.closest('.wf-run, .wf-note')) return;
    e.preventDefault();
    const r = $('board').getBoundingClientRect();
    if (e.ctrlKey || e.metaKey) zoomAt(Math.exp(-e.deltaY * 0.0022), e.clientX - r.left, e.clientY - r.top);
    else { V.x -= e.deltaX; V.y -= e.deltaY; applyView(); }
  }, { passive: false });
  $('edge-tools').onpointerdown = e => e.stopPropagation();
  $('edge-delete').onclick = () => {
    if (!S.selectedEdge) return;
    if ('loop' in S.selectedEdge) removeLoop(S.selectedEdge.loop);
    else removeEdge(S.selectedEdge.from, S.selectedEdge.to);
  };
  $('edge-insert').onclick = () => { if (S.selectedEdge && !('loop' in S.selectedEdge)) insertOnEdge(S.selectedEdge.from, S.selectedEdge.to); };
  // One menu handler for the whole canvas: it looks at what was clicked. The run bar and zoom buttons keep the
  // browser's own menu, so the input box can still paste.
  window.SQMenu.on($('board'), ({ target, event, keyboard }) => {
    if (S.page !== 'editor' || S.tab !== 'editor' || !S.trigger) return null;
    if (target.closest && target.closest('.wf-run, .wf-zoom, .wf-plus, #edge-tools, .port-add, .wf-note')) return null;
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
    if (S.view !== 'team' || S.page !== 'editor' || S.tab !== 'editor' || !S.trigger) return;
    const width = $('board').clientWidth;
    if (boardWidth && Math.abs(width - boardWidth) / boardWidth > 0.25) fitView(); else applyView();
    boardWidth = width;
  });
  new ResizeObserver(() => { if (!boardWidth && $('board').clientWidth) boardWidth = $('board').clientWidth; }).observe($('board'));

  window.addEventListener('keydown', e => {
    if (S.view !== 'team' || S.page !== 'editor') return;
    const key = e.key.toLowerCase();
    const mod = e.ctrlKey || e.metaKey;
    if (mod && key === 's') { e.preventDefault(); saveAndSay(); return; }
    if (e.key === 'Escape') {
      if (!$('creator').classList.contains('hidden')) closeCreator();
      else if (S.ndv) closeNdv();
      return;
    }
    if (S.tab !== 'editor' || typing()) return;
    if (mod && key === 'z' && !e.shiftKey) { e.preventDefault(); undo(); return; }
    if (mod && (key === 'y' || (key === 'z' && e.shiftKey))) { e.preventDefault(); redo(); return; }
    if (S.ndv) return;
    if (mod && key === 'c' && S.selected && S.selected !== TRIGGER) { e.preventDefault(); copyNode(S.selected); return; }
    if (mod && key === 'v' && S.clipboard) { e.preventDefault(); pasteNode(); return; }
    if (mod && key === 'd' && S.selected && S.selected !== TRIGGER) { e.preventDefault(); duplicateNode(S.selected); return; }
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

  /* ── Editor tabs ─────────────────────────────────── */
  function showTab(name) {
    S.tab = name;
    document.querySelectorAll('#wf-tabs button').forEach(b => {
      const on = b.dataset.tab === name;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    ['editor', 'runs'].forEach(tab => $(`wf-${tab}`).classList.toggle('hidden', tab !== name));
    if (name === 'runs') loadRuns();
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
        const took = run.started_at ? formatSeconds(((run.completed_at || Date.now() / 1000) - run.started_at) * 1000) : '-';
        const when = new Date(run.created_at * 1000).toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'short' });
        const source = (run.trigger && run.trigger.source) || 'manual';
        return `<button class="run-row ${S.runDetail === run.id ? 'on' : ''}" data-run="${esc(run.id)}" type="button">
          <span class="run-dot" data-status="${esc(run.status)}"></span>
          <span class="run-main"><b>${esc(RUN_STATUS[run.status] || run.status)}</b><small>${esc(when)} · ${esc(source)}</small></span>
          <span class="run-meta">${esc(took)}<small>${formatTokens(run.total_tokens || 0)} token</small></span></button>`;
      }).join('') : '<p class="io-empty">Belum ada eksekusi. Jalankan alur dari tab Editor.</p>';
      box.querySelectorAll('[data-run]').forEach(row => { row.onclick = () => showRunDetail(row.dataset.run); });
      if ((!S.runDetail || !S.runs.some(r => r.id === S.runDetail)) && S.runs[0]) showRunDetail(S.runs[0].id);
    } catch (e) { box.innerHTML = `<p class="hint hint-warn">${esc(e.message)}</p>`; }
  }

  function showRunDetail(id) {
    const run = (S.runs || []).find(r => r.id === id);
    if (!run) return;
    S.runDetail = id;
    document.querySelectorAll('#runs-list .run-row').forEach(r => r.classList.toggle('on', r.dataset.run === id));
    const nodes = Object.entries(run.agents || {}).map(([nodeId, state]) => {
      const member = memberById(nodeId);
      return `<tr><td>${esc(member ? member.name : state.name || nodeId)}</td><td><span class="io-status" data-status="${esc(state.status)}">${esc(STATUS[state.status] || state.status)}</span></td>
        <td>${state.completed_at && state.started_at ? formatSeconds((state.completed_at - state.started_at) * 1000) : '-'}</td>
        <td>${state.tokens ? formatTokens(state.tokens) : '-'}</td><td class="muted">${esc(state.error || state.note || '')}</td></tr>`;
    }).join('');
    $('run-detail').innerHTML = `
      <div class="run-detail-head"><div><h3>${esc(RUN_STATUS[run.status] || run.status)}</h3><p class="hint">${esc(new Date(run.created_at * 1000).toLocaleString('id-ID'))}</p></div>
        <button id="run-open" class="btn btn-ghost" type="button">Lihat di kanvas</button></div>
      <div class="io-block"><div class="io-head">Input</div>${ioView(run.task, `run-task-${run.id}`) || '<p class="io-empty">Tanpa input.</p>'}</div>
      ${nodes ? `<table class="run-table"><thead><tr><th>Node</th><th>Status</th><th>Waktu</th><th>Token</th><th>Catatan</th></tr></thead><tbody>${nodes}</tbody></table>` : ''}
      ${run.error ? `<div class="notice" style="white-space: pre-line">${esc(run.error)}</div>` : ''}
      ${run.result ? `<div class="io-block"><div class="io-head">Hasil</div>${ioView(run.result, `run-result-${run.id}`)}</div>` : ''}`;
    bindIo($('run-detail'));
    $('run-detail').querySelectorAll('[data-io-mode]').forEach(b => { b.onclick = () => { S.ioMode[b.dataset.ioKey] = b.dataset.ioMode; showRunDetail(id); }; });
    $('run-open').onclick = () => openRunOnCanvas(run);
  }

  function openRunOnCanvas(run) {
    S.openResult = false;
    S.tests = {};
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
      { label: 'Salin input', run: () => copyText(run.task || '') },
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
      const kind = group.key === 'telegram' ? 'telegram' : group.key === 'search' ? 'http' : group.key === 'limits' ? 'delay' : 'database';
      const dbSelect = group.key === 'database' ? `<select class="cred-db" aria-label="Database yang dites">
          <option value="sqlite">SQLite</option><option value="postgres">PostgreSQL</option><option value="mysql">MySQL</option><option value="mongodb">MongoDB</option></select>` : '';
      return `<section class="cred-card" data-group="${group.key}">
        <header><span class="node-icon" data-cat="${group.key === 'telegram' ? 'output' : group.key === 'search' ? 'ai' : group.key === 'limits' ? 'logic' : 'data'}">${nodeIcon(kind, 18)}</span>
          <div><h3>${esc(group.label)} ${ready ? '<span class="cred-ok">tersambung</span>' : ''}</h3><p class="hint">${esc(group.description)}</p></div></header>
        ${fields.map(f => `<label class="field cred-field"><span>${esc(f.label)} ${sourceBadge(f)}</span>
            <span class="cred-input">${f.multiline
    ? `<textarea data-cred="${f.key}" rows="5" autocomplete="off" spellcheck="false" placeholder="${esc(f.set ? `${f.masked}  ·  tempel kunci baru untuk mengganti` : f.example)}"></textarea>`
    : `<input data-cred="${f.key}" type="${f.secret ? 'password' : 'text'}" autocomplete="off" spellcheck="false"${group.key === 'limits' ? ' inputmode="numeric"' : ''}
              placeholder="${esc(f.set ? `${f.masked}  ·  ketik untuk mengganti` : f.example)}">`}
            ${f.source === 'web' ? `<button class="btn btn-ghost btn-small" type="button" data-cred-clear="${f.key}" title="Hapus dari web, pakai .env atau nilai bawaan lagi">Hapus</button>` : ''}</span>
            ${f.hint ? `<small class="hint">${esc(f.hint)}</small>` : ''}</label>`).join('')}
        ${group.key === 'google' && fields.some(f => f.set && f.masked) ? `<p class="hint">Bagikan tiap spreadsheet ke <b>${esc(fields.find(f => f.set).masked)}</b> sebagai Editor, lalu tambahkan di tab Tabel data.</p>` : ''}
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

  /* ── Export to app.js and data.js ────────────────── */
  Object.assign(S, { starts: [], past: [], future: [], tests: {}, ioMode: {}, dtTables: {}, page: 'home', homeTab: 'flows', tab: 'editor' });
  window.SQTeam = {
    open() {
      if (S.page !== 'editor') showHome(S.homeTab || 'flows');
      loadTeams();
    },
    modelsChanged() {
      if (S.ndv && S.ndv !== TRIGGER && !document.activeElement?.closest('#ndv')) renderNdv();
      if (S.page === 'editor' && S.trigger) render();
    },
    /* For the Tabel data tab: saving needs the router PIN from outside this computer, same as credentials. */
    pinApi: credApi,
    showCreds() { if (S.page === 'editor' && !confirmLeave()) return; markDirty(false); showHome('creds'); },
    async sourcesChanged() {
      S.dtTables = {};
      await refreshSources();
      const pending = S.pendingSource;
      if (!pending) return;
      const made = (S.dataSources || []).find(x => !pending.before.has(x.name));
      const member = memberById(pending.member);
      S.pendingSource = null;
      if (!made || !member) return;
      checkpoint();
      member.config = { table: (member.config || {}).table || 'hasil_alur', source: made.name };
      markDirty();
      refreshNodeCard(member);
      if (S.ndv === member.id) renderNdv();
      toast(`Node ${member.name} sekarang menyimpan ke ${made.label || made.name}.`);
    },
  };
  window.addEventListener('beforeunload', e => { if (S.dirty) { e.preventDefault(); e.returnValue = ''; } });
})();
