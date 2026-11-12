"""live_record_probe_315t.py — prove the memory stores RECORD live, under 3.15t.

WHY THIS EXISTS
---------------
The question was: "Does LocalMemoryStore and DirectMemory DB get live recorded
to also?" A store that opens, counts rows and returns OK answers a *different*
question than "does a write land". This probe answers the second one, on the
production interpreter:

  1. print the interpreter (and whether the GIL is actually disabled),
  2. resolve the real database path each store uses,
  3. count rows BEFORE,
  4. write ONE clearly-labelled event through the public API,
  5. count rows AFTER and read the row back by id,
  6. verify the row is queryable through the store's own read path.

The row is left in place: an event log that deletes its own evidence proves
nothing, and both stores are append-only by design. Every row it writes carries
`probe=3.15t-live-record` so it is greppable and unambiguous.

Usage:
    C:/Azazael/.venvs/py315t/Scripts/python.exe azazael/tools/live_record_probe_315t.py
    ... --json      # machine-readable summary only
"""
from __future__ import annotations

import argparse
import inspect
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PROBE_TAG = "3.15t-live-record"
PASS, FAIL = "PASS", "FAIL"


def _interp() -> dict:
    gil = getattr(sys, "_is_gil_enabled", None)
    return {
        "executable": sys.executable,
        "version": sys.version.split()[0],
        "free_threaded_build": bool(getattr(sys, "_is_gil_enabled", None)),
        "gil_enabled": (bool(gil()) if callable(gil) else None),
    }


def _count(db: Path, table: str) -> int | None:
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error as e:
        print("live_record_probe_315t:_count failed on %s: %s" % (table, type(e).__name__),
              file=sys.stderr)
        return None


def _row(db: Path, table: str, row_id: int) -> dict | None:
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
            found = cur.fetchone()
            return dict(found) if found else None
        finally:
            conn.close()
    except sqlite3.Error as e:
        print("live_record_probe_315t:_row failed on %s: %s" % (table, type(e).__name__),
              file=sys.stderr)
        return None


def probe_local_memory_store() -> dict:
    """LocalMemoryStore: write through record_event, read back by id."""
    result = {"store": "LocalMemoryStore", "verdict": FAIL, "notes": []}
    sys.path.insert(0, str(SRC))
    import CORE.local_memory as lm  # noqa: PLC0415

    cls = getattr(lm, "LocalMemoryStore", None)
    if cls is None:
        result["notes"].append("CORE.local_memory has no LocalMemoryStore")
        return result

    params = list(inspect.signature(cls.__init__).parameters)
    result["init_params"] = params
    # Instantiate with NO arguments: the production default is the point.
    store = cls()
    db_candidates = []
    for attr in ("db_path", "path", "_db_path", "database", "_db"):
        value = getattr(store, attr, None)
        if isinstance(value, (str, Path)):
            db_candidates.append(Path(value))
    result["resolved_db_candidates"] = [str(p) for p in db_candidates]
    db = db_candidates[0] if db_candidates else None
    if db is None:
        result["notes"].append("could not resolve the store's database path from the instance")

    before = _count(db, "memories") if db else None
    result["rows_before"] = before

    row_id = 0
    try:
        row_id = store.record_event(
            source="live_record_probe_315t",
            event_type="probe_live_record",
            data={"probe": PROBE_TAG, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "interpreter": sys.executable},
            tags=[PROBE_TAG],
        )
    except Exception as exc:  # noqa: BLE001 - the failure IS the finding
        result["notes"].append(f"record_event raised {type(exc).__name__}: {exc}")
    result["row_id"] = row_id
    result["rows_after"] = _count(db, "memories") if db else None

    if row_id:
        row = _row(db, "memories", int(row_id)) if db else None
        result["row_readback"] = row
        result["verdict"] = PASS if row else FAIL
        if not row:
            result["notes"].append("wrote an id but the row is not readable at that id")
    else:
        result["notes"].append("record_event returned a falsy row id")

    # The store's OWN read path must also see it.
    try:
        for reader in ("recent_events", "search", "query", "get_recent"):
            fn = getattr(store, reader, None)
            if callable(fn):
                try:
                    found = fn(limit=5) if "limit" in inspect.signature(fn).parameters else fn()
                except TypeError:
                    continue
                result["read_path"] = reader
                result["read_path_hits"] = json.dumps(found, default=str)[:400]
                break
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"read path raised {type(exc).__name__}: {exc}")

    delta = None
    if before is not None and result["rows_after"] is not None:
        delta = result["rows_after"] - before
    result["row_delta"] = delta
    if result["verdict"] == PASS and delta is not None and delta < 1:
        result["verdict"] = FAIL
        result["notes"].append("row id came back but the table count did not grow")
    return result


