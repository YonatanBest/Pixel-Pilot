import fs from 'node:fs';
import path from 'node:path';
import type { UiPreferences } from '../shared/types.js';

const defaults: UiPreferences = {
  cornerGlowEnabled: true,
  statusNotchEnabled: false,
};

function normalizeBoolean(value: unknown, fallback: boolean): boolean {
  if (typeof value === 'boolean') {
    return value;
  }
  return fallback;
}

function normalizePreferences(payload: unknown): UiPreferences {
  if (!payload || typeof payload !== 'object') {
    return { ...defaults };
  }
  const candidate = payload as Record<string, unknown>;
  return {
    cornerGlowEnabled: normalizeBoolean(candidate.cornerGlowEnabled, defaults.cornerGlowEnabled),
    statusNotchEnabled: normalizeBoolean(candidate.statusNotchEnabled, defaults.statusNotchEnabled),
  };
}

export class UiPreferencesStore {
  private readonly filePath: string;
  private loaded = false;
  private cached: UiPreferences = { ...defaults };

  constructor(userDataDir: string) {
    this.filePath = path.join(userDataDir, 'ui-preferences.json');
  }

  load(): UiPreferences {
    if (this.loaded) {
      return { ...this.cached };
    }

    this.loaded = true;
    try {
      if (!fs.existsSync(this.filePath)) {
        this.cached = { ...defaults };
        return { ...this.cached };
      }
      const raw = fs.readFileSync(this.filePath, 'utf-8');
      this.cached = normalizePreferences(raw ? JSON.parse(raw) : null);
      return { ...this.cached };
    } catch {
      this.cached = { ...defaults };
      return { ...this.cached };
    }
  }

  save(partial: Partial<UiPreferences>): UiPreferences {
    const current = this.load();
    const next = normalizePreferences({
      ...current,
      ...partial,
    });
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath, JSON.stringify(next, null, 2), 'utf-8');
    this.cached = next;
    this.loaded = true;
    return { ...next };
  }
}
