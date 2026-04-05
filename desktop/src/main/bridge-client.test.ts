import { once } from 'node:events';
import { WebSocketServer } from 'ws';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RuntimeBridgeClient, parseControlEnvelopeData, parseSidecarFrame, rawDataToBuffer } from './bridge-client.js';

const activeServers = new Set<WebSocketServer>();
const activeClients = new Set<RuntimeBridgeClient>();

afterEach(async () => {
  for (const client of activeClients) {
    client.dispose();
  }
  activeClients.clear();

  await Promise.all(
    [...activeServers].map(
      (server) =>
        new Promise<void>((resolve) => {
          server.close(() => resolve());
        })
    )
  );
  activeServers.clear();
});

describe('bridge-client transport decoding', () => {
  it('parses control envelopes delivered as websocket buffers', () => {
    const payload = {
      id: 'evt-1',
      kind: 'event',
      method: 'state.snapshot',
      payload: { ready: true },
      protocolVersion: 1
    };

    expect(parseControlEnvelopeData(Buffer.from(JSON.stringify(payload), 'utf-8'))).toEqual(payload);
  });

  it('converts array buffers into node buffers', () => {
    const buffer = rawDataToBuffer(Uint8Array.from([1, 2, 3, 4]).buffer);
    expect(buffer).not.toBeNull();
    expect(Array.from(buffer ?? Buffer.alloc(0))).toEqual([1, 2, 3, 4]);
  });

  it('parses sidecar frames from websocket binary packets', () => {
    const metadata = Buffer.from(JSON.stringify({ width: 320, height: 200, timestamp: 1234 }), 'utf-8');
    const header = Buffer.alloc(4);
    header.writeUInt32BE(metadata.length, 0);
    const jpeg = Buffer.from([0xff, 0xd8, 0xff, 0xd9]);

    const frame = parseSidecarFrame(Buffer.concat([header, metadata, jpeg]));

    expect(frame.width).toBe(320);
    expect(frame.height).toBe(200);
    expect(frame.timestamp).toBe(1234);
    expect(frame.dataUrl).toBe(`data:image/jpeg;base64,${jpeg.toString('base64')}`);
  });

  it('waits for the control socket to connect before sending commands', async () => {
    const server = new WebSocketServer({ port: 0 });
    activeServers.add(server);
    const port = (server.address() as { port: number }).port;
    const client = new RuntimeBridgeClient(`ws://127.0.0.1:${port}/control?token=test`, `ws://127.0.0.1:${port}/sidecar?token=test`);
    activeClients.add(client);

    server.on('connection', (socket, request) => {
      if ((request.url || '').startsWith('/control')) {
        socket.on('message', (raw) => {
          const envelope = JSON.parse(raw.toString()) as { id: string; method: string };
          socket.send(
            JSON.stringify({
              id: envelope.id,
              kind: 'response',
              method: envelope.method,
              payload: { ok: true },
              protocolVersion: 1
            })
          );
        });
      }
    });

    client.start();
    const responsePromise = client.sendCommand('live.stop');

    await expect(responsePromise).resolves.toEqual({ ok: true });
  });

  it('rejects in-flight commands when the control socket disconnects', async () => {
    const server = new WebSocketServer({ port: 0 });
    activeServers.add(server);
    const port = (server.address() as { port: number }).port;
    const client = new RuntimeBridgeClient(`ws://127.0.0.1:${port}/control?token=test`, `ws://127.0.0.1:${port}/sidecar?token=test`);
    activeClients.add(client);

    server.on('connection', (socket, request) => {
      if ((request.url || '').startsWith('/control')) {
        socket.on('message', () => {
          socket.close();
        });
      }
    });

    client.start();
    await once(client, 'connected');

    await expect(client.sendCommand('live.stop')).rejects.toThrow(
      'Runtime bridge disconnected while waiting for a response.'
    );
  });

  it('forces reconnect when control socket remains stuck in connecting state', async () => {
    const client = new RuntimeBridgeClient(
      'ws://127.0.0.1:65535/control?token=test',
      'ws://127.0.0.1:65535/sidecar?token=test'
    );

    const anyClient = client as unknown as {
      controlSocket: { readyState: number; terminate: () => void } | null;
      connectControl: () => void;
      connectSidecar: () => void;
      waitForControlSocket: (timeoutMs?: number) => Promise<unknown>;
    };

    const terminate = vi.fn();
    const connectControl = vi.fn();
    const connectSidecar = vi.fn();

    anyClient.controlSocket = {
      readyState: 0,
      terminate
    };
    anyClient.connectControl = connectControl;
    anyClient.connectSidecar = connectSidecar;

    await expect(anyClient.waitForControlSocket(5)).rejects.toThrow(
      'Runtime bridge is reconnecting. Please try again in a moment.'
    );

    expect(terminate).toHaveBeenCalledTimes(1);
    expect(connectControl).toHaveBeenCalledTimes(1);
    expect(connectSidecar).toHaveBeenCalledTimes(1);
  });
});
