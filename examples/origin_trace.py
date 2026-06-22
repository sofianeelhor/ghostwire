# Real task: you saw an auth token on the wire and want the exact function that builds it, in a
# minified bundle where reading the source is slow and error-prone. A click runs
# onGo -> buildAuth -> sign, and sign is where the token is first assembled. gw.origin breaks on
# the handler, steps with framework code blackboxed, snapshots at each user-land return, and
# reports that sign() is where the value first appears in the heap.
import sys, os, base64, threading, http.server, socketserver
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ghostwire

TOKEN = "AUTH_" + base64.b64encode(b"craig:secret").decode()   # AUTH_Y3JhaWc6c2VjcmV0

PAGE = b"""<!doctype html><html><body><button id="go">go</button><script>
function sign(user){ return "AUTH_" + btoa(user + ":secret"); }
function buildAuth(user){ var s = sign(user); window.session = { token: s }; return s; }
window.onGo = function(){ return buildAuth("craig"); };
document.getElementById("go").addEventListener("click", window.onGo);
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
        result = gw.origin(TOKEN, enter="window.onGo", trigger="document.getElementById('go').click()")

        print(f"tracing origin of {TOKEN!r}")
        print(f"  found      : {result['found']}  confidence: {result['confidence']}")
        if result.get("origin"):
            o = result["origin"]
            print(f"  origin     : {o['function']}  ({o['url'].split('/')[-1]}:{o['line']})")
        print(f"  pre-existed: {result['preexisting']}")
        print("  trail (user-land returns, earliest first):")
        for step in result["trail"]:
            print(f"    {step['function']:12} line {step['line']:<3} value_present_after={step['value_present_after']}")

        ok &= result["found"] and result["origin"] and result["origin"]["function"] == "sign"
        print("\nORIGIN TRACE:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        gw.close(); srv.shutdown()


if __name__ == "__main__":
    main()
