// A still image that slowly zooms and drifts (the documentary move), from public/.
import React from 'react';
import {AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

export const KenBurns: React.FC<{file: string; from?: number; to?: number; panX?: number; panY?: number; darken?: number}> = ({file, from = 1, to = 1.15, panX = -3, panY = -2, darken = 0.25}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const p = interpolate(frame, [0, durationInFrames], [0, 1], {extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{overflow: 'hidden', backgroundColor: '#000'}}>
      <Img src={staticFile(file)} style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${from + (to - from) * p}) translate(${panX * p}%, ${panY * p}%)`}} />
      <AbsoluteFill style={{background: `linear-gradient(180deg, rgba(0,0,0,${darken * 0.6}) 0%, rgba(0,0,0,0) 40%, rgba(0,0,0,${darken}) 100%)`}} />
    </AbsoluteFill>
  );
};
