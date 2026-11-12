# coding: utf-8
"""Ex Machina operator tier: RADIX, CATENA, NAVIS, VELA, NEXUS, OPUS, LIMEN.

Each test asserts the property the organ CLAIMS. A test that only checks
"returns a dict" would pass on a stub, which is how a tier becomes decorative.
"""
import math
import os
import random
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "src", "CORE", "WORMS", "expansion")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import exmachina_operators as ops
except ImportError:                                        # pragma: no cover
    ops = None

pytestmark = pytest.mark.skipif(ops is None, reason="exmachina_operators not importable")


def _chain(n=300, seed=3):
    """X -> Y -> Z. X and Z are dependent marginally, independent given Y."""
    rng = random.Random(seed)
    x = [rng.gauss(0, 1) for _ in range(n)]
    y = [2.0 * x[i] + rng.gauss(0, 0.3) for i in range(n)]
    z = [1.5 * y[i] + rng.gauss(0, 0.3) for i in range(n)]
    return {"x": x, "y": y, "z": z}


def _collider(n=300, seed=4):
    """X -> Y <- Z: X and Z are marginally independent, dependent given Y."""
    rng = random.Random(seed)
    x = [rng.gauss(0, 1) for _ in range(n)]
    z = [rng.gauss(0, 1) for _ in range(n)]
    y = [x[i] + z[i] + rng.gauss(0, 0.2) for i in range(n)]
    return {"x": x, "y": y, "z": z}


# ── RADIX ───────────────────────────────────────────────────────────────

class TestRadix:
    def test_partial_correlation_removes_the_chain_edges(self):
        d = _chain()
        marg = ops.partial_correlation(d, "x", "z")
        cond = ops.partial_correlation(d, "x", "z", ["y"])
        assert marg["p"] < 0.01, "x and z are marginally dependent here"
        assert cond["independent"] is True, "conditioning on y must separate them"
        assert abs(cond["r"]) < abs(marg["r"])

    def test_partial_correlation_without_data_is_unknown(self):
        got = ops.partial_correlation({"a": [1, 2, 3], "b": [1, 2, 3]}, "a", "b")
        assert got["p"] is None and got["independent"] is None

    def test_skeleton_drops_the_indirect_edge(self):
        skel = ops.pc_skeleton(_chain(), max_cond=1)
        edges = {tuple(sorted(e)) for e in skel["edges"]}
        assert ("x", "y") in edges and ("y", "z") in edges
        assert ("x", "z") not in edges, "the indirect edge must be removed"

    def test_constant_series_do_not_crash(self):
        d = {"x": [1.0] * 40, "y": [float(i) for i in range(40)], "z": [1.0] * 40}
        skel = ops.pc_skeleton(d, max_cond=1)
        assert isinstance(skel["edges"], list)

    def test_constant_variable_is_untestable_not_independent(self):
        """A column that never varies cannot be tested. Saying "independent"
        would DELETE an edge nobody examined -- and on real data a constant
        check column made the objective look d-separated from it."""
        d = {"flat": [1.0] * 40, "y": [float(i % 3) for i in range(40)]}
        got = ops.partial_correlation(d, "flat", "y")
        assert got["independent"] is None
        assert "untestable" in got["note"]
        # ...and the edge survives the skeleton pass.
        skel = ops.pc_skeleton(d, max_cond=1)
        assert ("flat", "y") in {tuple(sorted(e)) for e in skel["edges"]}

    def test_conditioning_that_explains_a_variable_is_independence(self):
        d = {"x": [float(i) for i in range(40)]}
        d["y"] = [3.0 * v + 1.0 for v in d["x"]]        # y = 3x + 1 exactly
        d["z"] = [7.0 - 2.0 * v for v in d["x"]]       # z = 7 - 2x exactly
        got = ops.partial_correlation(d, "y", "z", ["x"])
        assert got["independent"] is True
        assert "explains" in got["note"]

    def test_collider_is_oriented(self):
        skel = ops.pc_skeleton(_collider(), max_cond=1)
        oriented = ops.orient_edges(skel)
        directed = {tuple(e) for e in oriented["directed"]}
        # At least one of x->y or z->y should be oriented, and never y->both.
        into_y = [e for e in directed if e[1] == "y"]
        out_of_y = [e for e in directed if e[0] == "y"]
        assert into_y, "a collider at y must be oriented into y"
        assert not out_of_y, "y must not be oriented outward in this graph"

    def test_blanket_excludes_the_target(self):
        skel = ops.pc_skeleton(_chain(), max_cond=1)
        blanket = ops.markov_blanket(skel, {}, "y")
        assert "y" not in blanket
        assert "x" in blanket and "z" in blanket

    def test_discover_reports_d_separation(self):
        got = ops.discover(_chain(), target="x", check="z", max_cond=1)
        assert got["linked"] is False, "x and z must be d-separated given y"
        assert got["nodes"] == 3 and got["tests"] > 0


