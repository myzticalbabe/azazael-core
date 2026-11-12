#!/usr/bin/env python3
"""Capability liveness: has the fix actually run?

WHY THIS EXISTS
---------------
Forensic 2026-09-29. The route-censoring mechanism had been shipped, reviewed,
documented, and given a test. The test was RED. And the live ledger said:

    entries with a censored counter: 0   (of 492)
    route failures on the ledger:   180  (wsl2-ubuntu 117, wsl2-kali 63)

Zero. Not low -- zero, ever. The branch existed, was reachable, and had never
executed once in production. Every instrument the project owns reported
healthy: the code was present, the artifact was fresh, the type gate counted
files, the freshness gate compared mtimes. None of them can tell the
difference between a mechanism that WORKS and a mechanism that is merely
PRESENT.

That is the gap this tool closes. The rule it enforces:

    For any capability added to suppress a failure mode, require a live
    counter that proves the failure mode stopped. A counter of zero is not
    neutral -- it is the absence of evidence that the capability ever ran.

THE FALSE-POSITIVE RULE
-----------------------
A zero counter is only a FINDING when the thing it suppresses is known to
occur. If no peer has ever been unreachable, then zero censored events is the
correct and expected answer, and reporting it as a defect would train
operators to ignore this tool. So every check pairs the capability counter
with the failure it is supposed to suppress, and stays silent when the failure
has not happened.

USAGE
-----
    python tools/capability_liveness.py            # human table, exit 1 on FAIL
    python tools/capability_liveness.py --json     # machine-readable
    python tools/capability_liveness.py --state PATH

Exit codes: 0 = every check either passed or is legitimately dormant;
1 = at least one capability is present in code and has never executed, OR the
    state store could not be read -- an unread instrument is not a passing one.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_STATE = Path.home() / ".azazael" / "spine_state.json"

OK, DORMANT, UNPROVEN, FAIL = "ok", "dormant", "unproven", "fail"

#: The state store could not be read AT ALL. Deliberately not in PASSING: an
#: instrument that cannot tell "nothing to report" from "I read nothing" is the
#: blindness this tool exists to name (registry item L-5). MEASURED 2026-10-11:
#: `_load`'s error sentinel had no reader, so a missing or unreadable store made
#: every check report `dormant`/`ok` and the whole gate exit 0.
UNREADABLE = "unreadable"

#: Statuses that do NOT fail the gate. `unproven` is the important one: the
#: ledger holds history from before the mechanism was reachable, so a zero
#: counter there says nothing about the code running now. Reporting it as a
#: hard failure would be indistinguishable from a real defect, and a gate that
#: cries wolf is a gate that gets switched off.
PASSING = (OK, DORMANT, UNPROVEN)


def _load(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        # NOT a benign missing store. `run()` inspects this sentinel and reports
        # every check UNREADABLE, because degrading to {} here is exactly what
        # made the instrument answer "dormant" and "ok" about a store it never
        # opened.
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def _homeo(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    h = state.get("homeostat")
    return h if isinstance(h, dict) else {}


def _q(state: Dict[str, Any], key: str) -> float:
    q = state.get("q_weights")
    return float((q or {}).get(key, 0.0) or 0.0)


def _n(state: Dict[str, Any], key: str) -> int:
    c = state.get("q_update_counts")
    try:
        return int((c or {}).get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


# ── the checks ─────────────────────────────────────────────────────────
#
# Each returns (status, detail). The pairing of "did the thing it suppresses
# happen?" with "did the capability fire?" is the whole point: a zero counter
# beside a non-zero failure count is a finding; a zero counter beside a zero
# failure count is dormancy.


def _after(record: Dict[str, Any], iso: str) -> bool:
    """True when the record was touched at or after `iso`."""
    try:
        bound = datetime.fromisoformat(iso)
    except ValueError:
        return False
    if bound.tzinfo is None:
        bound = bound.replace(tzinfo=timezone.utc)
    try:
        when = datetime.fromisoformat(str(record.get("last_at") or ""))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= bound


def _recent(record: Dict[str, Any], window_days: float) -> bool:
    """True when the record was touched inside the window."""
    stamp = str(record.get("last_at") or "")
    if not stamp:
        return False
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= datetime.now(timezone.utc) - timedelta(days=window_days)


def check_route_censoring(state: Dict[str, Any], window_days: float = 7.0,
                          since: str | None = None) -> tuple[str, str]:
    """Unreachable peers must be CENSORED, not scored as route failures.

    The subtlety is ATTRIBUTION. The ledger is cumulative and carries the 181
    failures recorded while the mechanism was unreachable in practice, so
    their presence proves nothing about the code running now. Only a route
    attempt made after the mechanism became reachable can.

    Pass ``--since <iso8601>`` (the moment the fix landed) and any route
    record touched after it moves this from `unproven` to a real verdict.
    Without it, a zero counter beside recent activity is reported as
    `unproven` -- needing one observation to settle, not failing.
    """
    homeo = _homeo(state)
    routes = {k: v for k, v in homeo.items() if str(k).startswith("route:")}
    censored = sum(int(v.get("censored") or 0) for v in routes.values())
    total_failures = sum(int(v.get("failures") or 0) for v in routes.values())
    if not routes:
        return DORMANT, "no route has ever been recorded"
    if censored > 0:
        return OK, (f"{censored} censored of {total_failures} failures across "
                    f"{len(routes)} routes")
    recent = [k for k, v in routes.items() if _recent(v, window_days)]
    if not recent:
        return DORMANT, (f"no route touched in {window_days:g}d; the "
                         f"{total_failures} historical failures are not "
                         f"evidence about the current code")
    if since:
        touched = [k for k, v in routes.items() if _after(v, since)]
        if touched:
            return FAIL, (
                f"route(s) {[k[6:] for k in touched[:4]]} recorded activity "
                f"after {since} with 0 censored events -- the mechanism is "
                f"reachable and still not running")
        return DORMANT, (f"no route activity since {since}; the "
                         f"{total_failures} historical failures predate it")
    return UNPROVEN, (
        f"0 censored against {total_failures} historical failures, none "
        f"attributable to the current code. Needs one unreachable peer (or "
        f"--since <iso8601>) to settle. The fix landed 2026-09-29; the "
        f"ledger predates it.")


def check_invalidation_persisted(state: Dict[str, Any]) -> tuple[str, str]:
    """An evidence reduction must survive the save it rides on.

    Every read path merges counters with max(), so a reduction is restored
    unless it was recorded as a fact. `invalidate_q()` journalled
    `q_invalidated` and emitted a spine event while the save path folded the
    old number straight back over it -- the journal counted invalidations
    that did not happen.
    """
    floor = state.get("q_evidence_floor")
    if not isinstance(floor, dict):
        # Absent on a store written before the fix. Not a failure: there is
        # nothing yet to have been restored.
        return DORMANT, "no evidence floors recorded (store predates the fix)"
    if not floor:
        return DORMANT, "no evidence has been invalidated on this env yet"
    honoured = sum(1 for k, v in floor.items() if _n(state, k) <= int(v))
    if honoured < len(floor):
        restored = [k for k, v in floor.items() if _n(state, k) > int(v)]
        return FAIL, (
            f"{len(restored)} of {len(floor)} recorded invalidations were "
            f"restored above their floor: {restored[:4]}")
    return OK, f"{honoured} evidence floors honoured"


def check_route_ledger_writers(state: Dict[str, Any]) -> tuple[str, str]:
    """A route key must carry a real latency, never a fabricated one.

    `scripts/spine_cycle_apply.py` runs HOURLY and wrote an eBPF syscall-alert
    count into `route:<src>><env>` with the 8s collection window hardcoded as
    if it were a route latency. It was the dominant writer of the live ledger.
    The signature is an exact, repeated duration: real probes are not 8000.0ms
    every other sample.
    """
    homeo = _homeo(state)
    bad = []
    judged = 0
    for k, v in homeo.items():
        if not str(k).startswith("route:"):
            continue
        hist = v.get("hist_ms")
        if not isinstance(hist, list) or len(hist) < 6:
            continue
        judged += 1
        # A real latency series is not dominated by one repeated value. A
        # hardcoded collection window IS one value, over and over: the live
        # record alternated [~95ms, 8000ms, ~98ms, 8000ms, ...] forever. The
        # distinct-count test is wrong -- the real sub-8000 values varied --
        # so the discriminator is DOMINANCE, not periodicity.
        n_eight = sum(1 for x in hist if x == 8000.0)
        if n_eight >= 3 and n_eight >= 0.4 * len(hist):
            bad.append(k[6:])
    if not bad and judged == 0:
        # Vacuous truth is not a verdict: with no latency series to inspect,
        # "no fabricated durations found" is a claim about nothing. Reporting
        # it as OK is how an instrument that reads nothing looks healthy.
        return DORMANT, ("no route record carries a latency series of >= 6 "
                         "samples, so no fabricated duration could be detected")
    if not bad:
        return OK, (f"no fabricated latency series across {judged} route "
                    f"record(s)")
    return FAIL, (
        f"route(s) with a hardcoded 8000ms latency series (fabricated "
        f"durations, not measurements): {bad}")


def check_derived_artifacts_fresh(state: Dict[str, Any] | None = None
                                  ) -> tuple[str, str]:
    """A derived map older than its sources is a confidently-wrong document."""
    root = Path(__file__).resolve().parents[1]
    try:
        sys.path.insert(0, str(root / "tools"))
        import artifact_freshness as af  # type: ignore
    except Exception as exc:
        return DORMANT, f"artifact_freshness unavailable ({type(exc).__name__})"
    # It prints a table; swallow it so this tool's own output stays parseable
    # when it is composed into a gate.
    rc: Any = None
    try:
        fn = getattr(af, "main", None)
        if callable(fn):
            with contextlib.redirect_stdout(io.StringIO()):
                rc = fn([])
    except SystemExit as exc:
        # `sys.exit()` carries code None and means SUCCESS, not "unmeasured".
        rc = 0 if exc.code is None else exc.code
    except Exception as exc:
        rc = f"raised {type(exc).__name__}"
    if rc is None:
        # MEASURED 2026-10-11: this used to return OK, because `rc is None` was
        # folded in with `rc == 0`. That asserts freshness for a check that never
        # executed -- the same blindness as a zero counter over an unread store.
        return UNPROVEN, ("artifact_freshness exposes no callable main(); "
                          "freshness is UNMEASURED, not verified")
    if rc == 0:
        return OK, "every derived artifact is newer than its sources"
    return FAIL, f"artifact_freshness reports stale derived artifacts (rc={rc})"


CHECKS = (
    ("route_censoring", check_route_censoring),
    ("invalidation_persisted", check_invalidation_persisted),
    ("route_ledger_writers", check_route_ledger_writers),
    ("derived_artifacts_fresh", check_derived_artifacts_fresh),
)


def run(state_path: Path, since: str | None = None) -> List[Dict[str, str]]:
    state = _load(state_path)
    err = state.get("__error__")
    if err:
        # ONE cause, reported per check, so a caller filtering on `status`
        # cannot read a single genuine verdict out of a store never opened.
        return [
            {"check": name, "status": UNREADABLE,
             "detail": f"state store {state_path} could not be read ({err}); "
                       f"no check can be settled"}
            for name, _fn in CHECKS
        ]
    rows: List[Dict[str, str]] = []
    for name, fn in CHECKS:
        try:
            if name == "route_censoring":
                status, detail = fn(state, since=since)
            else:
                status, detail = fn(state)
        except Exception as exc:
            status, detail = FAIL, f"check raised {type(exc).__name__}: {exc}"
        rows.append({"check": name, "status": status, "detail": detail})
    return rows


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--since", default=None,
                    help="ISO8601. Only route activity at/after this instant "
                         "counts against the censoring check, so history that "
                         "predates the fix cannot be mistaken for a live defect.")
    args = ap.parse_args(argv)

    state_path = Path(os.path.expanduser(args.state))
    rows = run(state_path, since=args.since)
    failed = [r for r in rows if r["status"] == FAIL]
    unreadable = [r for r in rows if r["status"] == UNREADABLE]
    unproven = [r for r in rows if r["status"] == UNPROVEN]

    if args.as_json:
        print(json.dumps({
            "state": str(state_path),
            "since": args.since,
            "checks": rows,
            "failed": len(failed),
            "unreadable": len(unreadable),
            "unproven": len(unproven),
        }, indent=2))
    else:
        print(f"state: {state_path}")
        print(f"{'CHECK':<26} {'STATUS':<9} DETAIL")
        for r in rows:
            print(f"{r['check']:<26} {r['status']:<9} {r['detail']}")
        print()
        if unreadable:
            print(f"UNREADABLE: {len(unreadable)} check(s) could not be settled "
                  f"because the state store could not be read. That is a "
                  f"FAILURE, not dormancy -- pass --state PATH.")
        if unproven:
            print(f"WATCH: {len(unproven)} check(s) cannot be settled from the "
                  f"current ledger -- re-run with --since to attribute.")
        if failed:
            print(f"FAIL: {len(failed)} capability/capabilities are present in "
                  f"code and have never executed.")
            print("A capability that suppresses a failure mode must leave a "
                  "live trace. Zero is not neutral.")
        elif all(r["status"] in PASSING for r in rows):
            print("OK: every checked capability either ran or is legitimately "
                  "dormant.")
    return 1 if (failed or unreadable) else 0


if __name__ == "__main__":
    raise SystemExit(main())
