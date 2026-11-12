#!/usr/bin/env python3
# coding: utf-8
"""SELF-MODIFY -- the mutator harness. The organ this architecture was missing.

Measured 2026-09-18: the mesh had selection (47 functions entered per cycle),
regulation (64), plasticity (13) -- and NO path by which anything it computed
could edit its own source. Variation existed only as a parameter vector
(`_fabric_variation` searches a priority vector against recorded Q), and a
mutation of a vector cannot produce a call site. So the 166 census findings --
every one a mechanism that is correct and uncalled -- could only ever be fixed
by a human editing Python.

This closes that loop with the four components recursion actually requires,
in the order that is safe: a TRUSTWORTHY TIERED EVALUATOR first (a fast tier
and a band tier, both real), then RETENTION (keep only if the evaluator does
not regress), then the LEDGER (record every attempt with its outcome so
failures accumulate into learning instead of being re-derived), and only then
AUTHORITY -- bounded, because every mutation is reverted unless it earns its
place. There is no rehearsal mode: every run is a real run, and the only thing
that stops a bad change from landing is the evaluator.

    mutate -> evaluate (tier1 compile, tier2 band) -> retain or revert -> record

The evaluator must come first and must be trustworthy: a mutator coupled to a
lying evaluator does not learn, it optimizes nonsense faster. That is not
hypothetical -- the BlackQueen was one approval from retiring EvolutionEngine,
DreamingModule, GeneticOptimizer and HypnagogicSynthesizer using a similarity
metric that scored unrelated worms identical, and it was stopped by a
human-authored quorum rule rather than by the system noticing.

Usage:
    python tools/self_modify.py --patch fix.diff --tier2 tests/test_x.py
    python tools/self_modify.py --patch fix.diff --strict        # demand improvement
    python tools/self_modify.py --ledger                         # read attempts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "logs" / "mutation_ledger.jsonl"
BAND = ("tests/test_mesh_fabric_followups.py", "tests/test_mesh_fabric_gaps.py",
        "tests/test_mesh_fabric_health.py", "tests/test_artifact_freshness.py")

#: How much of a command's output a ledger row carries. The VERDICT is read
#: from rc alone; these slices exist so a human reading the ledger can see WHY
#: a mutation failed without re-running it. Bounded because the ledger is
#: append-only, and unbounded rows are how an audit file becomes unreadable.
EVIDENCE_CHARS = 2000
#: rc written when a command does not return. An authoritative CODE is the
#: point of a failure record -- `None` reads as "unknown" to every consumer of
#: the ledger, which is the one thing a failure row must not be. 124/127 are
#: the conventions the rest of the toolbelt already speaks (code_evo_pilot).
RC_TIMEOUT = 124
RC_SPAWN_ERROR = 127

#: Rotation budget. The ledger is append-only and each row now carries up to
#: EVIDENCE_CHARS of evidence, so unbounded growth is a slow but real hazard
#: (~3KB/row). Rotation is only SAFE because every consumer reads ALL
#: generations: refresh_all.attempts() exists so a reverted attempt is not
#: re-derived, local_inference's scorecard counts verdicts per model, and
#: meta_evolution_cycle._attempted_ids() must never re-select a consumed
#: candidate. Archiving a row those readers cannot see would silently erase
#: exactly the memory the ledger exists to hold.
LEDGER_MAX_BYTES = 4_000_000
LEDGER_GENERATIONS = 5


def ledger_paths() -> list[Path]:
    """Rotated generations oldest-first, then the live ledger."""
    archived = [LEDGER.with_name("%s.%d" % (LEDGER.name, i))
                for i in range(LEDGER_GENERATIONS, 0, -1)]
    return archived + [LEDGER]


def rotate_ledger() -> str:
    """Shift generations when the live ledger is over budget.

    Returns "" for a no-op or a successful shift, else the reason it could not
    rotate. The OLDEST generation is dropped and that is the only place history
    is lost -- bounded and stated, rather than a file that grows forever.
    """
    try:
        if not LEDGER.is_file() or LEDGER.stat().st_size < LEDGER_MAX_BYTES:
            return ""
        for i in range(LEDGER_GENERATIONS, 0, -1):
            newer = (LEDGER if i == 1
                     else LEDGER.with_name("%s.%d" % (LEDGER.name, i - 1)))
            if newer.is_file():
                os.replace(newer, LEDGER.with_name("%s.%d" % (LEDGER.name, i)))
        return ""
    except OSError as exc:
        return "%s: %s" % (type(exc).__name__, str(exc)[:120])


def read_rows() -> tuple[list[dict[str, Any]], int]:
    """Every ledger row (oldest generation first) plus the torn-line count."""
    rows: list[dict[str, Any]] = []
    torn = 0
    for path in ledger_paths():
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                torn += 1  # a torn append: counted and reported, never fatal
    return rows, torn


def _text(blob: Any) -> str:
    """Normalise TimeoutExpired stdout/stderr, which stay bytes under text=True."""
    if blob is None:
        return ""
    if isinstance(blob, bytes):
        return blob.decode("utf-8", "replace")
    return str(blob)


def _slice(text: str) -> str:
    return text[-EVIDENCE_CHARS:]


def _run(cmd: Sequence[str], timeout: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # A hung command still produced output, and TimeoutExpired carries it.
        # Dropping it (the old `"out": ""`) made a mutation that wedged pytest
        # indistinguishable from one that wedged on an import error -- the row
        # said only "timeout", so the evidence died with the process.
        return {"rc": RC_TIMEOUT, "timeout": True,
                "out": _slice(_text(exc.stdout)),
                "err": _slice(_text(exc.stderr)) or "timeout"}
    except OSError as exc:
        return {"rc": RC_SPAWN_ERROR, "timeout": False, "out": "",
                "err": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    # Every branch states `timeout`, so a ledger row always has the same shape.
    # `worse_than` and `tier2_band` read it, and an audit row whose keys vary by
    # how the command ended is a row a reader has to special-case.
    return {"rc": proc.returncode, "timeout": False,
            "out": _slice(proc.stdout or ""),
            "err": _slice(proc.stderr or "")}


def _apply_subs(subs: dict[str, list], encoding: str) -> tuple[bool, str]:
    """Byte-exact substitution apply -- the default way a mutation lands.

    Preferred over `git apply` for mechanical edits because it does not care
    about `core.autocrlf`, mixed LF/CRLF worktrees, or a dirty index. A 46-file
    cap fix was rejected by `git apply` three times for end-of-line reasons
    that had nothing to do with whether the change was correct; the files are a
    genuine mix (some LF in the worktree, some CRLF), so no single git strategy
    satisfies all of them. Line endings here are preserved because the text is
    never split or joined -- only the pattern is replaced.
    """
    for rel, pairs in subs.items():
        p = ROOT / rel
        if not p.is_file():
            return False, "%s: file does not exist" % rel
        raw = p.read_bytes()
        text = raw.decode(encoding, errors="surrogateescape")
        for pair in pairs:
            old, new = pair[0], pair[1]
            if old not in text:
                return False, "%s: pattern absent: %r" % (rel, old[:40])
            text = text.replace(old, new)
        p.write_bytes(text.encode(encoding, errors="surrogateescape"))
    return True, "applied %d file(s)" % len(subs)


def tier1_compile(files: Sequence[Path]) -> dict[str, Any]:
    """Fastest tier: can the mutated source even be imported/parsed?"""
    py = [str(f) for f in files if f.suffix == ".py"]
    if not py:
        return {"ok": True, "detail": "no python files", "checked": 0}
    res = _run([sys.executable, "-m", "py_compile", *py], timeout=120)
    return {"ok": res["rc"] == 0, "checked": len(py),
            "detail": (res["err"] or "").strip()[-200:] if res["rc"] else "compiles"}


_PASSED = re.compile(r"(\d+) passed")
_FAILED = re.compile(r"(\d+) failed")
_ERRORS = re.compile(r"(\d+) error")


def tier2_band(targets: Sequence[str], timeout: int = 600) -> dict[str, Any]:
    """Behaviour tier: does the system still do what its specs say?

    Counts, not just a return code -- a mutation that 'passes' while dropping
    ten tests has regressed, and retention has to see that.
    """
    res = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                *targets], timeout=timeout)
    text = res["out"] + res["err"]
    tail = text.strip().splitlines()[-1:] or [""]
    passed = int((_PASSED.search(text) or [0, 0])[1])
    failed = int((_FAILED.search(text) or [0, 0])[1])
    errors = int((_ERRORS.search(text) or [0, 0])[1])
    return {"rc": res["rc"], "passed": passed, "failed": failed,
            "errors": errors, "timeout": bool(res.get("timeout")),
            "summary": tail[0][:160]}


# The full suite is the third tier: a band can stay green while something far
# from the mutation breaks. Opt-in because it doubles the cost -- it runs before
# the mutation to establish a baseline and again after, and the baseline is not
# optional: 73 tests fail in this suite for pre-existing reasons, so "the suite
# passed" is not a usable signal. Only the delta is.
FULL_SUITE = ("tests",)


def tier3_suite(timeout: int = 3600) -> dict[str, Any]:
    return tier2_band(FULL_SUITE, timeout)


def worse_than(new: dict[str, Any], base: dict[str, Any]) -> str:
    """The retention rule, stated once so it can be argued with.

    Returns "" when the mutation is not a regression, else the reason. A
    regression is: fewer passing, more failing/erroring, or a timeout on a
    baseline that did not time out. Equal counts are accepted: a refactor that
    changes no behaviour is not a regression, and demanding improvement would
    reject every honest cleanup.
    """
    if new.get("timeout") and not base.get("timeout"):
        return "tier2 timed out but the baseline did not"
    if new["passed"] < base["passed"]:
        return "passing tests fell %d -> %d" % (base["passed"], new["passed"])
    if new["failed"] > base["failed"]:
        return "failing tests rose %d -> %d" % (base["failed"], new["failed"])
    if new["errors"] > base["errors"]:
        return "test errors rose %d -> %d" % (base["errors"], new["errors"])
    return ""


def record(row: dict[str, Any]) -> None:
    """Append to the ledger -- the part that makes attempts cumulative.

    Without this, each cycle re-derives the same findings: there is no memory of
    'we tried wiring X, it regressed, we reverted', so nothing accumulates.

    Ledger discipline (2026-10-14) -- stated once, because the ledger is an
    audit artifact and these three rules are what keep it readable years later:

      1. every row carries a ``verdict``. Readers group by it and a row without
         one is invisible. A row is recorded on every early-return path and not
         all of them set it, so the guarantee is made here, at the one write.
      2. the row is flushed AND fsynced before the handle closes, so a crash
         mid-append cannot leave a torn line. `--ledger` used to parse every
         line with json.loads and die on the first torn one, so a single crash
         made the entire attempt history unreadable.
      3. a DirectMemory mirror failure is recorded INTO the row rather than
         swallowed: "the second record of account is down" is itself worth
         knowing, and a bare pass hides it.
    """
    row.setdefault("verdict", "unknown")
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.direct_memory import DirectMemory  # noqa: PLC0415
        DirectMemory().record("self_modify_attempt", row)
    except Exception as exc:
        # The mirror is a nice-to-have; the ledger is the record of account.
        # Record that the mirror failed rather than dropping the fact.
        row["direct_memory_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    rotate_error = rotate_ledger()
    if rotate_error:
        # Rotation is best-effort: a locked file must not stop the append.
        # Record why it did not rotate rather than swallow the failure.
        row["ledger_rotate_error"] = rotate_error
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def self_modify(patch: Path, targets: Sequence[str],
                keep_on_tie: bool = True, tier2_timeout: int = 600,
                tier3: bool = False) -> dict[str, Any]:
    started = time.time()
    patch_text = patch.read_text(encoding="utf-8", errors="replace")
    row: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "patch": str(patch),
        "patch_hash": hashlib.sha256(patch_text.encode()).hexdigest()[:16],
    }

    # Touch nothing until the pre-state is saved. Revert is byte-restore, which
    # works even on a dirty tree -- so this cannot depend on git being clean.
    subs: dict[str, list] | None = None
    if patch.suffix.lower() == ".json":
        try:
            payload = json.loads(patch_text)
        except ValueError as exc:
            row.update({"verdict": "rejected",
                        "reason": "unreadable substitution patch: %s" % exc})
            record(row)
            return row
        subs = payload.get("subs") or {}
        row["encoding"] = payload.get("encoding", "utf-8")
        row["format"] = "subs"
        touched = sorted(subs)
    else:
        row["format"] = "diff"
        touched = sorted({m.group(1).strip() for m in
                          re.finditer(r"^\+\+\+ b/(.+)$", patch_text, re.M)})
    if not touched:
        row.update({"verdict": "rejected", "reason": "patch touches no file"})
        record(row)
        return row
    saved: dict[str, bytes] = {}
    missing: list[str] = []
    for rel in touched:
        p = ROOT / rel
        if p.is_file():
            saved[rel] = p.read_bytes()
        else:
            missing.append(rel)
    row["files"] = touched

    def revert(reason: str) -> None:
        for rel, blob in saved.items():
            (ROOT / rel).write_bytes(blob)
        # A mutation that CREATES a file is reverted by deleting it. Byte-restore
        # alone would leave the new (broken) file on disk and the revert would
        # silently be partial -- so the check is `was it there before?`.
        for rel in missing:
            p = ROOT / rel
            if p.is_file():
                p.unlink()
        row["reverted_files"] = sorted(saved) + ["-" + m for m in missing]

    base = tier2_band(targets, tier2_timeout)
    row["baseline"] = base
    base3 = tier3_suite() if tier3 else None
    if base3 is not None:
        row["baseline_full"] = base3

    # core.autocrlf=false is load-bearing: with it on, git normalises the patch
    # to CRLF before matching, so a byte-faithful LF diff fails against an LF
    # worktree file. That rejected the whole 46-file cap patch twice, on a
    # different set of files each time because the normalisation only bites
    # files git considers text.
    if subs is not None:
        ok, detail = _apply_subs(subs, row["encoding"])
        row["applied"] = ok
        if not ok:
            revert("substitution failed")
            row.update({"verdict": "rejected", "reason": detail})
            record(row)
            return row
    else:
        applied = _run(["git", "-c", "core.autocrlf=false", "apply",
                        "--whitespace=nowarn", str(patch)], timeout=60)
        if applied["rc"] != 0:
            if saved:
                revert("patch failed to apply")
            row.update({"verdict": "rejected",
                        "reason": "git apply failed: %s" % (applied["err"] or "")[:200]})
            record(row)
            return row
        row["applied"] = True

    t1 = tier1_compile([ROOT / rel for rel in touched if (ROOT / rel).suffix == ".py"])
    row["tier1"] = t1
    if not t1["ok"]:
        revert("tier1")
        row.update({"verdict": "reverted", "reason": "tier1: %s" % t1["detail"]})
        record(row)
        return row

    t2 = tier2_band(targets, tier2_timeout)
    row["tier2"] = t2
    regression = worse_than(t2, base)
    if regression:
        revert("tier2")
        row.update({"verdict": "reverted", "reason": "tier2: %s" % regression})
        record(row)
        return row

    if base3 is not None:
        t3 = tier3_suite()
        row["tier3"] = t3
        regression3 = worse_than(t3, base3)
        if regression3:
            revert("tier3")
            row.update({"verdict": "reverted", "reason": "tier3: %s" % regression3})
            record(row)
            return row

    row.update({"verdict": "kept",
                "reason": "tier1 ok; tier2 no regression"
                          + (" (tie accepted)" if keep_on_tie and
                             t2["passed"] == base["passed"] else "")})
    if missing:
        row["note"] = "patch referenced files that do not exist: %s" % missing
    row["elapsed_s"] = round(time.time() - started, 2)
    record(row)
    return row


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--patch", help="unified diff to attempt")
    ap.add_argument("--tier2", nargs="*", default=list(BAND),
                    help="pytest targets for the behaviour tier")
    ap.add_argument("--strict", action="store_true",
                    help="require a strict improvement, not merely no regression")
    ap.add_argument("--tier3", action="store_true",
                    help="also evaluate against the full tests/ suite (slower, stronger)")
    ap.add_argument("--ledger", action="store_true", help="print the attempt ledger")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.ledger:
        if not any(p.is_file() for p in ledger_paths()):
            print("no attempts recorded yet")
            return 0
        rows, torn = read_rows()
        if torn:
            print("WARNING: %d unreadable ledger line(s) skipped" % torn)
        print("%-22s %-10s %-34s %s" % ("WHEN", "VERDICT", "REASON", "TIER2"))
        for r in rows[-20:]:
            t2 = r.get("tier2") or {}
            print("%-22s %-10s %-34s %s" % (
                str(r.get("ts"))[11:19], r.get("verdict", "?"),
                str(r.get("reason"))[:34],
                "passed=%s failed=%s" % (t2.get("passed"), t2.get("failed"))))
        untried = [r for r in rows if r.get("verdict") == "kept"]
        print("\n%d attempt(s), %d kept" % (len(rows), len(untried)))
        return 0

    if not args.patch:
        ap.error("--patch is required unless --ledger")
    row = self_modify(Path(args.patch), args.tier2,
                      keep_on_tie=not args.strict, tier2_timeout=600,
                      tier3=args.tier3)
    if args.json:
        print(json.dumps(row, indent=2, default=str))
    else:
        print("verdict: %s" % row.get("verdict"))
        print("reason : %s" % row.get("reason"))
        print("tier1  : %s" % (row.get("tier1") or {}).get("detail"))
        print("tier2  : baseline=%s -> %s"
              % (row.get("baseline", {}).get("passed"),
                 (row.get("tier2") or {}).get("passed")))
        if row.get("tier3"):
            print("tier3  : baseline=%s -> %s"
                  % (row.get("baseline_full", {}).get("passed"),
                     row["tier3"].get("passed")))
        print("ledger : %s" % LEDGER)
    return 0 if row.get("verdict") == "kept" else 1


if __name__ == "__main__":
    sys.exit(main())
