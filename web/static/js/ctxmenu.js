/* Right-click menus, shared by the flow canvas and the Data tab.

   Editing here is done with a menu on the right mouse button, never with a double click. The same menu opens from
   the keyboard (the Menu key or Shift+F10 on a focused element) and from a long press on a touch screen, so nothing
   depends on having a right button.

   SQMenu.on(element, build)   open a menu for right-click, keyboard and long press on the element.
                               build({ event, target, keyboard }) returns the items, or nothing to leave the
                               browser's own menu alone.
   SQMenu.show(items, x, y)    open a menu at a point. An item is { label, hint, danger, disabled, run } or
                               { heading: 'text' } or { separator: true }.
   SQMenu.close()              close it. Esc, a click elsewhere, scrolling and resizing close it too.

   Labels are always set as text, never as HTML, because they carry names the user typed. */
(() => {
  'use strict';

  const LONG_PRESS_MS = 550;
  const LONG_PRESS_SLOP = 9;
  let open = null; // { root, opener, onDown, onKey, items }

  const actionable = item => item && !item.separator && !item.heading;
  const enabled = item => actionable(item) && !item.disabled;

  function close(restoreFocus = true) {
    if (!open) return;
    const { root, opener, off } = open;
    open = null;
    off();
    root.remove();
    if (restoreFocus && opener && opener.isConnected && typeof opener.focus === 'function') {
      try { opener.focus({ preventScroll: true }); } catch { /* the element may not take focus */ }
    }
  }

  /* Where a menu of this size goes so that it stays on screen. Exposed for testing. */
  function place(x, y, width, height, viewW, viewH, margin = 8) {
    const left = Math.max(margin, Math.min(x, viewW - width - margin));
    const top = Math.max(margin, Math.min(y, viewH - height - margin));
    return { left, top };
  }

  function build(items, run) {
    const root = document.createElement('div');
    root.className = 'ctx-menu';
    root.tabIndex = -1;
    root.setAttribute('role', 'menu');
    const buttons = [];
    items.filter(Boolean).forEach(item => {
      if (item.separator) {
        const line = document.createElement('div');
        line.className = 'ctx-sep';
        line.setAttribute('role', 'separator');
        root.appendChild(line);
        return;
      }
      if (item.heading) {
        const head = document.createElement('div');
        head.className = 'ctx-head';
        head.textContent = item.heading;
        head.setAttribute('role', 'presentation');
        root.appendChild(head);
        return;
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `ctx-item${item.danger ? ' danger' : ''}`;
      button.setAttribute('role', 'menuitem');
      button.tabIndex = -1;
      const label = document.createElement('span');
      label.className = 'ctx-label';
      label.textContent = item.label;
      button.appendChild(label);
      if (item.hint) {
        const hint = document.createElement('span');
        hint.className = 'ctx-hint';
        hint.textContent = item.hint;
        button.appendChild(hint);
      }
      if (item.disabled) {
        button.setAttribute('aria-disabled', 'true');
        button.classList.add('disabled');
        if (typeof item.disabled === 'string') button.title = item.disabled;
      }
      button.onclick = e => {
        e.preventDefault();
        e.stopPropagation();
        if (item.disabled) return;
        run(item);
      };
      root.appendChild(button);
      buttons.push({ button, item });
    });
    return { root, buttons };
  }

  function show(items, x, y, opener, options = {}) {
    const list = (items || []).filter(Boolean);
    if (!list.some(actionable)) return;
    close(false);
    const fresh = build(list, item => {
      const after = item.run;
      const returnTo = opener;
      close(false);
      if (typeof after === 'function') after();
      else if (returnTo && returnTo.isConnected && typeof returnTo.focus === 'function') returnTo.focus({ preventScroll: true });
    });
    const { root, buttons } = fresh;
    root.style.visibility = 'hidden';
    document.body.appendChild(root);
    // Two pixels off the pointer, so letting go of the button never lands on an item.
    const spot = place(x + 2, y + 2, root.offsetWidth, root.offsetHeight, window.innerWidth, window.innerHeight);
    root.style.left = `${spot.left}px`;
    root.style.top = `${spot.top}px`;
    root.style.maxHeight = `${Math.max(120, window.innerHeight - 16)}px`;
    root.style.visibility = '';

    const usable = buttons.filter(b => !b.item.disabled);
    const focusAt = index => {
      if (!usable.length) return;
      const next = (index + usable.length) % usable.length;
      usable[next].button.focus({ preventScroll: true });
    };
    const current = () => usable.findIndex(b => b.button === document.activeElement);
    const onKey = e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return; }
      if (e.key === 'Tab') { close(false); return; }
      if (e.key === 'ArrowDown') { e.preventDefault(); focusAt(current() + 1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); focusAt(current() < 0 ? -1 : current() - 1); return; }
      if (e.key === 'Home') { e.preventDefault(); focusAt(0); return; }
      if (e.key === 'End') { e.preventDefault(); focusAt(usable.length - 1); return; }
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const letter = e.key.toLowerCase();
        const start = current() + 1;
        for (let step = 0; step < usable.length; step += 1) {
          const candidate = usable[(start + step) % usable.length];
          if (candidate.item.label.toLowerCase().startsWith(letter)) { candidate.button.focus({ preventScroll: true }); break; }
        }
      }
    };
    const onDown = e => { if (!root.contains(e.target)) close(false); };
    const onAway = () => close(false);
    const onScroll = e => { if (!root.contains(e.target)) close(false); };
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('blur', onAway);
    window.addEventListener('resize', onAway);
    document.addEventListener('scroll', onScroll, true);
    document.addEventListener('wheel', onScroll, { capture: true, passive: true });
    open = {
      root, opener, items: list,
      off() {
        document.removeEventListener('pointerdown', onDown, true);
        document.removeEventListener('keydown', onKey, true);
        window.removeEventListener('blur', onAway);
        window.removeEventListener('resize', onAway);
        document.removeEventListener('scroll', onScroll, true);
        document.removeEventListener('wheel', onScroll, { capture: true });
      },
    };
    // A menu opened from the keyboard starts on its first item; with the mouse it waits for the pointer.
    if (options.keyboard) focusAt(0);
    else root.focus({ preventScroll: true });
  }

  /* Wire an element so that right-click, the Menu key and a long press open its menu. */
  function on(element, builder, options = {}) {
    const fire = (event, keyboard, x, y) => {
      const items = builder({ event, target: event.target, keyboard });
      if (!items || !items.filter(Boolean).length) return false;
      show(items, x, y, keyboard ? (event.target instanceof Element ? event.target : element) : element, { keyboard });
      return true;
    };
    element.addEventListener('contextmenu', event => {
      // A key press can also raise "contextmenu"; the keydown handler below already dealt with it.
      if (event.button === 0 && event.detail === 0 && event.clientX === 0 && event.clientY === 0) return;
      if (fire(event, false, event.clientX, event.clientY)) { event.preventDefault(); event.stopPropagation(); }
    });
    element.addEventListener('keydown', event => {
      if (!(event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey))) return;
      const target = event.target instanceof Element ? event.target : element;
      const box = target.getBoundingClientRect();
      if (fire(event, true, box.left + Math.min(box.width, 220) / 2, box.bottom - Math.min(box.height, 24) / 2)) {
        event.preventDefault();
        event.stopPropagation();
      }
    });
    if (options.longPress === false) return;
    let timer = 0;
    let origin = null;
    const cancel = () => { clearTimeout(timer); timer = 0; origin = null; };
    element.addEventListener('pointerdown', event => {
      if (event.pointerType !== 'touch') return;
      cancel();
      origin = { x: event.clientX, y: event.clientY };
      const target = event.target;
      timer = window.setTimeout(() => {
        timer = 0;
        const fake = { target, preventDefault() {}, stopPropagation() {}, clientX: origin ? origin.x : 0, clientY: origin ? origin.y : 0 };
        fire(fake, false, fake.clientX, fake.clientY);
      }, LONG_PRESS_MS);
    });
    element.addEventListener('pointermove', event => {
      if (origin && Math.hypot(event.clientX - origin.x, event.clientY - origin.y) > LONG_PRESS_SLOP) cancel();
    });
    ['pointerup', 'pointercancel', 'pointerleave'].forEach(name => element.addEventListener(name, cancel));
  }

  window.SQMenu = { on, show, close, place, isOpen: () => Boolean(open) };
})();
