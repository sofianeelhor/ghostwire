"""
tracer.py — invisible function instrumentation via CDP debugger primitives.

Hooks are set with Debugger.setBreakpointOnFunctionCall on the live function object, so
the function object is never replaced or wrapped. The hook is invisible to the page's
own defenses (fn.toString() native-code checks, Proxy traps, monkeypatch detection),
which is the main advantage over in-page injection or Frida-style hooking.

Session-aware: a hook can target the root page or any attached worker/iframe by url
substring, and each pause is read and resumed on the session it came from.
"""
import threading

GW = "/*gw*/"  # sentinel so our own eval scripts can be filtered out of capture
ARGS_EXPR = GW + "JSON.stringify(Array.prototype.slice.call(arguments))"


class Tracer:
    def __init__(self):
        self.captures = []
        self._lock = threading.Lock()
        self.engine = None

    def attach(self, engine):
        self.engine = engine
        engine.on("Debugger.paused", self._on_paused)
        return self

    def hook(self, expression, target_url=None):
        """Hook a function resolved by `expression`. If `target_url` is given, resolve
        and hook inside the worker/iframe whose url contains it; else the root page."""
        sid = self.engine.session_for(target_url) if target_url else None
        r = self.engine.send("Runtime.evaluate",
                             {"expression": GW + expression, "silent": True}, session_id=sid)
        obj = r.get("result", {})
        if obj.get("type") != "function" or not obj.get("objectId"):
            raise RuntimeError(f"{expression!r} is not a function in target {target_url or 'root'}: {obj}")
        self.engine.send("Debugger.setBreakpointOnFunctionCall",
                         {"objectId": obj["objectId"]}, session_id=sid)
        return self

    def _on_paused(self, params, session_id=None):
        frames = params.get("callFrames", [])
        rec = {"session": session_id, "reason": params.get("reason")}
        if frames:
            cf = frames[0]
            rec["fn"] = cf.get("functionName") or "<anonymous>"
            try:
                ev = self.engine.send("Debugger.evaluateOnCallFrame",
                                      {"callFrameId": cf["callFrameId"], "expression": ARGS_EXPR,
                                       "returnByValue": True}, session_id=session_id)
                rec["args"] = ev.get("result", {}).get("value")
            except Exception as e:
                rec["args"] = f"<eval error: {e}>"
            rec["stack"] = [f.get("functionName") or "<anon>" for f in frames[:5]]
        with self._lock:
            self.captures.append(rec)
        print(f"[hook] {rec.get('fn')}({rec.get('args')}) @ {session_id or 'root'}")
        self.engine.send("Debugger.resume", session_id=session_id)
