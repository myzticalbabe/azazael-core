"""Contracts for the rebuilt meta-evolution control plane.

The old chain census is intentionally absent from these tests.  They exercise
current source boundaries, safe source mutation, non-vacuous retention, and the
fact that a disabled production scheduler cannot be mistaken for a running
system.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from CORE import meta_evolution as me  # noqa: E402


def _load_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    monkeypatch.setenv("AZAZAEL_META_EVOLUTION_DIR", str(state))
    for name, value in {
        "REPORT_PATH": state / "current.json",
        "PROOF_PATH": state / "proof.json",
        "LINEAGE_PATH": state / "lineage.jsonl",
        "PATCH_DIR": state / "patches",
    }.items():
        pass
    spec = importlib.util.spec_from_file_location(
        "meta_evolution_cycle_test", ROOT / "scripts" / "meta_evolution_cycle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.STATE_DIR = state
    mod.REPORT_PATH = state / "current.json"
    mod.PROOF_PATH = state / "proof.json"
    mod.LINEAGE_PATH = state / "lineage.jsonl"
    mod.PATCH_DIR = state / "patches"
    mod.AUDIT_PATH = tmp_path / "audit.json"
    return mod


# ---------------------------------------------------------------- contracts

def test_lifecycle_has_no_illegal_shortcut():
    life = me.Lifecycle()
    with pytest.raises(me.MetaEvolutionError):
        life.transition("retain")
    life.transition("observe")
    life.transition("select")
    life.transition("blocked")
    life.transition("done")
    assert life.snapshot()["history"] == [
        "idle", "observe", "select", "blocked", "done"]


def test_protected_paths_cannot_be_targets():
    for rel in (
        "tests/test_neural_loop.py",
        "tools/self_modify.py",
        "src/CORE/compliance/orchestrator.py",
        "src/CORE/meta_evolution.py",
        "scripts/meta_evolution_cycle.py",
        "archive/old.py",
        "src/CORE/WORMS/expansion/generated/x.py",
    ):
        assert me.is_protected_path(rel), rel
    assert not me.is_protected_path("src/CORE/agent_registry.py")


def test_candidate_choice_is_current_covered_and_not_merely_severity(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    a = root / "src" / "a.py"
    b = root / "src" / "b.py"
    a.write_text("ok = True\n", encoding="utf-8")
    b.write_text("ok = True\n", encoding="utf-8")
    rows = [
        {"path": "src/a.py", "line": 1, "detector": "count_zero_tie", "severity": "HIGH"},
        {"path": "src/b.py", "line": 1, "detector": "default_true", "severity": "HIGH"},
    ]
    chosen = me.choose_candidate(root, rows, lambda path: path == "src/b.py")
    assert chosen is not None and chosen.path == "src/b.py"
    assert me.choose_candidate(root, rows, lambda path: False) is None


def test_candidate_id_changes_when_source_changes(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    target = root / "src" / "x.py"
    row = {"path": "src/x.py", "line": 1, "detector": "assert_true", "severity": "MED"}
    target.write_text("assert True\n", encoding="utf-8")
    first = me.choose_candidate(root, [row], lambda path: True)
    assert first is not None
    target.write_text("assert True  # changed\n", encoding="utf-8")
    second = me.choose_candidate(root, [row], lambda path: True)
    assert second is not None and first.id != second.id


def test_patch_contract_is_one_unprotected_exact_substitution(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    target = root / "src" / "x.py"
    target.write_text("verify(value, default=True)\n", encoding="utf-8")
    payload = {"subs": {"src/x.py": [["default=True", "default=False"]]}}
    rel, old, new = me.validate_substitution_patch(root, payload)
    assert (rel, old, new) == ("src/x.py", "default=True", "default=False")

    with pytest.raises(me.MetaEvolutionError):
        me.validate_substitution_patch(root, {"subs": {"tests/test_x.py": [["a", "b"]]}})
    # Repeated text is refused because replacement would be ambiguous.
    target.write_text("default=True\ndefault=False\n", encoding="utf-8")
    with pytest.raises(me.MetaEvolutionError):
        me.validate_substitution_patch(root, {"subs": {"src/x.py": [["default", "changed"]]}})
    target.write_text("verify(value, default=True)\n", encoding="utf-8")
    with pytest.raises(me.MetaEvolutionError):
        me.validate_substitution_patch(root, {"subs": {
            "src/x.py": [["default=True", "default=False"]],
            "src/y.py": [["a", "b"]],
        }})


def test_patch_contract_repeat_mode(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    target = root / "src" / "x.py"
    # Repeat mode: one pair at every occurrence (2..12 sites).
    target.write_text("x = 1\nx = 1\nx = 1\n", encoding="utf-8")
    rel, old, new = me.validate_substitution_patch(
        root, {"subs": {"src/x.py": [["x = 1", "x = 2"]]},
               "repeat": True})
    assert (rel, old, new) == ("src/x.py", "x = 1", "x = 2")
    # Single site masquerading as pattern: refused.
    target.write_text("x = 1\ny = 2\n", encoding="utf-8")
    with pytest.raises(me.MetaEvolutionError):
        me.validate_substitution_patch(
            root, {"subs": {"src/x.py": [["x = 1", "x = 2"]]},
                   "repeat": True})
    # Over the cap: refused.
    target.write_text("x = 1\n" * 13, encoding="utf-8")
    with pytest.raises(me.MetaEvolutionError):
        me.validate_substitution_patch(
            root, {"subs": {"src/x.py": [["x = 1", "x = 2"]]},
                   "repeat": True})
    # Cap constant exists and is sane.
    assert me.MAX_REPEAT_SITES == 12


def test_zero_test_evaluation_and_no_reduction_cannot_retain():
    ok, _ = me.improvement_proved(2, 1, True, {"passed": 9, "vacuous": False})
    assert ok is True
    assert me.improvement_proved(2, 2, True, {"passed": 9, "vacuous": False})[0] is False
    assert me.improvement_proved(2, 1, True, {"passed": 0, "vacuous": True})[0] is False
    assert me.improvement_proved(2, 1, False, {"passed": 9, "vacuous": False})[0] is False


# ------------------------------------------------------------ fresh evidence

def test_architecture_snapshot_excludes_history_and_enumerates_functions():
    proof = me.architecture_snapshot(ROOT)
    assert proof["scope"]["historical_mesh_census_used"] is False
    assert not any("archive/" in path for path in proof["scope"]["included"])
    names = {row["component"] for row in proof["components"]}
    assert "src/CORE/meta_evolution.py" in names
    assert "scripts/meta_evolution_cycle.py" in names
    meta = next(row for row in proof["components"]
                if row["component"] == "src/CORE/meta_evolution.py")
    assert meta["function_count"] >= 10
    assert all("reachability" in fn for fn in meta["functions"])


def test_cli_proof_is_written_atomically(tmp_path, monkeypatch, capsys):
    mod = _load_cli(tmp_path, monkeypatch)
    assert mod._write_proof() == 0
    data = json.loads(mod.PROOF_PATH.read_text(encoding="utf-8"))
    assert data["schema"] == "azazael.meta_evolution.proof.v1"
    assert data["summary"]["functions_enumerated"] > 0
    assert not mod.PROOF_PATH.with_suffix(".json.tmp").exists()
    assert "meta-evolution proof:" in capsys.readouterr().out


def test_cli_dry_run_observes_without_proposal_or_mutation(tmp_path, monkeypatch):
    mod = _load_cli(tmp_path, monkeypatch)
    target = tmp_path / "repo_target.py"
    target.write_text("safe\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_scan_source", lambda: {
        "ok": True, "files_scanned": 1, "findings": [], "by_severity": {},
        "artifact": "fresh.json", "artifact_mtime": 1, "caveat": "test"})
    monkeypatch.setattr(mod, "_ask_model", lambda *a, **k: pytest.fail(
        "dry run asked a model for a patch"))
    rc = mod.run_cycle(apply=False)
    report = json.loads(mod.REPORT_PATH.read_text(encoding="utf-8"))
    assert rc == mod.EXIT_BLOCKED  # no actionable candidate is a safe block
    assert report["verdict"] == "BLOCKED"
    assert report["apply"] is False
    assert not list(mod.PATCH_DIR.glob("*.json")) if mod.PATCH_DIR.exists() else True
    assert target.read_text(encoding="utf-8") == "safe\n"


def test_cli_blocks_when_model_is_unavailable_in_apply_mode(tmp_path, monkeypatch):
    mod = _load_cli(tmp_path, monkeypatch)
    target = ROOT / "src" / "CORE" / "__init__.py"
    before = target.read_bytes()
    finding = {"path": "src/CORE/__init__.py", "line": 1,
               "detector": "default_true", "severity": "HIGH",
               "code": "get(ok, True)", "why": "test candidate"}
    monkeypatch.setattr(mod, "_scan_source", lambda: {
        "ok": True, "files_scanned": 1, "findings": [finding],
        "by_severity": {"HIGH": 1}, "artifact": "fresh.json",
        "artifact_mtime": 1, "caveat": "test"})
    monkeypatch.setattr(mod, "_test_band_for", lambda path: (list(mod.DEFAULT_BAND), []))
    monkeypatch.setattr(mod, "_model_available", lambda model: {
        "ok": False, "models": [], "requested": model, "endpoint": "offline"})
    rc = mod.run_cycle(apply=True)
    report = json.loads(mod.REPORT_PATH.read_text(encoding="utf-8"))
    assert rc == mod.EXIT_BLOCKED
    assert report["verdict"] == "BLOCKED"
    assert target.read_bytes() == before
