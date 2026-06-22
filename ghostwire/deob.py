import json, threading


class StringDumper:
    # Drive the obfuscator's own decoder at runtime instead of reimplementing it: call it over an
    # index range and collect what it returns. This is faithful (the real decoder, real rotated
    # array, real page environment) and beats static deob, which cannot see the runtime rotation.
    def __init__(self, engine):
        self.engine = engine

    def dump(self, decoder, start=0, count=512, extra_args=None, target_url=None, stop_after=48):
        sid = self.engine.resolve_session(target_url)
        suffix = "" if not extra_args else "," + ",".join(json.dumps(a) for a in extra_args)
        decoded, misses, errors = {}, 0, 0
        for index in range(start, start + count):
            r = self.engine.send("Runtime.evaluate",
                                 {"expression": f"/*gw*/({decoder})({index}{suffix})",
                                  "returnByValue": True, "silent": True}, session_id=sid)
            if r.get("exceptionDetails"):
                errors += 1
                misses += 1
            elif r.get("result", {}).get("type") == "string":
                decoded[index] = r["result"]["value"]
                misses = 0
            else:
                misses += 1
            if misses >= stop_after and decoded:        # ran off the end of the table
                break
        notes = f"{len(decoded)} strings over indices {start}..{start + count - 1}"
        if errors:
            notes += f"; {errors} indices threw (likely out of range)"
        return {"decoded": decoded, "count": len(decoded), "notes": notes}


class VMTracer:
    # Instrument a bytecode-VM dispatch loop (or any hot line): break at a location and log watched
    # expressions on every hit, emitting a (state) trace an agent/auto-disassembler can lift. The
    # location is given as a url substring + 1-based line; find it with gw_scripts.
    def __init__(self, engine):
        self.engine = engine

    def watch(self, url_substr, line, column=0, watch=None, iterations=300, trigger=None,
              target_url=None, timeout=20):
        sid = self.engine.resolve_session(target_url)
        watch = watch or []
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
        breakpoint_id, trace = None, []
        try:
            placed = self.engine.send("Debugger.setBreakpointByUrl",
                                      {"urlRegex": url_substr, "lineNumber": line - 1, "columnNumber": column},
                                      session_id=sid)
            breakpoint_id = placed.get("breakpointId")
            if not placed.get("locations"):
                return {"error": f"no breakpoint resolved at {url_substr!r}:{line}", "hits": 0, "trace": []}
            if trigger:
                self.engine.cdp.send("Runtime.evaluate", {"expression": trigger, "silent": True},
                                     session_id=sid, wait=False)
            for _ in range(iterations):
                frames = wait_pause()
                if not frames:
                    break
                frame = frames[0]
                row = {}
                for expression in watch:
                    try:
                        ev = self.engine.send("Debugger.evaluateOnCallFrame",
                                              {"callFrameId": frame["callFrameId"], "expression": expression,
                                               "returnByValue": True}, session_id=sid)
                        row[expression] = ev.get("result", {}).get("value")
                    except Exception:
                        row[expression] = "<error>"
                trace.append(row)
                self.engine.send("Debugger.resume", session_id=sid)
            return {"hits": len(trace), "trace": trace}
        finally:
            if breakpoint_id:
                try:
                    self.engine.send("Debugger.removeBreakpoint", {"breakpointId": breakpoint_id}, session_id=sid)
                except Exception:
                    pass
            self.engine.cdp.handlers["Debugger.paused"] = saved_handlers
            try:
                self.engine.send("Debugger.resume", session_id=sid)
            except Exception:
                pass