def probe_direct_memory() -> dict:
    """DirectMemory: write through the public record(), read back by id."""
    result = {"store": "DirectMemory", "verdict": FAIL, "notes": []}
    sys.path.insert(0, str(SRC))
    import CORE.direct_memory as dm  # noqa: PLC0415

    db = Path(getattr(dm, "DEFAULT_DB_PATH", "") or "")
    if not db.name:
        for attr in ("DB_PATH", "MEMORY_DB_PATH", "DEFAULT_DB"):
            value = getattr(dm, attr, None)
            if isinstance(value, (str, Path)) and str(value):
                db = Path(value)
                break
    result["db_path"] = str(db)
    before = _count(db, "memories")
    result["rows_before"] = before

    instance = dm.DirectMemory(session_id="live_record_probe_315t")
    result["session_id"] = getattr(instance, "session_id", None)
    row_id = 0
    try:
        row_id = instance.record(
            "probe_live_record",
            {"probe": PROBE_TAG, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "interpreter": sys.executable},
            severity="info",
            tags=[PROBE_TAG],
            source="live_record_probe_315t",
        )
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"record() raised {type(exc).__name__}: {exc}")
    result["row_id"] = row_id
    result["rows_after"] = _count(db, "memories")

    if row_id:
        row = _row(db, "memories", int(row_id))
        result["row_readback"] = row
        result["verdict"] = PASS if row else FAIL
        data = (row or {}).get("data") or ""
        if isinstance(data, str) and PROBE_TAG not in data:
            result["verdict"] = FAIL
            result["notes"].append("row exists but does not carry the probe payload")
    else:
        result["notes"].append("record() returned a falsy row id")

    # FTS must have been maintained for the new row (the report claimed triggers).
    fts = _count(db, "memories_fts")
    result["fts_rows"] = fts
    if before is not None and result["rows_after"] is not None:
        result["row_delta"] = result["rows_after"] - before
        if fts is not None and result["rows_after"] != fts:
            result["notes"].append(
                f"memories={result['rows_after']} but memories_fts={fts} (FTS out of sync)")
    try:
        instance.end({"probe": PROBE_TAG})
        result["session_closed"] = True
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"end() raised {type(exc).__name__}: {exc}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = {"interpreter": _interp(), "probes": []}
    for probe in (probe_local_memory_store, probe_direct_memory):
        try:
            report["probes"].append(probe())
        except Exception as exc:  # noqa: BLE001
            report["probes"].append({"store": probe.__name__, "verdict": FAIL,
                                     "notes": [f"probe crashed: {type(exc).__name__}: {exc}"]})

    failed = [p for p in report["probes"] if p["verdict"] != PASS]
    report["verdict"] = FAIL if failed else PASS

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        i = report["interpreter"]
        print(f"interpreter : {i['executable']}")
        print(f"version     : {i['version']}  free-threaded={i['free_threaded_build']}  "
              f"gil_enabled={i['gil_enabled']}")
        for p in report["probes"]:
            print(f"\n--- {p['store']}: {p['verdict']}")
            for key in ("init_params", "db_path", "resolved_db_candidates", "session_id",
                        "rows_before", "row_id", "rows_after", "row_delta", "fts_rows",
                        "read_path", "session_closed"):
                if key in p:
                    print(f"    {key:22s} {p[key]}")
            if p.get("row_readback"):
                rb = p["row_readback"]
                print(f"    row_readback           id={rb.get('id')} source={rb.get('source')} "
                      f"event_type={rb.get('event_type')} session={str(rb.get('session_id'))[:24]}")
            for note in p.get("notes", []):
                print(f"    NOTE: {note}")
        print(f"\nVERDICT: {report['verdict']}")
    return 0 if report["verdict"] == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
