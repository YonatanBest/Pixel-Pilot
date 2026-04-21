import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App.js';
import type {
  PixelPilotApi,
  RendererConfirmationRequest,
  RuntimeEventEnvelope,
  RuntimeSnapshot,
  SidecarFrame,
  WindowKind
} from '@shared/types.js';

type Listener<T> = (value: T) => void;

function createDeferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

function makeSnapshot(overrides: Partial<RuntimeSnapshot> = {}): RuntimeSnapshot {
  return {
    operationMode: 'SAFE',
    visionMode: 'OCR',
    workspace: 'user',
    liveAvailable: true,
    liveUnavailableReason: '',
    liveEnabled: true,
    liveVoiceActive: false,
    liveSessionState: 'connected',
    wakeWordEnabled: true,
    wakeWordState: 'armed',
    wakeWordPhrase: 'Hey Pixie',
    wakeWordUnavailableReason: '',
    userAudioLevel: 0.3,
    assistantAudioLevel: 0.1,
    expanded: true,
    backgroundHidden: false,
    agentViewEnabled: true,
    agentViewRequested: false,
    agentViewVisible: false,
    clickThroughEnabled: false,
    agentPreviewAvailable: true,
    sidecarVisible: false,
    auth: {
      signedIn: true,
      directApi: false,
      email: 'dev@example.com',
      userId: 'user-1',
      backendUrl: 'http://localhost:8000',
      hasApiKey: false,
      needsAuth: false
    },
    recentMessages: [
      {
        id: 'user-1',
        kind: 'user',
        text: 'Open the latest project notes.',
        speaker: 'user',
        final: true
      },
      {
        id: 'assistant-1',
        kind: 'assistant',
        text: 'I can search the current workspace and summarize the notes.',
        speaker: 'assistant',
        final: true
      }
    ],
    recentActionUpdates: [
      {
        action_id: 'open',
        name: 'Open notes',
        status: 'running',
        message: 'Launching the current project workspace'
      }
    ],
    latestSessionContext: {
      available: false
    },
    extensions: {
      status: 'ready',
      pluginCount: 0,
      mcpServerCount: 0,
      toolCount: 0,
      pluginIds: [],
      mcpServerNames: [],
      toolNames: []
    },
    voiceprint: {
      enabled: false,
      enrolled: false,
      available: true,
      status: 'disabled',
      lastScore: null,
      threshold: 0.78,
      uncertainThreshold: 0.72,
      sampleCount: 0,
      pendingSampleCount: 0,
      minEnrollmentSamples: 4,
      embeddingDim: 0,
      modelId: '',
      modelPath: '',
      unavailableReason: ''
    },
    settingsSources: [],
    sessionDirectory: 'C:\\Users\\tester\\.pixelpilot\\sessions',
    lastDoctorReport: {},
    bridgeStatus: 'connected',
    bridgeStatusMessage: '',
    uiPreferences: {
      cornerGlowEnabled: true,
      statusNotchEnabled: false
    },
    ...overrides
  };
}

