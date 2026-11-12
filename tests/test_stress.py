"""Stress test: 50 concurrent DirectMemory instances under 3.14t free-threading."""
import sys, os, threading, time, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
# Stress traffic must not pollute the production memory store.
os.environ.setdefault("AZAZAEL_MEMORY_DIR", tempfile.mkdtemp(prefix="dm_stress_"))
from CORE.direct_memory import DirectMemory

def worker(results: list, idx: int):
    try:
        dm = DirectMemory()
        dm.file_read([f"/stress/test_{idx}.py"], f"stress test {idx}")
        dm.skill_loaded(f"stress-skill-{idx}")
        dm.milestone(f"stress-{idx}", {"idx": idx})
        dm.end({"ok": True, "idx": idx})
        results.append((idx, True, dm.count()))
    except Exception as e:
        results.append((idx, False, str(e)))

class TestStress:
    def test_50_concurrent_sessions(self):
        results = []
        threads = []
        for i in range(50):
            t = threading.Thread(target=worker, args=(results, i))
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=30)
        successes = [r for r in results if r[1]]
        failures = [r for r in results if not r[1]]
        assert len(successes) >= 45, f"Only {len(successes)}/{len(results)} succeeded: {failures[:3]}"
