import { Link } from 'react-router-dom';
import './DocsPage.css';
import { Footer } from '../components/Footer';

const envVars = [
    { key: 'GEMINI_API_KEY', note: 'Required' },
    { key: 'BACKEND_URL', note: 'Required for backend mode' },
    { key: 'Advanced tuning', note: 'Edit src/config.py for all other runtime values' }
];

const moduleMap = [
    { name: 'src/main.py', detail: 'PySide6 UI, live-only runtime wiring, and app entry.' },
    { name: 'src/agent', detail: 'Shared automation runtime, capture, action execution, and workspace state.' },
    { name: 'src/live/session.py', detail: 'Gemini Live session manager, reconnect flow, voice, and transcript lifecycle.' },
    { name: 'src/live/broker.py', detail: 'Serialized action broker with queued/running/cancel/succeeded states.' },
    { name: 'src/live/tools.py', detail: 'Live tool declarations for UI Automation and mutating action boundaries.' },
    { name: 'src/core', detail: 'Controllers, logging, and app lifecycle glue.' },
    { name: 'src/tools', detail: 'Mouse, keyboard, vision, and app indexing tools.' },
    { name: 'src/skills', detail: 'Browser, media, system, timer skill surfaces.' },
    { name: 'src/desktop', detail: 'Agent Desktop sandbox and preview stream.' },
    { name: 'src/uac', detail: 'Orchestrator and Secure Desktop agent.' },
    { name: 'src/services', detail: 'Gateway and auxiliary service adapters.' },
    { name: 'backend', detail: 'FastAPI service and auth utilities.' }
];

const modeGuide = [
    { mode: 'GUIDANCE', detail: 'Live read-only coaching mode. Pixie tutors the user but does not take desktop actions.' },
    { mode: 'SAFE', detail: 'Live autonomous mode that confirms every mutating desktop action.' },
    { mode: 'AUTO', detail: 'Live autonomous mode without per-action confirmation.' },
    { mode: 'Live connection', detail: 'When available, Live stays enabled and the top-bar control disconnects or reconnects the session instead of toggling AI off.' }
];

const visionGuide = [
    { mode: 'UIA', detail: 'Blind-first UI Automation snapshotting, targeting, text reads, and window focus.' },
    { mode: 'OCR', detail: 'EasyOCR + OpenCV for fast local parsing when semantics are enough.' },
    { mode: 'ROBO', detail: 'Gemini Robotics-ER fallback for semantic UI disambiguation.' }
];

const liveGuide = [
    { mode: 'Action Broker', detail: 'Mutating actions are serialized with queued/running/cancel states.' },
    { mode: 'Voice Runtime', detail: 'Native-audio live stream with mic/speaker queue handling and reconnect continuity.' },
    { mode: 'Stop Safety', detail: 'Live stop requests cancel current actions and pause at safe boundaries.' }
];

