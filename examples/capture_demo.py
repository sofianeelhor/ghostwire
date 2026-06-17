"""
capture_demo.py — prove ghostwire sees what static tools / devtools-by-hand miss.

The page builds code at runtime via eval() and new Function() (invisible to "view
source" and the Network tab), POSTs a JSON body, and computes values on a timer.
ghostwire captures: (1) the runtime-generated source, (2) the POST request + body,
(3) live args of the hidden function — all at once, invisibly.

Run:  python3 examples/capture_demo.py
"""
import sys, os, time, json, threading, http.server, socketserver

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ghostwire import Engine, Tracer, ScriptWatcher, NetLog

PAGE = b"""<!doctype html><html><body><script>
eval("function hidden(x){ return x*7 + 1; }");           // runtime-generated code
var f = new Function('a','b','return a + "-" + b;');      // never in page source
window.combo = f('alpha','beta');
setTimeout(function(){
  fetch('/api/echo', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({user:'craig', n: hidden(3)})});
}, 500);
setInterval(function(){ window._h = hidden((Date.now() % 50)); }, 300);
</script></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE))); self.end_headers()
        self.wfile.write(PAGE)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); self.rfile.read(n)
        body = b'{"ok":true,"echo":"craig"}'
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass


def main():
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    eng = Engine(headless=True)
    try:
        scripts = ScriptWatcher(); net = NetLog(); tracer = Tracer()
        eng.add_probe(scripts); eng.add_probe(net); eng.add_probe(tracer)
        eng.start()
        eng.navigate(f"http://127.0.0.1:{port}/")
        time.sleep(1.2)
        tracer.hook("window.hidden")
        time.sleep(1.5)

        gen = [s for s in scripts.scripts if s["dynamic"] or "x*7" in (s["source"] or "") or 'a + "-"' in (s["source"] or "")]
        print(f"\n[scripts] {len(scripts.scripts)} parsed, runtime-generated of interest:")
        for s in gen:
            print("   dynamic=%s url=%r source=%r" % (s["dynamic"], s["url"], (s["source"] or "")[:60]))

        echo = net.find("/api/echo")
        print(f"\n[network] {len(net.all())} requests; POST /api/echo:")
        for e in echo:
            print("   %s %s  body=%s  status=%s  resp=%s" %
                  (e["method"], e["url"], e["post"], e["status"], e["body"]))

        print(f"\n[hook] hidden() live calls: {len(tracer.captures)}")
        for c in tracer.captures[:3]:
            print("   ", c["fn"], c["args"])

        found_eval = any("x*7" in (s["source"] or "") for s in scripts.scripts)
        found_fn = any('a + "-"' in (s["source"] or "") for s in scripts.scripts)
        found_post = any(e["method"] == "POST" and "craig" in (e["post"] or "") for e in echo)
        hooked = len(tracer.captures) >= 1
        print("\nchecks: eval-source=%s  Function-source=%s  POST-body=%s  hook=%s" %
              (found_eval, found_fn, found_post, hooked))
        ok = found_eval and found_fn and found_post and hooked
        print("CAPTURE DEMO:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        eng.close()
        srv.shutdown()


if __name__ == "__main__":
    main()
