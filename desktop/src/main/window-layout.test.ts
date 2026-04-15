import { describe, expect, it } from 'vitest';
import { getAnchoredWindowBounds, normalizeWindowSize } from './window-layout.js';

const workArea = {
  x: 0,
  y: 0,
  width: 1920,
  height: 1080
};

describe('window-layout', () => {
  it('anchors the notch to the top-center of the display', () => {
    const bounds = getAnchoredWindowBounds('notch', workArea, { width: 420, height: 76 });

    expect(bounds).toEqual({
      x: 750,
      y: 0,
      width: 420,
      height: 76
    });
  });

  it('anchors the overlay near the top-middle of the screen', () => {
    const bounds = getAnchoredWindowBounds('overlay', workArea, { width: 920, height: 220 });

    expect(bounds).toEqual({
      x: 500,
      y: 56,
      width: 920,
      height: 220
    });
  });

  it('anchors the glow window across the whole work area', () => {
    const bounds = getAnchoredWindowBounds('glow', workArea, { width: 400, height: 300 });

    expect(bounds).toEqual({
      x: 0,
      y: 0,
      width: 1920,
      height: 1080
    });
  });

  it('allows the overlay to shrink down to the compact bar height', () => {
    const size = normalizeWindowSize('overlay', workArea, { width: 920, height: 88 });

    expect(size).toEqual({
      width: 920,
      height: 88
    });
  });

  it('clamps sidecar size within the available work area', () => {
    const size = normalizeWindowSize('sidecar', { x: 0, y: 0, width: 380, height: 420 }, { width: 900, height: 999 });

    expect(size).toEqual({
      width: 332,
      height: 372
    });
  });

  it('clamps the settings hub within the available work area', () => {
    const size = normalizeWindowSize('settings', { x: 0, y: 0, width: 240, height: 260 }, { width: 900, height: 999 });

    expect(size).toEqual({
      width: 520,
      height: 420
    });
  });

  it('preserves the intended settings hub size when the display has room', () => {
    const size = normalizeWindowSize('settings', workArea, { width: 640, height: 560 });

    expect(size).toEqual({
      width: 640,
      height: 560
    });
  });
});
