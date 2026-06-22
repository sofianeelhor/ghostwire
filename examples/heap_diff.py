# Real task: a function decrypts/decodes a payload and stashes the result somewhere you don't
# know. gw.heapdiff snapshots before and after the call and shows exactly what it allocated —
# here the decoded plaintext string and the byte buffer it built — so you recover the secret
# without knowing where it is stored. The plaintext is only produced at runtime, so a static
# read of the page would not show it.
import sys, os, base64, threading, http.server, socketserver
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ghostwire

SECRET = "secret_payload_" + "".join("%02x" % b for b in b"\x9f\x3a\x01")   # secret_payload_9f3a01
ENC = base64.b64encode(SECRET.encode()).decode()

PAGE = ("""<!doctype html><html><body><script>
window.decodePayload = function(){
  var plain = atob("%s");                       // decoded only at runtime
  var bytes = new Uint8Array(4096);
  for (var i = 0; i < plain.length; i++) bytes[i] = plain.charCodeAt(i);
  (window.__vault = window.__vault || new Map()).set("k" + Date.now(), { data: plain, raw: bytes });
  return plain.length;
};
</script></body></html>""" % ENC).encode()


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
        diff = gw.heapdiff("decodePayload()")

        secrets = [s for s in diff["new_strings"] if "secret_payload" in s]
        print(f"new strings revealing the decoded payload: {secrets[:2]}")
        print(f"new typed arrays: {diff['new_buffers'][:3]}")
        print(f"new objects by constructor (top): {dict(list(diff['new_objects_by_constructor'].items())[:6])}")

        ok &= any(s == SECRET for s in diff["new_strings"])
        ok &= any(b["type"] == "Uint8Array" for b in diff["new_buffers"]) or \
              "Uint8Array" in diff["new_objects_by_constructor"]

        print("\nHEAP DIFF:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        gw.close(); srv.shutdown()


if __name__ == "__main__":
    main()
