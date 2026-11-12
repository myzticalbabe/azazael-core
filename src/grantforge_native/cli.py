"""Command line for the executable. argparse, because it is in the standard library.

Exit codes are part of the contract:
  0  the command ran and its result is what it says
  1  a domain failure (a review with errors, a failed seal)
  2  BLIND: the RFP carried no criterion that could be read (ELIZA's exit 2)
  3  the local model could not be reached or did not answer usefully
  4  the input could not be read as JSON
A verdict of `ineligible` is a *successful* run: the tool answered the question.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, errors, grants, mcp
from . import case as case_mod
from .llm import LLMError, LocalLLM

DEMO_RFP = {
    "id": "DEMO-2026-R21",
    "funder": "Demo Institute",
    "title": "Pilot Technology Development",
    "deadline": "2026-12-15",
    "mechanism": "R21",
    "no_prior_mechanism": True,
    "max_years_post_phd": 10,
    "citizenship": "us_citizen",
    "text_criteria": [
        {"id": "institutional_support",
         "question": "Does the applicant document institutional support?"}
    ],
    "criteria_text": ("Applicants must document institutional support for the project. "
                      "The applicant must be within 10 years of the terminal degree. "
                      "Only one R21 may be held at a time."),
    "sections": {"significance": {"max_words": 300}, "approach": {"max_words": 400}},
}
DEMO_PROFILE = {
    "name": "Demo Applicant",
    "organisation": "Demo University",
    "years_post_phd": 6,
    "citizenship": "us_citizen",
    "prior_mechanisms": ["R01"],
    "keywords": ["point-of-care", "optical sensing"],
}


# The console on this host is cp1252, so a typographic dash or quote reaches the user as
# a replacement character. This is fixed once, at the boundary, instead of by hunting
# non-ASCII characters out of every message a module might ever print.
_ASCII_EQUIVALENTS = {
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ", "\u00b7": "-",
}


def ascii_safe(text):
    """Translate the punctuation cp1252 cannot carry, then replace anything left."""
    for dash, plain in _ASCII_EQUIVALENTS.items():
        text = text.replace(dash, plain)
    return text.encode("ascii", "replace").decode("ascii")


def _emit(payload, as_json, human):
    if as_json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for line in human:
            sys.stdout.write(ascii_safe(line) + "\n")
    return 0


def _write(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(target)


def _model(args):
    if getattr(args, "offline", False):
        return None
    return LocalLLM(base=getattr(args, "base", None))


def cmd_doctor(args):
    model = LocalLLM(base=args.base)
    record = model.probe(roundtrip=not args.no_roundtrip)
    human = ["endpoint   %s" % record["base"], "root       %s" % record["root"],
             "models     %d" % len(record["models"]),
             "model      %s" % (record["model"] or "-"),
             "roundtrip  %s ms" % record["roundtrip_ms"]]
    if record.get("reply"):
        human.append("reply      %r" % record["reply"])
    if model.faults:
        human.append("faults     " + "; ".join(model.faults[-3:]))
    return _emit(record, args.json, human)


def cmd_models(args):
    model = LocalLLM(base=args.base)
    listed = model.models()
    return _emit({"root": model.root, "models": listed}, args.json,
                 ["%s  %s" % (model.root, name) for name in listed] or ["no models listed"])


def _case_ready(path):
    """A --case directory must already exist. The global `input_unreadable` remedy says
    "check the path and the JSON", which sends an operator to inspect a file that was
    never written; the honest instruction is the command that creates the case."""
    if Path(path, "case.json").exists():
        return None
    return ("no case at %s - run: grantforge case init \"%s\" <rfp.json> <profile.json>"
            % (path, path))


def cmd_assess(args):
    rfp, profile = grants.load_json(args.rfp), grants.load_json(args.profile)
    result = grants.assess(rfp, profile, llm=_model(args))
    if args.seal:
        result = grants.seal(result)
    if args.case:
        problem = _case_ready(args.case)
        if problem:
            sys.stderr.write("grantforge: %s\n" % problem)
            return 4
        # The verdict enters the custody chain with the inputs that produced it.
        result["custody"] = case_mod.record(args.case, "assessment", result)["hash"]
    if args.out:
        result["out"] = _write(args.out, result)
    lines = ["status     %s" % result["status"],
             "verdict    %s  (engine: %s)" % (result["verdict"], result["engine"])]
    for row in result["rows"]:
        lines.append("  %-9s %-22s %s" % (row["verdict"], row["criterion"], row["evidence"]))
    if result.get("llm"):
        llm = result["llm"]
        lines.append("  model      %s: %d judged, %d accepted, %d downgraded"
                     % (llm.get("model"), llm["criteria"], llm["accepted"], llm["downgraded"]))
    _emit(result, args.json, lines)
    # BLIND is ELIZA's exit 2: the question could not be read, which is neither a
    # pass nor a finding about the applicant.
    return 2 if result["status"] == "BLIND" else 0


def cmd_therapy(args):
    """The therapy button: live diagnosis in, ordered treatment plan out."""
    from . import therapy as therapy_mod
    rfp, profile = grants.load_json(args.rfp), grants.load_json(args.profile)
    plan = therapy_mod.diagnose(rfp, profile, proposal=args.proposal, llm=_model(args))
    if args.case:
        problem = _case_ready(args.case)
        if problem:
            sys.stderr.write("grantforge: %s\n" % problem)
            return 4
        plan["custody"] = case_mod.record(args.case, "therapy", plan)["hash"]
    if args.out:
        plan["out"] = _write(args.out, plan)
    lines = ["prognosis  %s  (status: %s, engine: %s)"
             % (plan["prognosis"], plan["status"], plan["engine"]),
             "findings   %d error, %d warn" % (plan["counts"]["error"],
                                               plan["counts"]["warn"])]
    for item in plan["findings"]:
        lines.append("  [%s] %s @ %s" % (item["severity"], item["code"], item["where"]))
        lines.append("     cause        %s" % item["cause"])
        lines.append("     prescription %s" % item["prescription"])
        lines.append("     evidence     %s" % item["evidence_needed"])
        lines.append("     recheck      %s" % item["recheck"])
    lines.append("next       %s" % plan["next_call"])
    _emit(plan, args.json, lines)
    # 0 nothing to treat, 1 a plan was produced, 2 nothing could be read.
    if plan["prognosis"] == "BLIND":
        return 2
    return 1 if plan["findings"] else 0


def cmd_draft(args):
    if args.offline:
        sys.stderr.write("grantforge: draft needs a model; --offline writes nothing\n")
        return 4
    rfp, profile = grants.load_json(args.rfp), grants.load_json(args.profile)
    references = Path(args.references).read_text(encoding="utf-8") if args.references else ""
    result = grants.draft(rfp, profile, args.section, _model(args),
                          max_tokens=args.max_tokens, references=references)
    result = grants.seal(result)
    if args.case:
        result["custody"] = case_mod.record(args.case, "draft", result)["hash"]
    if args.out:
        result["out"] = _write(args.out, result)
    lines = ["section    %s (%d words%s)" % (result["section"], result["words"],
                                             ", limit %s" % result["limit"] if result["limit"] else ""),
             "grade      %s" % result["grade"]]
    for finding in result["findings"]:
        lines.append("  %-18s %s" % (finding["code"], finding["detail"]))
    lines.extend(["", result["text"]])
    return _emit(result, args.json, lines)


def cmd_review(args):
    proposal = grants.load_json(args.proposal)
    rfp = grants.load_json(args.rfp)
    profile = grants.load_json(args.profile) if args.profile else {}
    result = grants.review(proposal, rfp, profile)
    lines = ["review     %s (%d error(s), %d warning(s))"
             % ("ok" if result["ok"] else "fail", result["errors"], result["warnings"])]
    for finding in result["findings"]:
        lines.append("  %-8s %-18s %s: %s" % (finding["severity"], finding["code"],
                                              finding.get("section", "-"), finding["detail"]))
    _emit(result, args.json, lines)
    return 0 if result["ok"] else 1


def cmd_verify(args):
    artefact = grants.load_json(args.artefact)
    ok, detail = grants.verify_seal(artefact)
    _emit({"ok": ok, "detail": detail}, args.json,
          ["seal       %s" % ("valid" if ok else "INVALID"), "detail     %s" % detail])
    return 0 if ok else 1


def cmd_mcp(args):
    mcp.serve(offline=args.offline)
    return 0


def cmd_case(args):
    if args.action == "init":
        if not (args.rfp and args.profile):
            raise ValueError("case init needs an RFP and a profile path")
        manifest = case_mod.init_case(args.dir, {"rfp": args.rfp, "profile": args.profile},
                                    case_id=args.id)
        lines = ["case       %s" % manifest["case_id"],
                 "inputs     %d, all hashed as read" % len(manifest["inputs"])]
        for item in manifest["inputs"]:
            lines.append("  %-9s %s  %s" % (item["name"], item["sha256"][:16], item["path"]))
        return _emit(manifest, args.json, lines)
    result = case_mod.verify_case(args.dir)
    _emit(result, args.json, ["chain      %s" % ("INTACT" if result["ok"] else "BROKEN"),
                              "detail     %s" % result["detail"]])
    return 0 if result["ok"] else 1


def cmd_sections(args):
    index = grants.section_index(grants.load_json(args.rfp))
    lines = ["rfp        %s (%d section(s), %d encodable)"
             % (index["rfp"], index["count"], index["encodable"])]
    for row in index["sections"]:
        lines.append("  %-24s %-9s limit %-5s %s"
                     % (row["section"], row["state"], row["max_words"] or "-",
                        "encodable" if row["encodable"] else "NOT encodable"))
    if index["unbounded"]:
        # ASCII only: a cp1252 console turns an em dash into a replacement glyph,
        # and terminal output is part of what the operator reads.
        lines.append("  unbounded: %s - draft with care; the scan still runs, "
                     "but no word limit is enforceable" % ", ".join(index["unbounded"]))
    return _emit(index, args.json, lines)


def cmd_harness(args):
    """The operating contract, printed as the sequence an agent should follow."""
    from . import harness
    payload = harness.contract()
    lines = ["product    %s %s" % (payload["product"], payload["version"]),
             "role       %s" % payload["role"],
             "sequence   %d step(s)" % len(payload["sequence"])]
    for step in payload["sequence"]:
        lines.append("  %d. %-22s %s" % (step["step"], step["call"], step["when"]))
        lines.append("     stop if: %s" % step["stop_if"])
    lines.append("codes      %d, each with a next action" % len(payload["on_error"]))
    lines.append("invariants %d" % len(payload["invariants"]))
    return _emit(payload, args.json, lines)


def cmd_capabilities(args):
    """The seam registry: what this build declares, so evolution is enumerable."""
    payload = mcp.capabilities()
    lines = ["version    %s" % payload["version"],
             "rules      %s" % ", ".join(payload["rules"]),
             "tools      %d" % len(payload["tools"]),
             "codes      %s" % ", ".join(payload["error_codes"])]
    return _emit(payload, args.json, lines)


def cmd_errors(args):
    entries = errors.read_ledger(limit=args.limit)
    defects = errors.LAST_READ_DEFECTS
    if args.json:
        return _emit({"ledger": str(errors.ledger_path()), "entries": entries,
                      "unreadable_lines": defects}, True, [])
    if not entries:
        return _emit({}, False, ["no recorded errors at %s" % errors.ledger_path()])
    lines = ["ledger     %s" % errors.ledger_path()]
    if defects:
        lines.append("defects    %d unreadable line(s) - a writer died mid-append" % defects)
    for entry in entries:
        lines.append("  %s  %-18s %s" % (entry.get("ts", "-"), entry.get("code", "-"),
                                          (entry.get("detail") or "")[:80]))
    return _emit({}, False, lines)


def cmd_demo(args):
    assessment = grants.assess(DEMO_RFP, DEMO_PROFILE, llm=_model(args))
    proposal = {"rfp_id": DEMO_RFP["id"],
                "sections": {"significance": {"text": " ".join(
                    ["Demo University proposes a point-of-care optical sensing pilot."] * 8)}}}
    review = grants.review(proposal, DEMO_RFP, DEMO_PROFILE)
    payload = grants.seal({"assessment": assessment, "review": review,
                           "rfp": DEMO_RFP["id"], "engine": assessment["engine"]})
    lines = ["demo       %s" % DEMO_RFP["id"],
             "verdict    %s (%s)" % (assessment["verdict"], assessment["engine"]),
             "review     %s: %d error(s)" % ("ok" if review["ok"] else "fail", review["errors"]),
             "seal       %s" % payload["seal"]["digest"][:16]]
    return _emit(payload, args.json, lines)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="grantforge",
        description="GrantForge - grant assessment and drafting against a LOCAL model, "
                    "with an MCP surface any AI client can call.")
    parser.add_argument("--version", action="version", version="grantforge %s" % __version__)
    subs = parser.add_subparsers(dest="command", required=True)

    def shared(sub, offline=True):
        sub.add_argument("--json", action="store_true", help="machine-readable output")
        sub.add_argument("--base", default=None,
                         help="model endpoint (default GRANTFORGE_LLM_URL or 127.0.0.1:8081)")
        if offline:
            sub.add_argument("--offline", action="store_true",
                             help="rules only; never touch the network")

    doctor = subs.add_parser("doctor", help="probe the local model endpoint")
    shared(doctor, offline=False)
    doctor.add_argument("--no-roundtrip", action="store_true",
                        help="list models without generating a token")
    doctor.set_defaults(func=cmd_doctor)

    models = subs.add_parser("models", help="list the endpoint's models")
    shared(models, offline=False)
    models.set_defaults(func=cmd_models)

    therapy_cmd = subs.add_parser("therapy", help="live diagnosis as an ordered treatment plan")
    therapy_cmd.add_argument("rfp")
    therapy_cmd.add_argument("profile")
    therapy_cmd.add_argument("--proposal", default=None,
                             help="also treat the defects in a draft (review findings)")
    shared(therapy_cmd)
    therapy_cmd.add_argument("--out", default=None)
    therapy_cmd.add_argument("--case", default=None, help="append the plan to a case chain")
    therapy_cmd.set_defaults(func=cmd_therapy)

    assess = subs.add_parser("assess", help="eligibility verdicts for one applicant")
    assess.add_argument("rfp")
    assess.add_argument("profile")
    shared(assess)
    assess.add_argument("--out", default=None)
    assess.add_argument("--seal", action="store_true")
    assess.add_argument("--case", default=None, help="append the verdict to a case chain")
    assess.set_defaults(func=cmd_assess)

    draft = subs.add_parser("draft", help="one proposal section, scanned for unsupported claims")
    draft.add_argument("rfp")
    draft.add_argument("profile")
    draft.add_argument("section")
    shared(draft)
    draft.add_argument("--references", default=None, help="file of source material")
    draft.add_argument("--max-tokens", type=int, default=900)
    draft.add_argument("--out", default=None)
    draft.add_argument("--case", default=None, help="append the draft to a case chain")
    draft.set_defaults(func=cmd_draft)

    review = subs.add_parser("review", help="deterministic proposal review")
    review.add_argument("proposal")
    review.add_argument("rfp")
    review.add_argument("--profile", default=None)
    shared(review)
    review.set_defaults(func=cmd_review)

    verify = subs.add_parser("verify", help="recompute a sealed artefact's digest")
    verify.add_argument("artefact")
    shared(verify, offline=False)
    verify.set_defaults(func=cmd_verify)

    server = subs.add_parser("mcp", help="serve MCP over stdio (for any AI client)")
    server.add_argument("--offline", action="store_true")
    server.set_defaults(func=cmd_mcp, json=False, base=None)

    demo = subs.add_parser("demo", help="built-in RFP and applicant, no files needed")
    shared(demo)
    demo.set_defaults(func=cmd_demo)

    case = subs.add_parser("case", help="hash-chained custody: init a case, verify its chain")
    case.add_argument("action", choices=("init", "verify"))
    case.add_argument("dir")
    case.add_argument("rfp", nargs="?", default=None)
    case.add_argument("profile", nargs="?", default=None)
    case.add_argument("--id", default=None)
    case.add_argument("--json", action="store_true")
    case.set_defaults(func=cmd_case, base=None, offline=False)

    harness_cmd = subs.add_parser("harness", help="the agent operating contract, from live seams")
    harness_cmd.add_argument("--json", action="store_true")
    harness_cmd.set_defaults(func=cmd_harness, base=None, offline=False)

    sections = subs.add_parser("sections",
                               help="every section the RFP declares, with its encodability")
    sections.add_argument("rfp")
    sections.add_argument("--json", action="store_true")
    sections.set_defaults(func=cmd_sections, base=None, offline=False)

    capabilities = subs.add_parser("capabilities",
                                   help="the seam registry: rules, tools, error codes")
    capabilities.add_argument("--json", action="store_true")
    capabilities.set_defaults(func=cmd_capabilities, base=None, offline=False)

    ledger = subs.add_parser("errors", help="recent failures, with the remedy for each code")
    ledger.add_argument("--json", action="store_true")
    ledger.add_argument("--limit", type=int, default=20)
    ledger.set_defaults(func=cmd_errors, base=None, offline=False)
    return parser


def _fail(exc):
    """Print the code and its remedy, and put the failure in the ledger.

    A crash that only prints a message teaches nothing across sessions; the code is
    what turns the next occurrence into a searchable row with a known fix.
    """
    entry = errors.explain(exc)
    errors.record(entry)
    sys.stderr.write("grantforge: %s [%s]\n" % (entry["meaning"], entry["code"]))
    sys.stderr.write("  detail: %s\n  remedy: %s\n" % (entry["detail"], entry["remedy"]))
    return entry


def verbs():
    """The verbs this build registers. A prescription that names an absent verb is a
    broken instruction, so `recheck` strings are checked against this at test time."""
    action = next(a for a in build_parser()._actions if a.dest == "command")
    return tuple(action.choices)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LLMError as exc:
        _fail(exc)
        return 3
    except (ValueError, OSError) as exc:
        _fail(exc)
        return 4


if __name__ == "__main__":
    sys.exit(main())
