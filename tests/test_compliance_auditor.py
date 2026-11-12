# coding: utf-8
"""
Unit tests for CORE/compliance_auditor.py — External Compliance Auditor (ECA).

Run with: python tests/test_compliance_auditor.py
"""

import os
import sys
import json
import time
import socket
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from CORE.compliance_auditor import (
    ComplianceAuditor,
    EcaHttpServer,
    get_eca,
    reset_eca,
    compliance_gate,
    ComplianceError,
    halt_agent,
    resume_agent,
    is_agent_halted,
    HALT_FLAG_PATH,
    COMPLIANCE_DB_PATH,
)


# ─── Helper ─────────────────────────────────────────────────────────

def _cleanup():
    """Clean up ECA artifacts between tests."""
    try:
        if HALT_FLAG_PATH.exists():
            HALT_FLAG_PATH.unlink()
    except OSError:
        pass
    try:
        if COMPLIANCE_DB_PATH.exists():
            COMPLIANCE_DB_PATH.unlink()
    except OSError:
        pass
    reset_eca()


# ─── Tests ──────────────────────────────────────────────────────────

def test_eca_creation():
    """Creating ECA should initialize DB."""
    _cleanup()
    eca = ComplianceAuditor()
    assert eca is not None
    assert eca.VERSION == "1.0.0"
    summary = eca.get_summary()
    assert "version" in summary
    assert summary["total_checks"] == 0


