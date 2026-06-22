import threading

from .browser import Browser


class Engine:
    def __init__(self, browser=None, headless=True, proxy=None):
        self.browser = browser or Browser(headless=headless, proxy=proxy)
        self.cdp = self.browser.cdp
        self.sessions = {}
        self.page_session = None
        self.blackbox = None
        self.probes = []
        self.lock = threading.Lock()
        self.private = set()                 # target ids kept out of the probes
        self.page_target = None
        self.page_attached = threading.Event()
        self.cdp.on("Target.attachedToTarget", self._attached)
        self.cdp.on("Target.detachedFromTarget", self._detached)
        self.cdp.on("Target.targetInfoChanged", self._info_changed)

    def add_probe(self, probe):
        probe.attach(self)
        self.probes.append(probe)
        return probe

    def on(self, method, handler):
        self.cdp.on(method, handler)

    def send(self, method, params=None, session_id=None):
        return self.cdp.send(method, params, session_id=session_id if session_id is not None else self.page_session)

    def start(self, blackbox=None):
        self.blackbox = blackbox
        self.page_target = self.cdp.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.cdp.send("Target.attachToTarget", {"targetId": self.page_target, "flatten": True})
        if not self.page_attached.wait(timeout=10):
            raise RuntimeError("page target never attached")
        return self

    def navigate(self, url):
        self.cdp.send("Page.navigate", {"url": url}, session_id=self.page_session)
        return self

    def _enable(self, sid, is_page):
        def enable(method, params=None):
            try:
                return self.cdp.send(method, params, session_id=sid)
            except Exception:
                return None
        for domain in ("Runtime", "Debugger", "Network"):
            enable(domain + ".enable")
        if is_page:
            enable("Page.enable")
        if self.blackbox:
            enable("Debugger.setBlackboxPatterns", {"patterns": self.blackbox})
        enable("Target.setAutoAttach",
               {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})

    def _attached(self, params, session_id=None):
        sid = params["sessionId"]
        info = params.get("targetInfo", {})
        with self.lock:
            if sid in self.sessions:
                return
            self.sessions[sid] = info
            private = info.get("targetId") in self.private
        if private:
            self.cdp.send("Runtime.enable", session_id=sid)
            return
        self._enable(sid, info.get("type") in ("page", "iframe"))
        if info.get("targetId") == self.page_target:
            self.page_session = sid
            self.page_attached.set()
        try:
            self.cdp.send("Runtime.runIfWaitingForDebugger", session_id=sid)  # release waitForDebuggerOnStart
        except Exception:
            pass

    def _detached(self, params, session_id=None):
        with self.lock:
            self.sessions.pop(params.get("sessionId"), None)

    def _info_changed(self, params, session_id=None):
        info = params.get("targetInfo", {})
        with self.lock:
            for sid, existing in self.sessions.items():
                if existing.get("targetId") == info.get("targetId"):
                    self.sessions[sid] = info
                    break

    def resolve_session(self, url_substr=None):
        if not url_substr:
            return self.page_session
        with self.lock:
            for sid, info in self.sessions.items():
                if sid != self.page_session and url_substr in (info.get("url") or ""):
                    return sid
        raise RuntimeError(f"no target url contains {url_substr!r}")

    def targets(self):
        with self.lock:
            return [(sid, info.get("type"), info.get("url", ""))
                    for sid, info in self.sessions.items() if info.get("targetId") not in self.private]

    def open_isolated_page(self):
        target = self.cdp.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        with self.lock:
            self.private.add(target)
        sid = self.cdp.send("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        try:
            self.cdp.send("Runtime.enable", session_id=sid)
        except Exception:
            pass
        return sid

    def close_target(self, sid):
        with self.lock:
            target = self.sessions.get(sid, {}).get("targetId")
        if target:
            try:
                self.cdp.send("Target.closeTarget", {"targetId": target})
            except Exception:
                pass

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass
