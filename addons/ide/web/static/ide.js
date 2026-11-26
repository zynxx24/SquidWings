/* IDE add-on: the projects the AI made (each chat's folder), their files, and every change the AI made to them.
   CodeMirror 6 comes bundled as window.CM (vendor/codemirror.js, built from addons/ide/build). */
(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const CM = window.CM;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const params = new URLSearchParams(location.search);
  const POLL_MS = 3500;
  const PIN_KEY = 'router-pin';
  const LAST_KEY = 'sq-ide-last';
  const IMAGE = /\.(png|jpe?g|gif|webp|bmp|svg|ico|avif)$/i;
  const VIDEO = /\.(mp4|m4v|webm|mov)$/i;
  const AUDIO = /\.(mp3|wav|ogg|m4a|flac)$/i;
  const PAGE = /\.html?$/i;
  const MARKDOWN = /\.(md|markdown|mdx)$/i;
  let embedded = false;
  try { embedded = window.self !== window.top; } catch { embedded = true; }

  const S = {
    projects: [], project: null, files: new Map(), folders: new Set(), changed: {}, lastTurn: {}, stamp: 0,
    expanded: new Set(), selected: '', tabs: [], active: '', turns: [], view: null, polling: 0, previewOn: false,
  };

  /* ── Small helpers ───────────────────────────────────── */
  function toast(message, ms = 3200) {
    const el = $('toast');
    el.textContent = message;
    el.classList.remove('hidden');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => el.classList.add('hidden'), ms);
  }
  const pinHeader = () => { try { const pin = sessionStorage.getItem(PIN_KEY); return pin ? { 'X-Router-Pin': pin } : {}; } catch { return {}; } };

  async function api(url, options = {}) {
    const init = { ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...pinHeader(), ...(options.headers || {}) } };
    if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
    let response = await fetch(url, init);
    if (response.status === 403) {
      const pin = prompt('Perubahan dari luar komputer server butuh PIN Router:');
      if (pin) {
        try { sessionStorage.setItem(PIN_KEY, pin); } catch { /* private window */ }
        response = await fetch(url, { ...init, headers: { ...init.headers, 'X-Router-Pin': pin } });
      }
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : `Gagal (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  const P = () => `/api/ide/p/${encodeURIComponent(S.project.id)}`;
  const rawUrl = path => `${P()}/raw/${path.split('/').map(encodeURIComponent).join('/')}`;
  const base = path => path.split('/').pop();
  const dir = path => path.split('/').slice(0, -1).join('/');
  const extOf = path => ((base(path).match(/\.([^.]+)$/) || [])[1] || '').toLowerCase();

  function ago(seconds) {
    const diff = Math.max(0, Date.now() / 1000 - seconds);
    if (diff < 60) return 'baru saja';
    if (diff < 3600) return `${Math.floor(diff / 60)} mnt lalu`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} jam lalu`;
    if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} hari lalu`;
    return new Date(seconds * 1000).toLocaleDateString('id-ID', { day: 'numeric', month: 'short', year: 'numeric' });
  }
  function bytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }
  const STATUS = { added: ['A', 'Baru dari AI'], modified: ['M', 'Diubah AI'], deleted: ['D', 'Dihapus AI'] };

  /* ── Editor ──────────────────────────────────────────── */
  const t = CM.tags;
  const highlight = CM.HighlightStyle.define([
    { tag: t.keyword, class: 'tok-kw' },
    { tag: [t.string, t.special(t.string), t.regexp, t.character], class: 'tok-str' },
    { tag: [t.number, t.bool, t.null, t.atom, t.unit], class: 'tok-num' },
    { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], class: 'tok-com' },
    { tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.function(t.variableName))], class: 'tok-fn' },
    { tag: [t.typeName, t.className, t.namespace, t.standard(t.typeName)], class: 'tok-type' },
    { tag: [t.tagName, t.angleBracket], class: 'tok-tag' },
    { tag: t.attributeName, class: 'tok-attr' },
    { tag: [t.propertyName, t.definition(t.propertyName)], class: 'tok-prop' },
    { tag: [t.operator, t.punctuation, t.separator, t.bracket, t.derefOperator], class: 'tok-op' },
    { tag: [t.meta, t.processingInstruction, t.annotation], class: 'tok-meta' },
    { tag: [t.heading, t.heading1, t.heading2, t.heading3], class: 'tok-head' },
    { tag: [t.link, t.url], class: 'tok-link' },
    { tag: t.emphasis, class: 'tok-em' },
    { tag: t.strong, class: 'tok-strong' },
    { tag: t.invalid, class: 'tok-bad' },
  ]);
  const language = new CM.Compartment();
  const readOnly = new CM.Compartment();

  function baseExtensions(tab) {
    return [
      CM.lineNumbers(), CM.highlightActiveLineGutter(), CM.highlightSpecialChars(), CM.history(), CM.foldGutter(),
      CM.drawSelection(), CM.dropCursor(), CM.EditorState.allowMultipleSelections.of(true), CM.indentOnInput(),
      CM.syntaxHighlighting(highlight), CM.syntaxHighlighting(CM.defaultHighlightStyle, { fallback: true }),
      CM.bracketMatching(), CM.closeBrackets(), CM.autocompletion(), CM.rectangularSelection(), CM.crosshairCursor(),
      CM.highlightActiveLine(), CM.highlightSelectionMatches(),
      CM.keymap.of([
        { key: 'Mod-s', run: () => { save(); return true; } },
        { key: 'Mod-i', run: () => { openAsk(); return true; } },
        ...CM.closeBracketsKeymap, ...CM.defaultKeymap, ...CM.searchKeymap, ...CM.historyKeymap,
        ...CM.foldKeymap, ...CM.completionKeymap, CM.indentWithTab,
      ]),
      CM.EditorView.updateListener.of(update => {
        if (update.docChanged && tab.kind === 'file') {
          const dirty = update.state.doc.toString() !== tab.saved;
          if (dirty !== tab.dirty) { tab.dirty = dirty; renderTabs(); }
          if (S.previewOn && MARKDOWN.test(tab.path)) schedulePreview();
        }
        if (update.selectionSet || update.docChanged) showPosition(update.state);
      }),
      language.of([]),
      readOnly.of(CM.EditorState.readOnly.of(tab.kind !== 'file')),
    ];
  }

  async function languageFor(path) {
    const found = CM.LanguageDescription.matchFilename(CM.languages, base(path));
    if (!found) return null;
    try { return found.support || await found.load(); } catch { return null; }
  }

  function applyLanguage(tab, support) {
    tab.lang = support.language ? support.language.name : '';
    if (S.active === tab.id && S.view) {
      S.view.dispatch({ effects: language.reconfigure(support) });
      tab.state = S.view.state;
      showStatus(tab);
    } else tab.state = tab.state.update({ effects: language.reconfigure(support) }).state;
  }

  function ensureView() {
    if (!S.view) S.view = new CM.EditorView({ parent: $('editor-host') });
    return S.view;
  }

  function showPosition(state) {
    if (!state) { $('st-pos').textContent = ''; return; }
    const head = state.selection.main.head;
    const line = state.doc.lineAt(head);
    const picked = state.selection.ranges.reduce((n, r) => n + (r.to - r.from), 0);
    $('st-pos').textContent = `Baris ${line.number}, Kolom ${head - line.from + 1}${picked ? ` (${picked} dipilih)` : ''}`;
  }

  /* ── Projects ────────────────────────────────────────── */
  async function loadProjects() {
    try { S.projects = (await api('/api/ide/projects')).projects; } catch (e) { toast(e.message); S.projects = []; }
    renderHome();
    return S.projects;
  }

  function projectMeta(p) {
    const bits = [`${p.files} file`, bytes(p.size)];
    if (p.ai_turns) bits.unshift(`<b>${p.ai_turns}</b> giliran AI`);
    return `${bits.join(' · ')}<br>${p.last_change ? `diubah AI ${ago(p.last_change)}` : `diperbarui ${ago(p.updated)}`}`;
  }

  function renderHome() {
    const q = $('proj-filter').value.trim().toLowerCase();
    const list = S.projects.filter(p => !q || p.name.toLowerCase().includes(q));
    $('proj-list').innerHTML = list.length ? list.map(p => `
      <button class="proj" type="button" data-id="${esc(p.id)}">
        <span class="tag">${esc(p.tag || 'chat')}</span>
        <span class="nm">${esc(p.name)}</span>
        <span class="meta">${projectMeta(p)}</span>
      </button>`).join('') : `<div class="empty-note">${S.projects.length ? 'Tidak ada proyek dengan nama itu.' : 'Belum ada proyek. Minta AI membuat sesuatu di chat, foldernya muncul di sini.'}</div>`;
    $('proj-list').querySelectorAll('.proj').forEach(b => { b.onclick = () => openProject(b.dataset.id); });
  }
  $('proj-filter').oninput = renderHome;

  async function openProject(id, then = {}) {
    if (S.tabs.some(tb => tb.dirty) && S.project && S.project.id !== id && !confirm('Ada file yang belum disimpan. Tetap ganti proyek?')) return;
    if (!S.projects.length) await loadProjects();
    const project = S.projects.find(p => p.id === id);
    if (!project) { toast('Proyek itu tidak ada lagi.'); showHome(); return; }
    if (!S.project || S.project.id !== id) {
      S.project = project;
      S.tabs = [];
      S.active = '';
      S.selected = '';
      S.stamp = 0;
      try { S.expanded = new Set(JSON.parse(localStorage.getItem(`sq-ide-open:${id}`) || '[]')); } catch { S.expanded = new Set(); }
      try { localStorage.setItem(LAST_KEY, id); } catch { /* private window */ }
    }
    $('proj-name').textContent = project.name;
    $('act-zip').href = `${P()}/zip`;
    $('home').classList.add('hidden');
    await refresh(true);
    await loadTurns();
    if (then.path) await openFile(then.path);
    else if (!S.tabs.length) pickFirstFile();
    if (then.turn) {
      showPane('changes');
      if (then.diff) openDiff(then.turn, then.diff);
    }
    renderTabs();
  }

  function showHome() {
    S.project = null;
    $('proj-name').textContent = 'Pilih proyek';
    $('home').classList.remove('hidden');
    $('editor-host').classList.add('hidden');
    $('viewer').classList.add('hidden');
    $('tabs').innerHTML = '';
    $('tree').innerHTML = '';
    $('timeline').innerHTML = '';
    $('crumbs').innerHTML = '';
    ['st-file', 'st-ai', 'st-pos', 'st-lang', 'st-save'].forEach(id => { $(id).textContent = ''; });
    loadProjects();
  }

  function pickFirstFile() {
    const order = ['README.md', 'index.html', 'main.py', 'app.py', 'package.json'];
    const fresh = Object.keys(S.lastTurn).find(p => S.files.has(p));
    const found = fresh || order.find(p => S.files.has(p));
    if (found) openFile(found);
    else showViewer(`<div class="nofile"><h2>${esc(S.project.name)}</h2><p>${S.files.size ? 'Pilih file di kiri, atau tekan Ctrl+P untuk mencarinya.' : 'Folder ini masih kosong. Minta AI di chat untuk mulai membuat sesuatu.'}</p></div>`);
  }

  /* ── Files and the tree ──────────────────────────────── */
  async function refresh(force = false) {
    if (!S.project) return;
    let data;
    try { data = await api(`${P()}/tree`); } catch (e) { if (e.status === 404) { toast('Proyek ini sudah tidak ada.'); showHome(); } return; }
    const before = S.files;
    S.files = new Map(data.files.map(f => [f.path, f]));
    S.folders = new Set(data.folders);
    S.changed = data.changed || {};
    S.lastTurn = data.last_turn || {};
    const moved = data.stamp !== S.stamp;
    S.stamp = data.stamp;
    $('files-meta').textContent = `${data.files.length} file`;
    if (moved || force) renderTree();
    if (!force && moved) await followDisk(before);
  }

  /* What changed on disk while the editor was open: reload what is clean, warn about what is not. */
  async function followDisk(before) {
    const turnsBefore = S.turns.length;
    await loadTurns();
    const freshTurn = S.turns.length > turnsBefore ? S.turns[0] : null;
    for (const tab of S.tabs.filter(tb => tb.kind === 'file')) {
      const now = S.files.get(tab.path);
      const was = before.get(tab.path);
      if (!now) {
        if (was) { tab.gone = true; if (tab.id === S.active) banner(`${esc(base(tab.path))} sudah dihapus dari folder.`, 'warn'); }
        continue;
      }
      if (Math.abs(now.mtime - Number(tab.mtime) / 1e9) < 0.002) continue;
      if (tab.dirty) {
        tab.conflict = true;
        if (tab.id === S.active) conflictBanner(tab);
      } else {
        await reloadTab(tab);
        if (tab.id === S.active && freshTurn && freshTurn.changes.some(c => c.path === tab.path)) banner('Diperbarui oleh AI barusan.', 'info', 2600);
      }
    }
    if (S.previewOn) schedulePreview();
    if (freshTurn && freshTurn.by === 'ai') toast(`AI mengubah ${freshTurn.changes.length} file.`);
    renderTabs();
  }

  function treeNodes() {
    const children = new Map([['', []]]);
    const add = (parent, node) => { if (!children.has(parent)) children.set(parent, []); children.get(parent).push(node); };
    for (const folder of S.folders) add(dir(folder), { path: folder, dir: true });
    for (const path of S.files.keys()) add(dir(path), { path, dir: false });
    for (const list of children.values()) list.sort((a, b) => (b.dir - a.dir) || base(a.path).localeCompare(base(b.path), 'id', { numeric: true }));
    return children;
  }

  function rowHtml(path, isDir, depth, label) {
    const fresh = S.lastTurn[path] && !isDir;
    const status = S.changed[path];
    const mark = fresh ? `<span class="ai-mark ${S.lastTurn[path]}" title="${STATUS[S.lastTurn[path]][1]} di giliran terakhir">${STATUS[S.lastTurn[path]][0]}</span>` : '';
    const cls = ['row', isDir ? 'dir' : 'file', isDir && S.expanded.has(path) ? 'open' : '', fresh ? 'fresh' : '', !fresh && status && !isDir ? 'ever' : '', S.selected === path ? 'sel' : ''].filter(Boolean).join(' ');
    const icon = isDir ? '<span class="twist">▶</span>' : `<span class="twist"></span><span class="ext">${esc(extOf(path).slice(0, 4) || '·')}</span>`;
    return `<div class="${cls}" role="treeitem" data-path="${esc(path)}" data-dir="${isDir ? 1 : 0}" style="padding-left:${8 + depth * 14}px" ${isDir ? `aria-expanded="${S.expanded.has(path)}"` : ''} title="${esc(path)}${status && !isDir ? ` · ${STATUS[status] ? STATUS[status][1] : ''}` : ''}">${icon}<span class="name">${esc(label || base(path))}</span>${mark}</div>`;
  }

  function renderTree() {
    const tree = $('tree');
    const q = $('tree-filter').value.trim().toLowerCase();
    if (!S.files.size && !S.folders.size) { tree.innerHTML = '<div class="tree-empty">Belum ada file.</div>'; return; }
    if (q) {
      const hits = [...S.files.keys()].filter(p => p.toLowerCase().includes(q)).slice(0, 400);
      tree.innerHTML = hits.length ? hits.map(p => rowHtml(p, false, 0, p)).join('') : '<div class="tree-empty">Tidak ada yang cocok.</div>';
    } else {
      const children = treeNodes();
      const out = [];
      const walk = (parent, depth) => {
        for (const node of children.get(parent) || []) {
          out.push(rowHtml(node.path, node.dir, depth));
          if (node.dir && S.expanded.has(node.path)) walk(node.path, depth + 1);
        }
      };
      walk('', 0);
      tree.innerHTML = out.join('');
    }
  }

  function saveExpanded() {
    try { localStorage.setItem(`sq-ide-open:${S.project.id}`, JSON.stringify([...S.expanded])); } catch { /* private window */ }
  }

  function revealInTree(path) {
    const parts = path.split('/');
    for (let i = 1; i < parts.length; i++) S.expanded.add(parts.slice(0, i).join('/'));
    S.selected = path;
    renderTree();
    const row = $('tree').querySelector(`.row[data-path="${CSS.escape(path)}"]`);
    if (row) row.scrollIntoView({ block: 'nearest' });
  }

  $('tree').addEventListener('click', e => {
    const row = e.target.closest('.row');
    if (!row) return;
    const path = row.dataset.path;
    S.selected = path;
    if (row.dataset.dir === '1') {
      if (S.expanded.has(path)) S.expanded.delete(path); else S.expanded.add(path);
      saveExpanded();
      renderTree();
    } else {
      openFile(path);
      if (matchMedia('(max-width: 860px)').matches) closeSide();
    }
  });
  $('tree').addEventListener('contextmenu', e => {
    const row = e.target.closest('.row');
    if (!row) return;
    e.preventDefault();
    treeMenu(row.dataset.path, row.dataset.dir === '1', e.clientX, e.clientY);
  });
  $('tree').addEventListener('keydown', e => {
    const rows = [...$('tree').querySelectorAll('.row')];
    if (!rows.length) return;
    let index = rows.findIndex(r => r.dataset.path === S.selected);
    const current = rows[index];
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      index = Math.max(0, Math.min(rows.length - 1, index + (e.key === 'ArrowDown' ? 1 : -1)));
      S.selected = rows[index].dataset.path;
      renderTree();
      $('tree').querySelector('.row.sel')?.scrollIntoView({ block: 'nearest' });
    } else if (current && (e.key === 'Enter' || e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
      e.preventDefault();
      if (current.dataset.dir === '1') {
        if (e.key === 'ArrowLeft') S.expanded.delete(S.selected); else S.expanded.add(S.selected);
        saveExpanded();
        renderTree();
      } else if (e.key === 'Enter') openFile(S.selected);
    } else if (current && e.key === 'Delete') {
      e.preventDefault();
      removePath(S.selected);
    } else if (current && e.key === 'F2') {
      e.preventDefault();
      renamePath(S.selected);
    }
  });
  $('tree-filter').oninput = renderTree;
  $('collapse-all').onclick = () => { S.expanded.clear(); saveExpanded(); renderTree(); };
  $('refresh').onclick = () => refresh(true);

  /* ── Context menu ────────────────────────────────────── */
  function menu(items, x, y) {
    const box = $('menu');
    box.innerHTML = items.map((item, i) => item === '-' ? '<hr>' : `<button type="button" role="menuitem" data-i="${i}" class="${item.danger ? 'danger' : ''}">${esc(item.label)}</button>`).join('');
    box.classList.remove('hidden');
    const rect = box.getBoundingClientRect();
    box.style.left = `${Math.min(x, innerWidth - rect.width - 8)}px`;
    box.style.top = `${Math.min(y, innerHeight - rect.height - 8)}px`;
    box.querySelectorAll('button').forEach(b => { b.onclick = () => { closeMenu(); items[Number(b.dataset.i)].run(); }; });
    box.querySelector('button')?.focus();
  }
  function closeMenu() { $('menu').classList.add('hidden'); }
  document.addEventListener('click', e => { if (!e.target.closest('#menu')) closeMenu(); });

  function treeMenu(path, isDir, x, y) {
    const items = [];
    if (!isDir) items.push({ label: 'Buka', run: () => openFile(path) });
    if (!isDir && (PAGE.test(path) || MARKDOWN.test(path))) items.push({ label: 'Pratinjau', run: async () => { await openFile(path); setPreview(true); } });
    if (!isDir) items.push({ label: 'Tanya AI tentang file ini', run: async () => { await openFile(path); openAsk(); } });
    items.push('-');
    items.push({ label: isDir ? 'File baru di sini' : 'File baru di folder ini', run: () => newFile(isDir ? path : dir(path)) });
    items.push({ label: 'Ganti nama (F2)', run: () => renamePath(path) });
    items.push({ label: 'Salin path', run: () => navigator.clipboard.writeText(path).then(() => toast('Path disalin.')) });
    items.push('-');
    items.push({ label: 'Hapus (masuk sampah proyek)', danger: true, run: () => removePath(path) });
    menu(items, x, y);
  }

  async function newFile(folder = '') {
    const name = prompt('Nama file baru (boleh pakai folder, misalnya src/app.js):', folder ? `${folder}/` : '');
    if (!name || name.endsWith('/')) return;
    try {
      await api(`${P()}/file`, { method: 'PUT', body: { path: name.replace(/^\/+/, ''), text: '' } });
      await refresh(true);
      await openFile(name.replace(/^\/+/, ''));
    } catch (e) { toast(e.message); }
  }
  async function newFolder() {
    const name = prompt('Nama folder baru:', S.selected && S.folders.has(S.selected) ? `${S.selected}/` : '');
    if (!name) return;
    try {
      const made = await api(`${P()}/mkdir`, { method: 'POST', body: { path: name.replace(/^\/+|\/+$/g, '') } });
      S.expanded.add(made.path);
      saveExpanded();
      await refresh(true);
    } catch (e) { toast(e.message); }
  }
  async function renamePath(path) {
    const to = prompt('Nama baru:', path);
    if (!to || to === path) return;
    try {
      const done = await api(`${P()}/rename`, { method: 'POST', body: { path, to } });
      S.tabs.forEach(tab => {
        if (tab.path === path || tab.path.startsWith(`${path}/`)) { tab.path = done.path + tab.path.slice(path.length); tab.id = `${tab.kind}:${tab.path}`; }
      });
      await refresh(true);
      renderTabs();
    } catch (e) { toast(e.message); }
  }
  async function removePath(path) {
    if (!confirm(`Hapus ${path}? File dipindah ke sampah proyek (.agent_memory/trash), jadi masih bisa diambil lagi.`)) return;
    try {
      await api(`${P()}/delete`, { method: 'POST', body: { path } });
      S.tabs.filter(tab => tab.path === path || tab.path.startsWith(`${path}/`)).forEach(tab => closeTab(tab.id, true));
      await refresh(true);
      toast('Dipindah ke sampah proyek.');
    } catch (e) { toast(e.message); }
  }
  $('new-file').onclick = () => newFile(S.selected && S.folders.has(S.selected) ? S.selected : '');
  $('new-folder').onclick = newFolder;

  /* ── Tabs ────────────────────────────────────────────── */
  function activeTab() { return S.tabs.find(tab => tab.id === S.active) || null; }

  async function openFile(path, line = 0) {
    if (!S.project) return;
    const id = `file:${path}`;
    let tab = S.tabs.find(tb => tb.id === id);
    if (!tab) {
      let data;
      try { data = await api(`${P()}/file?path=${encodeURIComponent(path)}`); } catch (e) { toast(e.message); return; }
      tab = { id, kind: 'file', path: data.path, mtime: data.mtime, saved: data.text ?? '', dirty: false, binary: data.binary, tooBig: data.too_big, size: data.size };
      if (data.text !== null) {
        tab.state = CM.EditorState.create({ doc: data.text, extensions: baseExtensions(tab) });
        languageFor(path).then(support => {
          if (support) applyLanguage(tab, support);
        });
      }
      S.tabs.push(tab);
    }
    activate(tab.id);
    revealInTree(tab.path);
    if (line && tab.state) {
      const view = ensureView();
      const target = view.state.doc.line(Math.min(line, view.state.doc.lines));
      view.dispatch({ selection: { anchor: target.from }, effects: CM.EditorView.scrollIntoView(target.from, { y: 'center' }) });
      view.focus();
    }
  }

  async function openDiff(turnId, path) {
    const id = `diff:${turnId}:${path}`;
    let tab = S.tabs.find(tb => tb.id === id);
    if (!tab) {
      let data;
      try { data = await api(`${P()}/diff?turn=${encodeURIComponent(turnId)}&path=${encodeURIComponent(path)}`); } catch (e) { toast(e.message); return; }
      tab = { id, kind: 'diff', path, turn: turnId, data };
      if (!data.binary && data.stored) {
        tab.state = CM.EditorState.create({
          doc: data.after,
          extensions: [...baseExtensions(tab), CM.unifiedMergeView({ original: data.before, mergeControls: false, gutter: true, highlightChanges: true, syntaxHighlightDeletions: true })],
        });
        languageFor(path).then(support => {
          if (support) applyLanguage(tab, support);
        });
      }
      S.tabs.push(tab);
    }
    activate(tab.id);
  }

  function activate(id) {
    const old = activeTab();
    if (old && old.state && S.view && old.id !== id) old.state = S.view.state;
    S.active = id;
    const tab = activeTab();
    clearBanner();
    if (!tab) { renderTabs(); return; }
    if (tab.state) {
      $('viewer').classList.add('hidden');
      $('editor-host').classList.remove('hidden');
      ensureView().setState(tab.state);
      if (tab.kind === 'file') S.view.focus();
    } else {
      $('editor-host').classList.add('hidden');
      showViewer(viewerHtml(tab));
    }
    if (tab.conflict) conflictBanner(tab);
    else if (tab.kind === 'diff') diffBanner(tab);
    renderTabs();
    showStatus(tab);
    renderCrumbs(tab);
    if (S.previewOn) schedulePreview(true);
  }

  function viewerHtml(tab) {
    if (tab.kind === 'diff') {
      const d = tab.data;
      return `<div class="nofile"><h2>${esc(base(tab.path))}</h2><p>${d.binary ? 'File biner: isinya tidak bisa dibandingkan baris per baris.' : 'Isi file ini tidak tersimpan di riwayat (lebih dari 1 MB), jadi diff-nya tidak bisa ditampilkan.'}</p></div>`;
    }
    const url = rawUrl(tab.path);
    if (IMAGE.test(tab.path)) return `<img src="${esc(url)}" alt="${esc(base(tab.path))}">`;
    if (VIDEO.test(tab.path)) return `<video src="${esc(url)}" controls playsinline></video>`;
    if (AUDIO.test(tab.path)) return `<audio src="${esc(url)}" controls></audio>`;
    return `<div class="nofile"><h2>${esc(base(tab.path))}</h2><p>${tab.tooBig ? `File ini ${bytes(tab.size)}, terlalu besar untuk dibuka di editor.` : `File biner (${bytes(tab.size)}), tidak bisa diedit sebagai teks.`}</p><p><a class="tb" href="${esc(url)}" target="_blank" rel="noopener">Buka file</a></p></div>`;
  }

  function showViewer(html) {
    const viewer = $('viewer');
    viewer.innerHTML = html;
    viewer.classList.remove('hidden');
    $('editor-host').classList.add('hidden');
  }

  function closeTab(id, force = false) {
    const index = S.tabs.findIndex(tab => tab.id === id);
    if (index < 0) return;
    const tab = S.tabs[index];
    if (tab.dirty && !force && !confirm(`${base(tab.path)} belum disimpan. Tutup saja?`)) return;
    S.tabs.splice(index, 1);
    if (S.active === id) {
      const next = S.tabs[Math.min(index, S.tabs.length - 1)];
      if (next) activate(next.id);
      else {
        S.active = '';
        $('editor-host').classList.add('hidden');
        showViewer(`<div class="nofile"><h2>${esc(S.project ? S.project.name : '')}</h2><p>Pilih file di kiri, atau tekan Ctrl+P.</p></div>`);
        renderTabs();
        showStatus(null);
        renderCrumbs(null);
      }
    } else renderTabs();
  }

  function renderTabs() {
    const box = $('tabs');
    box.innerHTML = S.tabs.map(tab => {
      const fresh = tab.kind === 'file' && S.lastTurn[tab.path];
      return `<div class="tab ${tab.id === S.active ? 'on' : ''} ${tab.dirty ? 'dirty' : ''} ${fresh ? 'fresh' : ''}" role="tab" aria-selected="${tab.id === S.active}" data-id="${esc(tab.id)}" title="${esc(tab.path)}">
        ${tab.kind === 'diff' ? '<span class="kind">DIFF</span>' : ''}<span class="t">${esc(base(tab.path))}</span>
        <button class="x" type="button" aria-label="Tutup ${esc(base(tab.path))}"></button></div>`;
    }).join('');
    box.querySelectorAll('.tab').forEach(el => {
      el.onclick = e => { if (e.target.closest('.x')) closeTab(el.dataset.id); else activate(el.dataset.id); };
      el.onauxclick = e => { if (e.button === 1) closeTab(el.dataset.id); };
    });
    box.querySelector('.tab.on')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function renderCrumbs(tab) {
    if (!tab) { $('crumbs').innerHTML = ''; return; }
    const parts = tab.path.split('/');
    $('crumbs').innerHTML = parts.map((part, i) => i === parts.length - 1 ? `<b>${esc(part)}</b>` : `<span>${esc(part)}</span><span class="sep">/</span>`).join('');
  }

  function showStatus(tab) {
    $('st-file').textContent = tab ? (tab.kind === 'diff' ? `diff · ${tab.path}` : tab.path) : '';
    $('st-lang').textContent = tab && tab.lang ? tab.lang : '';
    $('st-save').textContent = tab && tab.kind === 'file' && tab.state ? (tab.dirty ? 'Belum disimpan' : 'Tersimpan') : '';
    const status = tab && S.changed[tab.path];
    const turn = status && S.turns.find(tr => tr.by === 'ai' && tr.changes.some(c => c.path === tab.path));
    $('st-ai').textContent = turn ? `${STATUS[status] ? STATUS[status][1] : 'Diubah AI'} ${ago(turn.ended)}` : '';
    showPosition(tab && tab.state && S.view && S.active === tab.id ? S.view.state : null);
  }

  async function reloadTab(tab) {
    let data;
    try { data = await api(`${P()}/file?path=${encodeURIComponent(tab.path)}`); } catch { return; }
    if (data.text === null) return;
    tab.saved = data.text;
    tab.mtime = data.mtime;
    tab.dirty = false;
    tab.conflict = false;
    const live = S.active === tab.id && S.view;
    const state = live ? S.view.state : tab.state;
    const head = Math.min(state.selection.main.head, data.text.length);
    const tr = state.update({ changes: { from: 0, to: state.doc.length, insert: data.text }, selection: { anchor: head } });
    if (live) { S.view.dispatch(tr); tab.state = S.view.state; } else tab.state = tr.state;
    tab.dirty = false;
    renderTabs();
    if (live) showStatus(tab);
  }

  async function save(force = false) {
    const tab = activeTab();
    if (!tab || tab.kind !== 'file' || !tab.state) return;
    const text = S.view.state.doc.toString();
    try {
      const done = await api(`${P()}/file${force ? '?force=1' : ''}`, { method: 'PUT', body: { path: tab.path, text, mtime: tab.mtime } });
      tab.mtime = done.mtime;
      tab.saved = text;
      tab.dirty = false;
      tab.conflict = false;
      tab.state = S.view.state;
      clearBanner();
      renderTabs();
      showStatus(tab);
      if (S.previewOn) schedulePreview(true);
      refresh();
    } catch (e) {
      if (e.status === 409) { tab.conflict = true; conflictBanner(tab); } else toast(e.message);
    }
  }

  /* ── Banners ─────────────────────────────────────────── */
  function banner(html, kind = 'warn', ms = 0, actions = []) {
    const el = $('banner');
    el.className = `banner ${kind === 'info' ? 'info' : ''}`;
    el.innerHTML = `<span class="grow">${html}</span>${actions.map((a, i) => `<button type="button" class="tb ${a.cls || ''}" data-i="${i}">${esc(a.label)}</button>`).join('')}`;
    el.querySelectorAll('button').forEach(b => { b.onclick = () => actions[Number(b.dataset.i)].run(); });
    clearTimeout(banner.timer);
    if (ms) banner.timer = setTimeout(clearBanner, ms);
  }
  function clearBanner() { $('banner').classList.add('hidden'); $('banner').innerHTML = ''; }
  function conflictBanner(tab) {
    banner(`<b>${esc(base(tab.path))}</b> diubah di disk (mungkin oleh AI) sementara kamu juga mengubahnya.`, 'warn', 0, [
      { label: 'Ambil versi di disk', run: () => reloadTab(tab).then(clearBanner) },
      { label: 'Simpan punyaku', cls: 'primary', run: () => save(true) },
    ]);
  }
  function diffBanner(tab) {
    const d = tab.data;
    const label = { added: 'dibuat', modified: 'diubah', deleted: 'dihapus' }[d.status] || 'diubah';
    banner(`File ini ${label} saat: <i>${esc(d.prompt || 'giliran AI')}</i> · ${ago(d.ended)}`, 'info', 0, [
      ...(d.status !== 'deleted' ? [{ label: 'Buka file', run: () => openFile(tab.path) }] : []),
      { label: 'Kembalikan file ini', run: () => restore(tab.turn, tab.path) },
    ]);
  }

  /* ── AI changes ──────────────────────────────────────── */
  async function loadTurns() {
    if (!S.project) return;
    try { S.turns = (await api(`${P()}/turns`)).turns; } catch { S.turns = []; }
    renderTimeline();
  }

  function renderTimeline() {
    const ai = S.turns.filter(tr => tr.by === 'ai').length;
    $('changes-count').textContent = ai;
    $('changes-count').classList.toggle('hidden', !ai);
    if (!S.turns.length) {
      $('timeline').innerHTML = '<div class="tree-empty">Belum ada perubahan tercatat. Perubahan dicatat sejak add-on ini aktif, untuk setiap giliran chat.</div>';
      return;
    }
    $('timeline').innerHTML = S.turns.map(turn => {
      const who = esc({ ai: 'AI', restore: 'Dikembalikan', editor: 'Editor' }[turn.by] || turn.by);
      return `<article class="turn by-${esc(turn.by)}" data-turn="${esc(turn.id)}">
        <div class="turn-head"><span class="who">${who}</span><span title="${esc(new Date(turn.ended * 1000).toLocaleString('id-ID'))}">${ago(turn.ended)}</span>
          <span class="st"><span class="plus">+${turn.added}</span> <span class="minus">−${turn.removed}</span></span></div>
        ${turn.prompt ? `<p class="turn-prompt" title="${esc(turn.prompt)}">${esc(turn.prompt)}</p>` : ''}
        <ul class="turn-files">${turn.changes.map(c => `<li>
          <button class="f" type="button" data-path="${esc(c.path)}" title="Lihat diff ${esc(c.path)}"><span class="ai-mark ${c.status}">${STATUS[c.status][0]}</span><span class="p">${esc(c.path)}</span>${c.binary ? '<span class="n">biner</span>' : `<span class="n"><span class="plus">+${c.added}</span> <span class="minus">−${c.removed}</span></span>`}</button>
          <button class="ib undo" type="button" data-undo="${esc(c.path)}" title="Kembalikan file ini ke sebelum giliran" aria-label="Kembalikan ${esc(c.path)}"><svg viewBox="0 0 24 24" class="ico" aria-hidden="true"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg></button></li>`).join('')}</ul>
        <div class="turn-acts">
          <button class="tb" type="button" data-act="explain">Minta AI jelaskan</button>
          <button class="tb" type="button" data-act="undo">Kembalikan semua</button>
        </div></article>`;
    }).join('');
    $('timeline').querySelectorAll('.turn').forEach(el => {
      const turn = S.turns.find(tr => tr.id === el.dataset.turn);
      el.querySelectorAll('button.f').forEach(b => { b.onclick = () => openDiff(turn.id, b.dataset.path); });
      el.querySelectorAll('[data-undo]').forEach(b => { b.onclick = () => restore(turn.id, b.dataset.undo); });
      el.querySelector('[data-act="undo"]').onclick = () => restore(turn.id);
      el.querySelector('[data-act="explain"]').onclick = () => askChat(
        `[IDE] Jelaskan perubahan yang kamu buat pada giliran "${turn.prompt || 'sebelumnya'}" di file ${turn.changes.map(c => c.path).join(', ')}: apa tujuannya, apa yang berubah, dan apa yang perlu aku cek.`,
      );
    });
  }

  async function restore(turnId, path) {
    const what = path ? path : 'semua file di giliran ini';
    if (!confirm(`Kembalikan ${what} ke keadaan sebelum giliran itu? Pengembalian ini juga tercatat, jadi bisa diulang.`)) return;
    try {
      await api(`${P()}/restore`, { method: 'POST', body: { turn: turnId, path: path || null } });
      toast('Dikembalikan.');
      await refresh();
      await loadTurns();
      for (const tab of S.tabs.filter(tb => tb.kind === 'file' && !tb.dirty)) await reloadTab(tab);
    } catch (e) { toast(e.message, 5000); }
  }

  /* ── Search across files ─────────────────────────────── */
  let searchTimer = 0;
  function scheduleSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 280); }
  async function runSearch() {
    const q = $('search-q').value;
    if (!q.trim() || !S.project) { $('search-results').innerHTML = ''; $('search-meta').textContent = ''; return; }
    let data;
    try { data = await api(`${P()}/search?q=${encodeURIComponent(q)}&regex=${$('search-regex').checked}&case=${$('search-case').checked}`); } catch (e) { $('search-meta').textContent = e.message; return; }
    $('search-meta').textContent = `${data.hits.length}${data.more ? '+' : ''} hasil di ${data.files} file`;
    let pattern = null;
    try { pattern = new RegExp($('search-regex').checked ? q : q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), $('search-case').checked ? 'g' : 'gi'); } catch { pattern = null; }
    const mark = text => {
      if (!pattern) return esc(text);
      let out = '';
      let last = 0;
      for (const found of text.matchAll(pattern)) {
        if (!found[0]) break;
        out += `${esc(text.slice(last, found.index))}<mark>${esc(found[0])}</mark>`;
        last = found.index + found[0].length;
      }
      return out + esc(text.slice(last));
    };
    const groups = new Map();
    data.hits.forEach(h => { if (!groups.has(h.path)) groups.set(h.path, []); groups.get(h.path).push(h); });
    $('search-results').innerHTML = [...groups].map(([path, hits]) => `<div class="hit-file" title="${esc(path)}">${esc(path)}</div>${hits.map(h => `
      <button class="hit" type="button" data-path="${esc(path)}" data-line="${h.line}"><span class="ln">${h.line}</span><span class="tx">${mark(h.text.trim())}</span></button>`).join('')}`).join('')
      || '<div class="tree-empty">Tidak ketemu.</div>';
    $('search-results').querySelectorAll('.hit').forEach(b => { b.onclick = () => openFile(b.dataset.path, Number(b.dataset.line)); });
  }
  $('search-q').oninput = scheduleSearch;
  $('search-case').onchange = runSearch;
  $('search-regex').onchange = runSearch;

  /* ── Side panes ──────────────────────────────────────── */
  function showPane(name) {
    document.querySelectorAll('.side-tabs button').forEach(b => {
      const on = b.dataset.pane === name;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', String(on));
    });
    ['files', 'search', 'changes'].forEach(p => $(`pane-${p}`).classList.toggle('hidden', p !== name));
    if (name === 'search') setTimeout(() => $('search-q').focus(), 0);
    if (matchMedia('(max-width: 860px)').matches) openSide();
  }
  document.querySelectorAll('.side-tabs button').forEach(b => { b.onclick = () => showPane(b.dataset.pane); });
  function openSide() { $('side').classList.add('open'); $('scrim').classList.remove('hidden'); }
  function closeSide() { $('side').classList.remove('open'); $('scrim').classList.add('hidden'); }
  $('side-toggle').onclick = () => ($('side').classList.contains('open') ? closeSide() : openSide());
  $('scrim').onclick = closeSide;

  /* ── Quick open and the project switcher ─────────────── */
  function score(query, text) {
    if (!query) return 1;
    const q = query.toLowerCase();
    const s = text.toLowerCase();
    const name = base(s);
    if (name.startsWith(q)) return 1000 - name.length;
    if (name.includes(q)) return 800 - name.length;
    if (s.includes(q)) return 600 - s.length;
    let at = 0;
    let gaps = 0;
    for (const ch of q) {
      const found = s.indexOf(ch, at);
      if (found < 0) return 0;
      gaps += found - at;
      at = found + 1;
    }
    return Math.max(1, 400 - gaps - s.length);
  }

  function picker(boxId, inputId, listId, items, render, choose) {
    const box = $(boxId);
    const input = $(inputId);
    let index = 0;
    let shown = [];
    const draw = () => {
      const q = input.value.trim();
      shown = items().map(item => [score(q, item.key), item]).filter(([s]) => s > 0).sort((a, b) => b[0] - a[0]).slice(0, 60).map(([, item]) => item);
      index = Math.min(index, Math.max(0, shown.length - 1));
      $(listId).innerHTML = shown.length ? shown.map((item, i) => `<button type="button" class="qi ${i === index ? 'on' : ''}" role="option" aria-selected="${i === index}" data-i="${i}">${render(item)}</button>`).join('') : '<div class="tree-empty">Tidak ada yang cocok.</div>';
      $(listId).querySelectorAll('.qi').forEach(b => { b.onclick = () => { close(); choose(shown[Number(b.dataset.i)]); }; });
      $(listId).querySelector('.qi.on')?.scrollIntoView({ block: 'nearest' });
    };
    const close = () => box.classList.add('hidden');
    input.oninput = () => { index = 0; draw(); };
    input.onkeydown = e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); index = Math.min(index + 1, shown.length - 1); draw(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); index = Math.max(index - 1, 0); draw(); }
      else if (e.key === 'Enter') { e.preventDefault(); if (shown[index]) { close(); choose(shown[index]); } }
      else if (e.key === 'Escape') { e.preventDefault(); close(); }
    };
    box.onclick = e => { if (e.target === box) close(); };
    return () => { input.value = ''; index = 0; box.classList.remove('hidden'); draw(); input.focus(); };
  }

  const openQuick = picker('quick', 'quick-q', 'quick-list',
    () => [...S.files.keys()].map(path => ({ key: path, path })),
    item => `<span class="nm">${esc(base(item.path))}</span><span class="dir">${esc(dir(item.path))}</span>${S.lastTurn[item.path] ? `<span class="right"><span class="ai-mark ${S.lastTurn[item.path]}">${STATUS[S.lastTurn[item.path]][0]}</span></span>` : ''}`,
    item => openFile(item.path));
  const openProjects = picker('projects', 'projects-q', 'projects-list',
    () => S.projects.map(p => ({ key: p.name, p })),
    item => `<span class="nm">${esc(item.p.name)}</span><span class="dir">${esc(item.p.tag || '')}</span><span class="right">${item.p.files} file · ${ago(item.p.updated)}</span>`,
    item => openProject(item.p.id));
  $('act-quick').onclick = () => { if (S.project) openQuick(); else toast('Pilih proyek dulu.'); };
  $('proj-btn').onclick = async () => { await loadProjects(); openProjects(); };

  /* ── Preview ─────────────────────────────────────────── */
  let previewTimer = 0;
  function schedulePreview(now = false) { clearTimeout(previewTimer); previewTimer = setTimeout(renderPreview, now ? 0 : 500); }

  async function previewSources() {
    const tab = activeTab();
    const options = [];
    if (tab && tab.kind === 'file' && (PAGE.test(tab.path) || MARKDOWN.test(tab.path))) options.push({ value: `file:${tab.path}`, label: `${MARKDOWN.test(tab.path) ? 'Markdown' : 'Halaman'}: ${tab.path}` });
    for (const path of S.files.keys()) {
      if (/(^|\/)index\.html?$/i.test(path) && !options.some(o => o.value === `file:${path}`)) options.push({ value: `file:${path}`, label: `Halaman: ${path}` });
    }
    try {
      const running = (await api('/api/preview')).previews || [];
      running.forEach(p => options.push({ value: `port:${p.port}`, label: `Server berjalan: port ${p.port}${p.title ? ` · ${p.title}` : ''}` }));
    } catch { /* no dev servers */ }
    return options;
  }

  async function renderPreview() {
    if (!S.previewOn || !S.project) return;
    const select = $('preview-src');
    const before = select.value;
    const options = await previewSources();
    const tab = activeTab();
    const wanted = tab && tab.kind === 'file' && options.some(o => o.value === `file:${tab.path}`) ? `file:${tab.path}` : before;
    select.innerHTML = options.map(o => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join('');
    if (options.some(o => o.value === wanted)) select.value = wanted;
    const body = $('preview-body');
    const value = select.value;
    if (!value) {
      body.innerHTML = '<div class="none"><p>Buka file .html atau .md, atau jalankan server (npm run dev, php artisan serve) lewat AI atau terminal.</p></div>';
      $('preview-open').removeAttribute('href');
      return;
    }
    if (value.startsWith('port:')) {
      try {
        const opened = await api('/api/preview/open', { method: 'POST', body: { port: Number(value.slice(5)) } });
        setFrame(opened.url);
      } catch (e) { body.innerHTML = `<div class="none"><p>${esc(e.message)}</p></div>`; }
      return;
    }
    const path = value.slice(5);
    if (MARKDOWN.test(path)) {
      const open = S.tabs.find(tb => tb.id === `file:${path}`);
      const text = open && S.active === open.id ? S.view.state.doc.toString() : open ? open.state.doc.toString() : (await api(`${P()}/file?path=${encodeURIComponent(path)}`)).text || '';
      const html = window.DOMPurify ? window.DOMPurify.sanitize(window.marked.parse(text)) : esc(text);
      const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html');
      doc.querySelectorAll('img[src]').forEach(img => {
        const src = img.getAttribute('src');
        if (src && !/^([a-z]+:|\/|#)/i.test(src)) img.setAttribute('src', rawUrl([dir(path), src].filter(Boolean).join('/')));
      });
      body.innerHTML = `<div class="md">${doc.body.firstChild.innerHTML}</div>`;
      $('preview-open').removeAttribute('href');
      return;
    }
    setFrame(`${rawUrl(path)}?t=${Date.now()}`);
  }

  function setFrame(url) {
    const body = $('preview-body');
    let frame = body.querySelector('iframe');
    if (!frame) {
      body.innerHTML = '';
      frame = document.createElement('iframe');
      frame.title = 'Pratinjau';
      frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-popups allow-modals allow-downloads');
      body.append(frame);
    }
    frame.src = url;
    $('preview-open').href = url;
  }

  function setPreview(on) {
    S.previewOn = on;
    $('preview').classList.toggle('hidden', !on);
    $('act-preview').setAttribute('aria-pressed', String(on));
    if (on) schedulePreview(true);
    else $('preview-body').innerHTML = '';
  }
  $('act-preview').onclick = () => { if (S.project) setPreview(!S.previewOn); else toast('Pilih proyek dulu.'); };
  $('preview-close').onclick = () => setPreview(false);
  $('preview-reload').onclick = () => schedulePreview(true);
  $('preview-src').onchange = () => schedulePreview(true);

  /* ── Ask the AI ──────────────────────────────────────── */
  function selectionContext() {
    const tab = activeTab();
    if (!tab || !tab.state || !S.view || S.active !== tab.id) return { tab, text: '', from: 0, to: 0 };
    const state = S.view.state;
    const range = state.selection.main;
    if (range.empty) return { tab, text: '', from: 0, to: 0 };
    return { tab, text: state.sliceDoc(range.from, range.to), from: state.doc.lineAt(range.from).number, to: state.doc.lineAt(range.to).number };
  }

  function openAsk() {
    if (!S.project) { toast('Pilih proyek dulu.'); return; }
    const ctx = selectionContext();
    $('ask-context').textContent = ctx.tab ? (ctx.text ? `${ctx.tab.path} · baris ${ctx.from}-${ctx.to}` : ctx.tab.path) : S.project.name;
    $('ask').classList.remove('hidden');
    $('ask-q').value = '';
    setTimeout(() => $('ask-q').focus(), 0);
  }
  function closeAsk() { $('ask').classList.add('hidden'); }
  $('act-ask').onclick = openAsk;
  $('ask-cancel').onclick = closeAsk;
  $('ask').onclick = e => { if (e.target === $('ask')) closeAsk(); };
  $('ask-quick').querySelectorAll('button').forEach(b => { b.onclick = () => { $('ask-q').value = b.dataset.q; $('ask-q').focus(); }; });
  $('ask-q').onkeydown = e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $('ask-form').requestSubmit(); } };
  $('ask-form').onsubmit = e => {
    e.preventDefault();
    const question = $('ask-q').value.trim();
    if (!question) { $('ask-q').focus(); return; }
    const ctx = selectionContext();
    let text = '';
    if (ctx.tab && ctx.text) {
      const lang = extOf(ctx.tab.path);
      text = `[IDE] ${ctx.tab.path} (baris ${ctx.from}-${ctx.to})\n\n\`\`\`${lang}\n${ctx.text.slice(0, 12000)}\n\`\`\`\n\n${question}`;
    } else if (ctx.tab) {
      text = `[IDE] File: ${ctx.tab.path}\n\n${question}`;
    } else text = `[IDE] ${question}`;
    if (!S.project.session) text = `${text}\n\n(Proyek ini ada di folder: ${S.project.name})`;
    closeAsk();
    askChat(text);
  };

  function askChat(text) {
    if (embedded) {
      window.parent.postMessage({ type: 'sq-chat', text, session: S.project ? S.project.session : '' }, '*');
      toast('Dikirim ke chat. Perubahan dari AI muncul di sini begitu selesai.');
    } else {
      navigator.clipboard.writeText(text).then(() => toast('Pesan disalin. Buka chat proyek ini dan tempel di sana.'), () => toast('Buka IDE dari menu SquidWings untuk bertanya ke AI.'));
    }
  }

  /* ── Keys, polling, messages from the chat page ──────── */
  document.addEventListener('keydown', e => {
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === 'Escape') { closeMenu(); closeAsk(); closeSide(); return; }
    // The editor's own keymap already took it (Ctrl+S, Ctrl+I inside the editor): do not run it twice.
    if (!mod || e.defaultPrevented) return;
    const key = e.key.toLowerCase();
    if (key === 'p' && !e.shiftKey) { e.preventDefault(); if (S.project) openQuick(); }
    else if (key === 'o') { e.preventDefault(); $('proj-btn').click(); }
    else if (key === 's') { e.preventDefault(); save(); }
    else if (key === 'f' && e.shiftKey) { e.preventDefault(); showPane('search'); }
    else if (key === 'v' && e.shiftKey) { e.preventDefault(); $('act-preview').click(); }
    else if (key === 'i') { e.preventDefault(); openAsk(); }
    else if (key === 'w' && S.active) { e.preventDefault(); closeTab(S.active); }
  });
  window.addEventListener('beforeunload', e => { if (S.tabs.some(tb => tb.dirty)) { e.preventDefault(); e.returnValue = ''; } });

  async function poll() {
    if (!document.hidden && S.project) await refresh();
    S.polling = setTimeout(poll, POLL_MS);
  }

  async function openFor({ project, session, path, turn, diff }) {
    await loadProjects();
    const found = project ? S.projects.find(p => p.id === project) : session ? S.projects.find(p => p.session === session) : null;
    if (found) await openProject(found.id, { path, turn, diff });
    else if (!S.project) {
      const last = (() => { try { return localStorage.getItem(LAST_KEY); } catch { return ''; } })();
      if (!session && last && S.projects.some(p => p.id === last)) await openProject(last);
    }
  }

  window.addEventListener('message', e => {
    if (e.source !== window.parent || !e.data || e.data.type !== 'sq-ide-open') return;
    openFor(e.data);
  });

  /* ── Start ───────────────────────────────────────────── */
  if (!CM) {
    document.body.innerHTML = '<p style="padding:2rem">Editor belum terpasang: vendor/codemirror.js tidak ada. Jalankan npm install && npm run build di addons/ide/build.</p>';
    return;
  }
  openFor({ project: params.get('project') || '', session: params.get('session') || '', path: params.get('path') || '', turn: params.get('turn') || '', diff: params.get('diff') || '' })
    .finally(() => { poll(); });
})();
