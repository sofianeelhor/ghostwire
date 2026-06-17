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

- Real scope. `evaluateOnCallFrame` reads the actual closure, not an isolated world like playwright with patchright.
- Network is captured regardless of when (or whether) page scripts wrapped fetch/XHR.
- Beats static deob. It observes resolved values, so it cracks runtime-rotated string
  arrays and bytecode VMs.
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
| selftest, capture, multitarget demos | done |
| anti-anti-debug (`setBlackboxPatterns` wired) | partial, i needs to test this on more real targets |
| return-value / dataflow (followReturn) capture | todo |
| crypto-logger (plaintext/ciphertext/key at the JS boundary) | todo |
| vm-tracer (dispatch-loop log to auto-disasm) | todo |

## Install

```bash
pip install -e .            # or: pip install -r requirements.txt
```

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
```

## Use from Claude Code

```bash
pip install -e .
claude mcp add ghostwire -- ghostwire-mcp
```

Tools: `gw_attach(url, headless, proxy, blackbox)`, `gw_targets()`, `gw_hook(expr, target_url)`,
`gw_captures()`, `gw_scripts(search, dynamic_only, full)`, `gw_network(url_substr)`,
`gw_eval(expr, target_url)`, `gw_navigate(url)`, `gw_save(path)`, `gw_close()`.

The loop: ghostwire does deterministic, invisible capture across all targets. Claude forms
a hypothesis, sets the next hook, reads the captures, refines, then synthesizes a
reimplementation and verifies it against captured ground truth.

## Layout

```
ghostwire/
  cdp.py        CDP transport
  browser.py    launch/attach Chrome
  engine.py     auto-attach + session routing + probe wiring
  tracer.py     invisible function hooks
  probes/
    scripts.py  all parsed code, incl. runtime-generated
    network.py  all traffic, all targets
  api.py        attach() + Inspector (high-level reuse API)
  __main__.py   CLI
  mcp_server.py MCP tools
examples/
  selftest.py  capture_demo.py  multitarget_demo.py
pyproject.toml  LICENSE  requirements.txt  .mcp.json
```

## Caveats

It is an arms race. CDP presence itself is detectable, so the stealth layer is ongoing
maintenance, not a one-time fix. Anything an LLM infers from traces must be checked
against ground truth before it is trusted.