function setupApi(windowKind: WindowKind, snapshot: RuntimeSnapshot | null): {
  getStartupDefaults: ReturnType<typeof vi.fn>;
  getUiPreferences: ReturnType<typeof vi.fn>;
  setUiPreferences: ReturnType<typeof vi.fn>;
  invokeRuntime: ReturnType<typeof vi.fn>;
  openExternal: ReturnType<typeof vi.fn>;
  setBackgroundHidden: ReturnType<typeof vi.fn>;
  setTrayOnly: ReturnType<typeof vi.fn>;
  setExpanded: ReturnType<typeof vi.fn>;
  toggleSettingsWindow: ReturnType<typeof vi.fn>;
  closeSettingsWindow: ReturnType<typeof vi.fn>;
  setStartupDefaults: ReturnType<typeof vi.fn>;
  updateWindowLayout: ReturnType<typeof vi.fn>;
  resolveConfirmation: ReturnType<typeof vi.fn>;
  quitApp: ReturnType<typeof vi.fn>;
  emitEvent: (event: RuntimeEventEnvelope) => void;
  emitState: (nextSnapshot: RuntimeSnapshot) => void;
  emitConfirmation: (request: RendererConfirmationRequest) => void;
  emitSidecar: (frame: SidecarFrame) => void;
} {
  let stateListener: Listener<RuntimeSnapshot> | null = null;
  let eventListener: Listener<RuntimeEventEnvelope> | null = null;
  let confirmationListener: Listener<RendererConfirmationRequest> | null = null;
  let sidecarListener: Listener<SidecarFrame> | null = null;

  const invokeRuntime = vi.fn().mockResolvedValue({});
  const openExternal = vi.fn().mockResolvedValue(undefined);
  const getUiPreferences = vi.fn().mockResolvedValue(snapshot?.uiPreferences || {
    cornerGlowEnabled: true,
    statusNotchEnabled: false
  });
  const setUiPreferences = vi.fn().mockResolvedValue(snapshot?.uiPreferences || {
    cornerGlowEnabled: true,
    statusNotchEnabled: false
  });
  const getStartupDefaults = vi.fn().mockResolvedValue({
    operationMode: (snapshot?.operationMode || 'AUTO').toUpperCase(),
    visionMode: (snapshot?.visionMode || 'OCR').toUpperCase(),
    hasPersisted: false,
    source: 'runtime'
  });
  const setBackgroundHidden = vi.fn().mockResolvedValue({});
  const setTrayOnly = vi.fn().mockResolvedValue({});
  const setExpanded = vi.fn().mockResolvedValue({});
  const toggleSettingsWindow = vi.fn().mockResolvedValue({ visible: true });
  const closeSettingsWindow = vi.fn().mockResolvedValue({ visible: false });
  const setStartupDefaults = vi.fn().mockResolvedValue({
    operationMode: (snapshot?.operationMode || 'AUTO').toUpperCase(),
    visionMode: (snapshot?.visionMode || 'OCR').toUpperCase(),
    hasPersisted: true,
    source: 'persisted'
  });
  const updateWindowLayout = vi.fn().mockResolvedValue(undefined);
  const resolveConfirmation = vi.fn().mockResolvedValue({});
  const quitApp = vi.fn().mockResolvedValue(undefined);

  const api: PixelPilotApi = {
    getWindowKind: vi.fn().mockResolvedValue(windowKind),
    getSnapshot: vi.fn().mockResolvedValue(snapshot),
    getUiPreferences,
    setUiPreferences,
    getStartupDefaults,
    invokeRuntime,
    openExternal,
    setExpanded,
    setBackgroundHidden,
    setTrayOnly,
    toggleSettingsWindow,
    closeSettingsWindow,
    setStartupDefaults,
    updateWindowLayout,
    resolveConfirmation,
    quitApp,
    onState: (listener) => {
      stateListener = listener;
      return () => {
        stateListener = null;
      };
    },
    onEvent: (listener) => {
      eventListener = listener;
      return () => {
        eventListener = null;
      };
    },
    onConfirmationRequest: (listener) => {
      confirmationListener = listener;
      return () => {
        confirmationListener = null;
      };
    },
    onSidecarFrame: (listener) => {
      sidecarListener = listener;
      return () => {
        sidecarListener = null;
      };
    }
  };

  Object.defineProperty(window, 'pixelPilot', {
    configurable: true,
    value: api
  });

  return {
    getStartupDefaults,
    getUiPreferences,
    setUiPreferences,
    invokeRuntime,
    openExternal,
    setBackgroundHidden,
    setTrayOnly,
    setExpanded,
    toggleSettingsWindow,
    closeSettingsWindow,
    setStartupDefaults,
    updateWindowLayout,
    resolveConfirmation,
    quitApp,
    emitEvent: (event) => {
      act(() => {
        eventListener?.(event);
      });
    },
    emitState: (nextSnapshot) => {
      act(() => {
        stateListener?.(nextSnapshot);
      });
    },
    emitConfirmation: (request) => {
      act(() => {
        confirmationListener?.(request);
      });
    },
    emitSidecar: (frame) => {
      act(() => {
        sidecarListener?.(frame);
      });
    }
  };
}

