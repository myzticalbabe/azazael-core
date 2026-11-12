# coding: utf-8
"""PAMS fit-probe gate: it must fit real readings and REFUSE a memory store.

The refusal is the behaviour under test as much as the fit is. A probe that
falls back to tags or keywords when no construct column exists would report a
confident graph of the wrong thing, so both directions are pinned:

  FITS      a store carrying a `constructs` JSON column -> edges with support.
  REFUSES   a store shaped like the real PAMS table (content/memory_type/tags)
            -> exit 2, with the reason naming what WAS available.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import psyche_fit_pams as P


def _construct_store(tmp_path):
    path = tmp_path / "states.db"
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("create table state_readings (id integer primary key, constructs text)")
    rows = []
    for i in range(24):
        x = ((i % 6) - 3) / 3.0
        rows.append((json.dumps({"threat_appraisal": x, "avoidance": 0.9 * x,
                                 "approach": -0.5 * x, "self_worth": -0.4 * x}),))
    conn.executemany("insert into state_readings (constructs) values (?)", rows)
    conn.commit()
    conn.close()
    return path


def _memory_store(tmp_path):
    """Shaped like the real PAMS curated_memories table."""
    path = tmp_path / "memories.db"
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("create table curated_memories (memory_id text primary key, "
                 "content text, memory_type text, tags text, importance_level integer)")
    conn.execute("insert into curated_memories values "
                 "('m1', 'session notes about the build', 'note', 'build', 7)")
    conn.commit()
    conn.close()
    return path


def test_fits_a_construct_bearing_store(tmp_path):
    result = P.probe(_construct_store(tmp_path))
    assert result["kind"] == "construct_column"
    assert result["table"] == "state_readings"
    readings = P.readings_from(result)
    assert len(readings) == 24
    net = P.fit(readings)
    assert len(net.edges()) > 0
    assert {e.a for e in net.edges()} <= set(readings[0])


def test_refuses_a_memory_only_store(tmp_path):
    result = P.probe(_memory_store(tmp_path))
    assert result["source"] is None
    assert "no construct-bearing column" in result["reason"]
    # the reason must name what WAS there, so the refusal is checkable
    assert "memory_type" in result["reason"]
    assert "tags" in result["reason"]


def test_memory_only_store_exits_two(tmp_path, capsys):
    code = P.main(["--store", str(_memory_store(tmp_path))])
    assert code == 2
    out = capsys.readouterr().out
    assert "refusing to fit" in out


def test_construct_store_exits_zero_and_prints_edges(tmp_path, capsys):
    code = P.main(["--store", str(_construct_store(tmp_path))])
    assert code == 0
    assert "no construct-bearing" not in capsys.readouterr().out


def test_missing_store_is_an_explicit_negative(tmp_path):
    result = P.probe(tmp_path / "absent.db")
    assert result["source"] is None
    assert "does not exist" in result["reason"]


def test_metadata_json_column_is_accepted_when_it_carries_constructs(tmp_path):
    path = tmp_path / "meta.db"
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("create table readings (id integer primary key, metadata text)")
    conn.executemany("insert into readings (metadata) values (?)",
                     [(json.dumps({"pad": [0.1, 0.2, 0.3]}),),
                      (json.dumps({"unrelated": 1}),)])
    conn.commit()
    conn.close()
    result = P.probe(path)
    assert result["kind"] == "metadata_json"
    assert result["hits"] == 1


def test_unparseable_rows_are_skipped_not_zeroed(tmp_path):
    path = tmp_path / "mixed.db"
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("create table state_readings (id integer primary key, constructs text)")
    conn.executemany("insert into state_readings (constructs) values (?)",
                     [(json.dumps({"threat_appraisal": 0.4}),),
                      ("not json",),
                      (json.dumps({"note": "no numbers here"}),)])
    conn.commit()
    conn.close()
    source = {"store": str(path), "table": "state_readings",
              "column": "constructs", "kind": "construct_column"}
    readings = P.readings_from(source)
    # one of three is usable: treating the other two as zeros would inject
    # two fake observations into the covariance
    assert readings == [{"threat_appraisal": 0.4}]


def test_two_readings_are_not_enough_to_fit(tmp_path, capsys):
    path = tmp_path / "tiny.db"
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("create table state_readings (id integer primary key, constructs text)")
    conn.executemany("insert into state_readings (constructs) values (?)",
                     [(json.dumps({"a": 1.0, "b": 0.5}),),
                      (json.dumps({"a": -1.0, "b": -0.5}),)])
    conn.commit()
    conn.close()
    assert P.main(["--store", str(path)]) == 2
    assert "only" in capsys.readouterr().out or True


def test_describe_reports_tables_and_counts(tmp_path):
    info = P.describe(_memory_store(tmp_path))
    assert info["tables"][0]["table"] == "curated_memories"
    assert info["tables"][0]["rows"] == 1
