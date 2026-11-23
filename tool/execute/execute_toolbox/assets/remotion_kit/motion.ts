// Small helpers so scenes read in seconds, not frames.
import {interpolate, spring, useCurrentFrame, useVideoConfig, Easing} from 'remotion';

/** seconds -> frames at the composition's fps */
export const useSec = () => {
  const {fps} = useVideoConfig();
  return (s: number) => Math.round(s * fps);
};

/** 0 -> 1 with a spring, starting `delay` seconds into the current Sequence */
export const useEnter = (delay = 0, damping = 14) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return spring({frame: frame - Math.round(delay * fps), fps, config: {damping}});
};

/** 1 -> 0 over the last `seconds` of the current Sequence (pass its length in frames) */
export const useExit = (length: number, seconds = 0.4) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return interpolate(frame, [length - seconds * fps, length], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.in(Easing.cubic)});
};