# ── CATENA ──────────────────────────────────────────────────────────────

class TestCatena:
    def test_gp_interpolates_its_own_training_points(self):
        xs = [[i / 10.0] for i in range(11)]
        ys = [math.sin(3.0 * x[0]) for x in xs]
        gp = ops.SquaredExponentialGP().fit(xs, ys)
        preds = gp.predict(xs)
        worst = max(abs(preds[i][0] - ys[i]) for i in range(len(ys)))
        assert worst < 0.05, "a GP should reproduce its training data (%s)" % worst
        assert gp.ls > 0

    def test_gp_uncertainty_grows_away_from_data(self):
        xs = [[i / 10.0] for i in range(6)]
        ys = [x[0] ** 2 for x in xs]
        gp = ops.SquaredExponentialGP().fit(xs, ys)
        near = gp.predict([[0.25]])[0][1]
        far = gp.predict([[4.0]])[0][1]
        assert far > near, "posterior sd must grow away from the data"

    def test_ei_peaks_when_uncertain_but_promising(self):
        flat = ops.expected_improvement(0.0, 0.0, 0.0)
        uncertain = ops.expected_improvement(0.0, 1.0, 0.0)
        assert uncertain > flat
        assert ops.expected_improvement(1.0, 0.1, 0.0) > 0

    def test_trust_region_improves_a_quadratic(self):
        bounds = {"a": (-2.0, 2.0), "b": (-2.0, 2.0)}

        def f(x):
            return -((x[0] - 0.7) ** 2) - ((x[1] + 0.3) ** 2)

        bo = ops.TrustRegionBO(bounds, seed=5)
        bo.tell([0.0, 0.0], f([0.0, 0.0]))
        for _ in range(40):
            x = bo.ask(n_candidates=64)
            bo.tell(x, f(x))
        assert bo.best_y > f([0.0, 0.0]), "TR-BO must beat its own start"
        rep = bo.report()
        assert rep["radius"] > 0 and rep["n"] >= 41


# ── NAVIS ───────────────────────────────────────────────────────────────

class TestNavis:
    def test_spectral_radius_is_enforced(self):
        esn = ops.EchoState(n_in=2, n_res=40, spectral_radius=0.8, seed=11)
        actual = esn._spectral_radius(esn.W)
        assert abs(actual - 0.8) < 0.35, "scaled radius was %s" % actual

    def test_reservoir_has_delay_memory(self):
        rng = random.Random(1)
        u = [[1.0 if rng.random() > 0.5 else -1.0] for _ in range(200)]
        delay = 3
        y = [0.0] * delay + [u[i - delay][0] for i in range(delay, len(u))]
        esn = ops.EchoState(n_in=1, n_res=60, seed=2)
        assert esn.fit(u, y, washout=5, ridge=1e-4) is not None
        pred = esn.predict(u, washout=5)
        tgt = y[len(y) - len(pred):]
        # Correlation, not accuracy: the claim is that the state carries the
        # delayed input, not that a linear readout is a perfect classifier.
        n = len(pred)
        mp, mt = sum(pred) / n, sum(tgt) / n
        num = sum((pred[i] - mp) * (tgt[i] - mt) for i in range(n))
        den = math.sqrt(sum((p - mp) ** 2 for p in pred) * sum((t - mt) ** 2 for t in tgt))
        corr = num / den if den else 0.0
        assert corr > 0.5, "reservoir memory correlation was %.3f" % corr

    def test_predict_before_fit_is_empty(self):
        assert ops.EchoState(n_in=1, n_res=8).predict([[0.1]]) == []


