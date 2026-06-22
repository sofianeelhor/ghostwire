import json, time

from .engine import Engine
from .tracer import Tracer
from .oracle import Oracle
from .probes import ScriptWatcher, NetLog


class Inspector:
    def __init__(self, engine, scripts, net, tracer, oracle):
        self.engine, self.scripts, self.net, self.tracer, self.oracle = engine, scripts, net, tracer, oracle

    def targets(self):
        return self.engine.targets()

    def navigate(self, url):
        self.engine.navigate(url); return self

    def wait(self, seconds):
        time.sleep(seconds); return self

    def hook(self, expression, target_url=None, capture_returns=False, label=None):
        self.tracer.hook(expression, target_url=target_url, capture_returns=capture_returns, label=label)
        return self

    def eval(self, expression, target_url=None):
        sid = self.engine.resolve_session(target_url)
        r = self.engine.send("Runtime.evaluate",
            {"expression": "/*gw*/" + expression, "returnByValue": True, "silent": True}, session_id=sid)
        return r.get("result", {}).get("value")

    def corpus(self, label):
        return self.oracle.corpus(label)

    def corpora(self):
        return self.oracle.corpora()

    def verify(self, fn_expr, candidate, label=None, fresh_inputs=None,
               target_url=None, sample=200, max_mismatches=5):
        return self.oracle.verify(fn_expr, candidate, label=label, fresh_inputs=fresh_inputs,
                                  target_url=target_url, sample=sample, max_mismatches=max_mismatches)

    @property
    def captures(self):
        return self.tracer.captures

    def dump(self):
        return {"targets": self.targets(), "scripts": self.scripts.scripts,
                "network": self.net.all(), "captures": self.tracer.captures, "corpus": self.tracer.pairs}

    def save(self, path):
        with open(path, "w") as f:
            json.dump(self.dump(), f, indent=1, default=str)
        return path

    def close(self):
        try:
            self.oracle.close()
        except Exception:
            pass
        self.engine.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def attach(url="about:blank", headless=True, proxy=None, blackbox=None):
    engine = Engine(headless=headless, proxy=proxy)
    scripts, net, tracer = ScriptWatcher(), NetLog(), Tracer()
    for probe in (scripts, net, tracer):
        engine.add_probe(probe)
    oracle = Oracle(engine, tracer)
    engine.start(blackbox=blackbox)
    engine.navigate(url)
    return Inspector(engine, scripts, net, tracer, oracle)
