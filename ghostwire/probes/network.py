import threading


class NetLog:
    # protocol-level capture across every target, so it is unaffected by when or whether
    # page scripts wrapped fetch/XHR; worker and iframe traffic included.
    def __init__(self, capture_bodies=True):
        self.capture_bodies = capture_bodies
        self.requests = {}
        self.lock = threading.Lock()
        self.engine = None

    def attach(self, engine):
        self.engine = engine
        engine.on("Network.requestWillBeSent", self._request)
        engine.on("Network.responseReceived", self._response)
        engine.on("Network.loadingFinished", self._finished)
        return self

    def _request(self, p, session_id=None):
        req = p.get("request", {})
        post = req.get("postData")
        if post is None and req.get("hasPostData"):
            try:
                post = self.engine.send("Network.getRequestPostData", {"requestId": p["requestId"]},
                                        session_id=session_id).get("postData")
            except Exception:
                post = "<unavailable>"
        stack = (p.get("initiator", {}).get("stack") or {}).get("callFrames", [])
        entry = {"session": session_id, "target": self.engine.sessions.get(session_id, {}).get("type", "page"),
                 "method": req.get("method"), "url": req.get("url"), "req_headers": req.get("headers", {}),
                 "post": post, "initiator": [f.get("functionName") or "<anon>" for f in stack][:5],
                 "status": None, "body": None}
        with self.lock:
            self.requests[p["requestId"]] = entry

    def _response(self, p, session_id=None):
        with self.lock:
            entry = self.requests.get(p["requestId"])
        if entry:
            entry["status"] = p.get("response", {}).get("status")

    def _finished(self, p, session_id=None):
        if not self.capture_bodies:
            return
        rid = p["requestId"]
        with self.lock:
            entry = self.requests.get(rid)
        if not entry:
            return
        try:
            r = self.engine.send("Network.getResponseBody", {"requestId": rid}, session_id=session_id)
            entry["body"], entry["body_b64"] = r.get("body"), r.get("base64Encoded")
        except Exception:
            pass

    def all(self):
        with self.lock:
            return list(self.requests.values())

    def find(self, url_substr):
        with self.lock:
            return [e for e in self.requests.values() if url_substr in (e["url"] or "")]
