# coding: utf-8
"""PSYCHE Phase 1 — affect: PAD space, OCC appraisal, regulation, TD bridge.

Quarter 2 of 4 in the person artifact (P1): the appraisal function
A: event x goals -> PAD. It reads theta (Phase 0) and episodic context,
and its outputs feed policy (Phase 3). Every parameter is per-person and
editable; group-level claims appear nowhere in the computation.

INDIVIDUAL-FIRST, by construction (read before extending):
  * No function in this module takes a demographic attribute. There is no
    gender/age/culture parameter to set, so no caller can route a person
    through a group template. `test_no_demographic_inputs` introspects the
    signatures to pin this: add such a parameter and the suite fails.
  * Research basis: meta-analytic and neuroimaging work finds more
    similarity than difference in emotional CAPACITY across genders; the
    robust differences live in EXPRESSION norms and regulation strategies
    (socialization), which vary person to person. So: one appraisal
    machinery for everyone; thresholds, decay rates, regulation strategies,
    and display rules are per-person parameters learned from THAT person's
    data (Phase 2 fits them; here they are explicit arguments).
  * Expression is NEVER reported as experience. Display output always
    carries both felt and displayed PAD plus which rule, if any, modified
    the display — so the artifact cannot lecture a person about what they
    "really" feel on the basis of what they showed.

COMPLEXITY, not flattening:
  * Full OCC structure (Ortony, Clore & Collins): events (desirability x
    likelihood x effort x realization -> hope/fear/satisfaction/relief/
    disappointment/joy/distress + fortunes-of-others), actions
    (praiseworthiness x self/other agency -> pride/shame/admiration/
    reproach), objects (appealingness x familiarity -> love/hate).
    Intensity variables are computed, not asserted.
  * Mixed emotions: appraisal returns a RANKED LIST of (emotion,
    intensity, basis), never a single label. People feel several things.
  * Regulation follows Gross's process model: reappraisal (cognitive
    change) alters the appraisal and therefore the FEELING; suppression
    (response modulation) alters DISPLAY only. The dissociation is tested.
  * Affective chronometry: rise/decay rates are per-person parameters;
    decay runs toward the person's own temperament baseline, not zero.
  * TD bridge (arXiv 2309.06367 pattern): reward-prediction errors and
    state visitation from `learning_rl` map to appraisal checks
    (suddenness, relevance, conduciveness, power). The learning system and
    the feeling system read the same events.

Temperament weights below are INITIAL, approximately signed after
Mehrabian's PAD-temperament regressions, and REPLACEABLE: Phase 2 fits
them per person. Tests pin directions and editability, not the constants.
"""
from __future__ import annotations

import math
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Initial temperament weights: (E, A, N, O, C, H) multipliers.
# Signs follow the published pattern (E/A lift pleasure, N depresses it;
# N damps arousal regulation, E/C raise dominance agency). Magnitudes are
# explicitly provisional -- identification fits them per person.
TEMPERAMENT_WEIGHTS = {
    "P": {"E": 0.21, "A": 0.59, "N": -0.19, "O": 0.0, "C": 0.0, "H": 0.0},
    "A": {"E": 0.30, "A": 0.0, "N": -0.57, "O": 0.15, "C": 0.0, "H": 0.0},
    "D": {"E": 0.60, "A": -0.32, "N": 0.0, "O": 0.25, "C": 0.17, "H": 0.0},
}

# OCC emotion families with eliciting structure. Each entry:
# (branch, conditions-summary). Intensity computed per family below.
OCC_FAMILIES = (
    "joy", "distress",              # well-being: desirable/undesirable event
    "hope", "fear",                 # prospect: unconfirmed desirable/undesirable
    "satisfaction", "fears_confirmed",
    "relief", "disappointment",     # prospect confirmation/disconfirmation
    "happy_for", "resentment",      # fortunes of others, liked agent
    "gloating", "pity",             # fortunes of others, disliked agent
    "pride", "shame",               # self action praiseworthy/blameworthy
    "admiration", "reproach",        # other action praiseworthy/blameworthy
    "love", "hate",                 # object appealing/unappealing
    "gratitude", "anger",           # compound: desirable event + praiseworthy act
)


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def pad_normalize(pad: tuple[float, ...]) -> tuple[float, float, float]:
    vals = tuple(clamp(v) for v in pad)
    if len(vals) != 3:
        raise ValueError(f"PAD state needs 3 components, got {len(vals)}")
    p, a, d = vals
    return (round(p, 4), round(a, 4), round(d, 4))


def pad_distance(a: tuple[float, float, float],
                 b: tuple[float, float, float]) -> float:
    return round(math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b))), 4)


def pad_blend(a: tuple[float, float, float], b: tuple[float, float, float],
              weight: float = 0.5) -> tuple[float, float, float]:
    """Weighted blend of two PAD states (mixed emotions live here)."""
    w = max(0.0, min(1.0, weight))
    return pad_normalize(tuple(x * (1.0 - w) + y * w for x, y in zip(a, b)))


