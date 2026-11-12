"""Meta-evolution over recorded evidence.

The thing being evolved is not code and not prose: it is the **admissibility
parameters** a verdict is computed with. The evidence is a set of rows that
already exist on disk, so a genome is scored by *replaying* those rows under its
parameters. No model calls, no re-judging, no fabricated fitness.

Objectives (all maximised, then Pareto-filtered):

``verdicts``    how many pins obtain a verdict at all
``stability``   how many pins keep that verdict across an independent re-draw
``margin``      the smallest distance from any pin's share to the quorum -- the
                robustness of the choice to corpus movement

Hard constraint: **no pin below quorum may obtain a verdict.** That single line is
what makes a split vote unrepresentable as a verdict, and it is the constraint the
pre-RC rule violated.

Two honesties the engine enforces on itself, because an engine that pads a
population with genes that do nothing is the same defect as an orphan knob:

* ``inert_genes`` names every gene that changed no objective on this corpus. They
  are still reported in the overlay -- with their value -- but they are **not**
  claimed to be tuned.
* the frontier is reported whole, and the shipped value is placed on it. If the
  shipped value is not on the frontier, that is printed, not smoothed over.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from HYPER_EDGE.compat import default_workers, parallel_map
from HYPER_EDGE.microcode import NO_QUORUM, PASS, quorum_state

__all__ = [
    "BOUNDS",
    "GENOME_KEYS",
    "HOLDOUT_MIN_MODEL_FAMILIES",
    "HOLDOUT_MIN_PACKET_CLUSTERS",
    "load_evidence",
    "pin_shares",
    "crosscheck",
    "score",
    "evolve",
    "shipped_score",
    "evaluate_holdout",
    "evaluate_holdout_file",
]

#: name -> (low, high, kind). A Continuous gene is sampled as a float; a Flag as
#: a bool. Bounds are inclusive and validated on every mutation, so a genome can
#: never leave the admissible region by drift.
BOUNDS: Dict[str, Tuple[float, float, str]] = {
    # 0.50 is not a legal quorum: at 0.50 a 3-2-1 split's top share equals the
    # quorum and obtains a verdict, which is the defect this engine exists to
    # make unrepresentable (measured 2026-11-05: selftest's edge probe graded
    # all 7 pins at q=0.50 including the split). The bound is 0.501, not 0.50.
    "quorum": (0.501, 1.00, "continuous"),
    "max_age_days": (0.0, 30.0, "continuous"),
    "truncation_strict": (0.0, 1.0, "flag"),
}
GENOME_KEYS: Tuple[str, ...] = tuple(BOUNDS)


#: Import faults for the adopted judge, kept so a fallback is never silent.
_JUDGE_FAULTS: List[str] = []


def _judge_module():
    """The production judge module, if this tree is importable.

    One import site, so the grade function and the verdict vocabulary the
    held-out validator accepts cannot drift apart.
    """
    tools_dir = Path(__file__).resolve().parents[2] / "tools"
    try:
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        import arena_llm_judge  # type: ignore
        return arena_llm_judge
    except BaseException as fault:  # noqa: BLE001 - absence is a fallback, not a crash
        _JUDGE_FAULTS.append("%s: %s" % (type(fault).__name__, fault))
        return None


def _judge_grade_pin():
    """The judge's own aggregation function, if this tree is importable.

    The engine must not own a second definition of "share". Whoever changes the
    judge's rule changes the engine's fitness by construction, and the selftest
    checks the two agree row-for-row rather than assuming it.
    """
    module = _judge_module()
    return None if module is None else module.grade_pin


def _grade_share(slot: Mapping[str, Any], quorum: Optional[float], grade_pin) -> Optional[str]:
    """Grade an aggregated slot through the production judge, not a shadow rule."""
    if grade_pin is None:
        return None
    # Production's tie-breaking order is VERDICTS order, not first-seen row order.
    verdict_order = ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "BLOCKED")
    rows: List[Dict[str, Any]] = []
    tally = slot.get("tally") or {}
    for grade in verdict_order:
        rows.extend({"grade": grade} for _ in range(int(tally.get(grade, 0))))
    rows.extend({"grade": "BLIND"} for _ in range(int(slot.get("blind", 0))))
    kwargs = {} if quorum is None else {"quorum": quorum}
    return str(grade_pin(rows, **kwargs)["grade"])


def crosscheck(evidence: Mapping[str, Any], shares: Mapping[str, Mapping[str, Any]],
               quorum: Optional[float] = None) -> Dict[str, Any]:
    """Compare tally, availability and final grade against the production judge."""
    grade_pin = _judge_grade_pin()
    if grade_pin is None:
        return {"available": False, "checked": 0, "mismatches": [],
                "faults": list(_JUDGE_FAULTS)}
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in evidence["rows"]:
        grouped.setdefault(row.get("pin") or "?", []).append(row)
    mismatches: List[str] = []
    for pin, rows in sorted(grouped.items()):
        kwargs = {} if quorum is None else {"quorum": quorum}
        expected = grade_pin(rows, **kwargs)
        slot = shares[pin]
        ours = float(slot.get("share") or 0.0)
        theirs = (max(expected["tally"].values()) / expected["votes"]
                  if expected["votes"] else 0.0)
        if abs(ours - theirs) > 1e-9:
            mismatches.append("%s: share engine %.4f vs judge %.4f" % (pin, ours, theirs))
        replay_grade = _grade_share(slot, quorum, grade_pin)
        if replay_grade != expected["grade"]:
            mismatches.append("%s: grade replay %s vs judge %s" %
                               (pin, replay_grade, expected["grade"]))
        if int(slot.get("blind", 0)) != expected["blind"] or int(slot.get("votes", 0)) != expected["votes"]:
            mismatches.append("%s: counts replay %d/%d vs judge %d/%d" %
                               (pin, int(slot.get("votes", 0)), int(slot.get("blind", 0)),
                                expected["votes"], expected["blind"]))
    return {"available": True, "checked": len(grouped), "mismatches": mismatches,
            "faults": list(_JUDGE_FAULTS)}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_evidence(path: str | Path) -> Dict[str, Any]:
    """Read a judge artifact and keep only what scoring needs, plus its digest."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError("evidence not found: %s" % p)
    doc = json.loads(p.read_text(encoding="utf-8"))
    rows = doc.get("rows") or []
    if not rows:
        raise ValueError("evidence holds no rows: %s" % p)
    return {"path": str(p), "sha256": _sha256(p), "meta": doc.get("meta") or {},
            "rows": rows}


