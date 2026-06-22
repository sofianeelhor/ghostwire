import json
from mcp.server.fastmcp import FastMCP

from .api import attach

mcp = FastMCP("ghostwire")
session = None


def gw():
    if session is None:
        raise RuntimeError("not attached: call gw_attach(url) first")
    return session


@mcp.tool(description=(
    "Launch Chrome over a debug pipe (no open port), attach across page+workers+iframes, "
    "enable the script/network/tracer probes, navigate to url. blackbox is a comma-separated "
    "list of script-url regexes the debugger must not pause inside (anti-debug evasion)."))
def gw_attach(url: str, headless: bool = True, proxy: str = "", blackbox: str = "") -> str:
    global session
    gw_close()
    patterns = [p for p in blackbox.split(",") if p.strip()] or None
    session = attach(url, headless=headless, proxy=proxy or None, blackbox=patterns)
    return f"attached, navigating to {url} (headless={headless})"


@mcp.tool(description="List attached targets as [session_id, type, url]; type is page/iframe/worker/etc.")
def gw_targets() -> str:
    return json.dumps(gw().targets(), indent=1)


@mcp.tool(description=(
    "Evaluate a JS expression in the page, or in the worker/iframe whose url contains "
    "target_url, and return the value."))
def gw_eval(expression: str, target_url: str = "") -> str:
    return json.dumps(gw().eval(expression, target_url=target_url or None))


@mcp.tool(description=(
    "Set an invisible breakpoint-on-call hook on the function that expression resolves to, "
    "in the page or the worker/iframe matching target_url. With capture_returns=True, record "
    "(input -> output) pairs into a ground-truth corpus under label (default = expression) for "
    "gw_verify to check candidate code against."))
def gw_hook(expression: str, target_url: str = "", capture_returns: bool = False, label: str = "") -> str:
    gw().hook(expression, target_url=target_url or None, capture_returns=capture_returns, label=label or None)
    return f"hooked {expression} in {target_url or 'page'} ({'input->output' if capture_returns else 'args'})"


@mcp.tool(description="Recent arg-only hook captures: [{session, fn, args, stack}].")
def gw_captures(limit: int = 50) -> str:
    return json.dumps(gw().captures[-limit:], indent=1)


@mcp.tool(description=(
    "Inspect the ground-truth (input -> output) corpus from capture_returns hooks. No label: "
    "{label: count} for every boundary. With a label: the captured pairs (truncated unless full)."))
def gw_corpus(label: str = "", limit: int = 20, full: bool = False) -> str:
    inspector = gw()
    if not label:
        return json.dumps(inspector.corpora(), indent=1)
    pairs = inspector.corpus(label)
    shown = pairs[-limit:]
    if not full:
        shown = [{"input": _clip(p.get("input")), "output": _clip(p.get("output")),
                  "output_type": p.get("output_type"), "unserialized": p.get("unserialized")} for p in shown]
    return json.dumps({"label": label, "total": len(pairs), "shown": len(shown), "pairs": shown},
                      indent=1, default=str)


@mcp.tool(description=(
    "The gate: check a candidate reimplementation against ground truth before trusting it. "
    "real_fn_expr resolves to the real function on the target (used to make fresh ground truth). "
    "candidate is a JS expression evaluating to a function; it runs in an isolated page and "
    "cannot see the real function. label tests against an observed corpus. fresh_inputs is a JSON "
    "array of argument-lists, e.g. '[[\"craig\",5],[\"dave\",9]]', whose true outputs come from "
    "invoking the real function live. Returns {verified, tested, matched, mismatches, coverage_notes}; "
    "iterate on candidate until mismatches is empty."))
def gw_verify(real_fn_expr: str, candidate: str, label: str = "", fresh_inputs: str = "",
              target_url: str = "", sample: int = 200, max_mismatches: int = 5) -> str:
    try:
        fresh = json.loads(fresh_inputs) if fresh_inputs.strip() else None
    except Exception as e:
        return json.dumps({"error": f"fresh_inputs must be a JSON array: {e}"})
    return json.dumps(gw().verify(real_fn_expr, candidate, label=label or None, fresh_inputs=fresh,
                                  target_url=target_url or None, sample=sample, max_mismatches=max_mismatches),
                      indent=1, default=str)


@mcp.tool(description=(
    "Find live objects in the heap by a value they hold, by constructor name, or by a property "
    "key. Returns each match's constructor, the holding property, its heap id, and a short "
    "retaining path to a GC root — i.e. where the value lives and what reaches it. Takes a fresh "
    "snapshot of the page (or a worker/iframe via target_url) each call."))
