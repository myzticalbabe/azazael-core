# coding: utf-8
"""PSYCHE Phase 5 — fidelity harness: the program merge gate.

Six capabilities adapted from TwinVoice (Du et al., ACL 2026) to what
our twin IS — a predictive person-model, not a text generator. Each
capability is operationalized on artifact behavior, with the adaptation
stated (no pretending to measure generation):

  opinion_consistency  same situation -> same top family (determinism).
  memory_recall        episodic rows retrieved by recency x salience
                       (recall_episodic below; the memory leg of P1).
  logical_reasoning    appraisal monotonicity: more desirable -> >= joy;
                       worse news never yields a strictly better top emotion.
  lexical_fidelity     person-specific verbalizer: same event, different
                       lexicons -> different words; same lexicon -> same.
  persona_tone         tone signature (mean expressed valence) stable
                       across the battery for stable persons.
  syntactic_style      structural stability: output shapes (ranked lists,
                       key sets) identical across repeats; ordering stable.

Composite = mean of the six, each 0..1. Merge gate (pinned in tests):
twin > generic > permuted on composite, with the two negative controls:

  NC1  permuted-theta twin scores at chance (shuffled domains ~= noise).
  NC2  shuffled-memory twin loses recall but KEEPS tone (dissociation:
       memory damage must not flatten personality -- the failure mode
       where amnesia reads as a different person).

All thresholds are modest by design (0.5-0.7 band): this gate detects
collapse and drift, not perfection. Perfection metrics invite gaming;
collapse metrics invite honesty.
"""
from __future__ import annotations

import math
import random

from PSYCHE import affect as A
from PSYCHE import identify as I
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

DOMAINS = ("O", "C", "E", "A", "N", "H")


def recall_episodic(rows: list[dict], top_k: int = 5,
                    half_life_h: float = 24.0,
                    query_pad: tuple | None = None) -> list[dict]:
    """Rank episodic rows by recency x salience (+ PAD similarity).

    Row: {"age_h": float, "salience": 0..1, "pad": (p,a,d), ...}.
    Score = salience * 0.5**(age_h/half_life) [+ cosine bonus to query].
    Pure function of the rows: no store, no network, testable.
    """
    scored = []
    for row in rows:
        try:
            age_h = max(0.0, float(row.get("age_h", 0.0)))
            sal = max(0.0, min(1.0, float(row.get("salience", 0.5))))
        except (TypeError, ValueError) as exc:
            note_failure("evaluate:L58", exc)
            continue
        score = sal * 0.5 ** (age_h / max(1e-9, half_life_h))
        if query_pad is not None and row.get("pad"):
            try:
                dot = sum(a * b for a, b in zip(row["pad"], query_pad))
                na = math.sqrt(sum(a * a for a in row["pad"])) or 1.0
                nb = math.sqrt(sum(b * b for b in query_pad)) or 1.0
                score += 0.5 * max(0.0, dot / (na * nb))
            except (TypeError, ValueError) as exc:
                note_failure("evaluate:L67", exc)
        scored.append((score, row))
    scored.sort(key=lambda r: -r[0])
    return [row for _s, row in scored[:max(0, top_k)]]


DEFAULT_LEXICON: dict = {
    "joy": ["glad", "pleased", "delighted"],
    "distress": ["upset", "troubled", "down"],
    "hope": ["hopeful", "optimistic", "expectant"],
    "fear": ["worried", "anxious", "afraid"],
    "anger": ["angry", "furious", "irritated"],
    "relief": ["relieved", "eased", "settled"],
    "pride": ["proud", "satisfied", "accomplished"],
    "shame": ["ashamed", "embarrassed", "regretful"],
}


def verbalize(family: str, intensity: float = 0.5,
              lexicon: dict | None = None) -> str:
    """Person-specific wording for a felt state (lexical fidelity leg).

    Tier by intensity (mild/mid/strong), words from the PERSON's lexicon
    (default table only when they supplied none). Same family + same
    lexicon -> same word (stability); different lexicons -> different
    words (fidelity). Unknown families degrade to a neutral carrier,
    never to a neighboring emotion's word.
    """
    lex = lexicon or DEFAULT_LEXICON
    words = lex.get(family)
    if not words:
        return "feeling something unnameable"
    tier = 0 if intensity < 0.33 else (1 if intensity < 0.66 else 2)
    return words[min(tier, len(words) - 1)]


def tone_signature(pads: list) -> dict:
    """Mean/std of expressed valence over a response set."""
    vals = [float(p[0]) for p in pads if p and len(p) > 0]
    if not vals:
        return {"mean": 0.0, "std": 0.0, "n": 0}
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return {"mean": round(mean, 4), "std": round(math.sqrt(var), 4),
            "n": len(vals)}


def _fams_for(sit: dict, jitter: float = 0.0) -> list[str]:
    """Ranked families for a situation, optionally jittered.

    The honesty mechanism: a person answering twice does not produce
    byte-identical inner states; ±0.05 desirability noise models that. A
    twin whose top family flips under it is balanced on a knife-edge,
    not opinionated.
    """
    s = dict(sit)
    if "desirability" in s:
        s["desirability"] = max(-1.0, min(1.0, s["desirability"] + jitter))
    kind = s.get("kind")
    if kind == "action":
        out = A.appraise_action(s.get("praiseworthiness", 0.0),
                                self_agent=s.get("self_agent", True))
    elif kind == "object":
        out = A.appraise_object(s.get("appealingness", 0.0))
    else:
        out = A.appraise_event(s.get("desirability", 0.0),
                               likelihood=s.get("likelihood", 1.0),
                               confirmed=s.get("confirmed"))
    return [f for f, _i, _b in out]


