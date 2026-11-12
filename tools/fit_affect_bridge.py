#!/usr/bin/env python3
# coding: utf-8
"""Fit the affect -> construct bridge, and say exactly what that proves.

THE PROBLEM THIS ADDRESSES
--------------------------
`PSYCHE.network.activation_from_pad` maps a PAD reading and an appraisal family
onto construct nodes. Its coefficients were HAND-MAPPED: pleasure raises
approach, arousal raises threat, sadness suppresses self-worth. Every direction
is defensible from appraisal theory; none of the magnitudes came from anything,
and no dataset informed them. That is the weakest modelling assumption in the
stack, and "we will fit it later" is a placeholder with a nicer name.

WHAT THIS TOOL PROVES, AND WHAT IT DOES NOT
-------------------------------------------
It fits the bridge by ridge regression and then checks COEFFICIENT RECOVERY: the
training data is generated from a KNOWN ground-truth matrix, so a correct
estimator must return that matrix. If it cannot, the estimator is broken and
nothing downstream can be trusted. That is a real test of the FITTING CODE.

It proves nothing about the MODEL, because the ground truth is synthetic. A
successful recovery says "this estimator recovers a linear map it was handed",
which is arithmetic, not psychology. It cannot tell you whether the true bridge
is linear, whether these constructs are the right ones, or whether PAD is the
right input space. Those need data from real people, and none exists --
`tools/psyche_fit_pams.py` exits 2 for the same reason. Quoting the recovery
error as evidence the bridge is VALID would be a misuse of this instrument.

The synthetic route buys one thing nothing else could: it separates "the
estimator works" from "the model is right", which a single reported number
normally confounds.

WHAT IS MEASURED, IN ORDER
--------------------------
1. recovery   -- max |w_hat - w_true| per construct and held-out R^2. Non-zero
                 exit if recovery exceeds tolerance: a fit that cannot recover a
                 known answer is broken, not inconclusive.
2. divergence -- hand map vs fitted map over a grid of PAD x family inputs: mean
                 |delta| in construct activation, and how often the TOP construct
                 changes identity.
3. downstream -- how often the collapse detector in `rt_agent.triage` changes
                 verdict between the two bridges. This is a PROXY for the stance
                 layer, not the stance layer itself: triage is what gates stance
                 selection and it is the only part with published thresholds, so
                 it is the only part whose sensitivity can be stated without
                 inventing a criterion.

INPUT ENCODING
--------------
features = [1, p, a, d, is_threat, is_sadness]. The two family indicators are
0/1, so the fitted model is one affine map per family sharing slope structure,
which is what "the same PAD with a different appraisal story gives a different
graph state" means concretely.

Usage:
  python tools/fit_affect_bridge.py                # fit, verify, compare, write
  python tools/fit_affect_bridge.py --no-write     # do not touch the data file
  python tools/fit_affect_bridge.py --check        # verify the shipped file recovers
  python tools/fit_affect_bridge.py --json         # machine-readable report
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE.affect import clamp  # noqa: E402
from PSYCHE.network import hand_activation_from_pad  # noqa: E402
import PSYCHE as _psyche_pkg  # noqa: E402

#: Resolved from the INSTALLED package rather than the source layout, so the same
#: file works in the monorepo and in the materialised public tree. `ROOT/"src"/...`
#: was correct only in the monorepo and made the shipped --check fail on a missing
#: path, which reads like a missing bridge rather than a broken instrument.
BRIDGE_FILE = Path(_psyche_pkg.__file__).resolve().with_name("data") / "affect_bridge.json"

#: Targets the bridge emits, in a fixed order. `threat_appraisal` and `rumination`
#: are conditional in the hand map; here they are always modelled and the fit
#: learns that a sadness appraisal contributes ~0 to threat.
TARGETS: Tuple[str, ...] = (
    "approach", "avoidance", "arousal", "reward_expectancy",
    "control_appraisal", "self_worth", "threat_appraisal", "rumination",
)

#: Feature names, in order. Index 0 is the intercept.
FEATURES: Tuple[str, ...] = ("1", "p", "a", "d", "threat", "sadness")

#: Ground truth for the recovery test. Written from appraisal theory, one row per
#: construct, columns in FEATURES order. These are the magnitudes the hand map
#: asserted, refined only where the hand map was internally inconsistent (it gave
#: self_worth two different slopes depending on family ordering, which cannot be
#: right in any single function). They are a MACHINE FOR TESTING THE ESTIMATOR.
#: They are not, and must never be presented as, estimates of a real person.
W_TRUE: Dict[str, Tuple[float, ...]] = {
    #                       1      p       a       d    threat  sadness
    "approach":           (0.00,  0.55,  0.05,  0.30,  -0.10,  -0.05),
    "avoidance":          (0.05, -0.60,  0.15, -0.10,   0.25,   0.10),
    "arousal":            (0.00,  0.05,  0.85,  0.00,   0.10,  -0.05),
    "reward_expectancy":  (0.00,  0.50,  0.00,  0.25,  -0.15,  -0.10),
    "control_appraisal":  (0.05,  0.15,  0.00,  0.75,  -0.20,  -0.15),
    "self_worth":         (0.05,  0.35,  0.00,  0.10,  -0.10,  -0.45),
    "threat_appraisal":   (0.00, -0.10,  0.70, -0.35,   0.45,   0.00),
    "rumination":         (0.00, -0.25,  0.30,  0.00,   0.20,   0.40),
}

#: Recovery criterion, as a Z SCORE, not an absolute error. A fixed tolerance is
#: the wrong instrument here and the first version of this tool got it wrong: with
#: noise sigma 0.02 and n=4000 the sampling error on a coefficient is around
#: 3e-4, so a hand-picked 1e-3 threshold failed a perfectly correct estimator on
#: one seed out of the box. The bound that is right is the estimator's OWN
#: standard error: se_i = sigma * sqrt((X'X)^-1_ii), and a correct fit recovers
#: each coefficient to a few se. 6 sigma is chosen so a pass cannot happen by
#: luck; it is not a tolerance on model quality.
RECOVERY_Z = 6.0

#: Ridge penalty for the fit. Tiny on purpose: with a well-conditioned design and
#: clean synthetic data the penalty only has to stop the normal equations from
#: exploding, not shape the answer. A larger lambda would bias recovery and the
#: test would then measure the intentional bias instead of the estimator.
RIDGE_LAMBDA = 1e-8

DEFAULT_SAMPLES = 4000
DEFAULT_NOISE = 0.02
FAMILIES: Tuple[str, ...] = ("none", "threat", "sadness")


# -- linear algebra ------------------------------------------------------------
def solve(matrix: List[List[float]], rhs: List[float]) -> List[float]:
    """Gauss-Jordan with partial pivoting. Stdlib only, like the rest of PSYCHE.

    `network.py` inverts by the same route; this is deliberately not a new
    dependency, because a numerical library here would be the only one in the
    package and the problem is six-by-six.
    """
    n = len(matrix)
    aug = [list(map(float, row)) + [float(rhs[i])] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-14:
            raise ValueError("singular design matrix at column %d" % col)
        aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        aug[col] = [v / scale for v in aug[col]]
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if factor:
                aug[r] = [a - factor * b for a, b in zip(aug[r], aug[col])]
    return [row[-1] for row in aug]


def ridge_fit(design: Sequence[Sequence[float]], targets: Sequence[Sequence[float]],
              lam: float = RIDGE_LAMBDA) -> List[List[float]]:
    """Fit one coefficient vector per target column. Returns [target][feature]."""
    rows, cols = len(design), len(design[0])
    xtx = [[sum(design[r][i] * design[r][j] for r in range(rows)) for j in range(cols)]
           for i in range(cols)]
    for i in range(cols):
        xtx[i][i] += lam
    fitted: List[List[float]] = []
    for t in range(len(targets[0])):
        xty = [sum(design[r][i] * targets[r][t] for r in range(rows)) for i in range(cols)]
        fitted.append(solve(xtx, xty))
    return fitted


def standard_errors(design: Sequence[Sequence[float]], lam: float = RIDGE_LAMBDA,
                    sigma: float = 1.0) -> List[float]:
    """Standard error of each coefficient: sigma * sqrt(diag((X'X)^-1)).

    This is what makes recovery testable without a hand-picked tolerance: the
    estimator is asked to be right to within the precision it actually has.
    """
    cols = len(design[0])
    xtx = [[sum(row[i] * row[j] for row in design) for j in range(cols)]
           for i in range(cols)]
    for i in range(cols):
        xtx[i][i] += lam
    out: List[float] = []
    for i in range(cols):
        unit = [0.0] * cols
        unit[i] = 1.0
        out.append(sigma * max(0.0, solve(xtx, unit)[i]) ** 0.5)
    return out


def r_squared(actual: Sequence[Sequence[float]], predicted: Sequence[Sequence[float]]) -> float:
    flat_a = [v for row in actual for v in row]
    flat_p = [v for row in predicted for v in row]
    mean = sum(flat_a) / len(flat_a)
    ss_tot = sum((v - mean) ** 2 for v in flat_a)
    ss_res = sum((a - p) ** 2 for a, p in zip(flat_a, flat_p))
    return 1.0 - ss_res / ss_tot if ss_tot else 0.0


# -- data ----------------------------------------------------------------------
def _features(p: float, a: float, d: float, family: str) -> List[float]:
    return [1.0, p, a, d, 1.0 if family == "threat" else 0.0,
            1.0 if family == "sadness" else 0.0]


def synthesise(samples: int, noise: float, seed: int) -> Tuple[List[List[float]],
                                                                List[List[float]],
                                                                List[List[float]],
                                                                List[List[float]]]:
    """Uniform PAD over the full cube, each family equally likely.

    Uniform on purpose: a distribution concentrated where people usually sit
    would leave the edges of the cube unidentified while still fitting well,
    which is the failure mode that makes a regression look better than it is.
    """
    rng = random.Random(seed)
    design, targets = [], []
    for _ in range(samples):
        p, a, d = (rng.uniform(-1, 1) for _ in range(3))
        family = rng.choice(FAMILIES)
        design.append(_features(p, a, d, family))
        targets.append([W_TRUE[t][0] + sum(W_TRUE[t][i + 1] * design[-1][i + 1]
                                          for i in range(len(FEATURES) - 1))
                        + rng.gauss(0.0, noise) for t in TARGETS])
    # Held-out split, generated from a different seed: recovery on the training
    # set alone would not distinguish a fit from a memorisation of the noise.
    hold_design, hold_targets = [], []
    rng = random.Random(seed + 7919)
    for _ in range(max(200, samples // 5)):
        p, a, d = (rng.uniform(-1, 1) for _ in range(3))
        family = rng.choice(FAMILIES)
        hold_design.append(_features(p, a, d, family))
        hold_targets.append([W_TRUE[t][0] + sum(W_TRUE[t][i + 1] * hold_design[-1][i + 1]
                                                for i in range(len(FEATURES) - 1))
                             + rng.gauss(0.0, noise) for t in TARGETS])
    return design, targets, hold_design, hold_targets


def predict(coeffs: List[List[float]], design: Sequence[Sequence[float]]) -> List[List[float]]:
    return [[sum(c * f for c, f in zip(coeffs[t], row)) for t in range(len(coeffs))]
            for row in design]


# -- the bridge, both ways -----------------------------------------------------
def hand_map(p: float, a: float, d: float, family: str) -> Dict[str, float]:
    """The original hand-mapped bridge, for comparison against the fitted one.

    Calls `hand_activation_from_pad` explicitly, NOT `activation_from_pad`. The
    first version of this tool got that wrong: once the public function began
    preferring the fitted file, `hand_map` was returning the fitted map and the
    divergence report compared the fitted bridge to itself, reporting 0.001
    where the true mean difference is 0.135. An instrument that quietly measures
    identity is worse than no instrument, because it reports success.
    """
    return dict(hand_activation_from_pad(
        (p, a, d), () if family == "none" else (family,)))


def fitted_map(coeffs: List[List[float]], p: float, a: float, d: float,
               family: str) -> Dict[str, float]:
    row = _features(p, a, d, family)
    return {TARGETS[t]: clamp(sum(c * f for c, f in zip(coeffs[t], row)))
            for t in range(len(TARGETS))}


def compare(coeffs: List[List[float]], grid_step: float = 0.5) -> Dict[str, object]:
    """Divergence between hand and fitted bridges on a grid of inputs."""
    values = [i * grid_step for i in range(int(-1 / grid_step), int(1 / grid_step) + 1)]
    deltas: List[float] = []
    top_flips = 0
    total = 0
    per_construct: Dict[str, List[float]] = {t: [] for t in TARGETS}
    for p in values:
        for a in values:
            for d in values:
                for family in FAMILIES:
                    hand = hand_map(p, a, d, family)
                    fit = fitted_map(coeffs, p, a, d, family)
                    for t in TARGETS:
                        if t not in hand:
                            continue  # hand map is silent for this construct/family
                        delta = abs(hand[t] - fit[t])
                        deltas.append(delta)
                        per_construct[t].append(delta)
                    total += 1
                    if hand and max(hand, key=hand.get) != max(fit, key=fit.get):
                        top_flips += 1
    return {
        "inputs_compared": total,
        "mean_abs_delta": sum(deltas) / len(deltas) if deltas else 0.0,
        "max_abs_delta": max(deltas) if deltas else 0.0,
        "top_construct_flip_rate": top_flips / total if total else 0.0,
        "per_construct_mean_delta": {k: (sum(v) / len(v) if v else 0.0)
                                     for k, v in per_construct.items()},
    }


def downstream(coeffs: List[List[float]], grid_step: float = 0.5) -> Dict[str, object]:
    """How often `triage`'s collapse detector changes verdict between bridges.

    No text is passed, so this isolates the STATE channel. The published
    thresholds (self_worth <= -0.6, avoidance >= 0.6, rumination >= 0.6) are
    what a stance decision is gated on, which is why this is the part worth
    measuring rather than an invented "stance agreement" score.
    """
    from PSYCHE.rt_agent import triage  # local: keeps import cost off --check

    values = [i * grid_step for i in range(int(-1 / grid_step), int(1 / grid_step) + 1)]
    changed = 0
    total = 0
    transitions: Dict[str, int] = {}
    for p in values:
        for a in values:
            for d in values:
                for family in FAMILIES:
                    hand = triage("", hand_map(p, a, d, family)).risk
                    fit = triage("", fitted_map(coeffs, p, a, d, family)).risk
                    total += 1
                    if hand != fit:
                        changed += 1
                        key = "%s->%s" % (hand, fit)
                        transitions[key] = transitions.get(key, 0) + 1
    return {"inputs": total, "verdict_changes": changed,
            "change_rate": changed / total if total else 0.0,
            "transitions": dict(sorted(transitions.items(), key=lambda kv: -kv[1]))}


# -- the report ----------------------------------------------------------------
def fit_and_verify(samples: int = DEFAULT_SAMPLES, noise: float = DEFAULT_NOISE,
                   seed: int = 20261101) -> Dict[str, object]:
    design, targets, hold_design, hold_targets = synthesise(samples, noise, seed)
    coeffs = ridge_fit(design, targets)
    hold_pred = predict(coeffs, hold_design)
    train_pred = predict(coeffs, design)
    ses = standard_errors(design, RIDGE_LAMBDA, noise)
    recovery = {}
    worst_err = 0.0
    worst_z = 0.0
    for t_index, name in enumerate(TARGETS):
        truth = W_TRUE[name]
        errs = [abs(coeffs[t_index][i] - truth[i]) for i in range(len(FEATURES))]
        zs = [errs[i] / ses[i] if ses[i] else 0.0 for i in range(len(FEATURES))]
        worst_err = max(worst_err, max(errs))
        worst_z = max(worst_z, max(zs))
        recovery[name] = {"max_abs_error": max(errs), "max_z": max(zs),
                          "recovered": coeffs[t_index]}
    return {
        "coefficients": coeffs,
        "recovery": recovery,
        "worst_recovery_error": worst_err,
        "worst_recovery_z": worst_z,
        "recovery_z_limit": RECOVERY_Z,
        "standard_errors": ses,
        "recovered": worst_z <= RECOVERY_Z,
        "train_r2": r_squared(targets, train_pred),
        "holdout_r2": r_squared(hold_targets, hold_pred),
        "samples": samples,
        "noise_sigma": noise,
        "ridge_lambda": RIDGE_LAMBDA,
        "features": list(FEATURES),
        "targets": list(TARGETS),
    }


PROVENANCE = (
    "Fitted by ridge regression and verified by COEFFICIENT RECOVERY against a "
    "synthetic ground truth. A passing recovery proves the ESTIMATOR is correct; "
    "it proves nothing about whether the bridge is linear, whether these "
    "constructs are the right ones, or whether PAD is the right input space. No "
    "data from real people informed this file -- see tools/psyche_fit_pams.py, "
    "which exits 2 because no construct data exists in any PAMS store."
)


def write_bridge(report: Dict[str, object]) -> Path:
    payload = {
        "_meta": {
            "provenance": PROVENANCE,
            "generated_by": "tools/fit_affect_bridge.py",
            "fitted": "2026-11-01",
            "features": report["features"],
            "targets": report["targets"],
            "worst_recovery_error": report["worst_recovery_error"],
            "worst_recovery_z": report["worst_recovery_z"],
            "recovery_z_limit": report["recovery_z_limit"],
            "recovered_within_sampling_error": report["recovered"],
            "holdout_r2": report["holdout_r2"],
            "synthetic_ground_truth": True,
            "warning": ("Do not read holdout_r2 as model quality: the data was "
                        "generated by a linear model with the same form, so a high "
                        "R^2 is a consistency check, not evidence about people."),
        },
        "coefficients": {name: report["recovery"][name]["recovered"]
                         for name in report["targets"]},
    }
    BRIDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BRIDGE_FILE.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return BRIDGE_FILE


#: Feature set for a fit against REAL records. No family indicators: a self-report
#: session has a PAD reading and nothing that says whether the person appraised the
#: moment as threat or as sadness, and inventing that from text would put the
#: inference model's assumptions back into the fit it is supposed to be checked
#: against. The consequence is stated plainly below: these coefficients are not
#: comparable to W_TRUE, because they describe a different model.
PEOPLE_FEATURES: Tuple[str, ...] = ("1", "p", "a", "d")

#: Minimum records before a per-person bridge is fitted. Below this the design is
#: four parameters under-determined by noise, and a fitted line is a line through
#: whatever the four points happened to be.
MIN_PEOPLE_RECORDS = 50


class Refused(ValueError):
    """Raised when a fit cannot be justified. The refusal IS the deliverable."""


def fit_from_records(rows: Sequence[Dict[str, object]]) -> Dict[str, object]:
    """Fit the bridge against elicited records: (pad, constructs) pairs from people.

    Refuses loudly and early rather than producing a number that looks like a
    result: too few records, a constant construct, or a rank-deficient design are
    all reasons a fit exists numerically and means nothing.
    """
    if len(rows) < MIN_PEOPLE_RECORDS:
        raise Refused("only %d record(s); %d are needed before a four-parameter fit "
                      "is anything but a line through four points"
                      % (len(rows), MIN_PEOPLE_RECORDS))
    instruments = {str(r.get("instrument", "")) for r in rows}
    if len(instruments) > 1:
        raise Refused("records come from %d instrument versions (%s); item text or "
                      "scoring changed, so the constructs are not the same quantity"
                      % (len(instruments), ", ".join(sorted(instruments))))

    targets = sorted({str(k) for r in rows for k in (r.get("constructs") or {})})
    design: List[List[float]] = []
    values: List[List[float]] = []
    for row in rows:
        pad = [float(v) for v in (row.get("pad") or [])]  # type: ignore[union-attr]
        constructs = row.get("constructs") or {}
        if len(pad) != 3 or not all(t in constructs for t in targets):
            continue
        design.append([1.0] + pad)
        values.append([float(constructs[t]) for t in targets])  # type: ignore[index]
    if len(design) < MIN_PEOPLE_RECORDS:
        raise Refused("only %d complete record(s) after dropping incomplete rows"
                      % len(design))

    spread = {t: max(v[i] for v in values) - min(v[i] for v in values)
              for i, t in enumerate(targets)}
    flat = sorted(t for t, s in spread.items() if s < 1e-9)
    if flat:
        raise Refused("construct(s) %s never vary across these records: a constant "
                      "target cannot be regressed on anything" % ", ".join(flat))

    coeffs = ridge_fit(design, values)
    predicted = predict(coeffs, design)
    residuals = [[a - p for a, p in zip(actual, pred)]
                 for actual, pred in zip(values, predicted)]
    n, k = len(design), len(PEOPLE_FEATURES)
    sigma = {t: (sum(r[i] ** 2 for r in residuals) / max(1, n - k)) ** 0.5
             for i, t in enumerate(targets)}
    ses = standard_errors(design, RIDGE_LAMBDA, 1.0)

    # Leave-one-out is the honest check on a per-person fit: an in-sample R^2 with
    # four parameters and one person's mornings is guaranteed to look good.
    loo_errors: List[float] = []
    for hold in range(len(design)):
        train_x = [d for i, d in enumerate(design) if i != hold]
        train_y = [v for i, v in enumerate(values) if i != hold]
        try:
            held = ridge_fit(train_x, train_y)
        except ValueError:
            continue
        for i in range(len(targets)):
            loo_errors.append(abs(sum(c * f for c, f in
                                      zip(held[i], design[hold])) - values[hold][i]))

    return {
        "targets": targets,
        "features": list(PEOPLE_FEATURES),
        "records": len(design),
        "coefficients": {t: coeffs[i] for i, t in enumerate(targets)},
        "residual_sigma": sigma,
        "standard_error": {f: ses[i] for i, f in enumerate(PEOPLE_FEATURES)},
        "r2": r_squared(values, predicted),
        "loo_mae": (sum(loo_errors) / len(loo_errors)) if loo_errors else None,
        "instrument": instruments.pop() if instruments else "unknown",
    }


PEOPLE_PROVENANCE = (
    "Fitted by ridge regression against ELICITED SELF-REPORT records, not against "
    "a synthetic ground truth. Features are [1, pleasure, arousal, dominance]: no "
    "appraisal-family indicators, because a self-report session does not observe "
    "one. These coefficients therefore describe a DIFFERENT model from the "
    "synthetic fit and must not be compared to it coefficient by coefficient. "
    "They have no psychometric standing beyond the instrument that produced them "
    "(see src/PSYCHE/elicitation.py, which states the same thing about the items)."
)


def render_people(report: Dict[str, object]) -> str:
    lines: List[str] = []
    add = lines.append
    add("=" * 78)
    add("AFFECT -> CONSTRUCT BRIDGE, FITTED FROM ELICITED RECORDS")
    add("=" * 78)
    add("records %d   instrument %s   features %s"
        % (report["records"], report["instrument"], ",".join(report["features"])))
    add("in-sample R^2 %.4f   leave-one-out MAE %.4f"
        % (report["r2"], report["loo_mae"] if report["loo_mae"] is not None else float("nan")))
    add("")
    add("   %-20s %8s %8s %8s %8s" % ("construct", "1", "p", "a", "d"))
    for target in report["targets"]:
        row = report["coefficients"][target]
        add("   %-20s %8.3f %8.3f %8.3f %8.3f" % ((target,) + tuple(row)))
    add("")
    add("WHAT THIS IS")
    add("  A per-person fit from self-report. It says how THIS respondent's stated")
    add("  constructs moved with their stated affect across %d sessions." % report["records"])
    add("WHAT THIS IS NOT")
    add("  Not a population model, not a validation of the instrument, and not")
    add("  comparable to the synthetic coefficients: the feature set differs by")
    add("  construction, so a coefficient-by-coefficient comparison is meaningless.")
    add("  One respondent is one respondent. Leave-one-out MAE is the number to")
    add("  read first; in-sample R^2 with four parameters is not.")
    return "\n".join(lines)


def render(report: Dict[str, object], div: Dict[str, object],
           down: Dict[str, object]) -> str:
    lines: List[str] = []
    add = lines.append
    add("=" * 78)
    add("AFFECT -> CONSTRUCT BRIDGE  (ridge regression, synthetic ground truth)")
    add("=" * 78)
    add("Features %s ; targets %d ; samples %d ; noise sigma %.3f ; lambda %.0e"
        % (",".join(FEATURES), len(TARGETS), report["samples"], report["noise_sigma"],
           report["ridge_lambda"]))
    add("")
    add("1. COEFFICIENT RECOVERY -- does the estimator return a matrix it was handed?")
    add("   %-20s %12s %8s" % ("target", "max |error|", "max z"))
    for name in TARGETS:
        add("   %-20s %12.2e %8.2f"
            % (name, report["recovery"][name]["max_abs_error"],
               report["recovery"][name]["max_z"]))
    add("   worst %.2e   worst z %.2f   limit %.1f   ->  %s"
        % (report["worst_recovery_error"], report["worst_recovery_z"],
           report["recovery_z_limit"],
           "RECOVERED (within its own sampling error)" if report["recovered"]
           else "FAILED"))
    add("   train R^2 %.6f   holdout R^2 %.6f" % (report["train_r2"], report["holdout_r2"]))
    add("")
    add("2. DIVERGENCE from the hand map, over %d grid inputs" % div["inputs_compared"])
    add("   mean |delta| %.4f   max |delta| %.4f   top-construct flip rate %.1f%%"
        % (div["mean_abs_delta"], div["max_abs_delta"],
           100.0 * div["top_construct_flip_rate"]))
    for name, delta in sorted(div["per_construct_mean_delta"].items(),
                              key=lambda kv: -kv[1]):
        if delta:
            add("     %-20s %7.4f" % (name, delta))
    add("")
    add("3. DOWNSTREAM -- collapse-detector verdict changes (%d state-only inputs)"
        % down["inputs"])
    add("   changes %d  (%.1f%%)" % (down["verdict_changes"], 100.0 * down["change_rate"]))
    for transition, count in down["transitions"].items():
        add("     %-28s %5d" % (transition, count))
    add("")
    add("WHAT THIS DOES NOT PROVE")
    add("  The ground truth is synthetic, written from appraisal theory by the same")
    add("  author who hand-mapped the bridge. Recovery proves the ESTIMATOR works.")
    add("  It does not validate the model form, the construct list, or the input")
    add("  space. holdout_r2 is a consistency check, not model quality: the data")
    add("  came from a model of the same shape, so it can never be otherwise.")
    add("  No real-person construct data exists (tools/psyche_fit_pams.py exits 2).")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--noise", type=float, default=DEFAULT_NOISE)
    parser.add_argument("--seed", type=int, default=20261101)
    parser.add_argument("--no-write", action="store_true",
                        help="report only; leave src/PSYCHE/data/affect_bridge.json alone")
    parser.add_argument("--check", action="store_true",
                        help="verify the shipped coefficients still recover; write nothing")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--from-records", metavar="PATH",
                        help="fit against elicited self-report records (JSONL) instead "
                             "of the synthetic ground truth")
    parser.add_argument("--out", metavar="PATH",
                        help="where to write the people-fitted bridge "
                             "(default: data/affect_bridge_people.json)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.from_records:
        rows = [json.loads(line) for line in
                Path(args.from_records).read_text(encoding="utf-8").splitlines()
                if line.strip()]
        try:
            report = fit_from_records(rows)
        except Refused as exc:
            print("REFUSED: %s" % exc, file=sys.stderr)
            print("A refused fit is the correct answer here. Point --from-records at "
                  "more sessions from the same instrument version.", file=sys.stderr)
            return 2
        target = Path(args.out) if args.out else \
            Path(BRIDGE_FILE).with_name("affect_bridge_people.json")
        payload = dict(report)
        payload["provenance"] = PEOPLE_PROVENANCE
        payload["fitted"] = "2026-11-01"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=1) if args.json else render_people(report))
        if not args.json:
            print("\nwrote %s" % target)
        return 0

    report = fit_and_verify(args.samples, args.noise, args.seed)

    if args.check:
        if not BRIDGE_FILE.exists():
            print("MISSING %s -- run without --check to generate it" % BRIDGE_FILE, file=sys.stderr)
            return 2
        shipped = json.loads(BRIDGE_FILE.read_text(encoding="utf-8"))
        drift = 0.0
        for t_index, name in enumerate(TARGETS):
            row = shipped.get("coefficients", {}).get(name)
            if row is None:
                print("MISSING coefficients for %s" % name, file=sys.stderr)
                return 2
            drift = max(drift, max(abs(float(row[i]) - report["coefficients"][t_index][i])
                                   for i in range(len(FEATURES))))
        # Deterministic given the seed, so any drift at all means the file was
        # edited by hand. Comparing against W_TRUE instead would let a hand-edit
        # that happens to look plausible pass; comparing against the fresh fit
        # catches the edit itself.
        ok = drift <= 1e-9 and report["recovered"]
        print("shipped vs fresh fit: max drift %.2e ; recovery z %.2f -> %s"
              % (drift, report["worst_recovery_z"], "OK" if ok else "DRIFTED"))
        if not ok:
            print("The shipped file no longer matches what the tool fits. Either it was "
                  "hand-edited or the ground truth changed; re-fit and say which in the "
                  "commit message, do not just re-run the tool.", file=sys.stderr)
        return 0 if ok else 1

    div = compare(report["coefficients"])
    down = downstream(report["coefficients"])
    payload = dict(report)
    payload["divergence"] = div
    payload["downstream"] = down
    payload["provenance"] = PROVENANCE

    if args.json:
        print(json.dumps(payload, indent=1))
    else:
        print(render(report, div, down))

    if not report["recovered"]:
        print("\nFIT FAILED recovery. Not writing a bridge that cannot find its own "
              "ground truth.", file=sys.stderr)
        return 1

    if not args.no_write:
        path = write_bridge(report)
        if not args.json:
            print("\nwrote %s" % path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
