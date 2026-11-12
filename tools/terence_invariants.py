#!/usr/bin/env python3
# coding: utf-8
"""terence_invariants.py — the re-engineered TERENCE: fix by verified invariant.

WHY THIS REPLACES THE OLD SWEEP
-------------------------------
`TERENCE.py` scans SOURCE for patterns and reported 8,009 "fixes" on a tree
measured at **0 compile failures across 1,668 files and 0 real import
failures**. Two of its three largest-volume fixes were destructive as fixes:
"comment out print statement" (2,876 sites) and "comment out continue/break
outside loop" (895 sites). `pattern_scores` returned 0 entries, because
nothing had ever applied a fix through it -- the learning loop had no data and
no caller.

So the signal was wrong. This module replaces a pattern sweep with ORACLES:
things that go wrong, observed, each with an exact predicate saying whether a
proposed repair worked.

THE RULE
--------
    No verifier, no fix.

A pattern with no checkable predicate is a preference, not a repair, and a
preference does not belong in an error ledger. "Replace magic numbers with
constants" cannot be verified -- nothing in the program's behaviour says the
extraction helped -- so it is not offered as a fix here, at any volume.

WHAT THE OLD SWEEP COULD NOT SEE
---------------------------------
Every defect actually found in this system on 2026-09-29 was an INVARIANT
violation, invisible to any AST pattern:

  a filter matching positionally after the data moved depth   (BIBL parts[:2])
  a metric counting its own output as evidence               (PASS 1.0 at 0.00 rows)
  a gate starving because a pin moves faster than evidence   (0 promotions/33)
  an objective arm capped at zero by a missing table row     (alert: critical)
  a search space rewritten between two measurements          (100% alternation)
  an instrument reporting healthy because it read nothing     (empty != clean)

All of them are detectable -- by asserting an expectation against measured
state. None is a syntax pattern. That is why the old sweep found 8,009
nonsense and none of the real faults.

Usage:
    python tools/terence_invariants.py            # run oracles, report
    python tools/terence_invariants.py --json
    python tools/terence_invariants.py --repair   # apply only verified remedies
Exit: 0 no faults / 1 faults present / 2 oracle could not run
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import re
import re as _re
import tempfile
import shutil
import collections

AZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
WORKSPACE = os.environ.get(
    "PMB_WORKSPACE_DIR", r"C:\Users\clint\.pmb\workspaces\04a151a73354")
ERROR_DB = os.path.join(WORKSPACE, "events.sqlite")
LEDGER = os.path.join(AZ, "state", "terence_invariants.json")


# ── fault + oracle model ──────────────────────────────────────────────────
class Fault(dict):
    """A real failure, with the evidence and (optionally) its repair.

    `verifier` is a callable that returns True only if the world is now
    correct. Without one, `repair` is None and the fault can only be reported.
    """

    def __init__(self, fault_id, klass, subject, detail, severity,
                 remedy=None, verifier=None):
        # `remedy` and `verifier` MUST be in the dict, not just attributes:
        # the ledger and the printed report read them back out of the dict, and
        # an earlier version of this file stored them only as attributes -- so
        # two faults that WERE verified-and-repaired printed "remedy: NONE" and
        # were written to the ledger as unremediable. Correct computation, false
        # record, which is the same "computed and thrown away" family in a new
        # place: the truth has to survive the trip into the artifact.
        super().__init__(fault_id=fault_id, klass=klass, subject=subject,
                         detail=detail, severity=severity,
                         remedy=remedy or None,
                         verifiable=bool(remedy and verifier))
        self.remedy = remedy
        self.verifier = verifier


def _run(args, timeout=900):
    p = subprocess.run([PY] + args, cwd=AZ, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


# ── oracles ───────────────────────────────────────────────────────────────
def oracle_evolution_progress():
    """Is the loop improving, not merely running?"""
    try:
        rc, out, _ = _run(["tools/evolution_progress.py", "--json"])
    except Exception as e:
        return None, f"oracle unavailable: {type(e).__name__}: {e}"
    try:
        d = json.loads(out)
    except Exception:
        return None, "oracle produced non-JSON"
    # CURRENT state only. The first version reported over the WHOLE log, so a
    # condition that has already been fixed kept reporting as DEGRADED forever
    # -- the stale-artifact failure, inside the tool meant to catch it. The
    # recent window is what the loop is doing now; history is a separate
    # question and is labelled as such.
    recent = dict(d)
    cycles = d.get("cycles", 0)
    if cycles > 12:
        recent = dict(d, verdict="HISTORICAL", current=d.get("verdict"),
                      cycles_overall=cycles, window=12)
    faults = []
    if d.get("verdict") == "DEGRADED":
        seen_keys = set()
        for f in d.get("findings", []):
            key = f.split(":")[0].strip().lower()[:24]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            faults.append(Fault(
                fault_id="evo:" + key, klass="evolution_progress",
                subject="promotion gate", detail=f[:200], severity="high"))
    if d.get("promotions", 1) == 0 and d.get("cycles", 0) >= 6:
        faults.append(Fault(
            fault_id="evo:no-promotion", klass="evolution_progress",
            subject="promotion gate",
            detail=f"0 promotions in {d.get('cycles')} cycles; evidence is "
                   f"not accumulating",
            severity="high",
            remedy="replay the gate over stored rows to see whether evidence "
                   "exists (tools/evolution_backfill.py)",
            verifier=lambda: _backfill_promotable()))
    return faults, None


def _backfill_promotable():
    """Verifier: does the real gate find promotable evidence in real history?"""
    try:
        rc, out, _ = _run(["tools/evolution_backfill.py", "--json"])
        d = json.loads(out)
        return bool(d.get("any_promotable"))
    except Exception:
        return False


def oracle_fitness_arms():
    """Is every objective arm actually contributing?"""
    try:
        rc, out, _ = _run(["tools/fitness_arm_liveness.py", "--json"])
        d = json.loads(out)
    except Exception as e:
        return None, f"oracle unavailable: {type(e).__name__}: {e}"
    if d.get("verdict") == "ALL_ARMS_LIVE":
        return [], None
    faults = [Fault(fault_id="arms:" + f.split()[0].strip("`"),
                    klass="fitness_arms", subject="objective arm",
                    detail=f[:200], severity="high")
              for f in d.get("findings", [])[:3]]
    return faults, None


def oracle_lesson_liveness():
    """Are surfaced lessons being acted on? Measured independently of the agent."""
    try:
        rc, out, _ = _run(["tools/lesson_liveness.py", "--json"])
        d = json.loads(out)
    except Exception as e:
        return None, f"oracle unavailable: {type(e).__name__}: {e}"
    if d.get("status") == "NO_DATA":
        return [], None                      # cannot judge; not a fault
    faults = []
    if d.get("status") == "FAIL":
        faults.append(Fault(
            fault_id="lessons:follow-through", klass="lesson_liveness",
            subject="meta-evolution loop",
            detail=f"action rate {d.get('effective_action_rate')} of "
                   f"{d.get('surfaces_total')} surfaces; "
                   f"explicit mark rate {d.get('explicit_mark_rate')}",
            severity="medium"))
    dg = (d.get("diagnostics") or {}).get("error_log_rows")
    if dg == 0:
        faults.append(Fault(
            fault_id="lessons:error-log-empty", klass="lesson_liveness",
            subject="error ledger",
            detail="error_log has 0 rows: the SOP's designated home has never "
                   "been written, so RULE 0I is 0% followed",
            severity="medium",
            remedy="write scans into error_log (tools/terence_watch.py)",
            verifier=lambda: _error_log_rows() > 0))
    return faults, None


def _error_log_rows():
    if not os.path.exists(ERROR_DB):
        return -1
    con = sqlite3.connect(f"file:{ERROR_DB}?mode=ro", uri=True, timeout=15)
    try:
        r = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='error_log'").fetchone()
        return con.execute("SELECT COUNT(*) FROM error_log").fetchone()[0] if r else -1
    except Exception:
        return -1
    finally:
        con.close()


def oracle_pool_shape():
    """Is each training pool big enough to be a control problem?

    A pool with one distinct state is a bandit: no transition, no horizon, no
    credit to assign. It cannot distinguish a good learner from a bad one, and
    every arm lands near the ceiling -- so it reports a result while measuring
    nothing. This is the defect class that produced the `REPLAY_WINS +0.0708`
    reading on the 7-record alert pool.
    """
    sys.path.insert(0, os.path.join(AZ, "src"))
    sys.path.insert(0, os.path.join(AZ, "scripts"))
    try:
        import evolution_cycle as EC
        from CORE.local_memory import get_memory_store
        faults = []
        for et in ("db_health_alert",):
            try:
                rows = get_memory_store().query_memories(event_type=et, limit=500) or []
            except Exception:
                continue
            recs = []
            for r in rows:
                d = r.get("data", r) if isinstance(r, dict) else r
                if isinstance(d, str):
                    try:
                        d = json.loads(d)
                    except Exception:
                        continue
                if isinstance(d, dict):
                    recs.append(d)
            if not recs:
                continue
            states = len({EC._state_key_for(r) for r in recs})
            if states <= 1:
                faults.append(Fault(
                    fault_id=f"pool:degenerate:{et}",
                    klass="pool_shape", subject=et,
                    detail=f"{len(recs)} rows but {states} distinct state(s): "
                           f"a 1-state bandit, so no learner comparison on this "
                           f"arm can mean anything",
                    severity="high",
                    remedy="select the pool by severity, not event_type "
                           "(EV-6 in evolution_cycle._extract_alert_pool)",
                    verifier=lambda et=et: _pool_states(et) > 1))
        return faults, None
    except Exception as e:
        return None, f"oracle unavailable: {type(e).__name__}: {e}"


def _pool_states(event_type):
    sys.path.insert(0, os.path.join(AZ, "src"))
    sys.path.insert(0, os.path.join(AZ, "scripts"))
    try:
        import evolution_cycle as EC
        return len({EC._state_key_for(r) for r in EC._extract_alert_pool(limit=3000)})
    except Exception:
        return 0


# ── the four new oracles ───────────────────────────────────────────────────
SENTINEL = os.path.join(AZ, "src", "SCRIPTS", "sentinel_gate.py")
MANDATES = os.path.join(AZ, "MANDATES.md")
GITHOOKS = os.path.join(AZ, ".githooks")
WORKFLOWS = os.path.join(AZ, ".github", "workflows")

# Probes must be TYPE-APPROPRIATE, not plausible-looking strings.
#
# First draft of this oracle passed prose probes like "no gemini" and reported
# validate_rule_0g/0l/0m/0i as unfailable -- four FALSE CRITICALS, on the same
# day the whole exercise was about instruments reporting confidently on nothing.
# All four were my probe's fault:
#   0G's real False path needs {"spawned": True, "citation": ""}; the string
#      "no gemini" contains "no " and lands in its N/A branch.
#   0L and 0M gate their False paths behind a dict input or a real audit trail.
#   0I returns UNVERIFIABLE with no audit trail, which is CORRECT behaviour --
#      evidence-dependent, not unfailable.
#
# So each probe is (data, audit_path) and the oracle distinguishes two very
# different defects:
#   unfailable   -- no input at all makes it False. Critical.
#   unverifiable -- it can only fail when evidence is present, and the evidence
#                  is absent. Informational, and a coverage gap, not a defect.
# ── audit-trail fixtures ───────────────────────────────────────────────────
# An EMPTY audit is not enough to exercise RULE 0I: the rule needs a BATCH of
# >=3 file_edit entries, and `edits < 3` is a legitimate pass. So the 0I probe
# requires a trail that actually contains the violation it is meant to detect.
# Without this the oracle reported validate_rule_0i as unfailable -- the probe's
# fault again, for the third time, which is why the fixture is explicit.
_EMPTY_AUDIT = os.path.join(AZ, "state", "terence_probe_empty_audit.jsonl")
_BATCH_AUDIT = os.path.join(AZ, "state", "terence_probe_batch_audit.jsonl")


def _write_audit(path, entries):
    """Minimal but REAL audit lines: the reader matches on the `type` field, and
    a fabricated shape would be testing the fixture rather than the validator.

    Routed through sentinel_enforcer.permit_here: the global audit hook denies
    ungated writes to state/, so the first version of this could not create its
    own fixtures and the runtime oracle reported itself UNRUNNABLE.
    """
    if os.path.exists(path):
        return path
    body = []
    prev = "0" * 64
    for i, etype in enumerate(entries):
        body.append(json.dumps({
            "audit_trail_version": "3.0.0", "hash": "0" * 64,
            "prev_hash": prev, "session_file": os.path.basename(path),
            "session_uuid": "probe", "ts": "2026-09-30T00:00:%02d" % i,
            "type": etype, "uuid": "probe-%04d" % i}) + "\n")
        prev = "0" * 64
    text = "".join(body)
    try:
        sys.path.insert(0, os.path.join(AZ, "src"))
        from CORE.sentinel_enforcer import permit_here
        with permit_here("terence_invariants: write probe fixture"):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        return path
    except Exception:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return path
        except Exception:
            return None


def _ensure_empty_audit():
    """A real, valid, EMPTY audit trail. 'No entries' is the evidence several
    probes need; inventing a missing file instead would just move the failure."""
    return _write_audit(_EMPTY_AUDIT, [])


def _ensure_batch_audit():
    """3 file_edit entries and NO inference_spawn -- the exact shape RULE 0I
    forbids: a batch of edits with no Gemini analysis in front of them."""
    return _write_audit(_BATCH_AUDIT, ["file_edit", "file_edit", "file_edit"])


# validator -> list of (label, data, audit_path) that SHOULD each yield False
_PROBES = {
    "validate_rule_0b_a": [
        ("acceptance language after a clean five-item block",
         "1. named deliverable yes 2. longer than no 3. claiming acceptance no "
         "4. reshape no 5. disagree yes. This passes.", None),
    ],
    "validate_rule_0q_a": [
        ("claims 'Yes' with no inference-cache filepath", "Yes verified.", None),
    ],
    "validate_rule_0g": [
        ("spawned but no citation", {"spawned": True, "citation": ""}, None),
        ("empty response", "", None),
        ("claims Yes with a citation absent from the audit",
         "Yes, .audit/fake.jsonl:1, .inference_cache/fake.log", _EMPTY_AUDIT),
    ],
    "validate_rule_0l": [
        ("test claimed, audit has no test_run", "Yes", _EMPTY_AUDIT),
        ("test claimed, no test_run entry",
         {"test_ran": True}, _EMPTY_AUDIT),
    ],
    "validate_rule_0m": [
        ("empty response", "", None),
        ("'Yes' naming no agent", "Yes", None),
        ("'Yes' naming agents, audit shows <2 spawns",
         "Yes, gemini and web", _EMPTY_AUDIT),
    ],
    "validate_rule_0i": [
        ("batch of 3 file_edit with 0 inference_spawn",
         "batch script", _BATCH_AUDIT),
    ],
    "validate_ap1": [
        # AP-1 is labelled INFORMATIONAL and CAN return False. The first probe
        # here said "rather than asked", and the substring "asked" satisfies its
        # confirm-condition -- so the probe passed a check that is fine.
        ("empty AP-1", "", None),
        ("explicit denial", "no", None),
    ],
}


def _load_sentinel():
    import importlib.util
    spec = importlib.util.spec_from_file_location("terence_sentinel", SENTINEL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["terence_sentinel"] = mod
    spec.loader.exec_module(mod)
    return mod


def oracle_unfailable_validators():
    """runtime. A validator that cannot return False is a green light with
    nothing behind it. Probed with type-appropriate inputs, not read as source
    text: a source read can be fooled by a False in an unreachable branch, and a
    prose probe can land in a validator's N/A branch and accuse a healthy check.

    Two distinct defects are reported separately, because conflating them is how
    this oracle produced four false CRITICALS on its first run:
      unfailable    -- no probe at all yields False. critical.
      unverifiable  -- every probe yields False ONLY when evidence is missing,
                       which is a coverage gap, not a defect. informational.
    """
    faults = []
    try:
        sg = _load_sentinel()
    except Exception as e:
        return None, "oracle unavailable: %s: %s" % (type(e).__name__, e)

    audit = _ensure_empty_audit()
    batch_audit = _ensure_batch_audit()
    if audit is None or batch_audit is None:
        return None, ("oracle needs its audit fixtures; could not write to %s. "
                      "Without real trails the evidence-dependent validators "
                      "would all look unfailable, which is how this oracle "
                      "produced false CRITICALs." % os.path.dirname(_EMPTY_AUDIT))
    probed = 0
    needs_evidence = []
    for name, cases in _PROBES.items():
        fn = getattr(sg, name, None)
        if not callable(fn):
            continue
        probed += 1
        fails_without_evidence = False
        fails_with_evidence = False
        for label, data, audit_path in cases:
            path = None
            if audit_path == _EMPTY_AUDIT:
                path = audit
            elif audit_path == _BATCH_AUDIT:
                path = batch_audit
            try:
                res = fn(data, path)
            except Exception:
                continue
            passed = res[0] if isinstance(res, tuple) else res
            if passed is False:
                if path:
                    fails_with_evidence = True
                else:
                    fails_without_evidence = True
        if not (fails_without_evidence or fails_with_evidence):
            faults.append(Fault(
                "unfailable_validator:%s" % name, "unfailable_validator",
                "%s::%s" % (SENTINEL, name),
                "no probe made this validator return False, with or without an "
                "audit trail. Cases tried: %s. A rule with teeth in its text "
                "and none in its code."
                % "; ".join(c[0] for c in cases),
                "critical",
                remedy="extract the check to a function and return False on "
                       "the violation predicate",
                verifier=None))
        elif not fails_without_evidence:
            # Falsifiable ONLY with evidence supplied. A coverage gap in the
            # evidence, not a broken check. Previously reported for 0B-A and
            # 0Q-A, both of which are falsifiable on plain text alone -- the
            # flag tracked "did a probe WITH a path fail" instead of "can it
            # fail without evidence", which is a different question.
            needs_evidence.append(name)

    if not probed:
        return None, ("no probeable validators found - sentinel_gate.py shape "
                      "changed, or _PROBES needs updating")
    if needs_evidence:
        faults.append(Fault(
            "validator_needs_evidence:%s" % ",".join(sorted(needs_evidence)),
            "unverifiable_validator", SENTINEL,
            "these validators can be falsified only when an audit trail is "
            "supplied; they always pass when called with evidence absent: %s. "
            "That is a COVERAGE GAP in the evidence, not a broken check, and is "
            "reported as informational so it cannot be mistaken for the "
            "critical case above." % ", ".join(sorted(needs_evidence)),
            "low", remedy=None, verifier=None))
    return faults, None


def _rule_headers():
    with open(MANDATES, encoding="utf-8") as fh:
        text = fh.read()
    out = []
    for m in re.finditer(r"^##+\s*RULE\s+([0-9A-Z\-]+):\s*(.+)$", text, re.M):
        rid = m.group(1).strip()
        title = m.group(2)
        # strip the trailing severity/date parenthetical so a rule is compared
        # on what it mandates, not on when it was last edited
        title = re.sub(r"\s*\((?:CRITICAL|[^)]*20\d\d)[^)]*\)\s*$", "", title)
        norm = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        out.append((rid, title.strip(), norm))
    return out


# Duplicate detection: exact-title only, on measured evidence.
#
# The first version used Jaccard over title tokens at 0.60 and found nothing.
# Lowering it does not work either, and the measurement is worth keeping because
# it is a real negative result over 741 rule pairs:
#
#     0.455  0E/0E-1     true duplicate
#     0.308  0J/0S       true duplicate
#     0.308  0C/0J       NOT a duplicate
#     0.300  0Q-A/0T-B   NOT a duplicate
#     0.300  0G/0L       NOT a duplicate
#
# There is no threshold that separates them: 0J and 0S genuinely share "pre
# action gate assertion", and so do 0C and 0J by coincidence of domain
# vocabulary. Tuning the cut to catch the two known answers would be fitting the
# instrument to its own test set, which is worse than not shipping it. So the
# automated check is EXACT-TITLE only, semantic duplication goes to the coverage
# registry as UNCOVERED, and the two human-confirmed pairs are declared here so
# the information is not lost with the code.
CONFIRMED_DUPLICATE_PAIRS = [
    ("0E", "0E-1", "both mandate the 10-phase multi-tier Gemini protocol"),
    ("0J", "0S", "both are the pre-action assertion block/banner"),
]


def oracle_duplicate_rules():
    """source. EXACT duplicate rule titles only. Semantic duplication is
    declared, not detected -- see CONFIRMED_DUPLICATE_PAIRS and the coverage
    registry. `no verifier, no fix` applied to this oracle itself: I could not
    build a sound detector, so I did not ship one that looks like one."""
    try:
        rows = _rule_headers()
    except Exception as e:
        return None, "oracle unavailable: %s: %s" % (type(e).__name__, e)
    if not rows:
        return None, "no RULE headers parsed from MANDATES.md"

    faults, seen = [], {}
    for rid, title, norm in rows:
        if norm in seen:
            faults.append(Fault(
                "duplicate_rule_exact:%s~%s" % (seen[norm], rid),
                "duplicate_canon_rule", MANDATES,
                "RULE %s and RULE %s have an IDENTICAL title: %r"
                % (seen[norm], rid, title[:80]),
                "high", remedy=None, verifier=None))
        else:
            seen[norm] = rid
    return faults, None


# Every name that means "this file actually uses the gate". A caller reaches the
# gate by importing the module, by instantiating the validator, or by calling a
# validate_* entry point -- and the capability test proved the first version,
# which searched only for the literal `sentinel_gate`, could NOT see the
# class-based path. So a detector that missed `SentinelValidator()` would have
# been entitled to assert the gate is never invoked, on the strength of a
# detector that could not recognise half of how the gate is invoked.
GATE_NEEDLES = ("sentinel_gate", "SentinelValidator", "validate_rule_",
                "validate_ap1", "validate_decision")


def _invokes_gate(path, needles=None):
    """Does this file actually INVOKE the gate, or merely MENTION it?

    Measured 2026-09-30: the first version grepped for the string and counted
    src/CORE/compliance/pre_action_gate.py as a caller, because its DOCSTRING
    says "Designed for integration with azazael/src/SCRIPTS/sentinel_gate.py".
    That file never imports or runs it, and its own only real callers are
    scratch scripts under logs/gate_tmp/. So the oracle reported NO FAULT while
    the finding it exists to detect was true -- a docstring consumed as if it
    were the integration, which is the same output-not-consumed law one level
    down.

    So: parse with ast and require a real reference. An import, a Name/Attribute
    node, or a string literal that is an ARGUMENT to a call. Module, class and
    function docstrings are bare expression statements and are excluded by
    construction, because they are not Names.
    """
    import ast
    needles = tuple(needles or GATE_NEEDLES)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            tree = ast.parse(fh.read())
    except Exception:
        return False, "unparseable"
    hits = []

    def has(text):
        return any(n in (text or "") for n in needles)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", "") or ""
            names = [a.name for a in node.names]
            if has(mod) or any(has(n) for n in names):
                hits.append("import")
        elif isinstance(node, ast.Name) and has(node.id):
            hits.append("name")
        elif isinstance(node, ast.Attribute) and has(node.attr):
            hits.append("attribute")
        elif isinstance(node, ast.Call):
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and has(arg.value):
                    hits.append("call-arg")
    return bool(hits), ",".join(sorted(set(hits)))


def oracle_gate_timing():
    """wiring. A PRE-ACTION gate whose only trigger points run after the work is
    a post-action gate wearing the name.

    The check is about the TOOL PATH, not filenames. A git `pre-commit` hook
    matches a "pre-" prefix and is still post-work for the session that made the
    edits, so a prefix check false-passed on 2026-09-30. And a file that merely
    NAMES the gate in a docstring is not a caller, so the reference test is
    ast-based rather than a substring match.
    """
    import ast  # noqa: F401  (imported for the walk above)
    faults = []
    referrers = []
    # `SENTINEL` is a FILE (`src/SCRIPTS/sentinel_gate.py`, :275), so testing it
    # with `os.path.isdir` was False on every run and the scan below never
    # executed: `referrers` stayed empty and the fault was appended
    # UNCONDITIONALLY. Measured 2026-10-19 -- the critical "no file invokes the
    # gate" finding could not be falsified by any tree, so the oracle was
    # reporting its own dead branch as a finding, and the negative control
    # (which tests `_invokes_gate` directly) passed while the oracle stayed
    # broken. tests/test_terence_gate_timing.py pins the difference: a fixture
    # WITH a real caller must be silent.
    try:
        if os.path.isfile(SENTINEL):
            for root in (os.path.join(AZ, "scripts"), os.path.join(AZ, "src")):
                if not os.path.isdir(root):
                    continue
                for dirpath, dirs, files in os.walk(root):
                    dirs[:] = [d for d in dirs
                               if d not in ("__pycache__", "node_modules",
                                            "archive", "archived_unified")]
                    for fn in files:
                        if not fn.endswith(".py"):
                            continue
                        p = os.path.join(dirpath, fn)
                        if os.path.normpath(p) == os.path.normpath(SENTINEL):
                            continue
                        ok, _ = _invokes_gate(p)
                        if ok:
                            referrers.append(os.path.relpath(p, AZ))
    except Exception as e:
        return None, "oracle unavailable: %s: %s" % (type(e).__name__, e)

    if not referrers:
        faults.append(Fault(
            "gate_fires_only_after_work", "gate_fires_only_after_work",
            SENTINEL,
            "no file in scripts/ or src/ INVOKES the pre-action gate (checked "
            "with ast, so a docstring that merely names it does not count). The "
            "only trigger points are git hooks and CI, which run after the "
            "session's edits. So during a session RULE 0B-A/0G/0H/0I/0L/0M/0Q-A "
            "are not being checked at all: they passed on 2026-09-30 not "
            "because they were satisfied but because nobody ran the gate. RULE "
            "0J/0S call it permanent and unskippable, which is not what is wired.",
            "critical",
            remedy=None,          # wiring an agent tool path is not a patch
            verifier=None))
    return faults, None


def _validator_rule_ids():
    """Which rule ids the gate can actually reach, read from its own source.

    The leading ZERO IS KEPT. A first version captured group 1 as the digits
    after the 0 ("G", "H", "A") and compared them against ids that keep it
    ("0G", "0H"), so the match was empty and the coverage oracle reported
    "39 of 39 rules have NO validator" -- including 0B-A, which was hooked and
    tested hours earlier. An oracle that cannot see the thing it was built to
    measure is worse than no oracle: it looks busy and reports a total.
    """
    try:
        with open(SENTINEL, encoding="utf-8") as fh:
            src = fh.read()
    except Exception:
        return set()
    # match either "RULE 0Q-A" or the validate_rule_0q_a / rule_0q_post_phase
    # spellings, and normalise everything to the id as written in canon.
    ids = set()
    for m in re.finditer(r"RULE\s+(0[0-9A-Z]+(?:-[A-Z])?)", src):
        ids.add(m.group(1).upper())
    for m in re.finditer(r"validate_rule_(0[0-9a-z]+)", src):
        ids.add(m.group(1).upper())
    for m in re.finditer(r"rule_(0[0-9a-z]+)_(?:post_phase|batch|test|"
                         r"concurrent|gemini|sync|scope|edict)", src):
        ids.add(m.group(1).upper())
    return ids


def oracle_rule_coverage():
    """coverage. A CRITICAL rule with no validator referencing it. This is the
    28-rule prose-only layer measured rather than remembered -- the size of the
    job, from an instrument rather than from my judgement."""
    faults = []
    try:
        rows = _rule_headers()
    except Exception as e:
        return None, "oracle unavailable: %s: %s" % (type(e).__name__, e)
    try:
        covered = _validator_rule_ids()
    except Exception:
        covered = set()

    prose = []
    for rid, title, _ in rows:
        base = rid.split("-")[0]
        if base in covered or rid in covered:
            continue
        prose.append(rid)

    if prose:
        faults.append(Fault(
            "critical_rule_unenforced", "critical_rule_unenforced", MANDATES,
            "%d of %d rules have no validator: %s. A rule nothing can stop is a "
            "rule that costs reading and buys nothing -- and the ones missing "
            "here include 0A, 0C, 0D, 0E, 0E-1, 0F, 0J, 0J-A, 0K, 0N, 0P, 0U, "
            "0V, 0W, 0Z-B, 0Z-C, 0Z-F, 0Z-G, 0Z-I, most of which were violated on "
            "2026-09-30.\n"
            "METHOD CAVEAT, because a tidy number for a partial measurement is "
            "the exact failure this oracle exists to catch: 'covered' here means "
            "the rule ID appears in sentinel_gate.py, which is a PROXY. A rule "
            "whose ID is mentioned but whose check is still unfailable, or is "
            "only reachable on a code path nothing invokes, counts as covered. "
            "The runtime oracle measures the unfailable case; the wiring oracle "
            "measures the unreachable case. Read this count as a lower bound on "
            "the problem, not the size of it."
            % (len(prose), len(rows), ", ".join(prose)),
            "high",
            remedy=None,          # enforce-or-retire is a DECISION
            verifier=None))
    return faults, None


# ── the three "produced but not consumed" classes ─────────────────────────
# 2026-09-30, from the user: "THINGS CRASH. DICT ARE NOT READ. NO CALLERS,
# NOTHING PICKS UP CORRECTLY. THESE ARE ALL ERRORS WE CONTINOUSLY HAVE."
#
# They are ONE error with three faces, and the reason they recur rather than get
# fixed is that they are all DETECTABLE and none of them is DETECTED. Building
# the detector is most of the cure; repairing individual instances is the rest.

STATE_DIR = os.path.join(AZ, "state")
DUE = os.path.join(STATE_DIR, "chain_controller_due.json")


def oracle_silent_crashes():
    """artifact. A job that ran and FAILED, read from the scheduler's own record.

    Live evidence at the time of writing: artifacts rc=1, spine rc=1, tensor
    rc=3, train rc=1 -- all with empty error strings, and `tensor` recorded with
    ok=true despite a non-zero return. tools/audit_loop_liveness.py checks for
    SILENCE, so a job that runs and fails reads as alive.

    The verdict disagreement is reported as its own fault, because a non-zero
    return recorded as success means the scheduler's own success test and the
    job's exit code disagree, and nothing else will ever surface it.
    """
    faults = []
    try:
        with open(DUE, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, "no %s -- chain controller has not written a due file" % DUE
    except Exception as e:
        return None, "oracle unavailable: %s: %s" % (type(e).__name__, e)

    # The by-design skip table. A nonzero exit with ok=true is the DESIGNED
    # ENCODING OF AN HONEST SKIP, not a disagreement and not a crash:
    #     _SKIP_RC = {"train": (1,), "tensor": (3,)}
    #     if result.returncode in _SKIP_RC.get(name, ()): row["ok"] = True
    # The first version of this oracle knew nothing about that, reported
    # train/tensor as `critical`, and I was one step from a remedy that would
    # have turned every honest skip into a reported failure. The negative
    # control in _control_no_false_crash is what makes that impossible now.
    skip = _load_chain_skip_rc()

    jobs = data.get("jobs") if isinstance(data.get("jobs"), dict) else data
    if not isinstance(jobs, dict) or not jobs:
        return None, "due file has no recognisable job map (keys: %s)" % list(data.keys())[:6]
    records = {k: v for k, v in jobs.items()
               if isinstance(v, dict) and any(
                   f in v for f in ("returncode", "rc", "ok", "last", "last_run"))}
    if not records:
        return None, ("no job records in the due file -- %d top-level keys, "
                      "none carrying a returncode/ok/last field" % len(jobs))

    for name, rec in records.items():
        if not isinstance(rec, dict):
            continue
        rc = rec.get("returncode", rec.get("rc"))
        ok = rec.get("ok")
        if rc is None:
            continue
        if rc in skip.get(name, ()):
            # configured honest skip -- by design, not a fault
            continue
        if rc != 0:
            errs = rec.get("errors") or rec.get("error") or []
            silent = not errs
            faults.append(Fault(
                "silent_crash:%s" % name, "silent_crash",
                DUE,
                "job %r exited %s%s. last=%s. Not in _SKIP_RC %s, so this is "
                "not a configured honest skip."
                % (name, rc, " with NO error output" if silent else "",
                   rec.get("last"), skip or "(none declared)"),
                "high",
                remedy=None,   # why it crashed is a diagnosis, not a patch
                verifier=None))
        if rc != 0 and ok is True:
            faults.append(Fault(
                "verdict_disagreement:%s" % name, "verdict_disagreement",
                DUE,
                "job %r returned %s but was recorded ok=true, and %s is NOT a "
                "configured skip, so ok was not derived from the exit code. The "
                "scheduler's success test and the job's exit code disagree."
                % (name, rc, name),
                "critical",
                remedy=None,
                verifier=None))
    return faults, None


def _corpus_text():
    """All Python and shell text in the tree, for reference counting. Read once
    because this is the hot path for two oracles."""
    blobs = []
    for root in ("src", "scripts", "tools", "tests"):
        base = os.path.join(AZ, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs
                       if d not in ("__pycache__", "node_modules", "archive",
                                    "archived_unified", ".audit")]
            for fn in files:
                if not fn.endswith((".py", ".bat", ".sh", ".ps1")):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        blobs.append((os.path.relpath(p, AZ), fh.read()))
                except Exception:
                    continue
    return blobs


def _scheduler_targets():
    """Basenames the chain controller is configured to run. A CLI script's
    caller is the SCHEDULER, not an import -- so a file with no Python importer
    is not an orphan if the plan names it. Without this every CLI entry point
    reads as an orphan and the oracle is pure noise."""
    names = set()
    for j in ("chain_controller_plan.json", "chain_controller_due.json"):
        p = os.path.join(STATE_DIR, j)
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        for rec in (data.get("jobs") or {}).values():
            if isinstance(rec, dict):
                t = rec.get("target")
                if t:
                    names.add(os.path.basename(str(t)))
    return names


def oracle_unread_artifacts():
    """wiring. A state file that is WRITTEN every cycle and read by nobody.

    This is the purest instance of the law: the dict is produced, correctly,
    and nothing consumes it. Detection is sound -- zero references to the
    basename anywhere in the source tree -- so unlike semantic duplicate
    detection this one has no false-positive problem. What is NOT decided here
    is whether an unread artifact SHOULD be read; that is a judgement, so the
    fault is reported and ranked, not repaired.
    """
    faults = []
    if not os.path.isdir(STATE_DIR):
        return None, "no %s" % STATE_DIR
    try:
        blobs = _corpus_text()
    except Exception as e:
        return None, "oracle unavailable: %s: %s" % (type(e).__name__, e)

    try:
        entries = [e for e in os.scandir(STATE_DIR)
                   if e.is_file() and e.name.endswith((".json", ".jsonl"))
                   and e.stat().st_size > 0]
    except Exception as e:
        return None, "oracle unavailable: %s: %s" % (type(e).__name__, e)

    for e in sorted(entries, key=lambda x: -x.stat().st_mtime):
        stem = os.path.splitext(e.name)[0]
        refs = sum(1 for _rel, txt in blobs
                   if e.name in txt or ("\"%s\"" % stem) in txt
                   or ("'%s'" % stem) in txt)
        if refs == 0:
            faults.append(Fault(
                "unread_artifact:%s" % e.name, "unread_artifact", e.path,
                "written to state/ and referenced by NO source file, script, or "
                "test (%d bytes, mtime %s). Produced every cycle, consumed by "
                "nothing." % (e.stat().st_size,
                              time.strftime("%Y-%m-%d %H:%M",
                                            time.localtime(e.stat().st_mtime))),
                "medium",
                remedy=None,   # whether it SHOULD be read is a judgement
                verifier=None))
    return faults, None


def oracle_orphan_mechanisms():
    """wiring. A mechanism with no caller: no importer, no scheduler entry, no
    shell reference. `terence_watch.py` is the canonical case -- it was written
    specifically because TERENCE had no caller, and nothing called terence_watch
    either.

    A CLI script's caller is the scheduler, so scheduler targets are excluded
    before the orphan test. Without that exclusion every entry point in the tree
    is a false positive and the oracle is unusable.
    """
    faults = []
    scheduled = _scheduler_targets()
    try:
        blobs = _corpus_text()
    except Exception as e:
        return None, "oracle unavailable: %s: %s" % (type(e).__name__, e)

    for root in ("tools", "scripts"):
        base = os.path.join(AZ, root)
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            if not fn.endswith(".py") or fn.startswith("__"):
                continue
            p = os.path.join(base, fn)
            if not os.path.isfile(p):
                continue
            if fn in scheduled:
                continue
            stem = os.path.splitext(fn)[0]
            refs = 0
            for rel, txt in blobs:
                if os.path.normpath(rel) == os.path.normpath(
                        os.path.join(root, fn)):
                    continue
                # an import, a subprocess target, or a module dunder
                if ("import %s" % stem) in txt or ("from %s" % stem) in txt \
                        or (fn in txt) or ('"%s"' % stem) in txt:
                    refs += 1
            if refs == 0:
                faults.append(Fault(
                    "orphan_mechanism:%s" % fn, "orphan_mechanism", p,
                    "%s/%s has no importer, no scheduler entry, and no shell "
                    "reference. Written and never invoked." % (root, fn),
                    "medium",
                    remedy=None,   # wire it or delete it is the user's call
                    verifier=None))
    return faults, None


# ── HOOK: negative-control validation of the oracles themselves ────────────
# Added 2026-09-30 after the central error of the day. TERENCE reported
# `verdict_disagreement` as CRITICAL for `train rc=1 ok=true` and
# `tensor rc=3 ok=true`, and I was one step from applying a remedy that would
# have rewritten every by-design skip into a reported failure.
#
# The facts: chain_controller.py declares
#     _SKIP_RC = {"train": (1,), "tensor": (3,)}
# and run_one() does
#     if result.returncode in _SKIP_RC.get(name, ()):
#         row["ok"] = True; row["skipped"] = True
#     else:
#         row["ok"] = result.returncode == 0
# so a nonzero exit with ok=true is the DESIGNED ENCODING OF AN HONEST SKIP.
# My oracle tested `rc != 0 and ok is True` without knowing _SKIP_RC exists.
#
# The general defect is not that oracle. It is that TERENCE had NO SELF-VALIDATION:
# an oracle that has never been shown to stay quiet on a known-healthy case is
# trusted the moment it fires. Every wrong verdict today shared that shape --
#   unfailable_validators x4  fired on healthy validators
#   gate_timing               stayed SILENT on a real finding (the inverse error)
#   rule_coverage             reported "39 of 39" for an instrument that could
#                              not see any of them
#   verdict_disagreement      fired on the designed representation
#
# THE HOOK: every oracle ships a NEGATIVE CONTROL -- a known-healthy fixture it
# MUST NOT fire on. TERENCE reports an oracle as UNVALIDATED until its control
# passes, marks every finding from an UNVALIDATED oracle as untrusted, and
# REFUSES to let an unvalidated oracle license a destructive remedy. A detector
# that cannot demonstrate a false-positive rate is not a detector.
NEGATIVE_CONTROLS = {
    "silent_crashes": (
        "a nonzero exit that is a CONFIGURED honest skip must not be a crash",
        lambda: _control_no_false_crash()),
    "verdict_disagreement": (
        "rc != 0 with ok=true is the designed encoding of an honest skip, so it "
        "is NOT a verdict disagreement",
        lambda: _control_no_false_disagreement()),
    "unfailable_validators": (
        "a validator that can return False must not be reported as unfailable",
        lambda: _control_no_false_unfailable()),
    "gate_timing": (
        "a gate that IS invoked from the runtime must not be reported as "
        "never-invoked",
        lambda: _control_no_false_gate()),
    "orphan_mechanisms": (
        "a scheduler-registered CLI entry point must not be reported as an "
        "orphan",
        lambda: _control_no_false_orphan()),
    "unread_artifacts": (
        "a state file that IS referenced by source must not be reported unread",
        lambda: _control_no_false_unread()),
    "duplicate_rules": (
        "two DIFFERENTLY titled rules must not be reported as duplicates",
        lambda: _control_no_false_duplicate()),
    "rule_coverage": (
        "a rule WITH a validator must not be reported as unenforced",
        lambda: _control_no_false_coverage()),
    "test_failures": (
        "a suite that is known green must not be reported failing",
        lambda: _control_no_false_test_failure()),
    "import_failures": (
        "a module known to import cleanly must not be reported",
        lambda: _control_no_false_import_failure()),
    "environment_contract": (
        "a tree that matches its declared role must stay silent, and a forbidden "
        "path or a second mirror root must be reported",
        lambda: _control_no_false_environment_contract()),
    "stale_relocation_paths": (
        "a file naming the CURRENT path must stay silent; one naming the OLD "
        "path must fire; an allow-listed probe file must be exempt",
        lambda: _control_no_false_relocation()),
}


def _load_chain_skip_rc():
    """The by-design skip table, read from source. A negative control that does
    not consult the thing it is testing is not a control."""
    try:
        with open(os.path.join(AZ, "scripts", "chain_controller.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
    except Exception:
        return {}
    m = re.search(r"_SKIP_RC[^=]*=\s*\{(.*?)\}", src, re.S)
    if not m:
        return {}
    out = {}
    for jm in re.finditer(r'["\'](\w+)["\']\s*:\s*\(([^)]*)\)', m.group(1)):
        try:
            out[jm.group(1)] = tuple(int(x) for x in jm.group(2).split(",") if x.strip())
        except ValueError:
            pass
    return out


def _control_no_false_crash():
    skip = _load_chain_skip_rc()
    if not skip:
        return False, "could not read _SKIP_RC, so the control cannot run"
    try:
        with open(DUE, encoding="utf-8") as fh:
            due = json.load(fh)
    except Exception as e:
        return False, "no due file: %s" % e
    jobs = due.get("jobs") if isinstance(due.get("jobs"), dict) else due
    fires, err = oracle_silent_crashes()
    if err:
        return False, err
    fired = {f["fault_id"].split(":", 1)[-1] for f in (fires or [])}
    bad = []
    for name, codes in skip.items():
        rec = jobs.get(name) if isinstance(jobs, dict) else None
        if not isinstance(rec, dict):
            continue
        rc = rec.get("returncode", rec.get("rc"))
        if rc in codes and name in fired:
            bad.append("%s(rc=%s is a configured skip)" % (name, rc))
    if bad:
        return False, ("fired on a designed honest skip: " + ", ".join(bad))
    return True, "silent_crashes quiet on %d configured skips (%s)" % (
        len(skip), ", ".join("%s=%s" % kv for kv in sorted(skip.items())))


def _control_no_false_disagreement():
    skip = _load_chain_skip_rc()
    if not skip:
        return False, "could not read _SKIP_RC"
    fires, err = oracle_silent_crashes()
    if err:
        return False, err
    bad = [f["fault_id"] for f in (fires or [])
           if f["klass"] == "verdict_disagreement"
           and f["fault_id"].split(":", 1)[-1] in skip]
    if bad:
        return False, ("reported a verdict disagreement for a configured skip: "
                       + ", ".join(bad))
    return True, "verdict_disagreement quiet on all %d configured skips" % len(skip)


def _control_no_false_unfailable():
    fires, err = oracle_unfailable_validators()
    if err:
        return False, err
    bad = [f["fault_id"] for f in (fires or [])
           if f["klass"] == "unfailable_validator"]
    if bad:
        return False, "reported unfailable: " + ", ".join(bad)
    return True, "no validator reported unfailable"


def _control_no_false_gate():
    """gate_timing reports an ABSENCE, so a negative control cannot validate it.

    A detector that claims "X is not here" cannot be checked by showing it
    stays quiet on something X-ish -- that is exactly what it does when broken.
    The first version of this control said so and left the oracle permanently
    UNVALIDATED, which is honest but not useful. The right control for an
    absence detector is a POSITIVE CAPABILITY TEST: write a fixture that
    genuinely invokes the gate, and prove the detector SEES it. A detector that
    cannot see a real invocation is not entitled to assert that there is none.

    Three cases, all synthetic and written to a temp dir:
      import   -- `import sentinel_gate`
      attribute-- `obj.SentinelValidator()`
      docstring-- names the gate in a docstring and must NOT count

    SCOPE OF THIS CONTROL (and what it does NOT validate). It proves the
    REFERENCE TEST can see an invocation; it says nothing about whether
    `oracle_gate_timing` wires that test to a reachable branch. It did not: the
    oracle's existence guard used `os.path.isdir` on a file path, so the scan
    was unreachable and the fault unconditional, while this control passed.
    The oracle-level fixtures -- with and without a caller in the tree, plus a
    stub control that removes the predicate -- live in
    tests/test_terence_gate_timing.py and are the ones that validate the ORACLE.
    """
    import contextlib
    try:
        sys.path.insert(0, os.path.join(AZ, "src"))
        from CORE.sentinel_enforcer import permit_here as _ph
    except Exception:
        def _ph(_reason):
            return contextlib.nullcontext()

    cases = {
        "import": "import sentinel_gate\nprint(sentinel_gate)\n",
        "attribute": "def go():\n    return SentinelValidator()\n",
        "docstring": '"""Designed for integration with sentinel_gate.py."""\nx = 1\n',
    }
    seen, unseen = [], []
    with tempfile.TemporaryDirectory() as td:
        for name, body in cases.items():
            p = os.path.join(td, "probe_%s.py" % name)
            # A FRESH permit per iteration. permit_here is @contextmanager, so the
            # object is a single-use generator; holding one across the loop gave
            # "'_GeneratorContextManager' object has no attribute 'args'". The
            # enforcer's global audit hook denies ungated writes anywhere, so the
            # permit is required rather than tidy.
            with _ph("terence: gate capability fixture %s" % name):
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(body)
            hit, why = _invokes_gate(p)
            (seen if hit else unseen).append("%s(%s)" % (name, why))
    want_seen = {"import", "attribute"}
    want_unseen = {"docstring"}
    got_seen = {s.split("(")[0] for s in seen}
    got_unseen = {s.split("(")[0] for s in unseen}
    if not want_seen <= got_seen:
        return False, ("the detector cannot see a real invocation, so it is not "
                       "entitled to report an absence. saw=%s" % sorted(seen))
    if not want_unseen <= got_unseen:
        return False, ("the detector counts a DOCSTRING as an invocation, which "
                       "is the exact false pass of 2026-09-30. counted=%s"
                       % sorted(seen))
    return True, ("detector sees import and attribute references, and correctly "
                  "ignores a docstring that names the gate")


def _control_no_false_orphan():
    scheduled = _scheduler_targets()
    if not scheduled:
        return False, "no scheduler targets, cannot run the control"
    fires, err = oracle_orphan_mechanisms()
    if err:
        return False, err
    leaked = [f["fault_id"] for f in (fires or []) if f["fault_id"].split(":", 1)[-1] in scheduled]
    if leaked:
        return False, "reported a scheduler target as an orphan: " + ", ".join(leaked)
    return True, "orphan_mechanism quiet on %d scheduler targets" % len(scheduled)


def _control_no_false_unread():
    fires, err = oracle_unread_artifacts()
    if err:
        return False, err
    # A file the ledger itself reads every run is definitely referenced.
    must_be_read = {"chain_controller_due.json", "chain_controller_plan.json"}
    bad = [f["fault_id"].split(":", 1)[-1] for f in (fires or [])
           if f["fault_id"].split(":", 1)[-1] in must_be_read]
    if bad:
        return False, "reported a known-referenced file as unread: " + ", ".join(bad)
    return True, "unread_artifacts quiet on %d known-referenced files" % len(must_be_read)


def _control_no_false_duplicate():
    fires, err = oracle_duplicate_rules()
    if err:
        return False, err
    if fires:
        return False, ("reported exact duplicates: "
                       + ", ".join(f["fault_id"] for f in fires))
    return True, "duplicate_rules quiet (no exact-title duplicates in canon)"


def _control_no_false_coverage():
    fires, err = oracle_rule_coverage()
    if err:
        return False, err
    ids = {f["detail"] for f in (fires or [])}
    # 0B-A IS hooked and tested; if the coverage oracle lists it as having no
    # validator, the coverage oracle is broken, not the canon.
    for f in (fires or []):
        if "0B-A" in f["detail"].split("have no validator:")[-1].split(",")[0]:
            return False, "reported 0B-A as unenforced, but it has a validator"
    return True, "rule_coverage agrees 0B-A is enforced (%d finding(s))" % len(fires or [])


def oracle_self_validation():
    """Run every negative control. An oracle whose control fails is UNVALIDATED,
    and its findings are marked untrusted and barred from licensing a remedy."""
    rows, unvalidated = [], []
    for name, (desc, fn) in NEGATIVE_CONTROLS.items():
        try:
            ok, note = fn()
        except Exception as e:
            ok, note = False, "%s: %s" % (type(e).__name__, e)
        rows.append({"oracle": name, "control": desc, "passed": bool(ok),
                     "note": str(note)[:180]})
        if not ok:
            unvalidated.append(name)
    return rows, unvalidated


# ── the two classes that were uncovered for the cheapest possible reason ──
# 2026-09-30, the coverage registry said of both:
#   test_failure   "NOT covered, no oracle reads test exit codes"
#   import_failure "NOT covered, no oracle reads import results"
# and both are among the most consequential classes in the system -- every canon
# violation of 2026-09-30 would have been caught by a test run that reported
# honestly. They were uncovered because nobody had written twenty lines, not
# because the predicate does not exist. A bounded, honest test runner makes it.
#
# BOUNDED, and the bound is declared: TEST_TIMEOUT_S per run and a fixed module
# set. An oracle that can hang is an oracle that will be disabled.
TEST_TIMEOUT_S = 300
IMPORT_TIMEOUT_S = 20
# Modules whose importability is a precondition for the chain. A failure here is
# loud in a way a test suite is not, because nothing else can run.
# Named as CORE.* with src on sys.path, NOT src.CORE.*: `importlib.import_module`
# cannot resolve `src.CORE` even though src/__init__.py exists, because the
# AZ root is the CWD and the chain itself puts `src` on the path instead. The
# first version of this list used `src.CORE.*` and every module came back
# ModuleNotFoundError -- a total false positive, caught by the negative control.
_IMPORT_WATCH = [
    # Restored 2026-09-30 by the deleted-file recovery. Neither had an
    # importer, so nothing else can notice if they break -- this watch is
    # what makes "restored" mean "reachable".
    "CORE.worm_dependency",
    "CORE.audit_trail",
    "CORE.sentinel_enforcer",
    "CORE.compliance.orchestrator",
    "SCRIPTS.sentinel_gate",
    "tools.terence_invariants",
]


def _py():
    return sys.executable


def oracle_test_failures():
    """artifact/runtime. A test suite that fails and says so. Reads REAL exit
    codes, which is exactly what the coverage registry said no oracle did."""
    faults = []
    # _run() already prepends sys.executable, so this list holds ARGUMENTS ONLY.
    # Passing the interpreter here as well made python try to parse python.exe as
    # source ("Non-UTF-8 code starting with '\\x90'") and the oracle reported a
    # test failure on a suite that had just passed 14/14. Caught by the control.
    cmd = ["-m", "pytest", "tests/test_rule_0b_a_edict.py",
           "tests/test_meta_reflect.py", "-q", "--no-header", "-p",
           "no:cacheprovider"]
    try:
        rc, out, err = _run(cmd, timeout=TEST_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, ("test run exceeded %ds -- an oracle that can hang gets "
                      "disabled, so this is reported as unrunnable rather than "
                      "passed" % TEST_TIMEOUT_S)
    combined = (out or "") + (err or "")
    # pytest exit codes: 0 all passed, 1 tests failed, 5 no tests collected.
    if rc == 0:
        return [], None
    if rc == 5:
        return None, "pytest collected no tests (rc=5)"
    tail = "\n".join(l for l in combined.splitlines() if l.strip())[-400:]
    faults.append(Fault(
        "test_failure:watched_suite", "test_failure", "tests/",
        "pytest exited %s on the watched suite (2 files). Output tail: %s"
        % (rc, tail.replace("\n", " ")[:300]),
        "critical", remedy=None, verifier=None))
    return faults, None


def oracle_import_failures():
    """runtime. A module that cannot be imported. Reads REAL import results.

    The probe source is built with json.dumps for the watch list and NO % string
    formatting at all. The first version interpolated with `%r`/`%%s` and raised
    TypeError: not enough arguments for format string -- caught by its own
    negative control, which is the only reason it was caught before it mattered.
    """
    faults = []
    probe = (
        "import sys\n"
        "sys.path.insert(0, " + json.dumps(os.path.join(AZ, "src")) + ")\n"
        "sys.path.insert(0, " + json.dumps(AZ) + ")\n"
        "import importlib, json\n"
        "out = {}\n"
        "for m in " + json.dumps(_watched_modules()) + ":\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "        out[m] = 'ok'\n"
        "    except Exception as exc:\n"
        "        out[m] = type(exc).__name__ + ': ' + str(exc)\n"
        "print(json.dumps(out))\n"
    )
    try:
        rc, out, err = _run(["-c", probe], timeout=IMPORT_TIMEOUT_S * 2)
    except subprocess.TimeoutExpired:
        return None, "import probe exceeded its timeout"
    if rc != 0:
        return None, "import probe itself failed: %s" % (err or out)[:160]
    try:
        res = json.loads((out or "").strip().splitlines()[-1])
    except Exception as e:
        return None, "could not parse import probe output: %s" % e
    for mod, status in res.items():
        if status != "ok":
            faults.append(Fault(
                "import_failure:%s" % mod, "import_failure", mod,
                "module %s failed to import: %s" % (mod, status[:200]),
                "critical", remedy=None, verifier=None))
    return faults, None


def _control_no_false_test_failure():
    """A suite that is known green must not be reported failing. Consults the
    real runner rather than hardcoding an expectation."""
    watched = "test_rule_0b_a_edict.py"
    if watched not in _test_files():
        return False, ("the known-green control suite %r is missing from "
                       "tests/ -- without it this control cannot run" % watched)
    fires, err = oracle_test_failures()
    if err:
        return False, err
    if fires:
        return False, ("reported a failure on a suite that was green minutes "
                       "ago: " + ", ".join(f["fault_id"] for f in fires))
    return True, "watched suite is green, so no failure is claimed"


def _test_files():
    d = os.path.join(AZ, "tests")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


# A watched module that is deliberately absent from a pillar is NOT a broken
# import. The two restored modules live in the Ubuntu pillar only; watching them
# on Windows reported their absence as a failure -- a false positive of exactly
# the kind "no verifier, no fix" exists to prevent. Absence is a fact about the
# pillar, so the watch list is filtered by whether the source exists HERE.
# Only CORE.worm_dependency is listed: the restored CODEX/CORE/animation_protocols.py
# is a DEPRECATION SHIM on Windows that is not importable by that dotted name (the
# CODEX/CORE package chain is not a package there), so it is not a portable watch
# target. Absence on Windows is handled by the filter; non-importability is a real
# defect of the shim, reported separately rather than pinned into this list.
_IMPORT_WATCH_SOURCES = {
    "CORE.worm_dependency": "src/CORE/worm_dependency.py",
}


def _watched_modules():
    """Only the watched modules whose source exists in THIS pillar."""
    out = []
    for m in _IMPORT_WATCH:
        rel = _IMPORT_WATCH_SOURCES.get(m)
        if rel is None or os.path.exists(os.path.join(AZ, *rel.split("/"))):
            out.append(m)
    return out


def _control_no_false_import_failure():
    """A module known to import cleanly must not be reported. Uses the modules
    the oracle itself imports -- if it cannot import its own dependencies, the
    oracle is broken and the control must say so."""
    try:
        import ast  # noqa: F401
        import json    # noqa: F401
        import sqlite3  # noqa: F401
    except Exception as e:
        return False, "control precondition failed: %s" % e
    fires, err = oracle_import_failures()
    if err:
        return False, err
    if fires:
        return False, ("reported an import failure: "
                       + ", ".join(f["fault_id"] for f in fires))
    return True, "all %d watched modules import cleanly" % len(_watched_modules())


# ── environment contract: the quartet's declared roles, asserted ───────────
# WHY THIS EXISTS (2026-09-30). The quartet's four environments each have a
# DECLARED role, and the declarations are already written down --
# sync-ubuntu.exclude, SYNC.md "Quartet Sync Strategy", CROSS_ENV_MANIFEST.md.
#   Kali Linux   = CANONICAL SOURCE
#   Ubuntu-26.04 = ML ACCELERATOR (lean ML subset; HTML->MP4 belongs to Windows)
#   G: drive     = ARCHIVE / SOURCE OF TRUTH
#   Windows 11   = active-dev MIRROR
# Nothing checked them. So Ubuntu accumulated a video/render surface
# (src/CODEX/VIDEOS, OUTPUT/videos, the CAIN pipeline skills) that its role
# forbids -- four environments drift because one role was unasserted.
#
# CORRECTION 2026-10-31 (user-stated): "Vermillion LightFury is the quartet
# architecture ... it runs in all environments." The contract below used to
# FORBID vermillion_lightfury on Windows and Ubuntu on the reading that it was
# a Kali-only pillar. That was wrong twice over: it under-scoped the name to
# one pillar, and it would have fired a role violation on the CORRECT
# arrangement the moment the pillar was actually deployed there -- a gate that
# punishes the desired state. The assertion is now inverted: every live pillar
# is REQUIRED to carry it as a sibling, and its absence is the fault.
#
# This is a SOURCE oracle: it reads the declared contract and the tree and
# reports the difference. It is deliberately NOT machine-repairable -- "rip the
# video pipeline out of the ML node" is a DECISION with four environments
# depending on it, so it is reported and refused, like enforce-or-retire.
#
# FOR THE LEARNING SUBSTRATE: the same visible path means DIFFERENT things per
# pillar. src/CODEX/VIDEOS is a role violation on Ubuntu and the JOB on Windows.
# So `run_all` stamps every fault with its environment, the ledger report names
# its pillar, the error_log note carries `env=<pillar>`, and every attempt
# record does too. A learner must bucket by (klass, environment) -- grouping by
# klass alone would teach a global ban on rendering, which is exactly the
# over-generalization that produced the original destructive sweep.
ENV_CONTRACT = {
    "ubuntu": {
        # exact root-level names sync-ubuntu.exclude exists to keep OUT
        "forbidden": [
            "archive", "archived_unified", "build", "output", "logs",
            "coverage_inference_html",
            "src/CODEX/VIDEOS", "src/CODEX/OUTPUT/videos", "src/output/videos",
            "templates/video",
        ],
        # Vermillion LightFury runs in ALL environments, so a sibling copy is
        # required here, not forbidden. See the CORRECTION note above.
        "required_base": ["vermillion_lightfury"],
        "why": ("Ubuntu-26.04 is the quartet's ML ACCELERATOR; sync-ubuntu.exclude "
                "keeps it lean (~400 MB) and HTML->MP4 rendering belongs to Windows. "
                "Vermillion LightFury is required in every pillar."),
    },
    "windows": {
        "forbidden": [],
        "required_base": ["vermillion_lightfury"],
        "why": ("the quartet architecture runs in every environment; a missing "
                "vermillion_lightfury is the fault, not the presence of one"),
    },
    "kali": {"forbidden": [],
             "required_base": ["vermillion_lightfury"],
             "why": "canonical source; carries vermillion_lightfury"},
    # The archive is not a pillar that RUNS anything, so presence is not
    # asserted here: G: holds whatever the pillars have published.
    "g_drive": {"forbidden": [], "why": "archive / source of truth"},
}

# Duplicate mirror roots: the quartet's safety is ONE canonical tree mirrored
# OUTWARD. More than one of these inside a single environment is drift, and
# drift is the exact failure the sync strategy was written to prevent.
_MIRROR_ROOTS = ("azazael", "azazael-dev", "azazael-project",
                 "azazael-project-e", "workspace")


def _detect_environment(root):
    """Which pillar is this tree? The TREE decides, not the process.

    The first version read only the host OS, so a WSL process auditing
    `/mnt/c/Users/clint/azazael` (the Windows mirror) labelled it "ubuntu" and
    reported the Windows tree for Ubuntu-forbidden paths -- a total false
    positive, caught by running it rather than by reasoning. The tree's
    location is the environment; the OS only breaks ties for a native tree.
    """
    p = str(root).replace("\\", "/")
    if p.startswith(("G:", "/mnt/g")):
        return "g_drive"
    if p.startswith(("C:", "/mnt/c", "D:", "/mnt/d", "E:", "/mnt/e")):
        return "windows"
    if os.name == "nt":
        return "windows"
    try:
        with open("/etc/os-release", encoding="utf-8", errors="replace") as fh:
            txt = fh.read().lower()
    except OSError:
        return "unknown"
    if "id=kali" in txt:
        return "kali"
    if "id=ubuntu" in txt:
        return "ubuntu"
    return "unknown"


def _contract_violations(root, env, contract, base=None):
    """PURE detection: which forbidden paths exist for `env`.

    Checks inside the tree (`forbidden`) and, when `base` is given, among its
    SIBLINGS (`forbidden_base`) -- a Kali-native pillar is forbidden as a
    sibling of the ML node, not merely inside it. Split out from the oracle so
    the negative control runs it against a synthetic fixture, not the live tree.
    """
    spec = contract.get(env) or {}
    hits = []
    checks = [(root, spec.get("forbidden", ()))]
    if base:
        checks.append((base, spec.get("forbidden_base", ())))
    for where, rels in checks:
        for rel in rels:
            p = os.path.join(where, *rel.split("/"))
            if os.path.exists(p):
                try:
                    n = os.path.getsize(p) if os.path.isfile(p) else None
                except OSError:
                    n = None
                hits.append({"path": rel, "abs": p, "bytes": n})
    return hits


def _contract_missing(root, env, contract, base=None):
    """PURE: which REQUIRED siblings are absent for `env`.

    The mirror of `_contract_violations`: that one reports paths whose
    PRESENCE is the fault, this one reports paths whose ABSENCE is. Kept
    separate and pure so the negative control can drive both from a synthetic
    fixture instead of the live tree.
    """
    spec = contract.get(env) or {}
    if not base:
        return []
    return [rel for rel in spec.get("required_base", ())
            if not os.path.exists(os.path.join(base, *rel.split("/")))]


def _looks_like_project_root(path):
    """PURE: is this an azazael-shaped project TREE, or a directory that merely
    shares a mirror-root NAME?

    Refined 2026-09-30 after the first live run: `/mnt/c/Users/clint/workspace`
    held ONE sqlite file and was reported as a divergent mirror of azazael -- a
    false positive. `_MIRROR_ROOTS` are azazael-shaped names, so a candidate is
    only a mirror if it actually looks like the tree: it carries a project
    marker. A name is not a tree, and calling a name drift is the same class of
    error as calling an oracle healthy because it exists.
    """
    for marker in ("src", "tools", "CORE", ".git", "azazael"):
        if os.path.exists(os.path.join(path, marker)):
            return True
    return False


def _mirror_roots(base):
    """PURE: which known project-root names coexisting under `base` are REAL
    trees. A name-only directory is not drift."""
    return [n for n in _MIRROR_ROOTS
            if _looks_like_project_root(os.path.join(base, n))]


def oracle_environment_contract():
    """source. Does THIS environment match the role the quartet declared?"""
    env = _detect_environment(AZ)
    base = os.path.dirname(AZ.rstrip("/\\"))
    faults = []
    if env in ENV_CONTRACT:
        for hit in _contract_violations(AZ, env, ENV_CONTRACT, base):
            faults.append(Fault(
                fault_id="envrole:%s:%s" % (env, hit["path"]),
                klass="environment_role_violation",
                subject=hit["path"],
                detail="forbidden in the %s environment: %s"
                       % (env, ENV_CONTRACT[env]["why"]),
                severity="high", remedy=None, verifier=None))
        for miss in _contract_missing(AZ, env, ENV_CONTRACT, base):
            faults.append(Fault(
                fault_id="envrole:%s:missing:%s" % (env, miss),
                klass="environment_role_violation",
                subject=os.path.join(base, miss),
                detail=("REQUIRED in the %s environment but absent: %s"
                        % (env, ENV_CONTRACT[env]["why"])),
                severity="high", remedy=None, verifier=None))
    # An ARCHIVE legitimately holds many project trees (G: is the source of
    # truth and carries azazael, azazael-dev, ...). "One canonical tree" is a
    # rule about a live PILLAR, not about an archive, so duplicate_mirrors is
    # scoped to pillars. Reporting it on g_drive was a category error: it would
    # flag the archive for doing its job, which is worse than not checking.
    mirrors = _mirror_roots(base) if env != "g_drive" else []
    if len(mirrors) > 1:
        faults.append(Fault(
            fault_id="envrole:duplicate_mirrors",
            klass="environment_role_violation",
            subject=os.path.join(base, "{" + ",".join(mirrors) + "}"),
            detail=("%d divergent project roots in one environment (%s); the "
                    "quartet's safety is ONE canonical tree mirrored outward"
                    % (len(mirrors), ", ".join(mirrors))),
            severity="medium", remedy=None, verifier=None))
    return faults, None


def _control_no_false_environment_contract():
    """A tree matching its role must stay silent; a tree with a forbidden path
    must fire. Runs the pure detectors on synthetic fixtures, not the live tree.

    UPDATED 2026-10-31 for the inverted Vermillion LightFury contract. The old
    control asserted that a sibling copy was a VIOLATION -- which is now the
    required state. A control that still tests the superseded rule fails the
    oracle while the code is right, and passing it would mean the change never
    landed. It now asserts both directions: a missing pillar fires, a present
    one stays silent.
    """
    import tempfile
    clean = tempfile.mkdtemp(prefix="terence_env_clean_")
    dirty = tempfile.mkdtemp(prefix="terence_env_dirty_")
    base = tempfile.mkdtemp(prefix="terence_env_base_")
    try:
        if _contract_violations(clean, "ubuntu", ENV_CONTRACT, clean):
            return False, "false positive on a clean tree"
        os.makedirs(os.path.join(dirty, "build"), exist_ok=True)
        if not _contract_violations(dirty, "ubuntu", ENV_CONTRACT, dirty):
            return False, "missed a forbidden path on a dirty tree"
        # ABSENT pillar = the fault now.
        if not _contract_missing(os.path.join(base, "tree"), "ubuntu",
                                 ENV_CONTRACT, base):
            return False, "missed a REQUIRED pillar that is absent"
        # PRESENT pillar = correct, must stay silent in both detectors.
        os.makedirs(os.path.join(base, "vermillion_lightfury"), exist_ok=True)
        if _contract_missing(os.path.join(base, "tree"), "ubuntu",
                             ENV_CONTRACT, base):
            return False, "fired on a REQUIRED pillar that is present"
        if _contract_violations(os.path.join(base, "tree"), "ubuntu",
                                ENV_CONTRACT, base):
            return False, "a present Vermillion LightFury tree is not a violation"
        os.makedirs(os.path.join(base, "azazael", "src"), exist_ok=True)
        if len(_mirror_roots(base)) != 1:
            return False, "one real mirror root must not be a violation"
        os.makedirs(os.path.join(base, "workspace"), exist_ok=True)
        if len(_mirror_roots(base)) != 1:
            return False, ("a directory that only shares a mirror-root NAME "
                           "must not be drift")
        os.makedirs(os.path.join(base, "workspace", "tools"), exist_ok=True)
        if len(_mirror_roots(base)) < 2:
            return False, "two real mirror roots must be a violation"
        # the stamp that makes learning environment-scoped must reach EVERY
        # fault; if it silently stops, a Windows lesson gets applied to Ubuntu
        fs = [Fault("x:1", "k", "s", "d", "low"),
              Fault("x:2", "k", "s", "d", "low")]
        _stamp_environment(fs, "ubuntu")
        if [f.get("environment") for f in fs] != ["ubuntu", "ubuntu"]:
            return False, "environment stamp did not reach every fault"
    finally:
        for d in (clean, dirty, base):
            shutil.rmtree(d, ignore_errors=True)
    return True, ("clean tree silent; forbidden path fires; absent Vermillion "
                  "pillar fires; present one silent; real duplicate mirrors; "
                  "name-only dirs ignored; every fault env-stamped")


# ── relocation staleness: a moved tree must not be referenced at its OLD path ─
# Operator-approved relocations (2026-09-30). Each entry is
# (old_absolute_path, where_it_went, files_where_the_old_literal_is_FINE).
#
# The failure this catches: a tree is moved to clear a contract violation, and a
# hardcoded reference to the OLD path survives -- so the next run silently falls
# back to a stale location and nobody knows why. It is a SOURCE oracle: it greps
# source for the old literal.
#
# `allow_files` are probe/candidate lists DESIGNED to tolerate absence (the worm
# registry lists several possible locations and picks whichever exists). Flagging
# those would be the false positive that makes the oracle worthless, so the
# allowance is declared per relocation rather than guessed at from line content.
_RELOCATIONS = [
    ("C:/Users/clint/vermillion_lightfury",
     "C:/Users/clint/.kali_staging/vermillion_lightfury", ()),
    ("/root/vermillion_lightfury",
     "/root/.kali_staging/vermillion_lightfury",
     ("src/CORE/worm_registry.py", "src/CORE/worm_registry_cache.py",
      "scripts/cosmos_build_atlas.sh")),
    ("/root/azazael-dev", "/root/.mirror_quarantine/azazael-dev", ()),
    # gossamer-oxford-parser was moved OUT of /root/workspace and the stray
    # workspace archived, so any surviving /root/workspace/... reference is
    # stale. The generated BIBL skeleton keeps the OLD path inside a
    # docstring describing WSL2 behaviour; that is prose, not a live path.
    ("/root/workspace", "/root/.mirror_quarantine/workspace",
     ("src/ORCHESTRATION/BIBL/skeleton.json",)),
]

# Scanner bounds: an oracle that walks a 14 GB mirror unbounded is an oracle
# that gets disabled. Text only, small only, and the archive/cache dirs skipped.
# SOURCE only. `dashboard/` is generated output: the wallboard renders the
# fault list, which itself names the old paths -- scanning it made the
# oracle report its own rendering (a self-reference, not a finding).
_RELOC_SCAN_DIRS = ("src", "tools", "scripts")
_RELOC_SKIP_DIRS = {"__pycache__", ".git", "node_modules", "archive",
                    "archived_unified", "build", "output", "logs"}
_RELOC_EXTS = (".py", ".sh", ".js", ".bat", ".ps1", ".json", ".md", ".html",
               ".yml", ".yaml", ".cfg", ".ini", ".toml")
_RELOC_MAX_BYTES = 2_000_000


def _scan_old_path(root, old, allow_files):
    """PURE: every source line under `root` that still names `old`.

    Skips allow-listed files and bounded (dirs/size) so it stays fast on a big
    mirror. Returns [(relpath, lineno, line[:140])].
    """
    hits = []
    for d in _RELOC_SCAN_DIRS:
        top = os.path.join(root, d)
        for dp, dns, fns in os.walk(top):
            dns[:] = [x for x in dns if x not in _RELOC_SKIP_DIRS]
            for fn in fns:
                if not fn.endswith(_RELOC_EXTS):
                    continue
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, root).replace("\\", "/")
                if rel in allow_files:
                    continue
                try:
                    if os.path.getsize(p) > _RELOC_MAX_BYTES:
                        continue
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        for i, line in enumerate(fh, 1):
                            if old in line:
                                hits.append((rel, i, line.strip()[:140]))
                except OSError:
                    continue
    return hits


def oracle_stale_relocation_paths():
    """source. Did a relocated tree leave a hardcoded reference to its OLD path?"""
    faults = []
    # The registry above and the negative control below CONTAIN the old literals
    # by definition, so the tool's own file is always exempt -- otherwise the
    # oracle reports itself, which is noise, not a finding.
    self_rel = os.path.relpath(__file__, AZ).replace("\\", "/")
    for old, new, allow in _RELOCATIONS:
        if os.path.exists(old):
            continue  # not actually relocated here; nothing to be stale about
        for rel, ln, line in _scan_old_path(AZ, old, set(allow) | {self_rel})[:60]:
            faults.append(Fault(
                fault_id="reloc:%s:%s:%d" % (old, rel, ln),
                klass="stale_relocation_path", subject=rel,
                detail=("references relocated path %s (now %s) at line %d: %s"
                        % (old, new, ln, line)),
                severity="medium", remedy=None, verifier=None))
    return faults, None


def _control_no_false_relocation():
    """A file referencing the CURRENT path must stay silent; one referencing the
    OLD path must fire; an allow-listed file must be exempt."""
    import tempfile
    root = tempfile.mkdtemp(prefix="terence_reloc_")
    try:
        os.makedirs(os.path.join(root, "src", "CORE"), exist_ok=True)
        os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
        old = "/root/vermillion_lightfury"
        # src/CORE/sentinel_enforcer.py installs a CPython audit hook that
        # DENIES file writes from an ungated thread -- the first version of this
        # control died with "CRITICAL: Unauthorized write". That hook is real
        # enforcement; route the fixture writes through the authorized path
        # rather than weaken the hook for a test.
        with _permit():
            with open(os.path.join(root, "src", "live.py"), "w", encoding="utf-8") as fh:
                fh.write("ROOT = %r\n" % "/root/.kali_staging/vermillion_lightfury")
            if _scan_old_path(root, old, set()):
                return False, "false positive: current path reported as stale"
            with open(os.path.join(root, "src", "stale.py"), "w", encoding="utf-8") as fh:
                fh.write("ROOT = %r\n" % old)
        if not _scan_old_path(root, old, set()):
            return False, "missed a stale reference to the old path"
        if _scan_old_path(root, old, {"src/stale.py"}):
            return False, "allow-listed file was still flagged"
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return True, "current path silent; stale path fires; allow-list exempt"


ORACLES = [
    # (name, fn, kind). `kind` is the axis that matters when someone brings a
    # new bug: it says what sort of evidence the oracle reads, and therefore
    # which classes of error COULD be covered here and which cannot.
    #   artifact - reads a state/instrument JSON file
    #   runtime  - calls code and observes behaviour
    #   source   - parses source or canon text for structure
    #   wiring   - inspects where something is invoked from
    ("evolution_progress", oracle_evolution_progress, "artifact"),
    ("fitness_arms", oracle_fitness_arms, "artifact"),
    ("lesson_liveness", oracle_lesson_liveness, "artifact"),
    ("pool_shape", oracle_pool_shape, "artifact"),
    ("unfailable_validators", oracle_unfailable_validators, "runtime"),
    ("duplicate_rules", oracle_duplicate_rules, "source"),
    ("gate_timing", oracle_gate_timing, "wiring"),
    ("rule_coverage", oracle_rule_coverage, "coverage"),
    # the three "produced but not consumed" faces
    ("silent_crashes", oracle_silent_crashes, "artifact"),
    ("unread_artifacts", oracle_unread_artifacts, "wiring"),
    ("orphan_mechanisms", oracle_orphan_mechanisms, "wiring"),
    ("test_failures", oracle_test_failures, "runtime"),
    ("import_failures", oracle_import_failures, "runtime"),
    # the quartet's declared roles, asserted against the tree it is running in
    ("environment_contract", oracle_environment_contract, "source"),
    ("stale_relocation_paths", oracle_stale_relocation_paths, "source"),
]

# ── coverage registry, with its gaps stated ────────────────────────────────
# WHY THIS EXISTS. The ledger reports oracles_run / oracles_total. With four
# artifact oracles that reads as complete coverage of an unbounded set, which
# is the old failure in a new place: a tidy number for a partial measurement,
# the same shape as TERENCE's own 8,009-fixes-on-zero-files-scanned report.
#
# COVERAGE is the declared universe of error classes. Anything in ERROR_LOG or
# ERROR_LOG-equivalent that is not listed here is, by definition, NOT covered --
# and a class with no oracle is recorded as UNCOVERED rather than omitted,
# because an unlisted class is invisible and a listed-but-uncovered one is at
# least honest.
#
# `detector` is what would catch it. `repairable` is deliberately False for most
# of these: "no verifier, no fix" means a class whose repair requires a
# DECISION (merge these two rules, retire that one, wire the tool path) is
# reported and refused, not automated. Encoding a judgement call as an automatic
# patch is how the original sweep produced 8,009 nonsense fixes.
COVERAGE = [
    # fault_id                       detector                     kind       repairable  one-line
    ("unfailable_validator",         "oracle_unfailable_validators", "runtime",  True,
     "a validator whose every branch returns True can never fail"),
    ("duplicate_canon_rule",         "oracle_duplicate_rules",       "source",   False,
     "two RULE ids with an IDENTICAL title (semantic duplication UNCOVERED)"),
    ("gate_fires_only_after_work",   "oracle_gate_timing",           "wiring",   False,
     "a PRE-action gate no agent-runtime file actually invokes"),
    ("critical_rule_unenforced",     "oracle_rule_coverage",         "coverage", False,
     "a rule with no validator referencing it"),
    ("silent_crash",                 "oracle_silent_crashes",        "artifact", False,
     "a job that ran and exited non-zero"),
    ("verdict_disagreement",         "oracle_silent_crashes",        "artifact", False,
     "a non-zero exit recorded ok=true, so the failure is invisible"),
    ("unread_artifact",              "oracle_unread_artifacts",      "wiring",   False,
     "a state file written every cycle and read by nothing"),
    ("orphan_mechanism",             "oracle_orphan_mechanisms",     "wiring",   False,
     "a script with no importer, no scheduler entry, and no caller"),
    ("test_failure",                 "oracle_test_failures",         "runtime",  False,
     "a test suite exits non-zero; the two most consequential classes in the "
     "system were uncovered only because nobody had written the twenty lines"),
    ("import_failure",               "oracle_import_failures",       "runtime",  False,
     "a watched module cannot be imported; nothing else can run if this fails"),
    ("environment_role_violation",   "oracle_environment_contract",  "source",   False,
     "a pillar holds artifacts its declared role forbids, or one environment "
     "carries several divergent mirrors of the same tree (SYNC.md quartet contract). "
     "Not repairable: ripping a pipeline out of a pillar is a DECISION across "
     "four environments, not a patch"),
    # --- declared, deliberately uncovered ---------------------------------
    ("semantic_duplicate_rule",      None,                          "source",   False,
     "two rules that MEAN the same but are worded differently; UNCOVERED by "
     "measurement -- over 741 title pairs no token threshold separates the true "
     "pair 0J/0S (0.308) from the false 0C/0J (0.308). Declared pairs: 0E/0E-1, 0J/0S"),
    ("no_compile_failure",           None,                          "runtime",  True,
     "tree compiles clean -- regression, already covered by the build"),
    # import_failure and test_failure USED to be declared here as UNCOVERED while
    # oracle_import_failures / oracle_test_failures already covered them -- the
    # same class listed twice, once wrongly, so `--coverage` reported gaps that
    # no longer existed ("an oracle that cannot see the thing it was built to
    # measure"). Removed 2026-09-30: the covered rows above are the truth, and
    # `--self-test` exercises both oracles' negative controls.
    ("stale_relocation_path",        "oracle_stale_relocation_paths", "source",  False,
     "source still names the OLD path of a tree that was relocated to clear a "
     "contract violation; the reference silently falls back and nobody knows why. "
     "Not repairable: which of several legal paths a probe should keep is a call"),
    ("stale_generated_artifact",     None,                          "artifact", False,
     "a generated file no longer describes its tree; NOT covered"),
    ("semantic_regression",          None,                          "artifact", False,
     "a search or ranking result got worse; NOT covered, needs a golden set"),
    ("style_and_convention",         None,                          "source",   False,
     "convention drift; NOT COVERED BY DESIGN -- preference, not repair"),
    ("unreadable_specification",     None,                          "source",   False,
     "a requirement stated in prose that no predicate can check; NOT covered"),
    ("mesh_partition_vs_idle",       None,                          "artifact", False,
     "a dead mesh vs an idle mesh; NOT COVERED and not fixable until the mesh "
     "has a liveness proof -- mesh_listener.json reports received=0, errors=0 "
     "identically for both, so there is no predicate and therefore no verifier"),
]

# Classes ERROR_LOG records that no oracle claims. Populated from the ledger's
# own component field so the gap list is measured, not remembered.



# ── autonomy: the repair loop, the remedy library, and the learning substrate ──
# WHY THIS SECTION EXISTS. The first version of this file emitted 162 faults and
# repaired NONE, because every Fault was constructed with remedy=None, verifier=None.
# That was an over-reading of "no verifier, no fix": the rule forbids repairing
# WITHOUT A PREDICATE, and I had implemented it as "do not repair without a
# HAND-WRITTEN remedy per fault." Those are different things.
#
# The cost of the over-reading was total: a repair loop that never repairs emits
# no learning signal, so the loop cannot learn, and 162 faults are re-detected
# identically on every run forever. Reporting is not autonomy.
#
# THE CORRECTION. Verification does not need to be hand-written per fault. It can
# be GENERIC: apply a candidate remedy, then RE-RUN THE FAULT'S OWN ORACLE. The
# fault is cleared or it is not. That is the predicate, it is available for free
# for every fault class that has an oracle, and it is what makes autonomous
# repair safe -- a repair is kept only when the detector that found the fault
# stops finding it.
#
# SAFETY IS BLAST RADIUS, NOT PERMISSION. An autonomous repair must be:
#   reversible   -- snapshot before apply, roll back on failed verification
#   bounded      -- sandboxed to a declared set of actions, never arbitrary
#   evidenced    -- the attempt is written to ATTEMPTS before the change
#   learned-from -- every attempt, successful or not, feeds remedy ranking
# Nothing here deletes anything. The strongest automated action is a REVERSIBLE
# MOVE into a quarantine directory, which is recoverable by construction.
#
# WHAT IS STILL REFUSED, and the refusal is the point rather than a limitation:
#   a class with no predicate is still not repaired, because there is nothing to
#   verify against. The refusals below are recorded as NO_REMEDY so the gap stays
#   visible instead of being quietly skipped.
ATTEMPTS = os.path.join(STATE_DIR, "terence_attempts.jsonl")
QUARANTINE = os.path.join(AZ, "var", "dead")


def _permit():
    """sentinel_enforcer.permit_here, or a no-op if the enforcer is absent."""
    try:
        sys.path.insert(0, os.path.join(AZ, "src"))
        from CORE.sentinel_enforcer import permit_here
        return permit_here("terence_invariants: autonomous repair")
    except Exception:
        import contextlib
        return contextlib.nullcontext()


def _record_attempt(fault_id, klass, remedy_id, verified, detail):
    """The learning substrate. Without this there is no learning -- only
    repetition. Appended BEFORE the outcome is judged, so a crash mid-repair
    still leaves a record that the attempt was made."""
    rec = {"ts": time.time(), "fault_id": fault_id, "klass": klass,
           "environment": _detect_environment(AZ),
           "remedy": remedy_id, "verified": bool(verified),
           "detail": detail[:200]}
    try:
        with _permit():
            os.makedirs(os.path.dirname(ATTEMPTS), exist_ok=True)
            with open(ATTEMPTS, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
    except Exception:
        print("terence_invariants: attempts write failed", file=sys.stderr)
    return rec


def _remedy_history():
    """Measured success rate per (klass, remedy), from the attempts ledger."""
    hist = {}
    for line in _attempt_rows():
        r = json.loads(line)
        k = (r.get("klass"), r.get("remedy"))
        ok = hist.setdefault(k, [0, 0])
        ok[1] += 1
        if r.get("verified"):
            ok[0] += 1
        _LAST_TS[k] = max(_LAST_TS.get(k, 0.0), r.get("ts", 0.0))
    return hist


_LAST_TS = {}


def _attempt_rows():
    try:
        with open(ATTEMPTS, encoding="utf-8") as fh:
            return [l for l in fh if l.strip()]
    except Exception:
        return []


# ── the remedy library ────────────────────────────────────────────────────
# Each entry: (remedy_id, kinds_it_may_touch, apply_fn). apply_fn returns
# (ok: bool, rollback: callable|None, note: str). It performs ONE mechanical
# action. It must be idempotent and it must not be able to delete.
#
# REMEDIES is ranked by MEASURED success, not by authorial order: a remedy that
# has failed verification for a class is tried later, and a remedy that has never
# been tried keeps its declared rank. Ranking from outcomes is the whole point.
REMEDIES = [
    ("rerun_transient", ("silent_crash",),
     # A non-zero exit with NO error output is the signature of a transient:
     # a timeout, a port collision, a resource that was briefly held. Re-running
     # once is the minimal mechanical action, and the oracle is the verifier --
     # if the job was transient, the fault clears.
     lambda f: _remedy_rerun(f)),
    ("quarantine_orphan", ("orphan_mechanism",),
     # An orphan with no importer, no scheduler entry and no shell reference,
     # living in a scratch-named file, is safe to ISOLATE. Move, never delete.
     # Verifier: the orphan fault clears AND the scheduler target set is
     # unchanged AND the other oracles still pass.
     lambda f: _remedy_quarantine(f)),
    ("align_verdict_to_exit", ("verdict_disagreement",),
     # The scheduler recorded ok=true for a non-zero exit. The mechanical repair
     # is to make the success test agree with the exit code. This touches
     # production code, so it is applied only under a snapshot and verified by
     # re-running the oracle; a failed verification restores the snapshot.
     lambda f: _remedy_align_verdict(f)),
]


def _snapshot(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except Exception:
        return None


def _restore(path, blob):
    if blob is None:
        return
    try:
        with _permit():
            with open(path, "wb") as fh:
                fh.write(blob)
    except Exception:
        print("terence_invariants: blob write failed", file=sys.stderr)


def _remedy_rerun(fault):
    name = fault["fault_id"].split(":", 1)[-1]
    try:
        with open(DUE, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        return False, None, "cannot read due file: %s" % e
    rec = (data.get("jobs") if isinstance(data.get("jobs"), dict) else data).get(name)
    if not isinstance(rec, dict):
        return False, None, "job %r not in the due file" % name
    target = None
    try:
        with open(os.path.join(STATE_DIR, "chain_controller_plan.json"),
                  encoding="utf-8") as fh:
            target = (json.load(fh).get("jobs") or {}).get(name, {}).get("target")
    except Exception:
        pass
    if not target:
        return False, None, "no scheduler target for %r" % name
    rc, out, err = _run([os.path.join("scripts", str(target))], timeout=300)
    return True, None, "re-ran %s -> rc=%s %s" % (
        target, rc, (err or out or "")[:60].replace("\n", " "))


def _remedy_quarantine(fault):
    src = fault.get("subject") or ""
    if not os.path.isfile(src):
        return False, None, "subject is not a file"
    base = os.path.basename(src)
    # Only quarantine a scratch-named file. A named tool that nothing calls is a
    # DECISION, not a mechanical repair, and quarantining it would delete
    # capability on a guess -- which is the one thing TERENCE must not do.
    if not base.startswith(("_", "tmp", "debug", "scratch")):
        return False, None, ("%s is not scratch-named; quarantining a named "
                             "mechanism is a decision, not a repair" % base)
    dst_dir = os.path.join(QUARANTINE, os.path.basename(os.path.dirname(src)))
    dst = os.path.join(dst_dir, base)
    if os.path.exists(dst):
        return False, None, "already quarantined at %s" % dst

    def rollback():
        try:
            os.makedirs(os.path.dirname(src), exist_ok=True)
            if os.path.isfile(src):
                os.remove(src)
            with _permit():
                os.replace(dst, src)
        except Exception:
            pass

    try:
        with _permit():
            os.makedirs(dst_dir, exist_ok=True)
            os.replace(src, dst)
    except Exception as e:
        return False, None, "move failed: %s" % e
    return True, rollback, "moved %s -> %s (reversible)" % (base, dst_dir)


def _remedy_align_verdict(fault):
    """Make the scheduler's success test agree with the exit code.

    The predicate is the DEFECT SIGNATURE, not a line number. Measured on
    2026-09-30: chain_controller.py has 7 `"ok":` assignment sites, so a
    first attempt that counted them refused outright -- correctly, because it
    could not tell which one was the bug and guessing at production code is
    worse than not repairing.

    Reading the sites localised it. The defect is specifically

        "ok": bool(result.get("ok"))        <- copies ok from the result
        "ok": result.get("returncode") == 0 <- derives it from the exit code

    Copying `ok` is exactly how a job returning rc=3 lands in the due file with
    ok=true: the value is never recomputed from the evidence. So the remedy
    targets that signature, and refuses if the signature is absent or if
    rewriting it would leave no `ok` derivation in that function.
    """
    target = os.path.join(AZ, "scripts", "chain_controller.py")
    if not os.path.isfile(target):
        return False, None, "no %s" % target
    blob = _snapshot(target)
    if blob is None:
        return False, None, "cannot snapshot %s" % target
    try:
        src = blob.decode("utf-8")
    except Exception:
        return False, None, "undecodable source"
    sig = _re.compile(r"([\"']ok[\"']\s*:\s*)bool\(result\.get\([\"']ok[\"']\)\)")
    n = len(sig.findall(src))
    if n == 0:
        return False, None, ("refusing: defect signature "
                             '"ok": bool(result.get("ok")) not present')
    if n > 4:
        return False, None, ("refusing: %d copies of the signature; too many to "
                             "rewrite without a human" % n)

    def rollback():
        _restore(target, blob)

    new = sig.sub(r'\1(result.get("returncode") == 0)', src)
    changed = sum(1 for a, b in zip(src.splitlines(), new.splitlines()) if a != b)
    try:
        with _permit():
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(new)
    except Exception as e:
        _restore(target, blob)
        return False, None, "write failed: %s" % e
    return True, rollback, ("ok := (returncode == 0) at %d site(s) in %s"
                            % (changed, os.path.basename(target)))


# ── the generic loop ──────────────────────────────────────────────────────
def _oracle_for(fault):
    name = fault.get("oracle")
    for e in ORACLES:
        if e[0] == name:
            return e[1]
    return None


# Classes whose EVIDENCE IS HISTORICAL. Measured 2026-09-30: the autonomous
# loop applied the verdict fix to chain_controller.py, the change was correct,
# and verification still failed -- because the due file on disk had been written
# by the still-running OLD controller. A fault recorded in a file is a fact
# about the past. No repair to today's code can retract it; only a fresh run can
# overwrite it.
#
# Without this distinction the loop burns attempts forever on a fault that is
# already fixed in code and merely not yet re-measured. The run above spent 4
# attempts and rolled back 4, and would have kept going. So these classes are
# marked AWAITING_EVIDENCE and skipped until the evidence file is newer than the
# last attempt. This is the loop learning its own termination condition.
STALE_EVIDENCE = {"silent_crash", "verdict_disagreement"}


def _evidence_is_fresh(after_ts):
    """Is the evidence file newer than the last recorded attempt?"""
    try:
        return os.path.getmtime(DUE) > after_ts
    except Exception:
        return False


def attempt_repairs(faults, max_attempts=25, dry_run=False, unvalidated=frozenset()):
    """Generic apply -> re-run the fault's own oracle -> keep or roll back.

    The verifier is the detector that found the fault. If the fault is gone, the
    repair worked; if it is still there, the repair is rolled back and the
    attempt is recorded as failed.

    `unvalidated` is the negative-control gate. An oracle whose negative control
    FAILS has a demonstrated false-positive rate, so its findings are untrusted
    and may not license a repair -- in particular not one that writes to
    production code. Added 2026-09-30 after `verdict_disagreement` fired on a
    designed honest skip and nearly had a remedy applied to it. The refusal is
    the point: a detector that cannot demonstrate it stays quiet on known-healthy
    input is not trusted with anything destructive.
    """
    hist = _remedy_history()
    results = {"attempted": 0, "verified": 0, "rolled_back": 0, "no_remedy": 0,
               "awaiting_evidence": 0, "barred_unvalidated": 0}

    def last_attempt_ts(klass):
        t = 0.0
        for (k, _r), _v in hist.items():
            if k == klass:
                t = max(t, _LAST_TS.get((k, _r), 0.0))
        return t

    def rank(klass, rid):
        ok, n = hist.get((klass, rid), (0, 0))
        if n == 0:
            return (0, rid)          # never tried -> try first
        return (1, -ok / n)          # then by measured success rate

    for f in faults:
        if results["attempted"] >= max_attempts:
            break
        klass = f.get("klass")
        if f.get("oracle") in unvalidated:
            results["barred_unvalidated"] += 1
            _record_attempt(f["fault_id"], klass, "barred", False,
                            "oracle %r failed its negative control; findings "
                            "untrusted, destructive remedy barred"
                            % f.get("oracle"))
            continue
        if klass in STALE_EVIDENCE:
            ts = last_attempt_ts(klass)
            if ts and not _evidence_is_fresh(ts):
                results["awaiting_evidence"] += 1
                continue          # already tried; no new data to verify against
        cands = [r for r in REMEDIES if klass in r[1]]
        if not cands:
            results["no_remedy"] += 1
            _record_attempt(f["fault_id"], klass, "none", False,
                            "no mechanical remedy for this class")
            continue
        for rid, _kinds, fn in sorted(cands, key=lambda r: rank(klass, r[0])):
            if dry_run:
                _record_attempt(f["fault_id"], klass, rid, False, "dry-run")
                results["attempted"] += 1
                break
            results["attempted"] += 1
            try:
                ok, rollback, note = fn(f)
            except Exception as e:
                ok, rollback, note = False, None, "%s: %s" % (type(e).__name__, e)
            if not ok:
                _record_attempt(f["fault_id"], klass, rid, False, note)
                continue
            # verify: the fault's OWN oracle must no longer report it
            fn_oracle = _oracle_for(f)
            cleared = False
            if fn_oracle is not None:
                try:
                    again, err = fn_oracle()
                    if not err:
                        cleared = not any(
                            x["fault_id"] == f["fault_id"] for x in (again or []))
                except Exception:
                    cleared = False
            if cleared:
                results["verified"] += 1
                _record_attempt(f["fault_id"], klass, rid, True, note)
            else:
                results["rolled_back"] += 1
                if rollback:
                    rollback()
                _record_attempt(f["fault_id"], klass, rid, False,
                                "verification failed, rolled back: " + note)
            break
    return results

def _stamp_environment(faults, env):
    """PURE: tag every fault with the pillar it was observed in.

    The substrate is ONE shared error_log across all four pillars, so without
    this the same visible path -- src/CODEX/VIDEOS, forbidden on Ubuntu and the
    JOB on Windows -- is learned as a single global defect. Kept pure and apart
    from run_all so the negative control exercises it directly.
    """
    for f in faults:
        f["environment"] = env
    return faults


def run_all():
    faults, unavailable, ran = [], [], 0
    kinds = {}
    # The pillar this run is looking at. Stamped on EVERY fault, not just the
    # environment_contract ones, so the substrate can bucket any class by
    # (klass, environment): a lesson learned on Windows must not be applied to
    # Ubuntu, where sync-ubuntu.exclude deliberately forbids the same paths.
    env = _detect_environment(AZ)
    for entry in ORACLES:
        name, fn = entry[0], entry[1]
        kind = entry[2] if len(entry) > 2 else "artifact"
        kinds.setdefault(kind, []).append(name)
        try:
            f, err = fn()
        except Exception as e:
            unavailable.append((name, "%s: %s" % (type(e).__name__, e)))
            continue
        if err:
            unavailable.append((name, err))
            continue
        ran += 1
        for x in (f or []):
            x["oracle"] = name
            x["kind"] = kind
            faults.append(x)
    return _stamp_environment(faults, env), unavailable, ran, kinds



def write_ledger(faults, unavailable, ran, repaired, open_, kinds=None):
    con = sqlite3.connect(ERROR_DB, timeout=20)
    try:
        c = con.cursor()
        c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='error_log'")
        if c.fetchone():
            now = time.time()
            c.executemany(
                "INSERT INTO error_log (ts, component, message, trace_head, note) "
                "VALUES (?,?,?,?,?)",
                [(now, "terence:" + f["klass"], f"{f['fault_id']}: {f['detail'][:300]}",
                  f["subject"], f"severity={f['severity']} kind="
                  + str(f.get("kind")) + " status="
                  + ("REPAIRED" if f["fault_id"] in repaired else "OPEN")
                  + " env=" + str(f.get("environment"))
                  + " remedy=" + (f["remedy"][:60] if f.get("remedy") else "none"))
                 for f in faults])
            con.commit()
    except Exception:
        pass
    finally:
        con.close()

    # The coverage map, and its gaps, go INTO the ledger. A declared class with
    # no oracle is written as UNCOVERED rather than left out: an unlisted class
    # is invisible, a listed-and-uncovered one is at least reportable.
    covered_classes = {}
    uncovered = []
    for fid, detector, kind, repairable, desc in COVERAGE:
        entry = {"fault_id": fid, "detector": detector, "kind": kind,
                 "repairable": repairable, "description": desc}
        if detector is None:
            uncovered.append(entry)
        else:
            covered_classes[fid] = entry
    rep = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "environment": _detect_environment(AZ),
           "oracles_run": ran, "oracles_total": len(ORACLES),
           "oracle_kinds": {k: sorted(v) for k, v in (kinds or {}).items()},
           "oracles_unavailable": unavailable,
           "faults": faults, "fault_count": len(faults),
           "repaired": sorted(repaired), "open": sorted(open_),
           "coverage": {
               "declared_classes": len(COVERAGE),
               "covered": len(covered_classes),
               "uncovered_count": len(uncovered),
               "uncovered": uncovered,
               "note": "oracles_run/oracles_total is NOT a coverage figure. "
                       "It counts oracles, not error classes. See "
                       "coverage.uncovered_count for what is NOT caught.",
           },
           "rule": "no verifier, no fix"}
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    body = json.dumps(rep, indent=2, default=str)
    # Route the ledger write through the authorized path. src/CORE/
    # sentinel_enforcer.py installs a CPython audit hook that DENIES any write
    # to a state file from an ungated thread, so the first version of this
    # extension died with PermissionError. That hook is a real enforcement
    # mechanism and it is worth naming: it is `wiring`-class enforcement, and
    # it is the one rule in this system that actually stopped something today
    # without anyone remembering to run it. Bypassing it would have been the
    # exact failure mode the hook exists to prevent.
    try:
        sys.path.insert(0, os.path.join(AZ, "src"))
        from CORE.sentinel_enforcer import permit_here
        with permit_here("terence_invariants: write own ledger"):
            with open(LEDGER, "w", encoding="utf-8") as fh:
                fh.write(body)
    except Exception:
        # No enforcer on this tree: write directly rather than lose the ledger.
        with open(LEDGER, "w", encoding="utf-8") as fh:
            fh.write(body)
    return rep


def _print_environments():
    """Cross-pillar review of recorded history, bucketed by (class, environment).

    The pillar is read back OUT of the error_log note's `env=` key, so the view
    is measured from what was written, not from what we remember writing. Rows
    recorded before 2026-09-30 carry no tag and are labelled as such rather than
    guessed at.
    """
    import re
    try:
        con = sqlite3.connect(ERROR_DB, timeout=20)
        rows = con.execute(
            "SELECT component, note FROM error_log WHERE component LIKE 'terence:%'"
        ).fetchall()
        con.close()
    except Exception as e:
        print("cannot read %s: %s" % (ERROR_DB, e))
        return 1
    buckets = collections.Counter()
    untagged = 0
    for comp, note in rows:
        klass = comp.split("terence:", 1)[1] if comp.startswith("terence:") else comp
        m = re.search(r"env=(\w+)", note or "")
        env = m.group(1) if m else "(untagged:pre-2026-09-30)"
        buckets[(klass, env)] += 1
        untagged += 0 if m else 1
    print("=" * 74)
    print("TERENCE HISTORY BY (class, environment)  rows=%d  untagged=%d"
          % (len(rows), untagged))
    print("  a class seen in two pillars is NOT the same lesson: src/CODEX/VIDEOS")
    print("  is forbidden on ubuntu and the JOB on windows.\n")
    by_env = collections.Counter()
    for (klass, env), n in sorted(buckets.items()):
        print("  %-32s %-26s %5d" % (klass, env, n))
        by_env[env] += n
    print("\n  totals by environment:")
    for env, n in by_env.most_common():
        print("    %-26s %5d" % (env, n))
    return 0


def _env_audit(path):
    """READ-ONLY environment-contract check of an arbitrary tree.

    Writes nothing: no ledger, no error_log. That is what makes it safe to
    point at the G: archive, which is a source of truth and must not be mutated
    by an audit. Uses only the pure detectors.
    """
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        print("not a directory: %s" % path)
        return 2
    env = _detect_environment(path)
    base = os.path.dirname(path.rstrip("/\\"))
    print("=" * 74)
    print("ENVIRONMENT CONTRACT AUDIT (read-only)")
    print("  tree      : %s" % path)
    print("  base      : %s" % base)
    print("  detected  : %s" % env)
    if env in ENV_CONTRACT:
        print("  role      : %s" % ENV_CONTRACT[env]["why"])
    hits = _contract_violations(path, env, ENV_CONTRACT, base)
    # Absence can be the fault too: Vermillion LightFury runs in every pillar,
    # so a missing sibling is reported here exactly like a forbidden one. This
    # call is what makes the CLI agree with oracle_environment_contract; without
    # it the contract change was inert and the tool printed "0 violations" on a
    # pillar that lacks the pillar it requires.
    missing = _contract_missing(path, env, ENV_CONTRACT, base)
    # An archive legitimately holds many trees; duplicate_mirrors is a rule for
    # live pillars. Kept identical to oracle_environment_contract so the two can
    # never disagree about the same tree.
    mirrors = _mirror_roots(base) if env != "g_drive" else []
    for h in hits:
        print("  [VIOLATION] forbidden here: %s  (%s)" % (h["path"], h["abs"]))
    for m in missing:
        print("  [VIOLATION] REQUIRED but absent: %s  (%s)"
              % (m, os.path.join(base, m)))
    if len(mirrors) > 1:
        print("  [VIOLATION] %d divergent project roots under %s: %s"
              % (len(mirrors), base, ", ".join(mirrors)))
    total = len(hits) + len(missing) + (1 if len(mirrors) > 1 else 0)
    if not total:
        print("  RESULT: matches its declared role (0 violations)")
    else:
        print("  RESULT: %d violation(s)" % total)
    print("  (nothing written: this audit does not mutate the tree it reads)")
    return 1 if total else 0


CHAIN_DUE = os.path.join(AZ, "state", "chain_controller_due.json")


def _print_chain_jobs():
    """Report each chain job's LAST recorded outcome from the due file.

    Confirms honest-skip classification from what was WRITTEN, not from what the
    code intends: a job whose rc is a configured report/skip must read ok=true.
    This is how the `terence` rc=1 fix is verified once the controller re-runs.
    """
    try:
        with open(CHAIN_DUE, encoding="utf-8") as fh:
            due = json.load(fh)
    except Exception as e:
        print("cannot read %s: %s" % (CHAIN_DUE, e))
        return 1
    jobs = due.get("jobs") if isinstance(due.get("jobs"), dict) else due
    if not isinstance(jobs, dict):
        print("unexpected due-file shape")
        return 1
    skip = _load_chain_skip_rc()
    print("=" * 74)
    print("CHAIN JOBS -- last recorded outcome (%d)" % len(jobs))
    print("  ok=false && rc not in _SKIP_RC => a real failure\n")
    bad = 0
    for name in sorted(jobs):
        row = jobs[name] if isinstance(jobs[name], dict) else {}
        rc = row.get("returncode")
        ok = row.get("ok")
        carved = rc in skip.get(name, ()) if isinstance(rc, int) else False
        flag = ""
        if ok is False and not carved:
            flag = "  <-- FAILURE"
            bad += 1
        elif name == "terence" and rc == 1 and carved:
            flag = "  (rc=1 = faults FOUND, carved as a report)"
        print("  %-14s rc=%-4s ok=%-5s skip=%-5s %s%s"
              % (name, rc, ok, carved, str(row.get("finished_at", ""))[:19], flag))
    print("\n  failures (not carved): %d" % bad)
    print("  terence rc=1 carved: %s" % ("yes" if 1 in skip.get("terence", ()) else "NO"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repair", action="store_true",
                    help="run verifiers for faults that declare a remedy")
    ap.add_argument("--autonomous", action="store_true",
                    help="derive and apply mechanical remedies, verifying each by "
                         "RE-RUNNING THE FAULT'S OWN ORACLE; roll back on failure")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --autonomous: record attempts, change nothing")
    ap.add_argument("--max-attempts", type=int, default=25,
                    help="cap on autonomous repair attempts per run")
    ap.add_argument("--coverage", action="store_true",
                    help="print the coverage map and its explicit gaps, then exit")
    ap.add_argument("--self-test", action="store_true",
                    help="run every oracle's NEGATIVE CONTROL -- the known-healthy "
                         "case it must stay quiet on -- and report which oracles "
                         "are UNVALIDATED")
    ap.add_argument("--environments", action="store_true",
                    help="bucket the recorded TERENCE history by (class, "
                         "environment) for cross-pillar review")
    ap.add_argument("--env-audit", metavar="PATH",
                    help="READ-ONLY: check one tree against the environment "
                         "contract and print the result, writing nothing (safe "
                         "to point at the G: archive)")
    ap.add_argument("--chain-jobs", action="store_true",
                    help="report each chain job's last recorded outcome from the "
                         "due file, confirming honest-skip classification")
    a = ap.parse_args()

    if a.environments:
        return _print_environments()

    if a.env_audit:
        return _env_audit(a.env_audit)

    if a.chain_jobs:
        return _print_chain_jobs()

    if a.self_test:
        rows, bad = oracle_self_validation()
        print("=" * 74)
        print("ORACLE SELF-VALIDATION -- negative controls")
        print("An oracle that has not demonstrated a false-positive rate is not")
        print("trusted with anything destructive.\n")
        for r in rows:
            print("  [%s] %-22s %s" % ("PASS" if r["passed"] else "FAIL",
                                       r["oracle"], r["note"][:78]))
        print("\n  %d/%d oracles validated, %d UNVALIDATED: %s"
              % (len(rows) - len(bad), len(rows), len(bad), ", ".join(bad) or "none"))
        return 1 if bad else 0

    if a.coverage:
        print("=" * 74)
        print("TERENCE COVERAGE MAP -- what is caught, and what is NOT")
        for fid, detector, kind, repairable, desc in COVERAGE:
            mark = detector or "UNCOVERED"
            print(f"  [{mark:>28}]  {kind:<8} repair={'Y' if repairable else 'N'}  {desc}")
        gaps = [c for c in COVERAGE if c[1] is None]
        print(f"\n  {len(COVERAGE) - len(gaps)} covered, {len(gaps)} DECLARED UNCOVERED")
        print("  An unlisted class is invisible. These are listed so they are not.")
        return 0

    faults, unavailable, ran, kinds = run_all()
    repaired, open_ = [], []
    for f in faults:
        if a.repair and f.get("remedy") and f.get("verifier"):
            try:
                ok = bool(f["verifier"]())
            except Exception:
                ok = False
            (repaired if ok else open_).append(f["fault_id"])
        else:
            open_.append(f["fault_id"])

    rep = write_ledger(faults, unavailable, ran, set(repaired), set(open_), kinds)

    auto = None
    if a.autonomous:
        _rows, unval = oracle_self_validation()
        if unval:
            print("\nORACLE SELF-VALIDATION: %d UNVALIDATED -> %s"
                  % (len(unval), ", ".join(unval)))
            print("  findings from these are UNTRUSTED and barred from "
                  "licensing a destructive remedy")
        auto = attempt_repairs(faults, max_attempts=a.max_attempts,
                               dry_run=a.dry_run, unvalidated=set(unval))
        print(f"\nAUTONOMOUS: attempted={auto['attempted']} "
              f"verified={auto['verified']} rolled_back={auto['rolled_back']} "
              f"no_remedy={auto['no_remedy']} "
              f"awaiting_evidence={auto['awaiting_evidence']} "
              f"barred_unvalidated={auto['barred_unvalidated']}")
        print("  every attempt recorded to %s (the learning substrate)" % ATTEMPTS)
        # Re-run after repair so the report shows the post-repair truth, not the
        # pre-repair snapshot. A report that describes a state it just changed is
        # the exact defect this whole exercise has been about.
        faults2, unavailable2, ran2, kinds2 = run_all()
        rep = write_ledger(faults2, unavailable2, ran2, set(repaired),
                           {f["fault_id"] for f in faults2}, kinds2)
        print(f"  post-repair: {rep['fault_count']} faults (was {len(faults)})")
        rep["autonomous"] = auto

    if a.json:
        print(json.dumps(rep, indent=2, default=str))
        return 1 if faults else 0

    print("=" * 74)
    print(f"TERENCE (re-engineered)  oracles {ran}/{len(ORACLES)} run  "
          f"faults={len(faults)}")
    print(f"  kinds: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(kinds.items())))
    cov = rep["coverage"]
    print(f"  coverage: {cov['covered']}/{cov['declared_classes']} classes "
          f"covered, {cov['uncovered_count']} DECLARED UNCOVERED")
    if unavailable:
        print("  UNRUNNABLE oracles (reported, not assumed healthy):")
        for n, e in unavailable:
            print(f"    {n}: {e[:70]}")
    if not faults:
        print("  no invariant violations")
    for f in faults:
        tag = "REPAIRED" if f["fault_id"] in repaired else "OPEN"
        print(f"\n  [{tag}] {f['fault_id']}  ({f['severity']}, {f.get('kind')})")
        print(f"      {f['detail'][:220]}")
        if f.get("remedy"):
            print(f"      remedy : {f['remedy'][:100]}")
        else:
            print("      remedy : NONE -- no checkable predicate, so this is "
                  "reportable and not fixable here")
    print(f"\n  ledger: {LEDGER}")
    print(f"  rule   : {rep['rule']}")
    return 1 if faults else 0



if __name__ == "__main__":
    sys.exit(main())

