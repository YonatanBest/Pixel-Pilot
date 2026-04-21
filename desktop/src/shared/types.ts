export type ProviderCapabilities = {
  realtime: boolean;
  request: boolean;
  text_input: boolean;
  image_input: boolean;
  audio_input: boolean;
  video_input: boolean;
  text_output: boolean;
  audio_output: boolean;
  tool_calling: boolean;
};

export type ProviderInfo = {
  provider_id: string;
  display_name: string;
  mode_kind: string;
  model: string;
  api_key_env: string;
  has_api_key: boolean;
  base_url: string;
  capabilities: ProviderCapabilities;
};

export type AuthState = {
  signedIn: boolean;
  directApi: boolean;
  email: string;
  userId: string;
  backendUrl: string;
  hasApiKey: boolean;
  needsAuth: boolean;
  requestProvider?: ProviderInfo;
  liveProvider?: ProviderInfo;
};

export type MessageEntry = {
  id: string;
  kind: string;
  text: string;
  speaker: string;
  final: boolean;
};

export type LiveStatus = {
  level: 'idle' | 'info' | 'warning' | 'error';
  code: string;
  message: string;
  source: string;
};

export type ActionUpdate = {
  action_id?: string;
  name?: string;
  status?: string;
  message?: string;
  error?: string;
  done?: boolean;
};

export type DoctorCheck = {
  name: string;
  status: string;
  summary: string;
  details: Record<string, unknown>;
};

export type DoctorReport = {
  status: string;
  checks: DoctorCheck[];
};

export type SessionContextSummary = {
  available: boolean;
  workspaceFingerprint?: string;
  sessionId?: string;
  lastActivityAt?: string;
  summaryText?: string;
  resumePayload?: Record<string, unknown>;
  tail?: Record<string, unknown>[];
  logPath?: string;
};

export type ExtensionSummary = {
  status?: string;
  pluginCount: number;
  mcpServerCount: number;
  toolCount: number;
  pluginIds?: string[];
  mcpServerNames?: string[];
  toolNames: string[];
};

export type VoiceprintStatus = {
  enabled: boolean;
  enrolled: boolean;
  available: boolean;
  status: string;
  lastScore: number | null;
  lastDecision?: string;
  lastReason?: string;
  threshold: number;
  uncertainThreshold?: number;
  sampleCount: number;
  pendingSampleCount?: number;
  minEnrollmentSamples?: number;
  embeddingDim?: number;
  modelId?: string;
  modelPath?: string;
  unavailableReason: string;
};

export type BridgeStatus = 'starting' | 'connected' | 'recovering' | 'failed';

export type UiPreferences = {
  cornerGlowEnabled: boolean;
  statusNotchEnabled: boolean;
};

export type RuntimeSnapshot = {
  operationMode: 'GUIDANCE' | 'SAFE' | 'AUTO';
  visionMode: 'ROBO' | 'OCR';
  workspace: 'user' | 'agent';
  liveAvailable: boolean;
  liveUnavailableReason: string;
  liveEnabled: boolean;
  liveVoiceActive: boolean;
  liveSessionState: string;
  liveStatus?: LiveStatus;
  wakeWordEnabled: boolean;
  wakeWordState: string;
  wakeWordPhrase: string;
  wakeWordUnavailableReason: string;
  userAudioLevel: number;
  assistantAudioLevel: number;
  expanded: boolean;
  backgroundHidden: boolean;
  agentViewEnabled: boolean;
  agentViewRequested: boolean;
  agentViewVisible: boolean;
  clickThroughEnabled: boolean;
  agentPreviewAvailable: boolean;
  sidecarVisible: boolean;
  auth: AuthState;
  recentMessages: MessageEntry[];
  recentActionUpdates: ActionUpdate[];
  latestSessionContext: SessionContextSummary;
  extensions: ExtensionSummary;
  voiceprint: VoiceprintStatus;
  settingsSources: string[];
  settingsValidationErrors?: Record<string, string>[];
  sessionDirectory: string;
  lastDoctorReport: DoctorReport | Record<string, never>;
  bridgeStatus: BridgeStatus;
  bridgeStatusMessage: string;
  uiPreferences: UiPreferences;
};

export type RuntimeEventEnvelope = {
  id: string;
  kind: 'event' | 'request' | 'response' | 'error' | 'command';
  method: string;
  payload: Record<string, unknown>;
  protocolVersion: number;
};

export type RendererConfirmationRequest = {
  id: string;
  title: string;
  text: string;
};

export type StartupDefaultsSnapshot = {
  operationMode: 'GUIDANCE' | 'SAFE' | 'AUTO';
  visionMode: 'ROBO' | 'OCR';
  hasPersisted: boolean;
  source: 'persisted' | 'runtime' | 'fallback';
};

export type SidecarFrame = {
  width: number;
  height: number;
  timestamp: number;
  dataUrl: string;
};

export type WindowKind =
  | 'overlay'
  | 'notch'
  | 'glow'
  | 'sidecar'
  | 'settings';

export type RuntimeCommandPayload = Record<string, unknown>;

export type WindowLayoutPayload = {
  width: number;
  height: number;
};

export type PixelPilotApi = {
  getWindowKind: () => Promise<WindowKind>;
  getSnapshot: () => Promise<RuntimeSnapshot | null>;
  getUiPreferences: () => Promise<UiPreferences>;
  setUiPreferences: (payload: Partial<UiPreferences>) => Promise<UiPreferences>;
  getStartupDefaults: () => Promise<StartupDefaultsSnapshot>;
  invokeRuntime: (method: string, payload?: RuntimeCommandPayload) => Promise<Record<string, unknown>>;
  openExternal: (url: string) => Promise<void>;
  setExpanded: (expanded: boolean) => Promise<Record<string, unknown>>;
  setBackgroundHidden: (hidden: boolean) => Promise<Record<string, unknown>>;
  setTrayOnly: (enabled: boolean) => Promise<Record<string, unknown>>;
  toggleSettingsWindow: () => Promise<{ visible: boolean }>;
  closeSettingsWindow: () => Promise<{ visible: boolean }>;
  setStartupDefaults: (payload: {
    operationMode: 'GUIDANCE' | 'SAFE' | 'AUTO';
    visionMode: 'ROBO' | 'OCR';
  }) => Promise<StartupDefaultsSnapshot>;
  updateWindowLayout: (payload: WindowLayoutPayload) => Promise<void>;
  resolveConfirmation: (id: string, payload: RuntimeCommandPayload) => Promise<Record<string, unknown>>;
  quitApp: () => Promise<void>;
  onState: (listener: (snapshot: RuntimeSnapshot) => void) => () => void;
  onEvent: (listener: (event: RuntimeEventEnvelope) => void) => () => void;
  onConfirmationRequest: (listener: (request: RendererConfirmationRequest) => void) => () => void;
  onSidecarFrame: (listener: (frame: SidecarFrame) => void) => () => void;
};
