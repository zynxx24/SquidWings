/* A contact sheet of frames, for studio_still: the agent's (and the chat's) look at a Studio video. */
(async () => {
  'use strict';
  const R = window.StudioRender;
  const q = new URLSearchParams(location.search);
  const session = q.get('session'), project = q.get('project');
  const times = (q.get('times') || '0').split(',').map(Number).filter(t => t >= 0);
  const scale = Math.min(1, Math.max(0.05, Number(q.get('scale')) || 0.3));
  const base = `/api/studio/${encodeURIComponent(session)}/project/${encodeURIComponent(project)}`;
  const asset = src => `${base}/asset/${encodeURIComponent(src.replace(/^assets\//, ''))}`;
  const { doc } = await (await fetch(base)).json();

  const families = new Set(doc.fonts || []);
  doc.layers.forEach(l => { if (l.type === 'text' && l.font) families.add(l.font); });
  if (families.size) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = `/api/fonts/css2?${[...families].map(f => `family=${encodeURIComponent(f)}:wght@300;400;500;600;700;800;900`).join('&')}`;
    await new Promise(r => { link.onload = r; link.onerror = r; document.head.append(link); });
    await Promise.all(doc.layers.filter(l => l.type === 'text').map(l => document.fonts.load(`${l.weight || 400} 40px "${l.font}"`).catch(() => null)));
  }
  const images = {}, videos = {};
  await Promise.all(doc.layers.filter(l => l.type === 'image' && l.src).map(l => new Promise(r => {
    const img = new Image(); img.onload = r; img.onerror = r; img.src = asset(l.src); images[l.src] = img;
  })));
  await Promise.all(doc.layers.filter(l => l.type === 'video' && l.src && !videos[l.src]).map(l => new Promise(r => {
    const v = document.createElement('video'); v.muted = true; v.preload = 'auto'; v.onloadeddata = r; v.onerror = r; v.src = asset(l.src); videos[l.src] = v;
  })));

  const cols = Math.min(3, times.length);
  const grid = document.getElementById('grid');
  grid.style.gridTemplateColumns = `repeat(${cols}, max-content)`;
  for (const t of times) {
    await R.seekVideos(doc, t, videos);
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(doc.width * scale); canvas.height = Math.round(doc.height * scale);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    R.drawFrame(ctx, doc, t, { images, videos });
    const figure = document.createElement('figure');
    const caption = document.createElement('figcaption');
    const visible = doc.layers.filter(l => R.visible(l, t)).map(l => l.id).join(', ');
    caption.textContent = `${t.toFixed(2)} dtk · ${visible || 'kosong'}`;
    figure.style.width = `${canvas.width}px`;   // a long caption wraps instead of widening the column
    figure.append(canvas, caption);
    grid.append(figure);
  }
  document.title = 'ready';
})();
