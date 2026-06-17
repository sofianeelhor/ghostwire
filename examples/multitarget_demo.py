"""
multitarget_demo.py — prove ghostwire follows the whole target graph.

The page spawns a Web Worker (a separate CDP target) from a Blob. The worker defines a
function, runs it on a timer, and does its own fetch. ghostwire auto-attaches to the
worker and: (1) lists it as a target, (2) captures the worker's own source, (3) captures
the worker's network request, (4) hooks a function inside the worker.

Run:  python3 examples/multitarget_demo.py
"""
import sys, os, time, threading, http.server, socketserver

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ghostwire import Engine, Tracer, ScriptWatcher, NetLog

# worker fetches an ABSOLUTE url (relative urls resolve against the blob: base and fail)
PAGE = b"""<!doctype html><html><body><script>
var base = location.origin;
var code = [
  "function workerSecret(x){ return x*3 + 11; }",
  "setInterval(function(){ workerSecret((Date.now()%7)); }, 250);",
  "setTimeout(function(){ fetch('" + base + "/from-worker?v=' + workerSecret(5)); }, 500);"
].join("\\n");
new Worker(URL.createObjectURL(new Blob([code], {type:'application/javascript'})));
</script></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE if self.path == "/" else b"ok"
        ct = "text/html" if self.path == "/" else "text/plain"
        self.send_response(200); self.send_header("Content-Type", ct)
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

        targets = eng.targets()
        worker_url = next((u for _, t, u in targets if t == "worker"), None)
        print("targets:", [(t, (u or "")[:45]) for _, t, u in targets])

        # hook a function that lives INSIDE the worker target, by its url
        if worker_url:
            tracer.hook("workerSecret", target_url=worker_url)
        time.sleep(1.5)

        worker_scripts = [s for s in scripts.scripts if s["target"] == "worker" and "workerSecret" in (s["source"] or "")]
        worker_net = [e for e in net.all() if "/from-worker" in (e["url"] or "")]
        print("worker source captured (target=worker):", bool(worker_scripts))
        print("worker network captured:", worker_net[0]["url"] if worker_net else False,
              "(target=%s)" % (worker_net[0]["target"] if worker_net else "?"))
        print("hook captures inside worker:", len(tracer.captures))

        has_worker = worker_url is not None
        ok = has_worker and bool(worker_scripts) and bool(worker_net) and len(tracer.captures) >= 1
        print("\nMULTITARGET DEMO:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        eng.close()
        srv.shutdown()


if __name__ == "__main__":
    main()