def gw_objects(value: str = "", constructor: str = "", key: str = "", target_url: str = "", limit: int = 20) -> str:
    matches = gw().find_objects(value=value or None, constructor=constructor or None,
                                key=key or None, target_url=target_url or None, limit=limit)
    return json.dumps(matches, indent=1, default=str)


@mcp.tool(description=(
    "Origin trace: find the user-land function whose return first makes `value` appear in the "
    "heap (where the value came from). Breaks on `enter` (a JS expression resolving to a function "
    "on the path to the creation, e.g. an event handler), optionally provoked by the JS expression "
    "`trigger` (e.g. \"document.querySelector('#go').click()\"), then steps with framework code "
    "blackboxed, snapshotting at each user-land return. Returns the origin {function,url,line}, the "
    "stepped trail, and whether the value pre-existed the trace. Comma-separated blackbox regexes "
    "override the framework defaults."))
def gw_origin(value: str, enter: str, trigger: str = "", target_url: str = "",
              blackbox: str = "", max_steps: int = 300) -> str:
    patterns = [p for p in blackbox.split(",") if p.strip()] if blackbox else None
    result = gw().origin(value, enter, trigger=trigger or None, target_url=target_url or None,
                         blackbox=patterns, max_steps=max_steps)
    return json.dumps(result, indent=1, default=str)


@mcp.tool(description=(
    "Dataflow / followReturn: follow a producer function's return value into its consumer's frame "
    "(where it goes next). Breaks on `producer` (a JS expression resolving to a function), "
    "optionally provoked by `trigger`, captures what it returns, steps out, and reports the consumer "
    "{function,url,line,variable} that now holds the value — a live variable you can then read or "
    "patch. A Promise return is reported as async (the resolved consumer is the await continuation)."))
def gw_follow(producer: str, trigger: str = "", target_url: str = "", blackbox: str = "") -> str:
    patterns = [p for p in blackbox.split(",") if p.strip()] if blackbox else None
    result = gw().follow(producer, trigger=trigger or None, target_url=target_url or None, blackbox=patterns)
    return json.dumps(result, indent=1, default=str)


@mcp.tool(description=(
    "List parsed scripts across all targets, including eval/new Function/worker code. search "
    "filters by source substring; dynamic_only keeps runtime-generated code; full returns full source."))
def gw_scripts(search: str = "", dynamic_only: bool = False, full: bool = False) -> str:
    watcher = gw().scripts
    items = watcher.search(search) if search else (watcher.dynamic() if dynamic_only else watcher.scripts)
    out = [{"scriptId": s["scriptId"], "target": s["target"], "url": s["url"], "dynamic": s["dynamic"],
            "length": s["length"], "source": s["source"] if full else (s["source"] or "")[:300]}
           for s in items[:40]]
    return json.dumps(out, indent=1)


@mcp.tool(description=(
    "Captured requests across all targets: method, url, status, target, initiator, post body, "
    "response-body preview. Filter by url substring. full_bodies=True for complete bodies."))
def gw_network(url_substr: str = "", full_bodies: bool = False) -> str:
    net = gw().net
    items = net.find(url_substr) if url_substr else net.all()
    out = []
    for e in items[-60:]:
        body = e.get("body")
        out.append({"target": e.get("target"), "method": e.get("method"), "url": e.get("url"),
                    "status": e.get("status"), "initiator": e.get("initiator"),
                    "post": e.get("post") if full_bodies else (e.get("post") or "")[:400],
                    "body_len": len(body) if body else 0,
                    "body": body if full_bodies else ((body or "")[:400] or None)})
    return json.dumps(out, indent=1, default=str)


@mcp.tool(description="Navigate the page to a new url.")
def gw_navigate(url: str) -> str:
    gw().navigate(url)
    return f"navigating to {url}"


@mcp.tool(description="Save the full trace (targets, scripts, network, captures, corpus) as JSON to path.")
def gw_save(path: str) -> str:
    return f"saved trace to {gw().save(path)}"


@mcp.tool(description="Close the browser session and free resources.")
def gw_close() -> str:
    global session
    if session is not None:
        try:
            session.close()
        except Exception:
            pass
        session = None
    return "closed"


def _clip(value, limit=200):
    text = json.dumps(value, default=str)
    return value if len(text) <= limit else text[:limit] + "…"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
