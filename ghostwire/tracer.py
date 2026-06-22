import json, threading

GW = "/*gw*/"   # sentinel so our own evals are filtered out of the script probe
ARGS = GW + "JSON.stringify(Array.prototype.slice.call(arguments))"

# Hooks are Debugger.setBreakpointOnFunctionCall on the live function object: it is never
# wrapped or replaced, so fn.toString(), Proxy traps and monkeypatch detectors see nothing.
# For (input,output) corpus pairs we additionally breakpoint the function's own return
# location(s) — found via getPossibleBreakpoints(restrictToFunction) — where the top frame
# is still the callee, so args (in scope) and callFrames[0].returnValue read atomically.


class Tracer:
    def __init__(self):
        self.captures = []          # arg-only records
        self.pairs = {}             # label -> [{input, output, output_type}]
        self.lock = threading.Lock()
        self.engine = None
        self.hooks = []
        self.ret_bps = {}           # return-location breakpointId -> label
        self.by_location = {}       # (sid, scriptId, line, col) -> hook

    def attach(self, engine):
        self.engine = engine
        engine.on("Debugger.paused", self._paused)
        return self

    def hook(self, expression, target_url=None, capture_returns=False, label=None):
        sid = self.engine.resolve_session(target_url)
        obj = self.engine.send("Runtime.evaluate", {"expression": GW + expression, "silent": True},
                               session_id=sid).get("result", {})
        if obj.get("type") != "function" or not obj.get("objectId"):
            raise RuntimeError(f"{expression!r} is not a function in {target_url or 'page'}: {obj}")
        self.engine.send("Debugger.setBreakpointOnFunctionCall", {"objectId": obj["objectId"]}, session_id=sid)
        hook = {"label": label or expression, "session": sid, "returns": capture_returns, "ret_set": False}
        if capture_returns:
            if loc := self._location(obj["objectId"], sid):
                self.by_location[(sid, loc.get("scriptId"), loc.get("lineNumber"), loc.get("columnNumber"))] = hook
            with self.lock:
                self.pairs.setdefault(hook["label"], [])
        self.hooks.append(hook)
        return self

    def _location(self, object_id, sid):
        try:
            for ip in self.engine.send("Runtime.getProperties", {"objectId": object_id, "ownProperties": False},
                                       session_id=sid).get("internalProperties", []):
                if ip.get("name") == "[[FunctionLocation]]":
                    return (ip.get("value") or {}).get("value")
        except Exception:
            return None

    def _paused(self, params, session_id=None):
        frames = params.get("callFrames", [])
        hit = set(params.get("hitBreakpoints") or [])
        if (labels := [self.ret_bps[b] for b in hit if b in self.ret_bps]) and frames:
            self._pair(labels[0], frames[0], session_id)
            return self._resume(session_id)
        if frames:
            hook = self._match(frames[0], session_id)
            if hook and hook["returns"]:
                if not hook["ret_set"]:
                    self._set_return_bps(hook, frames[0], session_id)
                return self._resume(session_id)
            self._args(frames, session_id)
        self._resume(session_id)

    def _match(self, frame, sid):
        loc = frame.get("functionLocation") or {}
        if hook := self.by_location.get((sid, loc.get("scriptId"), loc.get("lineNumber"), loc.get("columnNumber"))):
            return hook
        pending = [h for h in self.hooks if h["returns"] and not h["ret_set"] and h["session"] == sid]
        return pending[0] if len(pending) == 1 else None

    def _set_return_bps(self, hook, frame, sid):
        loc = frame["functionLocation"]
        try:
            locations = self.engine.send("Debugger.getPossibleBreakpoints",
                {"start": loc, "restrictToFunction": True}, session_id=sid).get("locations", [])
        except Exception:
            locations = []
        count = 0
        for rl in (l for l in locations if l.get("type") == "return"):
            try:
                bp = self.engine.send("Debugger.setBreakpoint", {"location": rl}, session_id=sid).get("breakpointId")
            except Exception:
                bp = None
            if bp:
                self.ret_bps[bp] = hook["label"]
                count += 1
        hook["ret_set"] = True
        if not count:
            print(f"[gw] no return location for {hook['label']!r}; corpus stays empty")

    def _pair(self, label, frame, sid):
        try:
            args = self.engine.send("Debugger.evaluateOnCallFrame",
                {"callFrameId": frame["callFrameId"], "expression": ARGS, "returnByValue": True},
                session_id=sid).get("result", {}).get("value")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    pass
        except Exception as e:
            args = f"<args error: {e}>"
        rv = frame.get("returnValue") or {}
        rec = {"input": args, "output": rv.get("value"), "output_type": rv.get("type")}
        if "value" not in rv and rv.get("type"):     # objects/arraybuffers don't come by value
            rec["output"], rec["unserialized"] = None, rv.get("description") or rv.get("subtype") or rv.get("type")
        with self.lock:
            self.pairs.setdefault(label, []).append(rec)

    def _args(self, frames, sid):
        frame = frames[0]
        rec = {"session": sid, "fn": frame.get("functionName") or "<anonymous>"}
        try:
            rec["args"] = self.engine.send("Debugger.evaluateOnCallFrame",
                {"callFrameId": frame["callFrameId"], "expression": ARGS, "returnByValue": True},
                session_id=sid).get("result", {}).get("value")
        except Exception as e:
            rec["args"] = f"<eval error: {e}>"
        rec["stack"] = [f.get("functionName") or "<anon>" for f in frames[:5]]
        with self.lock:
            self.captures.append(rec)

    def _resume(self, sid):
        try:
            self.engine.send("Debugger.resume", session_id=sid)
        except Exception:
            pass
