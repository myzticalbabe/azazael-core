"""Regression tests for scripts/audit_wiring.py.

Two jobs:

1. Validate the INSTRUMENT. An audit that has never fired on a known-bad
   input is not evidence of anything -- a previous audit in this repo
   reported a confident "0 findings" from a detector that could not detect
   the bug it was built for. So these tests assert it fires on a fixture
   entry point nothing calls, and stays silent on three known-good controls.

2. Assert the LIVE repo is wired. This is the "fails when a registered
   mechanism has no caller" check. If it fails, the message names the
   entry point and the file.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

aw = importlib.import_module("audit_wiring")


def _fixture_tree() -> Path:
    """A tree with one dead entry point and three live controls."""
    td = tempfile.mkdtemp()
    root = Path(td)
    (root / "dead.py").write_text(
        "import json\n\n\n"
        "def start_forgotten_service(interval_hours: float = 24.0) -> None:\n"
        "    json.dumps(interval_hours)\n\n\n"
        "def main() -> None:\n"
        "    json.dumps(2)\n",
        encoding="utf-8",
    )
    (root / "live.py").write_text(
        "import json\n\n\n"
        "def start_used_service() -> None:\n"
        "    json.dumps(1)\n\n\n"
        "def main() -> None:\n"
        "    start_used_service()\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )
    (root / "cross.py").write_text(
        "def start_cross_called() -> None:\n    return None\n",
        encoding="utf-8",
    )
    (root / "caller.py").write_text(
        "from cross import start_cross_called\n\n\n"
        "def go() -> None:\n"
        "    start_cross_called()\n",
        encoding="utf-8",
    )
    # A stale duplicate tree must contribute nothing.
    (root / "build" / "lib").mkdir(parents=True)
    (root / "build" / "lib" / "stale.py").write_text(
        "def start_from_stale_build():\n    pass\n", encoding="utf-8")
    return root


def _hits(result: dict) -> set[tuple[str, str]]:
    return {(r["name"], Path(r["file"]).name) for r in result["findings"]}


class TestInstrumentValidation:
    """The detector must fire on known-bad and stay silent on known-good."""

    def test_fires_on_a_forgotten_entry_point(self):
        hits = _hits(aw.audit(_fixture_tree()))
        assert ("start_forgotten_service", "dead.py") in hits, (
            "instrument failed to fire on an entry point nothing calls"
        )

    def test_fires_on_main_with_no_main_guard(self):
        hits = _hits(aw.audit(_fixture_tree()))
        assert ("main", "dead.py") in hits

    def test_silent_on_a_called_entry_point(self):
        hits = _hits(aw.audit(_fixture_tree()))
        assert ("start_used_service", "live.py") not in hits

    def test_silent_on_a_guarded_main(self):
        hits = _hits(aw.audit(_fixture_tree()))
        assert ("main", "live.py") not in hits

    def test_silent_on_a_cross_module_call(self):
        hits = _hits(aw.audit(_fixture_tree()))
        assert ("start_cross_called", "cross.py") not in hits

    def test_ignores_a_stale_build_tree(self):
        hits = _hits(aw.audit(_fixture_tree()))
        assert not any(n == "start_from_stale_build" for n, _ in hits)

    def test_self_test_entry_point_passes(self):
        assert aw.self_test() == 0


class TestLiveRepoIsWired:
    """The check that must FAIL when a mechanism has no caller."""

    def test_no_dead_orchestration_entry_points(self):
        result = aw.audit(_ROOT)
        dead = result["findings"]
        assert not dead, "orchestration entry points with no caller:\n" + "\n".join(
            f"  {r['name']}  ({r['file']})" for r in dead
        )

    def test_audit_scans_our_own_code_not_archived_trees(self):
        """Scope must be src/scripts/tests/tools + root, never archived/."""
        result = aw.audit(_ROOT)
        assert result["scanned_files"] > 100, "scope collapsed to nothing"
        for r in result["findings"] + result["allowed"]:
            top = Path(r["file"]).parts[0]
            assert top not in {"archived", "archive", "build", "nuitka_build_backup"}, (
                f"{r['file']} is outside our own code"
            )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
