---
name: verify-reimplementation
description: >
  Verify a candidate reimplementation of a target's runtime function (decoder, signer,
  hash, crypto boundary) against ground truth before trusting it. Use whenever you have
  "read" obfuscated/minified JS and believe you understand what a function does — your
  reading is a hypothesis, not a fact, until ghostwire's oracle checks it.
---

# Verify a reimplementation against ground truth

**Axiom:** nothing you infer from reading code is trusted until it is checked against
ground truth. LLM deobfuscation is ~97% syntactically correct but only ~61% semantically
correct (JsDeObsBench). The oracle is what closes that gap. Never report "I understood
this function" without a green `gw_verify`.

## When to use which capture

- The function **runs on its own** (timer, event, page activity): hook it with
  `gw_hook(expr, capture_returns=True, label=...)` and wait — you get observed
  `(input -> output)` pairs for free. Faithful: these are real executions.
- The function is **pure and callable by an expression** (you can name it, e.g.
  `window.sign` or `app.dec`): you don't even need a corpus — pass `fresh_inputs` to
  `gw_verify` and the live function is invoked to produce truth on demand.
- Best: do both. Corpus covers the inputs the app actually uses; `fresh_inputs` lets you
  probe edge cases (empty string, 0, negative, very long) the app never exercised.

## Recipe

1. **Find the boundary.** Use `gw_scripts(search=...)` / `gw_network` / `gw_hook` (args
   only) to locate the function whose behaviour you want to reproduce.
2. **Capture ground truth.**
   `gw_hook("window.sign", capture_returns=True, label="sign")`, then let the page run,
   then `gw_corpus("sign")` to confirm pairs landed.
3. **Hypothesize** a candidate as a JS expression evaluating to a function, e.g.
   `(u,p)=>btoa(u+':'+(p*7+1))`. Plain, self-contained — it runs in an isolated page and
   **cannot** call the real function, so it can't cheat.
4. **Verify.**
   `gw_verify(real_fn_expr="window.sign", candidate="(u,p)=>...", label="sign",
   fresh_inputs='[["zoe",0],["",1],["x",-5]]')`.
5. **Read the diff.** If `verified` is true and `tested` is non-trivial — done. If not,
   each mismatch gives `{input, expected, got}`: a concrete counterexample. Fix the
   candidate to satisfy it and re-verify. Loop until `mismatches` is empty.

## Reading the result honestly

- `verified: true` only counts if `tested` is meaningful. `tested: 0` means **no ground
  truth was available** — capture a corpus or pass `fresh_inputs`; do not claim success.
- `coverage_notes` will tell you if corpus pairs were skipped (non-serializable output,
  e.g. an ArrayBuffer/CryptoKey — those need the crypto boundary tooling, not this) or if
  fresh inputs errored on the live function (bad argument shape).
- A passing oracle proves behavioural equivalence **over the tested inputs**, not a proof
  for all inputs. Widen `fresh_inputs` to raise confidence on adversarial cases.

## Worked example

See `examples/oracle_demo.py`: it captures a real signer's corpus, passes a correct
candidate (21/21), catches a candidate that drops a `+1` (0/21, with the exact
counterexample), and verifies against fresh inputs with no corpus.
