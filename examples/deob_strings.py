# Real task: recover an obfuscator.io-style string table whose array is rotated at load. Rather
# than reimplement the decoder (and get the rotation wrong), drive the page's OWN decoder over its
# index range and collect the resolved plaintext — ground truth that static deob cannot produce.
import sys, os, base64, threading, http.server, socketserver
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ghostwire

WORDS = ["guest", "admin", "token", "secret", "login", "hello", "world", "craig", "patched", "osint"]
ENCODED = [base64.b64encode(w.encode()).decode() for w in WORDS]

PAGE = ("""<!doctype html><html><body><script>
(function(){
  var arr = %s;
  (function(a, n){ while (n-- > 0) a.push(a.shift()); })(arr, 7);   // rotate at load
  window.dec = function(i){ return atob(arr[i]); };
})();
</script></body></html>""" % ENCODED).encode()


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
        result = gw.dump_strings("window.dec", start=0, count=64)
        print(f"dumped {result['count']} strings by driving the real decoder:")
        for i in sorted(result["decoded"]): print(f"  [{i}] {result['decoded'][i]}")

        recovered = set(result["decoded"].values())
        ok &= recovered == set(WORDS)
        print("\nDEOB STRINGS:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        gw.close(); srv.shutdown()


if __name__ == "__main__":
    main()
