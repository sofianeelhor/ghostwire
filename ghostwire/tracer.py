"""
tracer.py — invisible function instrumentation via CDP debugger primitives.

Hooks are set with Debugger.setBreakpointOnFunctionCall on the live function object, so
the function object is never replaced or wrapped. The hook is invisible to the page's
own defenses (fn.toString() native-code checks, Proxy traps, monkeypatch detection),
which is the main advantage over in-page injection or Frida-style hooking.

Two capture modes:
  * args-only (default) — pause at entry, read live arguments, resume. Cheap, legacy.
  * input->output pairs (capture_returns=True) — on the first entry pause we locate the
    function's return point(s) via getPossibleBreakpoints(restrictToFunction) and set a
    breakpoint there; thereafter every call is captured atomically as (args, returnValue)
    at the return location, where the top frame is still the callee. These pairs are the
    ground-truth corpus the verification oracle (oracle.py) checks the agent against.

Session-aware: a hook can target the root page or any attached worker/iframe by url
substring, and each pause is read and resumed on the session it came from.
"""
import json
import threading

GW = "/*gw*/"  # sentinel so our own eval scripts can be filtered out of capture
ARGS_EXPR = GW + "JSON.stringify(Array.prototype.slice.call(arguments))"


class Tracer:
    def __init__(self):
        self.captures = []          # legacy arg-only records: [{session, fn, args, stack}]
        self.pairs = {}             # label -> [{input, output, output_type}]  (the corpus)
        self._lock = threading.Lock()
        self.engine = None
        self._hooks = []            # [hook spec dict]
        self._ret_bps = {}          # return-location breakpointId -> label
        self._loc_index = {}        # (session, scriptId, line, col) -> hook spec

    def attach(self, engine):
        self.engine = engine
        engine.on("Debugger.paused", self._on_paused)
        return self

    # ---- hooking ----
    def hook(self, expression, target_url=None, capture_returns=False, label=None):
        """Hook a function resolved by `expression`. If `target_url` is given, resolve and
        hook inside the worker/iframe whose url contains it; else the root page. With
        capture_returns=True, capture (input -> output) pairs into the corpus under
        `label` (defaults to the expression)."""
        sid = self.engine.resolve_session(target_url)
        r = self.engine.send("Runtime.evaluate",
                             {"expression": GW + expression, "silent": True}, session_id=sid)
        obj = r.get("result", {})
        if obj.get("type") != "function" or not obj.get("objectId"):
            raise RuntimeError(f"{expression!r} is not a function in target {target_url or 'root'}: {obj}")
        oid = obj["objectId"]
        self.engine.send("Debugger.setBreakpointOnFunctionCall", {"objectId": oid}, session_id=sid)
        spec = {"expr": expression, "label": label or expression, "session": sid,
                "capture_returns": capture_returns, "ret_set": False}
        if capture_returns:
            loc = self._function_location(oid, sid)
            if loc:
                key = (sid, loc.get("scriptId"), loc.get("lineNumber"), loc.get("columnNumber"))
                self._loc_index[key] = spec
            with self._lock:
                self.pairs.setdefault(spec["label"], [])
        self._hooks.append(spec)
        return self

    def _function_location(self, object_id, sid):
        try:
            props = self.engine.send("Runtime.getProperties",
                                     {"objectId": object_id, "ownProperties": False}, session_id=sid)
            for ip in props.get("internalProperties", []):
                if ip.get("name") == "[[FunctionLocation]]":
                    return (ip.get("value") or {}).get("value")
        except Exception:
            pass
        return None

    # ---- pause handling (single owner of Debugger.paused) ----
    def _on_paused(self, params, session_id=None):
        frames = params.get("callFrames", [])
        hit = set(params.get("hitBreakpoints") or [])

        # 1) return-location breakpoint -> capture an (input, output) pair
        ret_labels = [self._ret_bps[b] for b in hit if b in self._ret_bps]
        if ret_labels and frames:
            self._capture_pair(ret_labels[0], frames[0], session_id)
            return self._resume(session_id)

        # 2) entry pause
        if frames:
            cf = frames[0]
            spec = self._match_entry(cf, session_id)
            if spec and spec["capture_returns"]:
                if not spec["ret_set"]:
                    self._set_return_bps(spec, cf, session_id)
                return self._resume(session_id)   # the pair is captured at the return bp
            self._record_args(cf, frames, session_id)
        self._resume(session_id)

    def _match_entry(self, cf, sid):
        loc = cf.get("functionLocation") or {}
        key = (sid, loc.get("scriptId"), loc.get("lineNumber"), loc.get("columnNumber"))
        spec = self._loc_index.get(key)
        if spec:
            return spec
        # fallback: a single still-unresolved return-capture hook on this session
        pend = [h for h in self._hooks
                if h["capture_returns"] and not h["ret_set"] and h["session"] == sid]
        return pend[0] if len(pend) == 1 else None

    def _set_return_bps(self, spec, cf, sid):
        loc = cf["functionLocation"]
        try:
            poss = self.engine.send("Debugger.getPossibleBreakpoints",
                                    {"start": {"scriptId": loc["scriptId"],
                                               "lineNumber": loc["lineNumber"],
                                               "columnNumber": loc["columnNumber"]},
                                     "restrictToFunction": True},
                                    session_id=sid).get("locations", [])
        except Exception:
            poss = []
        n = 0
        for rl in [p for p in poss if p.get("type") == "return"]:
            try:
                r = self.engine.send("Debugger.setBreakpoint",
                                     {"location": {"scriptId": rl["scriptId"],
                                                   "lineNumber": rl["lineNumber"],
                                                   "columnNumber": rl.get("columnNumber", 0)}},
                                     session_id=sid)
                if r.get("breakpointId"):
                    self._ret_bps[r["breakpointId"]] = spec["label"]
                    n += 1
            except Exception:
                pass
        spec["ret_set"] = True
        if not n:
            print(f"[oracle] warning: no return location found for {spec['label']!r}; "
                  f"corpus will stay empty for this hook")

    def _capture_pair(self, label, cf, sid):
        try:
            ev = self.engine.send("Debugger.evaluateOnCallFrame",
                                  {"callFrameId": cf["callFrameId"], "expression": ARGS_EXPR,
                                   "returnByValue": True}, session_id=sid)
            args = ev.get("result", {}).get("value")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    pass
        except Exception as e:
            args = f"<args error: {e}>"
        rv = cf.get("returnValue") or {}
        rec = {"input": args, "output": rv.get("value"), "output_type": rv.get("type")}
        # objects/arraybuffers come back without a by-value field; mark them unserialized
        if "value" not in rv and rv.get("type") not in (None,):
            rec["output"] = None
            rec["unserialized"] = rv.get("description") or rv.get("subtype") or rv.get("type")
        with self._lock:
            self.pairs.setdefault(label, []).append(rec)

    def _record_args(self, cf, frames, session_id):
        rec = {"session": session_id, "reason": "call", "fn": cf.get("functionName") or "<anonymous>"}
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

    def _resume(self, sid):
        try:
            self.engine.send("Debugger.resume", session_id=sid)
        except Exception:
            pass
