# Real task: an SDK hashes/signs something and you want to see the plaintext it feeds to crypto,
# without reading the obfuscated code. gw.crypto breakpoint-on-calls the native crypto.subtle.*
# (invisible — never wrapped) and decodes the buffers in the caller's scope, recovering the
# message bytes as they cross the boundary. The plaintext only exists at runtime.
import sys, os, threading, http.server, socketserver
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ghostwire

PAGE = b"""<!doctype html><html><body><script>
function hashMessage(){
  var n = (window.__n = (window.__n || 0) + 1);
  var data = new TextEncoder().encode("MSG_to_hash_craig_" + n);
  return crypto.subtle.digest("SHA-256", data);
}
setInterval(hashMessage, 200);
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
        entries = gw.crypto(duration=3.0)

        digests = [e for e in entries if e["method"] == "crypto.subtle.digest"]
        print(f"crypto.subtle.digest calls captured: {len(digests)}")
        for e in digests[:3]:
            plaintext = next((i for i in e["inputs"] if i.get("utf8", "").startswith("MSG_to_hash_craig")), None)
            print(f"  caller={e['caller']}  plaintext input -> {plaintext.get('utf8') if plaintext else None!r}")

        ok &= len(digests) >= 2
        ok &= any(any(i.get("utf8", "").startswith("MSG_to_hash_craig") for i in e["inputs"]) for e in digests)

        print("\nCRYPTO LOG:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        gw.close(); srv.shutdown()


if __name__ == "__main__":
    main()
