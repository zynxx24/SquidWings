// A title whose words rise in one after another with a spring, then hold.
import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

export const Title: React.FC<{
  text: string;
  sub?: string;
  color?: string;
  accent?: string;
  fontSize?: number;
  fontFamily?: string;
  align?: 'center' | 'left';
  /** frames between words */
  stagger?: number;
}> = ({text, sub, color = '#FFFFFF', accent = '#FFD84D', fontSize = 110, fontFamily = '"Plus Jakarta Sans", Inter, "Segoe UI", system-ui, sans-serif', align = 'center', stagger = 4}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const words = text.split(/\s+/);
  const subIn = spring({frame: frame - words.length * stagger - 6, fps, config: {damping: 16}});
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: align === 'center' ? 'center' : 'flex-start', padding: '0 8%'}}>
      <h1 style={{margin: 0, fontFamily, fontSize, fontWeight: 850, lineHeight: 1.02, letterSpacing: '-0.035em', color, textAlign: align}}>
        {words.map((w, i) => {
          const s = spring({frame: frame - i * stagger, fps, config: {damping: 13, stiffness: 160}});
          return (
            <span key={i} style={{display: 'inline-block', marginRight: '0.24em', opacity: s, transform: `translateY(${interpolate(s, [0, 1], [60, 0])}px) rotate(${interpolate(s, [0, 1], [4, 0])}deg)`}}>
              {w}
            </span>
          );
        })}
      </h1>
      {sub ? (
        <p style={{margin: '0.6em 0 0', fontFamily, fontSize: fontSize * 0.32, fontWeight: 600, color: accent, opacity: subIn, transform: `translateY(${(1 - subIn) * 20}px)`}}>
          {sub}
        </p>
      ) : null}
    </AbsoluteFill>
  );
};
