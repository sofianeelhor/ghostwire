import json, time

from .engine import Engine
from .tracer import Tracer
from .oracle import Oracle
from .origin import OriginTracer
from .dataflow import DataflowTracer
from .objects import LiveObjects
from .crypto import CryptoLogger
from .deob import StringDumper, VMTracer
from .heap import take_snapshot, diff_snapshots
from .probes import ScriptWatcher, NetLog


class Inspector:
    def __init__(self, engine, scripts, net, tracer, oracle, origin, dataflow, objects, crypto):
        self.engine, self.scripts, self.net, self.tracer, self.oracle = engine, scripts, net, tracer, oracle
        self.origin_tracer = origin
        self.dataflow = dataflow
        self.objects = objects
        self.crypto_logger = crypto
        self.string_dumper = StringDumper(engine)
        self.vm_tracer = VMTracer(engine)

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

    def snapshot(self, target_url=None):
        return take_snapshot(self.engine, session_id=self.engine.resolve_session(target_url))

    def find_objects(self, value=None, constructor=None, key=None, target_url=None, limit=50):
        return self.snapshot(target_url).find_objects(value=value, constructor=constructor, key=key, limit=limit)

    def patch(self, node_id=None, value=None, constructor=None, key=None,
              assign=None, apply=None, target_url=None):
        return self.objects.patch(node_id=node_id, value=value, constructor=constructor, key=key,
                                  assign=assign, apply=apply, target_url=target_url)

    def read_object(self, node_id=None, value=None, constructor=None, key=None, target_url=None):
        return self.objects.read(node_id=node_id, value=value, constructor=constructor, key=key, target_url=target_url)

    def heapdiff(self, trigger, target_url=None, wait_after=0.3):
        sid = self.engine.resolve_session(target_url)
        before = take_snapshot(self.engine, sid)
        self.engine.send("Runtime.evaluate", {"expression": "/*gw*/" + trigger, "awaitPromise": True, "silent": True},
                         session_id=sid)
        if wait_after:
            time.sleep(wait_after)
        after = take_snapshot(self.engine, sid)
        return diff_snapshots(before, after)

    def crypto(self, duration=4.0, target_url=None):
        return self.crypto_logger.capture(duration=duration, target_url=target_url)

    def dump_strings(self, decoder, start=0, count=512, extra_args=None, target_url=None):
        return self.string_dumper.dump(decoder, start=start, count=count, extra_args=extra_args, target_url=target_url)

    def vm_watch(self, url_substr, line, column=0, watch=None, iterations=300, trigger=None, target_url=None):
        return self.vm_tracer.watch(url_substr, line, column=column, watch=watch, iterations=iterations,
                                    trigger=trigger, target_url=target_url)

    def origin(self, value, enter, trigger=None, target_url=None, blackbox=None,
               max_steps=300, max_returns=40):
        return self.origin_tracer.trace(value, enter, trigger=trigger, target_url=target_url,
                                        blackbox=blackbox, max_steps=max_steps, max_returns=max_returns)

    def follow(self, producer, trigger=None, target_url=None, blackbox=None):
        return self.dataflow.follow(producer, trigger=trigger, target_url=target_url, blackbox=blackbox)

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
    origin = OriginTracer(engine)
    dataflow = DataflowTracer(engine)
    objects = LiveObjects(engine)
    crypto = CryptoLogger(engine)
    engine.start(blackbox=blackbox)
    engine.navigate(url)
    return Inspector(engine, scripts, net, tracer, oracle, origin, dataflow, objects, crypto)