def pad_decay(current: tuple[float, float, float],
              baseline: tuple[float, float, float],
              rate: float, dt: float = 1.0) -> tuple[float, float, float]:
    """Exponential return toward the person's OWN baseline (not zero).

    rate is per-person (affective chronometry). Higher rate = faster
    return. Baseline is temperament, so "calm" differs per person.
    """
    k = max(0.0, 1.0 - math.exp(-max(0.0, rate) * max(0.0, dt)))
    return pad_normalize(tuple(c + (b - c) * k for c, b in zip(current, baseline)))


def temperament(domains: dict[str, float],
                weights: dict | None = None) -> tuple[float, float, float]:
    """Mean-PAD baseline from trait domains (P4: behavior in, traits out).

    domains: 1..5 per-domain scores (TraitVector.domains()). Missing
    domains are skipped (partial profiles stay honest). Weights default
    to TEMPERAMENT_WEIGHTS; pass fitted weights to override.
    """
    w = weights or TEMPERAMENT_WEIGHTS
    out = []
    for dim in ("P", "A", "D"):
        num, den = 0.0, 0.0
        for domain, val in domains.items():
            if not isinstance(val, (int, float)):
                continue
            coef = (w.get(dim) or {}).get(domain, 0.0)
            centered = (float(val) - 3.0) / 2.0  # 1..5 -> -1..1
            num += coef * centered
            den += abs(coef)
        out.append(num / den if den else 0.0)
    return pad_normalize(tuple(out))


def _intensity(base: float, likelihood: float = 1.0, effort: float = 0.0,
               unexpectedness: float = 0.0) -> float:
    """OCC intensity: |value| scaled by likelihood, effort, surprise."""
    v = abs(float(base))
    v *= 0.5 + 0.5 * max(0.0, min(1.0, float(likelihood)))
    v *= 1.0 + 0.5 * max(0.0, min(1.0, float(effort)))
    v *= 1.0 + 0.5 * max(0.0, min(1.0, float(unexpectedness)))
    return round(max(0.0, min(1.0, v)), 4)


def appraise_event(desirability: float, likelihood: float = 1.0,
                   confirmed: bool | None = None, effort: float = 0.0,
                   unexpectedness: float = 0.0,
                   for_other: bool = False,
                   liking_other: float = 0.0) -> list[tuple[str, float, str]]:
    """OCC event branch -> ranked [(emotion, intensity, basis)].

    confirmed None = prospect (hope/fear); True/False = confirmation
    outcome (satisfaction/fears_confirmed/relief/disappointment).
    for_other routes through fortunes-of-others with liking sign.
    Returns empty only for a truly neutral event (|d| ~ 0).
    """
    d = clamp(desirability)
    out: list[tuple[str, float, str]] = []
    if abs(d) < 0.05:
        return out
    mag = _intensity(d, likelihood, effort, unexpectedness)
    if for_other:
        like = liking_other >= 0
        if d > 0:
            fam = "happy_for" if like else "resentment"
        else:
            fam = "pity" if like else "gloating"
        out.append((fam, mag, "fortune of %sliked other" % ("" if like else "dis")))
    elif confirmed is None:
        fam = "hope" if d > 0 else "fear"
        out.append((fam, mag, "prospect, likelihood %.2f" % likelihood))
    else:
        if d > 0:
            fam = "satisfaction" if confirmed else "disappointment"
        else:
            fam = "fears_confirmed" if confirmed else "relief"
        out.append((fam, mag, "prospect %sconfirmed" % ("" if confirmed else "dis")))
    # Well-being tone always co-occurs (joy/distress underlie prospect).
    out.append(("joy" if d > 0 else "distress",
                round(mag * 0.6, 4), "well-being tone"))
    out.sort(key=lambda r: -r[1])
    return out


def appraise_action(praiseworthiness: float, self_agent: bool = True,
                    effort: float = 0.0) -> list[tuple[str, float, str]]:
    """OCC action branch: pride/shame (self) or admiration/reproach (other)."""
    p = clamp(praiseworthiness)
    if abs(p) < 0.05:
        return []
    mag = _intensity(p, 1.0, effort)
    if self_agent:
        fam = "pride" if p > 0 else "shame"
    else:
        fam = "admiration" if p > 0 else "reproach"
    return [(fam, mag, "own action" if self_agent else "other action")]


def appraise_object(appealingness: float, familiarity: float = 0.5) -> list[tuple[str, float, str]]:
    """OCC object branch: love/hate, strengthened by familiarity."""
    a = clamp(appealingness)
    if abs(a) < 0.05:
        return []
    mag = _intensity(a, 0.5 + 0.5 * max(0.0, min(1.0, familiarity)))
    return [("love" if a > 0 else "hate", mag, "object appeal")]


