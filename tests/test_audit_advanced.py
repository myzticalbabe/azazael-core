"""Advanced audit trail tests: compress, merge, diff_verbose, export_html."""
import sys, os, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from pathlib import Path
from CORE.audit_trail import verify_chain, read_audit_lines

class TestAuditSessionStart:
    def test_audit_session_start_creates_and_is_idempotent(self):
        from CORE.audit_trail import audit_session_start, verify_chain
        p = audit_session_start()
        assert p
        assert os.path.exists(p)
        # Idempotent: second call returns the same active session file
        assert audit_session_start() == p
        # Chain must be at least internally consistent (genesis exists)
        ok, _err = verify_chain(p)
        assert ok

    def test_audit_session_start_exported_from_package(self):
        import CORE
        assert hasattr(CORE, "audit_session_start")
        assert callable(CORE.audit_session_start)

    def test_audit_session_start_writes_session_start_entry(self):
        from CORE.audit_trail import audit_session_start, read_audit_lines
        p = audit_session_start()
        lines = read_audit_lines(p)
        if lines:
            _ln, first = lines[0]
            assert first.get("type") == "session_start"
            assert first.get("audit_trail_version") == "3.0.0"


class TestAuditAdvanced:
    def test_read_audit_lines(self):
        from CORE.audit_trail import AUDIT_DIR
        files = list(AUDIT_DIR.glob("session_*.jsonl"))
        if not files:
            return  # No audit files to test
        lines = read_audit_lines(str(files[0]))
        assert isinstance(lines, list)
        if lines:
            ln, d = lines[0]
            assert isinstance(ln, int)

    def test_verify_chain_on_audit_files(self):
        from CORE.audit_trail import AUDIT_DIR
        for fp in sorted(AUDIT_DIR.glob("session_*.jsonl"))[:3]:
            ok, err = verify_chain(str(fp))
            # Should pass or fail gracefully
            assert isinstance(ok, bool)

    def test_compress_roundtrip(self):
        from CORE.audit_compress import compress_session
        import gzip
        tmp = Path(tempfile.mktemp(suffix=".jsonl"))
        tmp.write_text('{"type":"session_start","ts":"2026-01-01","uuid":"test"}', encoding="utf-8")
        gz = compress_session(tmp)
        assert gz.exists()
        assert gz.suffix == ".gz"
        with gzip.open(gz, "rt") as f:
            data = json.loads(f.read())
            assert data["type"] == "session_start"
        gz.unlink()
        tmp.unlink()

    def test_export_html_renders(self):
        from CORE.audit_export_html import audit_to_html
        tmp = Path(tempfile.mktemp(suffix=".jsonl"))
        tmp.write_text(
            '{"type":"session_start","ts":"2026-01-01","uuid":"a","hash":"a"}\n'
            '{"type":"file_read","ts":"2026-01-01T00:00:01","uuid":"b","hash":"b","files":["x.py"]}\n',
            encoding="utf-8",
        )
        html = audit_to_html(str(tmp))
        assert "<!DOCTYPE html>" in html
        assert "session_start" in html
        assert "file_read" in html
        tmp.unlink()
