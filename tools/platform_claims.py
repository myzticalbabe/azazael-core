# coding: utf-8
"""platform_claims -- re-run the claims that docs/platforms makes about the tree.

WHY THIS EXISTS
---------------
A platform guide is a document about a machine, written by someone who had one
machine. Every failure in this directory so far has the same shape: a page told a
reader to run a file that does not exist (`tools/commgate.py`), to consume a
resolver that was never built (`src/CORE/accelerate`), or quoted a gate count that
had moved from 15 to 19. None of those was a typo at the time it was written --
each was true once and then drifted, and nothing in the tree re-read the page.

Prose cannot be re-run. A claim can. So every assertion in `docs/platforms/` that
names a command, a path, a value or a status is listed in
`docs/platforms/claims.json` with the thing that settles it, and this tool runs
them. The output is the difference between a guide and a recollection.

THREE OUTCOMES, AND THE THIRD ONE IS THE POINT
----------------------------------------------
    CONFIRMED     the command ran and agreed
    STALE         the command ran and disagreed  -> exit 1, fix the page or the code
    UNVERIFIABLE  it could not run (no hardware, no wheel) -> reported, never fatal

A claim that cannot run is not "passing". The `refused` kind exists so that
"documented expectation" is a declared state with a reason attached, rather than
a phrase that hides an unmeasured number. The same distinction as
`box_profile.py`'s available / unavailable / unknown: the failure mode being
prevented is a row that says "unavailable" when the truth is "never probed".

`scope` separates the two things a page can be wrong about. `repo` claims must
hold in any clone and go red when they do not. `host` claims describe THIS box
(which torch build the venv holds, whether a CUDA device is reachable) and are
expected to differ elsewhere, so they are reported as HOST-DIFFERS and do not
fail a commit on somebody else's machine.

USAGE
-----
    python tools/platform_claims.py                  # run every claim
    python tools/platform_claims.py --no-run         # schema + paths only (hook speed)
    python tools/platform_claims.py --only hooks-path-set
    python tools/platform_claims.py --json
    python tools/platform_claims.py --self-test      # the negative control

Exit: 0 clean, 1 schema defect or STALE claim, 2 the ledger itself is unreadable.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "docs" / "platforms" / "claims.json"
PLATFORM_DIR = REPO / "docs" / "platforms"

KINDS = ("command", "path", "absent", "refused")
SCOPES = ("repo", "host")
DEFAULT_TIMEOUT = 180


def load(path: Path = LEDGER) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("  REFUSED: no claims ledger at %s" % path)
        print("  A page directory with no ledger is unchecked prose. Add the file.")
        raise SystemExit(2)
    except json.JSONDecodeError as exc:
        print("  REFUSED: the ledger is not valid JSON: %s" % exc)
        raise SystemExit(2)


def _as_list(value) -> list:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def validate(ledger: dict, require_all_pages: bool = True) -> list:
    """Schema defects. Structural problems are never 'warnings'.

    `require_all_pages` is on for the real ledger and off for fixtures: a test
    that builds a two-claim ledger in memory should not be failed for the pages
    it does not mention. The gate itself never turns it off.
    """
    problems: list = []
    claims = ledger.get("claims")
    if not isinstance(claims, list) or not claims:
        return ["ledger has no `claims` list"]
    if ledger.get("schema") != "platform-claims/1":
        problems.append("schema must be 'platform-claims/1'")

    seen: set = set()
    for index, claim in enumerate(claims):
        where = claim.get("id") or "#%d" % index
        if not claim.get("id"):
            problems.append("%s: missing `id`" % where)
        elif claim["id"] in seen:
            problems.append("%s: duplicate id" % where)
        else:
            seen.add(claim["id"])

        doc = claim.get("doc")
        if not doc:
            problems.append("%s: missing `doc`" % where)
        elif not (REPO / doc).is_file():
            problems.append("%s: doc does not exist: %s" % (where, doc))

        if not claim.get("claim"):
            problems.append("%s: missing `claim` (the sentence the page asserts)" % where)

        kind = claim.get("kind")
        if kind not in KINDS:
            problems.append("%s: kind must be one of %s" % (where, ", ".join(KINDS)))
            continue
        if claim.get("scope") not in SCOPES:
            problems.append("%s: scope must be 'repo' or 'host'" % where)

        if kind == "command":
            run = _as_list(claim.get("run"))
            if not run:
                problems.append("%s: command claim with no `run` argv" % where)
            if not claim.get("expect") and claim.get("expect_exit") is None:
                problems.append(
                    "%s: command claim must give `expect` or `expect_exit`; "
                    "an exit code alone cannot distinguish the right output" % where
                )
        elif kind in ("path", "absent"):
            if not _as_list(claim.get("path")):
                problems.append("%s: %s claim with no `path`" % (where, kind))
        elif kind == "refused":
            if not claim.get("refused_because"):
                problems.append(
                    "%s: a refused claim must say `refused_because` -- unmeasured "
                    "with no reason is the defect this kind exists to prevent" % where
                )

    # Orphan pages: deleting a page's entry must not be a way to pass the gate.
    cited = {c.get("doc") for c in claims}
    for page in sorted(PLATFORM_DIR.glob("*.md")) if require_all_pages else []:
        rel = page.relative_to(REPO).as_posix()
        if rel not in cited:
            problems.append("%s: no claim cites this page (add one or delete the page)" % rel)
    return problems


def _resolve(path_text: str) -> Path:
    candidate = Path(path_text)
    return candidate if candidate.is_absolute() else (REPO / candidate)


def _run_argv(argv: list) -> list:
    """Turn a declared argv into a real command.

    A leading `-c` and a leading `tools/...` both mean "run it with the
    interpreter running this checker", because the claims are about that
    interpreter's view of the tree (py315t, not whatever `python` resolves to).
    `git` and anything else runs as written.
    """
    if not argv:
        return argv
    head = argv[0]
    if head == "-c":
        return [sys.executable, "-c", *argv[1:]]
    if head.startswith("tools/") or head.startswith("scripts/"):
        return [sys.executable, str(REPO / head), *argv[1:]]
    return list(argv)


def check_claim(claim: dict, run: bool = True, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Return {id, status, detail, scope}. Status is CONFIRMED/STALE/HOST-DIFFERS/UNVERIFIABLE."""
    claim_id = claim.get("id", "?")
    kind = claim.get("kind")
    scope = claim.get("scope", "repo")
    result = {"id": claim_id, "kind": kind, "scope": scope, "status": "UNVERIFIABLE", "detail": ""}

    if kind == "refused":
        result["detail"] = "declared unmeasured: %s" % claim.get("refused_because", "")
        return result

    if kind in ("path", "absent"):
        want_present = kind == "path"
        bad, absent_ok = [], []
        for text in _as_list(claim.get("path")):
            exists = _resolve(text).exists()
            if want_present and not exists:
                bad.append("%s missing" % text)
            elif not want_present and exists:
                bad.append("%s exists" % text)
            else:
                absent_ok.append(text)
        if bad:
            result["status"] = "STALE" if scope == "repo" else "HOST-DIFFERS"
            result["detail"] = "; ".join(bad)
        else:
            result["status"] = "CONFIRMED"
            result["detail"] = "%d path(s) as declared" % len(absent_ok)
        return result

    # kind == "command"
    if not run:
        result["detail"] = "not run (--no-run: schema and paths only)"
        return result
    argv = _run_argv(_as_list(claim.get("run")))
    try:
        completed = subprocess.run(
            argv,
            cwd=str(REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["detail"] = "could not run %s: %s" % (argv[0], type(exc).__name__)
        return result

    combined = (completed.stdout or "") + (completed.stderr or "")
    want_expect = claim.get("expect")
    want_exit = claim.get("expect_exit")
    problems = []
    if want_expect and want_expect not in combined:
        problems.append("output does not contain %r" % want_expect)
    if want_exit is not None and completed.returncode != want_exit:
        problems.append("exit %s, declared %s" % (completed.returncode, want_exit))
    if want_expect and want_exit is None and completed.returncode != 0:
        # A command that crashed can still echo its own error text; require health.
        problems.append("exit %s for a claim that only declared output" % completed.returncode)

    result["detail"] = "; ".join(problems) if problems else "output matches"
    if problems:
        result["status"] = "STALE" if scope == "repo" else "HOST-DIFFERS"
    else:
        result["status"] = "CONFIRMED"
    return result


def evaluate(ledger: dict, run: bool = True, only: str | None = None,
             require_all_pages: bool = True) -> tuple:
    problems = validate(ledger, require_all_pages=require_all_pages)
    results = []
    for claim in ledger.get("claims", []):
        if only and claim.get("id") != only:
            continue
        results.append(check_claim(claim, run=run))
    return problems, results


def report(problems: list, results: list) -> int:
    if problems:
        print("SCHEMA DEFECTS (%d)" % len(problems))
        for problem in problems:
            print("  - %s" % problem)
        print("")
    buckets = {}
    for result in results:
        buckets.setdefault(result["status"], []).append(result)
    for status in ("CONFIRMED", "HOST-DIFFERS", "UNVERIFIABLE", "STALE"):
        for result in buckets.get(status, []):
            print("  %-13s %-32s %s" % (status, result["id"], result["detail"]))
    counts = ", ".join(
        "%s %d" % (s, len(buckets.get(s, []))) for s in ("CONFIRMED", "HOST-DIFFERS", "UNVERIFIABLE", "STALE")
    )
    print("\n%d claim(s): %s" % (len(results), counts or "none"))
    stale = buckets.get("STALE", [])
    if stale:
        print("\nSTALE means the page and the tree disagree. Fix the page (or the code),")
        print("then re-run: python tools/platform_claims.py")
    return 1 if (problems or stale) else 0


def self_test() -> int:
    """The negative control: a ledger that must fail, and one that must not.

    RULE 0B-C -- an oracle ships a known-healthy fixture it must not fire on,
    plus a known-broken one it must fire on. Without both, a checker that
    returns "clean" is indistinguishable from a checker that does nothing.
    """
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "docs" / "platforms").mkdir(parents=True)
        (root / "docs" / "platforms" / "page.md").write_text("# page\n", encoding="utf-8")
        (root / "good.txt").write_text("present\n", encoding="utf-8")

        healthy = {
            "schema": "platform-claims/1",
            "claims": [
                {
                    "id": "healthy-path",
                    "doc": "docs/platforms/page.md",
                    "claim": "good.txt exists",
                    "kind": "path",
                    "scope": "repo",
                    "path": "good.txt",
                },
                {
                    "id": "healthy-absent",
                    "doc": "docs/platforms/page.md",
                    "claim": "nothing.txt does not exist",
                    "kind": "absent",
                    "scope": "repo",
                    "path": "nothing.txt",
                },
                {
                    "id": "healthy-refused",
                    "doc": "docs/platforms/page.md",
                    "claim": "unmeasurable here",
                    "kind": "refused",
                    "scope": "host",
                    "refused_because": "self-test fixture",
                },
            ],
        }
        broken = json.loads(json.dumps(healthy))
        broken["claims"].append(
            {
                "id": "broken-path",
                "doc": "docs/platforms/page.md",
                "claim": "a file that is not there",
                "kind": "path",
                "scope": "repo",
                "path": "missing-file.txt",
            }
        )
        orphan = json.loads(json.dumps(healthy))

        global REPO, PLATFORM_DIR
        saved = (REPO, PLATFORM_DIR)
        try:
            REPO = root
            PLATFORM_DIR = root / "docs" / "platforms"

            problems, results = evaluate(healthy, run=False)
            statuses = {r["id"]: r["status"] for r in results}
            if problems:
                failures.append("healthy fixture reported schema defects: %s" % problems)
            if statuses.get("healthy-refused") != "UNVERIFIABLE":
                failures.append("refused claim should be UNVERIFIABLE, got %s" % statuses.get("healthy-refused"))
            if statuses.get("healthy-absent") != "CONFIRMED":
                failures.append("absent claim on a missing path should be CONFIRMED")

            problems, results = evaluate(broken, run=False)
            statuses = {r["id"]: r["status"] for r in results}
            if statuses.get("broken-path") != "STALE":
                failures.append("broken fixture did NOT fire (status %s) -- oracle is dead" % statuses.get("broken-path"))
            if any(r["id"] == "healthy-path" and r["status"] != "CONFIRMED" for r in results):
                failures.append("healthy claim in the broken fixture did not stay clean")

            # Written only for this case: a page the ledger does not cite must be
            # reported, so that deleting an entry cannot be how a page passes.
            orphan_page = root / "docs" / "platforms" / "unclaimed.md"
            orphan_page.write_text("# orphan\n", encoding="utf-8")
            problems, _ = evaluate(orphan, run=False)
            if not any("unclaimed.md" in p for p in problems):
                failures.append("an orphan page was not reported")
            orphan_page.unlink()

            # A refused claim with no reason is the exact defect the kind prevents.
            bad_refusal = json.loads(json.dumps(healthy))
            del bad_refusal["claims"][2]["refused_because"]
            problems, _ = evaluate(bad_refusal, run=False)
            if not any("refused_because" in p for p in problems):
                failures.append("a refused claim with no reason was accepted")

            # The command path: one that must agree, one that must disagree.
            talking = {
                "schema": "platform-claims/1",
                "claims": [
                    {
                        "id": "cmd-right",
                        "doc": "docs/platforms/page.md",
                        "claim": "python prints a token",
                        "kind": "command",
                        "scope": "repo",
                        "run": ["-c", "print('token')"],
                        "expect": "token",
                    },
                    {
                        "id": "cmd-wrong",
                        "doc": "docs/platforms/page.md",
                        "claim": "python prints a token it will not print",
                        "kind": "command",
                        "scope": "repo",
                        "run": ["-c", "print('other')"],
                        "expect": "token",
                    },
                ],
            }
            problems, results = evaluate(talking, run=True)
            statuses = {r["id"]: r["status"] for r in results}
            if statuses.get("cmd-right") != "CONFIRMED":
                failures.append("command claim that agrees was not CONFIRMED")
            if statuses.get("cmd-wrong") != "STALE":
                failures.append("command claim that disagrees was not STALE")
        finally:
            REPO, PLATFORM_DIR = saved

    if failures:
        print("SELF-TEST FAILED")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("SELF-TEST PASSED: 6 case(s) -- healthy clean, broken fires, orphan named,")
    print("  unreasoned refusal refused, agreeing command confirmed, disagreeing command stale.")
    return 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="run the ledger (default action)")
    parser.add_argument("--no-run", action="store_true", help="schema and path claims only; no subprocesses")
    parser.add_argument("--only", metavar="ID", help="run one claim")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--self-test", action="store_true", help="run the negative control")
    parser.add_argument("--ledger", default=str(LEDGER), help="alternate ledger path")
    parser.add_argument(
        "--no-page-coverage",
        action="store_true",
        help="skip the 'every page must be cited' rule (fixtures and controls only)",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    ledger = load(Path(args.ledger))
    problems, results = evaluate(
        ledger, run=not args.no_run, only=args.only,
        require_all_pages=not args.no_page_coverage,
    )
    if args.json:
        print(json.dumps({"defects": problems, "results": results}, indent=1))
        stale = [r for r in results if r["status"] == "STALE"]
        return 1 if (problems or stale) else 0
    return report(problems, results)


if __name__ == "__main__":
    sys.exit(main())
