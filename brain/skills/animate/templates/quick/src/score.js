  // Runs inside buildScore(). to = 'm' (music) or 's' (sfx). Notes: nz('D4').
  const T = TIMELINE, cu = T.cues;
  to = 'm';
  pad(0.0, T.duration, ['D3', 'A3', 'F4'], 0.04, { cut: 900, att: 0.05, rel: 0.2 });
  T.cuts.concat([0]).forEach((t, i) => pluck(t, nz(['D4', 'F4', 'A4', 'C5', 'D5'][i % 5]), 0.2, 0, 0.5, 2600, 0.3));
  to = 's';
  noiseHit(cu.pop, 0.3, 'bandpass', 900, 1, 0.05, 0);
