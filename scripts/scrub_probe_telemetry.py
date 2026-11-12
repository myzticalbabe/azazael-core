"""Scrub test/probe artifacts out of the production telemetry stream.

WHY THIS EXISTS (2026-09-21)
----------------------------
`_record_cluster_worm_telemetry` calls `get_memory_store()`, so a unit test that
executes a worm named `NONEXISTENT` writes a REAL execution row into the
developer's live `~/.azazael/memory_store.db`. Measured on 2026-09-21, the
production outcome tap contained:

    NONEXISTENT        36 rows, all failed
    NONEXISTENT_RETRY   9 rows, all failed
    TAPTEST             1 row

None of these are fleet executions. A learner reading per-worm reliability -
which is exactly what `CORE.WORMS.expansion.spawn_credit` now does - would grade
them as genuine failures of real components.

The ROOT CAUSE is fixed separately in `tests/conftest.py`, which now redirects
`AZAZAEL_MEMORY_DIR` to a session temp dir so a test can never write here again.
This script removes the rows already written.

SAFETY
------
`LocalMemoryStore.tombstone()` is NOT payload-preserving: it sets
`severity='deleted'` AND `data='{}'`. So this tool backs the full rows up to
JSON before touching anything, and refuses to run without `--apply`.

SECOND OCCURRENCE, DIFFERENT TAP (2026-09-21, later the same session)
---------------------------------------------------------------------
The first scrub cleaned the OUTCOME tap. The CHOICE tap had the same hole
from a different source, and it was NOT fixed by `tests/conftest.py` because
a conftest only covers its own subtree:

    pytest.ini:  testpaths = tests

`src/tests/test_worm_registry.py` defines fixture worm classes (`FleetOrch`,
`NetDiag`, `DivWorm`, `SysMonitor`, `ClockSync`, `BinAnalysis`, `BatchWorm2`,
`BatchWorm4`, `ShortCooldown`, `MonWorm`, `DiagWorm`, `OnlyWorm`) and calls
`SpawnDiversityEngine.record_spawn(...)`, which writes a REAL `spawn_selected`
row through `get_memory_store()`. Measured on 2026-09-21:

    spawn_selected rows: 608
      408 from those fixtures (12 names, 0 of them registered worms)
      200 from MetaCluster, the only real component ever chosen

So ~67% of the production "which worm did we choose" record was unit test
fixtures. `spawn_credit.chosen_but_never_run()` reads that tap, and the
morphogenesis opportunity trigger reads THAT - so the system was about to
build specialists for twelve worms that do not exist.

The root cause is fixed in the new rootdir `conftest.py`, which covers every
test directory rather than only `tests/`. Verified: running the polluting file
with the redirect in place leaves the live store byte-for-byte unchanged
(608/4240 before and after), and without it the same file writes 24 rows.

`--unregistered` is the principled predicate for this tap: a real choice can
only name a component the registry actually has, so "chosen" and "not a
registered worm" is self-contradictory rather than a hand-maintained list.

Usage:
    python scripts/scrub_probe_telemetry.py                 # dry run (default)
    python scripts/scrub_probe_telemetry.py --apply         # back up + tombstone
    python scripts/scrub_probe_telemetry.py --names Foo,Bar --apply
    python scripts/scrub_probe_telemetry.py --unregistered  # dry run, choice tap
    python scripts/scrub_probe_telemetry.py --unregistered --apply
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Confirmed artifacts, each traced to its source on 2026-09-21:
#   NONEXISTENT / NONEXISTENT_RETRY -> tests/test_clusterworms_unit.py,
#                                      tests/test_parallel_integration.py
#   TAPTEST                         -> an ad-hoc probe of the telemetry tap
DEFAULT_PROBE_NAMES = ("NONEXISTENT", "NONEXISTENT_RETRY", "TAPTEST")

# Taps that carry a per-worm name, in the order they are scanned.
TAPS = ("clusterwormz_worm_executed", "spawn_selected", "nature_cluster_executed")

PAGE = 500          # LocalMemoryStore.MAX_QUERY_LIMIT
MAX_PAGES = 200     # 100k rows; beyond this the scan is a sample and says so


def _page_all(store, event_type):
    """Read a whole tap, paginating past the store's one-query clamp."""
    rows, offset, pages = [], 0, 0
    while pages < MAX_PAGES:
        batch = store.query_memories(event_type=event_type, limit=PAGE,
                                     offset=offset)
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        pages += 1
        if len(batch) < PAGE:
            break
    return rows


