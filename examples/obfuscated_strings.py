# Real scenario: an obfuscator.io-style string array that is rotated at load by an IIFE, so
# the array order in the source is a lie. A static reader (or an LLM copying the source array)
# reimplements decode() against the wrong order and is silently wrong. ghostwire drives the
# page's own decoder over the full index range to get ground truth, and gw_verify catches the
# wrong reimplementation and confirms the right one.
import sys, os, json, time, threading, http.server, socketserver

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ghostwire

# the array exactly as it appears in source (pre-rotation); a model copying it sees this order
SOURCE_ARRAY = ["Z3Vlc3Q=", "YWRtaW4=", "dG9rZW4=", "c2VjcmV0", "bG9naW4=",
                "aGVsbG8=", "d29ybGQ=", "Y3JhaWc=", "cGF0Y2hlZA==", "b3NpbnQ="]
ROTATION = 7

PAGE = ("""<!doctype html><html><body><script>
(function(){
  var _0x = %s;
  (function(arr, n){ while (n-- > 0) { arr['push'](arr['shift']()); } })(_0x, %d);
  function _0x21f(_0xi){ _0xi = _0xi - 0x0; return atob(_0x[_0xi]); }
  window.decode = _0x21f;
  var _0xn = _0x.length;
  setInterval(function(){ window._r = decode(Math.floor(Math.random() * _0xn)); }, 60);
})();
</script></body></html>""" % (json.dumps(SOURCE_ARRAY), ROTATION)).encode()

INDICES = list(range(len(SOURCE_ARRAY)))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE))); self.end_headers(); self.wfile.write(PAGE)
    def log_message(self, *a): pass


def main():
    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    ok = True
    gw = ghostwire.attach(f"http://127.0.0.1:{port}/", headless=True)
    try:
        gw.wait(1.2)

        # drive the obfuscator's own decoder over the full index range -> runtime ground truth
        runtime = {i: gw.eval(f"window.decode({i})") for i in INDICES}
        print("ground truth (runtime, rotated):")
        print("  " + ", ".join(f"{i}={runtime[i]}" for i in INDICES))

        # what a static reader / LLM writes by copying the source array, ignoring the rotation
        naive = "(i)=>atob(" + json.dumps(SOURCE_ARRAY) + "[i])"
        static_view = {i: __import__("base64").b64decode(SOURCE_ARRAY[i]).decode() for i in INDICES}
        print("\nstatic view (source order, what an LLM would copy):")
        print("  " + ", ".join(f"{i}={static_view[i]}" for i in INDICES))
        print(f"\nstatic order matches runtime for {sum(runtime[i]==static_view[i] for i in INDICES)}/{len(INDICES)} indices "
              f"-> rotation makes the source a lie")

        # the oracle catches the naive reimplementation against fresh live ground truth
        r_naive = gw.verify("window.decode", naive, fresh_inputs=[[i] for i in INDICES])
        print(f"\nnaive static reimpl -> verified={r_naive['verified']} (matched {r_naive['matched']}/{r_naive['tested']})")
        for m in r_naive["mismatches"][:3]:
            print(f"  counterexample: decode({m['input'][0]}) is {m['expected']!r}, naive gives {m['got']!r}")
        ok &= r_naive["verified"] is False and len(r_naive["mismatches"]) >= 1

        # the reimplementation derived from runtime ground truth verifies, incl. fresh live queries
        table = "(i)=>(" + json.dumps({str(i): runtime[i] for i in INDICES}) + ")[i]"
        r_table = gw.verify("window.decode", table, fresh_inputs=[[i] for i in INDICES])
        print(f"\nruntime-derived reimpl -> verified={r_table['verified']} (matched {r_table['matched']}/{r_table['tested']})")
        ok &= r_table["verified"] is True and r_table["tested"] == len(INDICES)

        # and the passive corpus path: the page calls decode() on its own; we capture the pairs
        gw.hook("window.decode", capture_returns=True, label="decode")
        gw.wait(2.0)
        pairs = gw.corpus("decode")
        print(f"\npassive corpus from the page's own calls: {len(pairs)} (input -> output) pairs")
        ok &= len(pairs) >= 5

        artifact = os.path.join(os.path.dirname(__file__), "obfuscated_strings.trace.json")
        gw.save(artifact)
        print(f"artifact: {artifact}")

        print("\nOBFUSCATED STRINGS:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        gw.close(); server.shutdown()


if __name__ == "__main__":
    main()