def test_record_compliance():
    """Recording compliance checks should persist to DB."""
    _cleanup()
    eca = ComplianceAuditor()
    eca.record_compliance_check("test_check", "RULE_A", passed=True)
    eca.record_compliance_check("test_check", "RULE_B", passed=False,
                                  details={"error": "test failure"})
    summary = eca.get_summary()
    assert summary["total_checks"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert summary["pass_rate"] == 50.0


def test_audit_log():
    """Audit log should return recorded events."""
    _cleanup()
    eca = ComplianceAuditor()
    eca.record_compliance_check("audit_test", "RULE_X", passed=True)
    eca.record_compliance_check("audit_test", "RULE_Y", passed=False)
    log = eca.get_audit_log(limit=10)
    assert len(log) >= 2
    # Most recent first
    assert log[0]["rule_id"] == "RULE_Y"
    assert log[0]["passed"] == 0
    assert log[1]["rule_id"] == "RULE_X"
    assert log[1]["passed"] == 1


def test_audit_log_filter():
    """Audit log should support passed_only filter."""
    _cleanup()
    eca = ComplianceAuditor()
    eca.record_compliance_check("test", "R1", passed=True)
    eca.record_compliance_check("test", "R2", passed=False)
    eca.record_compliance_check("test", "R3", passed=True)

    passed = eca.get_audit_log(limit=10, passed_only=True)
    assert all(e["passed"] == 1 for e in passed)
    assert len(passed) == 2

    failed = eca.get_audit_log(limit=10, passed_only=False)
    assert all(e["passed"] == 0 for e in failed)
    assert len(failed) == 1


def test_halt_resume():
    """Halt should stop agent, resume should allow it to continue."""
    _cleanup()
    eca = ComplianceAuditor()

    assert not eca.is_halted()
    result = eca.check_compliance()
    assert not result["halted"]
    assert result["ok"]

    # Halt
    eca.halt("Test halt reason", triggered_by="test")
    assert eca.is_halted()
    assert eca.get_halt_reason() == "Test halt reason"
    assert HALT_FLAG_PATH.exists()

    result = eca.check_compliance()
    assert result["halted"]
    assert result["reason"] == "Test halt reason"
    assert not result["ok"]

    # Resume
    eca.resume(resolved_by="test")
    assert not eca.is_halted()
    assert not HALT_FLAG_PATH.exists()

    result = eca.check_compliance()
    assert not result["halted"]


def test_halt_flag_file():
    """Halt flag file set by external process should be detected."""
    _cleanup()
    eca = ComplianceAuditor()

    # External process writes halt flag
    HALT_FLAG_PATH.write_text(json.dumps({
        "halted": True,
        "timestamp": time.time(),
        "reason": "External halt",
        "triggered_by": "external_process",
    }), encoding="utf-8")

    assert eca.is_halted()
    assert "External" in eca.get_halt_reason()

    # Resume should clear it
    eca.resume()
    assert not HALT_FLAG_PATH.exists()
    assert not eca.is_halted()


def test_halt_history():
    """Halt events should be recorded and queryable."""
    _cleanup()
    eca = ComplianceAuditor()

    eca.halt("Reason 1")
    eca.resume()
    eca.halt("Reason 2")
    eca.resume()

    history = eca.get_halt_history(limit=10)
    assert len(history) == 2
    assert history[0]["reason"] == "Reason 2"  # Most recent first
    assert history[0]["resolved_at"] is not None
    assert history[1]["reason"] == "Reason 1"


def test_summary():
    """Summary should return accurate stats."""
    _cleanup()
    eca = ComplianceAuditor()
    summary = eca.get_summary()
    assert summary["version"] == "1.0.0"
    assert "total_checks" in summary
    assert "passed" in summary
    assert "failed" in summary
    assert "pass_rate" in summary
    assert "open_halts" in summary
    assert "db_path" in summary
    assert "halt_flag_path" in summary


def test_compliance_gate():
    """Compliance gate should raise ComplianceError when halted and pass when not."""
    _cleanup()

    # Use get_eca() so compliance_gate() sees the same instance
    eca = get_eca()

    # Not halted - should pass
    assert compliance_gate("test_action", "TEST_GATE", record_violation=False)

    # Halt
    eca.halt("Gate test halt")
    # Should raise ComplianceError
    try:
        compliance_gate("test_action_after_halt", "TEST_GATE",
                         record_violation=False)
        assert False, "Should have raised ComplianceError"
    except ComplianceError as e:
        assert "Gate test halt" in str(e)

    # Resume
    eca.resume()
    assert compliance_gate("test_action_after_resume", "TEST_GATE",
                            record_violation=False)


def test_external_halt_functions():
    """External halt/resume/is_agent_halted should work."""
    _cleanup()
    # get_eca() ensures singleton is initialized
    eca = get_eca()
    assert not is_agent_halted()

    halt_agent("External halt test")
    assert is_agent_halted()

    resume_agent()
    assert not is_agent_halted()


def test_http_server():
    """ECA HTTP server endpoints should work."""
    _cleanup()
    server = EcaHttpServer(port=18425)
    thread = server.start_background()
    time.sleep(0.3)

    try:
        def http_get(path):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(("127.0.0.1", 18425))
            s.sendall(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode("utf-8"))
            data = s.recv(65535)
            s.close()
            return data.decode("utf-8").split("\r\n\r\n", 1)[-1]

        def http_post(path, body=b""):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(("127.0.0.1", 18425))
            req = f"POST {path} HTTP/1.0\r\nHost: localhost\r\n"
            if body:
                req += f"Content-Length: {len(body)}\r\n"
                req += "Content-Type: application/json\r\n"
            req += "\r\n"
            if body:
                req = req.encode("utf-8") + body
            else:
                req = req.encode("utf-8")
            s.sendall(req)
            data = s.recv(65535)
            s.close()
            return json.loads(data.decode("utf-8").split("\r\n\r\n", 1)[-1])

        # Test root
        resp = http_get("/")
        assert "ECA v" in resp
        assert "External Compliance Auditor" in resp

        # Test status
        resp = http_get("/status")
        status = json.loads(resp)
        assert status["version"] == "1.0.0"

        # Test halted
        resp = http_get("/halted")
        assert "OK" in resp

        # Test POST /halt
        body = json.dumps({"reason": "HTTP halt test"}).encode("utf-8")
        resp = http_post("/halt", body)
        assert resp["status"] == "halted"

        # Test halted after halt
        resp = http_get("/halted")
        assert "HALTED" in resp

        # Test POST /resume
        resp = http_post("/resume")
        assert resp["status"] == "resumed"

        # Test halted after resume
        resp = http_get("/halted")
        assert "OK" in resp

        # Test check
        resp = http_get("/check")
        check = json.loads(resp)
        assert "halted" in check
        assert "ok" in check

        # Test 404
        resp = http_get("/nonexistent")
        err = json.loads(resp)
        assert "error" in err

    finally:
        server.stop()


if __name__ == "__main__":
    tests = [
        ("eca_creation", test_eca_creation),
        ("record_compliance", test_record_compliance),
        ("audit_log", test_audit_log),
        ("audit_log_filter", test_audit_log_filter),
        ("halt_resume", test_halt_resume),
        ("halt_flag_file", test_halt_flag_file),
        ("halt_history", test_halt_history),
        ("summary", test_summary),
        ("compliance_gate", test_compliance_gate),
        ("external_halt_functions", test_external_halt_functions),
        ("http_server", test_http_server),
    ]
    passed = 0
    failed = 0
    print(f"\n  ComplianceAuditor Tests ({len(tests)} tests)")
    print(f"  {'='*55}")
    for name, fn in tests:
        try:
            fn()
            print(f"  [OK] {name}")
            passed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] {name}: {e}")
            failed += 1
    print(f"  {'='*55}")
    print(f"  Passed: {passed}, Failed: {failed}")
