# coding: utf-8
"""
Tests for scripts/desktop_projects_reference_check.py — the standalone
METAWORMS / Azazael_UNIFIED reference check.

Uses temp directories to exercise manifest building, comparison, and the
per-project check logic without touching the real Desktop or G: drive.
Runs standalone (no pytest) because pytest segfaults on 3.14t (Error #48).

Run:  python tests/test_desktop_projects_reference_check.py
"""

import os
import sys
import json
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import desktop_projects_reference_check as rc  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────
# Test helpers
# ─────────────────────────────────────────────────────────────────────────
def make_tree(root: Path, files: dict):
    """Create files under root: {'rel/path': 'content'}."""
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


class TempProject:
    """Temporary desktop + reference dirs on the real PROJECTS table."""

    def __init__(self, name, desktop_files, ref_files):
        self.name = name
        self._tmp = tempfile.mkdtemp(prefix="rc_test_")
        self.desktop = Path(self._tmp) / "desktop"
        self.reference = Path(self._tmp) / "reference"
        make_tree(self.desktop, desktop_files)
        make_tree(self.reference, ref_files)
        self._saved = dict(rc.PROJECTS[name])
        rc.PROJECTS[name] = {
            "label": "test",
            "desktop_locations": [self.desktop],
            "reference_locations": [self.reference],
        }

    def close(self):
        rc.PROJECTS[self.name] = self._saved
        shutil.rmtree(self._tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────
def test_build_manifest_basic():
    root = Path(tempfile.mkdtemp(prefix="rc_manifest_"))
    try:
        make_tree(root, {"a.py": "x", "sub/b.py": "y"})
        m = rc.build_manifest(root)
        assert set(m) == {"a.py", "sub/b.py"}, set(m)
        assert len(m["a.py"]) == 64
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_build_manifest_skips_pycache_and_pyc():
    root = Path(tempfile.mkdtemp(prefix="rc_skip_"))
    try:
        make_tree(root, {
            "run.py": "print(1)",
            "__pycache__/run.cpython-314.pyc": "bin",
            "sub/__pycache__/x.pyc": "bin",
            "sub/real.py": "ok",
            ".hash_manifest.txt": "ignored",
        })
        m = rc.build_manifest(root)
        assert set(m) == {"run.py", "sub/real.py"}, set(m)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_save_load_roundtrip():
    refs = rc.references_dir()
    old = refs.exists()
    try:
        rc.save_manifest("_probe", {"a.py": "0" * 64})
        loaded = rc.load_manifest("_probe")
        assert loaded == {"a.py": "0" * 64}
        # missing manifest -> empty dict, no crash
        assert rc.load_manifest("_does_not_exist") == {}
    finally:
        p = rc.manifest_path("_probe")
        if p.exists():
            p.unlink()
        if not old:
            shutil.rmtree(refs, ignore_errors=True)


def test_compare_manifests_classifies_all():
    status = rc.compare_manifests(
        desktop={"same.py": "a", "changed.py": "NEW", "added.py": "z"},
        reference={"same.py": "a", "changed.py": "OLD", "removed.py": "q"},
    )
    assert status["same.py"] == "untouched"
    assert status["changed.py"] == "modified"
    assert status["added.py"] == "added"
    assert status["removed.py"] == "removed"


def test_check_project_untouched():
    t = TempProject("METAWORMS",
                    desktop_files={"run.py": "RUN", "output/o.json": "{}"},
                    ref_files={"run.py": "RUN", "output/o.json": "{}"})
    try:
        lines, code = rc.check_project("METAWORMS")
        assert code == 0, lines
        assert any("UNTOUCHED" in l for l in lines)
    finally:
        t.close()


def test_check_project_drift():
    t = TempProject("METAWORMS",
                    desktop_files={"run.py": "RUN2", "extra.py": "new"},
                    ref_files={"run.py": "RUN", "gone.py": "old"})
    try:
        lines, code = rc.check_project("METAWORMS", verbose=True)
        assert code == 1, lines
        assert any("DRIFT" in l for l in lines)
        # verbose lists each drift file
        assert any("modified" in l and "run.py" in l for l in lines)
        assert any("added" in l and "extra.py" in l for l in lines)
        assert any("removed" in l and "gone.py" in l for l in lines)
    finally:
        t.close()


def test_check_project_desktop_absent():
    t = TempProject("METAWORMS",
                    desktop_files={}, ref_files={"run.py": "RUN"})
    try:
        shutil.rmtree(t.desktop, ignore_errors=True)  # simulate no Desktop copy
        lines, code = rc.check_project("METAWORMS")
        assert code == 2, lines
        assert any("ABSENT" in l for l in lines)
    finally:
        t.close()


def test_check_project_reference_absent():
    t = TempProject("METAWORMS",
                    desktop_files={"run.py": "RUN"}, ref_files={"run.py": "RUN"})
    try:
        shutil.rmtree(t.reference)  # simulate no reference copy
        lines, code = rc.check_project("METAWORMS")
        assert code == 2, lines
        assert any("REFERENCE UNAVAILABLE" in l for l in lines)
    finally:
        t.close()


def test_skip_path_rules():
    assert rc._skip_path("__pycache__/x.pyc")
    assert rc._skip_path("a/b/__pycache__/x.cpython-314.pyc")
    assert rc._skip_path(".hash_manifest.txt")
    assert rc._skip_path("sub/.venv/lib/x.py")
    assert not rc._skip_path("run.py")
    assert not rc._skip_path("META/output/o.json")


def test_find_first():
    a = Path(tempfile.mkdtemp(prefix="rc_ff_a_"))
    b = Path(tempfile.mkdtemp(prefix="rc_ff_b_"))
    try:
        assert rc.find_first([b, a]) == b
        assert rc.find_first([Path("Z:/definitely_missing_xyz"), a]) == a
        assert rc.find_first([Path("Z:/definitely_missing_xyz")]) is None
    finally:
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_real_reference_manifests_exist_and_parse():
    """The canonical reference manifests were built and load cleanly."""
    for project in ("METAWORMS", "Azazael_UNIFIED"):
        m = rc.load_manifest(project)
        assert m, "no reference manifest for %s" % project
        for rel, h in m.items():
            assert len(h) == 64 and rel, (rel, h)


# ─────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────
def main():
    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS  %s" % name)
            passed += 1
        except Exception as exc:  # noqa: BLE001 - test harness
            import traceback
            print("FAIL  %s: %s" % (name, exc))
            traceback.print_exc()
            failed += 1
    print("\nPassed: %d, Failed: %d" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