def compound_gratitude_anger(event_out: list, action_out: list) -> list[tuple[str, float, str]]:
    """OCC compounds: desirable event + praiseworthy act -> gratitude;
    undesirable event + blameworthy act -> anger."""
    have = {fam for fam, _i, _b in event_out} | {fam for fam, _i, _b in action_out}
    out = []
    if {"joy", "satisfaction", "happy_for"} & have and \
            {"pride", "admiration"} & have:
        out.append(("gratitude", 0.7, "desirable event caused by praiseworthy act"))
    if {"distress", "disappointment", "fears_confirmed"} & have and \
            {"shame", "reproach"} & have:
        out.append(("anger", 0.7, "undesirable event caused by blameworthy act"))
    return out


def emotion_to_pad(family: str, intensity: float = 1.0) -> tuple[float, float, float]:
    """Canonical PAD direction per OCC family (centroids, editable).

    Positive families pull toward +P; prospect families raise A;
    agency (pride/admiration/anger) raises D, subjugation (shame/pity/
    fear) lowers it. Intensity scales the displacement from neutral.
    """
    table = {
        "joy": (0.8, 0.3, 0.2), "distress": (-0.8, 0.3, -0.2),
        "hope": (0.5, 0.6, 0.1), "fear": (-0.6, 0.8, -0.5),
        "satisfaction": (0.9, 0.2, 0.4), "fears_confirmed": (-0.9, 0.5, -0.6),
        "relief": (0.7, -0.3, 0.3), "disappointment": (-0.7, 0.2, -0.3),
        "happy_for": (0.7, 0.2, 0.1), "resentment": (-0.5, 0.5, -0.2),
        "gloating": (0.4, 0.5, 0.4), "pity": (-0.4, 0.1, -0.3),
        "pride": (0.6, 0.4, 0.7), "shame": (-0.6, 0.4, -0.7),
        "admiration": (0.6, 0.3, -0.1), "reproach": (-0.5, 0.5, 0.2),
        "love": (0.8, 0.2, 0.0), "hate": (-0.8, 0.5, 0.1),
        "gratitude": (0.8, 0.2, -0.2), "anger": (-0.7, 0.8, 0.6),
    }
    vec = table.get(family, (0.0, 0.0, 0.0))
    k = max(0.0, min(1.0, float(intensity)))
    return pad_normalize(tuple(v * k for v in vec))


# ── Regulation (Gross process model): strategies are per-person data ──

def reappraise_desirability(desirability: float, factor: float) -> float:
    """Cognitive change: re-interpreting the event scales its desirability,
    which changes everything downstream INCLUDING the feeling. factor > 1
    amplifies, < 1 dampens. This is why reappraisal works and suppression
    does not: it edits the appraisal, not the output."""
    return clamp(float(desirability) * float(factor))


def suppress_display(pad: tuple[float, float, float],
                     factor: float) -> tuple[float, float, float]:
    """Response modulation: scales the DISPLAYED state only.

    The felt state is untouched (returned separately by the caller holding
    both). Gross's dissociation, mechanized: suppress all you like, the
    feeling stays where the appraisal put it.
    """
    f = max(0.0, min(1.0, float(factor)))
    return pad_normalize(tuple(v * f for v in pad))


def express(felt: tuple[float, float, float],
            display_rules: dict | None = None) -> dict:
    """Felt vs displayed, with the rule cited or explicitly absent.

    display_rules (optional, per-person, sourced from THEIR data):
    {"amplify": [...families], "mask": [...], "dampen_factor": 0.x}.
    Returns {"felt": ..., "displayed": ..., "rule": <name|None>}.
    No rules -> displayed IS felt, stated, not assumed.
    """
    rules = display_rules or {}
    if not rules:
        return {"felt": felt, "displayed": felt, "rule": None}
    return {"felt": felt, "displayed": felt,
            "rule": str(rules.get("name", "custom")),
            "note": "display-rule application is caller-side; "
                    "felt state preserved verbatim above"}


# ── TD bridge: learning signals -> appraisal checks ─────────────

def td_appraisal(reward_prediction_error: float, state_visits: int = 0,
                 goal_value: float = 0.0, coping: float = 0.5) -> dict:
    """Map RL signals to appraisal checks (arXiv 2309.06367 pattern).

    suddenness: novelty = inverse visitation (first sight = maximally sudden)
    relevance: |goal value| at stake
    conduciveness: signed, scaled RPE (better/worse than expected)
    power: coping estimate (per-person parameter, NOT assumed high)
    desirability: conduciveness sign for event appraisal input.
    """
    visits = max(0, int(state_visits))
    suddenness = round(1.0 / (1.0 + visits), 4)
    relevance = round(max(0.0, min(1.0, abs(float(goal_value)))), 4)
    conduciveness = round(max(-1.0, min(1.0, float(reward_prediction_error))), 4)
    power = round(max(0.0, min(1.0, float(coping))), 4)
    return {"suddenness": suddenness, "relevance": relevance,
            "conduciveness": conduciveness, "power": power,
            "desirability": conduciveness}
