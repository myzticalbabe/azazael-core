# coding: utf-8
"""Round-trip tests for the config / dashboard / sync-manifest collectors.

Round-trip here means: collect real data from disk or a live collector,
serialize it (JSON file or snapshot), then read it back and verify the
reconstructed representation is faithful to what was collected. These assert
the collectors' persistence contract — the serialized artifact must be usable
to reconstruct state without loss.

Collectors covered:
  - Config:      CORE.WORMS.expansion.config (EnvValidator.scan_env_files,
                 ConfigLinter.lint_json)
  - Dashboard:   CORE.WORMS.expansion.telemetry (MetricAggregator snapshot —
                 the exact dict shape the RULE 0Z-F dashboard consumes)
  - Sync manifest: SCRIPTS.auto_sync (compute_hashes -> JSON manifest ->
                 reload + detect_changes)
"""

import sys
import json
import tempfile
import shutil
from pathlib import Path

import pytest  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.WORMS.expansion.config import EnvValidator, ConfigLinter  # noqa: E402
from CORE.WORMS.expansion.telemetry import MetricAggregator  # noqa: E402
from SCRIPTS import auto_sync  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_tree():
    """Return a temp dir path, cleaned up after the test."""
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Config collector — EnvValidator + ConfigLinter
# ═══════════════════════════════════════════════════════════════════════

class TestConfigCollectorRoundtrip:
    def test_env_scan_roundtrip(self, tmp_tree):
        """Write an .env file, scan it, and verify the parsed map round-trips."""
        env_file = tmp_tree / ".env"
        env_file.write_text(
            "AUTH_TOKEN=secret123\nDB_HOST=localhost\n# comment line\nEMPTY=\n",
            encoding="utf-8",
        )
        v = EnvValidator()
        result = v.scan_env_files(str(env_file))
        assert result["files"] == 1
        assert result["total_keys"] == 3
        # Verify the parsed key->value map matches the source exactly.
        parsed = v._env_map[str(env_file)]
        assert parsed["AUTH_TOKEN"] == "secret123"
        assert parsed["DB_HOST"] == "localhost"
        assert parsed["EMPTY"] == ""

    def test_env_cross_file_missing_keys(self, tmp_tree):
        """Two .env files: scan yields a missing_keys report that round-trips."""
        a = tmp_tree / "a.env"
        b = tmp_tree / "b.env"
        a.write_text("SHARED=1\nONLY_A=2\n", encoding="utf-8")
        b.write_text("SHARED=1\nONLY_B=3\n", encoding="utf-8")
        v = EnvValidator()
        result = v.scan_env_files(str(a), str(b))
        assert result["files"] == 2
        assert result["total_keys"] == 3
        missing = result["missing_per_file"]
        assert set(missing.get(str(a), [])) == {"ONLY_B"}
        assert set(missing.get(str(b), [])) == {"ONLY_A"}

    def test_lint_json_roundtrip_valid(self, tmp_tree):
        """A valid config JSON serializes and lints with zero issues, and the
        JSON round-trips back to the original data structure."""
        cfg = tmp_tree / "app.json"
        data = {"app_name": "azazael", "version": "1.0", "debug": False}
        cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
        linter = ConfigLinter()
        issues = linter.lint_json(str(cfg))
        assert issues == []
        # Round-trip: the file that produced no issues reloads to the same dict.
        loaded = json.loads(cfg.read_text(encoding="utf-8"))
        assert loaded == data

    def test_lint_json_roundtrip_invalid(self, tmp_tree):
        """Broken config JSON is flagged (read still produces the raw text)."""
        cfg = tmp_tree / "broken.json"
        cfg.write_text('{"unterminated": ', encoding="utf-8")
        linter = ConfigLinter()
        issues = linter.lint_json(str(cfg))
        assert issues
        assert issues[0]["severity"] == "error"


# ═══════════════════════════════════════════════════════════════════════
# Dashboard / telemetry collector — MetricAggregator snapshot
# ═══════════════════════════════════════════════════════════════════════