# ── VELA ────────────────────────────────────────────────────────────────

class TestVela:
    def test_selftest_recalls_what_it_stored(self):
        got = ops.HDC(dim=1024, seed=9).selftest(n_items=24, n_queries=12)
        assert got["recall"] == 1.0, got
        assert got["trials"] == 12

    def test_similarity_noise_floor_is_about_half(self):
        hdc = ops.HDC(dim=1024, seed=3)
        assert abs(hdc.similarity(hdc.symbol("a"), hdc.symbol("b")) - 0.5) < 0.08

    def test_bind_is_its_own_inverse(self):
        hdc = ops.HDC(dim=256, seed=1)
        a, b = hdc.symbol("k"), hdc.symbol("v")
        assert hdc.bind(hdc.bind(a, b), b) == a

    def test_bundle_keeps_similarity_to_its_parts(self):
        hdc = ops.HDC(dim=2048, seed=2)
        parts = [hdc.symbol("p%d" % i) for i in range(5)]
        b = hdc.bundle(parts)
        assert all(hdc.similarity(b, p) > 0.6 for p in parts)

    def test_permute_moves_information(self):
        hdc = ops.HDC(dim=512, seed=4)
        a = hdc.symbol("x")
        assert hdc.similarity(a, hdc.permute(a, 3)) < 0.7
        assert hdc.similarity(a, hdc.permute(a, 0)) == 1.0

    def test_ordered_encode_distinguishes_order(self):
        hdc = ops.HDC(dim=2048, seed=5)
        f = hdc.encode([("a", "1"), ("b", "2")])
        r = hdc.encode([("b", "2"), ("a", "1")])
        assert hdc.similarity(f, r) < 0.8


# ── NEXUS ───────────────────────────────────────────────────────────────

class TestNexus:
    def _two_regimes(self, n=120, seed=8):
        rng = random.Random(seed)
        X, y = [], []
        for _ in range(n):
            x = rng.random()
            X.append([x])
            y.append(x if x < 0.5 else -x)
        return X, y

    def test_experts_divide_the_regimes(self):
        X, y = self._two_regimes()
        moe = ops.SparseMoE(n_experts=2, top_k=1, seed=3).fit(X, y)
        rep = moe.report(X)
        assert rep["trained"] == 2
        assert rep["usage_entropy"] > 0.5, "both regimes need their own expert: %s" % rep

    def test_moe_beats_a_single_linear_model(self):
        X, y = self._two_regimes()
        split = 84
        moe = ops.SparseMoE(n_experts=2, top_k=1, seed=3).fit(X[:split], y[:split])
        pred = moe.predict(X[split:])
        moe_err = sum(abs(pred[i] - y[split + i]) for i in range(len(pred))) / len(pred)

        import exmachina as ex
        beta = ex.ridge_fit([r + [1.0] for r in X[:split]], y[:split], lam=1e-6)
        lin = [beta[0] * r[0] + beta[1] for r in X[split:]]
        lin_err = sum(abs(lin[i] - y[split + i]) for i in range(len(lin))) / len(lin)
        assert moe_err < lin_err, "MoE %.4f vs linear %.4f" % (moe_err, lin_err)

    def test_single_expert_reports_low_entropy(self):
        X, y = self._two_regimes()
        rep = ops.SparseMoE(n_experts=4, top_k=1, seed=3, temperature=0.01).fit(X, y).report(X)
        assert rep["usage_entropy"] <= rep["max_entropy"]


