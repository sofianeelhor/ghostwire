"""
mcp_server.py — expose ghostwire to Claude Code as MCP tools (the agent loop).

ghostwire is the hands and eyes (deterministic, invisible runtime capture across the
whole target graph); Claude is the analyst that decides what to hook next, reads the
traces, and synthesizes a verified reimplementation.

Run standalone:   python3 -m ghostwire.mcp_server   (or the `ghostwire-mcp` entry point)
Register:         claude mcp add ghostwire -- ghostwire-mcp
"""
import json
from mcp.server.fastmcp import FastMCP

from .api import attach

mcp = FastMCP("ghostwire")
_S = {"gw": None}


def _gw():
    if not _S["gw"]:
        raise RuntimeError("not attached: call gw_attach(url) first")
    return _S["gw"]


@mcp.tool()
def gw_attach(url: str, headless: bool = True, proxy: str = "", blackbox: str = "") -> str:
    """Launch Chrome, attach across all targets (page, workers, iframes), enable the
    scripts/network/tracer probes, and navigate to url. blackbox is a comma-separated
    list of script-url regexes the debugger must not pause inside (anti-debug evasion)."""
    gw_close()
    bb = [p for p in blackbox.split(",") if p.strip()] or None
    _S["gw"] = attach(url, headless=headless, proxy=proxy or None, blackbox=bb)
    return f"attached, navigating to {url} (headless={headless})"


@mcp.tool()
def gw_targets() -> str:
    """List attached targets: [session_id, type, url]. type is page/iframe/worker/etc."""
    return json.dumps(_gw().targets(), indent=1)


@mcp.tool()
def gw_eval(expression: str, target_url: str = "") -> str:
    """Evaluate a JS expression in the page (or in the worker/iframe whose url contains
    target_url) and return the value."""
    return json.dumps(_gw().eval(expression, target_url=target_url or None))


@mcp.tool()
def gw_hook(expression: str, target_url: str = "", capture_returns: bool = False,
            label: str = "") -> str:
    """Set an invisible breakpoint-on-call hook on the function expression resolves to,
    in the root page or in the worker/iframe whose url contains target_url. With
    capture_returns=True, also capture (input -> output) pairs into a ground-truth corpus
    (under `label`, default = expression) that gw_verify checks candidate code against —
    this is how you build the corpus for the verification oracle."""
    _gw().hook(expression, target_url=target_url or None,
               capture_returns=capture_returns, label=label or None)
    mode = "input->output pairs" if capture_returns else "args only"
    return f"hooked {expression} in {target_url or 'root'} ({mode})"


@mcp.tool()
def gw_captures(limit: int = 50) -> str:
    """Recent hooked-call captures: [{session, fn, args, stack}]."""
    return json.dumps(_gw().captures[-limit:], indent=1)


@mcp.tool()
def gw_corpus(label: str = "", limit: int = 20, full: bool = False) -> str:
    """Inspect the ground-truth (input -> output) corpus captured by capture_returns hooks.
    With no label, returns {label: pair_count} for every captured boundary. With a label,
    returns the captured pairs (slim: inputs/outputs truncated unless full=True). This is
    the ground truth gw_verify checks against; it is also saved by gw_save for offline
    re-verification."""
    gw = _gw()
    if not label:
        return json.dumps(gw.corpora(), indent=1)
    pairs = gw.corpus(label)[-limit:]
    if not full:
        def trim(v):
            s = json.dumps(v, default=str)
            return v if len(s) <= 200 else (s[:200] + "…")
        pairs = [{"input": trim(p.get("input")), "output": trim(p.get("output")),
                  "output_type": p.get("output_type"),
                  **({"unserialized": p["unserialized"]} if p.get("unserialized") else {})}
                 for p in pairs]
    return json.dumps({"label": label, "shown": len(pairs), "total": len(gw.corpus(label)),
                       "pairs": pairs}, indent=1, default=str)


