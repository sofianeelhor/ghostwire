import argparse

from .api import attach


def _stream(engine):
    # live, JS-level view of what the page is doing where no one else can see it: the whole target
    # graph attaching, code generated at runtime (eval / new Function / injected — invisible to view
    # source), console output, and hooked calls. Network noise is deliberately left out.
    def tag(sid):
        return (engine.sessions.get(sid, {}).get("type") or "page")

    def on_attached(p, sid=None):
        info = p.get("targetInfo", {})
        print(f"[+{info.get('type')}] {(info.get('url') or '')[:90]}")

    def on_script(p, sid=None):
        if p.get("url") or not p.get("stackTrace"):
            return                                  # only runtime-generated code is interesting here
        try:
            src = engine.send("Debugger.getScriptSource", {"scriptId": p["scriptId"]},
                              session_id=sid).get("scriptSource", "")
        except Exception:
            src = ""
        if src.startswith("/*gw*/"):
            return
        preview = " ".join(src.split())[:96]
        print(f"[{tag(sid)} eval {p.get('length')}b] {preview}")

    def on_console(p, sid=None):
        parts = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))
        print(f"[{tag(sid)} console.{p.get('type')}] {parts[:160]}")

    engine.on("Target.attachedToTarget", on_attached)
    engine.on("Debugger.scriptParsed", on_script)
    engine.on("Runtime.consoleAPICalled", on_console)


def main():
    ap = argparse.ArgumentParser(prog="ghostwire", description="Attach to a URL and capture runtime behavior over CDP.")
    ap.add_argument("url")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--proxy", default=None)
    ap.add_argument("--blackbox", default="", help="comma-separated script-url regexes to skip pausing in")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--hook", action="append", default=[], help="function expr to hook; 'expr@@urlpart' targets a worker/iframe")
    ap.add_argument("--grep", default=None, help="print scripts whose source contains this")
    ap.add_argument("--log", action="store_true", help="stream everything live: network, console, runtime-generated scripts, hooks")
    ap.add_argument("--out", default=None, help="save full trace JSON here")
    a = ap.parse_args()

    blackbox = [p for p in a.blackbox.split(",") if p.strip()] or None
    # in --log mode, wire the live handlers before navigating so page-load activity is streamed too
    gw = attach("about:blank" if a.log else a.url, headless=not a.headful, proxy=a.proxy, blackbox=blackbox)
    if a.log:
        _stream(gw.engine)
        gw.tracer.verbose = True
        gw.navigate(a.url)
    try:
        gw.wait(1.0)
        for h in a.hook:
            expr, _, url = h.partition("@@")
            try:
                gw.hook(expr, target_url=url or None)
                print(f"hooked {expr} ({url or 'page'})")
            except Exception as e:
                print(f"hook failed for {expr}: {e}")
        gw.wait(a.seconds)

        targets = gw.targets()
        print(f"\ntargets: {len(targets)}")
        for _, kind, url in targets:
            print(f"  {kind or '?':8} {(url or '')[:70]}")
        print(f"scripts: {len(gw.scripts.scripts)} ({len(gw.scripts.dynamic())} runtime-generated)")
        print(f"requests: {len(gw.net.all())}")
        print(f"hook captures: {len(gw.captures)}")
        if a.grep:
            hits = gw.scripts.search(a.grep)
            print(f"\nscripts containing {a.grep!r}: {len(hits)}")
            for s in hits[:5]:
                print(f"  [{s['target']}] {s['url'] or '(dynamic)'}: {(s['source'] or '')[:120]}")
        if a.out:
            gw.save(a.out)
            print(f"\nsaved trace to {a.out}")
    finally:
        gw.close()


if __name__ == "__main__":
    main()
