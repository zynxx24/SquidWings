/* Router page: providers, accounts and their live state, and token usage by period. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  try {
    const t = localStorage.getItem('theme');
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  } catch { /* private window */ }

  const S = { period: '24h', group: 'provider', canEdit: false, hasPin: false, local: false, providers: [], usage: null };
  const PIN_KEY = 'router-pin';
  const pin = () => { try { return sessionStorage.getItem(PIN_KEY) || ''; } catch { return ''; } };
  const PERIOD_TITLE = { '1h': 'Token per 5 menit', '24h': 'Token per jam', '7d': 'Token per 6 jam', '30d': 'Token per hari', all: 'Token per hari' };
  const STRATEGY = { smart: 'Smart round robin', round_robin: 'Round robin', fill_first: 'Isi akun pertama dulu' };
  const KIND = { openai: 'OpenAI-compatible', anthropic: 'Claude-compatible' };

  // Every value from the server goes in as text, never as HTML: names and model ids are user input.
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'class') el.className = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
    return el;
  }
  const fmt = n => {
    n = Number(n || 0);
    if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e4) return (n / 1e3).toFixed(1) + 'k';
    return n.toLocaleString('id-ID');
  };
  const full = n => Number(n || 0).toLocaleString('id-ID');
  // Dollars: small amounts keep enough digits to be more than "$0.00".
  const usd = v => {
    v = Number(v || 0);
    if (v === 0) return '$0';
    if (v < 0.01) return '$' + v.toFixed(4);
    if (v < 100) return '$' + v.toFixed(2);
    return '$' + Math.round(v).toLocaleString('en-US');
  };
  const idr = v => S.usage && S.usage.usd_idr ? 'Rp' + Math.round(Number(v || 0) * S.usage.usd_idr).toLocaleString('id-ID') : '';
  const money = v => (idr(v) ? `${usd(v)} · ${idr(v)}` : usd(v));

  function banner(text, err = false) {
    const b = $('banner');
    b.textContent = text || '';
    b.className = 'banner' + (err ? ' err' : '');
    b.hidden = !text;
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      method: opts.method || 'GET',
      headers: { 'Content-Type': 'application/json', ...(pin() ? { 'X-Router-Pin': pin() } : {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(Array.isArray(d) ? d.map(e => e.msg).join(', ') : (d || `Error ${res.status}`));
    }
    return data;
  }
  async function act(fn, okText) {
    try {
      const r = await fn();
      if (okText) banner(okText + modelsNote(r && r.models));
      await loadProviders();
      if (r && r.models) loadModels();
      return r;
    } catch (e) { banner(e.message, true); return null; }
  }
  // What the backend found when it fetched the provider's models right after the change.
  function modelsNote(m) {
    if (!m) return '';
    return m.ok ? ` ${m.count} model diambil${m.sample && m.sample.length ? ': ' + m.sample.slice(0, 6).join(', ') + (m.count > 6 ? ', …' : '') : ''}.`
      : ` Model belum bisa diambil: ${m.error}`;
  }
  async function syncModels(quiet) {
    try {
      const r = await api('/api/models/refresh', { method: 'POST' });
      if (!quiet) banner(`Daftar model disinkron: ${r.total} model.`);
    } catch (e) { if (!quiet) banner(e.message, true); }
  }

  /* ── usage ─────────────────────────────────────── */
  async function loadUsage() {
    try {
      S.usage = await api(`/api/router/usage?period=${S.period}&group=${S.group}`);
      renderUsage();
    } catch (e) { banner(e.message, true); }
  }

  function renderUsage() {
    const u = S.usage;
    if (!u) return;
    const t = u.totals;
    const kpi = (label, value, note, shown) => h('div', { class: 'kpi' }, h('div', { class: 'label' }, label),
      h('div', { class: 'value', title: shown ? money(value) : full(value) }, shown ? usd(value) : fmt(value)), h('div', { class: 'note' }, note || ''));
    const unpriced = t.unpriced_models && t.unpriced_models.length
      ? ` · ${t.unpriced_models.length} model belum ada harga` : '';
    const costNote = (t.cost_usd ? `≈ ${money(t.cost_30d_usd)} per 30 hari pada laju ini` : 'belum ada token berharga') + unpriced;
    $('kpis').replaceChildren(
      kpi('Request', t.requests, `${full(t.errors)} gagal${t.unreported ? ` · ${full(t.unreported)} tanpa laporan` : ''}`),
      kpi('Input', t.input, `rata-rata ${full(t.avg_latency_ms)} ms`),
      kpi('Output', t.output, ''),
      kpi('Cached', t.cached, `${t.cache_hit_pct}% dari input${t.cache_write ? ` · tulis ${fmt(t.cache_write)}` : ''}`),
      kpi('Total', t.total, 'input + output'),
      kpi('Biaya (perkiraan)', t.cost_usd, costNote, true),
    );
    $('chart-title').textContent = PERIOD_TITLE[u.period] || 'Token';
    renderChart(u.series, u.bucket_s);
    renderTable(u.rows, u.group);
  }

  function label(ts, bucket) {
    const d = new Date(ts * 1000);
    if (bucket < 86400) return d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' }) + (bucket >= 21600 ? ' ' + d.toLocaleDateString('id-ID', { day: 'numeric', month: 'short' }) : '');
    return d.toLocaleDateString('id-ID', { day: 'numeric', month: 'short' });
  }

  function renderChart(series, bucket) {
    const box = $('chart');
    const W = Math.max(box.clientWidth, 280), H = box.clientHeight || 220;
    const pad = { l: 46, r: 8, t: 8, b: 24 };
    const svgNS = 'http://www.w3.org/2000/svg';
    const s = (tag, attrs) => { const el = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); return el; };
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Grafik token per periode. Angka lengkap ada di tabel rincian.' });
    const max = Math.max(0, ...series.map(p => (p.input || 0) + (p.output || 0)));
    if (!series.length || max === 0) {
      const t = s('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', class: 'empty' });
      t.textContent = 'Belum ada token di periode ini';
      svg.append(t);
      box.replaceChildren(svg);
      return;
    }
    const top = niceMax(max);
    const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    const y = v => pad.t + ih - (v / top) * ih;
    for (let i = 0; i <= 4; i++) {
      const v = (top / 4) * i;
      svg.append(s('line', { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: 'gridline' }));
      const t = s('text', { x: pad.l - 6, y: y(v) + 4, 'text-anchor': 'end', class: 'tick' });
      t.textContent = fmt(v);
      svg.append(t);
    }
    const slot = iw / series.length;
    const bw = Math.max(2, Math.min(24, slot * 0.62));
    const every = Math.ceil(series.length / Math.max(2, Math.floor(iw / 70)));
    const tip = $('tip');
    series.forEach((p, i) => {
      const x = pad.l + slot * i + (slot - bw) / 2;
      const hit = s('rect', { x: pad.l + slot * i, y: pad.t, width: slot, height: ih, class: 'hit' });
      svg.append(hit);
      const fresh = Math.max(0, (p.input || 0) - (p.cached || 0));
      const parts = [[fresh, 'var(--series-1)'], [p.cached || 0, 'var(--series-2)'], [p.output || 0, 'var(--series-3)']].filter(q => q[0] > 0);
      let base = 0;
      parts.forEach(([v, color], k) => {
        const y0 = y(base), y1 = y(base + v);
        const gap = k > 0 ? 2 : 0;                 // 2px surface gap between stacked segments
        const hgt = Math.max(0, y0 - y1 - gap);
        if (hgt > 0) {
          const isTop = k === parts.length - 1;
          svg.append(s('path', { d: barPath(x, y1, bw, hgt, isTop ? Math.min(4, hgt, bw / 2) : 0), fill: color, 'pointer-events': 'none' }));
        }
        base += v;
      });
      if (i % every === 0) {
        const t = s('text', { x: pad.l + slot * i + slot / 2, y: H - 6, 'text-anchor': 'middle', class: 'tick' });
        t.textContent = label(p.bucket, bucket);
        svg.append(t);
      }
      const show = ev => {
        hit.classList.add('on');
        tip.replaceChildren(
          h('b', null, label(p.bucket, bucket)),
          row('s1', 'Input baru', fresh), row('s2', 'Input cache', p.cached), row('s3', 'Output', p.output),
          row('', 'Total', p.total), row('', 'Request', p.requests), p.errors ? row('', 'Gagal', p.errors) : null,
          p.cost_usd ? h('div', { class: 'r' }, h('span', null, 'Biaya'), h('span', null, money(p.cost_usd))) : null,
        );
        tip.hidden = false;
        const rect = box.getBoundingClientRect();
        const px = (ev.clientX || rect.left + x) - rect.left;
        tip.style.left = Math.min(Math.max(0, px + 12), rect.width - tip.offsetWidth) + 'px';
        tip.style.top = '28px';
      };
      hit.addEventListener('mousemove', show);
      hit.addEventListener('mouseleave', () => { hit.classList.remove('on'); tip.hidden = true; });
    });
    box.replaceChildren(svg);
  }
  const row = (cls, name, v) => h('div', { class: 'r' }, h('span', null, cls ? h('i', { class: `sw ${cls}` }) : null, name), h('span', null, full(v)));
  function barPath(x, y, w, hgt, r) {
    // Rounded data end on top, square at the baseline.
    return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
  }
  function niceMax(v) {
    const p = 10 ** Math.floor(Math.log10(v));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  function renderTable(rows, group) {
    const first = { provider: 'Provider', account: 'Akun', model: 'Model' }[group];
    const head = h('tr', null, [first, 'Request', 'Gagal', 'Input', 'Cached', 'Hit %', 'Output', 'Total', 'Biaya', 'Rata-rata ms'].map(t => h('th', null, t)));
    const body = rows.length ? rows.map(r => {
      const name = group === 'model' ? r.model
        : group === 'account' ? `${r.provider} · ${r.account_label || r.account || 'utama'}`
          : (r.provider_name || r.provider);
      return h('tr', null, h('td', { class: 'name', title: name }, name), h('td', null, full(r.requests)),
        h('td', { class: r.errors ? '' : 'muted' }, full(r.errors)), h('td', null, full(r.input)), h('td', null, full(r.cached)),
        h('td', null, `${r.cache_hit_pct}%`), h('td', null, full(r.output)), h('td', null, full(r.total)),
        h('td', { class: r.unpriced_requests ? 'muted' : '', title: r.unpriced_requests ? `${full(r.unpriced_requests)} request tanpa harga, tidak dihitung` : money(r.cost_usd) },
          usd(r.cost_usd) + (r.unpriced_requests ? '*' : '')),
        h('td', null, full(r.avg_latency_ms)));
    }) : [h('tr', null, h('td', { colspan: 10, class: 'muted' }, 'Belum ada request di periode ini.'))];
    $('usage-table').replaceChildren(h('thead', null, head), h('tbody', null, body));
  }

  /* ── prices ────────────────────────────────────── */
  const SOURCE = { custom: 'kamu isi', listed: 'dari provider', bawaan: 'bawaan Claude', gratis: 'gratis', '': 'belum ada' };
  async function loadPrices() {
    try { renderPrices(await api('/api/router/prices')); } catch (e) { banner(e.message, true); }
  }
  function renderPrices(r) {
    const rate = $('usd-idr');
    if (document.activeElement !== rate) rate.value = r.usd_idr || '';
    rate.disabled = !S.canEdit;
    const cell = (model, key, value) => h('input', {
      type: 'number', min: '0', step: 'any', value: value ?? '', 'aria-label': `${key} ${model}`, class: 'price-in',
      'data-key': key, disabled: !S.canEdit, placeholder: key === 'input' || key === 'output' ? 'wajib' : 'auto',
    });
    const head = h('tr', null, ['Model', 'Token', 'Sumber', 'Input', 'Cached', 'Output', ''].map(t => h('th', null, t)));
    const body = r.models.length ? r.models.map(m => {
      const p = m.price || {};
      const tr = h('tr', null, h('td', { class: 'name', title: m.model }, m.model), h('td', null, fmt(m.tokens)),
        h('td', { class: m.source ? '' : 'muted' }, SOURCE[m.source] ?? m.source),
        h('td', null, cell(m.model, 'input', p.input)), h('td', null, cell(m.model, 'cached', m.source === 'custom' || m.source === 'listed' ? p.cached : '')),
        h('td', null, cell(m.model, 'output', p.output)));
      const save = h('button', { class: 'btn small', disabled: !S.canEdit, onclick: () => savePrice(m.model, tr) }, 'Simpan');
      const clear = m.source === 'custom' ? h('button', { class: 'btn small ghost', disabled: !S.canEdit, onclick: () => savePrice(m.model, null) }, 'Hapus') : null;
      tr.append(h('td', null, save, clear));
      return tr;
    }) : [h('tr', null, h('td', { colspan: 7, class: 'muted' }, 'Belum ada model yang memakai token.'))];
    $('price-table').replaceChildren(h('thead', null, head), h('tbody', null, body));
  }
  async function savePrice(model, tr) {
    const body = { model };
    if (tr) tr.querySelectorAll('input[data-key]').forEach(i => { body[i.dataset.key] = i.value; });
    else body.clear = true;
    try { renderPrices(await api('/api/router/prices', { method: 'POST', body })); banner(`Harga ${model} ${tr ? 'disimpan' : 'dihapus'}.`); loadUsage(); }
    catch (e) { banner(e.message, true); }
  }
  $('usd-idr').addEventListener('change', async e => {
    try { renderPrices(await api('/api/router/prices', { method: 'POST', body: { usd_idr: e.target.value } })); loadUsage(); }
    catch (err) { banner(err.message, true); }
  });

  /* ── providers ─────────────────────────────────── */
  async function loadProviders() {
    try {
      const r = await api('/api/router/providers');
      S.canEdit = r.can_edit;
      S.hasPin = r.has_pin;
      S.local = r.local;
      S.providers = r.providers;
      renderProviders();
      $('pin-bar').hidden = S.canEdit;
      $('import-env').hidden = !(r.env_pending && S.canEdit);
      $('pin-status').textContent = S.hasPin
        ? 'PIN aktif: perubahan dari luar komputer ini (tunnel) butuh PIN. Dari komputer ini tidak perlu.'
        : 'Belum ada PIN: siapa pun yang tahu URL tunnel bisa menambah atau menghapus provider. API key utuh tidak pernah ditampilkan.';
      $('pin-form').querySelectorAll('input, button').forEach(el => { el.disabled = !S.canEdit; });
    } catch (e) { banner(e.message, true); }
  }

  function renderProviders() {
    $('show-add').disabled = !S.canEdit;
    $('empty').hidden = S.providers.length > 0;
    $('providers').replaceChildren(...S.providers.map(providerCard));
  }

  function providerCard(p) {
    const edit = !S.canEdit;
    const out = h('div', { class: 'test-out' });
    const patch = body => act(() => api(`/api/router/providers/${p.id}`, { method: 'PATCH', body }), 'Tersimpan.');
    const strategy = h('select', { disabled: edit, 'aria-label': 'Rotasi', onchange: e => patch({ strategy: e.target.value }) },
      Object.entries(STRATEGY).map(([v, t]) => h('option', { value: v, selected: v === p.strategy }, t)));
    const head = h('div', { class: 'p-head' },
      h('div', null,
        h('div', { class: 'p-title' }, h('h3', null, p.name), p.default ? h('span', { class: 'badge default', title: 'Model tanpa prefix dilayani provider ini' }, 'default') : null,
          h('span', { class: 'badge' }, KIND[p.kind] || p.kind), p.source === 'env' ? h('span', { class: 'badge' }, 'dari .env') : null,
          h('span', { class: 'badge' }, `${p.ready}/${p.accounts.length} akun siap`), p.enabled ? null : h('span', { class: 'badge' }, 'mati')),
        h('div', { class: 'p-meta' }, `${p.base_url} · prefix `, h('code', null, p.prefix || '(tanpa prefix)'),
          p.models.length ? ` · +${p.models.length} model manual` : '')),
      h('div', { class: 'p-actions' }, strategy,
        h('button', { class: 'btn small', disabled: edit, onclick: () => test(p, out) }, 'Tes koneksi'),
        h('button', { class: 'btn small', disabled: edit, onclick: () => act(() => api(`/api/router/providers/${p.id}/refresh`, { method: 'POST' }), `Model ${p.name} diperbarui.`) }, 'Ambil model'),
        p.default ? null : h('button', { class: 'btn small', disabled: edit, title: 'Model tanpa prefix akan dilayani provider ini', onclick: () => patch({ default: true }) }, 'Jadikan default'),
        h('button', { class: 'btn small', disabled: edit, onclick: () => act(() => api(`/api/router/providers/${p.id}/reset`, { method: 'POST' }), 'Status akun direset.') }, 'Reset status'),
        h('button', { class: 'btn small', disabled: edit, onclick: () => patch({ enabled: !p.enabled }) }, p.enabled ? 'Matikan' : 'Nyalakan'),
        h('button', { class: 'btn small', disabled: edit, onclick: () => editProvider(p) }, 'Ubah'),
        h('button', { class: 'btn small danger', disabled: edit, onclick: () => { if (confirm(`Hapus provider ${p.name} beserta semua API key-nya?`)) act(() => api(`/api/router/providers/${p.id}`, { method: 'DELETE' }), 'Provider dihapus.').then(loadModels); } }, 'Hapus')));

    const accHead = h('tr', null, ['Akun', 'Key', 'Status', 'Sukses', 'Latensi', 'Jalan', 'Sesi', 'Request', 'Gagal', 'Bobot', ''].map(t => h('th', null, t)));
    const accRows = p.accounts.map((a, i) => {
      const st = a.state || {};
      const status = st.status || (a.enabled ? 'siap' : 'mati');
      const why = st.cooling_s ? `${st.cooldown_reason}, ${st.cooling_s} dtk lagi` : '';
      const weight = h('input', { type: 'number', min: '0.1', max: '100', step: '0.1', value: a.weight, disabled: edit, style: 'width:70px', 'aria-label': 'Bobot',
        onchange: e => act(() => api(`/api/router/providers/${p.id}/accounts/${a.id}`, { method: 'PATCH', body: { weight: e.target.value } }), 'Bobot disimpan.') });
      return h('tr', null,
        h('td', { class: 'name', title: a.label }, a.label, p.strategy === 'fill_first' ? h('span', { class: 'muted' }, ` #${i + 1}`) : null),
        h('td', null, h('code', null, a.key)),
        h('td', { title: st.last_error || '' }, h('span', { class: `pill ${status}` }, status), why ? h('div', { class: 'muted' }, why) : null),
        h('td', null, `${st.success_pct ?? 100}%`), h('td', null, st.latency_ms ? `${full(st.latency_ms)} ms` : '–'),
        h('td', null, `${st.inflight || 0}${a.max_concurrency ? '/' + a.max_concurrency : ''}`), h('td', null, full(st.sessions || 0)),
        h('td', null, full(st.requests || 0)), h('td', null, full(st.errors || 0)), h('td', null, weight),
        h('td', null,
          h('button', { class: 'btn small', disabled: edit, onclick: () => test(p, out, a.id) }, 'Tes'),
          h('button', { class: 'btn small', disabled: edit, onclick: () => act(() => api(`/api/router/providers/${p.id}/accounts/${a.id}`, { method: 'PATCH', body: { enabled: !a.enabled } })) }, a.enabled ? 'Matikan' : 'Nyalakan'),
          h('button', { class: 'btn small danger', disabled: edit, onclick: () => { if (confirm(`Hapus akun ${a.label}?`)) act(() => api(`/api/router/providers/${p.id}/accounts/${a.id}`, { method: 'DELETE' }), 'Akun dihapus.'); } }, 'Hapus')));
    });
    const table = h('div', { class: 'table-wrap' }, h('table', { class: 'grid' }, h('thead', null, accHead),
      h('tbody', null, accRows.length ? accRows : [h('tr', null, h('td', { colspan: 11, class: 'muted' }, 'Belum ada API key.'))])));

    const keys = h('textarea', { rows: 1, placeholder: 'API key baru, satu per baris', autocomplete: 'off', spellcheck: 'false', disabled: edit, 'aria-label': 'API key baru' });
    const labelIn = h('input', { placeholder: 'Label (opsional)', maxlength: 60, disabled: edit, 'aria-label': 'Label' });
    const conc = h('input', { type: 'number', min: 0, max: 1000, placeholder: '0', disabled: edit, style: 'width:90px', 'aria-label': 'Batas paralel' });
    const add = h('div', { class: 'add-keys' },
      h('label', { class: 'grow' }, 'Tambah akun', keys), h('label', null, 'Label', labelIn), h('label', { style: 'flex:0;min-width:110px' }, 'Paralel maks', conc),
      h('button', { class: 'btn primary', disabled: edit, onclick: () => act(async () => {
        const r = await api(`/api/router/providers/${p.id}/accounts`, { method: 'POST', body: { api_key: keys.value, label: labelIn.value, max_concurrency: conc.value } });
        keys.value = '';
        return r;
      }, 'Akun ditambahkan.') }, 'Tambah'));
    return h('div', { class: 'provider' + (p.enabled ? '' : ' off') }, head, table, add, out);
  }

  async function test(p, out, accountId) {
    out.className = 'test-out';
    out.textContent = 'Menguji…';
    try {
      const r = await api(`/api/router/providers/${p.id}/test${accountId ? `?account_id=${encodeURIComponent(accountId)}` : ''}`, { method: 'POST' });
      out.className = 'test-out ' + (r.ok ? 'ok' : 'bad');
      out.textContent = r.ok
        ? `${r.account}: tersambung, ${r.models} model (${r.latency_ms} ms). ${r.sample.join(', ')}${r.models > r.sample.length ? ', …' : ''}`
        : `${r.account}: gagal, ${r.error}`;
    } catch (e) { out.className = 'test-out bad'; out.textContent = e.message; }
  }

  function editProvider(p) {
    const name = prompt('Nama provider', p.name); if (name === null) return;
    const base = prompt('Base URL', p.base_url); if (base === null) return;
    const prefix = prompt('Prefix model (diakhiri /)', p.prefix); if (prefix === null) return;
    const models = prompt('Model tambahan, pisahkan koma (kosongkan bila tidak ada)', p.models.join(', ')); if (models === null) return;
    act(() => api(`/api/router/providers/${p.id}`, { method: 'PATCH', body: { name, base_url: base, prefix, models } }), 'Provider diubah.');
  }

  /* ── add provider form ─────────────────────────── */
  const form = $('add-form');
  $('show-add').onclick = () => { form.hidden = false; form.elements.name.focus(); };
  $('cancel-add').onclick = () => { form.hidden = true; };
  form.elements.base_url.addEventListener('change', e => {
    if (/anthropic\.com/.test(e.target.value)) form.elements.kind.value = 'anthropic';
  });
  form.onsubmit = ev => {
    ev.preventDefault();
    const f = form.elements;
    const body = {
      name: f.name.value, kind: f.kind.value, base_url: f.base_url.value, prefix: f.prefix.value,
      strategy: f.strategy.value, models: f.models.value, default: f.default.checked,
      api_keys: f.api_keys.value.split('\n').map(k => k.trim()).filter(Boolean),
    };
    act(async () => {
      const r = await api('/api/router/providers', { method: 'POST', body });
      form.reset();
      form.hidden = true;
      return r;
    }, 'Provider tersimpan.');
  };

  /* ── models, PIN, .env ─────────────────────────── */
  async function loadModels() {
    try {
      const r = await api('/api/router/models');
      // The chat page's model picker follows this list.
      if (window.SquidPage) window.SquidPage.notify('sq-models');
      const select = $('default-model');
      const all = r.groups.flatMap(g => g.models);
      select.replaceChildren(h('option', { value: '' }, 'Otomatis (DEFAULT_MODEL atau model pertama)'),
        ...r.groups.map(g => h('optgroup', { label: g.name }, g.models.map(m => h('option', { value: m, selected: m === r.chosen }, m)))));
      if (r.chosen && !all.includes(r.chosen)) select.prepend(h('option', { value: r.chosen, selected: true }, `${r.chosen} (belum tersedia)`));
      select.disabled = !S.canEdit;
      $('model-groups').replaceChildren(...(r.groups.length ? r.groups.map(g => h('div', { class: 'model-group' },
        h('h3', null, g.name, h('span', null, `${g.models.length} model`)),
        h('div', { class: 'model-list' }, g.models.map(m => h('span', { class: 'model-chip' + (m === r.default_model ? ' on' : ''), title: m }, m)))))
        : [h('p', { class: 'hint' }, 'Belum ada model. Tambahkan provider dan API key; modelnya langsung muncul di sini.')]));
    } catch (e) { banner(e.message, true); }
  }
  $('default-model').addEventListener('change', e => act(() => api('/api/router/default-model', { method: 'POST', body: { model: e.target.value } }),
    e.target.value ? `Chat baru memakai ${e.target.value}.` : 'Model default kembali otomatis.').then(loadModels));
  $('pin-bar').addEventListener('submit', ev => {
    ev.preventDefault();
    try { sessionStorage.setItem(PIN_KEY, $('pin-input').value.trim()); } catch { /* private window */ }
    $('pin-input').value = '';
    loadProviders().then(() => { banner(S.canEdit ? 'PIN diterima.' : 'PIN salah.', !S.canEdit); loadModels(); });
  });
  $('pin-form').addEventListener('submit', ev => {
    ev.preventDefault();
    const value = $('pin-new').value.trim();
    act(async () => {
      const r = await api('/api/router/pin', { method: 'POST', body: { pin: value } });
      try { if (value) sessionStorage.setItem(PIN_KEY, value); else sessionStorage.removeItem(PIN_KEY); } catch { /* private window */ }
      $('pin-new').value = '';
      return r;
    }, value ? 'PIN disimpan.' : 'PIN dihapus.');
  });
  $('import-env').onclick = () => act(async () => {
    const r = await api('/api/router/import-env', { method: 'POST' });
    const names = r.added.map(a => `${a.provider.name} (${a.models.ok ? a.models.count + ' model' : 'model gagal diambil'})`);
    banner(names.length ? `Diimpor dari .env: ${names.join(', ')}.` : 'Tidak ada endpoint baru di .env.');
    loadModels();
  });

  /* ── tabs, refresh ─────────────────────────────── */
  const tabs = (id, key, attr) => $(id).addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    S[key] = b.dataset[attr];
    $(id).querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
    loadUsage();
  });
  tabs('periods', 'period', 'period');
  tabs('groups', 'group', 'group');
  $('sync').onclick = () => syncModels(false).then(loadModels);
  window.addEventListener('resize', () => { if (S.usage) renderChart(S.usage.series, S.usage.bucket_s); });

  loadProviders().then(loadModels).then(loadPrices);
  loadUsage();
  setInterval(() => { if (!document.hidden && !document.activeElement?.matches('input, textarea, select')) loadProviders(); }, 5000);
  setInterval(() => { if (!document.hidden) loadUsage(); }, 15000);
})();
