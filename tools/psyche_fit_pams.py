#!/usr/bin/env python3
# coding: utf-8
"""Fit a PSYCHE construct graph on real PAMS data -- or say why it cannot be done.

THE POINT OF THIS TOOL IS THE REFUSAL
-------------------------------------
`PersonNetwork.fit_edges` estimates partial correlations between named
constructs. That only means anything if the rows it consumes are construct
MEASUREMENTS: for each timestamp, a value per node. PAMS is an episodic memory
store -- memories, conversations, tool calls -- and a memory row is not a state
reading. Its columns are `content`, `memory_type`, `tags`, `importance_level`.

So the interesting question is not "what does the graph look like" but "does any
store in this workspace carry a per-construct reading at all". This tool answers
that by PROBING, in a fixed order, and refuses when the answer is no:

  0. an elicitation store       (`tools/elicit.py` JSONL: pad + constructs per
                                 session). The only source that is a MEASUREMENT
                                 by construction, so it is probed first -- a guess
                                 that succeeds while real data exists would make
                                 the graph a picture of the guess.
  1. an explicit construct column  (`constructs`, `construct_state`, `node_values`)
  2. an explicit affect column     (`pad`, `affect`, `appraisal`)
  3. a metadata JSON column carrying construct keys per row

Only (0)-(3) are measurements. There is deliberately NO fallback that builds a
graph out of `tags` co-occurrence or `content` keywords. Both would produce a
graph-shaped artifact with real numbers in it that answers a different question,
and this project has already paid for that mistake: an instrument that reports a
confident figure for something it did not measure. A tag graph is a tag graph.

Exit codes: 0 = fitted, 2 = no construct-bearing signal (the expected outcome on
a memory-only store, and what it prints today because nobody has been elicited),
1 = error.

Usage:
  python tools/psyche_fit_pams.py                    # probe the default store
  python tools/psyche_fit_pams.py --json
  python tools/psyche_fit_pams.py --store PATH --probe-all
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE.network import PersonNetwork  # noqa: E402  (path set above)

#: Default store: PAMS's curated memories. Overridable, because the point is to
#: probe whatever store the caller names rather than to assume this one.
DEFAULT_STORE = Path.home() / ".pams" / "memory_data" / "ai_memories.db"

#: Also probed by --probe-all, since state readings would most plausibly live in
#: the conversation store's `metadata` and not in the memory table.
SIBLING_STORES = ("conversations.db", "mcp_tool_calls.db")

#: THE STORE THAT CAN ACTUALLY ANSWER. PAMS never will: it holds memories, and a
#: memory is not a state reading. The elicitation battery (`tools/elicit.py`)
#: writes per-session PAD + construct rows to JSONL, which is a construct-bearing
#: signal by construction. Probe order puts it FIRST, because a real measurement
#: outranks a guess about where a measurement might be hiding.
ELICITATION_ENV = "PSYCHE_ELICITATION_STORE"
ELICITATION_STORE = Path.home() / ".psyche" / "elicitations.jsonl"

#: Records needed before this tool will fit. The same number
#: `fit_affect_bridge.py --from-records` refuses below, stated once here so the two
#: instruments cannot disagree about when data is sufficient.
MIN_RECORDS = 3

CONSTRUCT_COLUMNS: Tuple[str, ...] = ("constructs", "construct_state", "node_values",
                                      "network_state")
AFFECT_COLUMNS: Tuple[str, ...] = ("pad", "pad_state", "affect", "appraisal")
JSON_COLUMNS: Tuple[str, ...] = ("metadata", "source_metadata", "result_metadata")
#: Keys a metadata blob must carry for it to count as a construct reading.
CONSTRUCT_KEYS: Tuple[str, ...] = ("constructs", "pad", "construct_state", "node_values")


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open read-only. A probe must not be able to modify the store it inspects."""
    return sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True, timeout=5.0)


def describe(path: Path) -> Dict:
    """Schema and row counts, so a refusal can show what WAS available."""
    conn = open_readonly(path)
    try:
        tables: List[Dict] = []
        for name, _sql in conn.execute(
                "select name, sql from sqlite_master where type='table'"):
            if name.startswith("sqlite_"):
                continue
            count = conn.execute('select count(*) from "%s"' % name).fetchone()[0]
            columns = [row[1] for row in conn.execute('PRAGMA table_info("%s")' % name)]
            tables.append({"table": name, "rows": count, "columns": columns})
        return {"store": str(path), "tables": tables}
    finally:
        conn.close()