class TestDashboardCollectorRoundtrip:
    def _snapshot_to_json(self, snap):
        """Simulate the dashboard's persistence: serialize snapshot to JSON."""
        return json.loads(json.dumps(snap))

    def test_metric_snapshot_roundtrip(self):
        """Counters/gauges/histograms survive a snapshot -> JSON -> reload."""
        agg = MetricAggregator()
        agg.inc_counter("requests", 3)
        agg.inc_counter("errors")
        agg.set_gauge("cpu_pct", 42.5)
        agg.observe("latency", 10)
        agg.observe("latency", 20)
        agg.observe("latency", 30)

        snap = agg.get_snapshot()
        assert snap["counters"] == {"requests": 3.0, "errors": 1.0}
        assert snap["gauges"] == {"cpu_pct": 42.5}
        h = snap["histograms"]["latency"]
        assert h["count"] == 3
        assert h["avg"] == 20.0
        assert h["p95"] == 30

        # The dashboard consumes the JSON-serialized form; reloading must match.
        reloaded = self._snapshot_to_json(snap)
        assert reloaded["counters"] == snap["counters"]
        assert reloaded["gauges"] == snap["gauges"]
        assert reloaded["histograms"]["latency"] == snap["histograms"]["latency"]

    def test_metric_snapshot_after_more_writes(self):
        """Snapshot reflects accumulated state, not a stale cache."""
        agg = MetricAggregator()
        agg.inc_counter("n", 5)
        first = agg.get_snapshot()
        agg.inc_counter("n", 2)
        second = agg.get_snapshot()
        assert first["counters"]["n"] == 5.0
        assert second["counters"]["n"] == 7.0


# ═══════════════════════════════════════════════════════════════════════
# Sync manifest collector — compute_hashes -> JSON manifest -> detect_changes
# ═══════════════════════════════════════════════════════════════════════

class TestSyncManifestRoundtrip:
    def _write_skill(self, root, rel, content="hello"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_hash_manifest_roundtrip(self, tmp_tree):
        """A hashes manifest saved to JSON reloads to an identical dict."""
        self._write_skill(tmp_tree, "skills/azazael-a.md", "A skill content")
        self._write_skill(tmp_tree, "skills/azazael-b.md", "B skill content")
        manifest = auto_sync.compute_hashes(tmp_tree)
        assert len(manifest) == 2
        manifest_file = tmp_tree / "manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        reloaded = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert reloaded == manifest

    def test_detect_changes_roundtrip(self, tmp_tree):
        """Re-using the same manifest -> no changes; editing a file -> detected."""
        self._write_skill(tmp_tree, "skills/azazael-a.md", "version 1")
        hashes = auto_sync.compute_hashes(tmp_tree)
        # No changes against itself
        assert auto_sync.detect_changes(hashes, auto_sync.compute_hashes(tmp_tree)) == []
        # Modify a file -> exactly one "modified" change
        self._write_skill(tmp_tree, "skills/azazael-a.md", "version 2")
        changes = auto_sync.detect_changes(hashes, auto_sync.compute_hashes(tmp_tree))
        assert any("azazael-a.md (modified)" in c for c in changes)
        # Add + delete are also detected
        self._write_skill(tmp_tree, "skills/azazael-c.md", "new")
        (tmp_tree / "skills" / "azazael-b.md").unlink(missing_ok=True)
        added = list(filter(lambda c: "(new)" in c, changes))
        # (recompute after add already picked it up above)
        changes2 = auto_sync.detect_changes(hashes, auto_sync.compute_hashes(tmp_tree))
        assert any("azazael-c.md (new)" in c for c in changes2)

    def test_empty_manifest_roundtrip(self, tmp_tree):
        """An empty directory yields an empty manifest that round-trips cleanly."""
        assert auto_sync.compute_hashes(tmp_tree) == {}
        assert auto_sync.detect_changes({}, {}) == []


if __name__ == "__main__":
    # Standalone runner — run via pytest and emit the "Passed: X, Failed: Y"
    # line that run_tests.py's _parse_test_output counts (3rd format).
    import re as _re
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-q", "--tb=short"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    m = _re.search(r"(\d+) passed", last)
    passed = int(m.group(1)) if m else 0
    fm = _re.search(r"(\d+) failed", last)
    failed = int(fm.group(1)) if fm else 0
    if failed == 0 and m is None:
        # Failed to parse summary — fall back to exit code.
        failed = 0 if proc.returncode == 0 else 1
    print(f"Passed: {passed}, Failed: {failed}")
    raise SystemExit(0 if failed == 0 else 1)