describe('Electron renderer App', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined)
      }
    });
  });

  it('shows the auth gate and submits a direct API key', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        auth: {
          signedIn: false,
          directApi: false,
          email: '',
          userId: '',
          backendUrl: 'http://localhost:8000',
          hasApiKey: false,
          needsAuth: true
        }
      })
    );

    render(<App />);

    expect(await screen.findByRole('heading', { name: /welcome back/i })).toBeInTheDocument();

    await userEvent.type(
      await screen.findByPlaceholderText(/paste gemini api key \(starts with aiza/i),
      'AIza-demo-key'
    );
    await userEvent.click(screen.getByRole('button', { name: /use api key/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('auth.useApiKey', {
        apiKey: 'AIza-demo-key',
        provider: 'gemini'
      });
    });
  });

  it('submits the configured request provider without Gemini key validation', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        auth: {
          signedIn: false,
          directApi: false,
          email: '',
          userId: '',
          backendUrl: 'http://localhost:8000',
          hasApiKey: false,
          needsAuth: true,
          requestProvider: {
              provider_id: 'openai',
              display_name: 'OpenAI',
              mode_kind: 'request',
              model: 'gpt-4o',
              api_key_env: 'OPENAI_API_KEY',
              has_api_key: false,
              base_url: '',
              capabilities: {
                realtime: false,
                request: true,
                text_input: true,
                image_input: true,
                audio_input: false,
                video_input: false,
                text_output: true,
                audio_output: false,
                tool_calling: true
              }
            }
        }
      })
    );

    render(<App />);

    await userEvent.type(
      await screen.findByPlaceholderText(/paste openai api key/i),
      'sk-demo-key'
    );
    await userEvent.click(screen.getByRole('button', { name: /use api key/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('auth.useApiKey', {
        apiKey: 'sk-demo-key',
        provider: 'openai'
      });
    });
  });

  it('submits Ollama with a base URL and no API key', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        auth: {
          signedIn: false,
          directApi: false,
          email: '',
          userId: '',
          backendUrl: 'http://localhost:8000',
          hasApiKey: false,
          needsAuth: true
        }
      })
    );

    render(<App />);

    await userEvent.selectOptions(await screen.findByLabelText(/model provider/i), 'ollama');
    await userEvent.type(await screen.findByPlaceholderText(/localhost:11434/i), 'http://localhost:11434');
    await userEvent.click(screen.getByRole('button', { name: /use ollama/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('auth.useApiKey', {
        apiKey: '',
        provider: 'ollama',
        baseUrl: 'http://localhost:11434'
      });
    });
  });

  it('starts browser sign-in from the auth gate', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        auth: {
          signedIn: false,
          directApi: false,
          email: '',
          userId: '',
          backendUrl: 'http://localhost:8000',
          hasApiKey: false,
          needsAuth: true
        }
      })
    );

    controls.invokeRuntime.mockResolvedValueOnce({ authUrl: 'https://example.com/auth/sign-in?desktop_state=abc' });

    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: /sign in in browser/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('auth.startBrowserFlow', {
        mode: 'signin'
      });
      expect(controls.openExternal).toHaveBeenCalledWith('https://example.com/auth/sign-in?desktop_state=abc');
    });
  });

  it('submits a browser code from the auth gate and can quit the app', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        auth: {
          signedIn: false,
          directApi: false,
          email: '',
          userId: '',
          backendUrl: 'http://localhost:8000',
          hasApiKey: false,
          needsAuth: true
        }
      })
    );

    render(<App />);

    await userEvent.type(await screen.findByPlaceholderText(/enter browser code/i), 'code-123');
    await userEvent.click(screen.getByRole('button', { name: /continue with browser code/i }));
    await userEvent.click(screen.getByRole('button', { name: /close login dialog/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('auth.exchangeDesktopCode', {
        code: 'code-123'
      });
      expect(controls.quitApp).toHaveBeenCalled();
    });
  });

  it('renders the compact command bar after login', async () => {
    setupApi('overlay', makeSnapshot({ operationMode: 'SAFE' }));

    render(<App />);

    expect(await screen.findByLabelText(/pixelpilot command/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /run command/i })).toBeDisabled();
    expect(screen.queryByText(/process details/i)).not.toBeInTheDocument();
  });

  it('submits from the command bar and hides it after success', async () => {
    const controls = setupApi('overlay', makeSnapshot());

    render(<App />);

    const commandInput = await screen.findByLabelText(/pixelpilot command/i);
    await userEvent.type(commandInput, 'Summarize the open window');
    await userEvent.click(screen.getByRole('button', { name: /run command/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('live.submitText', { text: 'Summarize the open window' });
      expect(controls.setBackgroundHidden).toHaveBeenCalledWith(true);
    });
  });

  it('disables the command submit button while a command is in flight', async () => {
    const pendingSubmit = createDeferred<Record<string, never>>();
    const controls = setupApi('overlay', makeSnapshot());
    controls.invokeRuntime.mockImplementation((method: string, payload?: Record<string, unknown>) => {
      if (method === 'live.submitText') {
        return pendingSubmit.promise;
      }
      return Promise.resolve({ method, payload });
    });

    render(<App />);

    const commandInput = await screen.findByLabelText(/pixelpilot command/i);
    const runButton = screen.getByRole('button', { name: /run command/i });
    await userEvent.type(commandInput, 'Summarize the open window');
    await userEvent.click(runButton);

    await waitFor(() => {
      expect(runButton).toBeDisabled();
      expect(commandInput).toHaveAttribute('readonly');
    });

    await userEvent.click(runButton);
    expect(controls.invokeRuntime).toHaveBeenCalledTimes(1);

    pendingSubmit.resolve({});

    await waitFor(() => {
      expect(controls.setBackgroundHidden).toHaveBeenCalledWith(true);
    });
  });

  it('closes the command bar without submitting', async () => {
    const controls = setupApi('overlay', makeSnapshot());

    render(<App />);

    const commandInput = await screen.findByLabelText(/pixelpilot command/i);
    await userEvent.type(commandInput, '{Escape}');

    await waitFor(() => {
      expect(controls.setBackgroundHidden).toHaveBeenCalledWith(true);
      expect(controls.invokeRuntime).not.toHaveBeenCalledWith('live.submitText', expect.anything());
    });
  });

  it('renders progress and final replies inside the compact command status line', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        liveSessionState: 'acting',
        recentMessages: [
          {
            id: 'assistant-1',
            kind: 'assistant',
            text: 'I found the latest notes.',
            speaker: 'assistant',
            final: true
          }
        ],
        recentActionUpdates: [
          {
            action_id: 'ocr',
            name: 'OCR',
            status: 'running',
            message: 'OCR running on the current screen'
          }
        ]
      })
    );

    render(<App />);

    expect(await screen.findByPlaceholderText(/ocr running on the current screen/i)).toBeInTheDocument();
    expect(screen.queryByText(/i found the latest notes\./i)).not.toBeInTheDocument();

    controls.emitState(
      makeSnapshot({
        liveSessionState: 'connected',
        recentMessages: [
          {
            id: 'assistant-1',
            kind: 'assistant',
            text: 'I found the latest notes.',
            speaker: 'assistant',
            final: true
          }
        ],
        recentActionUpdates: [
          {
            action_id: 'ocr',
            name: 'OCR',
            status: 'done',
            message: 'OCR complete',
            done: true
          }
        ]
      })
    );

    expect(await screen.findByPlaceholderText(/i found the latest notes\./i)).toBeInTheDocument();
  });

  it('shows action failure messages instead of generic failed status in the command bar', async () => {
    setupApi(
      'overlay',
      makeSnapshot({
        liveSessionState: 'connected',
        recentActionUpdates: [
          {
            action_id: 'open',
            name: 'app_open',
            status: 'failed',
            message: 'App launched but window not verified: Google Chrome',
            error: 'failed',
            done: true
          }
        ]
      })
    );

    render(<App />);

    expect(await screen.findByPlaceholderText(/app launched but window not verified: google chrome/i)).toBeInTheDocument();
    expect(screen.queryByText(/^failed$/i)).not.toBeInTheDocument();
  });

  it('renders the redesigned settings tabs and wires account and behavior actions', async () => {
    const controls = setupApi(
      'settings',
      makeSnapshot({
        operationMode: 'SAFE',
        visionMode: 'OCR'
      })
    );

    render(<App />);

    expect(await screen.findByText(/^settings$/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^account$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^behavior$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^voice$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^health$/i })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /sign out/i }));
    await userEvent.click(screen.getByRole('button', { name: /^behavior$/i }));
    await userEvent.click(screen.getByRole('button', { name: /auto/i }));
    await userEvent.click(screen.getByRole('button', { name: /robo/i }));
    await userEvent.click(screen.getByRole('button', { name: /^save current choices$/i }));
    await userEvent.click(screen.getAllByRole('button', { name: /^turn off$/i })[0]);
    await userEvent.click(screen.getByRole('button', { name: /^turn on$/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('auth.logout');
      expect(controls.invokeRuntime).toHaveBeenCalledWith('mode.set', { value: 'AUTO' });
      expect(controls.invokeRuntime).toHaveBeenCalledWith('vision.set', { value: 'ROBO' });
      expect(controls.setStartupDefaults).toHaveBeenCalledWith({
        operationMode: 'SAFE',
        visionMode: 'OCR'
      });
      expect(controls.setUiPreferences).toHaveBeenCalledWith({ cornerGlowEnabled: false });
      expect(controls.setUiPreferences).toHaveBeenCalledWith({ statusNotchEnabled: true });
      expect(controls.updateWindowLayout).toHaveBeenCalledWith(expect.objectContaining({
        height: expect.any(Number),
        width: expect.any(Number)
      }));
    });
  });

  it('renders the voice tab and wires wake and voiceprint enrollment actions', async () => {
    const controls = setupApi(
      'settings',
      makeSnapshot({
        voiceprint: {
          enabled: false,
          enrolled: false,
          available: true,
          status: 'disabled',
          lastScore: null,
          threshold: 0.78,
          uncertainThreshold: 0.72,
          sampleCount: 0,
          pendingSampleCount: 0,
          minEnrollmentSamples: 1,
          embeddingDim: 0,
          modelId: 'speaker-embedding.onnx',
          modelPath: 'C:\\Users\\tester\\.pixelpilot\\models\\speaker-embedding.onnx',
          unavailableReason: ''
        }
      })
    );

    controls.invokeRuntime.mockImplementation((method: string, payload?: Record<string, unknown>) => {
      if (method === 'voiceprint.getStatus') {
        return Promise.resolve({
          voiceprint: {
            enabled: false,
            enrolled: false,
            available: true,
            status: 'disabled',
            lastScore: null,
            threshold: 0.78,
            uncertainThreshold: 0.72,
            sampleCount: 0,
            pendingSampleCount: 0,
            minEnrollmentSamples: 1,
            modelId: 'speaker-embedding.onnx',
            unavailableReason: ''
          }
        });
      }
      if (method === 'voiceprint.recordSample') {
        return Promise.resolve({
          voiceprint: {
            enabled: false,
            enrolled: false,
            available: true,
            status: 'disabled',
            lastScore: null,
            threshold: 0.78,
            uncertainThreshold: 0.72,
            sampleCount: 0,
            pendingSampleCount: 1,
            minEnrollmentSamples: 1,
            modelId: 'speaker-embedding.onnx',
            unavailableReason: ''
          }
        });
      }
      if (method === 'voiceprint.completeEnrollment') {
        return Promise.resolve({
          voiceprint: {
            enabled: true,
            enrolled: true,
            available: true,
            status: 'ready',
            lastScore: null,
            threshold: 0.78,
            uncertainThreshold: 0.72,
            sampleCount: 1,
            pendingSampleCount: 0,
            minEnrollmentSamples: 1,
            modelId: 'speaker-embedding.onnx',
            unavailableReason: ''
          }
        });
      }
      if (method === 'voiceprint.setEnabled') {
        return Promise.resolve({
          voiceprint: {
            enabled: Boolean(payload?.enabled),
            enrolled: true,
            available: true,
            status: 'ready',
            lastScore: null,
            threshold: 0.78,
            uncertainThreshold: 0.72,
            sampleCount: 1,
            pendingSampleCount: 0,
            minEnrollmentSamples: 1,
            modelId: 'speaker-embedding.onnx',
            unavailableReason: ''
          }
        });
      }
      if (method === 'voiceprint.clear') {
        return Promise.resolve({
          voiceprint: {
            enabled: false,
            enrolled: false,
            available: true,
            status: 'disabled',
            lastScore: null,
            threshold: 0.78,
            uncertainThreshold: 0.72,
            sampleCount: 0,
            pendingSampleCount: 0,
            minEnrollmentSamples: 1,
            modelId: 'speaker-embedding.onnx',
            unavailableReason: ''
          }
        });
      }
      return Promise.resolve({});
    });

    render(<App />);

    expect(await screen.findByText(/^settings$/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^voice$/i }));

    expect(await screen.findByText(/train pixelpilot to wake only for your voice/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /record sample/i }));
    await userEvent.click(await screen.findByRole('button', { name: /finish training/i }));
    expect(await screen.findByText(/protected/i)).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole('button', { name: /^turn off$/i })[1]);
    await userEvent.click(screen.getByRole('button', { name: /^clear$/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('voiceprint.recordSample', { seconds: 2 });
      expect(controls.invokeRuntime).toHaveBeenCalledWith('voiceprint.completeEnrollment', undefined);
      expect(controls.invokeRuntime).toHaveBeenCalledWith('voiceprint.setEnabled', { enabled: false });
      expect(controls.invokeRuntime).toHaveBeenCalledWith('voiceprint.clear', undefined);
    });
  });

  it('keeps voiceprint enrollment buttons safe across disabled, busy, and error states', async () => {
    const pendingRecord = createDeferred<{
      voiceprint: RuntimeSnapshot['voiceprint'];
    }>();
    let recordAttempts = 0;
    const initialVoiceprint: RuntimeSnapshot['voiceprint'] = {
      enabled: false,
      enrolled: false,
      available: true,
      status: 'disabled',
      lastScore: null,
      threshold: 0.78,
      uncertainThreshold: 0.72,
      sampleCount: 0,
      pendingSampleCount: 0,
      minEnrollmentSamples: 2,
      embeddingDim: 0,
      modelId: 'speaker-embedding.onnx',
      modelPath: 'C:\\Users\\tester\\.pixelpilot\\models\\speaker-embedding.onnx',
      unavailableReason: ''
    };
    const controls = setupApi('settings', makeSnapshot({ voiceprint: initialVoiceprint }));
    controls.invokeRuntime.mockImplementation((method: string) => {
      if (method === 'voiceprint.getStatus') {
        return Promise.resolve({ voiceprint: initialVoiceprint });
      }
      if (method === 'voiceprint.recordSample') {
        recordAttempts += 1;
        if (recordAttempts === 1) {
          return pendingRecord.promise;
        }
        return Promise.reject(new Error('Microphone unavailable'));
      }
      return Promise.resolve({ voiceprint: initialVoiceprint });
    });

    render(<App />);

    expect(await screen.findByText(/^settings$/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^voice$/i }));

    const finishButton = await screen.findByRole('button', { name: /finish training/i });
    expect(finishButton).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: /record sample/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /recording/i })).toBeDisabled();
      expect(screen.getByRole('button', { name: /turn on/i })).toBeDisabled();
      expect(screen.getByRole('button', { name: /^clear$/i })).toBeDisabled();
    });

    await act(async () => {
      pendingRecord.resolve({
        voiceprint: {
          ...initialVoiceprint,
          pendingSampleCount: 1
        }
      });
      await pendingRecord.promise;
    });

    expect(await screen.findByText('Record 2 clear samples. Current progress: 1 / 2.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /finish training/i })).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: /record sample/i }));

    expect(await screen.findByText(/microphone unavailable/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /record sample/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /finish training/i })).toBeDisabled();
  });

  it('saves startup choices from the behavior tab', async () => {
    const controls = setupApi('settings', makeSnapshot({ operationMode: 'SAFE', visionMode: 'OCR' }));
    controls.getStartupDefaults.mockResolvedValueOnce({
      operationMode: 'SAFE',
      visionMode: 'OCR',
      hasPersisted: false,
      source: 'runtime'
    });

    render(<App />);

    expect(await screen.findByText(/^settings$/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^behavior$/i }));
    expect(await screen.findByText(/^startup$/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /save current choices/i }));

    await waitFor(() => {
      expect(controls.setStartupDefaults).toHaveBeenCalledWith({
        operationMode: 'SAFE',
        visionMode: 'OCR'
      });
    });
  });

  it('disables settings runtime controls while the bridge is starting', async () => {
    const controls = setupApi(
      'settings',
      makeSnapshot({
        bridgeStatus: 'starting',
        bridgeStatusMessage: 'Starting runtime...'
      })
    );

    render(<App />);

    expect(await screen.findByText(/^settings$/i)).toBeInTheDocument();
    expect(screen.getByText(/settings will unlock/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sign out/i })).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: /^behavior$/i }));
    expect(await screen.findByRole('button', { name: /auto/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /save current choices/i })).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: /auto/i }));
    expect(controls.invokeRuntime).not.toHaveBeenCalledWith('mode.set', expect.anything());
  });

  it('shows loading bridge state and disables runtime controls during startup', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        expanded: false,
        bridgeStatus: 'starting',
        bridgeStatusMessage: 'Starting runtime...'
      })
    );

    render(<App />);

    const commandInput = await screen.findByPlaceholderText(/starting runtime/i);
    await userEvent.type(commandInput, 'hello');
    expect(commandInput).toHaveAttribute('readonly');
    expect(screen.getByRole('button', { name: /run command/i })).toBeDisabled();
    expect(controls.invokeRuntime).not.toHaveBeenCalledWith('live.submitText', expect.anything());
  });

  it('renders the glow shell with ambient progress status', async () => {
    setupApi(
      'glow',
      makeSnapshot({
        liveSessionState: 'acting',
        userAudioLevel: 0,
        assistantAudioLevel: 0,
        recentActionUpdates: [
          {
            action_id: 'open',
            name: 'Open notes',
            status: 'running',
            message: 'Launching the current project workspace'
          }
        ]
      })
    );

    render(<App />);

    expect(await screen.findByLabelText(/pixelpilot status glow: launching the current project workspace/i)).toBeInTheDocument();
  });

  it('renders health session controls and wires resume and open-folder actions', async () => {
    const controls = setupApi(
      'settings',
      makeSnapshot({
        latestSessionContext: {
          available: true,
          summaryText: 'Recovered context from the deployment dashboard.',
          lastActivityAt: '2026-04-08T09:45:00Z',
          sessionId: 'sess-123'
        },
        sessionDirectory: 'C:\\Users\\tester\\.pixelpilot\\sessions'
      })
    );

    controls.invokeRuntime.mockImplementation((method: string) => {
      if (method === 'session.getLatestContext') {
        return Promise.resolve({
          session: {
            available: true,
            summaryText: 'Recovered context from the deployment dashboard.',
            lastActivityAt: '2026-04-08T09:45:00Z',
            sessionId: 'sess-123'
          }
        });
      }
      if (method === 'session.resumeLatestContext') {
        return Promise.resolve({
          session: {
            available: true,
            summaryText: 'Resumed context from the deployment dashboard.',
            lastActivityAt: '2026-04-08T10:15:00Z',
            sessionId: 'sess-123'
          }
        });
      }
      if (method === 'session.openFolder') {
        return Promise.resolve({ opened: true });
      }
      return Promise.resolve({});
    });

    render(<App />);

    expect(await screen.findByText(/^settings$/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^health$/i }));

    expect(await screen.findByText(/resume recent context or open the local session folder/i)).toBeInTheDocument();
    expect(screen.getByText(/recovered context from the deployment dashboard\./i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^resume$/i }));
    await userEvent.click(screen.getByRole('button', { name: /open logs/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('session.resumeLatestContext');
      expect(controls.invokeRuntime).toHaveBeenCalledWith('session.openFolder');
    });

    expect(await screen.findByText(/resumed context from the deployment dashboard\./i)).toBeInTheDocument();
  });

  it('renders health connector and checkup actions', async () => {
    const controls = setupApi(
      'settings',
      makeSnapshot({
        extensions: {
          status: 'ready',
          pluginCount: 1,
          mcpServerCount: 1,
          toolCount: 2,
          pluginIds: ['demo'],
          mcpServerNames: ['demo-server'],
          toolNames: ['plugin__demo__echo', 'mcp__demo__list_windows']
        },
        lastDoctorReport: {
          status: 'ok',
          checks: [
            {
              name: 'Audio',
              status: 'ok',
              summary: 'Input and output devices detected.',
              details: {}
            }
          ]
        }
      })
    );

    controls.invokeRuntime.mockImplementation((method: string) => {
      if (method === 'session.getLatestContext') {
        return Promise.resolve({
          session: {
            available: false
          }
        });
      }
      if (method === 'extensions.getSummary') {
        return Promise.resolve({
          extensions: {
            status: 'ready',
            pluginCount: 1,
            mcpServerCount: 1,
            toolCount: 2,
            pluginIds: ['demo'],
            mcpServerNames: ['demo-server'],
            toolNames: ['plugin__demo__echo', 'mcp__demo__list_windows']
          }
        });
      }
      if (method === 'extensions.reload') {
        return Promise.resolve({
          extensions: {
            status: 'ready',
            pluginCount: 1,
            mcpServerCount: 1,
            toolCount: 3,
            pluginIds: ['demo'],
            mcpServerNames: ['demo-server'],
            toolNames: [
              'plugin__demo__echo',
              'plugin__demo__summarize',
              'mcp__demo__list_windows'
            ]
          }
        });
      }
      if (method === 'doctor.run') {
        return Promise.resolve({
          doctor: {
            status: 'ok',
            checks: [
              {
                name: 'Wake word',
                status: 'ok',
                summary: 'Wake word assets are installed.',
                details: {}
              }
            ]
          },
          text: 'PixelPilot doctor: OK\n- Wake word: ok - Wake word assets are installed.'
        });
      }
      return Promise.resolve({});
    });

    render(<App />);

    expect(await screen.findByText(/^settings$/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^health$/i }));

    expect(await screen.findByText(/plugins and mcp servers add local tools/i)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/advanced health details/i));
    expect(screen.getByText(/plugin__demo__echo/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^reload$/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('extensions.reload');
    });

    expect(await screen.findByText(/1 plugins, 1 MCP servers, 3 tools\./i)).toBeInTheDocument();
    expect(await screen.findByText(/plugin__demo__summarize/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /run checkup/i }));
    await userEvent.click(screen.getByRole('button', { name: /copy text/i }));
    await userEvent.click(screen.getByRole('button', { name: /copy json/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('doctor.run');
      expect(navigator.clipboard.writeText).toHaveBeenCalled();
    });
  });

  it('renders the notch shell as passive status only', async () => {
    setupApi(
      'notch',
      makeSnapshot({
        backgroundHidden: true,
        liveEnabled: true,
        liveSessionState: 'disconnected',
        wakeWordEnabled: false,
        wakeWordState: 'disabled',
        recentMessages: [],
        recentActionUpdates: []
      })
    );

    render(<App />);

    expect(await screen.findByText(/type a command to reconnect pixelpilot/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /open command bar/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /stop current task/i })).not.toBeInTheDocument();
  });

  it('shows action failure messages instead of generic failed status in the notch', async () => {
    setupApi(
      'notch',
      makeSnapshot({
        backgroundHidden: true,
        recentActionUpdates: [
          {
            action_id: 'open',
            name: 'app_open',
            status: 'failed',
            message: 'App launched but window not verified: Google Chrome',
            error: 'failed',
            done: true
          }
        ]
      })
    );

    render(<App />);

    expect(await screen.findByText(/app launched but window not verified: google chrome/i)).toBeInTheDocument();
    expect(screen.queryByText(/^failed$/i)).not.toBeInTheDocument();
  });

  it('shows the latest assistant response in the passive notch', async () => {
    setupApi(
      'notch',
      makeSnapshot({
        backgroundHidden: true,
        liveSessionState: 'listening',
        recentActionUpdates: [],
        recentMessages: [
          {
            id: 'assistant-done',
            kind: 'assistant',
            text: 'Chrome is open and ready.',
            speaker: 'assistant',
            final: true
          }
        ]
      })
    );

    render(<App />);

    expect(await screen.findByText(/chrome is open and ready/i)).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('keeps the notch informational during active tasks', async () => {
    setupApi(
      'notch',
      makeSnapshot({
        backgroundHidden: true,
        liveSessionState: 'acting',
        recentActionUpdates: [
          {
            action_id: 'open',
            name: 'Open notes',
            status: 'running',
            message: 'Launching notes'
          }
        ]
      })
    );

    render(<App />);

    expect(await screen.findByText(/launching notes/i)).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('renders runtime errors in the passive notch', async () => {
    const controls = setupApi(
      'notch',
      makeSnapshot({
        backgroundHidden: true,
        recentActionUpdates: []
      })
    );

    render(<App />);

    controls.emitEvent({
      id: 'error-1',
      kind: 'error',
      method: 'runtime.error',
      payload: { message: 'Bridge still connecting' },
      protocolVersion: 1
    });

    expect(await screen.findByText(/bridge still connecting/i)).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('renders the sidecar shell and shows incoming preview frames', async () => {
    const controls = setupApi(
      'sidecar',
      makeSnapshot({
        workspace: 'agent',
        sidecarVisible: true,
        agentViewRequested: true
      })
    );

    render(<App />);

    expect(await screen.findByText(/isolated sidecar preview/i)).toBeInTheDocument();

    controls.emitSidecar({
      width: 4,
      height: 4,
      timestamp: Date.now(),
      dataUrl: 'data:image/jpeg;base64,AAAA'
    });

    expect(await screen.findByAltText(/agent desktop preview/i)).toBeInTheDocument();
  });

  it('wires the sidecar buttons for visibility and returning to the overlay', async () => {
    const controls = setupApi(
      'sidecar',
      makeSnapshot({
        workspace: 'agent',
        sidecarVisible: true,
        agentViewRequested: true
      })
    );

    render(<App />);

    expect(await screen.findByText(/isolated sidecar preview/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /hide sidecar/i }));
    await userEvent.click(screen.getByRole('button', { name: /return to bar/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('agentView.setRequested', { requested: false });
      expect(controls.setBackgroundHidden).toHaveBeenCalledWith(false);
    });
  });

  it('shows a sidecar error when returning to the bar fails', async () => {
    const controls = setupApi(
      'sidecar',
      makeSnapshot({
        workspace: 'agent',
        sidecarVisible: true,
        agentViewRequested: true
      })
    );
    controls.setBackgroundHidden.mockRejectedValueOnce(new Error('Runtime reconnecting'));

    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: /return to bar/i }));

    expect(await screen.findByText(/runtime reconnecting/i)).toBeInTheDocument();
  });

  it('opens a confirmation modal and resolves approval', async () => {
    const controls = setupApi('overlay', makeSnapshot());

    render(<App />);

    expect(await screen.findByLabelText(/pixelpilot command/i)).toBeInTheDocument();

    controls.emitConfirmation({
      id: 'confirm-1',
      title: 'Approve action',
      text: 'Allow PixelPilot to click the Send button?'
    });

    expect(await screen.findByText(/approve action/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /approve/i }));

    await waitFor(() => {
      expect(controls.resolveConfirmation).toHaveBeenCalledWith('confirm-1', { approved: true });
    });
  });
});
