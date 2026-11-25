// A voice-over or music file from public/ (copy the MP3 that voice_speak wrote into public/ first).
import React from 'react';
import {Audio, interpolate, staticFile, useVideoConfig} from 'remotion';

export const Voice: React.FC<{file: string; volume?: number; /** fade in/out in seconds (music beds) */ fade?: number}> = ({file, volume = 1, fade = 0}) => {
  const {fps, durationInFrames} = useVideoConfig();
  return (
    <Audio
      src={staticFile(file)}
      volume={(f) =>
        fade > 0
          ? volume * Math.min(
              interpolate(f, [0, fade * fps], [0, 1], {extrapolateRight: 'clamp'}),
              interpolate(f, [durationInFrames - fade * fps, durationInFrames], [1, 0], {extrapolateLeft: 'clamp'}),
            )
          : volume
      }
    />
  );
};
