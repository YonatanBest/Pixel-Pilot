import type { RuntimeEventEnvelope, RuntimeSnapshot } from './types.js';

const VALID_KINDS = new Set(['event', 'request', 'response', 'error', 'command']);

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function parseRuntimeEventEnvelope(value: unknown): RuntimeEventEnvelope | null {
  if (!isRecord(value)) {
    return null;
  }
  const kind = String(value.kind || '').trim();
  const method = String(value.method || '').trim();
  const payload = value.payload;
  const protocolVersion = Number(value.protocolVersion);
  if (!VALID_KINDS.has(kind) || !method || !isRecord(payload) || !Number.isInteger(protocolVersion)) {
    return null;
  }
  return {
    id: String(value.id || ''),
    kind: kind as RuntimeEventEnvelope['kind'],
    method,
    payload,
    protocolVersion
  };
}

export function isRuntimeSnapshot(value: unknown): value is RuntimeSnapshot {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.operationMode === 'string'
    && typeof value.visionMode === 'string'
    && typeof value.workspace === 'string'
    && isRecord(value.auth)
    && Array.isArray(value.recentMessages)
    && Array.isArray(value.recentActionUpdates)
  );
}
