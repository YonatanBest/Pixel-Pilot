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
            provider_id: 'openai'
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

  it('renders the unified settings hub and wires the general section actions', async () => {
    const controls = setupApi(
      'settings',
      makeSnapshot({
        operationMode: 'SAFE',
        visionMode: 'OCR'
      })
    );

    render(<App />);

    expect(await screen.findByText(/settings hub/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^general$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^startup$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^sessions$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^extensions$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^diagnostics$/i })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^auto$/i }));
    await userEvent.click(screen.getByRole('button', { name: /^robo$/i }));
    await userEvent.click(screen.getByRole('button', { name: /corner glow/i }));
    await userEvent.click(screen.getByRole('button', { name: /status notch/i }));
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('mode.set', { value: 'AUTO' });
      expect(controls.invokeRuntime).toHaveBeenCalledWith('vision.set', { value: 'ROBO' });
      expect(controls.setUiPreferences).toHaveBeenCalledWith({ cornerGlowEnabled: false });
      expect(controls.setUiPreferences).toHaveBeenCalledWith({ statusNotchEnabled: true });
      expect(controls.invokeRuntime).toHaveBeenCalledWith('auth.logout', undefined);
    });
  });

  it('switches to the startup section and saves persisted defaults', async () => {
    const controls = setupApi('settings', makeSnapshot({ operationMode: 'SAFE', visionMode: 'OCR' }));
    controls.getStartupDefaults.mockResolvedValueOnce({
      operationMode: 'SAFE',
      visionMode: 'OCR',
      hasPersisted: false,
      source: 'runtime'
    });

    render(<App />);

    expect(await screen.findByText(/settings hub/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^startup$/i }));
    expect(await screen.findByText(/^startup defaults$/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^auto$/i }));
    await userEvent.click(screen.getByRole('button', { name: /^robo$/i }));
    await userEvent.click(screen.getByRole('button', { name: /save startup defaults/i }));

    await waitFor(() => {
      expect(controls.setStartupDefaults).toHaveBeenCalledWith({
        operationMode: 'AUTO',
        visionMode: 'ROBO'
      });
    });
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

  it('renders the sessions section and wires resume and open-folder actions', async () => {
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

    expect(await screen.findByText(/settings hub/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^sessions$/i }));

    expect(await screen.findByText(/manual resume and session log access for the current workspace/i)).toBeInTheDocument();
    expect(screen.getByText(/recovered context from the deployment dashboard\./i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^resume last context$/i }));
    await userEvent.click(screen.getByRole('button', { name: /open session logs/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('session.resumeLatestContext');
      expect(controls.invokeRuntime).toHaveBeenCalledWith('session.openFolder');
    });

    expect(await screen.findByText(/resumed context from the deployment dashboard\./i)).toBeInTheDocument();
  });

  it('renders the extensions and diagnostics sections and handles their actions', async () => {
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

    expect(await screen.findByText(/settings hub/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^extensions$/i }));

    expect(await screen.findByText(/plugin and mcp tool discovery with explicit local opt-in/i)).toBeInTheDocument();
    expect(screen.getByText(/plugin__demo__echo/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /reload extensions/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('extensions.reload');
    });

    expect(await screen.findByText(/plugin__demo__summarize/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^diagnostics$/i }));
    expect(await screen.findByText(/run the shared doctor pipeline and copy the latest runtime health report/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /run diagnostics/i }));
    await userEvent.click(screen.getByRole('button', { name: /copy doctor text/i }));
    await userEvent.click(screen.getByRole('button', { name: /copy doctor json/i }));

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
