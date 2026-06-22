from .heap import take_snapshot

# Patch live objects found via heap search — including closure-captured state that has no JS
# path from window (the case where gw_eval cannot help). Each call snapshots, resolves the
# target to a live handle via HeapProfiler.getObjectByHeapObjectId (heap ids are stable, so an
# id from an earlier gw_objects call still resolves), then mutates it with Runtime.callFunctionOn.


class LiveObjects:
    def __init__(self, engine):
        self.engine = engine

    def _resolve(self, snapshot, sid, node_id, value, constructor, key):
        meta = {"id": node_id}
        if node_id is None:
            matches = snapshot.find_objects(value=value, constructor=constructor, key=key, limit=1)
            if not matches:
                return None, None
            meta, node_id = matches[0], matches[0]["id"]
        bridged = self.engine.send("HeapProfiler.getObjectByHeapObjectId",
                                   {"objectId": str(node_id)}, session_id=sid).get("result", {})
        return bridged.get("objectId"), meta

    def patch(self, node_id=None, value=None, constructor=None, key=None,
              assign=None, apply=None, target_url=None):
        sid = self.engine.resolve_session(target_url)
        snapshot = take_snapshot(self.engine, sid)
        handle, meta = self._resolve(snapshot, sid, node_id, value, constructor, key)
        if not handle:
            return {"patched": False, "error": "no matching live object, or its heap id no longer resolves"}
        result = {"patched": False, "object": meta}
        if assign:
            keys = list(assign.keys())
            result["before"] = self._read_keys(handle, sid, keys)
            self._call(handle, sid, "function(p){ Object.assign(this, p); }", [{"value": assign}])
            result["after"] = self._read_keys(handle, sid, keys)
            result["patched"] = True
        if apply:
            result["apply_result"] = self._call(handle, sid, apply, [])
            result["patched"] = True
        return result

    def read(self, node_id=None, value=None, constructor=None, key=None, target_url=None):
        sid = self.engine.resolve_session(target_url)
        snapshot = take_snapshot(self.engine, sid)
        handle, meta = self._resolve(snapshot, sid, node_id, value, constructor, key)
        if not handle:
            return {"error": "no matching live object"}
        scalars = ("function(){ var o={}; for (var k in this){ try { var v=this[k]; "
                   "if (v===null||['string','number','boolean'].indexOf(typeof v)>=0) o[k]=v; } catch(e){} } return o; }")
        return {"object": meta, "properties": self._call(handle, sid, scalars, [])}

    def _read_keys(self, handle, sid, keys):
        return self._call(handle, sid,
                          "function(keys){ var o={}; for (var k of keys) o[k]=this[k]; return o; }",
                          [{"value": keys}])

    def _call(self, handle, sid, declaration, arguments):
        r = self.engine.send("Runtime.callFunctionOn",
                             {"objectId": handle, "functionDeclaration": declaration,
                              "arguments": arguments, "returnByValue": True, "awaitPromise": True},
                             session_id=sid)
        if r.get("exceptionDetails"):
            return {"__error__": (r["exceptionDetails"].get("exception") or {}).get("description")}
        return r.get("result", {}).get("value")
