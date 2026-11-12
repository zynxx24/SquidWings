/* Voice in the chat: a mic button that types what you say into the message box, and a
   "Bacakan" button under each answer. Both use the browser's own speech features, so no key
   is needed here. Chrome and Edge send the audio to their speech service to recognise it;
   a browser without the feature never sees the buttons. The message is never sent by itself:
   you read what was heard first, the same as /voice in the terminal. */
(() => {
  'use strict';
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  const LANG = 'id-ID';
  const SPEAKER = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 0 1 0 6"/></svg>';

  const note = message => {
    const el = document.getElementById('toast');
    if (!el) return;
    el.textContent = message;
    el.classList.remove('hidden');
    clearTimeout(note.timer);
    note.timer = setTimeout(() => el.classList.add('hidden'), 3500);
  };

  /* ── Speak into the message box ───────────────────── */
  const mic = document.getElementById('mic');
  const box = document.getElementById('input');
  if (mic && box && Recognition) {
    mic.classList.remove('hidden');
    let rec = null;
    let before = '';

    const stop = () => { if (rec) rec.stop(); };
    const done = () => { rec = null; mic.classList.remove('listening'); mic.setAttribute('aria-pressed', 'false'); mic.title = 'Bicara'; };
    const fill = text => {
      box.value = before + text;
      box.dispatchEvent(new Event('input', { bubbles: true }));  // the box resizes itself on input
    };

    mic.addEventListener('click', () => {
      if (rec) { stop(); return; }
      rec = new Recognition();
      rec.lang = LANG;
      rec.interimResults = true;
      rec.continuous = true;
      before = box.value && !/\s$/.test(box.value) ? box.value + ' ' : box.value;
      rec.onresult = event => {
        let text = '';
        for (const result of event.results) text += result[0].transcript;
        fill(text);
      };
      rec.onerror = event => {
        const reasons = { 'not-allowed': 'Izin mikrofon ditolak.', 'service-not-allowed': 'Izin mikrofon ditolak.',
          'no-speech': 'Tidak ada suara yang terdengar.', 'audio-capture': 'Mikrofon tidak ditemukan.', network: 'Pengenal suara butuh internet.' };
        note(reasons[event.error] || `Suara gagal (${event.error}).`);
        done();
      };
      rec.onend = () => { box.focus(); done(); };
      try {
        rec.start();
        mic.classList.add('listening');
        mic.setAttribute('aria-pressed', 'true');
        mic.title = 'Berhenti merekam';
      } catch { done(); }
    });
  }

  /* ── Read an answer aloud ─────────────────────────── */
  if (!synth) return;
  const plain = turn => {
    const prose = turn.querySelector('.prose');
    if (!prose) return '';
    const copy = prose.cloneNode(true);
    copy.querySelectorAll('pre, .code-head, style, script').forEach(el => el.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim();
  };
  const pickVoice = () => {
    const voices = synth.getVoices();
    return voices.find(v => v.lang === LANG) || voices.find(v => v.lang.startsWith('id')) || null;
  };
  let playing = null;
  const reset = () => { if (playing) { playing.classList.remove('speaking'); playing.lastChild.textContent = 'Bacakan'; playing = null; } };

  const attach = foot => {
    if (foot.dataset.voice) return;
    foot.dataset.voice = '1';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'link-btn voice-btn';
    button.innerHTML = `${SPEAKER}<span>Bacakan</span>`;
    button.onclick = () => {
      const was = playing === button;
      synth.cancel();
      reset();
      if (was) return;
      const text = plain(foot.closest('.turn-agent'));
      if (!text) return;
      const utterance = new SpeechSynthesisUtterance(text.slice(0, 3000));
      utterance.lang = LANG;
      const voice = pickVoice();
      if (voice) utterance.voice = voice;
      utterance.onend = utterance.onerror = () => { if (playing === button) reset(); };
      playing = button;
      button.classList.add('speaking');
      button.lastChild.textContent = 'Berhenti';
      synth.speak(utterance);
    };
    foot.appendChild(button);
  };

  const scan = root => root.querySelectorAll('.turn-foot:not(.hidden)').forEach(attach);
  const thread = document.getElementById('thread');
  if (thread) {
    // foot() rewrites its HTML when a turn finishes, which drops the button, so look again on every change.
    new MutationObserver(() => {
      thread.querySelectorAll('.turn-foot:not(.hidden)').forEach(foot => {
        if (!foot.querySelector('.voice-btn')) { delete foot.dataset.voice; attach(foot); }
      });
    }).observe(thread, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    scan(thread);
  }
  window.addEventListener('pagehide', () => synth.cancel());
})();
