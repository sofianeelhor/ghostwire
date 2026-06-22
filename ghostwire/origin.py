import threading

from .heap import take_snapshot

# framework code to skip while stepping, so we pause only at user-land returns. the agent can
# override per trace; these are just sensible defaults.
DEFAULT_BLACKBOX = [r"/node_modules/", r"react-dom", r"react\.production", r"react\.development",
                    r"vue\.", r"angular", r"zone\.js", r"polyfill", r"webpack", r"jquery",
                    r"lodash", r"\.min\.js"]


class OriginTracer:
    def __init__(self, engine):
        self.engine = engine

    # find the user-land function whose return first makes `value` appear in the heap. start by
    # breaking on `enter` (a function on the path to the creation); `trigger` is an optional JS
    # expression that provokes the call (a click, etc.). we then step with framework code
    # blackboxed, snapshotting at each user-land return until the value shows up.
    def trace(self, value, enter, trigger=None, target_url=None, blackbox=None,
              max_steps=300, max_returns=40):
        sid = self.engine.resolve_session(target_url)
        box, ready = {}, threading.Event()

        def on_paused(params, s=None):
            box["frames"], box["sid"] = params.get("callFrames", []), s
            ready.set()

        def wait_pause(timeout=20):
            if not ready.wait(timeout):
                return None
            ready.clear()
            return box["frames"]

        saved_handlers = self.engine.cdp.handlers.get("Debugger.paused", [])
        self.engine.cdp.handlers["Debugger.paused"] = [on_paused]
        patterns = DEFAULT_BLACKBOX if blackbox is None else blackbox
        self._safe("Debugger.setBlackboxPatterns", {"patterns": patterns}, sid)

        breakpoint_id = None
        try:
            fn = self.engine.send("Runtime.evaluate", {"expression": enter, "silent": True},
                                  session_id=sid).get("result", {})
            if fn.get("type") != "function" or not fn.get("objectId"):
                return {"found": False, "error": f"{enter!r} is not a function in {target_url or 'page'}"}
            breakpoint_id = self.engine.send("Debugger.setBreakpointOnFunctionCall",
                                             {"objectId": fn["objectId"]}, session_id=sid).get("breakpointId")
            if trigger:
                self.engine.cdp.send("Runtime.evaluate", {"expression": trigger, "silent": True},
                                     session_id=sid, wait=False)

            frames = wait_pause()
            if not frames:
                return {"found": False, "error": f"{enter!r} was not called (provide a trigger, or invoke it)"}

            preexisting = bool(take_snapshot(self.engine, sid).find_value(value))
            trail, origin, previous, returns = [], None, frames, 0
            for _ in range(max_steps):
                self.engine.send("Debugger.stepInto", session_id=sid)
                frames = wait_pause()
                if not frames:
                    break
                if len(frames) < len(previous):                 # a function just returned
                    returned = previous[0]
                    present = bool(take_snapshot(self.engine, sid).find_value(value))
                    location = returned.get("functionLocation", {})
                    step = {"function": returned.get("functionName") or "<anonymous>",
                            "url": returned.get("url", ""),
                            "line": (location.get("lineNumber") or 0) + 1,
                            "column": (location.get("columnNumber") or 0) + 1,
                            "value_present_after": present}
                    trail.append(step)
                    returns += 1
                    if present and origin is None and not preexisting:
                        origin = step
                        break
                    if returns >= max_returns:
                        break
                previous = frames

            notes = []
            if preexisting:
                notes.append("value already in the heap before enter ran — its origin is upstream "
                             "of the trace start; break on an earlier function")
            elif origin is None:
                notes.append("value never appeared at a user-land return within the step budget — "
                             "raise max_steps, widen blackbox, or start deeper")
            return {"found": origin is not None, "value": value, "origin": origin,
                    "preexisting": preexisting, "returns_observed": returns, "trail": trail,
                    "confidence": "high" if origin else "none",
                    "notes": "; ".join(notes)}
        finally:
            if breakpoint_id:
                self._safe("Debugger.removeBreakpoint", {"breakpointId": breakpoint_id}, sid)
            self._safe("Debugger.setBlackboxPatterns", {"patterns": []}, sid)
            self.engine.cdp.handlers["Debugger.paused"] = saved_handlers
            self._safe("Debugger.resume", None, sid)

    def _safe(self, method, params, sid):
        try:
            self.engine.send(method, params, session_id=sid)
        except Exception:
            pass
