"""
engine.py — one connection, every target.

Connects to the root page and uses Target.setAutoAttach(flatten) to follow the whole
target graph: dedicated/shared workers, service workers, and out-of-process iframes.
Each child target gets its own CDP session id; the engine enables the probe domains on
every session and routes events to probes tagged with the session they came from.

This is what makes ghostwire work on real targets, where the interesting code (crypto,
anti-bot engines) runs inside an iframe + worker rather than the top page.
"""
from .browser import Browser


class Engine:
    def __init__(self, browser=None, headless=True, proxy=None):
        self.browser = browser or Browser(headless=headless, proxy=proxy)
        self.cdp = self.browser.new_page()
        self.sessions = {}          # session_id -> targetInfo  (root is None -> {"type":"page"})
        self.blackbox = None
        self._probes = []
        self.cdp.on("Target.attachedToTarget", self._on_attached)
        self.cdp.on("Target.detachedFromTarget", self._on_detached)

    # ---- probes ----
    def add_probe(self, probe):
        probe.attach(self)
        self._probes.append(probe)
        return probe

    def on(self, method, handler):
        self.cdp.on(method, handler)

    def send(self, method, params=None, session_id=None):
        return self.cdp.send(method, params, session_id=session_id)

    # ---- lifecycle ----
    def start(self, blackbox=None):
        self.blackbox = blackbox
        self.sessions[None] = {"type": "page", "url": ""}
        self._enable_session(None, is_page=True)
        return self

    def navigate(self, url):
        self.cdp.send("Page.navigate", {"url": url})
        return self

    def _enable_session(self, sid, is_page=False):
        def send(method, params=None):
            try:
                return self.cdp.send(method, params, session_id=sid)
            except Exception:
                return None
        for dom in ("Runtime", "Debugger", "Network"):
            send(dom + ".enable")
        if is_page:
            send("Page.enable")
        if self.blackbox:
            send("Debugger.setBlackboxPatterns", {"patterns": self.blackbox})
        # follow grandchildren too (workers inside iframes, etc.)
        send("Target.setAutoAttach",
             {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})

    def _on_attached(self, params, session_id=None):
        sid = params["sessionId"]
        info = params.get("targetInfo", {})
        self.sessions[sid] = info
        is_page = info.get("type") in ("page", "iframe")
        self._enable_session(sid, is_page=is_page)
        # child started paused (waitForDebuggerOnStart); let it run now that probes are live
        try:
            self.cdp.send("Runtime.runIfWaitingForDebugger", session_id=sid)
        except Exception:
            pass

    def _on_detached(self, params, session_id=None):
        self.sessions.pop(params.get("sessionId"), None)

    # ---- helpers ----
    def targets(self):
        """[(session_id, type, url)] for every attached target."""
        return [(sid, i.get("type"), i.get("url", "")) for sid, i in self.sessions.items()]

    def session_for(self, url_substr):
        """Find the session id of the target whose url contains url_substr (None = root)."""
        for sid, info in self.sessions.items():
            if sid is not None and url_substr in (info.get("url") or ""):
                return sid
        return None

    def close(self):
        try:
            self.cdp.close()
        except Exception:
            pass
        try:
            self.browser.close()
        except Exception:
            pass
