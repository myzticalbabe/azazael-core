# coding: utf-8
"""Tests for tools/artifact_freshness.py.

The guard exists because a stale map gets acted on. So it is tested in both
directions: it must FLAG an artifact older than its source, and it must NOT
flag one that is current -- a guard that always fails is as useless as one that
never does, and only the flagging direction is easy to get right by accident.
"""

from __future__ import annotations

import json
import os
import sys
import time
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import artifact_freshness as af  # noqa: E402


def _touch(path: Path, when: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (when, when))


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    _touch(root / "src" / "a.py", time.time() - 1000)
    return root


REG = (("out.json", ("src",), ("*.py",)),)


def test_missing_artifact_is_reported(tmp_path):
    root = _repo(tmp_path)
    rows = af.audit(root, REG)
    assert rows[0]["exists"] is False
    assert rows[0]["stale"] is True
    assert rows[0]["reason"] == "missing"


def test_fresh_artifact_is_not_flagged(tmp_path):
    """The direction a guard can pass vacuously if it only ever checks 'exists'."""
    root = _repo(tmp_path)
    _touch(root / "out.json", time.time())
    rows = af.audit(root, REG)
    assert rows[0]["stale"] is False, rows
    assert rows[0]["newer_count"] == 0


def test_artifact_older_than_source_is_flagged_with_evidence(tmp_path):
    root = _repo(tmp_path)
    _touch(root / "out.json", time.time() - 500)
    _touch(root / "src" / "b.py", time.time() - 10)  # source changed after build
    rows = af.audit(root, REG)
    assert rows[0]["stale"] is True
    assert rows[0]["newer_count"] == 1
    assert rows[0]["newer"] == ["src/b.py"], rows[0]


def test_graph_report_mismatch_detected(tmp_path):
    """A fresh report describing a re-derived graph is not a freshness problem.

    graphify's shrink guard can refuse the graph.json write while the report is
    regenerated from the re-derived graph, leaving both files new and
    disagreeing. mtime checks cannot see it; this is why the guard reads content.
    """
    root = _repo(tmp_path)
    g = root / "graphify-out" / "graph.json"
    g.parent.mkdir(parents=True, exist_ok=True)
    g.write_text(json.dumps({"nodes": [{"id": "a"}], "edges": []}), encoding="utf-8")
    os.utime(g, (time.time() - 10, time.time() - 10))
    _touch(root / "graphify-out" / "GRAPH_REPORT.md", time.time())
    out = af.graph_report_mismatch(root)
    assert out["checked"] is True
    assert out["clustered"] is False
    assert out["mismatch"] is True
    assert "no communities" in out["reason"]


def test_graph_report_consistent_when_clustered(tmp_path):
    root = _repo(tmp_path)
    g = root / "graphify-out" / "graph.json"
    g.parent.mkdir(parents=True, exist_ok=True)
    g.write_text(json.dumps({"nodes": [{"id": "a", "community": 0}], "edges": []}),
                 encoding="utf-8")
    os.utime(g, (time.time() - 10, time.time() - 10))
    _touch(root / "graphify-out" / "GRAPH_REPORT.md", time.time())
    out = af.graph_report_mismatch(root)
    assert out["clustered"] is True
    assert out["mismatch"] is False


def test_main_exit_codes(tmp_path, capsys):
    """Both directions, against the substituted registry.

    The real REGISTRY is not used: in a fixture repo none of its artifacts
    exist, so every row is legitimately stale and the exit code would be 1 for
    a reason unrelated to what this test is checking.
    """
    root = _repo(tmp_path)
    _touch(root / "out.json", time.time() - 500)
    _touch(root / "src" / "b.py", time.time() - 10)
    assert af.main(["--json", "--root", str(root)], REG) == 1
    _touch(root / "out.json", time.time())
    assert af.main(["--json", "--root", str(root)], REG) == 0


def test_missing_real_artifact_is_stale_by_default(tmp_path):
    """The default registry: a repo with no artifacts at all must FAIL.

    This is the direction that matters operationally -- an absent census is
    not 'fresh', and a guard that treated 'missing' as 'nothing to report'
    would pass on a checkout that had never been audited.
    """
    root = _repo(tmp_path)
    assert af.main(["--json", "--root", str(root)]) == 1
    rows = af.audit(root)
    assert rows and all(r["stale"] for r in rows), rows


