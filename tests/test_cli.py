"""CLI integration tests for all subcommands."""

import sys, os, io, tempfile
from pathlib import Path
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _setup():
    old_env = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    tmpdir = Path(tempfile.mkdtemp())
    os.environ["AZAZAEL_MEMORY_DIR"] = str(tmpdir)
    from CORE import direct_memory
    direct_memory.DB_PATH = direct_memory._get_db_path()
    # Initialize DB with a session so queries work
    dm = direct_memory.DirectMemory()
    dm.record("test_event", {"v": 1})
    dm.end()
    return tmpdir, old_env


def _teardown(tmpdir, old_env):
    if old_env:
        os.environ["AZAZAEL_MEMORY_DIR"] = old_env
    else:
        os.environ.pop("AZAZAEL_MEMORY_DIR", None)
    import shutil
    try:
        shutil.rmtree(str(tmpdir))
    except Exception:
        pass


class TestCLICrossref:
    def test_cmd_summary(self):
        tmpdir, old_env = _setup()
        try:
            from CORE.cli_crossref import cmd_summary
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_summary()
            out = buf.getvalue()
            assert "Session Mapping Summary" in out or "mappings" in out.lower()
        finally:
            _teardown(tmpdir, old_env)

    def test_cmd_unlinked(self):
        tmpdir, old_env = _setup()
        try:
            from CORE.cli_crossref import cmd_unlinked
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_unlinked()
            out = buf.getvalue()
            assert "Unlinked" in out or "unlinked" in out.lower()
        finally:
            _teardown(tmpdir, old_env)


class TestCLIAudit:
    def test_cmd_status(self):
        from CORE.cli_audit import cmd_status
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_status()
        out = buf.getvalue()
        assert "Audit Trail" in out


class TestCLIMemory:
    def test_cmd_status(self):
        tmpdir, old_env = _setup()
        try:
            from CORE.cli_memory import cmd_status
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_status()
            out = buf.getvalue()
            assert "DirectMemory" in out
        finally:
            _teardown(tmpdir, old_env)

    def test_cmd_stats(self):
        tmpdir, old_env = _setup()
        try:
            from CORE.cli_memory import cmd_stats
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_stats()
            out = buf.getvalue()
            assert "Event Type" in out or "total" in out or "events" in out.lower()
        finally:
            _teardown(tmpdir, old_env)


class TestCLIUnified:
    def test_azazael_ctl_import(self):
        from CORE.azazael_ctl import cmd_status
        assert callable(cmd_status)

    def test_vocab_migration_scan(self):
        from CORE.vocab_migration import cmd_scan
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_scan()
        out = buf.getvalue()
        assert "EVENT_VOCAB" in out or "All event types" in out or "Non-Vocab" in out


class TestInferenceSubcommand:
    def test_infer_benchmark_structure(self):
        from CORE.inference_pool import InferencePool
        pool = InferencePool(max_concurrent=1)
        result = pool.benchmark(model="test-model", iterations=1)
        assert "model" in result
        assert result["iterations"] == 1
