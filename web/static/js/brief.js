/* The daily brief on the empty chat page: yesterday's chats, what was made, what is still hanging.
   Everything comes from /api/brief as data and goes in as plain text, never as markup. "Tutup"
   hides it until tomorrow's brief. It stays out of the way when there is nothing to say. */
(() => {
  'use strict';
  const card = document.getElementById('brief');
  if (!card) return;

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const list = (title, items) => {
    if (!items.length) return null;
    const box = el('div', 'brief-block');
    box.appendChild(el('h3', '', title));
    const ul = el('ul');
    items.forEach(item => {
      const li = el('li');
      if (item.strong) li.appendChild(el('strong', '', item.strong));
      li.appendChild(document.createTextNode(item.text));
      ul.appendChild(li);
    });
    box.appendChild(ul);
    return box;
  };

  const render = brief => {
    card.replaceChildren();
    const head = el('div', 'brief-head');
    head.appendChild(el('h2', '', `Brief ${brief.weekday}`));
    const close = el('button', 'link-btn brief-close', 'Tutup');
    close.type = 'button';
    close.onclick = async () => {
      card.classList.add('hidden');
      try { await fetch('/api/brief/seen', { method: 'POST' }); } catch { /* it comes back tomorrow either way */ }
    };
    head.appendChild(close);
    card.appendChild(head);
    card.appendChild(el('p', 'brief-sum',
      `${brief.chat_count} chat sejak kemarin, ${brief.file_count} file dibuat atau diubah.`));
    const blocks = [
      list('Masih menggantung', brief.hanging.map(h => ({ strong: `${h.chat}: `, text: h.text }))),
      list('Hasil kerja', brief.files.slice(0, 6).map(f => ({ text: f }))),
      list('Chat', brief.chats.slice(0, 4).map(c => ({ strong: `${c.label}: `, text: c.asked }))),
    ];
    blocks.filter(Boolean).forEach(block => card.appendChild(block));
    card.classList.remove('hidden');
  };

  fetch('/api/brief')
    .then(response => (response.ok ? response.json() : null))
    .then(data => { if (data && data.enabled && !data.shown && !data.brief.empty) render(data.brief); })
    .catch(() => { /* no brief, no card */ });
})();