# ---------------------------------------------------------------------------
# Content drift (graph inputs)
#
# The mtime check above cannot see staleness in a git checkout, because
# `git checkout` restamps every tracked file with the checkout time. These
# tests pin the content-based replacement, and pin the checkout case
# specifically: a source whose content changed while its mtime stays OLDER
# than the artifact. An mtime comparison must report "current" there; the
# md5-vs-manifest check must not.
# ---------------------------------------------------------------------------


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _graph_repo(tmp_path: Path) -> Path:
    """A repo with a graphify manifest, two sources, and a derived artifact.

    `README.md` is in the manifest on purpose: the mtime rule watched
    `src/**/*.py` only, so markdown was 1,507 of the 3,660 graph inputs it
    could not see. The content rule has no such blind spot by construction.
    """
    root = tmp_path / "repo"
    src = root / "src"
    src.mkdir(parents=True)
    (root / "graphify-out").mkdir(parents=True)
    source = src / "a.py"
    source.write_text("print(1)\n", encoding="utf-8")
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    old = time.time() - 10_000
    for p in (source, root / "README.md"):
        os.utime(p, (old, old))

    manifest = {rel: {"ast_hash": _md5(root / rel), "mtime": 1.0}
                for rel in ("src/a.py", "README.md")}
    (root / "graphify-out" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    return root


def test_content_drift_sees_a_markdown_input_the_mtime_rule_cannot(tmp_path):
    """A .md graph input is invisible to the mtime rule, visible to this one."""
    root = _graph_repo(tmp_path)
    readme = root / "README.md"
    old = readme.stat().st_mtime
    readme.write_text("changed\n", encoding="utf-8")
    os.utime(readme, (old, old))

    assert not any(r["stale"] for r in af.audit(root, ()))   # mtime rule: blind
    assert af.graph_content_drift(root)["changed"] == ["README.md"]


def test_content_drift_sees_a_change_an_mtime_check_cannot(tmp_path):
    """The checkout case: content moved, mtime stayed old.

    This is the test the tool did not have. `_graph_repo` writes the source
    with an mtime 10,000s in the past, so the mtime rule reports "current".
    Rewriting the file's content with that mtime preserved is exactly what a
    `git checkout` leaves behind, and it is the state the committed
    NEURAL_MESH_FUNCTION_TODO.md was in (it pointed at L438 for a function
    sitting at L442 while the gate called it current).
    """
    root = _graph_repo(tmp_path)
    source = root / "src" / "a.py"
    old = source.stat().st_mtime

    source.write_text("print(2)\n", encoding="utf-8")   # content changed...
    os.utime(source, (old, old))                        # ...mtime deliberately NOT newer

    # the mtime rule cannot see it
    rows = af.audit(root, ())
    assert not any(r["stale"] for r in rows)

    # the content rule must
    drift = af.graph_content_drift(root)
    assert drift["checked"] is True
    assert drift["changed"] == ["src/a.py"], drift


def test_content_drift_is_quiet_when_nothing_changed(tmp_path):
    root = _graph_repo(tmp_path)
    drift = af.graph_content_drift(root)
    assert drift["checked"] is True
    assert drift["changed"] == [] and drift["missing"] == [], drift


def test_missing_manifest_is_not_checked_rather_than_a_pass(tmp_path):
    """No manifest means the check could not run -- that is not 'no drift'."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    drift = af.graph_content_drift(root)
    assert drift["checked"] is False
    assert drift["changed"] == []


def test_newest_manifest_wins_over_a_nested_stale_one(tmp_path):
    """graphify-out holds manifests at several depths with different schemas.

    The current manifest sits at the TOP of graphify-out; older ones sit a
    level down and an older one further down still carries absolute Windows
    keys from a different run. A `graphify-out/*/manifest.json` glob misses
    the top-level file entirely and silently falls back to the nested stale
    one -- which is exactly the defect this pins, so the fixture has to put
    the correct manifest where a `*/` glob cannot see it.
    """
    root = tmp_path / "repo"
    nested = root / "graphify-out" / "graphify-out"
    nested.mkdir(parents=True)
    stale = nested / "manifest.json"
    stale.write_text(json.dumps({r"C:\repo\x.py": {"ast_hash": "0" * 32}}), encoding="utf-8")
    os.utime(stale, (time.time() - 86_400, time.time() - 86_400))

    current = root / "graphify-out" / "manifest.json"
    current.write_text(json.dumps({}), encoding="utf-8")

    assert af.newest_manifest(root) == current


def test_a_manifest_at_the_top_of_graphify_out_is_found(tmp_path):
    """The real repo's current manifest is `graphify-out/manifest.json`.

    This is the depth the previous glob missed, and it is the one that
    matches graph.json's own mtime. If only this file exists it must be the
    answer -- a `*/` glob finds nothing and the check reports "not checked",
    which then reads as "no drift" to every caller.
    """
    root = tmp_path / "repo"
    (root / "graphify-out").mkdir(parents=True)
    only = root / "graphify-out" / "manifest.json"
    only.write_text(json.dumps({}), encoding="utf-8")
    assert af.newest_manifest(root) == only


def test_drift_is_checked_against_the_top_level_manifest(tmp_path):
    """End to end: drift in a file the top-level manifest records is caught.

    The nested stale manifest exists precisely so that a depth-limited lookup
    finds the wrong file, reports 134 spurious changes, and never notices the
    real one.
    """
    root = tmp_path / "repo"
    src = root / "src"
    src.mkdir(parents=True)
    (src / "a.py").write_text("print(1)\n", encoding="utf-8")
    out = root / "graphify-out"
    (out / "graphify-out").mkdir(parents=True)
    (out / "graphify-out" / "manifest.json").write_text(
        json.dumps({r"C:\elsewhere\zzz.py": {"ast_hash": "0" * 32}}), encoding="utf-8")
    (out / "manifest.json").write_text(
        json.dumps({"src/a.py": {"ast_hash": "0" * 32}}), encoding="utf-8")

    drift = af.graph_content_drift(root)
    assert drift["checked"] is True
    assert drift["changed"] == ["src/a.py"], drift


def test_absolute_manifest_keys_are_reported_relative(tmp_path):
    """Evidence must read the same on W11, WSL and bare metal."""
    root = tmp_path / "repo"
    (root / "graphify-out").mkdir(parents=True)
    src = root / "src"
    src.mkdir()
    (src / "a.py").write_text("print(1)\n", encoding="utf-8")
    (root / "graphify-out" / "manifest.json").write_text(
        json.dumps({str(src / "a.py"): {"ast_hash": "0" * 32}}), encoding="utf-8")

    drift = af.graph_content_drift(root)
    assert drift["changed"] == ["src/a.py"], drift


def test_main_fails_on_content_drift_even_when_mtimes_look_current(tmp_path):
    root = _graph_repo(tmp_path)
    source = root / "src" / "a.py"
    old = source.stat().st_mtime
    source.write_text("print(3)\n", encoding="utf-8")
    os.utime(source, (old, old))

    assert af.main(["--json", "--root", str(root)], ()) == 1
