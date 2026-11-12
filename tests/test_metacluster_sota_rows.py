"""The two SOTA rows whose host is `MetaCluster._plan_execution_order`.

EP374 (TopoAgent) and EP197 (teaching agents to plan like humans) both resolve
onto this method per `SOTA_ROW_RESCORE.md` part B: it is where the per-category
task lists are actually built. Neither is a new module -- both are branches the
host was missing.

What each test discriminates, and why it is the discriminating one:

  * EP374 fission: a category IS one dispatch unit, so two of its worm groups
    that share nothing still run one after the other. The atoms must therefore
    appear as separate plan nodes at the same level AND carry their own worm
    subset, because the executor would otherwise rebuild the whole category and
    the split would be decoration.
  * EP197 granularity: level comes from the dependency DAG, so nodes inside a
    level are independent and their order is a policy choice. The weight must
    reorder them, and a policy that RAISES must leave the old order -- advice
    cannot be allowed to fail a plan.
  * Both hooks are opt-in. With none supplied the plan has to be the plan built
    before them, which is why the default case is asserted directly rather than
    assumed from the signature.
  * EP186 exposure: `orchestrate` must forward a caller's hooks, and must NOT
    add keyword arguments when a caller supplied none -- an existing wrapper of
    this method (see `test_metacluster_rehearsal.py`, whose spy takes one
    positional argument) would break if the default call shape changed.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _mc(categories):
    from CORE.WORMS.METACLUSTER import MetaCluster
    mc = MetaCluster()
    # Injected directly: `discover_all()` would hit the live worm registry.
    mc.categories = categories
    return mc


# ── EP197: the within-level granularity policy ────────────────────────

def test_no_policy_reproduces_the_requested_order():
    """Default case: disjoint categories sit at one level in the asked order."""
    mc = _mc({"gamma": ["w1"], "alpha": ["w2"], "delta": ["w3"]})

    names, levels, note = mc._plan_execution_order(["gamma", "alpha", "delta"])

    assert names == ["gamma", "alpha", "delta"]
    assert levels == {"gamma": 0, "alpha": 0, "delta": 0}
    assert note.startswith("planned:")
    # The new branch must be invisible when unused, not merely harmless.
    assert "fissioned" not in note


def test_granularity_starts_the_heaviest_node_in_a_level_first():
    """The weight is load-bearing: delta is requested last and is planned first.

    The order asserted here is the PLANNER's `ready_order` (descending
    complexity inside a tier, ties broken by plan position), not a rule this
    host invented -- so the test pins the adopted mechanism, not a duplicate.
    """
    mc = _mc({"gamma": ["w1"], "alpha": ["w2"], "delta": ["w3"]})

    names, levels, note = mc._plan_execution_order(
        ["gamma", "alpha", "delta"], granularity={"delta": 3.0})

    assert names == ["delta", "alpha", "gamma"], (
        "the complexity policy did not reach the planner's ready_order")
    # Reordering is a SCHEDULING choice, never a dependency one: every node is
    # still at level 0 and therefore still eligible to run concurrently.
    assert levels == {"gamma": 0, "alpha": 0, "delta": 0}
    assert note.startswith("planned:")


def test_granularity_callable_receives_the_worm_set():
    """A callable policy is handed the node's own work, so it can measure."""
    mc = _mc({"heavy": ["w1", "w2", "w3"], "light": ["w4"]})
    seen = []

    def policy(name, worms):
        seen.append((name, list(worms)))
        return len(worms)

    names, _levels, _note = mc._plan_execution_order(
        ["light", "heavy"], granularity=policy)

    assert names == ["heavy", "light"]
    assert ("heavy", ["w1", "w2", "w3"]) in seen


def test_a_policy_that_raises_leaves_the_previous_order():
    """Advice must not be able to fail a plan -- the caller's order survives."""
    mc = _mc({"gamma": ["w1"], "alpha": ["w2"], "delta": ["w3"]})

    def broken(_name, _worms):
        raise RuntimeError("policy exploded")

    names, levels, note = mc._plan_execution_order(
        ["gamma", "alpha", "delta"], granularity=broken)

    assert names == ["gamma", "alpha", "delta"]
    assert levels == {"gamma": 0, "alpha": 0, "delta": 0}
    assert note.startswith("planned:")


# ── EP374: adaptive atomic fission at a capability boundary ───────────