# ── OPUS ────────────────────────────────────────────────────────────────

class TestOpus:
    def test_kan_interpolates_better_than_linear(self):
        # Held-out points are INTERLEAVED inside the training range. Splines
        # are local, so testing extrapolation would be asking the wrong
        # question -- and the first version of this did exactly that, which
        # is why it failed against a linear baseline on sin(6x).
        X = [[i / 60.0] for i in range(60)]
        y = [math.sin(6.0 * r[0]) for r in X]
        tr = [i for i in range(60) if i % 2 == 0]
        te = [i for i in range(60) if i % 2 == 1]
        kan = ops.KAN(n_in=1, n_grid=8, degree=3, ridge=1e-6)
        kan.fit([X[i] for i in tr], [y[i] for i in tr])
        kp = kan.predict([X[i] for i in te])
        kerr = sum(abs(kp[j] - y[te[j]]) for j in range(len(te))) / len(te)

        import exmachina as ex
        beta = ex.ridge_fit([X[i] + [1.0] for i in tr], [y[i] for i in tr], lam=1e-6)
        lin = [beta[0] * X[i][0] + beta[1] for i in te]
        lerr = sum(abs(lin[j] - y[te[j]]) for j in range(len(te))) / len(te)
        assert kerr < lerr / 2.0, "KAN %.4f vs linear %.4f" % (kerr, lerr)

    def test_kan_does_not_extrapolate(self):
        """The documented limitation, asserted so it cannot regress silently.

        The claim is the CONTRAST: near-perfect where it has data, badly wrong
        outside. Asserting an absolute bound on the outside prediction would
        be asserting an implementation detail of the ridge trade-off between
        the spline span and the linear tail, which is not the point and would
        break for harmless reasons.
        """
        X = [[i / 40.0] for i in range(40)]
        y = [math.sin(6.0 * r[0]) for r in X]
        kan = ops.KAN(n_in=1, n_grid=8, degree=3, ridge=1e-6).fit(X, y)
        inside = kan.predict(X)
        err_in = sum(abs(inside[i] - y[i]) for i in range(len(y))) / len(y)
        outside = [[1.5], [2.0]]
        preds = kan.predict(outside)
        truth = [math.sin(6.0 * 1.5), math.sin(6.0 * 2.0)]
        err_out = sum(abs(preds[i] - truth[i]) for i in range(2)) / 2.0
        assert err_in < 0.1, "it should fit where it has data: %.4f" % err_in
        assert err_out > 0.5, "and be wrong outside it: %.4f" % err_out

    def test_report_counts_parameters(self):
        kan = ops.KAN(n_in=2, n_grid=4, degree=3)
        kan.fit([[0.1, 0.2], [0.4, 0.5], [0.7, 0.8]], [1.0, 2.0, 3.0])
        rep = kan.report()
        assert rep["features"] > 0 and rep["params"] == rep["features"]

    def test_predict_before_fit_is_empty(self):
        assert ops.KAN(n_in=1).predict([[0.5]]) == []


# ── LIMEN ───────────────────────────────────────────────────────────────

