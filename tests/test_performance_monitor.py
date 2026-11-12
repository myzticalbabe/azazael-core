# coding: utf-8
"""Unit tests for PerformanceMonitor — mock-based, no real psutil/requests needed."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# Ensure azazael/src is on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from performance_monitor import (
    PerformanceMonitor,
    SystemSnapshot,
    ProcessSnapshot,
    DBHealthSnapshot,
    ServiceHealth,
    Alert,
    HAS_PSUTIL,
    HAS_REQUESTS,
    HAS_SQLITE,
    quick_snapshot,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def pm():
    """Fresh PerformanceMonitor with no extra services or DBs."""
    return PerformanceMonitor(services=[], db_paths=[])


# ── System Snapshot Tests ─────────────────────────────────────────────

class TestSystemSnapshot:
    def test_defaults(self):
        snap = SystemSnapshot()
        assert snap.cpu_percent == 0.0
        assert snap.cpu_count == 1
        assert snap.memory_total_gb == 0.0

    def test_no_psutil_returns_default(self, pm):
        """When psutil is not installed, capture_system returns default values."""
        with patch("performance_monitor.HAS_PSUTIL", False):
            snap = pm.capture_system()
        assert snap.cpu_percent == 0.0  # defaults
        assert snap.process_count == 0

    def test_with_psutil(self, pm):
        """With psutil installed, capture_system reads real/patched values."""
        mock_cpu = 45.2
        mock_mem = MagicMock()
        mock_mem.total = 16 * 1024**3
        mock_mem.used = 8 * 1024**3
        mock_mem.percent = 50.0
        mock_swap = MagicMock()
        mock_swap.used = 2 * 1024**3
        mock_disk = MagicMock()
        mock_disk.percent = 60.0
        mock_disk.free = 40 * 1024**3
        mock_io = MagicMock()
        mock_io.read_bytes = 100 * 1024**2
        mock_io.write_bytes = 50 * 1024**2
        mock_net = MagicMock()
        mock_net.bytes_sent = 200 * 1024**2
        mock_net.bytes_recv = 300 * 1024**2

        with patch("performance_monitor.HAS_PSUTIL", True), \
             patch("performance_monitor.psutil.cpu_percent", return_value=mock_cpu), \
             patch("performance_monitor.psutil.cpu_count", return_value=8), \
             patch("performance_monitor.psutil.virtual_memory", return_value=mock_mem), \
             patch("performance_monitor.psutil.swap_memory", return_value=mock_swap), \
             patch("performance_monitor.psutil.disk_usage", return_value=mock_disk), \
             patch("performance_monitor.psutil.disk_io_counters", return_value=mock_io), \
             patch("performance_monitor.psutil.net_io_counters", return_value=mock_net), \
             patch("performance_monitor.psutil.pids", return_value=list(range(100))):
            snap = pm.capture_system()

        assert snap.cpu_percent == 45.2
        assert snap.cpu_count == 8
        assert snap.memory_total_gb == pytest.approx(16.0, abs=0.1)
        assert snap.memory_used_gb == pytest.approx(8.0, abs=0.1)
        assert snap.memory_percent == 50.0
        assert snap.swap_used_gb == pytest.approx(2.0, abs=0.1)
        assert snap.disk_used_percent == 60.0
        assert snap.disk_free_gb == pytest.approx(40.0, abs=0.1)
        assert snap.io_read_mb == pytest.approx(100.0, abs=0.1)
        assert snap.io_write_mb == pytest.approx(50.0, abs=0.1)
        assert snap.network_sent_mb == pytest.approx(200.0, abs=0.1)
        assert snap.network_recv_mb == pytest.approx(300.0, abs=0.1)
        assert snap.process_count == 100

    def test_psutil_exception_graceful(self, pm):
        """When psutil raises, capture_system returns defaults gracefully."""
        with patch("performance_monitor.HAS_PSUTIL", True), \
             patch("performance_monitor.psutil.cpu_percent", side_effect=OSError("boom")):
            snap = pm.capture_system()
        # Should not raise — returns defaults
        assert snap.cpu_percent == 0.0


# ── Process Snapshot Tests ────────────────────────────────────────────

class TestProcessSnapshot:
    def test_no_psutil_returns_empty(self, pm):
        with patch("performance_monitor.HAS_PSUTIL", False):
            procs = pm.capture_watched_processes()
        assert procs == []

    def test_psutil_exception_returns_empty(self, pm):
        with patch("performance_monitor.HAS_PSUTIL", True), \
             patch("performance_monitor.psutil.process_iter", side_effect=OSError("boom")):
            procs = pm.capture_watched_processes()
        assert procs == []


# ── DB Health Tests ───────────────────────────────────────────────────

class TestDBHealth:
    def test_no_sqlite_returns_empty(self, pm):
        with patch("performance_monitor.HAS_SQLITE", False):
            dbs = pm.capture_db_health()
        assert dbs == []

    def test_missing_db_file(self, pm):
        """Non-existent DB paths are skipped."""
        pm.db_paths = ["/nonexistent/path.db"]
        with patch("performance_monitor.HAS_SQLITE", True):
            dbs = pm.capture_db_health()
        assert dbs == []

    def test_real_db_health(self, pm):
        """Test with a temporary real SQLite database."""
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE test (id INTEGER);")
            conn.execute("INSERT INTO test VALUES (1);")
            conn.commit()
            conn.close()

            assert db_path.exists(), f"DB file should exist at {db_path}"

            # Create a fresh monitor pointing at this DB
            from performance_monitor import PerformanceMonitor
            local_pm = PerformanceMonitor(services=[], db_paths=[str(db_path)])
            dbs = local_pm.capture_db_health()
            assert len(dbs) == 1, f"Expected 1 DB, got {len(dbs)}: {dbs}"
            db = dbs[0]
            assert db.path == str(db_path)
            assert db.size_mb > 0
            assert db.integrity == "ok"


# ── Service Health Tests ──────────────────────────────────────────────

class TestServiceHealth:
    def test_no_requests_returns_unreachable(self, pm):
        """Without requests lib, all services are unreachable."""
        pm.services = [("test-svc", "http://localhost:1234/health")]
        with patch("performance_monitor.HAS_REQUESTS", False):
            svcs = pm.capture_service_health()
        assert len(svcs) == 1
        assert svcs[0].reachable is False
        assert "requests not installed" in svcs[0].error

    def test_reachable_service(self, pm):
        """Mock a reachable HTTP service."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"version": "1.2.3"}

        pm.services = [("test-svc", "http://localhost:1234/health")]
        with patch("performance_monitor.HAS_REQUESTS", True), \
             patch("performance_monitor._requests.get", return_value=mock_resp):
            svcs = pm.capture_service_health()
        assert len(svcs) == 1
        assert svcs[0].reachable is True
        assert svcs[0].status_code == 200
        assert svcs[0].version == "1.2.3"
        assert svcs[0].latency_ms >= 0

    def test_unreachable_service(self, pm):
        """Mock an unreachable HTTP service."""
        pm.services = [("dead-svc", "http://localhost:9999/health")]
        with patch("performance_monitor.HAS_REQUESTS", True), \
             patch("performance_monitor._requests.get",
                   side_effect=Exception("Connection refused")):
            svcs = pm.capture_service_health()
        assert len(svcs) == 1
        assert svcs[0].reachable is False
        assert "Connection refused" in svcs[0].error


