"""
browser.py — launch (or attach to) Chrome with a remote-debugging endpoint and
hand back a CDP client bound to a fresh page target.

Stealth-minded launch flags; a real Chrome binary is preferred over bundled Chromium.
"""
import os
import json
import time
import socket
import shutil
import tempfile
import subprocess
import urllib.request

from .cdp import CDP

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def find_chrome():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    raise RuntimeError("no Chrome/Chromium binary found")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Browser:
    def __init__(self, headless=True, chrome=None, proxy=None, port=None,
                 extra_args=None):
        self.chrome = chrome or find_chrome()
        self.port = port or _free_port()
        self.userdir = tempfile.mkdtemp(prefix="ghostwire-")
        args = [
            self.chrome,
            f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={self.userdir}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            # NOTE: do not disable site-per-process; OOPIFs must stay separate targets
            # so cross-origin iframes (where engines like captchas run) auto-attach.
            "--disable-features=Translate",
        ]
        if headless:
            args.append("--headless=new")
        if proxy:
            args.append(f"--proxy-server={proxy}")
        if extra_args:
            args += extra_args
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._wait_ready()

    def _http_json(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode())

    def _wait_ready(self, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self._http_json("/json/version")
                return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("Chrome did not expose a debugging endpoint in time")

    def new_page(self, url="about:blank") -> CDP:
        """Create a fresh page target and return a CDP client wired directly to it."""
        ver = self._http_json("/json/version")
        bcdp = CDP(ver["webSocketDebuggerUrl"])
        try:
            target_id = bcdp.send("Target.createTarget", {"url": url})["targetId"]
        finally:
            bcdp.close()
        for _ in range(50):
            for t in self._http_json("/json/list"):
                if t.get("id") == target_id and t.get("webSocketDebuggerUrl"):
                    return CDP(t["webSocketDebuggerUrl"])
            time.sleep(0.1)
        raise RuntimeError("created target never exposed a websocket url")

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        shutil.rmtree(self.userdir, ignore_errors=True)