def _decode(value) -> Dict:
    """Return a JSON object, or an EMPTY dict meaning "not a JSON object".

    Empty rather than None so the callers have one shape to handle, and every
    caller that can tell the difference counts an unparseable row as SKIPPED.
    That count is the guard: a row silently treated as all-zeros would inject a
    fake observation into the covariance, which is a wrong graph rather than a
    missing one.
    """
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def probe(path: Path) -> Dict:
    """Find a construct-bearing source in one store.

    Returns {"source": None, "reason": ...} when nothing qualifies -- an explicit
    negative, not an empty success.
    """
    if not path.exists():
        return {"source": None, "reason": "store does not exist: %s" % path,
                "store": str(path)}
    schema = describe(path)
    conn = open_readonly(path)
    try:
        for table in schema["tables"]:
            columns = [c.lower() for c in table["columns"]]
            for column in CONSTRUCT_COLUMNS:
                if column in columns:
                    rows = conn.execute(
                        'select "%s" from "%s" where "%s" is not null limit 5'
                        % (column, table["table"], column)).fetchall()
                    if any(_decode(row[0]) for row in rows):
                        return {"store": str(path), "table": table["table"],
                                "column": column, "kind": "construct_column",
                                "readings": table["rows"], "schema": schema}
            for column in AFFECT_COLUMNS:
                if column in columns:
                    return {"store": str(path), "table": table["table"],
                            "column": column, "kind": "affect_column",
                            "readings": table["rows"], "schema": schema}
            for column in JSON_COLUMNS:
                if column not in columns:
                    continue
                rows = conn.execute(
                    'select "%s" from "%s" where "%s" is not null limit 25'
                    % (column, table["table"], column)).fetchall()
                hits = 0
                for row in rows:
                    blob = _decode(row[0])
                    if blob and any(key in blob for key in CONSTRUCT_KEYS):
                        hits += 1
                if hits:
                    return {"store": str(path), "table": table["table"],
                            "column": column, "kind": "metadata_json",
                            "hits": hits, "probed": len(rows), "schema": schema}
        available = sorted({c for t in schema["tables"] for c in t["columns"]})
        return {"source": None, "store": str(path),
                "reason": ("no construct-bearing column found: probed %d table(s) for "
                           "constructs/pad/appraisal columns and for construct keys in "
                           "metadata JSON; available columns were %s"
                           % (len(schema["tables"]), ", ".join(available[:20]))),
                "schema": schema}
    finally:
        conn.close()


def probe_elicitation(path: Path) -> Dict:
    """Find construct readings in an elicitation store (JSONL, one session per line).

    Returns an explicit negative when the file is absent rather than an empty
    success: "no store" and "a store with no rows" need different remedies, and a
    probe that reports both as zero teaches the reader to ignore it.
    """
    resolved = Path(os.environ.get(ELICITATION_ENV) or path).expanduser()
    if not resolved.exists():
        return {"source": None, "store": str(resolved), "kind": "records_jsonl",
                "reason": ("no elicitation store at %s -- run tools/elicit.py record "
                           "to create one; nothing has been elicited yet" % resolved)}
    rows = 0
    usable = 0
    instruments = set()
    crooked = 0
    for line in resolved.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows += 1
        try:
            row = json.loads(line)
        except ValueError:
            crooked += 1
            continue
        if not isinstance(row, dict):
            crooked += 1
            continue
        if isinstance(row.get("constructs"), dict) and isinstance(row.get("pad"), list) \
                and len(row["pad"]) == 3:
            usable += 1
            instruments.add(str(row.get("instrument", "unknown")))
    if not usable:
        return {"source": None, "store": str(resolved), "kind": "records_jsonl",
                "rows": rows, "reason": ("%d line(s) in the elicitation store, none "
                                          "carrying both a pad and constructs" % rows)}
    return {"store": str(resolved), "kind": "records_jsonl", "rows": rows,
            "readings": usable, "unreadable": crooked,
            "instruments": sorted(instruments)}


