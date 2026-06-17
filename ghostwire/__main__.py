"""
CLI: attach to a URL, let it run, report what was captured, optionally save a trace.

    python3 -m ghostwire https://site/ --seconds 5 --grep token --out trace.json
    python3 -m ghostwire https://site/ --hook 'window.fn' --hook 'enc@@worker.js' --headful
"""
import argparse

from .api import attach


def main():
    ap = argparse.ArgumentParser(prog="ghostwire",
                                 description="Attach to a URL and capture runtime behavior over CDP.")
    ap.add_argument("url")
    ap.add_argument("--headful", action="store_true", help="show the browser window")
    ap.add_argument("--proxy", default=None, help="proxy server, e.g. http://host:port")
    ap.add_argument("--blackbox", default="", help="comma-separated script-url regexes to skip pausing in")
    ap.add_argument("--seconds", type=float, default=5.0, help="how long to observe after load")
    ap.add_argument("--hook", action="append", default=[],
                    help="function expression to hook (repeatable); use 'expr@@urlpart' to target a worker/iframe")
    ap.add_argument("--grep", default=None, help="print scripts whose source contains this")
    ap.add_argument("--out", default=None, help="save full trace JSON to this path")
    a = ap.parse_args()

    blackbox = [p for p in a.blackbox.split(",") if p.strip()] or None
    gw = attach(a.url, headless=not a.headful, proxy=a.proxy, blackbox=blackbox)
    try:
        gw.wait(1.0)
        for h in a.hook:
            expr, _, turl = h.partition("@@")
            try:
                gw.hook(expr, target_url=turl or None)
                print(f"hooked {expr} ({turl or 'root'})")
            except Exception as e:
                print(f"hook failed for {expr}: {e}")
        gw.wait(a.seconds)

        ts = gw.targets()
        print(f"\ntargets: {len(ts)}")
        for _, t, u in ts:
            print(f"  {t:8} {(u or '')[:70]}")
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
