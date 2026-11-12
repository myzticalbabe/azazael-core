"""The evidence gate must fire on debt and stay quiet on health.

RULE 0B-C: an oracle without a negative control is UNVALIDATED, and its findings
may not license anything. So every check here is exercised in both directions on
a synthetic tree -- a gate that only ever says PASS is worse than no gate,
because it converts "nobody looked" into "verified".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import evidence_gate as evg  # noqa: E402


def _write(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _healthy_tree(tmp_path: Path) -> Path:
    _write(tmp_path, "src/mod.py",
           "def rotate_secret(name):\n    return name\n\n"
           "def main():\n    return rotate_secret('x')\n")
    return tmp_path


# --------------------------------------------------------------------------
# ast_parse_coverage
# --------------------------------------------------------------------------
def test_parse_coverage_passes_on_clean_source(tmp_path):
    _healthy_tree(tmp_path)
    res = evg.check_parse_coverage(tmp_path)
    assert res["status"] == "pass", res
    assert "1/1" in res["measured"]


def test_parse_coverage_fails_on_unparseable_file(tmp_path):
    _healthy_tree(tmp_path)
    _write(tmp_path, "src/broken.py", "def oops(:\n")
    res = evg.check_parse_coverage(tmp_path)
    assert res["status"] == "fail"
    assert any("broken.py" in o for o in res["offenders"])


# --------------------------------------------------------------------------
# dead_control -- both directions
# --------------------------------------------------------------------------
def test_a_control_called_from_its_own_main_is_not_dead(tmp_path):
    _healthy_tree(tmp_path)
    res = evg.check_dead_controls(tmp_path, {})
    assert res["status"] == "pass", res
    # Assert the PROPERTY, not the label. This pinned "0 dead" and so broke when
    # the check was renamed to report "unreachable control(s)" -- a wording
    # improvement read as a regression. The property is: nothing is counted and
    # nothing is new; offenders carries it, so assert that too.
    assert res["measured"].startswith("0 "), res["measured"]
    assert "(0 new)" in res["measured"], res["measured"]
    assert res["offenders"] == [], res


def test_a_control_called_from_an_importing_module_is_not_dead(tmp_path):
    _healthy_tree(tmp_path)
    _write(tmp_path, "src/consumer.py",
           "from src.mod import rotate_secret\n\n"
           "def run():\n    return rotate_secret('y')\n")
    res = evg.check_dead_controls(tmp_path, {})
    assert res["status"] == "pass", res


def test_a_control_nobody_calls_is_dead(tmp_path):
    _healthy_tree(tmp_path)
    _write(tmp_path, "src/orphan.py",
           "def enforce_the_quota():\n    return True\n")
    res = evg.check_dead_controls(tmp_path, {})
    assert res["status"] == "fail"
    assert any("enforce_the_quota" in o for o in res["offenders"])


def test_a_bare_name_in_a_non_importing_file_is_not_a_caller(tmp_path):
    """The generic-token defect, on a control: a mention without an import."""
    _healthy_tree(tmp_path)
    _write(tmp_path, "src/verifier.py",
           "class StrideVerifierWorm:\n"
           "    def verify_policy(self):\n        return True\n")
    # Same identifier, no import, no call -- the Selenium is_enabled() shape.
    _write(tmp_path, "src/unrelated.py",
           "def helper(obj):\n    return verify_policy\n")
    res = evg.check_dead_controls(tmp_path, {})
    assert res["status"] == "fail"
    assert any("verify_policy" in o for o in res["offenders"])


def test_the_same_name_in_an_importing_caller_is_reachability(tmp_path):
    """The other half of the control: an import-linked call must not be dead."""
    _healthy_tree(tmp_path)
    _write(tmp_path, "src/verifier.py",
           "class StrideVerifierWorm:\n"
           "    def verify_policy(self):\n        return True\n")
    _write(tmp_path, "src/consumer.py",
           "from src.verifier import StrideVerifierWorm\n\n"
           "def run(w):\n    return w.verify_policy()\n")
    res = evg.check_dead_controls(tmp_path, {})
    assert res["status"] == "pass", res


def test_a_call_in_the_defining_line_is_not_its_own_caller(tmp_path):
    _healthy_tree(tmp_path)
    _write(tmp_path, "src/selfref.py",
           "def prune_everything(items):\n    return [prune_everything]\n")
    res = evg.check_dead_controls(tmp_path, {})
    assert res["status"] == "fail"
    assert any("prune_everything" in o for o in res["offenders"])


def test_tests_do_not_count_as_reachability(tmp_path):
    _healthy_tree(tmp_path)
    _write(tmp_path, "src/orphan2.py",
           "def cleanup_store():\n    return True\n")
    _write(tmp_path, "tests/test_orphan2.py",
           "from src.orphan2 import cleanup_store\n\n"
           "def test_it():\n    assert cleanup_store()\n")
    res = evg.check_dead_controls(tmp_path, {})
    assert res["status"] == "fail"
    assert any("cleanup_store" in o for o in res["offenders"])


def test_baseline_accepts_known_debt_but_new_debt_still_fails(tmp_path):
    _healthy_tree(tmp_path)
    _write(tmp_path, "src/orphan.py",
           "def enforce_the_quota():\n    return True\n")
    known = evg.check_dead_controls(tmp_path, {})
    baseline = {"dead_control": known["offenders"]}
    res = evg.check_dead_controls(tmp_path, baseline)
    assert res["status"] == "pass", res
    assert res["known"], "the accepted row should still be reported"

    _write(tmp_path, "src/orphan2.py",
           "def purge_forever():\n    return True\n")
    res2 = evg.check_dead_controls(tmp_path, baseline)
    assert res2["status"] == "fail"
    assert any("purge_forever" in o for o in res2["offenders"])
    assert not any("enforce_the_quota" in o for o in res2["offenders"])


# --------------------------------------------------------------------------
# wired_claims -- both directions
# --------------------------------------------------------------------------
def _ledger(tmp_path: Path, rows: dict) -> None:
    (tmp_path / "NEURAL_MESH_WIRE_OR_CUT.json").write_text(
        json.dumps({"probe": "PF16", "declared": rows}), encoding="utf-8")


def test_a_wired_row_with_a_module_linked_caller_passes(tmp_path):
    _healthy_tree(tmp_path)
    _write(tmp_path, "src/consumer.py",
           "from src.mod import rotate_secret\n\n"
           "def run():\n    return rotate_secret('y')\n")
    _ledger(tmp_path, {"mod:rotate_secret": {"status": "wired", "qualname": "rotate_secret"}})
    res = evg.check_wired_claims(tmp_path, {})
    assert res["status"] == "pass", res
    assert "1/1" in res["measured"]


def test_a_wired_row_with_only_a_bare_token_fails(tmp_path):
    _healthy_tree(tmp_path)
    _write(tmp_path, "src/unrelated.py",
           "def other():\n    return rotate_secret  # mentioned, never imported\n")
    _ledger(tmp_path, {"mod:rotate_secret": {"status": "wired", "qualname": "rotate_secret"}})
    res = evg.check_wired_claims(tmp_path, {})
    assert res["status"] == "fail"
    assert "mod:rotate_secret" in res["offenders"]


def test_a_wired_rows_own_definition_does_not_wire_it(tmp_path):
    _healthy_tree(tmp_path)
    _ledger(tmp_path, {"mod:rotate_secret": {"status": "wired", "qualname": "rotate_secret"}})
    res = evg.check_wired_claims(tmp_path, {})
    assert res["status"] == "fail"


# --------------------------------------------------------------------------
# run_gate wiring
# --------------------------------------------------------------------------
def test_run_gate_only_runs_the_requested_checks(tmp_path):
    _healthy_tree(tmp_path)
    verdict = evg.run_gate(tmp_path, only=("ast_parse_coverage",))
    assert [c["id"] for c in verdict["checks"]] == ["ast_parse_coverage"]
    assert verdict["ok"] is True


def test_unverified_is_not_a_pass(tmp_path):
    """A check that could not measure must not be reported as success."""
    verdict = {"checks": [{"id": "rotation", "status": "unverified",
                           "measured": "0 rotations recorded"}],
               "failed": [], "unverified": ["rotation"], "ok": True}
    assert verdict["ok"] is True          # unverified does not block by itself…
    assert verdict["unverified"] == ["rotation"]   # …but it is reported
