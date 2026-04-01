from __future__ import annotations

import os
import sys
import time
from ctypes import wintypes

import ctypes


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_ROOT = os.path.dirname(CURRENT_DIR)
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from config import Config  # noqa: E402
from uac.ipc import ensure_ipc_root, load_request, pending_request_paths  # noqa: E402


kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32

MAXIMUM_ALLOWED = 0x02000000
PROCESS_QUERY_INFORMATION = 0x0400
SECURITY_IMPERSONATION = 2
TOKEN_PRIMARY = 1
CLAIM_TTL_SECONDS = 120.0

DEBUG_LOG = str(ensure_ipc_root() / "orchestrator.log")


def log_debug(message: str) -> None:
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{time.ctime()}: {message}\n")
    except Exception:
        pass


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_byte * 0),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def enable_privilege(privilege_name: str) -> bool:
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            0x0020 | 0x0008,
            ctypes.byref(token),
        ):
            return False

        luid = wintypes.LARGE_INTEGER()
        if not advapi32.LookupPrivilegeValueW(None, privilege_name, ctypes.byref(luid)):
            return False

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [
                ("Count", wintypes.DWORD),
                ("Luid", wintypes.LARGE_INTEGER),
                ("Attr", wintypes.DWORD),
            ]

        tp = TOKEN_PRIVILEGES(1, luid, 0x00000002)
        if not advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None):
            return False
        return True
    except Exception:
        return False


def get_base_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_winlogon_pid(session_id: int) -> int | None:
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == -1:
        return None

    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32)

    if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
        kernel32.CloseHandle(snapshot)
        return None

    found_pid = None
    while True:
        if entry.szExeFile.lower() == "winlogon.exe":
            current_session = wintypes.DWORD()
            if kernel32.ProcessIdToSessionId(entry.th32ProcessID, ctypes.byref(current_session)):
                if current_session.value == session_id:
                    found_pid = int(entry.th32ProcessID)
                    break
        if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
            break

    kernel32.CloseHandle(snapshot)
    return found_pid


def inject_agent_to_winlogon(session_id: int, request_path: str) -> bool:
    winlogon_pid = get_winlogon_pid(session_id)
    if not winlogon_pid:
        log_debug(f"Could not find winlogon for session {session_id}")
        return False

    log_debug(f"Found WinLogon PID: {winlogon_pid}")
    process = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, winlogon_pid)
    if not process:
        return False

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, MAXIMUM_ALLOWED, ctypes.byref(token)):
        return False

    duplicated = wintypes.HANDLE()
    if not advapi32.DuplicateTokenEx(
        token,
        MAXIMUM_ALLOWED,
        0,
        SECURITY_IMPERSONATION,
        TOKEN_PRIMARY,
        ctypes.byref(duplicated),
    ):
        return False

    base_path = get_base_path()
    agent_exe = os.path.join(base_path, "dist", "agent.exe")

    if os.path.exists(agent_exe):
        cmd_line = f'"{agent_exe}" --request "{request_path}"'
        log_debug(f"Launching compiled agent: {cmd_line}")
    else:
        agent_script = os.path.join(base_path, "agent.py")
        python_exe = sys.executable if not getattr(sys, "frozen", False) else os.path.join(base_path, "venv", "Scripts", "python.exe")
        if not os.path.exists(python_exe):
            python_exe = "python.exe"
        cmd_line = f'"{python_exe}" "{agent_script}" --request "{request_path}"'
        log_debug(f"Launching with Python: {cmd_line}")

    startup = STARTUPINFO()
    startup.cb = ctypes.sizeof(STARTUPINFO)
    startup.lpDesktop = "winsta0\\winlogon"
    proc_info = PROCESS_INFORMATION()

    if advapi32.CreateProcessAsUserW(
        duplicated,
        None,
        cmd_line,
        None,
        None,
        False,
        0,
        None,
        base_path,
        ctypes.byref(startup),
        ctypes.byref(proc_info),
    ):
        log_debug(f"Agent launched. PID={proc_info.dwProcessId}")
        kernel32.CloseHandle(proc_info.hProcess)
        kernel32.CloseHandle(proc_info.hThread)
        return True

    log_debug(f"CreateProcessAsUserW failed: {ctypes.GetLastError()}")
    return False


def main() -> None:
    log_debug("Orchestrator started")
    enable_privilege("SeDebugPrivilege")
    enable_privilege("SeTcbPrivilege")

    claimed: dict[str, float] = {}

    while True:
        now = time.time()
        claimed = {
            nonce: claimed_at
            for nonce, claimed_at in claimed.items()
            if now - claimed_at < CLAIM_TTL_SECONDS
        }

        for request_path in pending_request_paths(
            max_age_seconds=Config.UAC_REQUEST_MAX_AGE_SECONDS
        ):
            request_payload = load_request(
                request_path,
                max_age_seconds=Config.UAC_REQUEST_MAX_AGE_SECONDS,
            )
            if not request_payload:
                continue

            nonce = str(request_payload.get("nonce") or "")
            if nonce in claimed:
                continue

            session_id = kernel32.WTSGetActiveConsoleSessionId()
            if session_id == 0xFFFFFFFF:
                log_debug("No active console session for UAC request")
                continue

            if inject_agent_to_winlogon(int(session_id), str(request_path)):
                claimed[nonce] = time.time()

        time.sleep(0.5)


if __name__ == "__main__":
    main()
