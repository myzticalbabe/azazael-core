"""EP223 action-space half: cluster_route must be drivable by an external policy.

The rescore (`SOTA_ROW_RESCORE.md`, part B, EP223) found that `cluster_route`,
`cascade.CascadeRouter` and `allocation.ThompsonAllocator` each decide
SEPARATELY -- there is no single policy over their joint action space, and that
is what made the row L-sized. This file owns the `cluster_route` half of it.

Two things are asserted, and the second matters as much as the first:

  * `action_space` describes what this router can choose and what it returns, so
    a joint policy can reason about it without importing the sampler; and
  * `tpe_lite_suggest` accepts an externally-supplied suggestion source, while
    its DEFAULT behaviour is unchanged when that source is absent. An extension
    that changes the un-driven path would not be an extension.

A supplying source that is unusable (raises, or returns a non-dict) must fall
back to the local sampler rather than propagate: the router is called on the hot
path, and the failure is recorded rather than silently dropped.
"""


import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

BOUNDS = {"lr": (0.0, 1.0), "wd": (0.0, 0.2)}


def _route():
    from CORE import cluster_route as cr

    return cr


def test_action_space_describes_every_bound_and_the_return_contract():
    space = _route().action_space(BOUNDS)

    assert space["router"] == "cluster_route.tpe_lite_suggest"
    assert space["returns"] == "params"
    assert space["n_candidates"] == 24
    assert set(space["params"]) == set(BOUNDS)
    assert space["params"]["lr"] == {"lo": 0.0, "hi": 1.0}
    assert space["params"]["wd"] == {"lo": 0.0, "hi": 0.2}


def test_action_space_honours_a_caller_supplied_candidate_count():
    assert _route().action_space(BOUNDS, 7)["n_candidates"] == 7


def test_default_path_is_unchanged_and_stays_within_bounds():
    cr = _route()

    # Empty history: the local path samples uniformly inside the bounds.
    cold = cr.tpe_lite_suggest([], dict(BOUNDS))
    assert set(cold) == set(BOUNDS)
    assert all(BOUNDS[k][0] <= cold[k] <= BOUNDS[k][1] for k in BOUNDS)

    # With history: it samples around the best trial, still inside the bounds.
    trials = [({"lr": 0.8, "wd": 0.15}, 1.0), ({"lr": 0.1, "wd": 0.0}, 0.2)]
    warm = cr.tpe_lite_suggest(trials, dict(BOUNDS))
    assert set(warm) == set(BOUNDS)
    assert all(BOUNDS[k][0] <= warm[k] <= BOUNDS[k][1] for k in BOUNDS)


def test_external_source_is_called_with_the_contract_and_drives_the_result():
    cr = _route()
    seen = []

    def source(keys, bounds, n_candidates):
        seen.append((keys, bounds, n_candidates))
        return {"lr": 0.25, "wd": 0.05}

    out = cr.tpe_lite_suggest([], dict(BOUNDS), 24, suggest=source)

    assert out == {"lr": 0.25, "wd": 0.05}
    assert seen == [(["lr", "wd"], BOUNDS, 24)]


def test_external_value_outside_the_bounds_is_clamped():
    cr = _route()

    out = cr.tpe_lite_suggest(
        [], dict(BOUNDS), suggest=lambda keys, bounds, n: {"lr": 5.0}
    )

    assert out["lr"] == 1.0            # clamped to hi, not accepted at 5.0
    assert out["wd"] == 0.1            # unmentioned key -> midpoint


def test_unusable_external_source_falls_back_to_the_local_sampler():
    cr = _route()

    def raising(keys, bounds, n):
        raise RuntimeError("policy unavailable")

    fallback = cr.tpe_lite_suggest([], dict(BOUNDS), suggest=raising)
    assert set(fallback) == set(BOUNDS)
    assert all(BOUNDS[k][0] <= fallback[k] <= BOUNDS[k][1] for k in BOUNDS)

    # A source that answers with something that is not a params dict is equally
    # unusable, and must not be treated as an empty parameter set.
    non_dict = cr.tpe_lite_suggest([], dict(BOUNDS), suggest=lambda keys, bounds, n: None)
    assert set(non_dict) == set(BOUNDS)
