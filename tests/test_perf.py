"""Performance benchmark: measure DirectMemory write throughput."""

import sys, os, time, tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestPerformance:
    def _setup(self):
        old_env = os.environ.get("AZAZAEL_MEMORY_DIR", "")
        tmpdir = Path(tempfile.mkdtemp())
        os.environ["AZAZAEL_MEMORY_DIR"] = str(tmpdir)
        from CORE import direct_memory
        direct_memory.DB_PATH = direct_memory._get_db_path()
        return tmpdir, old_env

    def _teardown(self, tmpdir, old_env):
        if old_env:
            os.environ["AZAZAEL_MEMORY_DIR"] = old_env
        else:
            os.environ.pop("AZAZAEL_MEMORY_DIR", None)
        import shutil
        try:
            shutil.rmtree(str(tmpdir))
        except Exception:
            pass

    def test_1000_events_throughput(self):
        tmpdir, old_env = self._setup()
        try:
            from CORE.direct_memory import DirectMemory
            dm = DirectMemory()
            times = []
            for i in range(200):
                start = time.perf_counter()
                dm.file_read([f"/perf/test_{i}.py"])
                times.append((time.perf_counter() - start) * 1000)  # ms
            dm.end({"ok": True})
            avg_ms = sum(times) / len(times)
            p99 = sorted(times)[int(len(times) * 0.99)]
            total = dm.count()
            assert total >= 200
            print(f"\n  Avg write: {avg_ms:.2f}ms  P99: {p99:.2f}ms  Total: {total} events")
            assert avg_ms < 100, f"Average write time {avg_ms:.2f}ms exceeds 100ms threshold"
        finally:
            self._teardown(tmpdir, old_env)
