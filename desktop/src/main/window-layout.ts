import type { WindowKind } from '../shared/types.js';

export type ScreenWorkArea = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type WindowBounds = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type WindowSize = {
  width: number;
  height: number;
};

const EDGE_PADDING = 24;
const OVERLAY_TOP_OFFSET = 56;
const SIDECAR_TOP_OFFSET = 96;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export function normalizeWindowSize(kind: WindowKind, workArea: ScreenWorkArea, size: WindowSize): WindowSize {
  const maxWidth = Math.max(240, workArea.width - EDGE_PADDING * 2);
  const maxHeight = Math.max(140, workArea.height - EDGE_PADDING * 2);

  if (kind === 'notch') {
    return {
      width: clamp(Math.round(size.width), 180, Math.min(760, maxWidth)),
      height: clamp(Math.round(size.height), 48, Math.min(120, maxHeight))
    };
  }

  if (kind === 'glow') {
    return {
      width: Math.max(1, Math.round(workArea.width)),
      height: Math.max(1, Math.round(workArea.height))
    };
  }

  if (kind === 'sidecar') {
    return {
      width: clamp(Math.round(size.width), 320, Math.min(520, maxWidth)),
      height: clamp(Math.round(size.height), 260, Math.min(760, maxHeight))
    };
  }

  if (kind === 'settings') {
    return {
      width: clamp(Math.round(size.width), 520, Math.min(720, maxWidth)),
      height: clamp(Math.round(size.height), 420, Math.min(720, maxHeight))
    };
  }

  return {
    width: clamp(Math.round(size.width), 620, maxWidth),
    height: clamp(Math.round(size.height), 84, Math.min(820, maxHeight))
  };
}

export function getAnchoredWindowBounds(kind: WindowKind, workArea: ScreenWorkArea, requested: WindowSize): WindowBounds {
  const size = normalizeWindowSize(kind, workArea, requested);

  if (kind === 'notch') {
    return {
      x: Math.round(workArea.x + (workArea.width - size.width) / 2),
      y: workArea.y,
      width: size.width,
      height: size.height
    };
  }

  if (kind === 'sidecar') {
    return {
      x: workArea.x + workArea.width - size.width - EDGE_PADDING,
      y: workArea.y + SIDECAR_TOP_OFFSET,
      width: size.width,
      height: size.height
    };
  }

  if (kind === 'glow') {
    return {
      x: workArea.x,
      y: workArea.y,
      width: size.width,
      height: size.height
    };
  }

  return {
    x: Math.round(workArea.x + (workArea.width - size.width) / 2),
    y: workArea.y + OVERLAY_TOP_OFFSET,
    width: size.width,
    height: size.height
  };
}
