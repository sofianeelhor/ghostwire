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
def gw_hook(expression: str, target_url: str = "") -> str:
    """Set an invisible breakpoint-on-call hook on the function expression resolves to,
    in the root page or in the worker/iframe whose url contains target_url."""
    _gw().hook(expression, target_url=target_url or None)
    return f"hooked {expression} in {target_url or 'root'}"


@mcp.tool()
def gw_captures(limit: int = 50) -> str:
    """Recent hooked-call captures: [{session, fn, args, stack}]."""
    return json.dumps(_gw().captures[-limit:], indent=1)


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
