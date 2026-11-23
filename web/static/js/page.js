/* Shared by the chat, trading and router pages. Load it in <head>, before the page's own scripts.

   - A loading bar at the top while requests run, and a spinner on the button that started one.
     A GET that repeats within a few seconds is a poll, so it never shows the bar.
   - The palette (colour set from tokens.css): remembered per browser, a swatch menu for any button.
   - Inside the chat page (in a frame): tells the chat page it is ready, takes its theme and palette, and
     reports itself hidden while another page is shown, so its polling rests. */
(() => {
  'use strict';

  const root = document.documentElement;
  let embedded = false;
  try { embedded = window.self !== window.top; } catch { embedded = true; }
  if (embedded) root.classList.add('sq-embedded');

  /* ── Loading bar ─────────────────────────────────── */
  const SHOW_AFTER_MS = 120;     // a fast answer shows nothing at all
  const BUTTON_AFTER_MS = 180;
  const POLL_WINDOW_MS = 8000;
  const CLICK_LINK_MS = 400;     // a request this soon after a click belongs to that button
  let bar = null;
  let running = 0;
  let width = 0;
  let showTimer = 0;
  let trickle = 0;
  let doneTimer = 0;
  const holds = new Set();

  function ensureBar() {
    if (!bar && document.body) {
      bar = document.createElement('div');
      bar.className = 'sq-progress';
      bar.setAttribute('aria-hidden', 'true');
      document.body.append(bar);
    }
    return bar;
  }
  const busy = () => running > 0 || holds.size > 0;

  function start() {
    clearTimeout(doneTimer);
    if (showTimer || (bar && bar.classList.contains('on'))) return;
    showTimer = setTimeout(() => {
      showTimer = 0;
      if (!busy() || !ensureBar()) return;
      width = 8;
      bar.classList.add('on');
      bar.style.width = width + '%';
      clearInterval(trickle);
      // Creeps towards 90% and never gets there until the work is done.
      trickle = setInterval(() => { width += (90 - width) * 0.08; bar.style.width = width + '%'; }, 260);
    }, SHOW_AFTER_MS);
  }

  function settle() {
    if (busy()) return;
    clearTimeout(showTimer);
    showTimer = 0;
    clearInterval(trickle);
    if (!bar || !bar.classList.contains('on')) return;
    bar.style.width = '100%';
    doneTimer = setTimeout(() => {
      bar.classList.remove('on');
      doneTimer = setTimeout(() => { if (!busy()) bar.style.width = '0'; }, 350);
    }, 220);
  }

  const begin = () => { running += 1; start(); };
  const end = () => { running = Math.max(0, running - 1); settle(); };
  /** Keep the bar up while something other than a request loads, e.g. a frame. */
  function hold(key, on) {
    if (on) { holds.add(key); start(); } else if (holds.delete(key)) settle();
  }
  function track(promise) {
    begin();
    return Promise.resolve(promise).finally(end);
  }

  /* ── The button behind a request ─────────────────── */
  let clicked = null;
  let clickedAt = 0;
  document.addEventListener('click', e => {
    const button = e.target instanceof Element ? e.target.closest('button') : null;
    // Only buttons with words get a spinner; an icon button has no room for one.
    clicked = button && button.textContent.trim() ? button : null;
    clickedAt = Date.now();
  }, true);

  function spinFor(button) {
    if (!button) return () => {};
    let shown = false;
    const timer = setTimeout(() => { shown = true; button.classList.add('sq-busy'); button.setAttribute('aria-busy', 'true'); }, BUTTON_AFTER_MS);
    return () => {
      clearTimeout(timer);
      if (shown) { button.classList.remove('sq-busy'); button.removeAttribute('aria-busy'); }
    };
  }

  /* ── fetch, watched ──────────────────────────────── */
  const lastSeen = new Map();
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    let url = '';
    try { url = String(typeof input === 'string' || input instanceof URL ? input : input.url); } catch { /* odd input */ }
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const key = method + ' ' + url.split('?')[0];
    const now = Date.now();
    const poll = method === 'GET' && now - (lastSeen.get(key) || 0) < POLL_WINDOW_MS;
    lastSeen.set(key, now);
    if (poll) return nativeFetch(input, init);
    const button = clicked && now - clickedAt < CLICK_LINK_MS ? clicked : null;
    const unspin = spinFor(button);
    begin();
    return nativeFetch(input, init).finally(() => { end(); unspin(); });
  };

  /* ── Palette: one of the colour sets in tokens.css ── */
  // The list lives in the CSS (--palette-list), so a palette added there shows up in every picker.
  const PALETTE_KEY = 'sq:palette';
  function paletteList() {
    const css = getComputedStyle(root);
    const ids = (css.getPropertyValue('--palette-list') || '').replace(/["']/g, '').trim().split(/\s+/).filter(Boolean);
    const labels = Object.fromEntries((css.getPropertyValue('--palette-labels') || '').replace(/["']/g, '').trim()
      .split(/\s+/).filter(Boolean).map(pair => pair.split(':')));
    return ids.map(id => ({ id, label: labels[id] || id }));
  }
  const validPalette = id => typeof id === 'string' && /^[a-z0-9-]{2,24}$/.test(id);
  function getPalette() {
    return root.dataset.palette || 'laut';
  }
  function applyPalette(id, remember) {
    if (!validPalette(id)) return;
    root.dataset.palette = id;
    if (remember) { try { localStorage.setItem(PALETTE_KEY, id); } catch { /* not remembered */ } }
    window.dispatchEvent(new CustomEvent('sq-palette', { detail: id }));
  }
  try { const saved = localStorage.getItem(PALETTE_KEY); if (validPalette(saved)) root.dataset.palette = saved; } catch { /* default */ }

  /** A small menu of swatches under ``button``; ``onPick(id)`` runs after the palette changes. */
  function paletteMenu(button, onPick) {
    let menu = null;
    const close = () => { if (menu) { menu.remove(); menu = null; button.setAttribute('aria-expanded', 'false'); } };
    button.setAttribute('aria-haspopup', 'true');
    button.setAttribute('aria-expanded', 'false');
    button.addEventListener('click', e => {
      e.stopPropagation();
      if (menu) { close(); return; }
      menu = document.createElement('div');
      menu.className = 'sq-palette-menu';
      menu.setAttribute('role', 'menu');
      const current = getPalette();
      for (const p of paletteList()) {
        const item = document.createElement('button');
        item.type = 'button';
        item.setAttribute('role', 'menuitemradio');
        item.setAttribute('aria-checked', String(p.id === current));
        item.className = 'sq-palette-item';
        const sw = document.createElement('span');
        sw.className = 'sq-swatch';
        sw.dataset.palette = p.id;            // the swatch takes that palette's own tokens
        const name = document.createElement('span');
        name.textContent = p.label;
        item.append(sw, name);
        item.onclick = () => { applyPalette(p.id, true); close(); if (onPick) onPick(p.id); };
        menu.append(item);
      }
      const box = button.getBoundingClientRect();
      menu.style.left = Math.max(8, Math.min(box.left, window.innerWidth - 200)) + 'px';
      if (box.top > window.innerHeight / 2) menu.style.bottom = (window.innerHeight - box.top + 6) + 'px';
      else menu.style.top = (box.bottom + 6) + 'px';
      document.body.append(menu);
      const first = menu.querySelector('[aria-checked="true"]') || menu.firstChild;
      if (first) first.focus();
    });
    document.addEventListener('click', e => { if (menu && !menu.contains(e.target)) close(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  }

  /* ── Motion helpers ──────────────────────────────── */
  const calm = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /** Let ``els`` rise into place one after another (``from`` = stagger index of the first). */
  function reveal(els, from = 0) {
    const list = Array.from(els || []).filter(el => el instanceof Element);
    list.forEach((el, i) => {
      el.style.setProperty('--i', String(from + i));
      el.classList.remove('sq-in');
      el.classList.add('sq-reveal');
    });
    if (calm()) { list.forEach(el => el.classList.add('sq-in')); return; }
    requestAnimationFrame(() => requestAnimationFrame(() => list.forEach(el => el.classList.add('sq-in'))));
  }

  /** Count a number up (or down) inside ``el`` from what it showed to ``to``; ``fmt`` renders each frame. */
  function countTo(el, to, fmt, ms = 700) {
    if (!el) return;
    const target = Number(to);
    if (!Number.isFinite(target)) { el.textContent = fmt(to); return; }
    const from = Number(el.dataset.sqValue);
    el.dataset.sqValue = String(target);
    if (calm() || !Number.isFinite(from) || from === target) { el.textContent = fmt(target); return; }
    const t0 = performance.now();
    const step = now => {
      const k = Math.min(1, (now - t0) / ms);
      const e = 1 - Math.pow(1 - k, 4);
      el.textContent = fmt(from + (target - from) * e);
      if (k < 1 && el.dataset.sqValue === String(target)) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /* ── Inside the chat page ────────────────────────── */
  let shown = true;
  if (embedded) {
    // The chat page keeps this frame alive while it shows another page. Report that as hidden,
    // so the page's `if (!document.hidden)` polling rests until it is shown again.
    const nativeHidden = Object.getOwnPropertyDescriptor(Document.prototype, 'hidden');
    try {
      Object.defineProperty(document, 'hidden', {
        configurable: true,
        get: () => !shown || Boolean(nativeHidden && nativeHidden.get.call(document)),
      });
    } catch { /* the page polls a little more, nothing breaks */ }

    window.addEventListener('message', e => {
      if (e.source !== window.parent || !e.data || typeof e.data !== 'object') return;
      if (e.data.type === 'sq-theme' && ['system', 'light', 'dark'].includes(e.data.theme)) {
        if (e.data.theme === 'system') root.removeAttribute('data-theme');
        else root.dataset.theme = e.data.theme;
        window.dispatchEvent(new CustomEvent('sq-theme', { detail: e.data.theme }));
      } else if (e.data.type === 'sq-palette') {
        applyPalette(e.data.palette, false);
      } else if (e.data.type === 'sq-visible') {
        const next = Boolean(e.data.visible);
        if (next !== shown) {
          shown = next;
          document.dispatchEvent(new Event('visibilitychange'));
        }
      }
    });

    // Links back to the chat open it in the chat page, not inside this frame.
    document.addEventListener('click', e => {
      const link = e.target instanceof Element ? e.target.closest('a[data-sq-view]') : null;
      if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      window.parent.postMessage({ type: 'sq-nav', view: link.dataset.sqView }, '*');
    });
  }

  const ready = () => {
    ensureBar();
    // Nothing secret is sent: only that the page is here. The chat page checks the sender.
    if (embedded) window.parent.postMessage({ type: 'sq-ready', title: document.title }, '*');
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready, { once: true });
  else ready();

  /** Tell the chat page (around this frame, or in another tab) that something it shows changed. */
  let channel = null;
  try { channel = new BroadcastChannel('squidwings'); } catch { /* old browser */ }
  function notify(type) {
    const message = { type };
    if (embedded) window.parent.postMessage(message, '*');
    if (channel) channel.postMessage(message);
  }

  window.SquidPage = { begin, end, hold, track, notify, embedded, paletteList, getPalette, applyPalette, paletteMenu, reveal, countTo, calm };
})();