def pin_shares(evidence: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per-pin share of the top verdict, excluding BLIND rows from the tally.

    Identical arithmetic to ``arena_llm_judge.grade_pin`` on purpose: if the engine
    aggregated differently, its recommendation would not describe the judge.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for row in evidence["rows"]:
        pin = row.get("pin") or "?"
        slot = out.setdefault(pin, {"tally": {}, "votes": 0, "blind": 0})
        grade = row.get("grade")
        if grade == "BLIND":
            slot["blind"] += 1
            continue
        if grade not in ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "BLOCKED"):
            slot["invalid"] = slot.get("invalid", 0) + 1
            continue
        slot["tally"][grade] = slot["tally"].get(grade, 0) + 1
        slot["votes"] += 1
    for pin, slot in out.items():
        if slot["votes"]:
            top_votes = max(slot["tally"].values())
            slot["top"] = max(slot["tally"], key=lambda k: slot["tally"][k])
            slot["share"] = top_votes / slot["votes"]
        else:
            slot["top"], slot["share"] = None, 0.0
    return out


def _band(shares: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """The quorum interval the corpus itself permits.

    Lower edge: the highest share that must NOT obtain a verdict (a split).
    Upper edge: the lowest share that SHOULD obtain one. Anything in between is a
    choice the evidence cannot make for us, which is exactly why it is a gene.
    """
    low_block = 0.0
    high_ok = 1.0
    low_pin = high_pin = None
    for pin, slot in sorted(shares.items()):
        share = float(slot.get("share") or 0.0)
        if share >= 0.999:
            if high_pin is None or share < high_ok:
                high_ok, high_pin = share, pin
        else:
            if share >= low_block:
                low_block, low_pin = share, pin
    return {"must_not_pass": low_block, "must_not_pass_pin": low_pin,
            "should_pass": high_ok, "should_pass_pin": high_pin,
            "open_interval": [round(low_block, 4), round(high_ok, 4)]}


def score(genome: Mapping[str, float], shares: Mapping[str, Mapping[str, Any]],
          recheck: Optional[Mapping[str, Mapping[str, Any]]] = None) -> Dict[str, Any]:
    """Replay the corpus under one genome. Every number here is computed from rows."""
    quorum = float(genome["quorum"])
    if quorum <= 0.5:
        # The shared rule refuses this quorum outright, so a replay cannot be
        # performed meaningfully. A sampler needs a score, not an exception: the
        # genome is inadmissible and no verdict may be counted for it.
        return {
            "quorum": round(quorum, 4), "verdicts": 0, "pins": len(shares),
            "grades": {}, "grading_available": False,
            "stability": None, "stability_pins": 0, "margin": 0.0,
            "coercions": [], "admissible": False,
            "inadmissible_reasons": ["quorum %.3f cannot exclude a 0.50 split" % quorum],
            "no_verdict": sorted(shares),
        }
    grade_pin = _judge_grade_pin()
    verdicts = 0
    coercions: List[str] = []
    margins: List[float] = []
    obtained: Dict[str, bool] = {}
    grades: Dict[str, Optional[str]] = {}
    for pin, slot in sorted(shares.items()):
        share = float(slot.get("share") or 0.0)
        grade = _grade_share(slot, quorum, grade_pin)
        grades[pin] = grade
        got = grade in ("SUPPORTED", "PARTIAL")
        obtained[pin] = got
        verdicts += 1 if got else 0
        margins.append(abs(share - quorum))
    stability: Optional[float] = None
    comparable = 0
    if recheck is not None:
        agree = 0
        for pin, slot in sorted(shares.items()):
            other = recheck.get(pin)
            if other is None:
                continue
            comparable += 1
            other_grade = _grade_share(other, quorum, grade_pin)
            if other_grade is None or grades[pin] is None:
                continue
            agree += 1 if (other_grade in ("SUPPORTED", "PARTIAL")) == obtained[pin] else 0
        # Denominator is the pins that were actually re-drawn, not the whole
        # corpus: 1 of 7 re-drawn used to print 0.14 for a perfect agreement.
        stability = (agree / comparable) if comparable else None
    # A quorum at or below 0.50 cannot exclude a two-way split, so it is refused
    # as a parameter rather than merely avoided by a strict comparison.
    quorum_can_exclude_split = quorum > 0.5
    reasons: List[str] = []
    if coercions:
        reasons.append("coercion: %s below quorum with a verdict" % ",".join(coercions))
    if not quorum_can_exclude_split:
        reasons.append("quorum %.3f cannot exclude a 0.50 split" % quorum)
    if grade_pin is None:
        reasons.append("production Arena grader unavailable; replay cannot be trusted")
    return {
        "quorum": round(quorum, 4),
        "verdicts": verdicts,
        "pins": len(shares),
        "grades": grades,
        "grading_available": grade_pin is not None,
        "stability": stability,
        "stability_pins": comparable,
        "margin": round(min(margins) if margins else 0.0, 4),
        "coercions": coercions,
        "admissible": quorum_can_exclude_split and grade_pin is not None and not coercions,
        "inadmissible_reasons": reasons,
        "no_verdict": sorted(p for p, got in obtained.items() if not got),
    }


def _dominates(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Pareto dominance over (verdicts, stability, margin); higher is better."""
    def vec(item):
        return (item["verdicts"], item["stability"] or 0.0, item["margin"])
    va, vb = vec(a), vec(b)
    return all(x >= y for x, y in zip(va, vb)) and any(x > y for x, y in zip(va, vb))


def _frontier(results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    admissible = [r for r in results if r["admissible"]]
    front = [r for r in admissible
             if not any(_dominates(other, r) for other in admissible if other is not r)]
    # Deduplicate: independent genomes that land on the same quorum produce
    # identical rows, and five identical rows read as five corroborating votes.
    unique: Dict[float, Dict[str, Any]] = {}
    for item in front:
        unique.setdefault(item["quorum"], item)
    return sorted(unique.values(), key=lambda r: (r["quorum"], r["margin"]))


def _mutate(genome: Dict[str, float], rng: random.Random, scale: float) -> Dict[str, float]:
    child = dict(genome)
    for name, (low, high, kind) in BOUNDS.items():
        if rng.random() > 0.5:
            continue
        if kind == "flag":
            child[name] = 1.0 - float(child[name])
        else:
            step = (high - low) * scale * (rng.random() - 0.5)
            child[name] = min(high, max(low, float(child[name]) + step))
    return child


def _random_genome(rng: random.Random) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for name, (low, high, kind) in BOUNDS.items():
        out[name] = float(rng.randint(0, 1)) if kind == "flag" else rng.uniform(low, high)
    return out


def shipped_score(shares, recheck, shipped: float) -> Dict[str, Any]:
    return score({"quorum": shipped, "max_age_days": 0.0, "truncation_strict": 0.0},
                 shares, recheck)


# --- held-out evaluation -----------------------------------------------------
#
# The corpus the engine evolves on is the corpus it was tuned on, so a frontier
# chosen there cannot answer "does this quorum generalise". This section is the
# only place a generalisation number may come from, and it refuses to produce
# one unless the data can carry it: adjudicated labels, independent model
# families, unique packet clusters, and no packet shared with development.

#: Below these the interval is wider than any decision it could support.
HOLDOUT_MIN_PACKET_CLUSTERS = 20
HOLDOUT_MIN_MODEL_FAMILIES = 3

HOLDOUT_POSITIVE_GRADES = ("SUPPORTED", "PARTIAL")
HOLDOUT_ABSTAIN_GRADES = ("BLIND", "NO_QUORUM")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: Any, field: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()),
             "%s must be a non-empty string" % field)
    return str(value).strip()


def _hex64(value: Any, field: str) -> str:
    text = _text(value, field)
    _require(bool(_HEX64.match(text)),
             "%s must be a 64-character lowercase hex digest" % field)
    return text


def _validate_holdout(doc: Any, verdicts: Sequence[str]) -> Dict[str, Any]:
    """Validate a held-out manifest, or name the first thing that disqualifies it.

    Every check here exists because its absence would let an in-sample number be
    read as a generalisation: a packet that also sat in development, six persona
    prompts from one model counted as six judges, or a label nobody adjudicated.
    """
    _require(isinstance(doc, dict), "manifest must be a JSON object")
    protocol = doc.get("protocol")
    _require(isinstance(protocol, dict), "protocol must be an object")
    _require(protocol.get("partition") == "held_out",
             "protocol.partition must be 'held_out'")
    _require(protocol.get("adjudicated") is True, "protocol.adjudicated must be true")
    adjudicator = _text(protocol.get("adjudicator"), "protocol.adjudicator")
    adjudication = _text(protocol.get("adjudication_protocol"),
                         "protocol.adjudication_protocol")
    _require(protocol.get("independence_attested") is True,
             "protocol.independence_attested must be true")
    basis = _text(protocol.get("independence_basis"), "protocol.independence_basis")

    raw_quorums = protocol.get("preregistered_quorums")
    _require(isinstance(raw_quorums, list) and bool(raw_quorums),
             "protocol.preregistered_quorums must be a non-empty list")
    quorums: List[float] = []
    for value in raw_quorums:
        _require(isinstance(value, (int, float)) and not isinstance(value, bool),
                 "preregistered quorum %r is not a number" % (value,))
        quorum = float(value)
        _require(0.5 < quorum <= 1.0,
                 "preregistered quorum %.4f is outside (0.5, 1.0]" % quorum)
        quorums.append(quorum)
    _require(len(set(quorums)) == len(quorums),
             "protocol.preregistered_quorums contains duplicates")

    raw_dev = protocol.get("development_packet_sha256s")
    _require(isinstance(raw_dev, list),
             "protocol.development_packet_sha256s must be a list")
    development = {_hex64(item, "protocol.development_packet_sha256s[]")
                   for item in raw_dev}

    raw_packets = doc.get("packets")
    _require(isinstance(raw_packets, list) and bool(raw_packets),
             "packets must be a non-empty list")
    seen_packets: set = set()
    seen_hashes: set = set()
    seen_claims: set = set()
    family_of_model: Dict[str, str] = {}
    families: set = set()
    clusters: List[Dict[str, Any]] = []
    for pindex, packet in enumerate(raw_packets):
        where = "packets[%d]" % pindex
        _require(isinstance(packet, dict), "%s must be an object" % where)
        packet_id = _text(packet.get("packet_id"), where + ".packet_id")
        _require(packet_id not in seen_packets,
                 "duplicate packet_id %r" % packet_id)
        seen_packets.add(packet_id)
        digest = _hex64(packet.get("packet_sha256"), where + ".packet_sha256")
        _require(digest not in seen_hashes,
                 "duplicate packet_sha256 %s" % digest)
        _require(digest not in development,
                 "packet %s is listed in development_packet_sha256s; the held-out "
                 "partition is not held out" % packet_id)
        seen_hashes.add(digest)
        raw_claims = packet.get("claims")
        _require(isinstance(raw_claims, list) and bool(raw_claims),
                 where + ".claims must be a non-empty list")
        claims: List[Dict[str, Any]] = []
        for cindex, claim in enumerate(raw_claims):
            cwhere = "%s.claims[%d]" % (where, cindex)
            _require(isinstance(claim, dict), cwhere + " must be an object")
            claim_id = _text(claim.get("claim_id"), cwhere + ".claim_id")
            _require(claim_id not in seen_claims,
                     "duplicate claim_id %r" % claim_id)
            seen_claims.add(claim_id)
            label = claim.get("adjudicated_grade")
            _require(label in verdicts,
                     "%s.adjudicated_grade must be one of %s"
                     % (cwhere, ", ".join(verdicts)))
            raw_votes = claim.get("votes")
            _require(isinstance(raw_votes, list) and bool(raw_votes),
                     cwhere + ".votes must be a non-empty list")
            rows: List[Dict[str, Any]] = []
            claim_families: set = set()
            claim_models: set = set()
            for vindex, vote in enumerate(raw_votes):
                vwhere = "%s.votes[%d]" % (cwhere, vindex)
                _require(isinstance(vote, dict), vwhere + " must be an object")
                grade = vote.get("grade")
                _require(grade in verdicts or grade == "BLIND",
                         "%s.grade must be one of %s or BLIND"
                         % (vwhere, ", ".join(verdicts)))
                model = _text(vote.get("model_id"), vwhere + ".model_id")
                family = _text(vote.get("model_family"), vwhere + ".model_family")
                _require(model not in claim_models,
                         "%s repeats model_id %r inside one claim" % (vwhere, model))
                claim_models.add(model)
                _require(family not in claim_families,
                         "%s repeats model_family %r inside one claim; one family "
                         "is one vote" % (vwhere, family))
                claim_families.add(family)
                known = family_of_model.setdefault(model, family)
                _require(known == family,
                         "model_id %r is declared in two families (%r, %r)"
                         % (model, known, family))
                families.add(family)
                rows.append({"grade": grade})
            claims.append({"claim_id": claim_id, "label": label, "rows": rows})
        clusters.append({"packet_id": packet_id, "packet_sha256": digest,
                         "claims": claims})
    return {
        "protocol": {"partition": "held_out", "adjudicated": True,
                     "adjudicator": adjudicator,
                     "adjudication_protocol": adjudication,
                     "independence_attested": True,
                     "independence_basis": basis,
                     "preregistered_quorums": quorums,
                     "development_packet_sha256s": len(development)},
        "quorums": quorums,
        "clusters": clusters,
        "packet_clusters": len(clusters),
        "model_families": len(families),
        "claims": len(seen_claims),
        "development_hashes": len(development),
    }


def _summarize(outcomes: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Score one set of claim outcomes.

    An abstention (BLIND or NO_QUORUM) is a non-endorsement, so it is *correct*
    on an adjudicated negative and a false negative on an adjudicated positive.
    That is deliberate: a judge that never speaks must not score as accurate.
    """
    total = len(outcomes)
    endorsed = [row for row in outcomes if not row["abstain"]]
    correct = sum(1 for row in outcomes
                  if bool(row["predicted"]) == bool(row["truth"]))
    return {
        "claims": total,
        "endorsed": len(endorsed),
        "abstentions": total - len(endorsed),
        "coverage": (len(endorsed) / total) if total else 0.0,
        "accuracy": (correct / total) if total else 0.0,
        "selective_accuracy": ((sum(1 for row in endorsed if row["truth"])
                                / len(endorsed)) if endorsed else None),
        "false_positives": sum(1 for row in endorsed if not row["truth"]),
        "false_negatives": sum(1 for row in outcomes
                               if row["abstain"] and row["truth"]),
    }


def _percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _bootstrap(per_cluster: Sequence[Sequence[Mapping[str, Any]]],
               samples: int, seed: int) -> Dict[str, Any]:
    """Resample whole packet clusters, never individual claims.

    Claims inside one packet share a source, so resampling claims would treat
    correlated rows as independent evidence and print an interval narrower than
    the data can support. The same seed is reused for every quorum, so the
    candidates are compared on identical resamples.
    """
    rng = random.Random(seed)
    count = len(per_cluster)
    accuracy: List[float] = []
    coverage: List[float] = []
    selective: List[float] = []
    for _ in range(samples):
        drawn = [per_cluster[rng.randrange(count)] for _ in range(count)]
        row = _summarize([outcome for cluster in drawn for outcome in cluster])
        accuracy.append(row["accuracy"])
        coverage.append(row["coverage"])
        if row["selective_accuracy"] is not None:
            selective.append(row["selective_accuracy"])
    return {
        "accuracy_ci95": [_percentile(accuracy, 0.025), _percentile(accuracy, 0.975)],
        "coverage_ci95": [_percentile(coverage, 0.025), _percentile(coverage, 0.975)],
        "selective_accuracy_ci95": ([_percentile(selective, 0.025),
                                     _percentile(selective, 0.975)]
                                    if selective else None),
    }


def evaluate_holdout(doc: Any, bootstrap_samples: int = 1000,
                     seed: int = 20261105) -> Dict[str, Any]:
    """Score preregistered quorums on adjudicated held-out packets.

    Reports only. Nothing here writes a setting: a number that came from one
    corpus is not a licence to change the quorum another corpus runs under.

    ``eligible`` is a readiness flag, not a result. When it is false the
    per-quorum rows are still computed and printed, and none of them may be
    quoted as a generalisation measurement.
    """
    module = _judge_module()
    if module is None:
        raise ValueError("production Arena grader unavailable: %s"
                         % ("; ".join(_JUDGE_FAULTS[-1:]) or "import failed"))
    _require(isinstance(bootstrap_samples, int) and not isinstance(bootstrap_samples, bool)
             and bootstrap_samples >= 1,
             "bootstrap_samples must be a positive integer")
    parsed = _validate_holdout(doc, tuple(module.VERDICTS))
    reasons: List[str] = []
    if parsed["packet_clusters"] < HOLDOUT_MIN_PACKET_CLUSTERS:
        reasons.append("%d unique packet cluster(s); at least %d are required"
                       % (parsed["packet_clusters"], HOLDOUT_MIN_PACKET_CLUSTERS))
    if parsed["model_families"] < HOLDOUT_MIN_MODEL_FAMILIES:
        reasons.append("%d independent model family(ies); at least %d are required"
                       % (parsed["model_families"], HOLDOUT_MIN_MODEL_FAMILIES))
    if not parsed["development_hashes"]:
        reasons.append("protocol.development_packet_sha256s is empty; overlap with "
                       "the development corpus cannot be excluded")
    rows: List[Dict[str, Any]] = []
    for quorum in parsed["quorums"]:
        per_cluster: List[List[Dict[str, Any]]] = []
        for cluster in parsed["clusters"]:
            outcomes: List[Dict[str, Any]] = []
            for claim in cluster["claims"]:
                grade = str(module.grade_pin(claim["rows"], quorum=quorum)["grade"])
                outcomes.append({
                    "claim_id": claim["claim_id"],
                    "grade": grade,
                    "abstain": grade in HOLDOUT_ABSTAIN_GRADES,
                    "predicted": grade in HOLDOUT_POSITIVE_GRADES,
                    "truth": claim["label"] in HOLDOUT_POSITIVE_GRADES,
                })
            per_cluster.append(outcomes)
        summary = _summarize([outcome for cluster in per_cluster for outcome in cluster])
        summary.update(_bootstrap(per_cluster, bootstrap_samples, seed))
        summary["quorum"] = round(quorum, 4)
        rows.append(summary)
    shipped, source = shipped_quorum_from_settings()
    return {
        "protocol": parsed["protocol"],
        "packet_clusters": parsed["packet_clusters"],
        "model_families": parsed["model_families"],
        "claims": parsed["claims"],
        "eligible": not reasons,
        "readiness_reasons": reasons,
        "bootstrap": {"samples": bootstrap_samples, "seed": seed,
                      "unit": "packet_sha256"},
        "quorums": rows,
        "shipped_quorum": shipped,
        "shipped_quorum_source": source,
        "note": ("accuracy counts an abstention as a non-endorsement; "
                 "selective_accuracy is precision among endorsed claims; "
                 "intervals resample whole packet clusters. This command "
                 "reports evaluation and changes no setting."),
    }


def evaluate_holdout_file(path: str | Path, bootstrap_samples: int = 1000,
                          seed: int = 20261105) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError("held-out manifest not found: %s" % p)
    doc = json.loads(p.read_text(encoding="utf-8"))
    result = evaluate_holdout(doc, bootstrap_samples=bootstrap_samples, seed=seed)
    result["manifest"] = str(p)
    result["manifest_sha256"] = _sha256(p)
    return result


def evolve(evidence: Mapping[str, Any],
           recheck: Optional[Mapping[str, Any]] = None,
           generations: int = 8, population: int = 24, seed: int = 20261105,
           shipped_quorum: float = 0.66, workers: Optional[int] = None) -> Dict[str, Any]:
    """Evolve admissibility parameters against recorded rows.

    Deterministic for a given ``seed``: the RNG is seeded once and every ordering
    that matters is sorted, so two runs produce byte-identical artifacts.
    """
    shares = pin_shares(evidence)
    other = pin_shares(recheck) if recheck is not None else None
    band = _band(shares)

    def evaluate(genome):
        return score(genome, shares, other)

    rng = random.Random(seed)
    pool = [_random_genome(rng) for _ in range(max(4, population))]
    history: List[Dict[str, Any]] = []
    for generation in range(max(1, generations)):
        results = parallel_map(evaluate, pool, workers=workers, min_items=4)
        front = _frontier(results)
        best = max(front, key=lambda r: (r["verdicts"], r["stability"] or 0.0,
                                         r["margin"])) if front else None
        history.append({
            "generation": generation,
            "evaluated": len(results),
            "frontier": len(front),
            "best": best,
        })
        survivors = sorted(results, key=lambda r: (r["admissible"], r["verdicts"],
                                                   r["stability"] or 0.0, r["margin"]),
                           reverse=True)[: max(4, population // 2)]
        pool = [dict(zip(GENOME_KEYS, (s["quorum"], 0.0, 0.0))) for s in survivors]
        while len(pool) < max(4, population):
            parent = rng.choice(pool)
            pool.append(_mutate(parent, rng, scale=0.5 / (generation + 1)))

    final = parallel_map(evaluate, pool, workers=workers, min_items=4)
    front = _frontier(final)
    chosen = max(front, key=lambda r: (r["verdicts"], r["stability"] or 0.0,
                                       r["margin"])) if front else None

    inert: List[str] = []
    if chosen is not None:
        for name in GENOME_KEYS:
            if name == "quorum":
                continue
            inert.append("%s (no row in this corpus constrains it)" % name)

    return {
        "engine": {"name": "HYPER_EDGE", "version": "0.1.0rc1",
                   "isa": "HYPER_EDGE-ISA-12", "seed": seed,
                   "generations": generations, "population": population,
                   "workers": workers or default_workers(),
                   "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
        "evidence": {"path": evidence["path"], "sha256": evidence["sha256"],
                     "calls": evidence["meta"].get("calls"),
                     "round": evidence["meta"].get("round"),
                     "recheck": (recheck["path"] if recheck else None),
                     "recheck_sha256": (recheck["sha256"] if recheck else None)},
        "band": band,
        "shares": {pin: {"share": round(float(s.get("share") or 0.0), 4),
                         "tally": s["tally"], "blind": s["blind"]}
                   for pin, s in sorted(shares.items())},
        "history": history,
        "frontier": front,
        "chosen": chosen,
        "shipped": shipped_score(shares, other, shipped_quorum),
        "shipped_quorum": shipped_quorum,
        "inert_genes": inert,
        "overlay_proposal": ({"arena": {"quorum": chosen["quorum"]}} if chosen else {}),
        "note": ("Frontier entries are equal-evidence choices; 'chosen' is the "
                 "maximin member (largest margin to both edges of the band). An "
                 "inert gene is reported with its value and is NOT claimed tuned."),
    }


def shipped_quorum_from_settings(fallback: float = 0.66) -> Tuple[float, str]:
    """(value, source) for the quorum actually in force on this host.

    A constant here would make the engine compare a proposal against a number no
    running judge uses. Reads the same surface every other tool reads (HED-5),
    and degrades to the shipped default if `tools/` is not importable.
    """
    tools_dir = Path(__file__).resolve().parents[2] / "tools"
    try:
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        import defaults_profile  # type: ignore
        value = float(defaults_profile.read_default("arena.quorum", fallback))
        return value, "defaults_profile(%s)" % defaults_profile.environment()
    except Exception as fault:  # noqa: BLE001 - a settings read must never abort a run
        _JUDGE_FAULTS.append("settings: %s" % type(fault).__name__)
        return fallback, "literal fallback: %s" % type(fault).__name__


def _fmt(result: Mapping[str, Any]) -> str:
    if not result:
        return "none"
    stability = ("n/a" if result["stability"] is None
                 else "%.2f on %d" % (result["stability"], result.get("stability_pins", 0)))
    return ("q=%.4f verdicts=%d/%d stability=%s margin=%.3f%s" % (
        result["quorum"], result["verdicts"], result["pins"], stability,
        result["margin"], "" if result["admissible"] else " INADMISSIBLE"))


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="evolve admissibility parameters on recorded rows")
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--recheck", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--generations", type=int, default=8)
    ap.add_argument("--population", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--shipped-quorum", type=float, default=None,
                    help="quorum currently in force (default: read arena.quorum "
                         "from config/ through defaults_profile)")
    ap.add_argument("--apply", default="", metavar="ENV",
                    help="write the chosen overlay to config/defaults.<ENV>.json "
                         "instead of changing the shipped default")
    args = ap.parse_args(argv)

    if args.shipped_quorum is None:
        args.shipped_quorum, source = shipped_quorum_from_settings()
        print("shipped quorum in force: %.4f via %s" % (args.shipped_quorum, source))

    evidence = load_evidence(args.evidence)
    recheck = load_evidence(args.recheck) if args.recheck else None
    result = evolve(evidence, recheck, generations=args.generations,
                    population=args.population, seed=args.seed,
                    shipped_quorum=args.shipped_quorum)
    print("evidence: %s (calls=%s round=%s)" % (
        Path(args.evidence).name, result["evidence"]["calls"], result["evidence"]["round"]))
    print("band: quorum must be > %.2f (%s must not pass) and <= %.2f (%s must pass)" % (
        result["band"]["must_not_pass"], result["band"]["must_not_pass_pin"],
        result["band"]["should_pass"], result["band"]["should_pass_pin"]))
    for step in result["history"]:
        print("  gen %2d  evaluated=%2d frontier=%2d  best: %s" % (
            step["generation"], step["evaluated"], step["frontier"], _fmt(step["best"])))
    print("frontier (%d):" % len(result["frontier"]))
    for item in result["frontier"][:8]:
        print("   %s" % _fmt(item))
    if len(result["frontier"]) > 8:
        print("   ... %d more equal-evidence choices" % (len(result["frontier"]) - 8))
    print("shipped %.2f -> %s" % (result["shipped_quorum"], _fmt(result["shipped"])))
    print("chosen -> %s" % _fmt(result["chosen"]))
    print("overlay proposal: %s" % json.dumps(result["overlay_proposal"]))
    if result["inert_genes"]:
        print("inert genes (carried, not tuned): %s" % "; ".join(result["inert_genes"]))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        print("artifact: %s" % args.out)
    if args.apply and result["chosen"]:
        # The engine never edits the shipped defaults: a corpus-derived number
        # belongs in the layer that names its environment, so an adopter opts in
        # with AZAZAEL_ENV=<env> and the change is visible and revertible.
        target = Path("config") / ("defaults.%s.json" % args.apply)
        overlay: Dict[str, Any] = {}
        if target.exists():
            overlay = json.loads(target.read_text(encoding="utf-8"))
        arena = overlay.setdefault("arena", {})
        arena["quorum"] = result["chosen"]["quorum"]
        overlay.setdefault("_provenance", {})["arena.quorum"] = (
            "HYPER_EDGE evolve seed=%s evidence=%s sha=%s" % (
                result["engine"]["seed"],
                Path(result["evidence"]["path"]).name,
                result["evidence"]["sha256"][:12]))
        target.write_text(json.dumps(overlay, indent=2) + "\n", encoding="utf-8")
        print("applied overlay: %s (read only when AZAZAEL_ENV=%s)" % (target, args.apply))
    return 0 if result["chosen"] else 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
