# coding: utf-8
"""Publication-gate tests: every check must be shown to fail when it should.

A gate that only ever passes is decoration. Each test below breaks exactly one
thing and asserts that the corresponding check reports it -- which is the only
evidence that the check is wired to anything.

The disclosure test is the one that matters most: the README's Limitations
section is a release requirement, and this proves it cannot be deleted quietly.
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import nurse_ratchet_release_gate as G

PROJECT = ROOT / "Project_Nurse_Ratchet"


def _clone(tmp_path: Path) -> Path:
    target = tmp_path / "Project_Nurse_Ratchet"
    shutil.copytree(PROJECT, target)
    return target


def test_the_real_project_passes():
    report = G.run_checks()
    assert report["result"] == "PASS", json.dumps(report["checks"], indent=1)
    assert report["failures"] == 0


def test_missing_community_file_is_reported(tmp_path):
    clone = _clone(tmp_path)
    (clone / "SECURITY.md").unlink()
    assert "SECURITY.md" in G.check_required_files(clone)


def test_deleting_the_limitations_section_fails_the_gate(tmp_path):
    """The measured weakness cannot be quietly dropped from the public README."""
    clone = _clone(tmp_path)
    readme = clone / "README.md"
    text = readme.read_text(encoding="utf-8")
    start = text.index("## Limitations")
    end = text.index("## Negative results and open problems")
    readme.write_text(text[:start] + text[end:], encoding="utf-8")
    problems = G.check_readme_disclosure(clone)
    assert any("Limitations" in problem for problem in problems)


def test_deleting_the_negative_results_section_fails_the_gate(tmp_path):
    clone = _clone(tmp_path)
    readme = clone / "README.md"
    text = readme.read_text(encoding="utf-8")
    start = text.index("## Negative results and open problems")
    end = text.index("## Ethics")
    readme.write_text(text[:start] + text[end:], encoding="utf-8")
    problems = G.check_readme_disclosure(clone)
    assert any("Negative results" in problem for problem in problems)


def test_issue_template_without_frontmatter_keys_is_reported(tmp_path):
    clone = _clone(tmp_path)
    template = clone / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
    template.write_text("body:\n  - type: markdown\n", encoding="utf-8")
    problems = G.check_issue_templates(clone)
    assert len(problems) == 2                       # name and description
    assert all("bug_report.yml" in problem for problem in problems)


def test_manifest_path_that_does_not_exist_is_reported(tmp_path):
    clone = _clone(tmp_path)
    manifest = G.load_manifest(clone / "PUBLIC_MANIFEST.json")
    manifest["ship"].append({"from": "no_such_file.py", "to": "x.py", "kind": "code"})
    problems, _denied = G.check_manifest_paths(manifest, clone)
    assert any("no_such_file.py" in problem for problem in problems)


def test_declared_path_matching_a_deny_rule_is_reported(tmp_path):
    """A file that EXISTS and was explicitly declared is still refused."""
    clone = _clone(tmp_path)
    # one level above the project, where a real evidence file would sit
    (tmp_path / "bcc_990_series.json").write_text("{}", encoding="utf-8")
    manifest = G.load_manifest(clone / "PUBLIC_MANIFEST.json")
    manifest["ship"].append({"from": "../bcc_990_series.json",
                             "to": "bcc_990_series.json", "kind": "data"})
    problems, denied = G.check_manifest_paths(manifest, clone)
    assert denied, "a declared but denied path must be reported"
    # the file exists, so only the deny rule may fire for it (the clone's other
    # entries legitimately miss their ../src sources)
    assert not any("bcc_990_series" in problem for problem in problems)


def test_undeclared_large_file_fails_closed(tmp_path):
    """The allowlist half: evidence dropped into the tree is caught by default."""
    clone = _clone(tmp_path)
    smuggled = clone / "extra_notes.md"
    smuggled.write_text("x" * (G.MANIFEST_SIZE_FLOOR + 100), encoding="utf-8")
    manifest = G.load_manifest(clone / "PUBLIC_MANIFEST.json")
    problems = G.check_undeclared(manifest, clone)
    assert any("extra_notes.md" in problem for problem in problems)


def test_small_file_below_the_floor_is_not_demanded(tmp_path):
    clone = _clone(tmp_path)
    (clone / "tiny.md").write_text("ok", encoding="utf-8")
    manifest = G.load_manifest(clone / "PUBLIC_MANIFEST.json")
    assert not [p for p in G.check_undeclared(manifest, clone) if "tiny.md" in p]


def test_secret_scan_finds_and_masks(tmp_path):
    target = tmp_path / "leak.py"
    body = 'KEY = "-----BEGIN RSA PRIVATE KEY-----\\nabc"\n'
    target.write_text(body, encoding="utf-8")
    findings = G.scan_secrets(target)
    assert findings and findings[0]["rule"] == "private_key"
    # the report names the rule and masks the value; it never copies the secret
    assert "abc" not in findings[0]["masked"]
    assert findings[0]["masked"].startswith("----")


def test_secret_scan_ignores_ordinary_config(tmp_path):
    target = tmp_path / "config.py"
    target.write_text('THRESHOLD = 0.62\nNAME = "eliza"\n', encoding="utf-8")
    assert G.scan_secrets(target) == []


def test_secret_in_the_project_fails_the_gate(tmp_path):
    clone = _clone(tmp_path)
    (clone / "notes.md").write_text(
        "token: ghp_" + "a" * 36 + "\n", encoding="utf-8")
    report = G.run_checks(clone, clone / "PUBLIC_MANIFEST.json")
    assert report["checks"]["secrets"]
    assert report["result"] == "FAIL"


def test_deny_matching_is_prefix_and_glob_aware():
    manifest = {"never_ship": [{"glob": "*_evidence*.json", "why": ""}],
                "deny_substrings": ["bcc_audit"]}
    assert G._denied("/x/bcc_audit/01_verified/edge.json", manifest)
    assert G._denied("/x/part7_full_evidence.json", manifest)
    assert not G._denied("/x/src/PSYCHE/network.py", manifest)
