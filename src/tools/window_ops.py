import ctypes
from ctypes import wintypes
from typing import List, Dict, Any

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi

# Common system processes to hide from the AI because they aren't "User Apps"
IGNORED_APPS = {
    "TextInputHost.exe",  # Windows Input Experience
    "ShellExperienceHost.exe",  # Start Menu / Overlay
    "SearchHost.exe",  # Windows Search bar
    "sihost.exe",  # System Infrastructure
    "csrss.exe",
    "svchost.exe",
}


def get_process_name(hwnd) -> str:
    """Get the name of the executable for a given window handle."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    h_process = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
    if not h_process:
        return "unknown"

    try:
        buf = ctypes.create_unicode_buffer(1024)
        if psapi.GetModuleBaseNameW(h_process, None, buf, 1024):
            return buf.value
    except Exception:
        pass
    finally:
        kernel32.CloseHandle(h_process)
    return "unknown"


def get_open_windows() -> List[str]:
    """
    Returns a list of visible windows formatted as 'Title (app.exe)'.
    Filters out system background noise.
    """
    titles = []

    def enum_windows_proc(hwnd, lParam):
        if not user32.IsWindowVisible(hwnd):
            return True

        # Skip empty titles
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True

        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value

        if not title or title == "Program Manager":
            return True

        # Get the executable name
        exe_name = get_process_name(hwnd)

        # Filter out noise
        if exe_name in IGNORED_APPS:
            return True

        # Format: "My Document - Notepad (notepad.exe)"
        titles.append(f"{title} ({exe_name})")

        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(enum_windows_proc), 0)

    return titles
