# kit: ready parts for this Remotion project

Import from `./kit` in any scene: `import {Captions, Voice, Title, KenBurns, Backdrop, useEnter, useSec} from './kit';`

| Part | What it does |
|---|---|
| `<Voice file="voice.mp3" />` | plays a file from `public/`; `fade={1}` for a music bed |
| `<Captions words={words} />` | word-synced captions; `words` from `voice.words.json` (voice_speak, Edge) or transcribe(words=true) |
| `<Title text="…" sub="…" />` | title, words spring in one by one |
| `<KenBurns file="photo.jpg" />` | slow zoom and drift over a still |
| `<Backdrop a="#…" b="#…" />` | moving gradient background, cheap to render |
| `useEnter(delay)`, `useExit(len)`, `useSec()` | springs and timing in seconds |

Voice-over to video, the usual way:

1. `voice_speak(text, output="<project>/public/voice.mp3")` writes `voice.mp3`, `voice.srt` and `voice.words.json`.
2. In `src/`: `import words from '../public/voice.words.json';` then
   `<AbsoluteFill><Backdrop/><Title text="…"/><Captions words={words}/><Voice file="voice.mp3"/></AbsoluteFill>`.
3. Set `durationInFrames` in `src/Root.tsx` to the voice length: `Math.ceil((words.at(-1)!.end + 0.6) * fps)`.

Fast renders: no CSS `filter: blur()` or big `box-shadow` animated every frame, `<Img>`/`<OffthreadVideo>` instead of
`<img>`/`<video>`, images sized near the output, fonts loaded once with `@remotion/google-fonts`.
