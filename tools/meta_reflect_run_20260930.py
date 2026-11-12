"""Run the REAL meta_reflect pipeline over 2026-09-30's false claims.

The user corrected me once already on this: "meta REFLECT IS DIFFERENT THAN
METACOG". They are separate chain jobs --
    reflect  -> scripts/meta_reflect.py    (this file's subject)
    metacog  -> scripts/metacog_cycle.py  (subsystem outcome observation)
and I ran metacog when asked to reflect. Both were run; this is the reflect one.

Nothing here reimplements the instrument. It imports scripts/meta_reflect.py and
calls its own distill(), validate_candidate() and store_record(), so the stored
instruction has passed the REAL validator: material, <=280 chars, evidence-linked,
and non-duplicate against the 5 instructions already in the ledger. The only
substitution is the input: gather_trajectories() reads meta_evolution/lineage.jsonl,
which knows nothing about today, so the trajectories are the five false claims
with the evidence that disproves each one.

The point of the instrument is that it distills ONE generalisation that would have
changed a CLASS of decisions. Five near-identical incidents ("I believed an
instrument because it fired") must not become five instructions.
"""
import importlib.util, json, os, sys

AZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(AZ, "scripts"))
sys.path.insert(0, os.path.join(AZ, "src"))

spec = importlib.util.spec_from_file_location(
    "meta_reflect", os.path.join(AZ, "scripts", "meta_reflect.py"))
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)

# ATTEMPT 1 fed these as five INCIDENT statements and the model's reply was a
# 500-char enumeration, which the real validator rejected: "over 280 chars (not
# an instruction, an essay)". That refusal was correct -- all five are already in
# ERROR_LOG #211-215, so storing them is duplication dressed as a lesson. The
# cause was the input, not the instrument: five restatements of one failure read
# to the model as five facts, and it answered with five facts.
#
# ATTEMPT 2 states the failure CLASS, once, and gives the other four as
# instances beneath it. That is the shape the system prompt actually asks for
# ("must generalize beyond any single incident").
TRAJS = [
    {"kind": "class", "verdict": "believed a signal instead of checking its predicate",
     "reason": "FIVE instances this session, all one failure: an instrument or a "
               "reading was trusted the moment it produced a verdict, without "
               "checking that its predicate was correct. (1) rc!=0 with ok=true "
               "called a verdict bug when _SKIP_RC chain_controller.py:107 "
               "declares train=(1,) tensor=(3,) as by-design skips; (2) one "
               "timestamp generalised to 'PID 6192 is wedged' and written to "
               "canon; (3) an id-matcher that dropped a leading zero reported "
               "'39 of 39 unenforced' including a hooked rule; (4) four probes "
               "that landed in validators' N/A branches accused four healthy "
               "validators; (5) a docstring saying 'Designed for integration "
               "with' counted as the integration. A verdict is a claim about "
               "the predicate, not about the world. Repeated across five "
               "unrelated subsystems, including one that was about to write to "
               "production code.",
     "at": "2026-09-30"},
]


def main() -> int:
    print("=" * 74)
    print("META REFLECT (scripts/meta_reflect.py) over %d false claims"
          % len(TRAJS))
    print("  model: %s via %s" % (mr.MODEL, mr.OLLAMA_URL))
    print("  existing stored instructions: %d" % len(mr.load_ledger()))
    print("=" * 74)

    try:
        parsed = mr.distill(TRAJS)
    except Exception as e:
        print("distillation unavailable (%s: %s)" % (type(e).__name__, e))
        print("-> the local model did not answer; nothing stored. NOT a pass.")
        return 2
    print("model reply: %s" % json.dumps(parsed)[:400])

    ok, reason, record = mr.validate_candidate(parsed, mr.load_ledger())
    if not ok:
        print("REJECTED by the real validator: %s" % reason)
        print("-> nothing stored. The instrument refusing is the feature.")
        return 1
    print("validated: material, %d chars, evidence-linked, non-duplicate"
          % len(record["instruction"]))
    print("  instruction: %s" % record["instruction"])
    print("  evidence   : %s" % record["evidence"])
    print("  scope      : %s" % record.get("scope"))

    info = mr.store_record(record)
    print("STORED -> %s (%d total)" % (info["path"], info["count"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
