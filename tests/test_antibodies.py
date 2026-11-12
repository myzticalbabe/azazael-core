#!/usr/bin/env python3
"""Antibody shards: instance-derived detectors, evidence-gated lifecycle.

Covers routing (error namespace -> antibodies shard), probe execution,
generational recording, and the promotion rules that close the loop:
neutralized needs a passing seek, immune needs a streak, reinfection
reopens, backward skips are refused.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from SHARDS import antibodies
from SHARDS.antibodies import (antibody_status, promote_antibody, record_seek,
                               run_antibody)
from SHARDS.registry import SHARDS, route_shard


def _isolate(tmp_path, monkeypatch):
    store = tmp_path / "antibodies.json"
    monkeypatch.setattr(antibodies, "_STORE_PATH", store)
    return store


def test_registry_routes_error_tasks_to_antibodies():
    assert SHARDS["antibodies"]["langs"] == ["python"]
    assert SHARDS["antibodies"]["canonical"] == "python"
    assert route_shard("antibody seek for immune drift regression") == "antibodies"
    assert route_shard("stale sweep work golden mismatch") == "antibodies"


def test_unknown_antibody_is_structured():
    res = run_antibody("no-such-error")
    assert res["status"] == "unknown-antibody" and not res["ok"]
    assert not promote_antibody("no-such-error", "immune")["ok"]


def test_probes_pass_where_toolchains_exist():
    # Live seek on this box: work tier across toolchains + forced stale.
    # ran >= 1 guards against vacuous passes (python always runs).
    for name in ("seq-range-mismatch", "stale-table-routing"):
        res = run_antibody(name)
        assert res["ok"] and res["ran"] >= 1, res


def test_initial_status_is_seeking(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    st = antibody_status("seq-range-mismatch")
    assert st["ok"] and st["status"] == "seeking" and st["generations"] == []


def test_neutralized_requires_passing_seek(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    refused = promote_antibody("seq-range-mismatch", "neutralized")
    assert not refused["ok"] and "passing seek" in refused["error"]
    record_seek("seq-range-mismatch",
                {"ok": True, "instances": []})
    promoted = promote_antibody("seq-range-mismatch", "neutralized")
    assert promoted["ok"] and promoted["to"] == "neutralized"
    assert antibody_status("seq-range-mismatch")["status"] == "neutralized"


def test_immune_requires_streak_and_order(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    record_seek("stale-table-routing", {"ok": True, "instances": []})
    # Neutralized first: immune direct from seeking is refused.
    assert not promote_antibody("stale-table-routing", "immune")["ok"]
    assert promote_antibody("stale-table-routing", "neutralized")["ok"]
    # One passing seek is not a streak of three.
    assert not promote_antibody("stale-table-routing", "immune")["ok"]
    record_seek("stale-table-routing", {"ok": True, "instances": []})
    record_seek("stale-table-routing", {"ok": True, "instances": []})
    done = promote_antibody("stale-table-routing", "immune")
    assert done["ok"] and done["to"] == "immune"


def _mkcheckout(base, *parts):
    marker = base.joinpath(*parts, "src", "SHARDS", "antibodies.py")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("# marker", encoding="utf-8")
    return str(base.joinpath(*parts))


def test_discover_finds_marker_checkouts_only(tmp_path):
    from SHARDS.antibodies import discover_checkouts
    good = _mkcheckout(tmp_path, "a")
    _mkcheckout(tmp_path, "a", "nested")  # inside a checkout: not double-counted
    tmp_path.joinpath("b").mkdir()  # no marker: invisible
    _mkcheckout(tmp_path, ".hidden", "c")  # hidden: never entered
    _mkcheckout(tmp_path, "venv", "d")  # env dir: never entered
    found = discover_checkouts(roots=[str(tmp_path)], max_depth=5)
    assert found == [good]


def test_discover_honors_depth_cap(tmp_path):
    from SHARDS.antibodies import discover_checkouts
    _mkcheckout(tmp_path, "x", "y", "z")
    assert discover_checkouts(roots=[str(tmp_path)], max_depth=1) == []
    assert len(discover_checkouts(roots=[str(tmp_path)], max_depth=5)) == 1


def test_record_tags_root(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    record_seek("seq-range-mismatch", {"ok": True, "instances": []},
                root="C:/fake/checkout")
    gens = antibody_status("seq-range-mismatch")["generations"]
    assert gens and gens[-1]["root"] == "C:/fake/checkout"


def test_box_aggregates_per_root_and_unreachable(tmp_path, monkeypatch):
    from SHARDS import antibodies as ab
    monkeypatch.setattr(ab, "discover_checkouts",
                        lambda roots=None, max_depth=5: ["r-clean", "r-dirty", "r-dead"])

    def fake_seek(root, timeout=300):
        if root == "r-clean":
            return {"ok": True, "results": [
                {"name": "seq-range-mismatch", "ok": True, "ran": 6, "instances": []},
                {"name": "stale-table-routing", "ok": True, "ran": 1, "instances": []}]}
        if root == "r-dirty":
            return {"ok": False, "results": [
                {"name": "seq-range-mismatch", "ok": False, "ran": 6,
                 "instances": ["bash: sum mismatch"]},
                {"name": "stale-table-routing", "ok": True, "ran": 1, "instances": []}]}
        return {"unreachable": "timeout"}

    monkeypatch.setattr(ab, "_seek_checkout", fake_seek)
    rep = ab.seek_box(record=False)
    assert not rep["ok"]
    by_name = {r["name"]: r for r in rep["results"]}
    assert by_name["seq-range-mismatch"]["instances"] == [
        "r-dirty: bash: sum mismatch",
        "r-dead: checkout unreachable (timeout)"]
    assert by_name["stale-table-routing"]["instances"] == [
        "r-dead: checkout unreachable (timeout)"]
    assert rep["checkouts"] == ["r-clean", "r-dirty", "r-dead"]


def test_seek_checkout_parses_own_report():
    # Real subprocess against this checkout: proves the isolation path
    # (run elsewhere, parse here) end to end.
    import pathlib
    from SHARDS.antibodies import _seek_checkout
    repo = str(pathlib.Path(__file__).resolve().parents[1])
    rep = _seek_checkout(repo, timeout=300)
    assert rep["ok"] is True
    assert {r["name"] for r in rep["results"]} == {
        "seq-range-mismatch", "stale-table-routing"}


def test_reinfection_reopens_and_backward_skip_refused(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    for _ in range(3):
        record_seek("stale-table-routing", {"ok": True, "instances": []})
    assert promote_antibody("stale-table-routing", "neutralized")["ok"]
    assert promote_antibody("stale-table-routing", "immune")["ok"]
    # Immune -> neutralized skips backward: refused.
    assert not promote_antibody("stale-table-routing", "neutralized")["ok"]
    # Reinfection reopens: any status -> seeking always allowed.
    record_seek("stale-table-routing",
                {"ok": False, "instances": ["_measured_order"]})
    reopened = promote_antibody("stale-table-routing", "seeking")
    assert reopened["ok"] and reopened["to"] == "seeking"
