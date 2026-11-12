"""META REFLECT — run the real MetaCognitionWorm over this session's errors.

The user's instruction: "use the VLP and mete reflect on your errors."

This is not a written reflection. It feeds the five false claims of 2026-09-30
into CORE/WORMS/expansion/meta_cognition.MetaCognitionWorm as real observations
with real outcomes, then asks the worm for its own pattern analysis, confidence,
detected anomalies, and a strategy recommendation. Whatever it concludes is
recorded verbatim. If it finds less than I claim, that is the finding.

The five observations, each with the evidence that disproves the claim:

  O1  action: read _SKIP_RC before calling a designed skip a bug
      outcome: error -- did not read it; asserted verdict_disagreement was
      CRITICAL and was one step from applying a remedy that would have
      converted every by-design skip into a reported failure.
      evidence: scripts/chain_controller.py:107, :398-402

  O2  action: re-measure a runtime claim before writing it into canon
      outcome: error -- generalised one snapshot into "PID 6192 is wedged" and
      wrote it into SESSION_STARTUP.md and PMB as fact; re-measured an hour
      later it was firing normally.
      evidence: state/chain_controller_last_fire.json at 04:01:34, render rc=0

  O3  action: read an oracle's output against the source before reporting it
      outcome: error -- rule_coverage reported "39 of 39 rules have NO
      validator" for an instrument whose id-matching could not see any of them.
      evidence: tools/terence_invariants.py _validator_rule_ids()

  O4  action: validate a probe against what it must ACCEPT, not only reject
      outcome: error -- four false CRITICALs from prose probes that landed in
      validators' N/A branches; the validators were healthy, the probes were not.
      evidence: sentinel_gate.py validate_rule_0g/0l/0m/0i

  O5  action: distinguish a mention from an invocation
      outcome: error -- gate_timing counted a DOCSTRING saying "Designed for
      integration with sentinel_gate.py" as a caller, and false-passed a real
      finding.
      evidence: src/CORE/compliance/pre_action_gate.py:10

  O6  action: apply the negative-control hook before licensing a destructive
      remedy
      outcome: success -- 8 controls written; 3 oracles caught UNVALIDATED
      (silent_crashes, verdict_disagreement, gate_timing); their remedies barred;
      after teaching silent_crashes the skip table, 7/8 validated,
      verdict_disagreement 2 -> 0, faults 156 -> 152.
      evidence: tools/terence_invariants.py --self-test
"""
import json, os, sys

AZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(AZ, "src"))

from CORE.WORMS.expansion.meta_cognition import MetaCognitionWorm  # noqa: E402

WT = "assistant_claim_verification"

OBS = [
    ("read _SKIP_RC before calling a designed skip a bug", "error",
     "did not read it; asserted verdict_disagreement was CRITICAL and was one "
     "step from applying a remedy that would have converted every by-design "
     "skip into a reported failure",
     "scripts/chain_controller.py:107,398-402"),
    ("re-measure a runtime claim before writing it into canon", "error",
     "generalised one snapshot into 'PID 6192 is wedged' and wrote it into "
     "SESSION_STARTUP.md and PMB as fact; re-measured an hour later it was "
     "firing normally",
     "state/chain_controller_last_fire.json 04:01:34 render rc=0"),
    ("read an oracle's output against the source before reporting it", "error",
     "rule_coverage reported '39 of 39 rules have NO validator' for an "
     "instrument whose id-matching could not see any of them",
     "tools/terence_invariants.py _validator_rule_ids()"),
    ("validate a probe against what it must ACCEPT, not only what it rejects",
     "error",
     "four false CRITICALs from prose probes that landed in validators' N/A "
     "branches; the validators were healthy, the probes were not",
     "sentinel_gate.py validate_rule_0g/0l/0m/0i"),
    ("distinguish a mention from an invocation", "error",
     "gate_timing counted a DOCSTRING saying 'Designed for integration with "
     "sentinel_gate.py' as a caller, and false-passed a real finding",
     "src/CORE/compliance/pre_action_gate.py:10"),
    ("apply the negative-control hook before licensing a destructive remedy",
     "success",
     "8 controls written; 3 oracles caught UNVALIDATED and their remedies "
     "barred; after teaching silent_crashes the skip table 7/8 validated, "
     "verdict_disagreement 2->0, faults 156->152",
     "tools/terence_invariants.py --self-test"),
]


def main():
    mc = MetaCognitionWorm()
    print("=" * 74)
    print("META REFLECT -- MetaCognitionWorm over 2026-09-30's false claims")
    print("=" * 74)

    for action, outcome, err, ev in OBS:
        try:
            mc.observe(worm_type=WT, action=action, outcome=outcome,
                       error=err, evidence=ev, duration_ms=0.0)
            print("  observed [%s] %s" % (outcome.upper(), action[:58]))
        except TypeError:
            mc.observe(worm_type=WT, action=action, outcome=outcome)
            print("  observed [%s] %s (minimal signature)" % (outcome.upper(),
                                                              action[:44]))
        except Exception as e:
            print("  observe FAILED for %r: %s: %s" % (action[:40],
                                                       type(e).__name__, e))

    print()
    for label, fn in (("analyze_patterns", lambda: mc.analyze_patterns(WT)),
                      ("detect_anomalies", lambda: mc.detect_anomalies(WT)),
                      ("get_current_strategy", lambda: mc.get_current_strategy(WT)),
                      ("recommend_from_evidence", lambda: mc.recommend_from_evidence(WT)),
                      ("generate_introspection_report",
                       lambda: mc.generate_introspection_report())):
        print("-" * 74)
        print("%s:" % label)
        try:
            out = fn()
            txt = json.dumps(out, indent=1, default=str)
            print(txt[:1500])
        except Exception as e:
            print("  raised %s: %s" % (type(e).__name__, e))
    print()
    print("=" * 74)


if __name__ == "__main__":
    main()
