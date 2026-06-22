# Real task: a feature gate lives in closure-captured state with NO path from window, so you
# cannot reach it with an eval. ghostwire finds it by heap search and patches it in place:
# raise the limit (set_props) and unlock a feature (apply), and the page's own functions then
# report the new behaviour. The demo asserts the object is unreachable by window first, so this
# is a genuine heap-only patch, not a window.x shortcut.
import sys, os, threading, http.server, socketserver
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ghostwire

PAGE = b"""<!doctype html><html><body><script>
(function(){
  var config = { tier: "free_marker", limit: 3, features: { export: false } };
  var used = 1;
  window.canExport  = function(){ return config.features.export; };
  window.remaining  = function(){ return config.limit - used; };
})();
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

        # precondition: the config is genuinely unreachable by any window expression
        reachable = gw.eval("typeof window.config")
        print(f"window.config reachable? {reachable}  (must be 'undefined' or this would be cheating)")
        ok &= reachable == "undefined"

        print(f"before: canExport()={gw.eval('canExport()')}  remaining()={gw.eval('remaining()')}")

        # raise the limit via set_props
        patched = gw.patch(key="tier", assign={"limit": 9999})
        print(f"patch limit: {patched.get('before')} -> {patched.get('after')}  (constructor={patched.get('object',{}).get('constructor')})")
        ok &= patched.get("patched") and patched["after"].get("limit") == 9999

        # unlock the nested feature flag via apply (a function run with this=config)
        unlocked = gw.patch(value="free_marker", apply="function(){ this.features.export = true; return this.features.export; }")
        print(f"apply unlock export -> {unlocked.get('apply_result')}")
        ok &= unlocked.get("patched") and unlocked.get("apply_result") is True

        after_export, after_remaining = gw.eval("canExport()"), gw.eval("remaining()")
        print(f"after : canExport()={after_export}  remaining()={after_remaining}")
        ok &= after_export is True and after_remaining == 9998   # 9999 - used(1)

        print("\nLIVE PATCH:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        gw.close(); srv.shutdown()


if __name__ == "__main__":
    main()