def _worm_of(row):
    """The worm name named by a row, whichever field its producer used."""
    data = row.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return None  # scanner:ignore -- probe contract: unparseable payloads fall outside the census; None marks the row uncounted
    if not isinstance(data, dict):
        return None
    return data.get("worm") or data.get("worm_type")


def _is_registered(name):
    """Is this a component the registry actually has?

    Imported lazily and discovered once: `discover_worms()` is the same call the
    running system makes, so membership here means the same thing it means at
    dispatch time.
    """
    global _REGISTERED
    if _REGISTERED is None:
        try:
            from CORE.worm_registry import WORM_REGISTRY, discover_worms
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
            from CORE.worm_registry import WORM_REGISTRY, discover_worms
        try:
            discover_worms()
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: discover_worms() failed ({type(exc).__name__}); "
                  f"registry membership may be incomplete", file=sys.stderr)
        # Read AFTER discovery: `WORM_REGISTRY` is a live dict the decorator
        # populates, so snapshotting before the scan would find nothing.
        _REGISTERED = set(WORM_REGISTRY.keys())
    return name in _REGISTERED


# Populated on first use by `_is_registered`.
_REGISTERED = None

_EXECUTED = None


def _executed_names(store):
    """Names that have EVER produced an execution row.

    This guards the phantom predicate, and it exists because the dry run of a
    registry-membership-only version flagged `SYSTEMZ` - 1246 execution rows,
    1236 of them successful, the single busiest component in the fleet - as
    "not in the live registry". It is a real component registered by a path
    that does not populate `WORM_REGISTRY`; `discover_worms()` returned only
    232 names. Applying that earlier version would have tombstoned the 
    strongest evidence in the store.

    So "phantom" means not registered AND never executed. A name that has run
    is a live component by definition - and a fixture cannot have run, because
    running it is what would have registered it.
    """
    global _EXECUTED
    if _EXECUTED is None:
        names = set()
        try:
            for row in _page_all(store, "clusterwormz_worm_executed"):
                name = _worm_of(row)
                if name:
                    names.add(name)
        except Exception as exc:  # noqa: BLE001
            # Cannot prove anything without this guard, so say so and let the
            # caller refuse. Returning an empty set here would make every
            # name look unexecuted - the over-match direction, i.e. data loss.
            print(f"  WARNING: could not read the outcome tap "
                  f"({type(exc).__name__}); the executed-guard is unavailable",
                  file=sys.stderr)
            _EXECUTED = _UNREADABLE
        else:
            _EXECUTED = names
    return _EXECUTED


class _Unreadable:
    """Sentinel: the outcome tap could not be read, so nothing can be proven."""

    def __contains__(self, _item):
        return True  # treat every candidate as executed -> match nothing

    def __len__(self):
        return 0


_UNREADABLE = _Unreadable()


