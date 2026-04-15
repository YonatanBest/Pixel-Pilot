import { EventEmitter } from 'node:events';
import WebSocket, { type RawData } from 'ws';
import type { RuntimeEventEnvelope, RuntimeSnapshot, SidecarFrame } from '../shared/types.js';
import { isRecord, isRuntimeSnapshot, parseRuntimeEventEnvelope } from '../shared/runtime-guards.js';

type PendingCommand = {
  resolve: (payload: Record<string, unknown>) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
};

type ControlWaiter = {
  resolve: (socket: WebSocket) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
};

const CONTROL_CONNECT_TIMEOUT_MS = 30000;
const COMMAND_RESPONSE_TIMEOUT_MS = 45000;
const SIDECAR_MAX_HEADER_BYTES = 256 * 1024;
const RECONNECT_DELAY_MS = 1000;

export function rawDataToBuffer(packet: RawData): Buffer | null {
  if (Buffer.isBuffer(packet)) {
    return packet;
  }
  if (Array.isArray(packet)) {
    return Buffer.concat(packet.map((item) => (Buffer.isBuffer(item) ? item : Buffer.from(item))));
  }
  if (packet instanceof ArrayBuffer) {
    return Buffer.from(packet);
  }
  return null;
}

export function parseControlEnvelopeData(packet: RawData): RuntimeEventEnvelope | null {
  let parsed: unknown;
  if (typeof packet === 'string') {
    parsed = JSON.parse(packet) as unknown;
  } else {
    const buffer = rawDataToBuffer(packet);
    if (!buffer) {
      return null;
    }
    parsed = JSON.parse(buffer.toString('utf-8')) as unknown;
  }
  return parseRuntimeEventEnvelope(parsed);
}

export function parseSidecarFrame(packet: RawData): SidecarFrame {
  const buffer = rawDataToBuffer(packet);
  if (!buffer) {
    throw new Error('Expected a binary sidecar frame.');
  }
  if (buffer.length < 4) {
    throw new Error('Sidecar frame is too small.');
  }
  const headerSize = buffer.readUInt32BE(0);
  if (headerSize > SIDECAR_MAX_HEADER_BYTES) {
    throw new Error('Sidecar frame metadata is too large.');
  }
  const headerEnd = 4 + headerSize;
  if (buffer.length < headerEnd) {
    throw new Error('Sidecar frame metadata is truncated.');
  }
  const metadata = JSON.parse(buffer.subarray(4, headerEnd).toString('utf-8')) as unknown;
  if (!isRecord(metadata)) {
    throw new Error('Sidecar frame metadata must be an object.');
  }
  const width = Number(metadata.width);
  const height = Number(metadata.height);
  const timestamp = Number(metadata.timestamp);
  if (!Number.isFinite(width) || !Number.isFinite(height) || !Number.isFinite(timestamp)) {
    throw new Error('Sidecar frame metadata is missing numeric dimensions or timestamp.');
  }
  const jpeg = buffer.subarray(headerEnd);
  return {
    width,
    height,
    timestamp,
    dataUrl: `data:image/jpeg;base64,${jpeg.toString('base64')}`
  };
}

type RuntimeBridgeClientOptions = {
  commandTimeoutMs?: number;
};

export class RuntimeBridgeClient extends EventEmitter {
  private readonly controlUrl: string;
  private readonly sidecarUrl: string;
  private controlSocket: WebSocket | null = null;
  private sidecarSocket: WebSocket | null = null;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private pending = new Map<string, PendingCommand>();
  private controlWaiters = new Set<ControlWaiter>();
  private stopped = false;
  private readonly commandTimeoutMs: number;

  public snapshot: RuntimeSnapshot | null = null;

  isControlConnected(): boolean {
    return Boolean(this.controlSocket && this.controlSocket.readyState === WebSocket.OPEN);
  }

  constructor(controlUrl: string, sidecarUrl: string, options: RuntimeBridgeClientOptions = {}) {
    super();
    this.controlUrl = controlUrl;
    this.sidecarUrl = sidecarUrl;
    this.commandTimeoutMs = Math.max(100, Number(options.commandTimeoutMs || COMMAND_RESPONSE_TIMEOUT_MS));
  }

  start(): void {
    this.stopped = false;
    this.connectControl();
    this.connectSidecar();
  }

  dispose(): void {
    this.stopped = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.controlSocket?.close();
    this.sidecarSocket?.close();
    this.rejectPendingCommands(new Error('Runtime bridge closed.'));
    this.rejectControlWaiters(new Error('Runtime bridge closed.'));
  }

