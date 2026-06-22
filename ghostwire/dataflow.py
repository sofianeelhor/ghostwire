import threading

from .origin import DEFAULT_BLACKBOX

# Follow a producer's return value into its consumer's frame: break on the producer, capture
# what it returns, step out, and find the variable in the caller that now holds it. The value
# is then a live variable the agent can read or patch (Debugger.setVariableValue). Across await
# this is best-effort — setReturnValue/stepOut land at the awaiter, not the resolved use — so a
# Promise return is reported as async rather than guessed.


class DataflowTracer:
    def __init__(self, engine):
        self.engine = engine

    def follow(self, producer, trigger=None, target_url=None, blackbox=None, timeout=20):
        sid = self.engine.resolve_session(target_url)
        box, ready = {}, threading.Event()

        def on_paused(params, s=None):
            box["frames"], box["sid"] = params.get("callFrames", []), s
            ready.set()

        def wait_pause():
            if not ready.wait(timeout):
                return None
            ready.clear()
            return box["frames"]

        saved_handlers = self.engine.cdp.handlers.get("Debugger.paused", [])
        self.engine.cdp.handlers["Debugger.paused"] = [on_paused]
        self._safe("Debugger.setBlackboxPatterns",
                   {"patterns": DEFAULT_BLACKBOX if blackbox is None else blackbox}, sid)

        entry_bp, return_bps = None, []
        try:
            fn = self.engine.send("Runtime.evaluate", {"expression": producer, "silent": True},
                                  session_id=sid).get("result", {})
            if fn.get("type") != "function" or not fn.get("objectId"):
                return {"error": f"{producer!r} is not a function in {target_url or 'page'}"}
            entry_bp = self.engine.send("Debugger.setBreakpointOnFunctionCall",
                                        {"objectId": fn["objectId"]}, session_id=sid).get("breakpointId")
            if trigger:
                self.engine.cdp.send("Runtime.evaluate", {"expression": trigger, "silent": True},
                                     session_id=sid, wait=False)

            frames = wait_pause()
            if not frames:
                return {"error": f"{producer!r} was not called (provide a trigger, or invoke it)"}

            location = frames[0]["functionLocation"]
            possible = self.engine.send("Debugger.getPossibleBreakpoints",
                                        {"start": location, "restrictToFunction": True},
                                        session_id=sid).get("locations", [])
            for ret in (l for l in possible if l.get("type") == "return"):
                bp = self.engine.send("Debugger.setBreakpoint", {"location": ret}, session_id=sid).get("breakpointId")
                if bp:
                    return_bps.append(bp)
            self.engine.send("Debugger.resume", session_id=sid)

            frames = wait_pause()
            if not frames:
                return {"error": f"{producer!r} did not reach a return"}
            rv = frames[0].get("returnValue", {}) or {}
            returned = {"type": rv.get("type"), "subtype": rv.get("subtype"), "value": rv.get("value")}
            is_async = rv.get("subtype") == "promise"

            self.engine.send("Debugger.stepOut", session_id=sid)
            frames = wait_pause()
            consumer = None
            if frames:
                top = frames[0]
                consumer = {"function": top.get("functionName") or "<anonymous>",
                            "url": top.get("url", ""),
                            "line": (top.get("location", {}).get("lineNumber") or 0) + 1,
                            "variable": None if is_async else self._find_variable(top, rv, sid)}

            notes = []
            if is_async:
                notes.append("producer returns a Promise; the real consumer is the await/then "
                             "continuation — this lands at the awaiter, not the resolved use")
            elif consumer and consumer["variable"] is None:
                notes.append("return value is not bound to a named variable in the consumer "
                             "(passed inline or destructured); reporting the consumer frame only")
            return {"producer": producer, "returned": returned, "async": is_async, "consumer": consumer,
                    "confidence": "high" if (consumer and consumer["variable"]) else "low",
                    "notes": "; ".join(notes)}
        finally:
            for bp in filter(None, [entry_bp, *return_bps]):
                self._safe("Debugger.removeBreakpoint", {"breakpointId": bp}, sid)
            self._safe("Debugger.setBlackboxPatterns", {"patterns": []}, sid)
            self.engine.cdp.handlers["Debugger.paused"] = saved_handlers
            self._safe("Debugger.resume", None, sid)

    def _find_variable(self, frame, return_value, sid):
        if "value" not in return_value:          # objects come back without a by-value field
            return None
        target, target_type = return_value["value"], return_value.get("type")
        for scope in frame.get("scopeChain", []):
            if scope.get("type") not in ("local", "block", "closure"):
                continue
            object_id = (scope.get("object") or {}).get("objectId")
            if not object_id:
                continue
            try:
                props = self.engine.send("Runtime.getProperties", {"objectId": object_id, "ownProperties": True},
                                         session_id=sid).get("result", [])
            except Exception:
                continue
            for prop in props:
                value = prop.get("value") or {}
                if "value" in value and value["value"] == target and value.get("type") == target_type:
                    return prop["name"]
        return None

    def _safe(self, method, params, sid):
        try:
            self.engine.send(method, params, session_id=sid)
        except Exception:
            pass