def _sidecar_path():
    state = Path(__file__).resolve().parents[1] / "state"
    state.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return state / f"probe_telemetry_backup_{stamp}.json"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--names", default=",".join(DEFAULT_PROBE_NAMES),
                    help="Comma-separated probe worm names to remove")
    ap.add_argument("--unregistered", action="store_true",
                    help="Instead of --names, match any row naming a worm that is "
                         "NOT in the live registry (the choice-tap predicate)")
    ap.add_argument("--taps", default=None,
                    help="Comma-separated taps to scan (default: all taps, or "
                         "just the choice tap for --unregistered)")
    ap.add_argument("--apply", action="store_true",
                    help="Back up and tombstone (default is a dry run)")
    args = ap.parse_args(argv)

    names = set() if args.unregistered else {
        n.strip() for n in args.names.split(",") if n.strip()}
    if not names and not args.unregistered:
        print("no names to scrub", file=sys.stderr)
        return 2
    if args.taps:
        taps = tuple(t.strip() for t in args.taps.split(",") if t.strip())
    elif args.unregistered:
        # The phantom predicate is a statement about a CHOICE ("we chose a
        # worm the registry does not have"), which is only meaningful on the
        # choice tap. Defaulting it to every tap is how it flagged SYSTEMZ.
        taps = ("spawn_selected",)
    else:
        taps = TAPS

    try:
        from CORE.local_memory import get_memory_store
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from CORE.local_memory import get_memory_store

    store = get_memory_store()
    print(f"store: {store.db_path if hasattr(store, 'db_path') else '(unknown)'}")
    if args.unregistered:
        print("predicate: rows naming a worm NOT in the live registry")
    else:
        print(f"probe names: {', '.join(sorted(names))}")
    print(f"taps: {', '.join(taps)}\n")

    # The executed-guard is only needed for the phantom predicate, and it is
    # mandatory there: see `_executed_names` for the SYSTEMZ near-miss.
    executed = _executed_names(store) if args.unregistered else set()

    def is_hit(row):
        """Does this row match the selected predicate?"""
        name = _worm_of(row)
        if not name:
            return False
        if args.unregistered:
            return not _is_registered(name) and name not in executed
        return name in names

    found = []
    per_tap = {}
    for tap in taps:
        try:
            rows = _page_all(store, tap)
        except Exception as exc:  # noqa: BLE001
            print(f"  {tap}: read failed ({type(exc).__name__}: {exc})")
            continue
        hits = [r for r in rows if is_hit(r)]
        per_tap[tap] = (len(rows), len(hits))
        found.extend(hits)
        print(f"  {tap}: scanned {len(rows)}, matched {len(hits)}")

    if not found:
        print("\nNothing to scrub - the taps are already clean.")
        return 0

    by_name = {}
    for r in found:
        by_name.setdefault(_worm_of(r), 0)
        by_name[_worm_of(r)] += 1
    print("\nmatched rows by name:")
    if args.unregistered:
        print(f"  predicate: not one of the {len(_REGISTERED or ())} registered "
              f"components AND absent from the {len(executed)} names that have "
              f"ever executed")
    for name, count in sorted(by_name.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<24} {count}")

    ids = [int(r["id"]) for r in found if r.get("id") is not None]
    print(f"\ntotal rows: {len(found)} | with id: {len(ids)}")

    if not args.apply:
        print("\nDRY RUN - nothing changed. Re-run with --apply to scrub.")
        return 0

    # Back up BEFORE tombstoning: tombstone() sets data='{}', so the payloads
    # are unrecoverable afterwards without this.
    backup = _sidecar_path()
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": "Test/probe artifacts written into production telemetry",
        "predicate": "unregistered" if args.unregistered else "names",
        "names": sorted(set(by_name)),
        "count": len(found),
        "rows": [
            {"id": r.get("id"), "timestamp": r.get("timestamp"),
             "event_type": r.get("event_type"), "source": r.get("source"),
             "data": r.get("data")}
            for r in found
        ],
    }
    backup.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nbacked up {len(found)} rows -> {backup}")

    marked = store.tombstone(ids)
    print(f"tombstoned {marked} rows")

    # Verify rather than assume: re-scan and confirm nothing matches.
    remaining = 0
    for tap in taps:
        try:
            remaining += sum(1 for r in _page_all(store, tap) if is_hit(r))
        except Exception:
            remaining += -1
    if remaining == 0:
        print("verified: 0 probe rows remain in any tap")
        return 0
    print(f"WARNING: {remaining} probe rows still readable - check "
          f"the tombstone filter", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
