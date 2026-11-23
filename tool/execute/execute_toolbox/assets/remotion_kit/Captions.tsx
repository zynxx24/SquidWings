// Captions synced to a voice-over: pages of a few words, the word being spoken pops in the highlight colour.
// words: the <voice>.words.json that voice_speak writes (Edge engine), or transcribe(words=true).
import React from 'react';
import {AbsoluteFill, spring, useCurrentFrame, useVideoConfig} from 'remotion';

export type Word = {word: string; start: number; end: number};

export const Captions: React.FC<{
  words: Word[];
  perPage?: number;
  color?: string;
  highlight?: string;
  fontSize?: number;
  fontFamily?: string;
  /** distance from the bottom edge in px (vertical videos: keep clear of the app's UI, ~280) */
  bottom?: number;
  /** seconds to shift every word (the voice starts later than the composition) */
  offset?: number;
}> = ({words, perPage = 4, color = '#FFFFFF', highlight = '#FFD84D', fontSize = 68, fontFamily = '"Plus Jakarta Sans", Inter, "Segoe UI", system-ui, sans-serif', bottom = 260, offset = 0}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps - offset;
  const pages: Word[][] = [];
  for (let i = 0; i < words.length; i += perPage) pages.push(words.slice(i, i + perPage));
  const page = pages.find((p) => t >= p[0].start - 0.05 && t <= p[p.length - 1].end + 0.3);
  if (!page) return null;
  const enter = spring({frame: frame - Math.round((page[0].start + offset) * fps), fps, config: {damping: 14, stiffness: 180}});
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: bottom}}>
      <div
        style={{
          display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '0.28em', maxWidth: '86%',
          fontSize, fontWeight: 800, fontFamily, lineHeight: 1.15, textAlign: 'center', letterSpacing: '-0.01em',
          opacity: enter, transform: `translateY(${(1 - enter) * 30}px) scale(${0.92 + 0.08 * enter})`,
        }}
      >
        {page.map((w, i) => {
          const on = t >= w.start && t < w.end + 0.05;
          const pop = spring({frame: frame - Math.round((w.start + offset) * fps), fps, config: {damping: 10, stiffness: 240}});
          return (
            <span
              key={i}
              style={{
                display: 'inline-block', color: on ? highlight : color, transform: `scale(${on ? 1 + 0.12 * pop : 1})`,
                textShadow: '0 4px 20px rgba(0,0,0,.55)', WebkitTextStroke: '2px rgba(0,0,0,.35)',
              }}
            >
              {w.word}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