  async sendCommand(method: string, payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    const socket = await this.waitForControlSocket();
    const id = crypto.randomUUID();
    const envelope: RuntimeEventEnvelope = {
      id,
      kind: 'command',
      method,
      payload,
      protocolVersion: 1
    };
    const response = new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => {
        const pending = this.pending.get(id);
        if (!pending) {
          return;
        }
        this.pending.delete(id);
        pending.reject(new Error(`Runtime bridge command '${method}' timed out after ${this.commandTimeoutMs}ms.`));
      }, this.commandTimeoutMs);
      this.pending.set(id, { resolve, reject, timer });
    });
    socket.send(JSON.stringify(envelope));
    return response;
  }

  respond(id: string, method: string, payload: Record<string, unknown>): void {
    const socket = this.controlSocket;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    socket.send(
      JSON.stringify({
        id,
        kind: 'response',
        method,
        payload,
        protocolVersion: 1
      })
    );
  }

  private connectControl(): void {
    if (this.stopped || this.hasActiveSocket(this.controlSocket)) {
      return;
    }
    const socket = new WebSocket(this.controlUrl);
    this.controlSocket = socket;

    socket.on('open', () => {
      this.resolveControlWaiters(socket);
      this.emit('connected');
    });

    socket.on('message', (data: RawData) => {
      try {
        const envelope = parseControlEnvelopeData(data);
        if (!envelope) {
          this.emit('bridge-error', new Error('Runtime bridge sent an invalid envelope.'));
          return;
        }
        this.handleControlEnvelope(envelope);
      } catch (error) {
        this.emit('bridge-error', error instanceof Error ? error : new Error(String(error)));
      }
    });

    socket.on('close', () => {
      if (this.controlSocket === socket) {
        this.controlSocket = null;
      }
      this.rejectPendingCommands(new Error('Runtime bridge disconnected while waiting for a response.'));
      this.emit('disconnected');
      if (!this.stopped) {
        this.scheduleReconnect();
      }
    });

    socket.on('error', (error: Error) => {
      this.emit('bridge-error', error instanceof Error ? error : new Error(String(error)));
    });
  }

  private connectSidecar(): void {
    if (this.stopped || this.hasActiveSocket(this.sidecarSocket)) {
      return;
    }
    const socket = new WebSocket(this.sidecarUrl);
    this.sidecarSocket = socket;

    socket.on('message', (data: RawData) => {
      try {
        this.emit('sidecar-frame', parseSidecarFrame(data));
      } catch (error) {
        this.emit('bridge-error', error instanceof Error ? error : new Error(String(error)));
      }
    });

    socket.on('close', () => {
      if (this.sidecarSocket === socket) {
        this.sidecarSocket = null;
      }
      if (!this.stopped) {
        this.scheduleReconnect();
      }
    });

    socket.on('error', (error: Error) => {
      this.emit('bridge-error', error instanceof Error ? error : new Error(String(error)));
    });
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer || this.stopped) {
      return;
    }
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connectControl();
      this.connectSidecar();
    }, RECONNECT_DELAY_MS);
  }

  private forceControlReconnect(): void {
    if (this.stopped) {
      return;
    }

    const socket = this.controlSocket;
    if (socket && socket.readyState === WebSocket.CONNECTING) {
      try {
        socket.terminate();
      } catch {
        // Ignore terminate errors and continue resetting the bridge socket.
      }
      if (this.controlSocket === socket) {
        this.controlSocket = null;
      }
    }

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    this.connectControl();
    this.connectSidecar();
  }

  private handleControlEnvelope(envelope: RuntimeEventEnvelope): void {
    if (envelope.kind === 'response') {
      const pending = this.pending.get(envelope.id);
      if (!pending) {
        return;
      }
      this.pending.delete(envelope.id);
      clearTimeout(pending.timer);
      pending.resolve(envelope.payload);
      return;
    }

    if (envelope.kind === 'error') {
      const pending = this.pending.get(envelope.id);
      if (pending) {
        this.pending.delete(envelope.id);
        clearTimeout(pending.timer);
        pending.reject(new Error(String(envelope.payload.message || 'Runtime bridge error')));
        return;
      }
      this.emit('runtime-error', envelope.payload);
      return;
    }

    if (envelope.kind === 'request') {
      this.emit('request', envelope);
      return;
    }

    if (envelope.method === 'state.snapshot' || envelope.method === 'state.updated') {
      if (isRuntimeSnapshot(envelope.payload)) {
        this.snapshot = envelope.payload;
        this.emit('snapshot', this.snapshot);
      } else {
        this.emit('bridge-error', new Error(`Runtime bridge sent an invalid ${envelope.method} payload.`));
      }
    }

    this.emit('event', envelope);
  }

  private waitForControlSocket(timeoutMs: number = CONTROL_CONNECT_TIMEOUT_MS): Promise<WebSocket> {
    const socket = this.controlSocket;
    if (socket && socket.readyState === WebSocket.OPEN) {
      return Promise.resolve(socket);
    }
    if (this.stopped) {
      return Promise.reject(new Error('Runtime bridge closed.'));
    }

    return new Promise<WebSocket>((resolve, reject) => {
      const waiter: ControlWaiter = {
        resolve: (nextSocket) => {
          clearTimeout(waiter.timer);
          this.controlWaiters.delete(waiter);
          resolve(nextSocket);
        },
        reject: (error) => {
          clearTimeout(waiter.timer);
          this.controlWaiters.delete(waiter);
          reject(error);
        },
        timer: setTimeout(() => {
          const latestSocket = this.controlSocket;
          if (!latestSocket || latestSocket.readyState !== WebSocket.OPEN) {
            this.forceControlReconnect();
          }
          waiter.reject(new Error('Runtime bridge is reconnecting. Please try again in a moment.'));
        }, timeoutMs)
      };

      this.controlWaiters.add(waiter);

      const latestSocket = this.controlSocket;
      if (latestSocket && latestSocket.readyState === WebSocket.OPEN) {
        waiter.resolve(latestSocket);
      }
    });
  }

  private resolveControlWaiters(socket: WebSocket): void {
    for (const waiter of [...this.controlWaiters]) {
      waiter.resolve(socket);
    }
  }

  private rejectControlWaiters(error: Error): void {
    for (const waiter of [...this.controlWaiters]) {
      waiter.reject(error);
    }
  }

  private rejectPendingCommands(error: Error): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private hasActiveSocket(socket: WebSocket | null): boolean {
    return Boolean(socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN));
  }
}
