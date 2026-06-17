"""
network.py — capture all HTTP(S) traffic across every target at the protocol level:
request method/url/body and response status/body, each tagged with the session it came
from (so worker and iframe traffic is captured too) and the JS initiator stack.
Protocol-level, so it is unaffected by when or whether page scripts wrapped fetch/XHR.
"""
import threading


class NetLog:
    def __init__(self, capture_bodies=True):
        self.capture_bodies = capture_bodies
        self.requests = {}                 # requestId -> entry
        self._lock = threading.Lock()
        self.engine = None

    def attach(self, engine):
        self.engine = engine
        engine.on("Network.requestWillBeSent", self._on_request)
        engine.on("Network.responseReceived", self._on_response)
        engine.on("Network.loadingFinished", self._on_finished)
        return self

    def _on_request(self, p, session_id=None):
        req = p.get("request", {})
        post = req.get("postData")
        if post is None and req.get("hasPostData"):
            try:
                post = self.engine.send("Network.getRequestPostData",
                                        {"requestId": p["requestId"]}, session_id=session_id).get("postData")
            except Exception:
                post = "<unavailable>"
        stack = (p.get("initiator", {}).get("stack") or {}).get("callFrames", [])
        entry = {
            "session": session_id,
            "target": self.engine.sessions.get(session_id, {}).get("type", "page"),
            "method": req.get("method"),
            "url": req.get("url"),
            "req_headers": req.get("headers", {}),
            "post": post,
            "initiator": [f.get("functionName") or "<anon>" for f in stack][:5],
            "status": None,
            "body": None,
        }
        with self._lock:
            self.requests[p["requestId"]] = entry

    def _on_response(self, p, session_id=None):
        with self._lock:
            e = self.requests.get(p["requestId"])
        if e:
            e["status"] = p.get("response", {}).get("status")

    def _on_finished(self, p, session_id=None):
        if not self.capture_bodies:
            return
        rid = p["requestId"]
        with self._lock:
            e = self.requests.get(rid)
        if not e:
            return
        try:
            r = self.engine.send("Network.getResponseBody", {"requestId": rid}, session_id=session_id)
            e["body"] = r.get("body")
            e["body_b64"] = r.get("base64Encoded")
        except Exception:
            pass

    def all(self):
        with self._lock:
            return list(self.requests.values())

    def find(self, url_substr):
        with self._lock:
            return [e for e in self.requests.values() if url_substr in (e["url"] or "")]
