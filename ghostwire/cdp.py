import os, json, queue, threading, itertools


class CDPError(Exception): pass


class PipeConnection:
    # one connection, NUL-delimited JSON; reader demuxes id-replies from events, a separate
    # dispatcher runs handlers so a handler can block on its own command while paused.
    def __init__(self, command_fd, event_fd, default_timeout=20.0):
        self.command_fd, self.event_fd, self.default_timeout = command_fd, event_fd, default_timeout
        self.next_id = itertools.count(1)
        self.write_lock = threading.Lock()
        self.replies, self.reply_ready = {}, threading.Condition()
        self.handlers = {}
        self.events = queue.Queue()
        self.closed = False
        threading.Thread(target=self._read, name="cdp-read", daemon=True).start()
        threading.Thread(target=self._dispatch, name="cdp-dispatch", daemon=True).start()

    def _read(self):
        buf = bytearray()
        while not self.closed:
            try:
                chunk = os.read(self.event_fd, 1 << 20)
            except OSError:
                break
            if not chunk:
                break
            buf.extend(chunk)
            while (end := buf.find(b"\0")) != -1:
                msg = bytes(buf[:end])
                del buf[:end + 1]
                if msg:
                    self._route(json.loads(msg))

    def _route(self, msg):
        if "id" in msg:
            with self.reply_ready:
                self.replies[msg["id"]] = msg
                self.reply_ready.notify_all()
        elif "method" in msg:
            self.events.put(msg)

    def _dispatch(self):
        while (msg := self.events.get()) is not None:
            for handler in list(self.handlers.get(msg["method"], ())):
                try:
                    handler(msg.get("params", {}), msg.get("sessionId"))
                except Exception as e:
                    print(f"[cdp] {msg['method']} handler: {e}")

    def on(self, method, handler):
        self.handlers.setdefault(method, []).append(handler)

    def off(self, method, handler):
        try:
            self.handlers.get(method, []).remove(handler)
        except ValueError:
            pass

    def send(self, method, params=None, session_id=None, wait=True, timeout=None):
        mid = next(self.next_id)
        msg = {"id": mid, "method": method, "params": params or {}}
        if session_id is not None:
            msg["sessionId"] = session_id
        payload = json.dumps(msg).encode() + b"\0"
        with self.write_lock:
            if self.closed:
                raise CDPError("connection closed")
            os.write(self.command_fd, payload)
        if not wait:
            return mid
        with self.reply_ready:
            if not self.reply_ready.wait_for(lambda: mid in self.replies, timeout or self.default_timeout):
                raise CDPError(f"timeout: {method}")
            reply = self.replies.pop(mid)
        if "error" in reply:
            raise CDPError(f"{method}: {reply['error']}")
        return reply.get("result", {})

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.events.put(None)
        for fd in (self.command_fd, self.event_fd):
            try:
                os.close(fd)
            except OSError:
                pass