# ── Full Capture Tests ────────────────────────────────────────────────

class TestFullCapture:
    def test_capture_structure(self, pm):
        """Full capture returns the expected dict structure."""
        with patch("performance_monitor.HAS_PSUTIL", False), \
             patch("performance_monitor.HAS_REQUESTS", False), \
             patch("performance_monitor.HAS_SQLITE", False):
            snap = pm.capture()
        assert "captured_at" in snap
        assert "system" in snap
        assert "processes" in snap
        assert "databases" in snap
        assert "services" in snap
        assert isinstance(snap["processes"], list)
        assert isinstance(snap["services"], list)

    def test_capture_serializable(self, pm):
        """Captured snapshot should be JSON-serializable."""
        with patch("performance_monitor.HAS_PSUTIL", False), \
             patch("performance_monitor.HAS_REQUESTS", False), \
             patch("performance_monitor.HAS_SQLITE", False):
            snap = pm.capture()
        # Should not raise
        data = json.dumps(snap, indent=2)
        assert isinstance(data, str)


# ── Formatting Tests ──────────────────────────────────────────────────

class TestFormatting:
    def test_format_empty(self, pm):
        """Format an empty snapshot."""
        snap = pm.capture()
        summary = pm.format_summary(snap)
        assert "PERFORMANCE SNAPSHOT" in summary
        assert isinstance(summary, str)

    def test_format_with_data(self, pm):
        """Format a populated snapshot."""
        snap = {
            "captured_at": "2026-01-01T00:00:00",
            "system": {
                "cpu_percent": 45.0, "cpu_count": 4,
                "memory_used_gb": 8.0, "memory_total_gb": 16.0,
                "memory_percent": 50.0, "disk_used_percent": 60.0,
                "disk_free_gb": 100.0, "process_count": 200,
            },
            "processes": [
                {"name": "python", "pid": 1234, "cpu_percent": 5.0,
                 "memory_rss_mb": 200.0, "threads": 4},
            ],
            "databases": [
                {"path": "test.db", "integrity": "ok",
                 "size_mb": 10.0, "wal_size_mb": 2.0},
            ],
            "services": [
                {"name": "ollama", "reachable": True,
                 "latency_ms": 5.0, "version": "1.0"},
            ],
        }
        summary = pm.format_summary(snap)
        assert "CPU: 45.0%" in summary
        assert "python" in summary
        assert "test.db" in summary
        assert "ollama" in summary


