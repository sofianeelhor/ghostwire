# Real task: a bytecode VM hides logic behind a dispatch loop. Set a breakpoint at the loop and
# log (pc, opcode, operand) on every iteration to recover the executed program — a trace an
# auto-disassembler can lift. The VM is served as its own file so the line numbers are stable.
import sys, os, threading, http.server, socketserver
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ghostwire

VM_JS = """window.runVM = function(program){
  var stack = [], pc = 0, acc = 0;
  while (pc < program.length) {
    var op = program[pc][0], arg = program[pc][1];   // DISPATCH
    if (op === 0) stack.push(arg);
    else if (op === 1) { var b = stack.pop(), a = stack.pop(); stack.push(a + b); }
    else if (op === 2) acc = stack.pop();
    pc++;
  }
  window.__acc = acc;
  return acc;
};"""
DISPATCH_LINE = next(i for i, l in enumerate(VM_JS.splitlines(), 1) if "DISPATCH" in l)
HTML = b"""<!doctype html><html><body><script src="/vm.js"></script></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = VM_JS.encode() if self.path.endswith("vm.js") else HTML
        ctype = "application/javascript" if self.path.endswith("vm.js") else "text/html"
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


def main():
    srv = socketserver.TCPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ok = True
    gw = ghostwire.attach(f"http://127.0.0.1:{port}/", headless=True)
    try:
        gw.wait(1.2)
        result = gw.vm_watch("vm\\.js", DISPATCH_LINE,
                             watch=["pc", "program[pc][0]", "program[pc][1]"],
                             trigger="window.runVM([[0,5],[0,3],[1,0],[2,0]])")
        print(f"dispatch loop traced for {result.get('hits')} iterations:")
        for row in result.get("trace", []):
            print(f"  pc={row['pc']}  opcode={row['program[pc][0]']}  operand={row['program[pc][1]']}")

        ops = [row["program[pc][0]"] for row in result.get("trace", [])]
        pcs = [row["pc"] for row in result.get("trace", [])]
        ok &= result.get("hits") == 4 and ops == [0, 0, 1, 2] and pcs == [0, 1, 2, 3]
        print("\nDEOB VM:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        gw.close(); srv.shutdown()


if __name__ == "__main__":
    main()
