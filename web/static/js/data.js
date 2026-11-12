/* The Data tab: every data source in one place, and a grid to read and edit it.

   Sources are files on this computer (xlsx, csv, json, sqlite) and tables in the cloud (Google Sheets, PostgreSQL,
   MySQL, MongoDB). All of them look the same here and to the agents. The Google key and the database URLs are
   typed in the Kredensial tab, never in .env.

   Editing is done with the right mouse button, not a double click: right-click a cell to edit it, a row number to
   insert, copy or delete a row, a column title to sort, rename or delete the column, a source to rename or remove
   it. The same menus open from the keyboard (Menu key or Shift+F10; Enter or F2 edits the selected cell) and from a
   long press on a touch screen. Changes need the router PIN from outside this computer, like saving a credential. */
(() => {
  'use strict';

  const { $, esc, api, toast } = window.SQ;
  const PAGE = 50;
  const LAST_KEY = 'data:last';
  const KIND = { file: 'Di komputer ini', sheets: 'Google Sheets', postgres: 'PostgreSQL', mysql: 'MySQL', mongodb: 'MongoDB' };
  const BADGE = { sheets: 'Sheets', postgres: 'PG', mysql: 'SQL', mongodb: 'Mongo' };
  const D = { sources: [], info: {}, name: '', tables: [], table: '', page: null, offset: 0, sort: '', desc: false, search: '', sel: null, editing: null, loading: 0 };

  const enc = encodeURIComponent;
  const base = () => `/api/data/sources/${enc(D.name)}`;
  const current = () => D.sources.find(s => s.name === D.name) || null;
  const show = value => (value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value));
  const guarded = (url, method, body) => (window.SQTeam && window.SQTeam.pinApi
    ? window.SQTeam.pinApi(url, method, body)
    : api(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined }));
  const remember = name => { try { localStorage.setItem(LAST_KEY, name); } catch { /* private window */ } };
  const remembered = () => { try { return localStorage.getItem(LAST_KEY) || ''; } catch { return ''; } };
  const goCreds = () => { if (window.SQTeam && window.SQTeam.showCreds) window.SQTeam.showCreds(); };

  /* ── A small form in a dialog ────────────────────── */
  function dialogForm(options) {
    return new Promise(resolve => {
      const dlg = document.createElement('dialog');
      dlg.className = 'data-dialog';
      const fieldHtml = (options.fields || []).map(f => {
        const id = `dlg-${f.name}`;
        const hint = f.hint ? `<small class="hint">${esc(f.hint)}</small>` : '';
        let control;
        if (f.type === 'select') {
          control = `<select id="${id}" name="${esc(f.name)}">${f.options.map(o => `<option value="${esc(o.value)}" ${o.value === f.value ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`;
        } else if (f.type === 'textarea') {
          control = `<textarea id="${id}" name="${esc(f.name)}" rows="${f.rows || 3}" placeholder="${esc(f.placeholder || '')}" spellcheck="false">${esc(f.value || '')}</textarea>`;
        } else if (f.type === 'file') {
          control = `<input id="${id}" name="${esc(f.name)}" type="file" accept="${esc(f.accept || '')}">`;
        } else {
          control = `<input id="${id}" name="${esc(f.name)}" type="text" value="${esc(f.value ?? '')}" placeholder="${esc(f.placeholder || '')}" autocomplete="off" spellcheck="false" maxlength="${f.max || 300}">`;
        }
        return `<label class="field" for="${id}"><span>${esc(f.label)}</span>${control}${hint}</label>`;
      }).join('');
      dlg.innerHTML = `<form method="dialog">
        <h3>${esc(options.title)}</h3>
        ${options.note ? `<p class="dlg-note">${esc(options.note)}</p>` : ''}
        ${options.html || ''}${fieldHtml}
        <p class="dlg-error" role="alert" hidden></p>
        <div class="dlg-actions"><button type="button" class="btn btn-ghost" data-cancel>Batal</button>
          <button type="submit" class="btn ${options.danger ? 'btn-danger' : 'btn-primary'}">${esc(options.submit || 'Simpan')}</button></div></form>`;
      document.body.appendChild(dlg);
      const form = dlg.querySelector('form');
      const error = dlg.querySelector('.dlg-error');
      const finish = ok => { try { dlg.close(); } catch { /* already closed */ } dlg.remove(); resolve(ok); };
      dlg.addEventListener('cancel', e => { e.preventDefault(); finish(false); });
      dlg.querySelector('[data-cancel]').onclick = () => finish(false);
      dlg.querySelectorAll('[data-go-creds]').forEach(b => { b.onclick = () => { finish(false); goCreds(); }; });
      dlg.querySelectorAll('[data-copy]').forEach(b => { b.onclick = () => copyText(b.dataset.copy); });
      form.onsubmit = async e => {
        e.preventDefault();
        const values = {};
        for (const f of options.fields || []) {
          const el = form.elements[f.name];
          values[f.name] = f.type === 'file' ? (el.files[0] || null) : el.value.trim();
          if (f.required && !values[f.name]) {
            error.textContent = `${f.label} belum diisi.`;
            error.hidden = false;
            el.focus();
            return;
          }
        }
        const submit = form.querySelector('[type="submit"]');
        submit.disabled = true;
        error.hidden = true;
        try { await options.onSubmit(values); finish(true); } catch (err) {
          error.textContent = err.message || 'Gagal.';
          error.hidden = false;
          submit.disabled = false;
        }
      };
      dlg.showModal();
      const first = form.querySelector('input:not([type="hidden"]), textarea, select');
      if (first) first.focus();
    });
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast('Disalin.'); return; } catch { /* fall back below */ }
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); toast('Disalin.'); } catch { toast('Tidak bisa menyalin.'); }
    area.remove();
  }

  const fileToBase64 = file => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('File tidak bisa dibaca.'));
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result);
      let binary = '';
      for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      resolve(btoa(binary));
    };
    reader.readAsArrayBuffer(file);
  });

  /* ── Sources ─────────────────────────────────────── */
  async function loadSources(keep = true) {
    const data = await api('/api/data/sources');
    D.sources = data.sources;
    D.info = data;
    if (!D.sources.some(s => s.name === D.name)) D.name = '';
    if (!D.name && D.sources.length) {
      const wanted = keep ? remembered() : '';
      D.name = (D.sources.find(s => s.name === wanted) || D.sources[0]).name;
    }
    renderSources();
  }

  function renderSources() {
    const box = $('data-sources');
    if (!D.sources.length) { box.innerHTML = '<p class="hint">Belum ada sumber data.</p>'; return; }
    const groups = [['Di komputer ini', s => !s.online], ['Online (cloud)', s => s.online]];
    box.innerHTML = groups.map(([title, test]) => {
      const list = D.sources.filter(test);
      if (!list.length) return '';
      return `<div class="data-group">${title}</div>${list.map(s => `
        <div class="data-src ${s.name === D.name ? 'on' : ''}" role="button" tabindex="0" data-src="${esc(s.name)}" aria-current="${s.name === D.name ? 'true' : 'false'}">
          <span class="data-src-icon" aria-hidden="true">${esc(s.kind === 'file' ? s.format : BADGE[s.kind] || s.kind)}</span>
          <span class="data-src-text"><b>${esc(s.label || s.name)}</b><small>${esc(s.name)} · ${esc(KIND[s.kind] || s.kind)}</small></span>
          <button class="data-src-more" type="button" data-more="${esc(s.name)}" aria-label="Menu ${esc(s.label || s.name)}" title="Menu (atau klik kanan)">⋯</button>
        </div>`).join('')}`;
    }).join('');
    box.querySelectorAll('.data-src').forEach(row => {
      row.onclick = e => { if (!e.target.closest('[data-more]')) pick(row.dataset.src); };
      row.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(row.dataset.src); } };
    });
    box.querySelectorAll('[data-more]').forEach(button => {
      button.onclick = e => {
        e.stopPropagation();
        const box2 = button.getBoundingClientRect();
        window.SQMenu.show(sourceMenu(D.sources.find(s => s.name === button.dataset.more)), box2.left, box2.bottom, button);
      };
    });
  }

  function sourceMenu(source) {
    if (!source) return [];
    return [
      { label: 'Buka', run: () => pick(source.name) },
      { label: 'Ganti nama atau label…', run: () => renameSource(source) },
      { label: 'Cek koneksi', run: () => checkSource(source) },
      source.kind === 'file' ? { label: 'Unduh file', run: () => download(source) } : null,
      { separator: true },
      { label: 'Hapus dari daftar', danger: true, run: () => removeSource(source, false) },
      source.kind === 'file' ? { label: 'Hapus beserta filenya', danger: true, run: () => removeSource(source, true) } : null,
    ];
  }

  function download(source) {
    const link = document.createElement('a');
    link.href = `/api/data/sources/${enc(source.name)}/download`;
    link.download = '';
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  async function checkSource(source) {
    try { toast((await api(`/api/data/sources/${enc(source.name)}/check`, { method: 'POST' })).message); } catch (e) { toast(e.message); }
  }

  function renameSource(source) {
    dialogForm({
      title: 'Ganti nama sumber',
      note: 'Nama dipakai agent dan alur untuk memanggil sumber ini (huruf kecil, angka, - dan _). Label hanya tampilan.',
      fields: [
        { name: 'name', label: 'Nama', value: source.name, required: true, max: 40 },
        { name: 'label', label: 'Label', value: source.label, max: 80 },
      ],
      onSubmit: async values => {
        const saved = await guarded(`/api/data/sources/${enc(source.name)}`, 'PATCH', { name: values.name, label: values.label });
        if (D.name === source.name) D.name = saved.name;
        await loadSources();
        if (window.SQTeam && window.SQTeam.sourcesChanged) window.SQTeam.sourcesChanged();
      },
    });
  }

  async function removeSource(source, withFile) {
    const warning = withFile
      ? `Hapus "${source.label || source.name}" dari daftar DAN hapus filenya dari komputer ini? Ini tidak bisa dibatalkan.`
      : `Hapus "${source.label || source.name}" dari daftar? ${source.kind === 'file' ? 'Filenya tetap ada di komputer ini.' : 'Data online-nya tidak disentuh.'}`;
    if (!confirm(warning)) return;
    try {
      await guarded(`/api/data/sources/${enc(source.name)}${withFile ? '?delete_file=true' : ''}`, 'DELETE');
      if (D.name === source.name) { D.name = ''; D.page = null; D.tables = []; }
      await loadSources();
      await openCurrent();
      if (window.SQTeam && window.SQTeam.sourcesChanged) window.SQTeam.sourcesChanged();
    } catch (e) { toast(e.message); }
  }

  /* The "+ Source" menu and its small forms. */
  function addMenu() {
    return [
      { heading: 'Di komputer ini' },
      { label: 'File baru…', hint: 'xlsx, csv, json, sqlite', run: newFileDialog },
      { label: 'Impor file…', hint: 'dari komputer ini', run: importDialog },
      { label: 'Contoh toko', hint: 'katalog dan pesanan', run: shopStarter },
      { heading: 'Online (cloud)' },
      { label: 'Google Sheets…', run: sheetsDialog },
      { label: 'PostgreSQL, MySQL, MongoDB…', run: onlineDialog },
    ];
  }

  const addedHook = () => { if (window.SQTeam && window.SQTeam.sourcesChanged) window.SQTeam.sourcesChanged(); };

  async function afterAdd(source) {
    await loadSources(false);
    await pick(source.name);
    addedHook();
  }

  function newFileDialog() {
    dialogForm({
      title: 'File baru',
      note: 'Dibuat di folder data aplikasi ini. Kolomnya bisa ditambah atau diganti nama nanti dengan klik kanan judul kolom.',
      fields: [
        { name: 'filename', label: 'Nama file', placeholder: 'pelanggan.xlsx', required: true, max: 120, hint: 'Berakhiran .xlsx, .csv, .json, atau .sqlite. Tanpa akhiran dibuat .xlsx.' },
        { name: 'columns', label: 'Kolom', type: 'textarea', rows: 2, placeholder: 'nama, hp, alamat', hint: 'Pisahkan dengan koma.' },
        { name: 'label', label: 'Label (boleh kosong)', max: 80 },
      ],
      submit: 'Buat',
      onSubmit: async values => {
        const filename = /\.[A-Za-z0-9]{2,7}$/.test(values.filename) ? values.filename : `${values.filename}.xlsx`;
        const columns = values.columns.split(',').map(c => c.trim()).filter(Boolean);
        await afterAdd(await guarded('/api/data/sources/new-file', 'POST', { filename, columns, label: values.label }));
      },
    });
  }

  function importDialog() {
    dialogForm({
      title: 'Impor file',
      note: 'File disalin ke folder data aplikasi ini (maksimal 25 MB). File aslinya tidak diubah.',
      fields: [
        { name: 'file', label: 'File', type: 'file', required: true, accept: (D.info.formats || []).join(',') },
        { name: 'label', label: 'Label (boleh kosong)', max: 80 },
      ],
      submit: 'Impor',
      onSubmit: async values => {
        if (values.file.size > 25 * 1024 * 1024) throw new Error('File lebih dari 25 MB.');
        const content = await fileToBase64(values.file);
        await afterAdd(await guarded('/api/data/sources/import', 'POST', { filename: values.file.name, content, label: values.label }));
      },
    });
  }

  async function shopStarter() {
    try {
      const made = await guarded('/api/data/starter/shop', 'POST');
      await loadSources(false);
      if (made.created.length) await pick(made.created[0].name); else toast('Katalog dan pesanan sudah ada.');
      addedHook();
    } catch (e) { toast(e.message); }
  }

  function sheetsDialog() {
    const email = D.info.google_email;
    dialogForm({
      title: 'Google Sheets',
      note: 'Aplikasi masuk ke Google memakai akun layanan yang kuncinya diisi di tab Kredensial. Spreadsheet harus dibagikan ke akun itu sebagai Editor.',
      html: email
        ? `<div class="dlg-copy"><span>${esc(email)}</span><button type="button" class="btn btn-ghost btn-small" data-copy="${esc(email)}">Salin</button></div>`
        : '<p class="dlg-error">Kunci Google belum diisi. <button type="button" class="link-btn" data-go-creds>Isi di Kredensial</button></p>',
      fields: [
        { name: 'location', label: 'Link atau ID spreadsheet', placeholder: 'https://docs.google.com/spreadsheets/d/…', required: true, max: 300 },
        { name: 'name', label: 'Nama sumber (boleh kosong)', max: 60, hint: 'Nama pendek yang dipakai agent, misalnya stok.' },
      ],
      submit: 'Sambungkan',
      onSubmit: async values => afterAdd(await guarded('/api/data/sources', 'POST', { kind: 'sheets', location: values.location, name: values.name, label: values.name })),
    });
  }

  function onlineDialog() {
    const ready = D.info.ready || {};
    const options = ['postgres', 'mysql', 'mongodb'].map(kind => ({ value: kind, label: `${KIND[kind]}${ready[kind] ? '' : ' (URL belum diisi)'}` }));
    dialogForm({
      title: 'Database online',
      note: 'Alamat database (URL) diisi sekali di tab Kredensial, bagian Database. Di sini hanya memilih dan memberi nama.',
      html: '<p class="dlg-note"><button type="button" class="link-btn" data-go-creds>Buka Kredensial</button></p>',
      fields: [
        { name: 'kind', label: 'Jenis', type: 'select', options, value: options.find(o => ready[o.value])?.value || 'postgres' },
        { name: 'name', label: 'Nama sumber (boleh kosong)', max: 60, hint: 'Misalnya gudang.' },
      ],
      submit: 'Sambungkan',
      onSubmit: async values => afterAdd(await guarded('/api/data/sources', 'POST', { kind: values.kind, name: values.name, label: values.name })),
    });
  }

  /* ── Choosing a source and table ─────────────────── */
  async function pick(name) {
    if (D.editing) return;
    D.name = name;
    D.table = '';
    D.offset = 0;
    D.sort = '';
    D.desc = false;
    D.search = '';
    D.sel = null;
    remember(name);
    renderSources();
    await openCurrent();
  }

  async function openCurrent() {
    renderBar();
    const source = current();
    if (!source) { D.page = null; D.tables = []; renderGrid(); return; }
    try {
      const data = await api(`${base()}/tables`);
      D.tables = data.tables;
      D.positional = data.positional;
      if (!D.tables.some(t => t.name === D.table)) D.table = (D.tables[0] || {}).name || '';
    } catch (e) {
      D.tables = [];
      D.page = null;
      renderBar();
      renderProblem(e.message);
      return;
    }
    renderBar();
    await loadRows();
  }

  async function loadRows() {
    if (!current()) return;
    const ticket = ++D.loading;
    const params = new URLSearchParams({ table: D.table, offset: String(D.offset), limit: String(PAGE), sort: D.sort, desc: String(D.desc), search: D.search });
    try {
      const page = await api(`${base()}/rows?${params}`);
      if (ticket !== D.loading) return;
      D.page = page;
      D.positional = page.positional;
      if (D.sel && (D.sel.row >= page.rows.length || D.sel.col >= page.columns.length)) D.sel = null;
      updateNote();      // the bar itself is not redrawn, or the search box would lose focus while typing
      renderGrid();
    } catch (e) {
      if (ticket === D.loading) { D.page = null; renderProblem(e.message); }
    }
  }

  /* ── Drawing ─────────────────────────────────────── */
  function renderProblem(message) {
    $('data-grid').innerHTML = `<div class="data-empty"><p>${esc(message)}</p><div class="row">
      <button class="btn btn-ghost" type="button" data-retry>Coba lagi</button>
      <button class="btn btn-ghost" type="button" data-creds>Buka Kredensial</button></div></div>`;
    $('data-grid').querySelector('[data-retry]').onclick = openCurrent;
    $('data-grid').querySelector('[data-creds]').onclick = goCreds;
    $('data-foot').innerHTML = '';
  }

  function renderBar() {
    const source = current();
    if (!source) { $('data-bar').innerHTML = ''; $('data-note').hidden = true; return; }
    const tables = D.tables.length > 1
      ? `<div class="data-tables" role="tablist" aria-label="Tabel">${D.tables.map(t => `<button class="data-tab ${t.name === D.table ? 'on' : ''}" type="button" role="tab" aria-selected="${t.name === D.table}" data-table="${esc(t.name)}">${esc(t.name)}</button>`).join('')}</div>` : '';
    $('data-bar').innerHTML = `<h3>${esc(source.label || source.name)}</h3>${tables}
      <input id="data-search" class="data-search" type="search" placeholder="Cari di tabel ini…" aria-label="Cari" value="${esc(D.search)}">
      <span class="data-spacer"></span>
      <button id="data-new-row" class="btn btn-ghost btn-small" type="button">+ Baris</button>
      <button id="data-reload" class="btn btn-ghost btn-small" type="button" title="Muat ulang">↻</button>`;
    $('data-bar').querySelectorAll('[data-table]').forEach(b => { b.onclick = () => { D.table = b.dataset.table; D.offset = 0; D.sort = ''; D.sel = null; renderBar(); loadRows(); }; });
    if ($('data-bar').querySelector('.data-tables')) {
      window.SQMenu.on($('data-bar').querySelector('.data-tables'), ({ target }) => {
        const tab = target.closest && target.closest('[data-table]');
        return tab ? tableMenu(tab.dataset.table) : null;
      });
    }
    let timer = 0;
    $('data-search').oninput = e => { clearTimeout(timer); timer = setTimeout(() => { D.search = e.target.value.trim(); D.offset = 0; loadRows(); }, 260); };
    $('data-new-row').onclick = () => addRowDialog(null);
    $('data-reload').onclick = openCurrent;
    updateNote();
  }

  function updateNote() {
    const note = $('data-note');
    if (D.page && !D.page.editable) { note.hidden = false; note.textContent = 'Tabel ini tidak punya kunci utama, jadi hanya bisa dibaca di sini.'; } else note.hidden = true;
  }

  function renderGrid() {
    const page = D.page;
    const box = $('data-grid');
    if (!current()) {
      box.innerHTML = `<div class="data-empty"><h3>Belum ada sumber data</h3>
        <p>Tempat agent membaca katalog, mencatat pesanan, dan menyimpan hasil. Mulai dari contoh toko, atau tambahkan file atau Google Sheets milikmu.</p>
        <div class="row"><button class="btn btn-primary" type="button" data-starter>Buat contoh toko</button><button class="btn btn-ghost" type="button" data-add>+ Sumber</button></div></div>`;
      box.querySelector('[data-starter]').onclick = shopStarter;
      box.querySelector('[data-add]').onclick = e => { const r = e.currentTarget.getBoundingClientRect(); window.SQMenu.show(addMenu(), r.left, r.bottom, e.currentTarget); };
      $('data-foot').innerHTML = '';
      return;
    }
    if (!page) { box.innerHTML = '<div class="data-empty"><p>Memuat…</p></div>'; return; }
    const head = page.columns.map((col, c) => `<th data-col="${esc(col)}" data-c="${c}" scope="col" title="Klik untuk mengurutkan. Klik kanan untuk menu kolom.">${esc(col)}${D.sort === col ? `<span class="sort-mark" aria-hidden="true">${D.desc ? '▼' : '▲'}</span>` : ''}</th>`).join('');
    const body = page.rows.map((row, r) => `<tr data-r="${r}"><th class="rn" scope="row" data-r="${r}">${esc(page.key ? D.offset + r + 1 : row.key)}</th>${page.columns.map((col, c) => {
      const value = row.values[col];
      const kind = value === null || value === undefined || value === '' ? ' nul' : typeof value === 'number' ? ' num' : '';
      const text = show(value);
      return `<td class="${kind.trim()}" data-r="${r}" data-c="${c}" title="${esc(text.slice(0, 400))}">${esc(text)}</td>`;
    }).join('')}</tr>`).join('');
    box.innerHTML = `<table class="grid" role="grid" aria-label="${esc(current().label || D.name)}"><thead><tr><th class="rn" scope="col">#</th>${head}</tr></thead><tbody>${body}</tbody></table>`;
    if (!page.rows.length) {
      box.insertAdjacentHTML('beforeend', `<div class="data-empty"><p>${D.search ? 'Tidak ada baris yang cocok.' : 'Tabel ini masih kosong.'}</p>
        ${D.search ? '' : '<div class="row"><button class="btn btn-ghost" type="button" data-first>+ Tambah baris pertama</button></div>'}</div>`);
      const first = box.querySelector('[data-first]');
      if (first) first.onclick = () => addRowDialog(null);
    }
    paintSelection();
    const last = Math.min(D.offset + page.rows.length, page.total);
    $('data-foot').innerHTML = `<span>${page.total ? `Baris ${D.offset + 1}–${last} dari ${page.total}` : '0 baris'}${(page.notes || []).length ? ` · ${esc(page.notes.join(' '))}` : ''}</span>
      <span class="pager"><button class="btn btn-ghost btn-small" type="button" data-prev ${D.offset <= 0 ? 'disabled' : ''} aria-label="Halaman sebelumnya">‹</button>
      <button class="btn btn-ghost btn-small" type="button" data-next ${D.offset + PAGE >= page.total ? 'disabled' : ''} aria-label="Halaman berikutnya">›</button></span>`;
    $('data-foot').querySelector('[data-prev]').onclick = () => { D.offset = Math.max(0, D.offset - PAGE); D.sel = null; loadRows(); };
    $('data-foot').querySelector('[data-next]').onclick = () => { D.offset += PAGE; D.sel = null; loadRows(); };
  }

  const cellEl = (r, c) => $('data-grid').querySelector(`td[data-r="${r}"][data-c="${c}"]`);

  function paintSelection() {
    $('data-grid').querySelectorAll('.sel').forEach(el => el.classList.remove('sel'));
    if (!D.sel) return;
    const el = cellEl(D.sel.row, D.sel.col);
    if (el) el.classList.add('sel');
  }

  function select(row, col, scroll = false) {
    D.sel = { row, col };
    paintSelection();
    const el = cellEl(row, col);
    if (scroll && el) el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  /* ── Changing data ───────────────────────────────── */
  const editable = () => Boolean(D.page && D.page.editable);
  const readOnlyReason = 'Tabel ini tidak punya kunci utama, jadi hanya bisa dibaca';
  const lock = () => (editable() ? false : readOnlyReason);

  async function change(url, method, body, after) {
    try {
      await guarded(url, method, body);
      if (after) after();
      await loadRows();
    } catch (e) { toast(e.message); await loadRows(); }
  }

  function writeCell(r, c, value) {
    const row = D.page.rows[r];
    const col = D.page.columns[c];
    return change(`${base()}/rows`, 'PATCH', { table: D.table, key: row.key, values: { [col]: value }, expect: D.positional ? row.values : null });
  }

  function editCell(r, c) {
    if (D.editing) return;
    if (!editable()) { toast(readOnlyReason); return; }
    const td = cellEl(r, c);
    if (!td) return;
    const row = D.page.rows[r];
    const col = D.page.columns[c];
    const text = show(row.values[col]);
    const multi = text.includes('\n') || text.length > 70;
    const input = document.createElement(multi ? 'textarea' : 'input');
    input.className = 'cell-edit';
    input.value = text;
    input.setAttribute('aria-label', `Isi ${col}, baris ${r + 1 + D.offset}`);
    if (multi) input.rows = Math.min(6, text.split('\n').length + 1);
    td.classList.add('editing');
    td.textContent = '';
    td.appendChild(input);
    D.editing = { r, c };
    input.focus();
    input.select();
    let done = false;
    const finish = async save => {
      if (done) return;
      done = true;
      D.editing = null;
      if (save && input.value !== text) await writeCell(r, c, input.value); else renderGrid();
      $('data-grid-wrap').focus({ preventScroll: true });
    };
    input.onkeydown = e => {
      e.stopPropagation();
      if (e.key === 'Escape') { e.preventDefault(); finish(false); }
      else if (e.key === 'Enter' && !(multi && e.shiftKey)) { e.preventDefault(); finish(true); }
    };
    input.onblur = () => finish(true);
  }

  function addRowDialog(at, template = {}) {
    if (!editable()) { toast(readOnlyReason); return; }
    const skip = D.page.key || '';
    const columns = D.page.columns.filter(c => c !== skip);
    dialogForm({
      title: at ? 'Sisipkan baris' : 'Tambah baris',
      note: skip ? `Kolom ${skip} diisi otomatis.` : '',
      fields: columns.map((col, i) => ({ name: `c${i}`, label: col, value: show(template[col]), max: 20000 })),
      submit: 'Tambah',
      onSubmit: async values => {
        const filled = {};
        columns.forEach((col, i) => { if (values[`c${i}`] !== '') filled[col] = values[`c${i}`]; });
        await guarded(`${base()}/rows`, 'POST', { table: D.table, values: filled, at: at || null });
        await loadRows();
        const rows = D.page ? D.page.rows.length : 0;
        if (rows) select(at && D.positional ? Math.max(0, Math.min(rows - 1, at - 1 - D.offset)) : Math.max(0, rows - 1), 0, true);
      },
    });
  }

  function rowNumber(r) {
    const key = D.page.rows[r].key;
    return D.positional ? Number(key) : null;
  }

  function duplicateRow(r) {
    const row = D.page.rows[r];
    const copy = {};
    D.page.columns.forEach(col => { if (col !== D.page.key) copy[col] = row.values[col]; });
    const at = D.positional ? rowNumber(r) + 1 : null;
    addRowDialog(at, copy);
  }

  function deleteRow(r) {
    const row = D.page.rows[r];
    const label = D.positional ? `baris ${row.key}` : `baris dengan ${D.page.key || 'kunci'} ${row.key}`;
    if (!confirm(`Hapus ${label}? File xlsx dan csv dicadangkan otomatis di folder .backups.`)) return;
    change(`${base()}/rows/delete`, 'POST', { table: D.table, keys: [row.key], expect: D.positional ? [row.values] : null }, () => { D.sel = null; });
  }

  function addColumn() {
    if (!editable()) { toast(readOnlyReason); return; }
    dialogForm({
      title: 'Kolom baru',
      fields: [{ name: 'name', label: 'Nama kolom', required: true, max: 64, hint: 'Huruf, angka, dan _. Diawali huruf. Kolom berawalan _ tidak pernah terlihat oleh bot pelanggan.' }],
      submit: 'Tambah',
      onSubmit: async values => { await guarded(`${base()}/columns`, 'POST', { table: D.table, action: 'add', name: values.name }); await loadRows(); },
    });
  }

  function renameColumn(col) {
    dialogForm({
      title: `Ganti nama kolom "${col}"`,
      fields: [{ name: 'name', label: 'Nama baru', value: col, required: true, max: 64 }],
      onSubmit: async values => { await guarded(`${base()}/columns`, 'POST', { table: D.table, action: 'rename', name: col, new_name: values.name }); await loadRows(); },
    });
  }

  function dropColumn(col) {
    if (!confirm(`Hapus kolom "${col}" beserta isinya? Ini tidak bisa dibatalkan (file xlsx dan csv dicadangkan otomatis).`)) return;
    change(`${base()}/columns`, 'POST', { table: D.table, action: 'drop', name: col }, () => { D.sel = null; if (D.sort === col) D.sort = ''; });
  }

  function newTable() {
    dialogForm({
      title: 'Tabel baru',
      fields: [
        { name: 'table', label: 'Nama tabel', required: true, max: 64 },
        { name: 'columns', label: 'Kolom', type: 'textarea', rows: 2, placeholder: 'nama, harga, stok', hint: 'Pisahkan dengan koma.' },
      ],
      submit: 'Buat',
      onSubmit: async values => {
        await guarded(`${base()}/tables`, 'POST', { table: values.table, columns: values.columns.split(',').map(c => c.trim()).filter(Boolean) });
        D.table = values.table;
        await openCurrent();
      },
    });
  }

  function dropTable(name) {
    if (!confirm(`Hapus tabel "${name}" beserta semua isinya? Ini tidak bisa dibatalkan.`)) return;
    guarded(`${base()}/tables/drop`, 'POST', { table: name }).then(() => { D.table = ''; return openCurrent(); }).catch(e => toast(e.message));
  }

  function sortBy(col, desc) { D.sort = col; D.desc = desc; D.offset = 0; D.sel = null; loadRows(); }
  function clearSort() { D.sort = ''; D.desc = false; loadRows(); }
  function cycleSort(col) { if (D.sort !== col) sortBy(col, false); else if (!D.desc) sortBy(col, true); else clearSort(); }

  /* ── Right-click menus ───────────────────────────── */
  function cellMenu(r, c) {
    const col = D.page.columns[c];
    const value = show(D.page.rows[r].values[col]);
    const no = lock();
    return [
      { label: 'Edit sel…', hint: 'Enter', disabled: no, run: () => editCell(r, c) },
      { label: 'Salin isi sel', run: () => copyText(value) },
      { label: 'Kosongkan sel', disabled: no || value === '', run: () => writeCell(r, c, '') },
      { separator: true },
      { label: 'Sisipkan baris di atas…', disabled: no, run: () => addRowDialog(D.positional ? rowNumber(r) : null) },
      { label: 'Sisipkan baris di bawah…', disabled: no, run: () => addRowDialog(D.positional ? rowNumber(r) + 1 : null) },
      { label: 'Duplikat baris…', disabled: no, run: () => duplicateRow(r) },
      { label: 'Hapus baris', danger: true, disabled: no, run: () => deleteRow(r) },
      { separator: true },
      { label: `Urutkan ${col} A → Z`, run: () => sortBy(col, false) },
      { label: `Urutkan ${col} Z → A`, run: () => sortBy(col, true) },
      { label: 'Cari baris dengan nilai ini', disabled: value === '', run: () => { $('data-search').value = value; D.search = value; D.offset = 0; loadRows(); } },
    ];
  }

  function rowMenu(r) {
    const no = lock();
    return [
      { heading: `Baris ${D.page.rows[r].key}` },
      { label: 'Sisipkan baris di atas…', disabled: no, run: () => addRowDialog(D.positional ? rowNumber(r) : null) },
      { label: 'Sisipkan baris di bawah…', disabled: no, run: () => addRowDialog(D.positional ? rowNumber(r) + 1 : null) },
      { label: 'Duplikat baris…', disabled: no, run: () => duplicateRow(r) },
      { separator: true },
      { label: 'Hapus baris', danger: true, disabled: no, run: () => deleteRow(r) },
    ];
  }

  function headMenu(col) {
    const no = lock();
    return [
      { heading: `Kolom ${col}` },
      { label: 'Urutkan A → Z', run: () => sortBy(col, false) },
      { label: 'Urutkan Z → A', run: () => sortBy(col, true) },
      { label: 'Hapus urutan', disabled: D.sort !== col, run: clearSort },
      { separator: true },
      { label: 'Ganti nama kolom…', disabled: no, run: () => renameColumn(col) },
      { label: 'Tambah kolom…', disabled: no, run: addColumn },
      { label: 'Hapus kolom', danger: true, disabled: no, run: () => dropColumn(col) },
    ];
  }

  function blankMenu() {
    const no = lock();
    return [
      { label: 'Tambah baris…', disabled: no, run: () => addRowDialog(null) },
      { label: 'Tambah kolom…', disabled: no, run: addColumn },
      { label: 'Tabel baru…', run: newTable },
      { separator: true },
      { label: 'Muat ulang', run: openCurrent },
    ];
  }

  function tableMenu(name) {
    return [
      { label: 'Buka', run: () => { D.table = name; D.offset = 0; D.sort = ''; renderBar(); loadRows(); } },
      { label: 'Tabel baru…', run: newTable },
      { separator: true },
      { label: 'Hapus tabel', danger: true, run: () => dropTable(name) },
    ];
  }

  /* ── Wiring ──────────────────────────────────────── */
  const wrap = $('data-grid-wrap');
  window.SQMenu.on(wrap, ({ target, keyboard }) => {
    if (!current() || !D.page) return null;
    if (keyboard && D.sel && D.page.rows[D.sel.row]) return cellMenu(D.sel.row, D.sel.col);
    const cell = target.closest && target.closest('td[data-c]');
    if (cell && D.page.rows[Number(cell.dataset.r)]) {
      select(Number(cell.dataset.r), Number(cell.dataset.c));
      return cellMenu(Number(cell.dataset.r), Number(cell.dataset.c));
    }
    const head = target.closest && target.closest('th[data-col]');
    if (head) return headMenu(head.dataset.col);
    const number = target.closest && target.closest('th.rn[data-r]');
    if (number && D.page.rows[Number(number.dataset.r)]) return rowMenu(Number(number.dataset.r));
    return blankMenu();
  });

  wrap.addEventListener('click', e => {
    const td = e.target.closest && e.target.closest('td[data-c]');
    if (td) { if (!td.classList.contains('editing')) { select(Number(td.dataset.r), Number(td.dataset.c)); wrap.focus({ preventScroll: true }); } return; }
    const th = e.target.closest && e.target.closest('th[data-col]');
    if (th) cycleSort(th.dataset.col);
  });

  wrap.addEventListener('keydown', e => {
    if (D.editing || e.target !== wrap || !D.page || !D.page.rows.length) return;
    const rows = D.page.rows.length;
    const cols = D.page.columns.length;
    const at = D.sel || { row: 0, col: 0 };
    const move = (row, col) => { e.preventDefault(); select(Math.max(0, Math.min(rows - 1, row)), Math.max(0, Math.min(cols - 1, col)), true); };
    if (e.key === 'ArrowDown') move(at.row + 1, at.col);
    else if (e.key === 'ArrowUp') move(at.row - 1, at.col);
    else if (e.key === 'ArrowRight') move(at.row, at.col + 1);
    else if (e.key === 'ArrowLeft') move(at.row, at.col - 1);
    else if (e.key === 'Home') move(at.row, 0);
    else if (e.key === 'End') move(at.row, cols - 1);
    else if (e.key === 'PageDown') move(at.row + 10, at.col);
    else if (e.key === 'PageUp') move(at.row - 10, at.col);
    else if ((e.key === 'Enter' || e.key === 'F2') && D.sel) { e.preventDefault(); editCell(D.sel.row, D.sel.col); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c' && D.sel) {
      e.preventDefault();
      copyText(show(D.page.rows[D.sel.row].values[D.page.columns[D.sel.col]]));
    }
  });

  window.SQMenu.on($('data-sources'), ({ target }) => {
    const row = target.closest && target.closest('[data-src]');
    if (row) return sourceMenu(D.sources.find(s => s.name === row.dataset.src));
    return addMenu();
  });

  $('data-add').onclick = e => {
    const box = e.currentTarget.getBoundingClientRect();
    window.SQMenu.show(addMenu(), box.left, box.bottom, e.currentTarget);
  };

  window.SQData = {
    async open() {
      try {
        await loadSources();
        await openCurrent();
      } catch (e) { $('data-grid').innerHTML = `<div class="data-empty"><p>${esc(e.message)}</p></div>`; }
    },
    /* Tell the editor which sources exist, for the "Data" part of an agent node. */
    sources: () => D.sources,
  };
})();
