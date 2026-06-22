import argparse

from .api import attach


def main():
    ap = argparse.ArgumentParser(prog="ghostwire", description="Attach to a URL and capture runtime behavior over CDP.")
    ap.add_argument("url")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--proxy", default=None)
    ap.add_argument("--blackbox", default="", help="comma-separated script-url regexes to skip pausing in")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--hook", action="append", default=[], help="function expr to hook; 'expr@@urlpart' targets a worker/iframe")
    ap.add_argument("--grep", default=None, help="print scripts whose source contains this")
    ap.add_argument("--out", default=None, help="save full trace JSON here")
    a = ap.parse_args()

    blackbox = [p for p in a.blackbox.split(",") if p.strip()] or None
    gw = attach(a.url, headless=not a.headful, proxy=a.proxy, blackbox=blackbox)
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
