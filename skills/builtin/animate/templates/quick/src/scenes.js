
// One function per era, in world coordinates (1080x1920). TT = seconds since the piece start.
// Each scene: a full-bleed wall, a ground, the hero (spark) reacting, 2+ details, a caption.
function sceneA() {
  cut(rect(-30, -30, W + 60, H + 60, 0), PAL.mint, { key: 'wallA', shadow: false, tear: 0, shade: false, pat: pat.stripes('rgba(255,255,255,0.18)', 30, 80) });
  for (let i = 0; i < 5; i++) cut(ellipsePts(140 + i * 190, 1000, 90, 160, 0, 30), PAL.green, { key: 'tree' + i });
  cut(rect(-30, 1150, W + 60, 900, 0), PAL.wood, { key: 'ground', tear: 0 });
  spark(500, 1300, 90, { rays: 6, mood: 'smile', key: 'hero' });
  yearTag('GANTI');
  capStrip('GANTI caption 1');
}
function sceneB() {
  cut(rect(-30, -30, W + 60, H + 60, 0), PAL.navy, { key: 'wallB', shadow: false, tear: 0, shade: false });
  // things pop in one after another from a cue
  for (let i = 0; i < 5; i++) { const k = popS(TIMELINE.cues.pop + i * 0.125); if (k > 0) cut(ellipsePts(140 + i * 190, 1050, 60 * k, 140 * k, 0, 24), PAL.mustard, { key: 'pop' + i, crayon: PAL.yellow }); }
  cut(rect(-30, 1150, W + 60, 900, 0), PAL.charcoal, { key: 'groundB', tear: 0 });
  spark(500, 1300, 90, { rays: 7, mood: 'wow', key: 'hero' });
  tag('GANTI!', 300, 600, 70);
  capStrip('GANTI caption 2');
}
function sceneC() {
  cut(rect(-30, -30, W + 60, H + 60, 0), '#8a8590', { key: 'wallC', shadow: false, tear: 0, shade: false, pat: pat.dots('rgba(255,255,255,0.15)', 6, 60) });
  // something drifting: Math.sin(TT) is deterministic, Math.random() is not allowed
  for (let i = 0; i < 4; i++) cut(ellipsePts(200 + i * 220 + Math.sin(TT + i) * 20, 600, 150, 80, 0, 24), '#d8d4cc', { key: 'cloud' + i });
  spark(500, 1300, 90, { rays: 8, mood: 'focus', key: 'hero' });
  capStrip('GANTI caption 3');
}