def readings_from_records(path: Path) -> List[Dict[str, float]]:
    """Per-session construct vectors from an elicitation store.

    The pad is NOT folded into the node vector. A construct graph is a graph over
    constructs, and adding three affect axes as nodes would silently change the
    graph's node set -- every partial correlation would then be conditioned on
    affect, which is a different (and unstated) model. The affect half is what
    `fit_affect_bridge.py` fits, against the same file.
    """
    resolved = Path(os.environ.get(ELICITATION_ENV) or path).expanduser()
    out: List[Dict[str, float]] = []
    for line in resolved.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        constructs = row.get("constructs") if isinstance(row, dict) else None
        if not isinstance(constructs, dict):
            continue
        values = {str(k): float(v) for k, v in constructs.items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if values:
            out.append(values)
    return out


def readings_from(source: Dict, limit: int = 500) -> List[Dict[str, float]]:
    """Turn a probed source into per-timestamp construct values.

    Accepts a JSON object of node -> number. Rows that do not parse are skipped
    and counted, because silently treating an unparseable row as a zero reading
    would inject a fake observation into the covariance.
    """
    path = Path(source["store"])
    conn = open_readonly(path)
    out: List[Dict[str, float]] = []
    skipped = 0
    try:
        rows = conn.execute(
            'select "%s" from "%s" where "%s" is not null limit %d'
            % (source["column"], source["table"], source["column"], limit)).fetchall()
        for row in rows:
            blob = _decode(row[0])
            if not blob:
                skipped += 1
                continue
            inner = blob.get("constructs") or blob.get("node_values") or blob
            if not isinstance(inner, dict):
                skipped += 1
                continue
            values = {}
            for key, value in inner.items():
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    values[str(key)] = float(value)
                elif isinstance(value, dict) and "value" in value:
                    try:
                        values[str(key)] = float(value["value"])
                    except (TypeError, ValueError):
                        skipped += 1
            if values:
                out.append(values)
            else:
                skipped += 1
        return out
    finally:
        conn.close()
    # `skipped` is deliberately not returned: the caller's row count is the
    # difference between the probe's figure and the length of this list.


def fit(readings: List[Dict[str, float]], nodes: Optional[List[str]] = None) -> PersonNetwork:
    net = PersonNetwork(nodes=nodes)
    for i, obs in enumerate(readings):
        net.observe(obs, at_s=float(i))
    net.fit_edges()
    return net


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--probe-all", action="store_true",
                        help="also probe sibling stores in the same directory")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    stores = [Path(args.store)]
    if args.probe_all:
        stores += [stores[0].parent / name for name in SIBLING_STORES]

    # A real measurement is probed first. Everything below it is a guess about
    # where a measurement might be hiding, and a guess that succeeds when better
    # data exists would make the graph a picture of the guess.
    records_probe = probe_elicitation(ELICITATION_STORE)
    probes = [probe(path) for path in stores]
    found = ([records_probe] if records_probe.get("readings") else []) + \
            [p for p in probes if p.get("table")]
    if records_probe.get("readings"):
        report_extra = {"elicitation": records_probe}
    else:
        report_extra = {"elicitation": {k: v for k, v in records_probe.items()
                                         if k != "readings"}}
    report = {"probes": [{k: v for k, v in p.items() if k != "schema"} for p in probes]}
    report.update(report_extra)

    if not found:
        report["outcome"] = "no_construct_signal"
        report["note"] = (
            "Refusing to fit. PAMS stores memories, not state readings, so any "
            "graph built here would be a graph of something else wearing these "
            "node names. The 119 curated memories carry memory_type, tags and "
            "importance_level: those support a tag co-occurrence analysis, which "
            "is a different instrument and is not built here.")
        if args.json:
            print(json.dumps(report, indent=1))
        else:
            # The elicitation store is printed even when it is absent. A refusal
            # that lists only the stores it happened to look inside hides the one
            # source that would actually answer, and the reader then concludes
            # the project has nowhere to put a measurement.
            print("elicitation probe -- %s" % records_probe["store"])
            print("    %s" % (records_probe.get("reason") or
                              "%d usable session(s) from %s"
                              % (records_probe.get("readings", 0),
                                 ", ".join(records_probe.get("instruments") or ["?"]))))
            print("PAMS construct probe -- %d store(s)" % len(probes))
            for p in probes:
                print("  %s" % p["store"])
                print("    %s" % (p.get("reason") or "construct source: %s.%s (%s)"
                                  % (p["table"], p["column"], p["kind"])))
            print("\n  OUTCOME: no construct-bearing signal -> refusing to fit.")
            print("  %s" % report["note"])
        return 2

    source = found[0]
    readings = (readings_from_records(Path(source["store"]))
                if source.get("kind") == "records_jsonl" else readings_from(source))
    report["source"] = {k: v for k, v in source.items() if k != "schema"}
    report["readings"] = len(readings)
    if len(readings) < MIN_RECORDS:
        report["outcome"] = "too_few_readings"
        print(json.dumps(report, indent=1) if args.json else
              "OUTCOME: only %d readable construct rows; %d are required and a "
              "graph of %d observations is noise."
              % (len(readings), MIN_RECORDS, MIN_RECORDS - 1))
        return 2
    net = fit(readings)
    report["outcome"] = "fitted"
    report["graph"] = {"nodes": len(net.nodes), "edges": len(net.edges()),
                       "diagnostics": net.diagnostics(),
                       "top_edges": [e.to_dict() for e in net.edges()[:10]]}
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        if source.get("kind") == "records_jsonl":
            print("fitted from %s (%s), %d elicited session(s)"
                  % (source["store"], source["kind"], len(readings)))
        else:
            print("fitted from %s.%s (%s), %d readings"
                  % (source["table"], source["column"], source["kind"], len(readings)))
        print("  %d nodes, %d edges" % (len(net.nodes), len(net.edges())))
        for edge in net.edges()[:10]:
            print("    %-18s %-18s pcorr=%+.3f support=%d"
                  % (edge.a, edge.b, edge.weight, edge.support))
    return 0


if __name__ == "__main__":
    sys.exit(main())
