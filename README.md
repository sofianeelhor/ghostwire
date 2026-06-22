# ghostwire

Stealth runtime instrumentation for web reverse engineering, built on the Chrome
DevTools Protocol. It hooks functions, captures network and dynamically generated code,
and follows the whole target graph (page, workers, iframes) without injecting anything
into the page. Designed to be driven by Claude Code over MCP as the analyst loop.


## Why CDP instead of in-page injection

Hooks are set with `Debugger.setBreakpointOnFunctionCall` on the live function object.
The object is never replaced or wrapped, so the hook is invisible to the page's own
defenses: `fn.toString()` native-code checks, Proxy traps, monkeypatch detection. That
invisibility is the point. It is what lets us instrument hostile, anti-debug, obfuscated
client-side JS (anti-bot, captcha, fingerprinting, fraud and payment SDKs).

Other consequences of working at the protocol level:

- Real scope. `evaluateOnCallFrame` reads the actual closure, not an isolated world.
- Network is captured regardless of when (or whether) page scripts wrapped fetch/XHR.
- Beats static deob. It observes resolved values, so it cracks runtime-rotated string
  arrays and bytecode VMs that static tools cannot.
- Whole target graph. Auto-attach follows workers and out-of-process iframes, where the
  interesting code (crypto, anti-bot engines) usually runs.

## Status

| Piece | State |
|---|---|
| CDP client (thread-safe reply/event demux, session routing) | done (`ghostwire/cdp.py`) |
| Browser launch/attach, stealth flags, real Chrome | done (`ghostwire/browser.py`) |
| Engine: auto-attach to workers/iframes, per-session domains | done (`ghostwire/engine.py`) |
| Invisible function tracer, hook any target by url | done (`ghostwire/tracer.py`) |
| scripts probe: eval/new Function/worker/injected code | done (`ghostwire/probes/scripts.py`) |
| network probe: req/resp/body/initiator, all targets | done (`ghostwire/probes/network.py`) |
| High-level API (`attach()` + `Inspector`, trace export) | done (`ghostwire/api.py`) |
| CLI (`python -m ghostwire URL ...`) | done (`ghostwire/__main__.py`) |
| MCP server, 10 tools for Claude Code | done (`ghostwire/mcp_server.py`) |
| Packaging (pyproject, entry point, LICENSE) | done |
| **verification oracle** (`gw_verify`: candidate vs ground-truth corpus + live target) | **done (`ghostwire/oracle.py`)** |
| return-value capture (input→output pairs into a per-boundary corpus) | done (`tracer.py`, `capture_returns=`) |
| selftest, capture, multitarget, oracle demos | done (all PASS) |
| anti-anti-debug (`setBlackboxPatterns` wired) | partial, needs hardening on real targets |
| dataflow / followReturn (consumer-frame, async) | todo |
| crypto-logger (plaintext/ciphertext/key at the JS boundary) | todo |
| deobfuscator (string-array dump: obfuscator.io + basE91) | todo |
| vm-tracer (dispatch-loop log to auto-disasm) | todo |

## Run it

No install. The core has no third-party dependencies — it drives Chrome over an OS pipe
(`--remote-debugging-pipe`), so there is no debugging port to open and no WebSocket library
to install. Clone the repo and run:

```bash
python3 -m ghostwire https://target/ --seconds 5 --grep token --out trace.json
```

Only the MCP server needs a package (`pip install mcp`); everything else runs from the
checkout.

## Use it on any target

One-liner API:

```python
import ghostwire
with ghostwire.attach("https://target/", blackbox=[r"antidebug"]) as gw:
    gw.wait(3)
    gw.hook("window.someFn")                       # root page
    gw.hook("engineInternal", target_url="worker.js")  # inside a worker/iframe
    gw.wait(2)
    gw.save("trace.json")                          # replayable artifact
    print(gw.targets(), len(gw.scripts.scripts), len(gw.net.all()), len(gw.captures))
```

CLI:

```bash
python3 -m ghostwire https://target/ --seconds 5 --grep token --out trace.json
python3 -m ghostwire https://target/ --hook 'window.fn' --hook 'enc@@worker.js' --headful
```

Demos (also a regression suite):

```bash
python3 examples/selftest.py         # invisible hook + live args
python3 examples/capture_demo.py     # hidden code (eval/new Function) + network + hook
python3 examples/multitarget_demo.py # worker auto-attach: source + network + hook
python3 examples/oracle_demo.py      # corpus capture + gw_verify catches a wrong reimpl
```

## Use from Claude Code

```bash
pip install mcp     # the only dependency, and only for the server
claude mcp add ghostwire -- python3 -m ghostwire.mcp_server   # run from the repo checkout
```

Tools: `gw_attach(url, headless, proxy, blackbox)`, `gw_targets()`,
`gw_hook(expr, target_url, capture_returns, label)`, `gw_captures()`,
`gw_corpus(label, limit, full)`, `gw_verify(real_fn_expr, candidate, label, fresh_inputs, ...)`,
`gw_scripts(search, dynamic_only, full)`, `gw_network(url_substr)`, `gw_eval(expr, target_url)`,
`gw_navigate(url)`, `gw_save(path)`, `gw_close()`.

The loop: ghostwire does deterministic, invisible capture across all targets. Claude forms
a hypothesis, sets the next hook, reads the captures, refines, then synthesizes a
reimplementation — and `gw_verify` is the gate: it runs that candidate, in an isolated
page, against the observed ground-truth corpus **and** against the live function for fresh
inputs, returning a structured diff with concrete counterexamples. Nothing inferred is
trusted until the diff is empty. See `skills/verify-reimplementation/`.

### The verification oracle (`gw_verify`)

The differentiator. An LLM reading obfuscated JS is right about *syntax* ~97% of the time
but about *behaviour* only ~61% (JsDeObsBench). `gw_verify` is what catches the other 39%:

```python
with ghostwire.attach(url) as gw:
    gw.wait(1); gw.hook("window.sign", capture_returns=True, label="sign"); gw.wait(3)
    gw.corpus("sign")                                  # observed (input -> output) pairs
    gw.verify("window.sign", "(u,p)=>btoa(u+':'+(p*7+1))", label="sign",
              fresh_inputs=[["zoe", 0], ["", 1]])      # -> {verified, tested, mismatches, ...}
```

Captured pairs are saved into the trace artifact (`gw_save`) so any claim can be
re-verified offline.

## Layout

```
ghostwire/
  cdp.py        CDP transport
  browser.py    launch/attach Chrome
  engine.py     auto-attach + session routing + probe wiring
  tracer.py     invisible function hooks + (input->output) corpus capture
  oracle.py     verification oracle: candidate vs corpus + live target, structured diff
  probes/
    scripts.py  all parsed code, incl. runtime-generated
    network.py  all traffic, all targets
  api.py        attach() + Inspector (high-level reuse API)
  __main__.py   CLI
  mcp_server.py MCP tools
examples/
  selftest.py  capture_demo.py  multitarget_demo.py  oracle_demo.py
skills/
  verify-reimplementation/   Agent Skill: hypothesize -> capture -> verify recipe
pyproject.toml  LICENSE  requirements.txt  .mcp.json
```

## Caveats

It is an arms race. CDP presence itself is detectable, so the stealth layer is ongoing
maintenance, not a one-time fix. Anything an LLM infers from traces must be checked
against ground truth before it is trusted.

## Next

P0 (the verification oracle) is done and is the gate on every "I understood it" claim.
Next, in priority order: P1 origin-trace/BDHS ("where did this value come from?"),
P2 followReturn dataflow ("where does it go next?"), then the crypto-logger and
string-array/VM deobfuscator probes — each feeding pairs straight into the oracle corpus.
Primary acceptance target stays the Continental captcha on patched.to (where we hold the
hand-derived answer key): success is the agent loop re-deriving that key autonomously
through MCP and `gw_verify` confirming equivalence against the live target.
```
