import { createRequire } from 'node:module';
import { RuntimeBridgeClient } from './bridge-client.js';
import { RuntimeProcessManager } from './runtime-process.js';
import { StartupDefaultsStore } from './startup-defaults.js';
import { WindowManager } from './window-manager.js';
import type { RuntimeEventEnvelope } from '../shared/types.js';

const require = createRequire(import.meta.url);
const { app } = require('electron') as typeof import('electron');

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
  process.exit(0);
}

let runtimeProcess: RuntimeProcessManager | null = null;
let bridgeClient: RuntimeBridgeClient | null = null;
let windowManager: WindowManager | null = null;
let startupDefaultsStore: StartupDefaultsStore | null = null;
let bridgeRecoveryTimer: NodeJS.Timeout | null = null;
let bridgeRecoveryInProgress = false;
let bridgeRecoveryPromise: Promise<void> | null = null;
let bridgeClientPromise: Promise<RuntimeBridgeClient> | null = null;
let shuttingDown = false;
let runtimeBridgeEndpoints: { controlUrl: string; sidecarUrl: string } | null = null;

const BRIDGE_RECOVERY_GRACE_MS = 8000;

function clearBridgeRecoveryTimer(): void {
  if (bridgeRecoveryTimer) {
    clearTimeout(bridgeRecoveryTimer);
    bridgeRecoveryTimer = null;
  }
}

async function invokeRuntimeFromMain(
  method: string,
  payload: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const client = await ensureBridgeClient();
  return client.sendCommand(method, payload);
}

async function beginBridgeClientStart(
  options: { reuseRuntime: boolean },
): Promise<RuntimeBridgeClient> {
  const promise = startRuntimeBridgeClient(options);
  bridgeClientPromise = promise;
  try {
    const client = await promise;
    bridgeClient = client;
    return client;
  } finally {
    if (bridgeClientPromise === promise) {
      bridgeClientPromise = null;
    }
  }
}

async function ensureBridgeClient(): Promise<RuntimeBridgeClient> {
  if (bridgeClient) {
    return bridgeClient;
  }

  if (bridgeClientPromise) {
    return bridgeClientPromise;
  }

  if (bridgeRecoveryPromise) {
    await bridgeRecoveryPromise;
    if (bridgeClient) {
      return bridgeClient;
    }
    if (bridgeClientPromise) {
      return bridgeClientPromise;
    }
  }

  if (shuttingDown) {
    throw new Error('PixelPilot is shutting down.');
  }

  const canReuseRuntime = Boolean(runtimeProcess && runtimeProcess.isRunning() && runtimeBridgeEndpoints);
  return beginBridgeClientStart({ reuseRuntime: canReuseRuntime });
}

async function waitForBridgeConnected(
  client: RuntimeBridgeClient,
  timeoutMs: number,
): Promise<boolean> {
  if (client.isControlConnected()) {
    return true;
  }

  return new Promise<boolean>((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) {
        return;
      }
      settled = true;
      client.off('connected', onConnected);
      resolve(false);
    }, Math.max(200, timeoutMs));

    const onConnected = (): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      client.off('connected', onConnected);
      resolve(true);
    };

    client.on('connected', onConnected);
  });
}

function scheduleBridgeRecovery(): void {
  if (shuttingDown || bridgeRecoveryInProgress || bridgeRecoveryTimer) {
    return;
  }

  bridgeRecoveryTimer = setTimeout(() => {
    bridgeRecoveryTimer = null;
    void recoverRuntimeBridge();
  }, BRIDGE_RECOVERY_GRACE_MS);
}

function attachBridgeHandlers(client: RuntimeBridgeClient): void {
  client.on('connected', () => {
    clearBridgeRecoveryTimer();
  });

  client.on('disconnected', () => {
    scheduleBridgeRecovery();
  });

  client.on('bridge-error', () => {
    scheduleBridgeRecovery();
  });

  client.on('snapshot', (snapshot) => {
    windowManager?.applySnapshot(snapshot);
  });

  client.on('event', (envelope: RuntimeEventEnvelope) => {
    if (envelope.method !== 'state.snapshot' && envelope.method !== 'state.updated') {
      windowManager?.broadcastEvent(envelope);
    }
  });

  client.on('request', async (envelope: RuntimeEventEnvelope) => {
    const payload = await windowManager?.handleRuntimeRequest(envelope);
    client.respond(envelope.id, envelope.method, payload ?? {});
  });

  client.on('sidecar-frame', (frame) => {
    windowManager?.sendSidecarFrame(frame);
  });

  client.on('runtime-error', (payload) => {
    windowManager?.broadcastEvent({
      id: crypto.randomUUID(),
      kind: 'error',
      method: 'runtime.error',
      payload,
      protocolVersion: 1
    });
  });
}

