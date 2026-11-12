"""technique_wiring — per-layer adapters that give the harvested techniques a call site.

One module per layer so the wiring can be written in parallel and reviewed by
layer. Each module imports `CORE.technique_runtime` and calls `register(...)`
once per technique at import time; nothing else is required of them.

Adapter contract (see `technique_runtime` docstring for the full version):

    def _adapter() -> dict:
        from CORE.some_module import the_symbol      # lazy, real import
        return {"accepted": ..., "summary": ...}     # a dict describing the run

    register("EP44", "ReAct driver loop",
             "CORE.structured_action:drive", _adapter, layer="L7")

Rules, all enforced by review rather than by the runtime:

- FAIL-OPEN. The adapter catches its own exceptions. `run_all()` reports an
  adapter that raises as an error, so a raise is a defect in the wiring, not a
  crash for the caller.
- READ-MOSTLY. `run_all()` invokes every adapter on a live system: an adapter
  that mutates the repo, spends money, or calls a network model is a bug.
- NO NEW DEPENDENCIES. Adapters may use stdlib and modules already vendored in
  this repo. A wiring that cannot run on this box is not a wiring.
- NAME THE SYMBOL. `where` must be the real `module:symbol`, so the registry is
  an audit trail rather than a list of intentions.
- A NO-HOST technique still registers. When the review's verdict is NO-HOST the
  honest adapter asserts the *design rule* the technique yields and shows that no
  host exists — that is what stops the technique being re-litigated next session.
  Import-time failure to find the disclaiming module is the evidence.
"""
