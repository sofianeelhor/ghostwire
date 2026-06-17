"""
selftest.py — ground-truth proof that ghostwire's invisible hook works.

Serves a page whose secret(user, pin) runs on a timer, hooks it via CDP, and captures
the live arguments — then proves the hook is INVISIBLE: the page's own
window.secret.toString() is byte-identical to the source (no wrapper, no monkeypatch).

Run:  python3 examples/selftest.py
"""
import sys, os, time, threading, http.server, socketserver, urllib.request, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ghostwire import Engine, Tracer

PAGE = b"""<!doctype html><html><body><script>
function secret(user, pin){ return btoa(user + ':' + pin); }
window.__src = secret.toString();
let i = 0;
setInterval(function(){ window._r = secret('craig' + (i++), 1000 + i); }, 300);
</script></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE))); self.end_headers()
        self.wfile.write(PAGE)
    def log_message(self, *a): pass


def main():
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    eng = Engine(headless=True)
    try:
        t = Tracer(); eng.add_probe(t); eng.start()
        eng.navigate(f"http://127.0.0.1:{port}/")
        time.sleep(1.2)                                   # let the page load + define secret
        t.hook("window.secret")
        time.sleep(2.5)                                   # collect a few timer-driven calls

        print(f"\ncaptured {len(t.captures)} live calls")
        for c in t.captures[:4]:
            print("  ", c)

        # invisibility proof: the page never saw a modified function
        now = eng.send("Runtime.evaluate",
                       {"expression": "window.secret.toString()", "returnByValue": True})["result"]["value"]
        orig = eng.send("Runtime.evaluate",
                        {"expression": "window.__src", "returnByValue": True})["result"]["value"]
        print("\nsecret.toString() unchanged by the hook:", now == orig)
        print("  page sees:", now.replace("\n", " "))

        ok = len(t.captures) >= 2 and (now == orig)
        print("\nSELFTEST:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        eng.close()
        srv.shutdown()


if __name__ == "__main__":
    main()
