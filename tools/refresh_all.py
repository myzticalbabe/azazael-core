#!/usr/bin/env python3
# coding: utf-8
"""One command to refresh every derived artifact, then audit the result.

The refresh order is the measured dependency chain, not a guess:

    mesh_function_preflight.py --verdict --propose --triage --worklist
                                           -> NEURAL_MESH_WIRE_OR_CUT.json
                                           + NEURAL_MESH_WIRE_OR_CUT_WORKLIST.md

(The census step that wrote NEURAL_MESH_FUNCTION_TODO.md was cut 2026-10-18
with the artifact: a generated probe census, not task state. `--verdict` calls
build() itself in main(), so the verdict table never consumed the census file
-- the historical "census must run first" coupling is gone with the step.)

The intermesh step (`nature_integration_map.py` -> NEURAL_MESH_INTEGRATION_MAP
.json + NEURAL_MESH_INTEROP.mmd + logs/capability_wiring_dataset.jsonl) was
dropped on 2026-10-09 together with the nature taxonomy it consumed (U3 surgery
/ D1). Its artifacts are deliberately left on disk as the forensic record of
that taxonomy -- the preflight exclusion regression asserts their filenames --
but nothing regenerates them any more, so they are out of this chain and out of
the freshness registry. The taxonomy they were built from was re-extracted from
bytecode into state/RECOVERED_NATURE_DEFINITIONS.json.

The hygiene step is a regression guard, not a clean-up: it fails on a new
duplicate basename under src/, a new file over 20 MB, or a new top-level path,
and passes on the legacy bulk that was baselined. A check that fails on facts
gets switched off, so the facts were recorded once and only deltas fail now.

The parseable-file step is the same shape, and closes a hole this chain left
open: on 2026-09-21 one HTML page saved as `.pdf` aborted a 41-minute graphify
run, because graphify dispatches on the suffix alone. This step re-checks every
parseable-binary file's magic bytes and fails only on offenders NEW since
`logs/parseable_files_baseline.json`, so a lie that would abort the next graph
pass becomes a named file here instead of a dead refresh.

The graph is *not* rebuilt here on purpose: a graphify pass is minutes long,
needs its own interpreter, and is the one artifact whose rebuild has a
side-effect budget. `--audit-only` reports its staleness instead.

Exit 0 only when the trailing freshness audit passes, so this is safe to put in
a session-startup sequence or a pre-commit hook: a stale map becomes a non-zero
exit rather than a silent input.

Usage:
    python tools/refresh_all.py               # refresh + audit
    python tools/refresh_all.py --audit-only  # audit only, no writes
    python tools/refresh_all.py --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]


def _ledger_rows() -> list[dict]:
    """Every ledger row, oldest generation first, tolerant of torn lines.

    The generation list comes from `self_modify`, which owns the rotator. This
    reader used to re-state the generation count and the path template, so it
    could silently stop seeing generations the rotator was writing -- and
    attempts() reads history so a failed attempt is not re-derived, so a reader
    that misses a generation re-derives exactly what the ledger exists to
    remember. `ledger_paths()` also reads the module globals at call time, so a
    caller that repoints the ledger for a test is now honoured here too.
    """
    tools_dir = str(ROOT / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import self_modify  # noqa: PLC0415 -- lazy, so this stays runnable as a script
    paths = self_modify.ledger_paths()
    rows: list[dict] = []
    torn = 0
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                torn += 1  # a torn append: counted, never fatal
    if torn:
        print("[ledger] %d unreadable line(s) skipped" % torn, file=sys.stderr)
    return rows


def attempts() -> dict:
    """Recent self-modification attempts -- so a failed one is not re-derived.

    The memory side of the loop. A system that forgets it already tried and
    reverted something does not learn -- it spends the same effort again and
    reaches the same conclusion. Reading the ledger at startup is what makes
    attempts cumulative instead of episodic.
    """
    empty = {"total": 0, "kept": 0, "reverted": [], "rejected": []}
    rows = _ledger_rows()
    if not rows:
        return empty
    return {"total": len(rows),
            "kept": sum(1 for r in rows if r.get("verdict") == "kept"),
            "reverted": [r for r in rows if r.get("verdict") == "reverted"],
            "rejected": [r for r in rows if r.get("verdict") == "rejected"]}


# (argv tail, human label). Order is the dependency chain.
STEPS: tuple[tuple[tuple[str, ...], str], ...] = (
    # census step cut 2026-10-18 (it wrote NEURAL_MESH_FUNCTION_TODO.md;
    # --verdict below runs build() itself and consumes no census output),
    # `--propose` fills the statuses the evidence decides and `--triage` labels
    # the ones it cannot. Without both, the table keeps the previous run's labels
    # while its findings have changed -- a reviewed row quietly described by a
    # stale class, which is the same failure ADR-010 exists to prevent for the
    # gates. Both are idempotent by test
    # (`tests/test_mesh_wire_or_cut.py::test_proposing_twice_keeps_the_evidence`).
    # `--worklist` regenerates the pending-backlog markdown from the same table:
    # 2026-09-28 forensic found spine's self-heal rebuilding every map EXCEPT
    # the worklist, so a WORKLIST-stale refusal ("still refused") could never
    # clear without a human running the trio by hand. Status unchanged.
    (("tools/mesh_function_preflight.py", "--verdict", "--propose", "--triage",
      "--worklist"),
     "verdict + triage + worklist -> NEURAL_MESH_WIRE_OR_CUT.json + WORKLIST.md"),
    (("tools/fs_hygiene.py", "--check"), "hygiene -> regression guard"),
    (("tools/audit_parseable_files.py", "--check"),
     "parseable files -> mislabel regression guard"),
)


def run_step(args: Sequence[str], timeout: int = 900) -> dict:
    """Run one refresh step; never let one failure abort the audit."""
    cmd = [sys.executable, *args]
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                              text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"cmd": " ".join(args), "rc": None, "error": "timeout"}
    tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    return {"cmd": " ".join(args), "rc": proc.returncode,
            "last": tail[0][:160], "error": (proc.stderr or "").strip()[-160:]}


def audit() -> tuple[int, dict]:
    """Run the freshness guard in-process and return (rc, parsed)."""
    sys.path.insert(0, str(ROOT / "tools"))
    import artifact_freshness as af  # noqa: PLC0415 -- local import keeps this runnable as a script

    rows = af.audit(ROOT)
    graph = af.graph_report_mismatch(ROOT)
    stale = [r for r in rows if r["stale"]]
    return (1 if stale or graph["mismatch"] else 0), {
        "artifacts": rows, "graph": graph,
        "stale_count": len(stale),
        "stale": [r["artifact"] for r in stale],
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-only", action="store_true",
                    help="report staleness without rewriting anything")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    results: list[dict] = []
    if not args.audit_only:
        for step, label in STEPS:
            res = run_step(step)
            res["label"] = label
            results.append(res)
            if not args.json:
                state = "ok" if res["rc"] == 0 else "FAILED(rc=%s)" % res["rc"]
                print("[refresh] %-14s %s  %s" % (state, label, res.get("last", "")))

    att = attempts()
    if not args.json and att["total"]:
        print("[startup] mutation ledger: %d attempt(s), %d kept, %d reverted, %d rejected"
              % (att["total"], att["kept"], len(att["reverted"]), len(att["rejected"])))
        for r in (att["reverted"] + att["rejected"])[-3:]:
            print("          %s %s -- %s" % (r.get("verdict"), r.get("patch_hash"),
                                               str(r.get("reason"))[:70]))

    rc, report = audit()
    if args.json:
        print(json.dumps({"refresh": results, "audit": report}, indent=2))
        return rc

    print()
    print("%-44s %-8s %s" % ("ARTIFACT", "STATUS", "NEWER SOURCES"))
    for row in report["artifacts"]:
        if not row["exists"]:
            status, detail = "MISSING", "-"
        elif row["stale"]:
            status, detail = "STALE", str(row["newer_count"])
        else:
            status, detail = "current", "0"
        print("%-44s %-8s %s" % (row["artifact"], status, detail))
    g = report["graph"]
    if g.get("mismatch"):
        print("\ngraph/report: MISMATCH -- %s" % g["reason"])
    elif g.get("checked"):
        print("\ngraph/report: consistent (nodes=%s clustered=%s)"
              % (g.get("nodes"), g.get("clustered")))

    failed = [r for r in results if r["rc"] != 0]
    print()
    if rc == 0 and not failed:
        print("OK: refreshed and audited -- every derived artifact is current")
        return 0
    if failed:
        print("FAIL: %d refresh step(s) failed: %s"
              % (len(failed), ", ".join(r["cmd"] for r in failed)))
        return 1
    print("FAIL: %d stale artifact(s)%s"
          % (report["stale_count"],
             ", plus a graph/report mismatch" if g.get("mismatch") else ""))
    return 1


if __name__ == "__main__":
    sys.exit(main())
