"""
oracle.py — the verification oracle (charter §2, the differentiator).

Founding axiom: nothing the agent infers is trusted until it is checked against ground
truth. JsDeObsBench (arXiv 2506.20170) found LLM deobfuscation is ~97% syntactically
correct but only ~61% semantically correct — a confident model is wrong about behaviour
more than a third of the time. The oracle is what catches that.

It holds a per-boundary corpus of OBSERVED (input -> output) pairs captured at a runtime
boundary (a decoder, a signer, a crypto call), runs an agent-supplied candidate
reimplementation against that corpus AND against the live target for fresh inputs, and
returns a structured behavioural diff with concrete counterexamples. The agent iterates
until the diff is empty.

Two independent sources of ground truth, both usable on their own:
  * passive corpus  — pairs captured by Tracer(capture_returns=True) from real execution.
  * live oracle     — invoke the real function in the page on fresh inputs the agent
                      chooses, to probe inputs the corpus never exercised.

The candidate runs in an ISOLATED page target (its own globals), so a buggy or hostile
candidate cannot read the real function and cannot contaminate the target page.
"""
import json

GW = "/*gw*/"


class Oracle:
    def __init__(self, engine, tracer):
        self.engine = engine
        self.tracer = tracer
        self._scratch_session = None    # lazily-opened isolated page for candidate runs

    # ---- corpus access ----
    def corpus(self, label):
        """Observed (input, output) pairs captured under `label`."""
        with self.tracer._lock:
            return list(self.tracer.pairs.get(label, []))

    def corpora(self):
        """{label: pair_count} for every captured boundary."""
        with self.tracer._lock:
            return {k: len(v) for k, v in self.tracer.pairs.items()}

    # ---- live ground truth ----
    def query_live(self, fn_expr, inputs, target_url=None):
        """Invoke the real function `fn_expr` in the page (or worker/iframe by url) on each
        input and return the real outputs — fresh, authoritative ground truth. Each input
        is an argument list (a bare value is wrapped as a 1-arg call)."""
        sid = self.engine.resolve_session(target_url)
        out = []
        for inp in inputs:
            args = inp if isinstance(inp, list) else [inp]
            expr = f"{GW}({fn_expr}).apply(null, {json.dumps(args)})"
            try:
                r = self.engine.send("Runtime.evaluate",
                                     {"expression": expr, "returnByValue": True,
                                      "awaitPromise": True, "silent": True}, session_id=sid)
            except Exception as e:
                out.append({"__error__": f"cdp: {e}"}); continue
            if r.get("exceptionDetails"):
                out.append({"__error__": _exc_text(r["exceptionDetails"])})
            else:
                out.append(r.get("result", {}).get("value"))
        return out

    # ---- candidate execution (isolated) ----
    def _scratch(self):
        if self._scratch_session is None:
            self._scratch_session = self.engine.open_isolated_page()
        return self._scratch_session

    def run_candidate(self, candidate, inputs):
        """Run the agent's candidate (a JS expression evaluating to a function) against each
        input in an isolated page with its own globals. Returns one output per input, or an
        {__error__}."""
        scratch_session = self._scratch()
        out = []
        for inp in inputs:
            args = inp if isinstance(inp, list) else [inp]
            expr = f"({candidate}).apply(null, {json.dumps(args)})"
            try:
                r = self.engine.send("Runtime.evaluate",
                                     {"expression": expr, "returnByValue": True, "awaitPromise": True},
                                     session_id=scratch_session)
            except Exception as e:
                out.append({"__error__": f"cdp: {e}"}); continue
            if r.get("exceptionDetails"):
                out.append({"__error__": _exc_text(r["exceptionDetails"])})
            else:
                out.append(r.get("result", {}).get("value"))
        return out

    # ---- the gate ----
    def verify(self, fn_expr, candidate, label=None, fresh_inputs=None,
               target_url=None, sample=200, max_mismatches=5):
        """Check `candidate` against ground truth. Test inputs come from the corpus under
        `label` (using the OBSERVED output as truth) and from `fresh_inputs` (using the
        live real function `fn_expr` as truth). Returns:
            {verified, tested, matched, mismatches:[{input,expected,got}], coverage_notes}
        verified is True only if at least one input was tested and every verifiable input
        matched. Non-serializable observed outputs are skipped and reported, never guessed.
        """
        tests = []   # [{input, expected, source}]
        skipped = 0
        for p in self.corpus(label)[:sample] if label else []:
            if p.get("output") is None and p.get("unserialized"):
                skipped += 1
                continue
            tests.append({"input": p["input"], "expected": p.get("output"), "source": "corpus"})

        fresh = list(fresh_inputs or [])
        live_errors = 0
        if fresh:
            live = self.query_live(fn_expr, fresh, target_url=target_url)
            for inp, exp in zip(fresh, live):
                if isinstance(exp, dict) and "__error__" in exp:
                    live_errors += 1
                    continue
                tests.append({"input": inp, "expected": exp, "source": "live"})

        # run candidate over all test inputs in the isolated page
        cand = self.run_candidate(candidate, [t["input"] for t in tests])
        matched, mismatches = 0, []
        for t, got in zip(tests, cand):
            if _equal(t["expected"], got):
                matched += 1
            elif len(mismatches) < max_mismatches:
                mismatches.append({"input": t["input"], "expected": t["expected"],
                                   "got": got, "source": t["source"]})

        n = len(tests)
        verified = n > 0 and matched == n
        notes = (f"{sum(1 for t in tests if t['source']=='corpus')} corpus + "
                 f"{sum(1 for t in tests if t['source']=='live')} live inputs tested; "
                 f"candidate run in isolated page target")
        if skipped:
            notes += f"; {skipped} corpus pairs skipped (non-serializable output, unverifiable)"
        if live_errors:
            notes += f"; {live_errors} fresh inputs errored on the live function (excluded)"
        if n == 0:
            notes += "; NO ground truth available — capture a corpus (hook capture_returns) " \
                     "or pass fresh_inputs"
        return {"verified": verified, "tested": n, "matched": matched,
                "mismatches": mismatches, "coverage_notes": notes}

    def close(self):
        if self._scratch_session is not None:
            self.engine.close_target(self._scratch_session)
            self._scratch_session = None


def _exc_text(details):
    exc = details.get("exception") or {}
    return exc.get("description") or details.get("text") or "exception"


def _equal(a, b):
    """Deep behavioural equality. Both sides arrive via returnByValue (JSON), so structural
    equality is faithful. Distinguishes 1 from '1' and 1 from 1.0-vs-int only when JSON
    would (i.e. true type/shape differences), which is exactly what we want to catch."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return a == b and type(a) == type(b)
