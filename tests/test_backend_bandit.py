# coding: utf-8
"""Unit tests for backend_bandit — Thompson routing over inference backends.

Pins the #1039 fix: successes count successes, throughput (tok/s) is
carried as its own quantity (tok_sum, n) and is never fused into the
Beta posterior. See Engram #1039 / #1049.
"""
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE import backend_bandit as bb


def _make_bandit(tmp_path, bench_content=None):
    if bench_content is None:
        bench = tmp_path / "bench_results.jsonl"
        bench.write_text("", encoding="utf-8")
    else:
        bench = tmp_path / "bench_results.jsonl"
        bench.write_text(bench_content, encoding="utf-8")
    bb.BENCH = bench
    return bb.BackendBandit(seed=1.0)


class TestUpdate:
    def test_success_increments_successes_by_one(self, tmp_path):
        b = _make_bandit(tmp_path)
        b.update("m", "cpu", ok=True, tps=100.0)
        s = b._stats[("m", "cpu")]
        assert s[0] == 1.0
        assert s[1] == 0.0
        assert s[2] == 100.0
        assert s[3] == 1

    def test_success_does_not_fuse_tps_into_success_count(self, tmp_path):
        b = _make_bandit(tmp_path)
        b.update("m", "cpu", ok=True, tps=50.0)
        s = b._stats[("m", "cpu")]
        assert s[0] == 1.0  # not 1 + 50/10

    def test_failure_increments_failures(self, tmp_path):
        b = _make_bandit(tmp_path)
        b.update("m", "cpu", ok=False, tps=10.0)
        s = b._stats[("m", "cpu")]
        assert s[0] == 0.0
        assert s[1] == 1.0
        assert s[2] == 10.0
        assert s[3] == 1

    def test_tps_accumulates_in_tok_sum(self, tmp_path):
        b = _make_bandit(tmp_path)
        b.update("m", "cpu", ok=True, tps=10.0)
        b.update("m", "cpu", ok=True, tps=20.0)
        s = b._stats[("m", "cpu")]
        assert s[0] == 2.0
        assert s[2] == 30.0
        assert s[3] == 2


class TestHydrate:
    def test_backend_compare_does_not_touch_successes(self, tmp_path):
        content = json.dumps({"type": "backend-compare", "model": "m",
                              "sycl_tps": 999.0, "cpu_tps": 888.0}) + "\n"
        b = _make_bandit(tmp_path, content)
        for (m, backend), (s, f, t, n) in b._stats.items():
            assert s == 0.0, (m, backend, s)
        # throughput recorded as its own quantity
        assert b._stats[("m", "sycl")][2] == 999.0
        assert b._stats[("m", "sycl")][3] == 1
        assert b._stats[("m", "cpu")][2] == 888.0
        assert b._stats[("m", "cpu")][3] == 1

    def test_go_via_ollama_increments_success(self, tmp_path):
        content = json.dumps({"status": "GO-VIA-OLLAMA", "file": "x.py", "tps": 5.0}) + "\n"
        b = _make_bandit(tmp_path, content)
        # find the ollama arm
        ollama_arms = [v for (m, backend), v in b._stats.items() if backend == "ollama"]
        assert ollama_arms, "expected an ollama arm"
        s, f, t, n = ollama_arms[0]
        assert s == 1.0
        assert f == 0.0

    def test_go_row_records_throughput_only(self, tmp_path):
        content = json.dumps({"status": "GO", "file": "x.py", "tps": 42.0}) + "\n"
        b = _make_bandit(tmp_path, content)
        cpu_arms = [v for (m, backend), v in b._stats.items() if backend == "cpu"]
        assert cpu_arms, "expected a cpu arm"
        s, f, t, n = cpu_arms[0]
        assert s == 0.0  # not a pseudo-success
        assert t == 42.0
        assert n == 1

    def test_missing_bench_file_does_not_crash(self, tmp_path):
        bb.BENCH = tmp_path / "does_not_exist.jsonl"
        b = bb.BackendBandit(seed=1.0)
        assert isinstance(b._stats, dict)


class TestPick:
    def test_pick_returns_backend_string(self, tmp_path):
        content = json.dumps({"type": "backend-compare", "model": "m",
                              "sycl_tps": 10.0, "cpu_tps": 5.0}) + "\n"
        b = _make_bandit(tmp_path, content)
        r = b.pick("m")
        assert r in bb.BACKENDS

    def test_pick_unknown_model_defaults_cpu(self, tmp_path):
        b = _make_bandit(tmp_path)
        assert b.pick("nope-model-xyz") == "cpu"

    def test_pick_does_not_crash_on_census_import_failure(self, tmp_path, monkeypatch):
        b = _make_bandit(tmp_path)
        # force fingerprint import to fail
        import sys
        sys.modules["CORE.gguf_census"] = None  # type: ignore
        try:
            r = b.pick("m", path="/some/path.gguf")
            assert r in bb.BACKENDS
        finally:
            del sys.modules["CORE.gguf_census"]


class TestTable:
    def test_table_reports_mean_tps_separately(self, tmp_path):
        content = json.dumps({"type": "backend-compare", "model": "m",
                              "sycl_tps": 100.0, "cpu_tps": 10.0}) + "\n"
        b = _make_bandit(tmp_path, content)
        t = b.table("m")
        assert "sycl" in t
        assert t["sycl"]["mean_tps"] == 100.0
        assert t["sycl"]["w"] == 0.0  # no successes recorded
