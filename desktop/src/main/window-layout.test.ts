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
      y: 24,
      width: 920,
      height: 220
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

  it('clamps the settings popup within the available work area', () => {
    const size = normalizeWindowSize('settings', { x: 0, y: 0, width: 240, height: 260 }, { width: 900, height: 999 });

    expect(size).toEqual({
      width: 240,
      height: 212
    });
  });

  it('lets the settings popup grow to fit taller content when the display has room', () => {
    const size = normalizeWindowSize('settings', workArea, { width: 220, height: 520 });

    expect(size).toEqual({
      width: 220,
      height: 520
    });
  });

  it('clamps the startup settings popup within the available work area', () => {
    const size = normalizeWindowSize(
      'startup-settings',
      { x: 0, y: 0, width: 300, height: 340 },
      { width: 900, height: 999 }
    );

    expect(size).toEqual({
      width: 260,
      height: 292
    });
  });

  it('clamps the session settings popup within the available work area', () => {
    const size = normalizeWindowSize(
      'session-settings',
      { x: 0, y: 0, width: 420, height: 360 },
      { width: 900, height: 999 }
    );

    expect(size).toEqual({
      width: 372,
      height: 312
    });
  });

  it('clamps the extensions settings popup within the available work area', () => {
    const size = normalizeWindowSize(
      'extensions-settings',
      { x: 0, y: 0, width: 460, height: 380 },
      { width: 900, height: 999 }
    );

    expect(size).toEqual({
      width: 412,
      height: 332
    });
  });
});
