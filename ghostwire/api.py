"""
api.py — high-level entry point for reuse on any target.

    import ghostwire
    with ghostwire.attach("https://site/", blackbox=[r"antidebug"]) as gw:
        gw.wait(3)
        gw.hook("window.someFn")                 # or hook inside a worker/iframe by url
        gw.wait(2)
        gw.save("trace.json")                    # replayable artifact
        print(gw.targets(), len(gw.scripts.scripts), len(gw.net.all()), len(gw.captures))
"""
import json
import time

from .engine import Engine
from .tracer import Tracer
from .probes import ScriptWatcher, NetLog


class Inspector:
    """Bundles an Engine with the standard probes and exposes a small reuse API."""

    def __init__(self, engine, scripts, net, tracer):
        self.engine = engine
        self.scripts = scripts
        self.net = net
        self.tracer = tracer

    def targets(self):
        return self.engine.targets()

    def navigate(self, url):
        self.engine.navigate(url)
        return self

    def wait(self, seconds):
        time.sleep(seconds)
        return self

    def hook(self, expression, target_url=None):
        self.tracer.hook(expression, target_url=target_url)
        return self

    def eval(self, expression, target_url=None):
        sid = self.engine.session_for(target_url) if target_url else None
        # sentinel prefix so our own eval is filtered out of the captured scripts
        r = self.engine.send("Runtime.evaluate",
                             {"expression": "/*gw*/" + expression, "returnByValue": True, "silent": True},
                             session_id=sid)
        return r.get("result", {}).get("value")

    @property
    def captures(self):
        return self.tracer.captures

    def dump(self):
        return {
            "targets": self.targets(),
            "scripts": self.scripts.scripts,
            "network": self.net.all(),
            "captures": self.tracer.captures,
        }

    def save(self, path):
        with open(path, "w") as f:
            json.dump(self.dump(), f, indent=1, default=str)
        return path

    def close(self):
        self.engine.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def attach(url="about:blank", headless=True, proxy=None, blackbox=None):
    """Launch Chrome, attach across all targets with the scripts/network/tracer probes,
    navigate to url, and return an Inspector. `blackbox` is a list of script-url regexes
    the debugger must not pause inside (anti-debug evasion)."""
    engine = Engine(headless=headless, proxy=proxy)
    scripts, net, tracer = ScriptWatcher(), NetLog(), Tracer()
    for p in (scripts, net, tracer):
        engine.add_probe(p)
    engine.start(blackbox=blackbox)
    engine.navigate(url)
    return Inspector(engine, scripts, net, tracer)
