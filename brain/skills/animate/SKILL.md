---
name: animate
description: Make a short animated video entirely in code (one canvas, a synthesized score, rendered to MP4) in a built-in style or one matched from references.
triggers: animasi, animasikan, animate, animation, animated, video animasi, motion graphic, motion graphics, motion design, video animasi, animasi video
scope: contextual
context: video, mp4, film, klip, clip, reel, reels, shorts, tiktok, explainer, storyboard, render, youtube, durasi, detik
requires: node, ffmpeg
---
# Animating in code

Adapted from cth9191/animate (MIT, see LICENSE). The kit, tools, styles, and story grammar are theirs, unchanged. This page is how to drive them from SquidWings.

A video here is one `index.html`: a `<canvas>` drawn frame by frame, plus a score synthesized in code, rendered to MP4 by a headless browser and ffmpeg. Every frame is deterministic, so the review tools can measure it.

## Before anything

1. Check the tools: `node --version` (18+), `ffmpeg -version`, and `npx playwright --version`. If Chromium for Playwright is missing: `npx playwright install chromium`. If something is missing, tell the user the exact install command and stop. Don't fake a render.
2. Install the kit: `load_skill(name="animate", install_files=true)`. It returns `installed_at`, this session's own copy (`.skills/<session>/animate`). Below it is written `$KIT`: always use the real path, never another session's copy. A copy that already exists is kept, so styles the user made survive.
3. Read `craft.md` with `load_skill(name="animate", file="craft.md")`. It is the distilled rule list, and most mistakes are already in it.

## The rules that matter most

- **No animating before the storyboard is approved.** A wrong story or look costs a full build. Three cheap check-ins come first.
- **Look before you draw.** Read the style's `STYLE.md` (`file="styles/<name>/STYLE.md"`) and check every key frame against `grammar/FRAME.md`.
- **Every fact on screen is checked** with `web_research` and listed with its source in `brief.md`. Never write "the first" without a source.
- **One file, all code.** No image, audio, or font URLs and no `data:` URIs. The user's own screenshots in `assets/` and their own music track are the only exceptions. `build.mjs` enforces this.
- **Deterministic.** Use `RNG(...)` and the boil index `B`, never `Math.random()` or `Date`.
- **"Done" is measured.** `review.mjs` passes, you looked at the contact sheets, and you report the numbers. "Looks good" is not a result.

## The flow

**0. Intake.** Ask only what the request leaves open, as one short list with a recommended default for each (`intake.md` has the questions): what it's about and the one true thing it should get across, format and length (default 9:16, 30 to 60s), voice or music only, the style, a hero. If the user says "langsung buat saja", use every default and say which you picked.

Style picks: cut paper for histories and stories, crosshatch for software explainers, math for anything with equations, pixel for computing history, isometric for products and infrastructure, riso or sketchbook for a softer editorial feel. For the user's own references, follow `new-style.md`.

**1. Story check (text only).** Choose the format yourself from `grammar/FORMATS.md` (a history is a chronology joined by morphs, someone-wants-something is a mission cut on the beat). Read `grammar/STORY.md`. Show the user:
- the idea in one line and the device you can say in a sentence
- format and why, style, length, BPM
- a numbered beat table: time, what happens, the bridge into the next beat, and the sound's role (where the silence is, where the loudest hit is)

Then ask: approve, change beats, or a different angle.

**2. Look check (2 to 4 frames).** Start the piece in `pieces/<name>/` by copying `piece.json` and `src/` from `$KIT/styles/<style>/demo/`, or from `templates/beat-cut/` for hard cuts on the beat. Set `"style"` in `piece.json`. Draw the most different beats at full size, then:

```bash
node $KIT/tools/still.mjs pieces/<name> 2.25,9.75,16.5 look.png --scale 0.5
```

Show `look.png`. Ask for a thumbs up per frame.

**3. Storyboard check (every beat).** Fill `TIMELINE.board` with one key time per beat (`{ t, title, sound, next }`), render with `tools/storyboard.mjs`, and ask for thumbs up or down by panel number. The panels are the real scene functions, so nothing gets redrawn later.

**4. Build.**

```bash
node $KIT/tools/build.mjs pieces/<name>                     # → index.html
node $KIT/tools/tile.mjs pieces/<name> tile.png 0 48 96 144 # look at it, across every cut
node $KIT/tools/export.mjs pieces/<name> --share             # → renders/share.mp4
node $KIT/tools/review.mjs pieces/<name>                     # the measured checks
```

Write `brief.md` (spec, beats, sound plan, claims with sources) and keep `LOG.md`. Edit the parts in `src/`, never the built `index.html`. Write big scene files in parts of about 200 lines. Iterate the mix with `export.mjs --only-audio`, which is about 4 times faster.

**5. Deliver.** Send `renders/share.mp4` and say in a few lines: what it is, the review numbers, what the review caught and you fixed, the weakest shot, and what you could not verify. You can't hear the score, so say so and ask the user to listen.

## When it goes wrong

- `review.mjs` → TEXT flags a cut-off or overlapping label: trust it over your eye, a cropped label looks fine on a contact sheet.
- `tile.mjs` prints `deterministic: false`: a frame depends on what the last one left on the canvas, or something uses `Math.random`.
- DEAD BEATS: nothing changes for more than 4 seconds. Add a move, a reveal, or a reaction.
- Long pieces (45 to 60s): one `src/era-NN.js` per section, listed in `piece.json` `"scenes"`. The head, bridges, score, and kit stay shared.

## More guides

Load with `load_skill(name="animate", file=...)`: `craft.md` (rules), `intake.md` (questions and defaults), `grammar/FORMATS.md`, `grammar/STORY.md`, `grammar/FRAME.md`, `styles/README.md` and `styles/<name>/STYLE.md`, `new-style.md` (a look from references), `REFERENCE.md` (the original full guide, including voice-over, the user's own music, product screenshots, and several formats from one piece).