export const DocsPage = () => {
    return (
        <div className="docs-page">
            <header className="docs-hero">
                <div className="container">
                    <span className="docs-kicker">PIXELPILOT DOCUMENTATION</span>
                    <h1>Operator Guide, Systems Map, and Runtime Notes</h1>
                    <p>
                        Detailed documentation for the PixelPilot codebase. Powered by the Gemini
                        GenAI SDK with Gemini Live sessions, UI Automation-first execution, and Secure Desktop support.
                    </p>
                    <div className="docs-hero-actions">
                        <Link className="docs-cta primary" to="/">Back to Landing</Link>
                        <a className="docs-cta" href="https://github.com/dagemawinegash/Pixel-Pilot-Project" target="_blank" rel="noreferrer">GitHub</a>
                    </div>
                </div>
            </header>

            <section className="docs-section">
                <div className="container docs-grid">
                    <article className="docs-card">
                        <h2>Install</h2>
                        <p>Run the installer to create the virtual environment and scheduled tasks.</p>
                        <pre>{`$ git clone https://github.com/dagemawinegash/Pixel-Pilot-Project.git\n$ cd Pixel-Pilot-Project\n$ python install.py`}</pre>
                        <div className="docs-note">Optional: <code>python install.py --no-tasks</code></div>
                        <ul>
                            <li>Builds UAC helpers and scheduled tasks.</li>
                            <li>Creates the Desktop shortcut launcher.</li>
                        </ul>
                    </article>

                    <article className="docs-card">
                        <h2>Configuration</h2>
                        <p>Create a <code>.env</code> in the repo root (copy from <code>env.example</code>). The app will not start without <code>GEMINI_API_KEY</code>.</p>
                        <div className="docs-env">
                            {envVars.map((item) => (
                                <div key={item.key} className="env-row">
                                    <span>{item.key}</span>
                                    <span>{item.note}</span>
                                </div>
                            ))}
                        </div>
                    </article>

                    <article className="docs-card">
                        <h2>Run</h2>
                        <p>Use the Desktop shortcut for full UAC coverage or run manually.</p>
                        <div className="docs-split">
                            <div>
                                <span className="pill">Recommended</span>
                                <p>Open the PixelPilot Desktop shortcut.</p>
                            </div>
                            <div>
                                <span className="pill">CLI</span>
                                <pre>$ .\venv\Scripts\python.exe .\src\main.py</pre>
                            </div>
                        </div>
                    </article>
                </div>
            </section>

            <section className="docs-section dark">
                <div className="container">
                    <div className="docs-section-header">
                        <h2>Architecture</h2>
                        <p>End-to-end flow from UI intent to Live execution, verification, and Secure Desktop orchestration.</p>
                    </div>
                    <div className="docs-diagram arch-diagram" role="img" aria-label="PixelPilot architecture diagram">
                        <div className="arch-lane">
                            <h3 className="arch-lane-title">Primary Desktop Runtime</h3>
                            <div className="arch-flow-row">
                                <article className="arch-node">
                                    <h4>Chat UI + Mode Control</h4>
                                    <p>Live toggle, workspace policy</p>
                                </article>
                                <span className="arch-arrow" aria-hidden="true">→</span>
                                <article className="arch-node">
                                    <h4>Live Session Manager</h4>
                                    <p>turn state, reconnect, transcript flow</p>
                                </article>
                                <span className="arch-arrow" aria-hidden="true">→</span>
                                <article className="arch-node">
                                    <h4>Vision Router</h4>
                                    <p>UIA first, OCR/Robo fallback</p>
                                </article>
                                <span className="arch-arrow" aria-hidden="true">→</span>
                                <article className="arch-node">
                                    <h4>Desktop Tools</h4>
                                    <p>mouse, keyboard, app skills</p>
                                </article>
                            </div>
                        </div>

                        <div className="arch-lane-connector" aria-hidden="true">↓</div>

                        <div className="arch-lane">
                            <h3 className="arch-lane-title">Live and Automation Execution</h3>
                            <div className="arch-flow-row arch-flow-row-3">
                                <article className="arch-node">
                                    <h4>Gemini Live Session</h4>
                                    <p>voice, transcript, reconnect</p>
                                </article>
                                <span className="arch-arrow" aria-hidden="true">→</span>
                                <article className="arch-node">
                                    <h4>Live Action Broker</h4>
                                    <p>queued, running, cancel states</p>
                                </article>
                                <span className="arch-arrow" aria-hidden="true">→</span>
                                <article className="arch-node">
                                    <h4>UI Automation</h4>
                                    <p>snapshot, focus, read text</p>
                                </article>
                            </div>
                        </div>

                        <div className="arch-lane-connector" aria-hidden="true">↓</div>

                        <div className="arch-lane">
                            <h3 className="arch-lane-title">Privilege and Integration Layer</h3>
                            <div className="arch-flow-row arch-flow-row-3">
                                <article className="arch-node">
                                    <h4>Agent Desktop Isolation</h4>
                                    <p>optional</p>
                                </article>
                                <span className="arch-arrow" aria-hidden="true">→</span>
                                <article className="arch-node">
                                    <h4>UAC Orchestrator + Secure Desktop Agent</h4>
                                    <p>elevated prompt handling</p>
                                </article>
                                <span className="arch-arrow" aria-hidden="true">→</span>
                                <article className="arch-node">
                                    <h4>FastAPI Backend and Gateway</h4>
                                    <p>optional integration path</p>
                                </article>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            <section className="docs-section">
                <div className="container docs-columns">
                    <div>
                        <h2>Operation Modes</h2>
                        <p>Choose the level of autonomy and when PixelPilot should ask for help.</p>
                        <div className="stack">
                            {modeGuide.map((item) => (
                                <div key={item.mode} className="stack-row">
                                    <span>{item.mode}</span>
                                    <span>{item.detail}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                    <div>
                        <h2>Perception Stack</h2>
                        <p>Execution prefers UI Automation evidence before escalating to visual models.</p>
                        <div className="stack">
                            {visionGuide.map((item) => (
                                <div key={item.mode} className="stack-row">
                                    <span>{item.mode}</span>
                                    <span>{item.detail}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </section>

            <section className="docs-section">
                <div className="container docs-grid">
                    <article className="docs-card">
                        <h2>Gemini Live Runtime</h2>
                        <p>Live sessions are first-class and default-on when direct API mode is available.</p>
                        <ul>
                            <li>Streams voice/text with session continuity and reconnect support.</li>
                            <li>Uses action serialization so mutating calls do not overlap.</li>
                            <li>Supports safe stop requests with broker-aware cancellation.</li>
                        </ul>
                    </article>
                    <article className="docs-card">
                        <h2>UI Automation Core</h2>
                        <p>Blind execution uses UIA primitives before screenshot-heavy reasoning.</p>
                        <ul>
                            <li>Window listing and focus for deterministic app targeting.</li>
                            <li><code>ui_element_id</code> actions for stable click/type workflows.</li>
                            <li>Text extraction via UIA first, OCR fallback when needed.</li>
                        </ul>
                    </article>
                    <article className="docs-card">
                        <h2>Live Orchestration</h2>
                        <p>Live runtime coordinates execution order, continuity, and interruption safety.</p>
                        <ul>
                            {liveGuide.map((item) => (
                                <li key={item.mode}><strong>{item.mode}:</strong> {item.detail}</li>
                            ))}
                        </ul>
                    </article>
                </div>
            </section>

            <section className="docs-section">
                <div className="container docs-grid">
                    <article className="docs-card">
                        <h2>Hotkeys</h2>
                        <p>System-wide controls for quick access.</p>
                        <ul>
                            <li><code>Ctrl+Shift+Z</code> Toggle click-through</li>
                            <li><code>Ctrl+Shift+X</code> Stop current Live turn</li>
                            <li><code>Ctrl+Shift+Q</code> Quit PixelPilot</li>
                        </ul>
                    </article>
                    <article className="docs-card">
                        <h2>Gateway (Optional)</h2>
                        <p>The gateway can submit remote commands into the live runtime when <code>ENABLE_GATEWAY=true</code>.</p>
                        <ul>
                            <li>File: <code>src/services/gateway.py</code></li>
                            <li>Uses the active Gemini Live session for execution</li>
                            <li>Protect with <code>PIXELPILOT_GATEWAY_TOKEN</code></li>
                        </ul>
                    </article>
                    <article className="docs-card">
                        <h2>Troubleshooting</h2>
                        <p>Quick checks for common startup issues.</p>
                        <ul>
                            <li>Verify <code>GEMINI_API_KEY</code> in <code>.env</code>.</li>
                            <li>Check <code>logs/pixelpilot.log</code> for errors.</li>
                            <li>Re-run installer as admin if UAC fails.</li>
                        </ul>
                    </article>
                </div>
            </section>

            <section className="docs-section dark">
                <div className="container">
                    <div className="docs-section-header">
                        <h2>Codebase Map</h2>
                        <p>High-signal modules that shape runtime behavior.</p>
                    </div>
                    <div className="module-grid">
                        {moduleMap.map((item) => (
                            <div key={item.name} className="module-card">
                                <span>{item.name}</span>
                                <p>{item.detail}</p>
                            </div>
                        ))}
                    </div>
                </div>
            </section>

            <section className="docs-section">
                <div className="container docs-grid">
                    <article className="docs-card">
                        <h2>Security + UAC</h2>
                        <p>Secure Desktop prompts are handled by the SYSTEM orchestrator task with one-shot IPC and explicit confirmation before allow.</p>
                        <ul>
                            <li>Tasks: PixelPilotUACOrchestrator and PixelPilotApp.</li>
                            <li>Orchestrator watches per-request UAC IPC files.</li>
                            <li>Allow responses require explicit user confirmation.</li>
                        </ul>
                    </article>
                    <article className="docs-card">
                        <h2>Logging</h2>
                        <p>Runtime logs live under <code>logs/</code>.</p>
                        <ul>
                            <li><code>logs/pixelpilot.log</code> for agent activity.</li>
                            <li><code>logs/app_launch.log</code> for launcher tasks.</li>
                        </ul>
                    </article>
                    <article className="docs-card">
                        <h2>Uninstall</h2>
                        <p>Remove tasks, venv, and cached assets.</p>
                        <pre>$ python uninstall.py</pre>
                        <p className="docs-note">Use flags to keep tasks, venv, or logs.</p>
                    </article>
                </div>
            </section>

            <Footer />
        </div>
    );
};
