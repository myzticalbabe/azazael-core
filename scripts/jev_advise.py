"""Ask Jev which unobserved component to exercise next, and grade the answer.

THE DECISION THIS EXISTS FOR (measured 2026-09-21)
--------------------------------------------------
    components registered          232
    components with ANY evidence    25
    components never observed      209

Fitness can only select on what has been executed, so 209 organs contribute
nothing to evolution and nothing to fitness. Choosing among them is a
finite-choice decision with a checkable outcome, which is the one shape Jev
answers well - and the outcome tap grades it afterwards.

WHAT THIS SCRIPT DOES NOT DO
----------------------------
It does not execute anything and it does not rewire the dispatch path. It asks,
records the answer under a `decision_id`, and prints it. Two reasons:

1. Wiring an unexercised external service into a hot path before its first
   successful live call is how you get a wire that looks connected and carries
   nothing - the failure this session has already found three times.
2. `decide_execution_order` runs on every dispatch and must never block on a
   network call.

`--grade` is the independent check Jev's own documentation requires: it joins
its answers to measured outcomes and reports accuracy. Until something executes
a chosen organ, accuracy is `None` and the ungraded count is reported - an
advisor whose advice was never acted on has no accuracy, and neither 0% nor
100% would be honest.

USAGE
-----
    python scripts/jev_advise.py                 # ask, and record the decision
    python scripts/jev_advise.py --limit 12      # candidate count (bounded)
    python scripts/jev_advise.py --json          # machine-readable answer
    python scripts/jev_advise.py --grade         # independent check on past answers

EXIT CODES
----------
    0  answered, or correctly fell back (fail-open is not an error)
    2  bad arguments
    1  the answer could not be recorded (the store rejected the write)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.WORMS.expansion.jev_advisor import (  # noqa: E402
    DEFAULT_CANDIDATE_LIMIT,
    JevAdvisor,
    exploration_candidates,
    grade_choices,
)

GOAL = ("Reduce uncertainty about the fleet. Choose the single component whose "
        "execution would produce the most NEW information about this system.")

RULES = (
    "Choose exactly one offered component.",
    "Prefer the component whose behaviour is least predictable from what is "
    "already known; a component already measured cannot remove an unknown.",
    "A component that other components depend on reveals more than one that "
    "nothing uses.",
    "Do not invent a component: choose only an offered name.",
)


def _observed_and_registry():
    """The live registry and the set of organs that have any evidence."""
    from CORE.worm_registry import WORM_REGISTRY, discover_worms
    discover_worms()
    from CORE.WORMS.expansion.spawn_credit import live_spawn_credit
    credit = live_spawn_credit()
    return WORM_REGISTRY, set(getattr(credit, "rewards", {}) or {}), credit


def _new_decision_id() -> str:
    """Reuse the dispatch path's own id so one decision is described once."""
    try:
        from CORE.WORMS.expansion.spawn_credit import new_decision_id
        return new_decision_id()
    except Exception:
        import uuid
        return f"jev-{uuid.uuid4().hex[:12]}"


def ask(limit: int, as_json: bool) -> int:
    registry, observed, _credit = _observed_and_registry()
    candidates = exploration_candidates(registry, observed, limit=limit)
    print(f"registry={len(registry)} observed={len(observed)} "
          f"never_observed={len(registry) - len(observed)} "
          f"candidates={len(candidates)}", file=sys.stderr)

    advisor = JevAdvisor()
    if not advisor.available:
        # Fail-open, and SAY SO. The system proceeds without an answer, but the
        # operator learns why rather than seeing an empty result.
        #
        # `advisor.enabled` is False BOTH when the switch is off and when no
        # key exists, so reading the cause off it reported "the switch is off"
        # for a missing key - a diagnostic that sends the operator to the wrong
        # variable. Read the two causes apart.
        switched_off = os.environ.get("AZAZAEL_JEV_ENABLED", "").strip().lower() \
            in {"0", "false", "no", "off"}
        if switched_off:
            reason = "AZAZAEL_JEV_ENABLED is off (kill switch)"
        elif not os.environ.get("TYPESAFE_API_KEY"):
            reason = "TYPESAFE_API_KEY is unset or empty"
        else:
            reason = "the advisor is disabled"
        print(f"Jev unavailable ({reason}); the fleet's non-Jev path is "
              f"unaffected.", file=sys.stderr)
        print("To enable: export TYPESAFE_API_KEY=... "
              "(see https://docs.typesafe.ai/ )", file=sys.stderr)

    decision_id = _new_decision_id()
    answer = advisor.choose(
        question="next_component_to_exercise",
        goal=GOAL,
        candidates=candidates,
        instructions=RULES,
        context={
            "components_registered": len(registry),
            "components_with_evidence": len(observed),
            "components_never_observed": len(registry) - len(observed),
        },
        decision_id=decision_id,
    )

    if as_json:
        payload = {
            "decision_id": decision_id,
            "available": answer is not None,
            "choice": answer.choice if answer else None,
            "confidence": answer.confidence if answer else None,
            "probabilities": dict(answer.probabilities) if answer else None,
            "model": answer.model if answer else None,
            "latency_ms": answer.latency_ms if answer else None,
            "candidates": list(candidates),
        }
        print(json.dumps(payload, indent=2))
        return 0

    if answer is None:
        print("no answer - the non-Jev path runs (see the recorded reason)",
              file=sys.stderr)
        return 0

    print(f"\nchoice     : {answer.choice}")
    print(f"confidence : {answer.confidence:.4f}")
    print(f"model      : {answer.model} | latency_ms: {answer.latency_ms}")
    print(f"decision_id: {decision_id}")
    print("distribution:")
    for name, p in sorted(answer.probabilities.items(), key=lambda kv: -kv[1]):
        mark = "*" if name == answer.choice else " "
        print(f"  {mark} {name:<28} {p:.3f}")
    print(f"\ncriterion  : {candidates.get(answer.choice, '')[:160]}")
    return 0


def grade() -> int:
    report = grade_choices()
    print(f"decisions recorded : {report['decisions']}")
    print(f"answered           : {report['answered']}")
    print(f"graded             : {report['graded']}")
    print(f"correct            : {report['correct']}")
    acc = report["accuracy"]
    print(f"accuracy           : {'n/a' if acc is None else f'{acc:.1%}'}")
    if report["ungraded"]:
        print(f"ungraded           : {report['ungraded']} "
              f"(advice not yet acted on - not a failure)")
    if report["fallbacks"]:
        print(f"fallbacks          : {report['fallbacks']}")
    if report["choices"]:
        print("choices made:")
        for name, count in sorted(report["choices"].items(), key=lambda kv: -kv[1]):
            print(f"  {name:<28} {count}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=DEFAULT_CANDIDATE_LIMIT,
                    help=f"max candidates to offer (default {DEFAULT_CANDIDATE_LIMIT})")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--grade", action="store_true",
                    help="independent check: grade recorded answers against outcomes")
    args = ap.parse_args(argv)
    if args.limit < 2:
        print("--limit must be at least 2 (a one-candidate question is not a "
              "decision)", file=sys.stderr)
        return 2
    return grade() if args.grade else ask(args.limit, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
