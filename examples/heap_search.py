# Real task: locate where an auth token lives in a running SPA's memory, when you only know
# the token value (e.g. you saw it on the wire) and the code is minified. ghostwire snapshots
# the heap and finds the live object holding it — its constructor, the property, and the chain
# of references back to a GC root — plus search by constructor and by property key. 500 decoy
# objects are in the heap to show it is not just grepping source.
import sys, os, threading, http.server, socketserver
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ghostwire

TOKEN = "eyJhbGciOi_SECRET_9f3a2b"

PAGE = ("""<!doctype html><html><body><script>
window.__APP = (function(){
  function Session(t){ this.token = t; this.refresh = "rt_4421"; this.created = 1700000000; }
  var store = { user: { id: 7, name: "craig", session: new Session("%s") }, cart: [] };
  var cfg = { apiBase: "https://api.example.com", flags: { beta: true } };
  window.__noise = [];
  for (var i = 0; i < 500; i++) { window.__noise.push({ k: "row" + i, v: i * 3 }); }
  return { store: store, cfg: cfg };
})();
</script></body></html>""" % TOKEN).encode()


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", str(len(PAGE))); self.end_headers(); self.wfile.write(PAGE)
    def log_message(self, *a): pass


def main():
    srv = socketserver.TCPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ok = True
    gw = ghostwire.attach(f"http://127.0.0.1:{port}/", headless=True)
    try:
        gw.wait(1.2)

        by_value = gw.find_objects(value=TOKEN)
        print(f"objects holding the token value ({len(by_value)}):")
        for m in by_value:
            chain = " <- ".join(f"{p['holder']}.{p['via']}" for p in m["path"])
            print(f"  {m['constructor']}.{m['property']}  (id {m['id']})  path: {chain}")
        ok &= any(m["constructor"] == "Session" and m["property"] == "token" for m in by_value)
        ok &= any("Window" in p["holder"] for m in by_value for p in m["path"])

        by_ctor = gw.find_objects(constructor="Session")
        print(f"\nobjects with constructor Session: {len(by_ctor)}")
        ok &= len(by_ctor) >= 1

        by_key = gw.find_objects(key="refresh")
        print(f"objects with a 'refresh' property: {len(by_key)} -> {[m['constructor'] for m in by_key]}")
        ok &= any(m["constructor"] == "Session" for m in by_key)

        print("\nHEAP SEARCH:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        gw.close(); srv.shutdown()


if __name__ == "__main__":
    main()
