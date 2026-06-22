import threading, time

# Log the data crossing the JS crypto boundary. We breakpoint-on-call the native crypto.subtle.*
# functions (invisible — never wrapped) and at each call read the caller frame's scope, decoding
# every ArrayBuffer / TypedArray / string in reach: that surfaces the plaintext and key material
# being fed to crypto, generically, without knowing the variable names. The resolved output is a
# Promise (async) so v1 captures inputs, not the ciphertext — correlate the output via the network
# or gw_follow. CryptoKey objects are reported by metadata (raw bytes only if extractable).

SUBTLE_METHODS = ("digest", "encrypt", "decrypt", "sign", "verify", "deriveBits", "deriveKey",
                  "importKey", "exportKey", "wrapKey", "unwrapKey", "generateKey")

_DECODE = ("function(){"
           "var u;"
           "if(this instanceof ArrayBuffer)u=new Uint8Array(this);"
           "else if(ArrayBuffer.isView(this))u=new Uint8Array(this.buffer,this.byteOffset,this.byteLength);"
           "else return null;"
           "var s=u.subarray(0,64);"
           "var hex=Array.prototype.map.call(s,function(x){return ('0'+x.toString(16)).slice(-2)}).join('');"
           "var utf8='';try{utf8=new TextDecoder().decode(s)}catch(e){}"
           "return {bytes:u.length,hex:hex,utf8:utf8};}")


class CryptoLogger:
    def __init__(self, engine):
        self.engine = engine

    def capture(self, duration=4.0, target_url=None):
        sid = self.engine.resolve_session(target_url)
        entries, lock = [], threading.Lock()
        breakpoint_method = {}

        def on_paused(params, s=None):
            try:
                self._record(params, s, breakpoint_method, entries, lock)
            finally:
                try:
                    self.engine.send("Debugger.resume", session_id=s)
                except Exception:
                    pass

        saved_handlers = self.engine.cdp.handlers.get("Debugger.paused", [])
        self.engine.cdp.handlers["Debugger.paused"] = [on_paused]
        breakpoints = []
        try:
            for method in SUBTLE_METHODS:
                fn = self.engine.send("Runtime.evaluate", {"expression": f"crypto.subtle.{method}", "silent": True},
                                      session_id=sid).get("result", {})
                if fn.get("type") == "function" and fn.get("objectId"):
                    bp = self.engine.send("Debugger.setBreakpointOnFunctionCall",
                                          {"objectId": fn["objectId"]}, session_id=sid).get("breakpointId")
                    if bp:
                        breakpoint_method[bp] = f"crypto.subtle.{method}"
                        breakpoints.append(bp)
            time.sleep(duration)
        finally:
            for bp in breakpoints:
                try:
                    self.engine.send("Debugger.removeBreakpoint", {"breakpointId": bp}, session_id=sid)
                except Exception:
                    pass
            self.engine.cdp.handlers["Debugger.paused"] = saved_handlers
        return entries

    def _record(self, params, sid, breakpoint_method, entries, lock):
        frames = params.get("callFrames", [])
        if not frames:
            return
        method = next((breakpoint_method[b] for b in (params.get("hitBreakpoints") or []) if b in breakpoint_method),
                      "crypto.subtle.*")
        caller = frames[0]
        inputs = []
        for scope in caller.get("scopeChain", []):
            if scope.get("type") not in ("local", "closure", "block"):
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
                if value.get("type") == "string" and value.get("value"):
                    inputs.append({"name": prop["name"], "kind": "string", "value": value["value"][:128]})
                elif value.get("subtype") in ("arraybuffer", "typedarray", "dataview") and value.get("objectId"):
                    decoded = self.engine.send("Runtime.callFunctionOn",
                                               {"objectId": value["objectId"], "functionDeclaration": _DECODE,
                                                "returnByValue": True}, session_id=sid).get("result", {}).get("value")
                    if decoded:
                        inputs.append({"name": prop["name"], "kind": value["subtype"], **decoded})
        with lock:
            entries.append({"method": method, "caller": caller.get("functionName") or "<anonymous>", "inputs": inputs})
