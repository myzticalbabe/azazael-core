"""VLP + metacog: record the metacognitive reflection in the signed audit trail.

The user's instruction: "you have consistently claimed things that are not true.
use the VLP and mete reflect on your errors. record all insights for new hooks to
prevent this."

This writes the reflection into the append-only, hash-chained, Ed25519-signed
audit trail at azazael/.audit/session_*.jsonl via RULE 0T's own writers, so the
record is verifiable rather than merely asserted -- which is the whole reason to
use VLP here instead of another markdown file. Every entry names the file and
line that proves the claim, because an unbacked reflection would be the same
defect it is reflecting on.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from CORE.audit_trail import (  # noqa: E402
    audit_session_start, audit_error_occurrence, audit_anomaly_scan,
    audit_file_edit, close_session,
)

OUT = []


def rec(label, value):
    OUT.append((label, value))
    print("  %-26s %s" % (label, str(value)[:96]))


def main():
    sid = audit_session_start()
    rec("session", sid)
    path = None
    for base in (".audit",):
        d = os.path.join(os.getcwd(), base)
        if os.path.isdir(d):
            cands = [os.path.join(d, f) for f in os.listdir(d)
                     if f.startswith("session_") and f.endswith(".jsonl")]
            if cands:
                path = max(cands, key=os.path.getmtime)
    rec("audit_file", os.path.basename(path or "?"))

    # ── the five false claims, each with the evidence that disproves it ──────
    #
    # 1. "verdict_disagreement is a critical bug in chain_controller.py"
    #    DISPROVED BY: scripts/chain_controller.py:107 _SKIP_RC =
    #    {"train": (1,), "tensor": (3,)} and :398-402 run_one() setting
    #    ok=True + skipped=True for exactly those codes. A nonzero exit with
    #    ok=true is the DESIGNED encoding of an honest skip. The remedy I was
    #    about to apply would have converted every by-design skip into a
    #    reported failure.
    eid = audit_error_occurrence(
        211, "I asserted a designed behaviour was a bug, and nearly 'fixed' it",
        "scripts/chain_controller.py",
        "verdict_disagreement flagged train rc=1 ok=true and tensor rc=3 ok=true "
        "as CRITICAL. _SKIP_RC at line 107 declares exactly those codes as "
        "honest skips and run_one at 398-402 sets ok=True/skipped=True for them, "
        "with the comment 'real failures never hide inside a skip'. The oracle "
        "did not know _SKIP_RC existed. The remedy align_verdict_to_exit would "
        "have rewritten ok := (returncode == 0) at lines 484 and 500, destroying "
        "that guarantee. STOPPED before applying.")
    rec("error_211", eid)

    # 2. "chain controller PID 6192 is wedged; it stopped iterating"
    #    DISPROVED BY: state/chain_controller_last_fire.json at 04:01:34 with
    #    job=render returncode=0, five seconds before the re-check.
    eid = audit_error_occurrence(
        212, "I generalised a snapshot into a diagnosis and wrote it into canon",
        "azazael/SESSION_STARTUP.md",
        "Recorded 'PID 6192 is wedged, last fire 02:36:45, 7.0 MB working set "
        "points at a wedged process' into SESSION_STARTUP.md and PMB as fact. "
        "Re-measured at 2026-09-30T04:01:34 it fired render, returncode 0, "
        "ouroboros.html written. The claim was false. A reading is a timestamp; "
        "a diagnosis is a claim; only the second needs verifying before it is "
        "written down. Corrected in place.")
    rec("error_212", eid)

    # 3. rule_coverage reported "39 of 39 rules have NO validator", including
    #    0B-A which was hooked and tested hours earlier.
    eid = audit_error_occurrence(
        213, "An oracle reported a total for an instrument that could not see",
        "tools/terence_invariants.py",
        "_validator_rule_ids() captured group 1 as the digits AFTER the 0 ('G', "
        "'H', 'A') and compared them against ids that KEEP it ('0G', '0H'), so "
        "the match set was empty and coverage reported 39 of 39 unenforced. An "
        "oracle that cannot see the thing it measures looks busy and reports a "
        "number.")
    rec("error_213", eid)

    # 4. unfailable_validators produced four false CRITICALs.
    eid = audit_error_occurrence(
        214, "Four false CRITICALs from probes that were not type-appropriate",
        "tools/terence_invariants.py",
        "Probes were prose strings. 'no gemini' contains 'no ' and lands in "
        "validate_rule_0g's N/A branch; validate_rule_0l/0m gate their False "
        "paths behind a dict input or a real audit trail; validate_rule_0i "
        "returns UNVERIFIABLE with no trail, which is CORRECT. The validators "
        "were fine. The probes were not, and the instrument accused them.")
    rec("error_214", eid)

    # 5. gate_timing false-PASSED by counting a docstring as a caller.
    eid = audit_error_occurrence(
        215, "A docstring was consumed as if it were an integration",
        "src/CORE/compliance/pre_action_gate.py",
        "The oracle grepped for the string 'sentinel_gate' and counted "
        "pre_action_gate.py as a caller, because its DOCSTRING says 'Designed "
        "for integration with azazael/src/SCRIPTS/sentinel_gate.py'. That file "
        "never imports or runs it. A substring match cannot tell a mention from "
        "an invocation; the check is now ast-based.")
    rec("error_215", eid)

    # the pattern, recorded once as an anomaly against the session
    n = audit_anomaly_scan("session_2026-09-30_reflection.jsonl")
    rec("anomaly_rows", len(n) if isinstance(n, list) else n)

    for f, d in (("tools/terence_invariants.py",
                  "add NEGATIVE_CONTROLS + oracle_self_validation(); unvalidated "
                  "oracles barred from licensing destructive remedies; "
                  "silent_crashes now reads _SKIP_RC"),
                 ("tools/terence_invariants.py",
                  "teach oracle_silent_crashes the by-design skip table, so "
                  "verdict_disagreement 2 -> 0 and silent_crash 4 -> 2"),
                 ("src/SCRIPTS/sentinel_gate.py",
                  "extract validate_rule_0i from an inline block that could not "
                  "fail; add validate_rule_0b_a with 9 tests"),
                 ("azazael/SESSION_STARTUP.md",
                  "retract the wedged-controller claim; add READ THIS FIRST "
                  "handoff and the rule-audit TODO")):
        audit_file_edit(f, d)

    close_session()
    rec("closed", "session closed and anchored")


if __name__ == "__main__":
    main()