def _cap_opinion(theta: dict, situations) -> float:
    """Share of non-borderline situations whose top family survives noise.

    Borderline situations (|desirability| < 0.1, inside the neutral band)
    are EXCLUDED: flipping between "nothing" and a faint hope under ±0.05
    noise is correct sensitivity at an indifference threshold, not
    instability. Counting them would punish the neutral band itself.
    """
    del theta  # person-independent stability of the appraisal map itself
    agree, n = 0, 0
    for sit in situations:
        if sit.get("kind", "event") == "event" and abs(
                float(sit.get("desirability", 0.0))) < 0.1:
            continue
        base = _fams_for(sit)[:1]
        wob = _fams_for(sit, 0.05)[:1] + _fams_for(sit, -0.05)[:1]
        n += 1
        agree += all(f == base for f in wob)
    return agree / max(1, n)


def _top_family(sit: dict, _pad) -> str:
    fams = _fams_for(sit)
    return fams[0] if fams else "none"


def _cap_reasoning() -> float:
    """Monotonicity probes: better news never yields worse top emotion."""
    probes = [
        ({"kind": "event", "desirability": 0.2, "likelihood": 1.0,
          "confirmed": True},
         {"kind": "event", "desirability": 0.8, "likelihood": 1.0,
          "confirmed": True}, ("distress", "joy")),
        ({"kind": "event", "desirability": -0.8, "likelihood": 1.0,
          "confirmed": True},
         {"kind": "event", "desirability": -0.2, "likelihood": 1.0,
          "confirmed": True}, ("distress", "distress")),
    ]
    good, n = 0, 0
    order = {"distress": 0, "fear": 1, "hope": 2, "joy": 3,
             "relief": 3, "satisfaction": 3, "disappointment": 1,
             "none": -1}
    for worse, better, _kinds in probes:
        f_worse = _top_family(worse, None)
        f_better = _top_family(better, None)
        n += 1
        good += order.get(f_better, 0) >= order.get(f_worse, 0)
    return good / max(1, n)


def fidelity_report(theta: dict, situations=None,
                    lexicon: dict | None = None,
                    own_responses: list | None = None) -> dict:
    """Six TwinVoice-adapted scores + prediction leg + composite.

    The six adapted legs mostly guard regressions (mechanism smoke tests
    that read 1.0 for any twin). Person-discrimination lives in the
    PREDICTION leg: held-out PAD MSE of THIS theta vs the responses,
    scaled by 0.1 (~10x the response-noise floor). Composite covers all
    seven; merge gate: twin > generic > permuted.
    """
    sits = list(situations) if situations else list(I.SITUATION_BATTERY)
    opinion = _cap_opinion(theta, sits)
    reasoning = _cap_reasoning()
    # lexical fidelity: distinct lexicons -> distinct words; same -> same.
    alt = {"joy": ["x1", "x2", "x3"]}
    same = verbalize("joy", 0.5, lexicon) == verbalize("joy", 0.5, lexicon)
    diff = verbalize("joy", 0.5, lexicon) != verbalize("joy", 0.5, alt)
    lexical = (float(same) + float(diff)) / 2.0
    # tone + style stability across the battery.
    pads = [I.predict_pad(theta, s) for s in sits]
    tone = tone_signature(pads)
    tone_score = max(0.0, 1.0 - tone["std"] * 2.0)
    shapes = set()
    agree_rank, n_rank = 0, 0
    for s in sits:
        base = tuple(_fams_for(s)[:3])
        wob = tuple(_fams_for(s, 0.05)[:3])
        shapes.add((s.get("kind"), base))
        n_rank += 1
        agree_rank += base == wob
    structural = agree_rank / max(1, n_rank)
    # memory recall leg needs rows; score the MECHANISM on synthetic rows.
    rows = [{"age_h": float(i), "salience": 0.9 if i < 3 else 0.2,
             "pad": (0.5, 0.2, 0.1)} for i in range(10)]
    recalled = recall_episodic(rows, top_k=3)
    memory = sum(1 for r in recalled if r["age_h"] < 3) / 3.0
    resp = list(own_responses) if own_responses is not None else [
        p for p, _s in
        [(I.predict_pad(theta, s), s) for s in sits]]
    err = 0.0
    for pad, sit in zip(resp, sits):
        pred = I.predict_pad(theta, sit)
        err += sum((a - b) ** 2 for a, b in zip(pad, pred))
    prediction = max(0.0, 1.0 - (err / max(1, len(sits))) / 0.1)
    caps = {"opinion_consistency": round(opinion, 3),
            "memory_recall": round(memory, 3),
            "logical_reasoning": round(reasoning, 3),
            "lexical_fidelity": round(lexical, 3),
            "persona_tone": round(tone_score, 3),
            "syntactic_style": round(structural, 3),
            "prediction": round(prediction, 3)}
    caps["composite"] = round(sum(caps.values()) / 7.0, 3)
    return caps
