"""HYPER_EDGE command line: isa | evolve | selftest.

``selftest`` is the point of the package. An instrument that has never been made
to fail is a hypothesis, so every one of the twelve ops is exercised with a
reading it MUST refuse, and the legacy/RC difference is proved on a split vote
rather than described in a changelog. Exit codes: 0 every check ran and passed,
1 a check failed, 2 a check could not be performed (which is not a pass).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from HYPER_EDGE import dream as dreams_mod
from HYPER_EDGE import kernel
from HYPER_EDGE import microcode
from HYPER_EDGE import mutate
from HYPER_EDGE.compat import capabilities, parallel_map, report as compat_report
from HYPER_EDGE import evolution

__all__ = ["CONTROLS", "GOOD", "corpus", "main", "selftest"]

GOOD: Dict[str, Any] = {
    "tier": "A",
    "quote_grounded": True,
    "population_claim": True,
    "n": 33,
    "m": 66,
    "negative": True,
    "universe": ["UJS", "IRS"],
    "share": 0.90,
    "instrument_answered": True,
    "truncation_class": "labelled",
    "verdict_adjacent": True,
    "command": "tools/foo.py --check",
    "digest": "EXIT=0 OK",
    "age_days": 0.5,
    "draws": 2,
    "reproduced": True,
    "knob": "arena.quorum",
    "knob_readers": 1,
    "enabled": True,
}


def _reading(**overrides: Any) -> Dict[str, Any]:
    record = dict(GOOD)
    record.update(overrides)
    return record


#: one negative control per op: (code, overrides, state the op must return, context)
CONTROLS: Tuple[Tuple[str, Dict[str, Any], str, Dict[str, Any]], ...] = (
    ("0x01", {"tier": "D"}, microcode.LEAD, {}),
    ("0x02", {"quote_grounded": False}, microcode.CUT, {}),
    ("0x03", {"n": None}, microcode.CUT, {}),
    ("0x04", {"universe": []}, microcode.CUT, {}),
    ("0x05", {"share": 0.50}, microcode.NO_QUORUM, {}),
    ("0x06", {"instrument_answered": False}, microcode.BLIND, {}),
    ("0x07", {"truncation_class": "silent", "verdict_adjacent": True}, microcode.CUT, {}),
    ("0x08", {"command": ""}, microcode.CUT, {}),
    # the staleness op is off at limit 0 by design (recorded, not enforced), so its
    # control must supply a live limit -- otherwise the control tests nothing.
    ("0x09", {"age_days": 9.0}, microcode.CUT, {"max_age_days": 1.0}),
    ("0x0A", {"reproduced": False}, microcode.LEAD, {}),
    ("0x0B", {"knob_readers": 0}, microcode.CUT, {}),
    ("0x0C", {"enabled": False}, microcode.BLIND, {}),
    # An undated artifact is not fresh and it is not hostile -- the honest answer is
    # BLIND. It was SKIP until the patch engine was pointed at this case; see
    # ARC/cain/hyper_edge_microcode_patches.jsonl for the evidence that bought it.
    ("0x09", {"age_days": None}, microcode.BLIND, {}),
)

#: What produced every case below. A case without this is refused by ``admit``.
SELFTEST_COMMAND = "PYTHONPATH=src python -m HYPER_EDGE.cli selftest"


def corpus() -> List[Dict[str, Any]]:
    """The control corpus as evidence: every case, its expected answer, its command.

    The digest is the corpus's own identity, so a patch proved against a corpus
    that has since changed is refused instead of silently re-interpreted.
    """
    cases: List[Dict[str, Any]] = [
        {"id": "%s/%02d" % (code, index), "code": code,
         "record": _reading(**overrides), "expected": expected, "ctx": dict(ctx)}
        for index, (code, overrides, expected, ctx) in enumerate(CONTROLS)
    ]
    digest = mutate.corpus_digest(cases)
    for case in cases:
        case["command"] = SELFTEST_COMMAND
        case["digest"] = digest
    return cases


class Checks:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str]] = []

    def check(self, name: str, ok: Optional[bool], detail: str) -> None:
        state = "SKIPPED" if ok is None else ("PASS" if ok else "FAIL")
        self.rows.append((state, name, detail))
        print("  [%-7s] %-34s %s" % (state, name, detail))

    def failed(self) -> int:
        return sum(1 for state, _, _ in self.rows if state == "FAIL")

    def skipped(self) -> int:
        return sum(1 for state, _, _ in self.rows if state == "SKIPPED")


def selftest(evidence: str = "", recheck: str = "") -> int:
    checks = Checks()
    print(compat_report())
    caps = capabilities()
    checks.check("compat probes", caps["probe_faults"] == 0,
                 "%s, %d probe fault(s)" % (caps["threading_mode"], caps["probe_faults"]))

    codes = [op.code for op in microcode.ISA]
    checks.check("isa has 12 unique ops", len(codes) == 12 and len(set(codes)) == 12,
                 "%d ops, %d unique" % (len(codes), len(set(codes))))
    table = microcode.isa_table()
    checks.check("every op carries its legacy behaviour",
                 all(row["legacy"] for row in table),
                 "%d/%d documented" % (sum(1 for r in table if r["legacy"]), len(table)))

    base = microcode.evaluate(_reading(quorum=0.66))
    checks.check("a complete reading reaches VERIFIED", base["rung"] == "VERIFIED",
                 "rung=%s" % base["rung"])

    by_code = {op.code: op for op in microcode.ISA}
    for code, overrides, expected, ctx in CONTROLS:
        result = microcode.evaluate(_reading(**overrides), **ctx)
        op = by_code[code]
        got = result["states"][code]
        checks.check("%s %s refuses" % (code, op.name), got == expected,
                     "%s on the control, rung=%s" % (got, result["rung"]))

    split = _reading(share=0.50)
    rc = microcode.evaluate(split)
    legacy = microcode.evaluate(split, legacy=True)
    checks.check("RC refuses a 0.50 split", rc["rung"] == "NO_QUORUM",
                 "rung=%s" % rc["rung"])
    # The precondition lives in the shared rule, so a caller cannot reach the
    # comparison with a threshold that a 3-2-1 split would meet.
    refused = "accepted"
    try:
        microcode.quorum_state(0.50, 0.50)
    except ValueError as fault:
        refused = "refused: %s" % fault
    checks.check("a quorum of 0.50 is refused at the rule", refused.startswith("refused"),
                 refused)
    checks.check("legacy mode reproduces the old coercion", legacy["rung"] == "VERIFIED",
                 "legacy rung=%s (the defect being migrated away from)" % legacy["rung"])

    # --- the self-modifying layer: an unpaid patch must not move the microcode ---
    # Two pinned cases on one knob: A wants the move, B pins the shipped answer. The
    # engine must refuse to buy A by breaking B, whatever the live policy happens to
    # be, so these controls hold before AND after a real patch is applied.
    guard = [
        {"id": "guard/A", "code": "0x0A", "record": _reading(draws=0),
         "expected": microcode.LEAD, "ctx": {}, "command": SELFTEST_COMMAND,
         "digest": "guard"},
        {"id": "guard/B", "code": "0x0A", "record": _reading(draws=0),
         "expected": microcode.SKIP, "ctx": {}, "command": SELFTEST_COMMAND,
         "digest": "guard"},
    ]
    unpaid = {"op": "0x0A", "knob": "undrawn", "old": "skip", "new": "lead",
              "corpus": mutate.corpus_digest(guard)}
    stripped = [dict(guard[0], digest=""), guard[1]]
    verdict = mutate.admit(unpaid, stripped)
    checks.check("an unnoted counterexample cannot buy a patch",
                 not verdict["admissible"] and any("unprovable" in reason
                                                   for reason in verdict["reasons"]),
                 "; ".join(verdict["reasons"])[:110])
    strict = mutate.admit(unpaid, guard)
    checks.check("a patch that would break a pinned case is refused",
                 not strict["admissible"] and any("regress" in reason
                                                  for reason in strict["reasons"]),
                 "; ".join(strict["reasons"])[:110])
    ledger = mutate.chain()
    checks.check("the patch ledger hash-chains", ledger["ok"],
                 "%d entr(ies), head=%s" % (ledger.get("entries", 0),
                                            str(ledger.get("head"))[:12]))
    live = microcode.POLICY.get("0x09", {}).get("undated")
    checks.check("the live microcode carries the admitted policy",
                 live is not None and not microcode.POLICY_FAULTS,
                 "0x09.undated=%s, %d policy fault(s)" % (live, len(microcode.POLICY_FAULTS)))

    serial = [len(text) for text in ("a", "bb", "ccc", "dddd", "eeeee", "ffffff")]
    pooled = parallel_map(lambda text: len(text), ("a", "bb", "ccc", "dddd", "eeeee", "ffffff"))
    checks.check("parallel_map preserves order", pooled == serial,
                 "%s workers=%d" % (pooled == serial, evolution.default_workers(caps)))

    path = evidence or "ARC/cain/arena_llm_judgments_r8.json"
    repath = recheck or "ARC/cain/arena_llm_judgments_r8_c4_recheck.json"
    try:
        ev = evolution.load_evidence(path)
        rc_ev = evolution.load_evidence(repath)
    except (FileNotFoundError, ValueError) as fault:
        checks.check("determinism on recorded evidence", None,
                     "cannot run: %s" % fault)
        ev = rc_ev = None
    if ev is not None and rc_ev is not None:
        first = evolution.evolve(ev, rc_ev, generations=3, population=8, seed=7)
        second = evolution.evolve(ev, rc_ev, generations=3, population=8, seed=7)
        checks.check("evolve is deterministic for a seed",
                     first["chosen"] == second["chosen"],
                     "chosen %s" % (first["chosen"] or {}).get("quorum"))
        shares = evolution.pin_shares(ev)
        band = first["band"]
        probe = evolution.score({"quorum": band["must_not_pass"],
                                 "max_age_days": 0.0, "truncation_strict": 0.0},
                                shares, evolution.pin_shares(rc_ev))
        checks.check("a quorum at the split edge is refused",
                     probe["admissible"] is False,
                     "q=%.3f verdicts=%d %s" % (probe["quorum"], probe["verdicts"],
                                                "; ".join(probe["inadmissible_reasons"])))
        checks.check("chosen genome is admissible",
                     bool(first["chosen"] and first["chosen"]["admissible"]),
                     "chosen q=%s" % (first["chosen"] or {}).get("quorum"))
        # Native adoption, proved rather than asserted: the engine's share must be
        # the judge's share, because both now resolve to the same rule.
        agree = evolution.crosscheck(ev, shares)
        checks.check("tally agrees with arena_llm_judge",
                     None if not agree["available"] else not agree["mismatches"],
                     ("%d pin(s), %d mismatch(es)%s" % (
                         agree["checked"], len(agree["mismatches"]),
                         (": " + "; ".join(agree["mismatches"])) if agree["mismatches"] else "")) if agree["available"] else "arena_llm_judge not importable")

    # A reading the instrument cannot type must be refused, not crashed on: the
    # dream that found this used "draws": [1, 2] with reproduced=true.
    # GOOD with exactly one field malformed, so the fault is the only difference.
    malformed = dict(GOOD)
    malformed.update({"draws": [1, 2], "reproduced": True})
    try:
        graded = microcode.evaluate(malformed)
    except Exception as fault:  # noqa: BLE001 - the crash is the finding
        checks.check("a malformed reading is graded, never crashed on", False,
                     "evaluate raised %s: %s" % (type(fault).__name__, fault))
    else:
        checks.check("a malformed reading is graded, never crashed on",
                     graded["rung"] == microcode.BLIND and bool(graded["faults"]),
                     "rung=%s faults=%s" % (graded["rung"], sorted(graded["faults"])))

    # A dream is a hypothesis generator, never a proof: every knob move it
    # separates must be stamped as such, and no stamp may claim admissibility.
    # A record with no age exercises 0x09, so at least one knob move separates it
    # and the check is not vacuously true on a reading that passes everything.
    dreams_probe = [{"id": "undated", "record": {k: v for k, v in GOOD.items()
                                                  if k != "age_days"}}]
    stamps = dreams_mod.discriminate(dreams_probe)
    # Kernel-backed evidence: measured against the userspace reading it replaces,
    # never assumed (the clock row proves the point -- this host's time.time() is
    # already microsecond grade, so no resolution advantage is claimed).
    proof = kernel.prove(workers=2, work_s=0.02)
    with tempfile.TemporaryDirectory() as tmp:
        artifact = Path(tmp) / "artifact.json"
        artifact.write_text('{"reading": "graded"}\n', encoding="utf-8")
        identity = kernel.file_identity(artifact)
        bound = dict(GOOD, artifact=str(artifact), artifact_id=(identity or {}).get("id") or "")
        reissued = dict(GOOD, artifact=str(artifact), artifact_id="0000000000000000:00")
        states = {}
        if identity is not None:
            states = {"bound": microcode.evaluate(bound)["states"]["0x08"],
                      "reissued": microcode.evaluate(reissued)["states"]["0x08"]}
    checks.check("kernel identity binds a receipt to the artefact it names",
                 None if not proof["available"] else (
                     proof["verdict"].startswith("NATIVE")
                     and states.get("bound") == microcode.PASS
                     and states.get("reissued") == microcode.CUT),
                 "%s, 0x08 bound=%s re-issued=%s" % (proof["verdict"], states.get("bound"),
                                                     states.get("reissued")))

    ungraded = [row["code"] for row in microcode.grades() if row["grade"] == "ungraded"]
    checks.check("every op states the evidence grade it can reach",
                 len(microcode.grades()) == len(microcode.ISA) and not ungraded,
                 "%d graded, ungraded=%s" % (len(microcode.ISA) - len(ungraded),
                                             ungraded or "none"))

    checks.check("a dream cannot buy a patch",
                 bool(stamps) and
                 all(row["verdict"] == "a_dream_is_not_evidence" for row in stamps),
                 "%d knob move(s) separated, %d stamped"
                 % (len(stamps), sum(row["verdict"] == "a_dream_is_not_evidence"
                                     for row in stamps)))

    failed, skipped = checks.failed(), checks.skipped()
    print("selftest: %d check(s), %d failed, %d skipped" %
          (len(checks.rows), failed, skipped))
    if failed:
        return 1
    return 2 if skipped else 0


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="HYPER_EDGE", description=(
        "HYPER_EDGE rc1 -- admissibility microcode + meta-evolution over recorded rows"))
    ap.add_argument("--version", action="version", version="HYPER_EDGE 0.1.0rc1")
    sub = ap.add_subparsers(dest="command")

    isa = sub.add_parser("isa", help="print the twelve admissibility ops")
    isa.add_argument("--json", action="store_true")

    st = sub.add_parser("selftest", help="exercise every op with a reading it must refuse")
    st.add_argument("--evidence", default="")
    st.add_argument("--recheck", default="")

    ev = sub.add_parser("evolve", help="evolve admissibility parameters on recorded rows")
    ev.add_argument("--evidence", required=True)
    ev.add_argument("--recheck", default="")
    ev.add_argument("--out", default="")
    ev.add_argument("--generations", type=int, default=8)
    ev.add_argument("--population", type=int, default=24)
    ev.add_argument("--seed", type=int, default=20261105)
    ev.add_argument("--shipped-quorum", type=float, default=None,
                    help="default: read arena.quorum from the settings surface")
    ev.add_argument("--apply", default="", metavar="ENV",
                    help="write the chosen overlay to config/defaults.<ENV>.json")

    validation_help = (
        "Validate preregistered quorum candidates on adjudicated held-out data. "
        "Manifest JSON requires protocol.partition='held_out', adjudicated=true, "
        "adjudicator, adjudication_protocol, independence_attested=true, "
        "independence_basis, preregistered_quorums, development_packet_sha256s; "
        "packets[] with unique packet_id/64-hex packet_sha256 and claims[]; each "
        "claim has claim_id, adjudicated_grade, and votes[] with one grade, "
        "model_id and model_family per independent family. Minimum readiness: "
        "20 unique packet clusters and 3 families."
    )
    val = sub.add_parser("validate", help="evaluate preregistered quorum candidates on held-out labels",
                         description=validation_help)
    val.add_argument("--manifest", required=True, help="adjudicated held-out manifest JSON")
    val.add_argument("--bootstrap-samples", type=int, default=1000)
    val.add_argument("--seed", type=int, default=20261105)
    val.add_argument("--json", action="store_true")

    pt = sub.add_parser("patch", help="repair the microcode from a failing case")
    pt.add_argument("--apply", action="store_true", help="persist the admitted patch")
    pt.add_argument("--env", default="", help="policy file to write (default: the base one)")
    pt.add_argument("--json", action="store_true")

    ps = sub.add_parser("patches", help="the admitted patch ledger, and its chain")
    ps.add_argument("--json", action="store_true")

    op = sub.add_parser("optimize", help="prove the microcode per environment, concurrently")
    op.add_argument("--env", default="windows,wsl,kali,rc")
    op.add_argument("--apply", default="", help="comma-separated envs to persist")
    op.add_argument("--workers", type=int, default=None)
    op.add_argument("--json", action="store_true")

    rb = sub.add_parser("rollback", help="write one patch's old value back")
    rb.add_argument("digest")
    rb.add_argument("--env", default="")

    dr = sub.add_parser("dream", help="grade adversarial readings invented by the local model")
    dr.add_argument("--count", type=int, default=4)
    dr.add_argument("--url", default="")
    dr.add_argument("--timeout", type=float, default=240.0)
    dr.add_argument("--out", default="")
    dr.add_argument("--replay", action="store_true", help="re-grade the stored dream file")
    dr.add_argument("--json", action="store_true")

    kn = sub.add_parser("kernel", help="prove the kernel-backed primitives against their userspace substitutes")
    kn.add_argument("--workers", type=int, default=4)
    kn.add_argument("--work", type=float, default=0.08)
    kn.add_argument("--out", default="")
    kn.add_argument("--baseline", default="", help="recorded par baseline to compare against")
    kn.add_argument("--write-baseline", action="store_true",
                    help="record this run's par ratio as the baseline")
    kn.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    if args.command == "kernel":
        proof = kernel.prove(workers=args.workers, work_s=args.work)
        if args.json:
            print(json.dumps(proof, indent=1))
        else:
            for row in proof["rows"]:
                print("%-58s %-9s %s" % (row["primitive"][:58], row["verdict"], row["kernel"]))
                print("%-58s %-9s %s" % ("", "userspace", row["userspace"]))
            print("verdict: %s   platform: %s   faults: %s"
                  % (proof["verdict"], proof["platform"], proof["faults"] or "none"))
        # A ratio is only evidence against something recorded; a baseline that
        # moves with every run can never show a regression.
        baseline_path = Path(args.baseline) if args.baseline else (
            Path(__file__).resolve().parents[2] / "ARC" / "cain"
            / "hyper_edge_kernel_baseline.json")
        par = next((row["detail"] for row in proof["rows"]
                    if row["primitive"].startswith("concurrency")), {})
        baseline: dict = {}
        if baseline_path.exists():
            try:
                baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                baseline = {"_unreadable": str(exc)}
                print("baseline %s unreadable: %s" % (baseline_path, exc))
        judged = kernel.compare_baseline(float(par.get("par_ratio") or 0.0), baseline)
        if args.write_baseline:
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(json.dumps(
                {"par_ratio": par.get("par_ratio"), "workers": par.get("workers"),
                 "platform": proof["platform"],
                 "at": proof.get("measured_at")}, indent=1) + "\n", encoding="utf-8")
            print("baseline written: %s (par %.2f)" % (baseline_path, par.get("par_ratio") or 0.0))
        if args.out:
            path = Path(args.out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(proof, indent=1) + "\n", encoding="utf-8")
            print("wrote %s" % path)
        print("baseline: %s" % judged["reason"])
        return 0 if proof["verdict"].startswith("NATIVE") and judged.get("ok", True) else 1
    if args.command == "isa":
        grade = {row["code"]: row["grade"] for row in microcode.grades()}
        rows = [dict(row, grade=grade.get(row["code"], "ungraded"))
                for row in microcode.isa_table()]
        if args.json:
            print(json.dumps(rows, indent=1))
        else:
            for row in rows:
                print("%s %-11s %-9s %s" % (row["code"], row["name"], row["grade"],
                                            row["question"]))
        ungraded = [row["code"] for row in rows if row["grade"] == "ungraded"]
        return 1 if ungraded else 0
    if args.command == "selftest":
        return selftest(args.evidence, args.recheck)
    if args.command == "validate":
        try:
            result = evolution.evaluate_holdout_file(
                args.manifest, bootstrap_samples=args.bootstrap_samples, seed=args.seed)
        except (OSError, ValueError, json.JSONDecodeError) as fault:
            print("INVALID HOLDOUT: %s" % fault)
            return 2
        if args.json:
            print(json.dumps(result, indent=1, sort_keys=True))
        else:
            print("held-out readiness: %s (%d packet clusters, %d independent families)" %
                  ("READY" if result["eligible"] else "NOT READY",
                   result["packet_clusters"], result["model_families"]))
            for reason in result["readiness_reasons"]:
                print("  - %s" % reason)
            for row in result["quorums"]:
                print("q=%.4f accuracy=%.3f [%.3f, %.3f] coverage=%.3f [%.3f, %.3f] "
                      "selective_accuracy=%s" %
                      (row["quorum"], row["accuracy"], *row["accuracy_ci95"],
                       row["coverage"], *row["coverage_ci95"],
                       "n/a" if row["selective_accuracy"] is None else
                       "%.3f" % row["selective_accuracy"]))
            print("quorum setting unchanged; this command reports evaluation only")
        return 0 if result["eligible"] else 1
    if args.command == "evolve":
        return evolution.main([
            "--evidence", args.evidence, "--recheck", args.recheck,
            "--out", args.out, "--generations", str(args.generations),
            "--population", str(args.population), "--seed", str(args.seed),
        ] + (["--shipped-quorum", str(args.shipped_quorum)]
             if args.shipped_quorum is not None else [])
          + (["--apply", args.apply] if args.apply else []))
    if args.command == "patch":
        cases = corpus()
        proposal = mutate.propose(cases)
        chosen = next((candidate for candidate in proposal
                       if mutate.admit(candidate, cases)["admissible"]), None)
        if chosen is None:
            if args.json:
                print(json.dumps({"applied": False, "candidates": proposal}, indent=1))
            else:
                print("no admissible patch: the corpus constrains no knob")
                for candidate in proposal:
                    print("  %s.%s=%s fixes=%s regressions=%s"
                          % (candidate["op"], candidate["knob"], candidate["new"],
                             candidate["fixes"], candidate["regressions"]))
            return 1
        try:
            entry = mutate.apply(chosen, cases, dry_run=not args.apply,
                                 env=args.env or None)
        except mutate.PatchRefused as refused:
            print("REFUSED: %s" % "; ".join(refused.reasons))
            return 1
        if args.json:
            print(json.dumps(entry, indent=1))
        else:
            print("patch %s.%s %s -> %s fixes=%s regressions=%s"
                  % (entry["op"], entry["knob"], entry["old"], entry["new"],
                     entry["fixes"], entry["regressions"]))
            print("patch=%s digest=%s %s" % (entry["patch"][:16], entry["digest"][:16],
                                             "applied" if args.apply else "dry-run"))
        return 0
    if args.command == "patches":
        entries = mutate.patches()
        ledger = mutate.chain()
        if args.json:
            print(json.dumps({"chain": ledger, "entries": entries}, indent=1))
        else:
            for entry in entries:
                print("%s %s.%s %s -> %s fixes=%s"
                      % (str(entry.get("digest"))[:12], entry.get("op"), entry.get("knob"),
                         entry.get("old"), entry.get("new"), entry.get("fixes")))
            print("chain: %s (%d entries, head %s)"
                  % ("ok" if ledger["ok"] else ledger.get("reason"),
                     ledger.get("entries", 0), str(ledger.get("head"))[:12]))
            artifacts = ledger.get("artifacts") or {}
            print("artefacts: %d bound, %d unbound, %d re-issued"
                  % (artifacts.get("bound", 0), artifacts.get("unbound", 0),
                     len(artifacts.get("reissued") or [])))
            for item in artifacts.get("reissued") or []:
                print("  entry %s (%s): recorded %s, live %s — the policy file this "
                      "patch was applied to was re-issued"
                      % (item["entry"], item["op"], item["recorded"], item["live"]))
        artifacts = ledger.get("artifacts") or {}
        return 0 if ledger["ok"] and not artifacts.get("reissued") else 1
    if args.command == "optimize":
        envs = [env.strip() for env in args.env.split(",") if env.strip()]
        cases = corpus()
        rows = mutate.optimize(envs, {env: cases for env in envs},
                               apply_envs=[env.strip() for env in args.apply.split(",") if env.strip()],
                               workers=args.workers)
        if args.json:
            print(json.dumps(rows, indent=1, default=str))
        else:
            for row in rows:
                detail = ("%s.%s %s -> %s" % (row["candidate"]["op"], row["candidate"]["knob"],
                                               row["candidate"]["old"], row["candidate"]["new"])
                          if row.get("candidate") else "; ".join(row.get("reasons") or []))
                print("%-12s %-10s %-9s %s" % (row["env"], row["status"],
                                                ("cpu %.3fs" % row["cpu_s"]
                                                 if float(row.get("cpu_s") or 0.0) >= 0.001
                                                 else "cpu unjudged") if "cpu_s" in row else "",
                                                detail))
            print("corpus: %s (shared by every environment in this run; cpu_s is kernel "
                  "thread accounting, so concurrency is measured rather than inferred)"
                  % mutate.corpus_digest(cases)[:16])
            concurrency = (rows[0].get("concurrency") if rows else {}) or {}
            if concurrency.get("judged"):
                print("concurrency: %s — %.3f cpu-s over %.3f s wall, par %.2f of %d"
                      % (concurrency.get("verdict"), concurrency.get("cpu_s", 0.0),
                         concurrency.get("wall_s", 0.0), concurrency.get("par_ratio", 0.0),
                         concurrency.get("workers", 0)))
            else:
                print("concurrency: unjudged — %.3f cpu-s over %.3f s wall is below "
                      "the kernel's thread-accounting quantum, so no ratio is quoted"
                      % (concurrency.get("cpu_s", 0.0), concurrency.get("wall_s", 0.0)))
        bad = [row for row in rows if row["status"] in ("serial", "stale")]
        for row in bad:
            print("%s: %s" % (row["env"], "; ".join(row.get("reasons") or [])))
        return 0 if not bad and all(row["status"] in ("patched", "proposed", "converged")
                                    for row in rows) else 1
    if args.command == "dream":
        target = Path(args.out) if args.out else dreams_mod.DREAM_FILE
        if args.replay:
            try:
                stored = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print("BLIND: cannot read %s: %s" % (target, exc))
                return 2
            rows = dreams_mod.replay(stored.get("dreams") or [])
        else:
            stored = dreams_mod.dream(args.count, url=args.url or None, timeout=args.timeout)
            rows = dreams_mod.replay(stored["dreams"])
            stored["replay"] = rows
            stored["discriminating"] = dreams_mod.discriminate(stored["dreams"])
            if stored["status"] in ("OK", "EMPTY") and args.count:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(stored, indent=1, sort_keys=True) + "\n",
                                   encoding="utf-8")
        if args.json:
            print(json.dumps({"status": stored.get("status"), "model": stored.get("model"),
                              "rows": rows,
                              "discriminating": stored.get("discriminating", [])}, indent=1))
        else:
            print("dreams: %s model=%s %d graded"
                  % (stored.get("status"), stored.get("model"), len(rows)))
            for row in rows:
                print("  %-18s rung=%-10s %s%s" % (row["id"], row["rung"], row["op_states"],
                                                   "  (moved from %s)" % row["was"] if row["moved"] else ""))
            for row in stored.get("discriminating", []):
                print("  hypothesis %s.%s %s -> %s separates %s"
                      % (row["op"], row["knob"], row["old"], row["new"], row["moved"]))
            for why in (stored.get("refused") or [])[:6]:
                print("  refused: %s" % why)
            if stored.get("reason"):
                print("  reason: %s" % stored["reason"])
            if not args.replay and stored.get("status") in ("OK", "EMPTY"):
                print("  stored: %s" % target)
        return 0 if stored.get("status") == "OK" else 2
    if args.command == "rollback":
        try:
            entry = mutate.rollback(args.digest, env=args.env or None)
        except mutate.PatchRefused as refused:
            print("REFUSED: %s" % "; ".join(refused.reasons))
            return 1
        print("rolled back %s: %s.%s %s -> %s"
              % (args.digest[:12], entry["op"], entry["knob"], entry["old"], entry["new"]))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
