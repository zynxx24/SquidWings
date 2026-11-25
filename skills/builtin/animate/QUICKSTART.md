# Quick piece: from brief to MP4 in about 10 steps

For team runs and anyone short on steps. It skips the storyboard check-ins and the morphs, and
uses hard cuts between 3 to 6 eras. Everything below is already proven to build and render.
Do not read `kit/core.js`, `kit/morph.js`, `tools/` or `examples/`: what you need is on this page.

## Steps

1. `load_skill(name="animate", install_files=true)` and keep `installed_at` (below: `$KIT`).
2. Copy the template: `cp -r $KIT/templates/quick pieces/<name>` (from the work folder).
3. Edit `pieces/<name>/src/head.html`: `DURATION`, `ERA_LIST`, `SHOTS`, `TIMELINE.cuts`, `cues`, `board`.
4. Edit `pieces/<name>/piece.json`: `name`, `title`, and `format.duration` / `format.frames` (= duration × 24), the same numbers as `head.html`.
5. Write `pieces/<name>/src/scenes.js`: one function per era (copy the three in the template and change them).
6. Edit `pieces/<name>/src/bridges.js`: one `ERA_BG` colour per era.
7. `node $KIT/tools/build.mjs pieces/<name>` and fix every error it prints. Build after every file you change.
8. `node $KIT/tools/export.mjs pieces/<name> --share` with `execute_command(timeout=900)`.
9. `rm -rf pieces/<name>/frames` (each frame is a 1.5 MB PNG; 24 s is about 860 MB).
10. `share_file(path="pieces/<name>/renders/share.mp4", caption="<judul>")`.

Replace every `GANTI` in the template. A piece that still says `GANTI` is not finished.

## head.html

```js
const DURATION = 18.0;                                   // seconds; 15-30 is plenty
const ERA_LIST = [[0, 6, () => sceneA()], [6, 12, () => sceneB()], [12, 18, () => sceneC()]];
const SHOTS = [['a', 0, 0.0, 6.0, 'judul beat 1'], ['b', 1, 6.0, 12.0, 'judul beat 2'], ['c', 2, 12.0, 18.0, 'judul beat 3']];
// in TIMELINE:
cuts: [6.0, 12.0], morphs: [], cues: { pop: 7.0 },
board: [{ t: 3, title: 'beat 1', sound: 'pad', next: 'beat 2' }, /* one per era */],
```

Times sit on the 8th-note grid: at 120 BPM a multiple of 0.25 s. Every era boundary is in `cuts`.

## scenes.js: the drawing kit (cut-paper)

Coordinates: 1080 × 1920, y down. Keep words and faces inside x 60-940, y 250-1500. `TT` is the time in
seconds. Every `cut` needs its own `key` (the key seeds the torn edge).

| Call | What |
|---|---|
| `cut(points, color, { key, pat, crayon, shadow, tear, shade, sy, sb })` | A torn-paper shape. Full-bleed wall: `cut(rect(-30, -30, W + 60, H + 60, 0), PAL.navy, { key: 'wall', shadow: false, tear: 0, shade: false })` |
| `rect(x, y, w, h, r)` · `ellipsePts(cx, cy, rx, ry, rot, n)` · `capsulePts(x1, y1, x2, y2, w)` · `arcPts(cx, cy, r, a0, a1)` | Point lists for `cut` |
| `[[x, y], [x, y], ...]` | Any polygon (a roof, a mountain, a flame) |
| `spark(x, y, r, { rays, mood, key, s })` | The hero. `mood`: `smile`, `focus`, `wow`, `happy`. `s` scales it |
| `person(x, y, s, {...})` · `cat(x, y, s, color, key)` · `clock(x, y, r, h, m, key)` · `mug(x, y, s, color, key)` · `plant(x, y, s, pot, key)` · `books(x, y, s, key)` · `sparkle(x, y, size)` | Props and background life |
| `face(x, y, s, mood)` | A face on anything |
| `tag(text, x, y, size)` · `yearTag(text)` · `capStrip(text)` | Hand-lettered paper labels; `capStrip` is the caption at the bottom |
| `ink(points, { w, color, key })` | A drawn line |
| `pat.stripes(color, w, gap)` · `pat.dots(color, r, gap)` · `pat.gingham(color, w)` · `pat.grainWood(color)` | Patterns for `pat:` |
| `popS(t0)` | 0 → 1 pop from second `t0` (0 before it): `const k = popS(7); if (k > 0) ...` |
| `Math.sin(TT * speed + i)` | Drifting and bobbing. Never `Math.random()` or `Date` |

Palette `PAL`: `navy`, `night`, `wood`, `woodD`, `mustard`, `yellow`, `teal`, `mint`, `pink`, `sky`, `purple`,
`blue`, `green`, `greenD`, `grey`, `charcoal`, `brick`, `cream`, `paper`, `lamp`, `skin1`-`skin3`. Orange belongs to the spark only. Any `'#hex'` works too.

A scene, in order: a wall, a ground or table, 2+ details, the spark reacting, a `capStrip`.

## score.js

Leave it as it is, or change the chord: `pad(t0, t1, ['D3', 'A3', 'F4'], 0.04, {...})`. A pluck lands on
every cut already. `noiseHit(t, dur, 'bandpass', freq, q, vel, pan)` marks a cue.

## When build fails

- `X is not defined`: a typo in a kit name; use only the names above.
- A scene throws: the message names the line in `index.html`; fix it in `src/`, never in `index.html`.
- `ENOSPC`: the disk is full. Delete old `pieces/*/frames` and tell the user how much space a render needs.
