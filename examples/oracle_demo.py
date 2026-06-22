# minimal oracle check: hook a signer, build a corpus of (input -> output) pairs, then verify
# a correct candidate (passes), a subtly-wrong one that drops a +1 (fails with a counterexample),
# and a correct one against fresh live inputs with no corpus.
import sys, os, time, threading, http.server, socketserver, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ghostwire

# The "obfuscated" boundary. Truth: base64 of  user + ":" + (pin*7+1).
PAGE = b"""<!doctype html><html><body><script>
function sign(user, pin){ return btoa(user + ':' + (pin*7+1)); }
window.sign = sign;
let i = 0;
setInterval(function(){ window._r = sign('craig' + (i++), 100 + i); }, 120);
</script></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE))); self.end_headers()
        self.wfile.write(PAGE)
    def log_message(self, *a): pass


def main():
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    ok = True
    gw = ghostwire.attach(f"http://127.0.0.1:{port}/", headless=True)
    try:
        gw.wait(1.2)
        gw.hook("window.sign", capture_returns=True, label="sign")
        gw.wait(2.5)                                   # collect timer-driven calls

        corpus = gw.corpus("sign")
        print(f"corpus: {len(corpus)} observed (input -> output) pairs")
        for p in corpus[:3]:
            print("  ", p["input"], "->", p["output"])
        ok &= len(corpus) >= 5

        # The TRUE algorithm. Should verify against every observed pair.
        good = "(u,p)=>btoa(u+':'+(p*7+1))"
        r_good = gw.verify("window.sign", good, label="sign")
        print(f"\nCORRECT candidate   -> verified={r_good['verified']} "
              f"(tested {r_good['tested']}, matched {r_good['matched']})")
        ok &= r_good["verified"] is True and r_good["tested"] >= 5

        # The 39% trap: forgets the +1. Syntactically fine, semantically wrong.
        bad = "(u,p)=>btoa(u+':'+(p*7))"
        r_bad = gw.verify("window.sign", bad, label="sign")
        print(f"WRONG candidate     -> verified={r_bad['verified']} "
              f"(tested {r_bad['tested']}, matched {r_bad['matched']})")
        if r_bad["mismatches"]:
            m = r_bad["mismatches"][0]
            print(f"  counterexample: input={m['input']} expected={m['expected']!r} got={m['got']!r}")
        ok &= r_bad["verified"] is False and len(r_bad["mismatches"]) >= 1

        # Oracle works with NO prior corpus: fresh inputs, truth from the live function.
        fresh = [["zoe", 1], ["zoe", 2], ["alice", 999]]
        r_fresh = gw.verify("window.sign", good, fresh_inputs=fresh)
        print(f"\nFresh-input verify (no corpus) -> verified={r_fresh['verified']} "
              f"(tested {r_fresh['tested']})")
        print(f"  {r_fresh['coverage_notes']}")
        ok &= r_fresh["verified"] is True and r_fresh["tested"] == len(fresh)

        # Replayable artifact (includes the corpus for offline re-verification).
        art = os.path.join(os.path.dirname(__file__), "oracle_demo.trace.json")
        gw.save(art)
        saved = json.load(open(art))
        print(f"\nartifact: {art}  (corpus boundaries: {list(saved.get('corpus', {}).keys())})")
        ok &= "sign" in saved.get("corpus", {})

        print("\nORACLE DEMO:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        gw.close()
        srv.shutdown()


if __name__ == "__main__":
    main()
