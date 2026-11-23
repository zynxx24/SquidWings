// A moving gradient background: two soft blobs drift over a base colour. Cheap to render (no filters).
import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';

export const Backdrop: React.FC<{base?: string; a?: string; b?: string; speed?: number}> = ({base = '#0B0E14', a = '#2F6BFF', b = '#FF5C8A', speed = 1}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = (frame / fps) * speed;
  const x1 = 30 + 18 * Math.sin(t * 0.6), y1 = 30 + 14 * Math.cos(t * 0.5);
  const x2 = 70 + 16 * Math.cos(t * 0.45), y2 = 72 + 12 * Math.sin(t * 0.7);
  return (
    <AbsoluteFill
      style={{
        backgroundColor: base,
        backgroundImage: `radial-gradient(circle at ${x1}% ${y1}%, ${a}AA 0%, transparent 45%), radial-gradient(circle at ${x2}% ${y2}%, ${b}88 0%, transparent 50%)`,
      }}
    />
  );
};
