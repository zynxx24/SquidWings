/* Video Editor for code projects (HyperFrames, Remotion, HTML): the C engine lists the texts, colours, timings and
   media of a file; this page edits them as fields and saves only what changed. Saves carry the file's stamp, so
   a change the AI made meanwhile is never overwritten (409: the blocks reload and unsaved values are kept). */
(() => {
  'use strict';
  const params = new URLSearchParams(location.search);
  const KINDS = { text: 'Teks', color: 'Warna', time: 'Waktu', media: 'Media', css: 'Gaya', json: 'Data' };
  const ENGINES = { hyperframes: 'HyperFrames', remotion: 'React (Remotion)', html: 'HTML' };
  const S = {
    session: params.get('session') || '', project: params.get('project') || '', path: params.get('path') || '',
    projects: [], files: [], kind: '', blocks: [], stamp: '', pending: new Map(), filter: '', show: 'all', ide: false,
    busy: false, poll: 0,
  };
  const $ = id => document.getElementById(id);
  const embedded = (() => { try { return window.self !== window.top; } catch { return true; } })();

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === false || value == null) continue;
      if (key === 'text') node.textContent = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children) if (child) node.append(child);
    return node;
  }

  async function call(path, options = {}) {
    const res = await fetch(`/api/studio/${encodeURIComponent(S.session)}/${path}`, {
      credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, ...options,
    });
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    if (!res.ok) {
      const detail = body && body.detail;
      const err = new Error(typeof detail === 'string' ? detail : (detail && detail.message) || `Permintaan gagal (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return body;
  }

  function state(text, kind = '') { const s = $('save-state'); s.textContent = text; s.dataset.kind = kind; }
  function banner(text, kind = 'warn') {
    const b = $('banner');
    b.hidden = !text;
    b.textContent = text || '';
    b.dataset.kind = kind;
  }

  // ── projects and files ─────────────────────────────────────────────────────────
  async function loadProjects() {
    if (!S.session) {
      showEmpty('Buka Video Editor dari Studio di dalam chat, supaya proyek chat itu yang tampil.');
      return;
    }
    state('Mencari proyek', 'busy');
    try {
      const data = await call('code/projects');
      S.projects = data.projects;
      S.ide = data.ide;
    } catch (err) {
      state('');
      showEmpty(`Daftar proyek tidak bisa dibaca: ${err.message}`);
      return;
    }
    const select = $('project-select');
    select.replaceChildren(...S.projects.map(p => el('option', { value: p.path, text: `${p.name} · ${ENGINES[p.kind] || p.kind}` })));
    state('');
    if (!S.projects.length) {
      showEmpty('Belum ada proyek video kode di chat ini. Minta AI membuat video HyperFrames atau Remotion, '
        + 'atau pakai Studio motion untuk video tanpa kode.');
      return;
    }
    if (!S.projects.some(p => p.path === S.project)) S.project = S.projects[0].path;
    select.value = S.project;
    await loadFiles();
  }

  async function loadFiles() {
    const data = await call(`code/files?project=${encodeURIComponent(S.project)}`).catch(err => { banner(err.message); return null; });
    if (!data) return;
    S.files = data.files;
    S.kind = data.kind;
    S.ide = data.ide;
    $('file-select').replaceChildren(...S.files.map(f => el('option', { value: f.path, text: f.path })));
    if (!S.files.some(f => f.path === S.path)) S.path = S.files.length ? S.files[0].path : '';
    $('file-select').value = S.path;
    $('open-ide').hidden = !S.ide;
    $('render').disabled = S.kind === 'html';
    $('render').title = S.kind === 'html' ? 'Halaman HTML biasa tidak dirender. Minta AI menjadikannya proyek HyperFrames.' : 'Render jadi MP4';
    $('tab-preview').hidden = S.kind === 'remotion';
    selectTab(S.kind === 'remotion' ? 'render' : 'preview');
    const project = S.projects.find(p => p.path === S.project);
    showRender(project && project.render_url);
    remember();
    await loadBlocks();
    if (S.kind !== 'remotion') refreshPreview();
  }

  function remember() {
    const q = new URLSearchParams({ session: S.session, project: S.project, path: S.path });
    history.replaceState(null, '', `${location.pathname}?${q}`);
  }

  // ── blocks ─────────────────────────────────────────────────────────────────────
  async function loadBlocks(keep = false) {
    if (!S.path) { S.blocks = []; render(); return; }
    const saved = keep ? pendingByKey() : null;
    state('Membaca file', 'busy');
    try {
      const data = await call(`code/blocks?project=${encodeURIComponent(S.project)}&path=${encodeURIComponent(S.path)}`);
      S.blocks = data.blocks;
      S.stamp = data.stamp;
    } catch (err) {
      state('');
      S.blocks = [];
      render(`File ini tidak bisa dibaca: ${err.message}`);
      return;
    }
    S.pending.clear();
    if (saved) for (const b of S.blocks) if (b.editable && saved.has(`${b.kind}|${b.key}`)) S.pending.set(b.id, saved.get(`${b.kind}|${b.key}`));
    state(S.pending.size ? `${S.pending.size} belum disimpan` : '', S.pending.size ? 'warn' : '');
    render();
  }

  function pendingByKey() {
    const out = new Map();
    for (const b of S.blocks) if (S.pending.has(b.id)) out.set(`${b.kind}|${b.key}`, S.pending.get(b.id));
    return out;
  }

  function renderKinds() {
    const counts = {};
    for (const b of S.blocks) counts[b.kind] = (counts[b.kind] || 0) + 1;
    const chips = [['all', `Semua ${S.blocks.length}`], ...Object.keys(KINDS).filter(k => counts[k]).map(k => [k, `${KINDS[k]} ${counts[k]}`])];
    if (!chips.some(([k]) => k === S.show)) S.show = 'all';
    $('kinds').replaceChildren(...chips.map(([kind, text]) => el('button', {
      type: 'button', text, 'aria-pressed': String(S.show === kind), onclick: () => { S.show = kind; render(); },
    })));
  }

  function matches(b) {
    if (S.show !== 'all' && b.kind !== S.show) return false;
    if (!S.filter) return true;
    const needle = S.filter.toLowerCase();
    return b.label.toLowerCase().includes(needle) || String(S.pending.get(b.id) ?? b.value).toLowerCase().includes(needle);
  }

  function render(message = '') {
    renderKinds();
    const list = $('list');
    if (message) { list.replaceChildren(el('p', { class: 'ce-empty', text: message })); updateSave(); return; }
    if (!S.blocks.length) {
      list.replaceChildren(el('p', { class: 'ce-empty', text: S.path
        ? 'File ini tidak punya bagian yang bisa diubah tanpa kode. Pilih file lain, atau buka di IDE.'
        : 'Proyek ini belum punya file HTML, CSS, JSON, atau React.' }));
      updateSave();
      return;
    }
    // One group per kind, in the order of KINDS; inside a group, the order of the file.
    const order = Object.keys(KINDS);
    const shown = S.blocks.filter(matches).map((b, i) => [b, i])
      .sort(([a, i], [b, j]) => (order.indexOf(a.kind) - order.indexOf(b.kind)) || i - j).map(([b]) => b);
    const rows = [];
    let group = '';
    for (const b of shown) {
      if (S.show === 'all' && b.kind !== group) { group = b.kind; rows.push(el('h3', { class: 'ce-group', text: KINDS[b.kind] || b.kind })); }
      rows.push(row(b));
    }
    list.replaceChildren(...(rows.length ? rows : [el('p', { class: 'ce-empty', text: 'Tidak ada bagian yang cocok dengan pencarian.' })]));
    updateSave();
  }

  function unit(b) {
    if (b.kind !== 'time') return '';
    const frames = S.kind === 'remotion' || /\.(tsx|jsx|ts|js|mjs)$/.test(S.path);
    return frames && /^(durationInFrames|from|fps|startFrom|endAt)@/.test(b.key) ? 'frame' : 'detik';
  }

  function hex6(value) {
    const v = String(value).trim();
    if (/^#[0-9a-f]{3}$/i.test(v)) return `#${v[1]}${v[1]}${v[2]}${v[2]}${v[3]}${v[3]}`.toLowerCase();
    if (/^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(v)) return v.slice(0, 7).toLowerCase();
    return '#000000';
  }

  function row(b) {
    const id = `b-${b.id.replace(':', '-')}`;
    const current = S.pending.has(b.id) ? S.pending.get(b.id) : b.value;
    const set = value => {
      if (value === b.value) S.pending.delete(b.id); else S.pending.set(b.id, value);
      wrap.classList.toggle('is-changed', S.pending.has(b.id));
      updateSave();
    };
    const head = el('label', { for: id }, el('code', { text: b.label }), unit(b) ? el('span', { class: 'ce-unit', text: unit(b) }) : null);
    const field = el('div', { class: 'ce-field' });
    if (!b.editable) {
      field.append(el('span', { class: 'ce-readonly', id, text: b.value }));
      if (S.ide) field.append(el('button', { type: 'button', class: 'st-link', text: 'Ubah di IDE', onclick: openIde }));
    } else if (b.kind === 'color') {
      const text = el('input', { type: 'text', id, value: current, spellcheck: 'false', autocomplete: 'off' });
      const pick = el('input', { type: 'color', value: hex6(current), 'aria-label': `Pilih warna ${b.label}` });
      pick.addEventListener('input', () => {
        const alpha = /^#[0-9a-f]{8}$/i.test(text.value.trim()) ? text.value.trim().slice(7) : '';
        text.value = pick.value + alpha;
        set(text.value);
      });
      text.addEventListener('input', () => { pick.value = hex6(text.value); set(text.value); });
      field.append(pick, text);
    } else if (b.kind === 'text' && (String(current).length > 60 || String(current).includes('\n'))) {
      const area = el('textarea', { id, rows: String(Math.min(8, Math.ceil(String(current).length / 60) + 1)) });
      area.value = current;
      area.addEventListener('input', () => set(area.value));
      field.append(area);
    } else {
      const input = el('input', { type: 'text', id, value: current, spellcheck: b.kind === 'text' ? 'true' : 'false', autocomplete: 'off',
        inputmode: b.kind === 'time' ? 'decimal' : null });
      input.addEventListener('input', () => set(input.value));
      field.append(input);
    }
    const wrap = el('div', { class: `ce-row${S.pending.has(b.id) ? ' is-changed' : ''}`, 'data-id': b.id }, head, field);
    return wrap;
  }

  function updateSave() {
    const n = S.pending.size;
    $('save').disabled = !n || S.busy;
    $('save').querySelector('span').textContent = n ? `Simpan ${n}` : 'Simpan';
    if (!S.busy) state(n ? `${n} belum disimpan` : (S.stamp ? 'Tersimpan' : ''), n ? 'warn' : (S.stamp ? 'ok' : ''));
  }

  async function save() {
    if (!S.pending.size || S.busy) return;
    S.busy = true;
    state('Menyimpan', 'busy');
    updateSave();
    const edits = [...S.pending].map(([id, value]) => ({ id, value }));
    try {
      const data = await call('code/blocks', { method: 'PUT', body: JSON.stringify({ project: S.project, path: S.path, base: S.stamp, edits }) });
      S.blocks = data.blocks;
      S.stamp = data.stamp;
      S.pending.clear();
      banner('');
      S.busy = false;
      render();
      if (S.kind !== 'remotion') refreshPreview();
    } catch (err) {
      S.busy = false;
      if (err.status === 409) {
        banner('File ini diubah di tempat lain (mungkin oleh AI). Bagian dimuat ulang; perubahanmu yang belum tersimpan tetap ada, periksa lalu simpan lagi.');
        await loadBlocks(true);
      } else {
        banner(err.message, 'error');
        updateSave();
      }
    }
  }

  // ── preview, render, IDE ───────────────────────────────────────────────────────
  function selectTab(which) {
    for (const name of ['preview', 'render']) {
      $(`tab-${name}`).setAttribute('aria-selected', String(name === which));
      $(`panel-${name}`).hidden = name !== which;
    }
  }

  async function refreshPreview() {
    const frame = $('preview');
    const page = /\.html?$/i.test(S.path) ? S.path : 'index.html';
    try {
      const data = await call('code/preview', { method: 'POST', body: JSON.stringify({ project: S.project, path: page }) });
      frame.src = data.url;
      frame.hidden = false;
      $('preview-empty').hidden = true;
    } catch (err) {
      frame.hidden = true;
      $('preview-empty').hidden = false;
      $('preview-empty').textContent = `Pratinjau tidak tersedia: ${err.message}`;
    }
  }

  function showRender(url) {
    const video = $('render-video');
    video.hidden = !url;
    $('render-empty').hidden = Boolean(url);
    if (url && video.getAttribute('src') !== url) video.src = url;
  }

  async function startRender() {
    if (S.pending.size && !confirm('Ada perubahan yang belum disimpan. Render memakai versi yang tersimpan. Lanjut?')) return;
    const out = $('render-state');
    selectTab('render');
    $('render').disabled = true;
    out.dataset.kind = '';
    out.textContent = 'Render dimulai. Hanya satu render berjalan di mesin ini; kalau ada yang lain, ini menunggu giliran.';
    try {
      const job = await call('code/render', { method: 'POST', body: JSON.stringify({ project: S.project }) });
      watch(job.id);
    } catch (err) {
      out.dataset.kind = 'error';
      out.textContent = err.message;
      $('render').disabled = false;
    }
  }

  function watch(id) {
    clearTimeout(S.poll);
    const tick = async () => {
      const out = $('render-state');
      let job;
      try { job = await call(`render/${encodeURIComponent(id)}`); } catch (err) {
        out.dataset.kind = 'error'; out.textContent = `Status render tidak terbaca: ${err.message}`; $('render').disabled = false; return;
      }
      if (job.state === 'done') {
        out.dataset.kind = ''; out.textContent = `Selesai dalam ${job.seconds} detik: ${job.file}`;
        showRender(job.url);
        $('render').disabled = false;
        return;
      }
      if (job.state === 'error' || job.state === 'cancelled') {
        out.dataset.kind = 'error'; out.textContent = `Render gagal: ${job.error || job.state}`; $('render').disabled = false; return;
      }
      out.textContent = `${job.message || 'Merender'} · ${Math.round(job.seconds)} detik`;
      S.poll = setTimeout(tick, 1500);
    };
    tick();
  }

  function openIde() {
    const path = S.project && S.project !== '.' ? `${S.project}/${S.path}` : S.path;
    if (embedded) window.parent.postMessage({ type: 'sq-open-ide', session: S.session, path }, location.origin);
    else window.open(`/ide?${new URLSearchParams({ embed: '1', session: S.session, path })}`, '_blank', 'noopener');
  }

  function showEmpty(text) {
    $('list').replaceChildren(el('p', { class: 'ce-empty', text }));
    for (const id of ['save', 'render', 'open-ide']) $(id).disabled = true;
    $('preview-empty').textContent = text;
  }

  // ── wiring ─────────────────────────────────────────────────────────────────────
  $('to-studio').href = `/studio?embed=1&session=${encodeURIComponent(S.session)}`;
  $('project-select').addEventListener('change', e => {
    if (S.pending.size && !confirm('Perubahan yang belum disimpan akan hilang. Pindah proyek?')) { e.target.value = S.project; return; }
    S.project = e.target.value; S.path = ''; S.pending.clear(); loadFiles();
  });
  $('file-select').addEventListener('change', e => {
    if (S.pending.size && !confirm('Perubahan yang belum disimpan akan hilang. Pindah file?')) { e.target.value = S.path; return; }
    S.path = e.target.value; S.pending.clear(); remember(); loadBlocks();
  });
  $('filter').addEventListener('input', e => { S.filter = e.target.value.trim(); render(); });
  $('save').addEventListener('click', save);
  $('render').addEventListener('click', startRender);
  $('open-ide').addEventListener('click', openIde);
  $('refresh-preview').addEventListener('click', refreshPreview);
  $('tab-preview').addEventListener('click', () => selectTab('preview'));
  $('tab-render').addEventListener('click', () => selectTab('render'));
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); }
  });
  document.addEventListener('visibilitychange', () => {
    // Back from the chat or the IDE: show what the AI changed, unless the person is mid-edit.
    if (document.visibilityState === 'visible' && S.path && !S.pending.size && !S.busy) loadBlocks();
  });
  window.addEventListener('beforeunload', e => { if (S.pending.size) { e.preventDefault(); e.returnValue = ''; } });
  loadProjects();
})();
