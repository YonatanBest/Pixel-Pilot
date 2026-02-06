import sys
import time
import ctypes
import os
from ctypes import wintypes

kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32
wtsapi32 = ctypes.windll.wtsapi32

MAXIMUM_ALLOWED = 0x02000000
SecurityImpersonation = 2
TokenPrimary = 1
PROCESS_QUERY_INFORMATION = 0x0400

def log_debug(msg):
    try:
        path = r"C:\Windows\Temp\uac_debug.log"
        with open(path, "a") as f:
            f.write(f"{time.ctime()}: {msg}\n")
    except: pass

class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR), ('lpDesktop', wintypes.LPWSTR),
        ('lpTitle', wintypes.LPWSTR), ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD),
        ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD), ('dwXCountChars', wintypes.DWORD),
        ('dwYCountChars', wintypes.DWORD), ('dwFillAttribute', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
        ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD), ('lpReserved2', ctypes.c_byte * 0),
        ('hStdInput', wintypes.HANDLE), ('hStdOutput', wintypes.HANDLE), ('hStdError', wintypes.HANDLE),
    ]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE), ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD)]

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
        ("szExeFile", ctypes.c_wchar * 260)
    ]

def enable_privilege(privilege_name):
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(token)): return False
        luid = wintypes.LARGE_INTEGER()
        if not advapi32.LookupPrivilegeValueW(None, privilege_name, ctypes.byref(luid)): return False
        
        class TP(ctypes.Structure):
            _fields_ = [("Count", wintypes.DWORD), ("Luid", wintypes.LARGE_INTEGER), ("Attr", wintypes.DWORD)]
        
        tp = TP(1, luid, 0x00000002)
        if not advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None): return False
        return True
    except: return False

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def get_winlogon_pid(session_id):
    TH32CS_SNAPPROCESS = 0x00000002
    h_snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h_snapshot == -1: return None

    pe32 = PROCESSENTRY32()
    pe32.dwSize = ctypes.sizeof(PROCESSENTRY32)

    if not kernel32.Process32FirstW(h_snapshot, ctypes.byref(pe32)):
        kernel32.CloseHandle(h_snapshot)
        return None

    found_pid = None
    while True:
        if pe32.szExeFile.lower() == "winlogon.exe":
            curr_pid_session = wintypes.DWORD()
            if kernel32.ProcessIdToSessionId(pe32.th32ProcessID, ctypes.byref(curr_pid_session)):
                if curr_pid_session.value == session_id:
                    found_pid = pe32.th32ProcessID
                    break
        if not kernel32.Process32NextW(h_snapshot, ctypes.byref(pe32)):
            break
            
    kernel32.CloseHandle(h_snapshot)
    return found_pid

def inject_agent_to_winlogon(session_id):
    winlogon_pid = get_winlogon_pid(session_id)
        
    if not winlogon_pid:
        log_debug(f"Could not find winlogon for session {session_id}")
        return False
    
    log_debug(f"Found WinLogon PID: {winlogon_pid}")

    h_process = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, winlogon_pid)
    if not h_process: return False
    
    h_token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(h_process, MAXIMUM_ALLOWED, ctypes.byref(h_token)): return False
    
    h_dup_token = wintypes.HANDLE()
    if not advapi32.DuplicateTokenEx(h_token, MAXIMUM_ALLOWED, 0, SecurityImpersonation, TokenPrimary, ctypes.byref(h_dup_token)): return False
    
    base_path = get_base_path()
    
    agent_exe = os.path.join(base_path, "dist", "agent.exe")
    
    if not os.path.exists(agent_exe):
        log_debug(f"Agent EXE not found at {agent_exe}, falling back to Python script")
        agent_script = os.path.join(base_path, "agent.py")
        if getattr(sys, 'frozen', False):
            python_exe = os.path.join(base_path, "venv", "Scripts", "python.exe")
            if not os.path.exists(python_exe): 
                python_exe = "python.exe"
        else:
            python_exe = sys.executable
        cmd_line = f'"{python_exe}" "{agent_script}"'
        log_debug(f"Launching with Python: {cmd_line}")
    else:
        cmd_line = f'"{agent_exe}"'
        log_debug(f"Launching compiled agent: {cmd_line}")
        
    si = STARTUPINFO(); si.cb = ctypes.sizeof(STARTUPINFO); si.lpDesktop = "winsta0\\winlogon"
    pi = PROCESS_INFORMATION()

    if advapi32.CreateProcessAsUserW(h_dup_token, None, cmd_line, None, None, False, 0, None, base_path, ctypes.byref(si), ctypes.byref(pi)):
        log_debug(f"Agent Launched! PID: {pi.dwProcessId}")
        kernel32.CloseHandle(pi.hProcess)
        kernel32.CloseHandle(pi.hThread)
        return True
    else:
        log_debug(f"CreateProcess Failed: {ctypes.GetLastError()}")
        return False

def main():
    log_debug("Orchestrator V3 Started")
    enable_privilege("SeDebugPrivilege")
    enable_privilege("SeTcbPrivilege")
    
    base_path = get_base_path()
    trigger_file = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp", "uac_trigger.txt")
    
    log_debug(f"Watching: {trigger_file}")

    while True:
        if os.path.exists(trigger_file):
            try: 
                time.sleep(0.1)
                os.remove(trigger_file)
            except: pass
            
            log_debug("Trigger Detected!")
            
            time.sleep(1.5)
            
            session_id = kernel32.WTSGetActiveConsoleSessionId()
            if session_id != 0xFFFFFFFF:
                inject_agent_to_winlogon(session_id)
        
        time.sleep(0.5)

if __name__ == "__main__":
    main()