def test_capability_split_turns_one_category_into_atoms():
    """Two disjoint capability groups become two independent plan nodes."""
    mc = _mc({"tools": ["w1", "w2", "w3", "w4"]})

    def split(category, worms):
        assert category == "tools"
        assert sorted(worms) == ["w1", "w2", "w3", "w4"]
        return {"io": ["w1", "w2"], "net": ["w3", "w4"]}

    names, levels, note = mc._plan_execution_order(
        ["tools"], capability_split=split, granularity=None)

    # One category requested, two dispatch units planned -- and they share no
    # worm, so they land on the same level and can run concurrently. The
    # pre-change plan had one node at level 0 and ran its four worms serially.
    assert set(names) == {"tools#io", "tools#net"}
    assert levels == {"tools#io": 0, "tools#net": 0}
    assert "fissioned=2 atoms" in note
    # The executor resolves the atom's OWN worms: without this the split would
    # be cosmetic, because `_execute_nature` would rebuild the whole category.
    assert mc._atom_worms == {
        "tools#io": ["w1", "w2"],
        "tools#net": ["w3", "w4"],
    }


def test_a_single_group_is_not_a_split():
    """A lone group IS the category; renaming it would churn the plan for nothing."""
    mc = _mc({"tools": ["w1", "w2"]})

    names, _levels, note = mc._plan_execution_order(
        ["tools"], capability_split=lambda _c, w: {"all": list(w)})

    assert names == ["tools"]
    assert "fissioned" not in note
    assert mc._atom_worms == {}


def test_a_split_that_raises_leaves_the_category_intact():
    """Fission is an optimisation: a category that cannot be grouped still runs."""
    mc = _mc({"tools": ["w1", "w2"]})

    def broken(_category, _worms):
        raise RuntimeError("cannot enumerate capabilities")

    names, levels, note = mc._plan_execution_order(
        ["tools"], capability_split=broken)

    assert names == ["tools"]
    assert levels == {}
    assert mc._atom_worms == {}


def test_atoms_of_one_category_can_still_share_a_dependency():
    """Overlapping groups keep the DAG honest rather than assuming disjointness."""
    mc = _mc({"tools": ["w1", "w2", "w3"]})

    def split(_category, worms):
        return {"first": ["w1", "w2"], "second": ["w2", "w3"]}

    names, levels, _note = mc._plan_execution_order(
        ["tools"], capability_split=split)

    # w2 is in both groups, so `second` depends on `first` and the plan may NOT
    # claim they are independent -- the shared-subgraph rule the parent plan
    # already derives from the worm sets.
    assert set(names) == {"tools#first", "tools#second"}
    assert levels["tools#second"] == 1
    assert levels["tools#first"] == 0


# ── EP186: the built plan handed back to the caller ──────────────────

def test_plan_out_receives_the_built_plan_and_its_artefacts():
    """SGDe's half is PLAN CONSTRUCTION, so the plan itself must be retrievable."""
    mc = _mc({"alpha": ["w1"], "beta": ["w2"]})
    out = {}

    names, _levels, note = mc._plan_execution_order(
        ["alpha", "beta"], plan_out=out)

    assert set(out) >= {"plan", "ordered", "levels", "assignment", "rehearsal",
                        "atoms", "note"}
    assert isinstance(out["plan"], dict) and out["plan"], (
        "the exposure must carry the plan object, not a summary of it")
    assert out["ordered"] == names
    assert out["note"] == note


def test_plan_out_is_not_published_on_the_single_category_early_exit():
    """The pre-existing early exits stay untouched, out-dict included."""
    mc = _mc({"alpha": ["w1"]})
    out = {}

    result = mc._plan_execution_order(["alpha"], plan_out=out)

    assert result == (["alpha"], {}, "plan-skipped:single")
    assert out == {}


# ── the production edge: `orchestrate` forwards the hooks ────────────

def _spy_calls(mc, calls, **kwargs):
    """Record the planner call, then report any dispatch failure rather than
    hiding it: the edge asserted here happens before dispatch, so a raise
    cannot mask a missing call, and the error is surfaced in the assertion."""
    try:
        mc.orchestrate(categories=[], **kwargs)
    except Exception as exc:  # noqa: BLE001 - reported by the caller's assert
        calls.append(("dispatch-error", repr(exc)))


def test_orchestrate_forwards_hooks_only_when_a_caller_supplies_one():
    """The default call keeps one positional argument; hooks are opt-in."""
    mc = _mc({"alpha": ["w1"]})
    seen = []
    original = mc._plan_execution_order

    def spy(order, **kw):
        seen.append((list(order), dict(kw)))
        return original(order, **kw)

    mc._plan_execution_order = spy
    calls = []

    _spy_calls(mc, calls)                       # no hooks
    out = {}
    _spy_calls(mc, calls, plan_out=out)         # one hook supplied

    assert seen, ("orchestrate never reached the planner; "
                  f"dispatch outcomes: {calls}")
    # A caller-supplied hook must actually arrive...
    assert seen[-1][1].get("plan_out") is out, (
        "orchestrate dropped the caller's plan_out", seen,
        f"dispatch outcomes: {calls}")
    # ...and the no-hook call must NOT add keywords, or a wrapper accepting
    # only `order` (the shape test_metacluster_rehearsal.py installs) breaks.
    assert seen[0][1] == {}, (
        "the default planner call grew keyword arguments", seen,
        f"dispatch outcomes: {calls}")
