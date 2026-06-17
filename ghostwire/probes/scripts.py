"""
scripts.py — capture every script every target parses, including code that never
appears in page source or the Network tab: eval(), new Function(), Blob/worker
scripts, injected <script> text. Covers all sessions (page, workers, iframes).
"""
import threading


class ScriptWatcher:
    def __init__(self, fetch_source=True):
        self.fetch_source = fetch_source
        self.scripts = []                  # [{scriptId, session, target, url, dynamic, length, source}]
        self._lock = threading.Lock()
        self.engine = None

    def attach(self, engine):
        self.engine = engine
        engine.on("Debugger.scriptParsed", self._on_parsed)
        return self

    def _on_parsed(self, p, session_id=None):
        try:
            src = (self.engine.send("Debugger.getScriptSource",
                   {"scriptId": p["scriptId"]}, session_id=session_id).get("scriptSource")
                   if self.fetch_source else None)
        except Exception as e:
            src = f"<source error: {e}>"
        if src and src.startswith("/*gw*/"):    # our own instrumentation eval, ignore
            return
        info = self.engine.sessions.get(session_id, {})
        rec = {
            "scriptId": p.get("scriptId"),
            "session": session_id,
            "target": info.get("type", "page"),
            "url": p.get("url") or "",
            "dynamic": (not p.get("url")) and bool(p.get("stackTrace")),
            "length": p.get("length"),
            "source": src,
        }
        with self._lock:
            self.scripts.append(rec)

    def search(self, needle):
        with self._lock:
            return [s for s in self.scripts if s.get("source") and needle in s["source"]]

    def dynamic(self):
        with self._lock:
            return [s for s in self.scripts if s["dynamic"]]
