import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { UiPreferencesStore } from './ui-preferences.js';

const tempDirs: string[] = [];

function makeTempDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pixelpilot-ui-prefs-'));
  tempDirs.push(dir);
  return dir;
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    fs.rmSync(dir, { force: true, recursive: true });
  }
});

describe('UiPreferencesStore', () => {
  it('loads default preferences when no file exists', () => {
    const store = new UiPreferencesStore(makeTempDir());

    expect(store.load()).toEqual({
      cornerGlowEnabled: true,
      statusNotchEnabled: false,
    });
  });

  it('persists partial preference updates', () => {
    const dir = makeTempDir();
    const store = new UiPreferencesStore(dir);

    expect(store.save({ statusNotchEnabled: true })).toEqual({
      cornerGlowEnabled: true,
      statusNotchEnabled: true,
    });

    expect(new UiPreferencesStore(dir).load()).toEqual({
      cornerGlowEnabled: true,
      statusNotchEnabled: true,
    });
  });

  it('recovers defaults from invalid JSON', () => {
    const dir = makeTempDir();
    fs.writeFileSync(path.join(dir, 'ui-preferences.json'), '{ nope', 'utf-8');

    expect(new UiPreferencesStore(dir).load()).toEqual({
      cornerGlowEnabled: true,
      statusNotchEnabled: false,
    });
  });

  it('accepts legacy nested notch visibility shape', () => {
    const dir = makeTempDir();
    fs.writeFileSync(
      path.join(dir, 'ui-preferences.json'),
      JSON.stringify({
        cornerGlowEnabled: true,
        notch: { visible: true },
      }),
      'utf-8'
    );

    expect(new UiPreferencesStore(dir).load()).toEqual({
      cornerGlowEnabled: true,
      statusNotchEnabled: true,
    });
  });

  it('accepts legacy statusNotchVisible boolean', () => {
    const dir = makeTempDir();
    fs.writeFileSync(
      path.join(dir, 'ui-preferences.json'),
      JSON.stringify({
        cornerGlowEnabled: false,
        statusNotchVisible: true,
      }),
      'utf-8'
    );

    expect(new UiPreferencesStore(dir).load()).toEqual({
      cornerGlowEnabled: false,
      statusNotchEnabled: true,
    });
  });
});
