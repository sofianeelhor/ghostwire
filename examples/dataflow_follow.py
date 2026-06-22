# Real task: you found the request signer (sign), now you want to know where its output goes —
# which function consumes the signature and under what name — so you can read or patch it. The
# chain is onSubmit -> buildRequest -> sign; sign's return is consumed by buildRequest as the
# local `signature`. gw.follow breaks on sign, captures its return, steps out, and names the
# consumer variable.
import sys, os, threading, http.server, socketserver
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ghostwire

PAGE = b"""<!doctype html><html><body><script>
function sign(payload){ return "SIG_" + btoa(payload); }
function buildRequest(payload){
  var signature = sign(payload);
  var request = { body: payload, sig: signature };
  window.lastRequest = request;
  return request;
}
window.onSubmit = function(){ return buildRequest("payload42"); };
</script></body></html>"""


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
        gw.wait(1.0)
        result = gw.follow("window.sign", trigger="window.onSubmit()")

        print(f"following the return of sign()")
        print(f"  returned   : {result['returned'].get('value')!r} (type {result['returned'].get('type')})")
        print(f"  async      : {result['async']}")
        c = result.get("consumer") or {}
        print(f"  consumed by: {c.get('function')}  as variable {c.get('variable')!r}  (line {c.get('line')})")
        print(f"  confidence : {result['confidence']}")

        ok &= (c.get("function") == "buildRequest" and c.get("variable") == "signature")
        print("\nDATAFLOW FOLLOW:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        gw.close(); srv.shutdown()


if __name__ == "__main__":
    main()