class TestLimen:
    def test_eig_peaks_at_maximum_uncertainty(self):
        assert ops.expected_information_gain(0.5) == pytest.approx(1.0, abs=1e-9)
        assert ops.expected_information_gain(0.0) == 0.0
        assert ops.expected_information_gain(1.0) == 0.0
        assert ops.expected_information_gain(0.9) < ops.expected_information_gain(0.6)

    def test_ranking_prefers_the_uncertain_item(self):
        items = [{"id": "certain", "p": 0.999}, {"id": "coin", "p": 0.5},
                 {"id": "hopeless", "p": 0.001}]
        ranked = ops.rank_candidates(items, limit=3)
        assert ranked[0]["id"] == "coin"
        assert ranked[-1]["priority"] < ranked[0]["priority"]

    def test_redundancy_is_discounted(self):
        held = [{"claim": "the sample was mishandled on 2026-03-04"}]
        items = [{"id": "dup", "p": 0.5, "claim": "the sample was mishandled on 2026-03-04"},
                 {"id": "new", "p": 0.5, "claim": "a different laboratory handled it"}]
        ranked = ops.rank_candidates(items, covered=held, limit=2)
        by_id = {r["id"]: r for r in ranked}
        assert by_id["dup"]["redundancy"] > by_id["new"]["redundancy"]
        assert by_id["new"]["priority"] > by_id["dup"]["priority"]

    def test_binary_entropy_bounds(self):
        assert ops.binary_entropy(0.5) == pytest.approx(1.0)
        assert ops.binary_entropy(0.0) == 0.0
        assert ops.binary_entropy(-3.0) == 0.0

    def test_bad_items_are_skipped_not_fatal(self):
        ranked = ops.rank_candidates([{"p": "nan"}, None, {"id": "ok", "p": 0.5}])
        assert [r.get("id") for r in ranked] == ["ok"]


# ── the aggregate report ────────────────────────────────────────────────

class TestOperatorReport:
    def _rows(self, n=60):
        rng = random.Random(21)
        out = []
        for i in range(n):
            a = rng.random()
            b = rng.random()
            out.append({"lr": a, "epsilon": b, "margin": 0.5 * a + 0.2 * b + rng.gauss(0, 0.05)})
        return out

    def test_report_covers_every_organ(self):
        rep = ops.operator_report(self._rows(), features=["lr", "epsilon"], target="margin")
        for key in ("radix", "catena", "navis", "vela", "nexus", "opus", "limen"):
            assert key in rep, "%s missing from the report" % key

    def test_report_measures_rather_than_asserts(self):
        rep = ops.operator_report(self._rows(), features=["lr", "epsilon"], target="margin")
        assert rep["vela"]["recall"] == 1.0
        assert rep["nexus"]["holdout_mae"] is not None
        assert rep["opus"]["holdout_mae"] is not None
        assert rep["navis"]["trained"] is True
        assert rep["radix"]["nodes"] >= 1

    def test_report_on_empty_input_does_not_raise(self):
        rep = ops.operator_report([], features=["lr"], target="margin")
        assert "radix" in rep and "vela" in rep

    def test_columns_are_row_aligned(self):
        """Regression: columns were built per-key, so a sparse check field
        misaligned every other column and RADIX reported a d-separation that
        did not exist in the data."""
        rows = []
        for i in range(60):
            r = {"lr": i / 60.0, "margin": (i / 60.0) * 2.0}
            if i < 10:
                r["_holdout"] = 1.0 if i % 2 else 0.0
            rows.append(r)
        rep = ops.operator_report(rows, features=["lr"], target="margin", check="_holdout")
        assert rep["n_rows"] == 60
        assert rep["n_complete"] == 10, "the check column restricts to 10 rows"
        assert rep["n_complete_models"] == 60, "the models do not need the check column"
        # The models must actually run on the 60-row subset, not be skipped.
        assert rep["opus"]["holdout_mae"] is not None
        assert rep["nexus"]["holdout_mae"] is not None

    def test_sparse_check_does_not_starve_the_models(self):
        """A monitor row set has the holdout in cooldown most of the time.
        Requiring that field for every organ cut them to a handful of rows."""
        rows = [{"lr": i / 40.0, "margin": 0.5 * (i % 7)}
                for i in range(80)]
        rows[0]["_holdout"] = 1.0
        rows[1]["_holdout"] = 0.0
        rep = ops.operator_report(rows, features=["lr"], target="margin", check="_holdout")
        assert rep["n_complete_models"] == 80
        assert rep["navis"]["trained"] is True

    def test_organs_are_registered_implemented(self):
        tiers = {o["key"] for o in ops.OPERATOR_ORGANS if o["tier"] == "implemented"}
        assert tiers == {"radix", "catena", "navis", "vela", "nexus", "opus", "limen"}