async function startRuntimeBridgeClient(
  options: { reuseRuntime: boolean },
): Promise<RuntimeBridgeClient> {
  const reuseRuntime = Boolean(options?.reuseRuntime);
  if (
    !reuseRuntime
    || runtimeProcess === null
    || !runtimeProcess.isRunning()
    || runtimeBridgeEndpoints === null
  ) {
    runtimeProcess?.stop();
    runtimeProcess = new RuntimeProcessManager();
    runtimeBridgeEndpoints = await runtimeProcess.start();
  }

  const endpoints = runtimeBridgeEndpoints;
  if (!endpoints) {
    throw new Error('Runtime bridge endpoints are unavailable.');
  }

  const client = new RuntimeBridgeClient(endpoints.controlUrl, endpoints.sidecarUrl);
  attachBridgeHandlers(client);
  client.start();

  if (!reuseRuntime && startupDefaultsStore) {
    const defaults = startupDefaultsStore.loadPersisted();
    if (defaults) {
      try {
        await client.sendCommand('mode.set', { value: defaults.operationMode });
        await client.sendCommand('vision.set', { value: defaults.visionMode });
      } catch (error) {
        console.error('Failed to apply startup defaults to runtime.', error);
      }
    }
  }

  return client;
}

async function recoverRuntimeBridge(): Promise<void> {
  if (shuttingDown || bridgeRecoveryInProgress) {
    return bridgeRecoveryPromise ?? Promise.resolve();
  }

  if (bridgeClient?.isControlConnected()) {
    return;
  }

  bridgeRecoveryInProgress = true;
  bridgeRecoveryPromise = (async () => {
    try {
      bridgeClient?.dispose();
      bridgeClient = null;

      const canReuseRuntime = Boolean(runtimeProcess && runtimeProcess.isRunning() && runtimeBridgeEndpoints);
      if (canReuseRuntime) {
        const reattachedClient = await beginBridgeClientStart({ reuseRuntime: true });
        const reattached = await waitForBridgeConnected(reattachedClient, 3500);
        if (reattached) {
          return;
        }
        reattachedClient.dispose();
        if (bridgeClient === reattachedClient) {
          bridgeClient = null;
        }
      }

      runtimeProcess?.stop();
      runtimeProcess = null;
      runtimeBridgeEndpoints = null;
      const restartedClient = await beginBridgeClientStart({ reuseRuntime: false });
      await waitForBridgeConnected(restartedClient, 5000);
    } catch (error) {
      console.error('Failed to recover runtime bridge.', error);
      scheduleBridgeRecovery();
    } finally {
      bridgeRecoveryInProgress = false;
      bridgeRecoveryPromise = null;
    }
  })();

  return bridgeRecoveryPromise;
}

async function bootstrap(): Promise<void> {
  startupDefaultsStore = new StartupDefaultsStore(app.getPath('userData'));
  windowManager = new WindowManager(
    invokeRuntimeFromMain,
    startupDefaultsStore,
  );

  windowManager.createWindows();
  await beginBridgeClientStart({ reuseRuntime: false });
}

app.whenReady().then(() => {
  void bootstrap().catch((error) => {
    console.error('Failed to bootstrap PixelPilot Electron.', error);
    app.exit(1);
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  shuttingDown = true;
  clearBridgeRecoveryTimer();
  void bridgeClient?.sendCommand('runtime.shutdown').catch(() => undefined);
  bridgeClient?.dispose();
  windowManager?.dispose();
  runtimeProcess?.stop();
  runtimeProcess = null;
  runtimeBridgeEndpoints = null;
});

app.on('second-instance', () => {
  // Bring the primary app UI back when a second launch is attempted.
  void bridgeClient
    ?.sendCommand('shell.setBackgroundHidden', { hidden: false })
    .catch(() => undefined);
});
