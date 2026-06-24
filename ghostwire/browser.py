import os, sys, shutil, tempfile, subprocess

from .cdp import PipeConnection

ENV_OVERRIDES = ("GHOSTWIRE_CHROME", "CHROME_PATH", "CHROME_BIN")

LOCATIONS = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
        "/Applications/Google Chrome Dev.app/Contents/MacOS/Google Chrome Dev",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ],
    "linux": [
        "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome-beta", "/usr/bin/google-chrome-unstable",
        "/opt/google/chrome/chrome", "/usr/bin/chromium",
        "/usr/bin/chromium-browser", "/snap/bin/chromium",
    ],
    "win32": [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Chromium\Application\chrome.exe"),
    ],
}

PATH_NAMES = ("google-chrome", "google-chrome-stable", "google-chrome-beta",
              "chromium", "chromium-browser", "chrome")


def find_chrome():
    for var in ENV_OVERRIDES:
        if (p := os.environ.get(var)) and os.path.exists(p):
            return p
    for p in LOCATIONS.get(sys.platform, []):
        if p and os.path.exists(p):
            return p
    for name in PATH_NAMES:
        if (p := shutil.which(name)):
            return p
    raise RuntimeError("no Chrome found; set GHOSTWIRE_CHROME or install Google Chrome")


# few non-default switches → less to fingerprint. site isolation left ON so cross-origin
# iframes (captcha/anti-bot engines) stay separate targets and auto-attach. the throttling
# switches are process-level (not JS-observable) and stop a backgrounded target's timers from
# dropping to 1Hz when we open a second page.
FLAGS = ("--remote-debugging-pipe=JSON", "--no-first-run", "--no-default-browser-check",
         "--disable-blink-features=AutomationControlled", "--disable-features=Translate",
         "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
         "--disable-renderer-backgrounding")


class Browser:
    def __init__(self, headless=True, executable=None, proxy=None, extra_flags=None):
        self.executable = executable or find_chrome()
        self.user_data_dir = tempfile.mkdtemp(prefix="ghostwire-")

        flags = [self.executable, *FLAGS, f"--user-data-dir={self.user_data_dir}"]
        if headless:
            flags.append("--headless=new")
        if proxy:
            flags.append(f"--proxy-server={proxy}")
        if extra_flags:
            flags.extend(extra_flags)

        spawn = _spawn_windows if sys.platform == "win32" else _spawn_posix
        self.process, command_fd, event_fd = spawn(flags)
        self.cdp = PipeConnection(command_fd, event_fd)

    def close(self):
        try:
            self.cdp.close()
        except Exception:
            pass
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        shutil.rmtree(self.user_data_dir, ignore_errors=True)


# Chrome reads CDP commands from fd 3, writes events to fd 4, on every platform.
def _spawn_posix(flags):
    chrome_in, parent_out = os.pipe()
    parent_in, chrome_out = os.pipe()
    for fd in (chrome_in, parent_out, parent_in, chrome_out):
        os.set_inheritable(fd, True)

    def place_fds():
        os.dup2(chrome_in, 3); os.dup2(chrome_out, 4)
        os.set_inheritable(3, True); os.set_inheritable(4, True)

    process = subprocess.Popen(
        flags, pass_fds=(chrome_in, chrome_out, 3, 4), preexec_fn=place_fds,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(chrome_in); os.close(chrome_out)
    return process, parent_out, parent_in


# Windows has no preexec_fn: the child's msvcrt rebuilds its fd table from a CRT
# inherited-fd block passed as STARTUPINFO.lpReserved2, which subprocess/_winapi do
# not expose — so launch via ctypes CreateProcessW with a hand-built block.
def _spawn_windows(flags):
    import msvcrt, _winapi, struct, ctypes
    from ctypes import wintypes

    cmd_r, cmd_w = _winapi.CreatePipe(0, 0)   # child reads cmd_r at fd 3; parent writes cmd_w
    evt_r, evt_w = _winapi.CreatePipe(0, 0)   # child writes evt_w at fd 4; parent reads evt_r
    os.set_handle_inheritable(cmd_r, True)
    os.set_handle_inheritable(evt_w, True)

    # block = int32 count + per-fd CRT flag bytes + pointer-sized handles.
    INVALID = (1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1
    FOPEN, FPIPE = 0x01, 0x08
    fd_flags = bytes([0, 0, 0, FOPEN | FPIPE, FOPEN | FPIPE])
    handles = [INVALID, INVALID, INVALID, cmd_r, evt_w]
    block = struct.pack("I", len(handles)) + fd_flags + b"".join(struct.pack("P", h) for h in handles)

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                    ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                    ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                    ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                    ("hStdError", wintypes.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    buf = (ctypes.c_byte * len(block)).from_buffer_copy(block)
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    si.cbReserved2 = len(block)
    si.lpReserved2 = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
    pi = PROCESS_INFORMATION()

    CREATE_NO_WINDOW = 0x08000000
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateProcessW.restype = wintypes.BOOL
    ok = kernel32.CreateProcessW(
        None, ctypes.create_unicode_buffer(subprocess.list2cmdline(flags)), None, None,
        True, CREATE_NO_WINDOW, None, None, ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise ctypes.WinError()

    kernel32.CloseHandle(pi.hThread)
    _winapi.CloseHandle(cmd_r); _winapi.CloseHandle(evt_w)   # the child owns these now
    command_fd = msvcrt.open_osfhandle(cmd_w, os.O_BINARY)
    event_fd = msvcrt.open_osfhandle(evt_r, os.O_BINARY)
    return _WinProcess(pi.hProcess, pi.dwProcessId), command_fd, event_fd


class _WinProcess:
    # subprocess.Popen-shaped enough for Browser.close().
    def __init__(self, handle, pid):
        self._handle, self.pid = handle, pid

    def terminate(self):
        import ctypes
        ctypes.windll.kernel32.TerminateProcess(self._handle, 1)

    kill = terminate

    def wait(self, timeout=None):
        import ctypes
        ms = 0xFFFFFFFF if timeout is None else int(timeout * 1000)
        ctypes.windll.kernel32.WaitForSingleObject(self._handle, ms)
