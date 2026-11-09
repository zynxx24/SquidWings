/* Shared by the chat, trading and router pages. Load it in <head>, before the page's own scripts.

   - A loading bar at the top while requests run, and a spinner on the button that started one.
     A GET that repeats within a few seconds is a poll, so it never shows the bar.
   - Inside the chat page (in a frame): tells the chat page it is ready, takes its theme, and
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

  window.SquidPage = { begin, end, hold, track, notify, embedded };
})();