@mcp.tool()
def gw_verify(real_fn_expr: str, candidate: str, label: str = "", fresh_inputs: str = "",
              target_url: str = "", sample: int = 200, max_mismatches: int = 5) -> str:
    """THE GATE. Check an agent's candidate reimplementation against ground truth before
    trusting it (charter §2 — nothing inferred is trusted until verified here).

    real_fn_expr : a JS expression resolving to the REAL function on the target (e.g.
                   "window.sign"); used to generate fresh ground truth for fresh_inputs.
    candidate    : a JS expression evaluating to a function — your reimplementation. It is
                   run in an ISOLATED page (cannot see or call the real function).
    label        : corpus label to test against (observed pairs from a capture_returns hook).
    fresh_inputs : JSON array of inputs to additionally test; each input is an argument
                   list, e.g. '[["craig",5],["dave",9]]'. The real function is invoked live
                   to get their true outputs. Use this to probe inputs the corpus never hit.
    target_url   : restrict the live real-function query to a worker/iframe by url substring.

    Returns {verified, tested, matched, mismatches:[{input,expected,got,source}],
    coverage_notes}. verified is True only if >=1 input was tested and every verifiable
    input matched. Iterate on `candidate` until mismatches is empty."""
    try:
        fresh = json.loads(fresh_inputs) if fresh_inputs.strip() else None
    except Exception as e:
        return json.dumps({"error": f"fresh_inputs must be a JSON array: {e}"})
    res = _gw().verify(real_fn_expr, candidate, label=label or None, fresh_inputs=fresh,
                       target_url=target_url or None, sample=sample, max_mismatches=max_mismatches)
    return json.dumps(res, indent=1, default=str)


@mcp.tool()
def gw_scripts(search: str = "", dynamic_only: bool = False, full: bool = False) -> str:
    """List parsed scripts across all targets (incl. eval/new Function/worker code).
    search filters by source substring; dynamic_only keeps runtime-generated code;
    full returns full source."""
    sw = _gw().scripts
    items = sw.search(search) if search else (sw.dynamic() if dynamic_only else sw.scripts)
    out = [{"scriptId": s["scriptId"], "target": s["target"], "url": s["url"], "dynamic": s["dynamic"],
            "length": s["length"], "source": (s["source"] if full else (s["source"] or "")[:300])}
           for s in items[:40]]
    return json.dumps(out, indent=1)


@mcp.tool()
def gw_network(url_substr: str = "", full_bodies: bool = False) -> str:
    """List captured requests across all targets: method, url, status, target, initiator,
    post body, and a response-body preview. Filter by a url substring. Bodies are
    previewed (length + first 400 chars) so the result stays small; pass full_bodies=True
    for complete bodies, or use gw_save() to write the full trace to disk."""
    net = _gw().net
    items = net.find(url_substr) if url_substr else net.all()
    out = []
    for e in items[-60:]:
        body = e.get("body")
        rec = {"target": e.get("target"), "method": e.get("method"), "url": e.get("url"),
               "status": e.get("status"), "initiator": e.get("initiator"),
               "post": (e.get("post") if full_bodies else (e.get("post") or "")[:400]),
               "body_len": len(body) if body else 0}
        rec["body"] = body if full_bodies else ((body or "")[:400] or None)
        out.append(rec)
    return json.dumps(out, indent=1, default=str)


@mcp.tool()
def gw_navigate(url: str) -> str:
    """Navigate the page to a new url."""
    _gw().navigate(url)
    return f"navigating to {url}"


@mcp.tool()
def gw_save(path: str) -> str:
    """Save the full trace (targets, scripts, network, captures) as JSON to path."""
    return f"saved trace to {_gw().save(path)}"


@mcp.tool()
def gw_close() -> str:
    """Close the browser session and free resources."""
    if _S["gw"]:
        try:
            _S["gw"].close()
        except Exception:
            pass
    _S["gw"] = None
    return "closed"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
