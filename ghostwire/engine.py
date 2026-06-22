import re, sys, platform as _platform, threading

from .browser import Browser


def _normalize_url(url):
    url = url.strip()
    if "://" in url or url.split(":", 1)[0] in ("about", "data", "blob", "chrome", "file", "javascript"):
        return url
    return "https://" + url            # bare host like "nike.com" -> omnibox behaviour


def _ua_metadata(user_agent, full_version):
    major = full_version.split(".")[0]
    name, arch = {"darwin": ("macOS", "arm64"), "win32": ("Windows", "x86")}.get(sys.platform, ("Linux", "x86"))
    plat_version = ".".join((_platform.mac_ver()[0] or "").split(".")[:3]) if sys.platform == "darwin" else ""
    grease = "Not)A;Brand"
    return {
        "brands": [{"brand": grease, "version": "24"}, {"brand": "Chromium", "version": major},
                   {"brand": "Google Chrome", "version": major}],
        "fullVersionList": [{"brand": grease, "version": "24.0.0.0"},
                            {"brand": "Chromium", "version": full_version},
                            {"brand": "Google Chrome", "version": full_version}],
        "platform": name, "platformVersion": plat_version, "architecture": arch, "model": "", "mobile": False,
    }


class Engine:
    def __init__(self, browser=None, headless=True, proxy=None):
        self.browser = browser or Browser(headless=headless, proxy=proxy)
        self.cdp = self.browser.cdp
        self.sessions = {}
        self.page_session = None
        self.blackbox = None
        self.user_agent = None               # set when launched headless, to drop the HeadlessChrome tell
        self.ua_metadata = None
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
        self._setup_stealth()
        self.page_target = self.cdp.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.cdp.send("Target.attachToTarget", {"targetId": self.page_target, "flatten": True})
        if not self.page_attached.wait(timeout=10):
            raise RuntimeError("page target never attached")
        return self

    def navigate(self, url):
        self.cdp.send("Page.navigate", {"url": _normalize_url(url)}, session_id=self.page_session)
        return self

    def _setup_stealth(self):
        try:
            version = self.cdp.send("Browser.getVersion")
        except Exception:
            version = {}
        ua = version.get("userAgent", "")
        if "Headless" in ua:                 # --headless=new still reports HeadlessChrome in the UA + client hints
            self.user_agent = ua.replace("HeadlessChrome", "Chrome")
            full = (re.search(r"Chrome/([\d.]+)", version.get("product", "")) or
                    re.search(r"Chrome/([\d.]+)", self.user_agent) or [None, "120.0.0.0"])[1]
            self.ua_metadata = _ua_metadata(self.user_agent, full)
        try:
            self.cdp.send("Target.setDiscoverTargets", {"discover": True})  # so targetInfoChanged keeps urls fresh
        except Exception:
            pass

    def _enable(self, sid, is_page):
        def enable(method, params=None):
            try:
                return self.cdp.send(method, params, session_id=sid)
            except Exception:
                return None
        for domain in ("Runtime", "Debugger", "Network"):
            enable(domain + ".enable")
        if self.user_agent:                  # set before the target runs, so initial requests are clean too
            enable("Network.setUserAgentOverride",
                   {"userAgent": self.user_agent, "userAgentMetadata": self.ua_metadata})
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
        target = self.cdp.send("Target.createTarget", {"url": "about:blank", "background": True})["targetId"]
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
