"""
cdp.py — minimal Chrome DevTools Protocol client (sync, thread-safe).

A reader thread demuxes command replies (matched by id) from events; a dispatcher
thread runs event handlers off the read path so a handler may itself issue blocking
CDP commands (e.g. evaluate-then-resume inside Debugger.paused) without deadlocking.
"""
import json
import queue
import threading
import itertools
import websocket  # websocket-client


class CDPError(Exception):
    pass


class CDP:
    def __init__(self, ws_url, timeout=20.0):
        self._ws = websocket.create_connection(ws_url, max_size=None, enable_multithread=True)
        self._timeout = timeout
        self._ids = itertools.count(1)
        self._cond = threading.Condition()
        self._results = {}            # id -> reply msg
        self._handlers = {}           # method -> [handler(params)]
        self._events = queue.Queue()
        self._alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._dispatch = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._reader.start()
        self._dispatch.start()

    def _read_loop(self):
        while self._alive:
            try:
                raw = self._ws.recv()
            except Exception:
                break
            if not raw:
                continue
            msg = json.loads(raw)
            if "id" in msg:
                with self._cond:
                    self._results[msg["id"]] = msg
                    self._cond.notify_all()
            elif "method" in msg:
                self._events.put(msg)

    def _dispatch_loop(self):
        while True:
            msg = self._events.get()
            if msg is None:
                break
            for h in list(self._handlers.get(msg["method"], [])):
                try:
                    h(msg.get("params", {}), msg.get("sessionId"))
                except Exception as e:  # never let a handler kill the dispatcher
                    print(f"[cdp] handler error for {msg['method']}: {e}")

    def on(self, method, handler):
        """Register an event handler. handler(params, session_id) runs on the
        dispatcher thread. session_id is None for the root target."""
        self._handlers.setdefault(method, []).append(handler)

    def send(self, method, params=None, session_id=None, wait=True, timeout=None):
        i = next(self._ids)
        payload = {"id": i, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        self._ws.send(json.dumps(payload))
        if not wait:
            return i
        with self._cond:
            ok = self._cond.wait_for(lambda: i in self._results, timeout=timeout or self._timeout)
            if not ok:
                raise CDPError(f"timeout waiting for {method}")
            msg = self._results.pop(i)
        if "error" in msg:
            raise CDPError(f"{method} -> {msg['error']}")
        return msg.get("result", {})

    def close(self):
        self._alive = False
        self._events.put(None)
        try:
            self._ws.close()
        except Exception:
            pass
