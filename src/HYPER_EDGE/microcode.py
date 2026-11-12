"""The admissibility ISA -- twelve ops, one per measurement this tree paid for.

Each op is a *predicate over a reading*, not a rule in prose. A reading is a flat
mapping; an op reads only the keys it declares and returns ``(state, reason)``
where state is one of:

``PASS``       the op's question was answered in the affirmative
``LEAD``       the reading is real but cannot carry a verdict (queue it)
``CUT``        the reading is hostile to admissibility and must not be used
``BLIND``      the instrument could not answer -- not the same as a zero
``NO_QUORUM``  the finders split below quorum -- the absence of a verdict
``SKIP``       the op does not apply to this reading (and says why)

Every op also carries ``legacy``: what the pre-RC code did. That column is the
backward-compatibility contract -- ``evaluate(..., legacy=True)`` reproduces the
old behaviour exactly, so a caller can be migrated by *proving the difference on
real rows* instead of trusting a changelog. Two of the twelve legacy behaviours
are defective on purpose (``0x05`` coerces a split into PARTIAL, ``0x0C`` treats a
disabled gate as a pass); they are kept because replaying them is how the defect is
demonstrated.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from HYPER_EDGE import kernel

__all__ = ["DEFAULT_POLICY", "ISA", "LADDER", "Op", "POLICY", "POLICY_CHOICES",
           "POLICY_FAULTS", "evaluate", "isa_table", "load_policy", "policy_path",
           "quorum_state", "rung", "set_policy"]

PASS = "PASS"
LEAD = "LEAD"
CUT = "CUT"
BLIND = "BLIND"
NO_QUORUM = "NO_QUORUM"
SKIP = "SKIP"

#: Declared policy knobs -- the only surface a patch may move. Each op whose answer
#: depends on a judgement call (what to do when a value is *absent*) exposes it by
#: name instead of hard-coding one answer, so "the rule is wrong" becomes a data
#: change with a replay proof rather than an edit to a predicate body.
DEFAULT_POLICY: Dict[str, Dict[str, str]] = {
    "0x09": {"undated": "skip"},
    "0x0A": {"undrawn": "skip"},
}

#: Legal values per knob, ordered least to most severe. A proposer tries them in
#: this order and stops at the first that fixes a failing case without regressing a
#: passing one -- so the least destructive repair wins by construction.
POLICY_CHOICES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "0x09": {"undated": ("skip", "blind", "cut")},
    "0x0A": {"undrawn": ("skip", "blind", "lead")},
}

#: Loading faults from the persisted policy (unknown op, unknown knob, illegal
#: value). Never silently dropped: the selftest prints them and fails on them.
POLICY_FAULTS: List[str] = []

#: The policy in force. Seeded from DEFAULT_POLICY, then overlaid by
#: ``config/microcode.json`` when that file exists -- a patch that does not survive
#: a process restart is not a repair, it is a mood.
POLICY: Dict[str, Dict[str, str]] = {code: dict(knobs) for code, knobs in DEFAULT_POLICY.items()}


_ENV_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def policy_path(env: Optional[str] = None) -> Path:
    """``<project>/config/microcode[.<env>].json``, resolved from this file.

    One file per environment, exactly like the settings overlay: a policy proved
    against one environment's corpus must not silently govern another. The env
    token is validated because it becomes part of a path.
    """
    root = Path(__file__).resolve().parents[2] / "config"
    if env is None:
        return root / "microcode.json"
    if not _ENV_TOKEN.match(str(env)):
        raise ValueError("illegal environment token %r" % (env,))
    return root / ("microcode.%s.json" % env)


def set_policy(policy: Mapping[str, Mapping[str, Any]]) -> List[str]:
    """Validate and install ``policy`` in place. Returns the faults it refused.

    Unknown ops, unknown knobs and illegal values are refused by name; a refused
    knob keeps its previous value, because a typo must not silently disable a gate.
    """
    faults: List[str] = []
    for code, knobs in policy.items():
        if code not in POLICY_CHOICES:
            faults.append("unknown op %r in policy" % (code,))
            continue
        if not isinstance(knobs, Mapping):
            faults.append("policy[%s] is not a mapping" % code)
            continue
        for knob, value in knobs.items():
            choices = POLICY_CHOICES[code].get(knob)
            if choices is None:
                faults.append("unknown knob %s.%s" % (code, knob))
                continue
            if value not in choices:
                faults.append("%s.%s=%r is not one of %s" % (code, knob, value, list(choices)))
                continue
            POLICY.setdefault(code, {})[knob] = value
    return faults


def load_policy(path: Optional[Path] = None, env: Optional[str] = None) -> List[str]:
    """Overlay ``config/microcode.json`` onto the shipped defaults.

    A missing file is normal (nothing has been patched yet) and is not a fault. A
    present-but-unreadable file IS a fault: it means someone believes a patch is
    in force when it is not.
    """
    target = Path(path) if path is not None else policy_path(env)
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ["%s unreadable: %s" % (target.name, exc)]
    if not isinstance(data, Mapping):
        return ["%s is not a JSON object" % target.name]
    return set_policy(data.get("policy") or {})


#: Applied at import: a persisted patch is in force in every process that imports
#: the ISA, which is what makes it a repair rather than a session-local edit. The
#: environment selects the overlay, so ``AZAZAEL_ENV=rc`` reads the policy proved
#: against the rc corpus and nothing else.
POLICY_FAULTS.extend(load_policy(env=os.environ.get("AZAZAEL_ENV") or None))

#: the verdict ladder: exclusive rungs, most severe first. A reading sits on one.
LADDER: Tuple[str, ...] = ("CUT", "BLIND", "NO_QUORUM", "LEAD", "PARTIAL", "VERIFIED")


@dataclass(frozen=True)
class Op:
    code: str
    name: str
    question: str
    keys: Tuple[str, ...]
    legacy: str
    fn: Callable[[Mapping[str, Any], Mapping[str, Any]], Tuple[str, str]]


def _present(record: Mapping[str, Any], key: str) -> bool:
    value = record.get(key)
    return value is not None and value != ""


def _tier(record, ctx):
    tier = str(record.get("tier") or "").upper()
    if tier in ("A", "B"):
        return PASS, "tier %s" % tier
    if tier == "C":
        return LEAD, "tier C corroborates an A or B; it cannot carry a fact alone"
    if tier == "D":
        return LEAD, "tier D is a pointer: queue it, never cite it"
    return CUT, "no tier declared: an untiered source cannot be weighed"


def _ground(record, ctx):
    if "quote_grounded" not in record:
        return SKIP, "no quotation exhibited"
    if record.get("quote_grounded") is True:
        return PASS, "quotation found in its cited capture"
    return CUT, "quotation not found in its source: no evidence, not weak evidence"


def _denom(record, ctx):
    if not record.get("population_claim"):
        return SKIP, "not a population claim"
    n, m = record.get("n"), record.get("m")
    if isinstance(n, int) and isinstance(m, int) and m > 0:
        return PASS, "%d/%d carry a denominator" % (n, m)
    return CUT, "population verdict with no usable denominator"


def _universe(record, ctx):
    if not record.get("negative"):
        return SKIP, "not a negative"
    universe = record.get("universe")
    if isinstance(universe, (list, tuple)) and len(universe) > 0:
        return PASS, "negative over %d named source(s)" % len(universe)
    return CUT, "universe-free negative: this is 'we did not search', not 'none exists'"


def quorum_state(share: float, quorum: float, legacy: bool = False) -> Tuple[str, str]:
    """The 0x05 decision, isolated so the engine and the judge share one rule.

    The precondition lives in the shared rule, not only in the engine's band: at a
    quorum of exactly 0.50 a 3-2-1 split's top share meets the threshold, so the
    split obtains a verdict. Refusing the quorum here means no caller can reach
    the comparison with a threshold that cannot exclude a split.
    """
    if float(quorum) <= 0.5:
        raise ValueError("quorum %.4f cannot exclude a 0.50 split; it must be > 0.50"
                         % float(quorum))
    if share >= quorum:
        return PASS, "share %.2f >= quorum %.2f" % (share, quorum)
    if legacy:
        # pre-RC: any PARTIAL in the tally was reported as a PARTIAL grade, with
        # the words "no quorum" printed beside it. A reader keeps the grade.
        return PASS, "legacy: split reported as PARTIAL (no quorum, %.2f)" % share
    return NO_QUORUM, "share %.2f < quorum %.2f: no verdict was obtained" % (share, quorum)


def _quorum(record, ctx):
    share = record.get("share")
    if share is None:
        return SKIP, "single finder, no share to aggregate"
    return quorum_state(float(share), float(ctx["quorum"]), bool(ctx["legacy"]))


def _blind(record, ctx):
    if record.get("instrument_answered") is False:
        return BLIND, "the instrument could not answer"
    return PASS, "instrument answered"


def _trunc(record, ctx):
    klass = str(record.get("truncation_class") or "").lower()
    if not klass:
        return SKIP, "no truncation reported on this path"
    if klass == "silent" and record.get("verdict_adjacent"):
        if ctx["legacy"]:
            return PASS, "legacy: silent cut in a verdict path went unclassified"
        return CUT, "silent cut feeding a verdict: the verdict is contaminated"
    return PASS, "%s cut is admissible on this path" % klass


def _provenance(record, ctx):
    command, digest = record.get("command"), record.get("digest")
    if command and digest:
        artifact, artifact_id = record.get("artifact"), record.get("artifact_id")
        if artifact and artifact_id:
            # A receipt is bound to an ARTEFACT, not to bytes. A regenerated file
            # with identical content is a new artefact, and only the kernel can
            # say so: sha256 equal, FILE_ID_INFO different. Measured 2026-11-05
            # (`HYPER_EDGE.kernel prove`: re-issued artefact -> ids differ, sha
            # equal). Without a kernel answer the binding is unverified, which is
            # BLIND, never a quiet PASS.
            identity = kernel.file_identity(artifact)
            if identity is None:
                return BLIND, ("kernel identity unavailable: this receipt cannot be "
                               "bound to the artefact it names")
            if identity["id"] != str(artifact_id):
                return CUT, ("artefact re-issued: kernel identity %s is not the %s this "
                             "receipt was proved against because identical bytes cannot "
                             "answer it" % (identity["id"], artifact_id))
            return PASS, "command + digest + kernel identity %s" % identity["id"]
        return PASS, "command + digest present"
    if ctx["legacy"]:
        return PASS, "legacy: a claim without a command was accepted"
    return CUT, "claim with no command/digest: indistinguishable from a recollection"


def _stale(record, ctx):
    age = record.get("age_days")
    if age is None:
        undated = str((ctx.get("policy") or {}).get("0x09", {}).get("undated", "skip"))
        if undated == "blind":
            return BLIND, "no age declared: freshness is unanswerable (policy 0x09.undated=blind)"
        if undated == "cut":
            return CUT, "no age declared and the policy cuts undated artifacts (0x09.undated=cut)"
        return SKIP, "no age declared"
    limit = float(ctx.get("max_age_days") or 0)
    if limit <= 0:
        return PASS, "staleness gate off (0): recorded, not enforced"
    if float(age) <= limit:
        return PASS, "%.1f day(s) old, limit %.1f" % (float(age), limit)
    return CUT, "artifact is %.1f day(s) old; it answers for a tree it no longer describes" % float(age)


def _repro(record, ctx):
    draws = record.get("draws")
    if not draws:
        undrawn = str((ctx.get("policy") or {}).get("0x0A", {}).get("undrawn", "skip"))
        if undrawn == "blind":
            return BLIND, "no re-draw recorded: reproducibility unanswerable (policy 0x0A.undrawn=blind)"
        if undrawn == "lead":
            return LEAD, "no re-draw recorded and the policy queues the grade (0x0A.undrawn=lead)"
        return SKIP, "no re-draw recorded"
    if record.get("reproduced") is True:
        try:
            count = int(draws)
        except (TypeError, ValueError) as exc:
            raise ValueError("draws must be a count, got %r" % (draws,)) from exc
        return PASS, "grade reproduced across %d draw(s)" % count
    return LEAD, "grade did not reproduce: quote the draws, not a verdict"


def _knob(record, ctx):
    knob = record.get("knob")
    if not knob:
        return SKIP, "no setting involved"
    readers = record.get("knob_readers")
    if isinstance(readers, int) and readers > 0:
        return PASS, "%s read by %d consumer(s)" % (knob, readers)
    return CUT, "%s is an orphan knob: declared, read by nothing" % knob


def _disabled(record, ctx):
    if "enabled" not in record:
        return SKIP, "not a gate state"
    if record.get("enabled") is True:
        return PASS, "gate enabled"
    if ctx["legacy"]:
        return PASS, "legacy: a disabled gate reported success"
    return BLIND, "gate disabled by config: exit 2, never a pass"


ISA: Tuple[Op, ...] = (
    Op("0x01", "TIER", "what weight can this source bear?", ("tier",),
       "any source was weighed the same", _tier),
    Op("0x02", "GROUND", "is the exhibited quotation in its source?", ("quote_grounded",),
       "a quotation was trusted if it looked plausible", _ground),
    Op("0x03", "DENOM", "does a population verdict carry N of M?", ("population_claim", "n", "m"),
       "a verdict was accepted with no count", _denom),
    Op("0x04", "UNIVERSE", "does a negative name what it searched?", ("negative", "universe"),
       "'not found' meant 'not found anywhere'", _universe),
    Op("0x05", "QUORUM", "did the finders reach the quorum?", ("share",),
       "a split was reported as PARTIAL with 'no quorum' beside it", _quorum),
    Op("0x06", "BLIND", "could the instrument answer at all?", ("instrument_answered",),
       "a blocked provider was reported as 0 results", _blind),
    Op("0x07", "TRUNC", "was a value silently cut on a verdict path?", ("truncation_class",),
       "every truncation looked like formatting", _trunc),
    Op("0x08", "PROVENANCE", "does the claim carry its command, digest and artefact?",
       ("command", "digest", "artifact", "artifact_id"),
       "a claim without a command was accepted", _provenance),
    Op("0x09", "STALE", "is the artifact newer than what it describes?", ("age_days",),
       "a stale artifact answered for a tree that had moved", _stale),
    Op("0x0A", "REPRO", "did the grade survive a re-draw?", ("draws", "reproduced"),
       "one draw was quoted as a finding", _repro),
    Op("0x0B", "KNOB", "is the setting read by anything?", ("knob", "knob_readers"),
       "orphan knobs and bypass readers were invisible", _knob),
    Op("0x0C", "DISABLED", "is the gate actually on?", ("enabled",),
       "a disabled gate reported success", _disabled),
)

_ORDER: Tuple[str, ...] = ("0x08", "0x09", "0x02", "0x01", "0x03", "0x04",
                           "0x07", "0x0B", "0x0C", "0x06", "0x0A", "0x05")


#: The highest evidence grade each op can reach. ``kernel`` means the op reads a
#: facility userspace cannot substitute for (artefact identity, per-thread CPU);
#: ``record`` means it grades only what the caller wrote down. A grade is not a
#: rank of importance -- it says who answered the question, so a reader can tell a
#: measured receipt from a transcribed one without opening the op.
EVIDENCE_GRADE: Dict[str, str] = {
    "0x01": "record", "0x02": "record", "0x03": "record", "0x04": "record",
    "0x05": "record", "0x06": "record", "0x07": "record", "0x08": "kernel",
    "0x09": "record", "0x0A": "record", "0x0B": "record", "0x0C": "record",
}


def grades() -> List[Dict[str, str]]:
    """Every op with the evidence grade it can reach, and whether it is graded.

    An op missing from the table reads ``ungraded`` rather than inheriting a
    default: a new op nobody graded must be visible, or the table silently becomes
    a comment.
    """
    return [{"code": op.code, "name": op.name,
             "grade": EVIDENCE_GRADE.get(op.code, "ungraded")} for op in ISA]


def isa_table() -> List[Dict[str, str]]:
    """The ISA as rows: code, name, question, legacy behaviour."""
    return [{"code": op.code, "name": op.name, "question": op.question,
             "legacy": op.legacy} for op in ISA]


def rung(states: Mapping[str, str]) -> str:
    """Collapse op states into exactly one ladder rung."""
    if states.get("0x02") == CUT or states.get("0x03") == CUT or states.get("0x04") == CUT:
        return CUT
    if states.get("0x08") == CUT or states.get("0x09") == CUT:
        return CUT
    if states.get("0x07") == CUT or states.get("0x0B") == CUT:
        return CUT
    if states.get("0x06") == BLIND or states.get("0x0C") == BLIND:
        return BLIND
    if states.get("0x05") == NO_QUORUM:
        return NO_QUORUM
    if states.get("0x01") == LEAD or states.get("0x0A") == LEAD:
        return LEAD
    if any(state == CUT for state in states.values()):
        return CUT
    if any(state == NO_QUORUM for state in states.values()):
        return NO_QUORUM
    if any(state == LEAD for state in states.values()):
        return LEAD
    return "VERIFIED"


def evaluate(record: Mapping[str, Any], quorum: float = 0.66,
             legacy: bool = False, max_age_days: float = 0.0,
             policy: Optional[Mapping[str, Mapping[str, str]]] = None) -> Dict[str, Any]:
    """Run the ISA over one reading and return the rung plus every op's reason.

    ``legacy=True`` reproduces the pre-RC behaviour for every op, including the
    two defective ones -- this is the migration instrument, not a mode to ship.
    ``policy`` overrides the knobs in force for this call only, which is how the
    patch engine replays a candidate without touching the live policy.
    """
    ctx: Dict[str, Any] = {"quorum": float(quorum), "legacy": bool(legacy),
                           "max_age_days": float(max_age_days),
                           "policy": policy if policy is not None else POLICY}
    by_code = {op.code: op for op in ISA}
    states: Dict[str, str] = {}
    reasons: Dict[str, str] = {}
    faults: Dict[str, str] = {}
    for code in _ORDER:
        op = by_code[code]
        try:
            state, reason = op.fn(record, ctx)
        except (TypeError, ValueError, KeyError, AttributeError, ZeroDivisionError) as exc:
            # A reading the instrument cannot type is a refusal, never a crash.
            # Measured 2026-11-05: a dreamed record with "draws": [1, 2] and
            # reproduced=true raised TypeError out of int(), and the whole CLI
            # died -- an adversary could stop the instrument by malforming one
            # field.  The fault is named here and the rung becomes BLIND.
            state = BLIND
            reason = "the reading could not be graded: %s: %s" % (type(exc).__name__, exc)
            faults[code] = reason
        states[code] = state
        reasons[code] = "%s %s: %s" % (op.code, op.name, reason)
    grade = rung(states)
    if faults and grade != CUT:
        grade = BLIND
    return {"rung": grade, "states": states, "reasons": reasons, "faults": faults,
            "quorum": ctx["quorum"], "legacy": ctx["legacy"],
            "policy": {code: dict(knobs) for code, knobs in ctx["policy"].items()},
            "isa": "%s-%d" % ("HYPER_EDGE-ISA", len(ISA))}


def summarize(result: Mapping[str, Any]) -> str:
    """One line: the rung and the ops that produced it (non-PASS/SKIP only)."""
    notable = [text for code, text in sorted(result["reasons"].items())
               if result["states"][code] not in (PASS, SKIP)]
    return "%s | %s" % (result["rung"], "; ".join(notable) or "every op passed")


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="print the admissibility ISA")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    import json
    rows = isa_table()
    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        for row in rows:
            print("%s %-11s %s" % (row["code"], row["name"], row["question"]))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
