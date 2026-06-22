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
# iframes (captcha/anti-bot engines) stay separate targets and auto-attach.
FLAGS = ("--remote-debugging-pipe=JSON", "--no-first-run", "--no-default-browser-check",
         "--disable-blink-features=AutomationControlled", "--disable-features=Translate")


class Browser:
    def __init__(self, headless=True, executable=None, proxy=None, extra_flags=None):
        self.executable = executable or find_chrome()
        self.user_data_dir = tempfile.mkdtemp(prefix="ghostwire-")

        # Chrome reads commands from fd 3, writes events to fd 4
        chrome_in, parent_out = os.pipe()
        parent_in, chrome_out = os.pipe()
        for fd in (chrome_in, parent_out, parent_in, chrome_out):
            os.set_inheritable(fd, True)

        flags = [self.executable, *FLAGS, f"--user-data-dir={self.user_data_dir}"]
        if headless:
            flags.append("--headless=new")
        if proxy:
            flags.append(f"--proxy-server={proxy}")
        if extra_flags:
            flags.extend(extra_flags)

        def place_fds():
            os.dup2(chrome_in, 3); os.dup2(chrome_out, 4)
            os.set_inheritable(3, True); os.set_inheritable(4, True)

        self.process = subprocess.Popen(
            flags, pass_fds=(chrome_in, chrome_out, 3, 4), preexec_fn=place_fds,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(chrome_in); os.close(chrome_out)
        self.cdp = PipeConnection(parent_out, parent_in)

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