# ── Print Snapshot Tests ──────────────────────────────────────────────

class TestPrintSnapshot:
    def test_print_with_none_captures(self, pm, capsys):
        """print_snapshot with None argument captures fresh."""
        with patch("performance_monitor.HAS_PSUTIL", False), \
             patch("performance_monitor.HAS_REQUESTS", False), \
             patch("performance_monitor.HAS_SQLITE", False):
            pm.print_snapshot(None)
        out = capsys.readouterr().out
        assert "PERFORMANCE SNAPSHOT" in out

    def test_print_with_snapshot(self, pm, capsys):
        """print_snapshot with explicit data."""
        snap = pm.capture()
        pm.print_snapshot(snap)
        out = capsys.readouterr().out
        assert "PERFORMANCE SNAPSHOT" in out


# ── Save / Load Tests ─────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_load(self, pm):
        """Save snapshot to file and load it back."""
        snap = pm.capture()
        with tempfile.TemporaryDirectory() as td:
            filepath = Path(td) / "snapshot.json"
            pm.save_snapshot(snap, str(filepath))
            assert filepath.exists()
            loaded = pm.load_snapshot(str(filepath))
            assert loaded["captured_at"] == snap["captured_at"]


# ── Graceful Degradation Tests ────────────────────────────────────────

class TestGracefulDegradation:
    def test_full_degraded_mode(self, pm):
        """When nothing is installed, capture still works without raising."""
        with patch("performance_monitor.HAS_PSUTIL", False), \
             patch("performance_monitor.HAS_REQUESTS", False), \
             patch("performance_monitor.HAS_SQLITE", False):
            snap = pm.capture()
        assert snap is not None
        assert snap["system"]["cpu_percent"] == 0.0
        assert snap["processes"] == []
        assert snap["databases"] == []

    def test_quick_snapshot(self):
        """quick_snapshot convenience function works."""
        with patch("performance_monitor.HAS_PSUTIL", False), \
             patch("performance_monitor.HAS_REQUESTS", False), \
             patch("performance_monitor.HAS_SQLITE", False):
            snap = quick_snapshot()
        assert "captured_at" in snap

    def test_partial_degradation_psutil_only(self, pm):
        """Only psutil available — system+processes populated, services+DBs unreachable."""
        pm.services = [("test-svc", "http://localhost:1234/health")]  # ensure non-empty
        mock_mem = MagicMock()
        mock_mem.total = 8 * 1024**3
        mock_mem.used = 4 * 1024**3
        mock_mem.percent = 50.0
        mock_swap = MagicMock()
        mock_swap.used = 0
        mock_disk = MagicMock()
        mock_disk.percent = 30.0
        mock_disk.free = 100 * 1024**3

        with patch("performance_monitor.HAS_PSUTIL", True), \
             patch("performance_monitor.psutil.cpu_percent", return_value=30.0), \
             patch("performance_monitor.psutil.cpu_count", return_value=4), \
             patch("performance_monitor.psutil.virtual_memory", return_value=mock_mem), \
             patch("performance_monitor.psutil.swap_memory", return_value=mock_swap), \
             patch("performance_monitor.psutil.disk_usage", return_value=mock_disk), \
             patch("performance_monitor.psutil.disk_io_counters", return_value=None), \
             patch("performance_monitor.psutil.net_io_counters", return_value=None), \
             patch("performance_monitor.psutil.pids", return_value=[1, 2, 3]), \
             patch("performance_monitor.psutil.process_iter", return_value=[]), \
             patch("performance_monitor.HAS_REQUESTS", False), \
             patch("performance_monitor.HAS_SQLITE", False):
            snap = pm.capture()
        assert snap["system"]["cpu_percent"] == 30.0
        assert snap["system"]["cpu_count"] == 4
        assert snap["system"]["memory_total_gb"] == pytest.approx(8.0, abs=0.1)
        assert snap["system"]["process_count"] == 3
        assert snap["processes"] == []  # No watched procs
        assert snap["databases"] == []
        assert snap["services"] != []  # Returns unreachable entries


# ── E2E: CLI Integration Test ─────────────────────────────────────────

class TestCLIIntegration:
    """Test that PerformanceMonitor integrates with CLI startup."""
    def test_import_in_cli_context(self):
        """PerformanceMonitor can be imported (used by CLI startup)."""
        from performance_monitor import PerformanceMonitor
        pm = PerformanceMonitor()
        assert pm is not None

    def test_dataclass_roundtrip(self):
        """Dataclasses serialize correctly."""
        snap = SystemSnapshot(
            cpu_percent=42.0,
            cpu_count=8,
            memory_used_gb=4.0,
        )
        d = PerformanceMonitor._snapshot_to_dict(snap)
        assert d["cpu_percent"] == 42.0
        assert d["cpu_count"] == 8
        assert d["memory_used_gb"] == 4.0

