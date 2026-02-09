# PixelPilot

![PixelPilot Logo](src/logos/pixelpilot-logo-creative.svg)

**Pilot Your Pixels.**

PixelPilot is a high-performance Windows desktop automation agent powered by **Gemini (Google GenAI SDK)** and advanced computer vision. It transforms natural language commands into precise mouse and keyboard actions, orchestrating a hybrid pipeline of vision-based and blind control across multiple isolated desktop workspaces.

## Architecture

![High-Level Design View](src/logos/System-Architecture.png)

> [View Detailed Architecture Diagram](src/logos/System-Architecture_Detailed.png)

## Key Features

### 🚀 Hybrid Planning & Execution
- **Native Tool Calling**: Uses Gemini's native function calling for robust, structured action planning (replaces legacy JSON parsing).
- **Turbo Mode (Enabled by Default)**: Optimizes planning by batching multiple stable actions into a single execution sequence.
- **Blind Mode**: The agent can plan and act without screenshots (using OS skills and hotkeys) when visual context is not required, automatically switching back to vision when needed.

### 👁️ Advanced Vision System
- **Lazy Vision Pipeline**: Implements a tiered approach—tries lightweight local OCR (EasyOCR + OpenCV) first and falls back to **Gemini Robotics-ER** for complex semantic understanding or unknown icons.
- **Incremental Screenshots**: Only captures and analyzes new screenshots when the screen state has changed, significantly reducing API latency.
- **Dynamic Resolution**: Automatically requests high-resolution media from the brain when magnification is active or confidence is low.
- **Magnification & Reference Sheets**: Zoom into dense UI regions and use visual coordinate reference sheets to solve small-element ambiguity.

### 🖥️ Desktop Orchestration
- **Agent Desktop (Isolated Workspaces)**: Create and switch between the live `user` desktop and an isolated `agent` desktop for background tasks.
- **Chrome & Excel Isolation**: Automatically applies separate user data directories for Chrome and forces new instances for Excel to prevent session leakage.
- **Sidecar Preview**: A high-performance, read-only live preview (supporting up to 30 FPS) of the Agent Desktop attached to the main UI.

### 🛡️ System Integration & Security
- **UAC / Secure Desktop Support**: A dedicated SYSTEM service (UAC Orchestrator) allows the agent to see and interact with Secure Desktop prompts.
- **Smart App Indexer**: Uses a multi-source index (Start Menu, Registry, Running Processes) to launch applications reliably without manual UI searching.
- **Loop Detection & Reflexion**: Detects repeated actions using perceptual hashing and uses "reflexion" logic to suggest alternatives or ask for clarification.
- **Task Verification**: Performs optional post-task screen analysis to confirm the user's goal was actually achieved.

## Quick Start

### 1. Installation

Run the installer to set up the Python environment, virtual environment (`venv`), and (optionally) the UAC orchestrator.

```powershell
python install.py
```

**What happens:**
- Creates a `venv` and installs all dependencies from `requirements.txt`.
- Compiles the UAC helper executables.
- Registers the `PixelPilotUACOrchestrator` SYSTEM service.
- Creates a Desktop shortcut for one-click launching.

### 2. Configuration

Create a `.env` file in the root directory (you can use `env.example` as a template):

```env
GEMINI_API_KEY=your_api_key_here
GEMINI_MODEL=gemini-3-flash-preview

# Operation Mode: guide | safe | auto
DEFAULT_MODE=auto

# Vision Mode: robo | ocr
VISION_MODE=robo

# Workspace: user | agent
DEFAULT_WORKSPACE=user
```

### 3. Usage

- **Launch**: Use the Desktop shortcut or run `.\venv\Scripts\python.exe .\src\main.py`.
- **Interacting**: Type or speak your command (e.g., "Install VS Code", "Play the next song on Spotify", "Open Device Manager as Admin").
- **Hotkeys**:
    - `Ctrl+Shift+Z`: Toggle UI click-through (Interactive vs. Overlay mode).
    - `Ctrl+Shift+X`: Stop the current task.
    - `Ctrl+Shift+Q`: Quit PixelPilot.

## Technical Components

- **Main App (`src/main.py`)**: The PySide6 GUI and event loop orchestrating the agent.
- **Agent Orchestrator (`src/agent/agent.py`)**: The core control loop managing vision, planning, and execution.
- **Brain (`src/agent/brain.py`)**: Handles multimodal integration with Gemini and action planning logic.
- **Desktop Manager (`src/desktop/desktop_manager.py`)**: Manages isolated desktops and Win32 process injection.
- **UAC Service (`src/uac/`)**: SYSTEM-level bridging for Secure Desktop interaction.

### Uninstall
To remove all components, including scheduled tasks and the virtual environment:
```powershell
python uninstall.py
```

## License

MIT

---
**Powered by Gemini + Advanced Computer Vision.**

