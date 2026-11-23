/* Terminal panel: this chat's shell on this machine (xterm.js over /ws/terminal).
 * The user types here, a sudo password included; the agent's terminal_run commands show up here too.
 * Closing the panel leaves the shell running, so a dev server keeps serving. */
(() => {
  'use strict';
  const { $, toast, S, api, post, esc } = window.SQ;
  const panel = $('term-panel');
  const screen = $('term-screen');
  const button = $('open-terminal');
  const state = $('term-state');
  if (!panel || !screen || !button || !window.Terminal) return;

  let term = null;
  let fit = null;
  let socket = null;
  let session = '';
  let retry = 0;

  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const theme = () => ({
    background: css('--surface-2') || '#1a1d1c',
    foreground: css('--text') || '#ecebe6',
    cursor: css('--accent') || '#3ccbb7',
    selectionBackground: css('--accent-soft') || '#12332f',
  });

  function setState(text, kind = '') {
    state.textContent = text;
    state.dataset.kind = kind;
  }

  function send(message) {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  }

  function ensureTerm() {
    if (term) return;
    term = new window.Terminal({
      cursorBlink: true, fontSize: 13, scrollback: 5000, convertEol: false,
      fontFamily: css('--mono') || 'ui-monospace, monospace', theme: theme(), allowProposedApi: false,
    });
    fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(screen);
    term.onData(data => send({ type: 'input', data }));
    term.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }));
    new ResizeObserver(() => { if (!panel.classList.contains('hidden')) fit.fit(); }).observe(screen);
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { term.options.theme = theme(); });
  }

  function connect() {
    if (!S.session) { setState('Kirim pesan dulu supaya chat ini punya folder.', 'warn'); return; }
    if (socket && session === S.session && socket.readyState <= WebSocket.OPEN) return;
    if (socket) socket.close();
    session = S.session;
    term.reset();
    ports.clear();
    renderPreviews();
    setState('Menyambung…');
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/terminal?session=${encodeURIComponent(session)}`);
    socket = ws;
    ws.onopen = () => {
      retry = 0;
      setState('Tersambung', 'ok');
      fit.fit();
      send({ type: 'resize', cols: term.cols, rows: term.rows });
    };
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.type === 'output') { term.write(m.data); spotServers(m.data); }
      else if (m.type === 'exit') setState('Shell berhenti. Tekan mulai ulang.', 'warn');
    };
    ws.onclose = e => {
      if (socket !== ws) return;
      if (e.code === 4403) { setState(e.reason || 'Terminal hanya bisa dibuka dari komputer ini.', 'warn'); return; }
      if (panel.classList.contains('hidden')) return;
      setState('Terputus, menyambung lagi…', 'warn');
      retry = Math.min(retry + 1, 5);
      setTimeout(() => { if (socket === ws) { socket = null; connect(); } }, 600 * retry);
    };
  }

  /* Servers the terminal mentions (php artisan serve, vite, http.server) get a button that opens them:
   * the local address on this machine, a Cloudflare link of their own from anywhere else. */
  const SERVER_RE = /(?:https?:\/\/)?(?:127\.0\.0\.1|localhost|0\.0\.0\.0|\[::\]|\[::1\]):(\d{2,5})/g;
  const ports = new Set();
  function spotServers(text) {
    const plain = text.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');
    let added = false;
    for (const m of plain.matchAll(SERVER_RE)) {
      const port = Number(m[1]);
      if (port > 0 && port < 65536 && !ports.has(port) && String(port) !== location.port) { ports.add(port); added = true; }
    }
    if (added) renderPreviews();
  }
  function renderPreviews() {
    const box = $('term-previews');
    box.innerHTML = [...ports].slice(-4).map(port =>
      `<button type="button" class="term-preview" data-port="${port}" title="Buka server di port ${port}">Buka :${esc(String(port))} ↗</button>`).join('');
    box.querySelectorAll('button').forEach(b => { b.onclick = () => openPreview(Number(b.dataset.port)); });
  }
  async function openPreview(port) {
    // Opened now, filled later: a window opened after an await is blocked as a popup.
    const tab = window.open('about:blank', '_blank');
    setState(`Menyiapkan link :${port}…`);
    try {
      const info = await post('/api/preview/open', { port });
      for (let left = info.ready_in || 0; left > 0; left--) {
        setState(`Link :${port} aktif dalam ${left} dtk…`);
        if (tab && tab.document && tab.document.body) tab.document.body.textContent = `Menyiapkan ${info.url} (${left} dtk)…`;
        await new Promise(r => setTimeout(r, 1000));
      }
      if (tab) { tab.opener = null; tab.location.href = info.url; } else window.open(info.url, '_blank', 'noopener');
      setState(info.local ? `:${port} dibuka` : `Link publik :${port} siap`, 'ok');
      if (!info.local) toast(`Link pratinjau: ${info.url}`);
    } catch (e) {
      if (tab) tab.close();
      setState(e.message, 'warn');
      if (/Belum ada server/.test(e.message)) { ports.delete(port); renderPreviews(); }
    }
  }

  async function open({ focus = true } = {}) {
    ensureTerm();
    const info = await api(`/api/terminal/status?session=${encodeURIComponent(S.session || '')}`).catch(() => null);
    if (info && !info.allowed) {
      toast('Terminal hanya bisa dibuka dari browser di komputer ini.');
      return;
    }
    if (info && info.cwd) { $('term-cwd').textContent = info.cwd.replace(/^\/home\/[^/]+/, '~'); $('term-cwd').title = info.cwd; }
    panel.classList.remove('hidden');
    button.setAttribute('aria-expanded', 'true');
    document.body.classList.add('term-open');
    requestAnimationFrame(() => { fit.fit(); connect(); if (focus) term.focus(); });
  }

  function close() {
    panel.classList.add('hidden');
    button.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('term-open');
  }

  function toggle() { panel.classList.contains('hidden') ? open() : close(); }

  function sessionChanged() {
    if (!term || panel.classList.contains('hidden')) { if (socket) { socket.close(); socket = null; } return; }
    connect();
  }

  button.onclick = toggle;
  $('term-close').onclick = close;
  $('term-ctrlc').onclick = () => { send({ type: 'input', data: '\x03' }); term && term.focus(); };
  $('term-restart').onclick = () => { send({ type: 'restart' }); term && term.reset(); };
  document.addEventListener('keydown', e => {
    if (e.ctrlKey && e.key === '`') { e.preventDefault(); toggle(); }
  });

  window.SQ.terminal = { open, close, toggle, sessionChanged };
})();