# ── Alerting Threshold Tests ───────────────────────────────────────────

class TestAlerting:
    def test_no_alerts_at_normal_levels(self, pm):
        with patch.multiple("performance_monitor", HAS_PSUTIL=False, HAS_REQUESTS=False, HAS_SQLITE=False):
            snap = pm.capture()
        assert len(snap["alerts"]) == 0

    def test_cpu_critical_alert(self, pm):
        snap = {
            "captured_at": "2026-01-01T00:00:00",
            "system": {"cpu_percent": 95.0, "cpu_count": 4,
                       "memory_percent": 50.0, "memory_used_gb": 8.0,
                       "memory_total_gb": 16.0, "disk_used_percent": 30.0,
                       "disk_free_gb": 100.0, "process_count": 100},
            "processes": [], "databases": [], "services": [],
        }
        alerts = pm.evaluate_alerts(snap)
        assert len(alerts) == 1
        assert alerts[0].level == "critical"
        assert "CPU" in alerts[0].message

    def test_memory_warning_alert(self, pm):
        snap = {
            "captured_at": "2026-01-01T00:00:00",
            "system": {"cpu_percent": 30.0, "cpu_count": 4,
                       "memory_percent": 85.0, "memory_used_gb": 13.6,
                       "memory_total_gb": 16.0, "disk_used_percent": 30.0,
                       "disk_free_gb": 100.0, "process_count": 100},
            "processes": [], "databases": [], "services": [],
        }
        alerts = pm.evaluate_alerts(snap)
        assert len(alerts) == 1
        assert alerts[0].level == "warning"
        assert "Memory" in alerts[0].message

    def test_memory_critical_alert(self, pm):
        snap = {
            "captured_at": "2026-01-01T00:00:00",
            "system": {"cpu_percent": 30.0, "cpu_count": 4,
                       "memory_percent": 95.0, "memory_used_gb": 15.2,
                       "memory_total_gb": 16.0, "disk_used_percent": 30.0,
                       "disk_free_gb": 100.0, "process_count": 100},
            "processes": [], "databases": [], "services": [],
        }
        alerts = pm.evaluate_alerts(snap)
        assert len(alerts) == 1
        assert alerts[0].level == "critical"

    def test_db_size_warning_alert(self, pm):
        snap = {
            "captured_at": "2026-01-01T00:00:00",
            "system": {"cpu_percent": 30.0, "cpu_count": 4,
                       "memory_percent": 50.0, "memory_used_gb": 8.0,
                       "memory_total_gb": 16.0, "disk_used_percent": 30.0,
                       "disk_free_gb": 100.0, "process_count": 100},
            "processes": [], "services": [],
            "databases": [{"path": "big.db", "size_mb": 150.0, "integrity": "ok"}],
        }
        alerts = pm.evaluate_alerts(snap)
        assert len(alerts) == 1
        assert alerts[0].level == "warning"
        assert "big.db" in alerts[0].message
        assert "150" in alerts[0].message

    def test_multiple_alerts(self, pm):
        snap = {
            "captured_at": "2026-01-01T00:00:00",
            "system": {"cpu_percent": 95.0, "cpu_count": 4,
                       "memory_percent": 92.0, "memory_used_gb": 14.7,
                       "memory_total_gb": 16.0, "disk_used_percent": 30.0,
                       "disk_free_gb": 100.0, "process_count": 100},
            "processes": [], "services": [],
            "databases": [{"path": "big1.db", "size_mb": 120.0},
                          {"path": "big2.db", "size_mb": 200.0}],
        }
        alerts = pm.evaluate_alerts(snap)
        assert len(alerts) == 4  # 2 critical (CPU, mem) + 2 warning (2 DBs)
        criticals = [a for a in alerts if a.level == "critical"]
        warnings = [a for a in alerts if a.level == "warning"]
        assert len(criticals) == 2
        assert len(warnings) == 2

    def test_alerts_in_capture_output(self, pm):
        """Alerts key should be present in full capture output."""
        with patch.multiple("performance_monitor", HAS_PSUTIL=False, HAS_REQUESTS=False, HAS_SQLITE=False):
            snap = pm.capture()
        assert "alerts" in snap
        assert isinstance(snap["alerts"], list)

    def test_alert_dataclass_serializable(self):
        a = Alert(level="critical", metric="cpu", message="CPU overload", value=95.0, threshold=90.0)
        d = PerformanceMonitor._snapshot_to_dict(a)
        assert d["level"] == "critical"
        assert d["value"] == 95.0
