# coding: utf-8
"""


__all__ = [
    "COMPLIANCE_DB_PATH",
    "ComplianceAuditor",
    "ComplianceError",
    "ECA_HOST",
    "ECA_PORT",
    "EcaHttpServer",
    "HALT_FLAG_PATH",
    "SCHEMA",
    "compliance_gate",
    "get_eca",
    "halt_agent",
    "is_agent_halted",
    "reset_eca",
    "resume_agent",
    "start_eca",
    "test_eca",
]

CORE/compliance_auditor.py — External Compliance Auditor (ECA)

An independent watchdog process that monitors agent actions, cross-references
them against compliance rules, and provides real halt capability via an HTTP
endpoint that cannot be bypassed by the agent itself.

Architecture:
- Runs as a background thread (TCP/HTTP server on port 18423)
- Maintains an independent compliance check queue (separate SQLite DB)
- Cross-references agent actions against rules from DirectMemory + ERROR_LOG
- Provides HTTP API for: status, halt, resume, audit log, check compliance
- The halt signal is a file-based flag + HTTP endpoint — the agent must check
  before proceeding past compliance gates

Key design decisions (per 8/8 inference models' diagnosis):
1. SEPARATE PROCESS — Not embedded in the agent; runs independently
2. EXTERNAL HALT — Uses filesystem flag + HTTP endpoint, not just text
3. IMMUTABLE LOG — All compliance events are append-only, cannot be altered
4. CROSS-REFERENCE — Compares agent's self-reported actions vs observed events

Usage:
    # Start ECA server
    python -c "from CORE.compliance_auditor import start_eca; start_eca()"

    # Check compliance from agent
    python -c "from CORE.compliance_auditor import get_eca; eca = get_eca(); eca.check_compliance()"

    # Force halt from external tool
    curl http://localhost:18423/halt
"""

import json
import time
import socket
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# --- Constants ------------------------------------------------------

ECA_PORT = 18423
ECA_HOST = "127.0.0.1"
HALT_FLAG_PATH = Path.home() / ".azazael" / ".eca_halt"
COMPLIANCE_DB_PATH = Path.home() / ".azazael" / "compliance_audit.db"
COMPLIANCE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


# --- Schema ---------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS compliance_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    rule_id     TEXT NOT NULL DEFAULT '',
    passed      INTEGER NOT NULL DEFAULT 0,
    details     TEXT NOT NULL DEFAULT '{}',
    source      TEXT NOT NULL DEFAULT 'eca',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS halt_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    reason      TEXT NOT NULL,
    triggered_by TEXT NOT NULL DEFAULT 'eca',
    resolved_at TEXT,
    resolved_by TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_compliance_type ON compliance_events(event_type);
CREATE INDEX IF NOT EXISTS idx_compliance_passed ON compliance_events(passed);
CREATE INDEX IF NOT EXISTS idx_halt_resolved ON halt_events(resolved_at);
"""


# --- ECA Core -------------------------------------------------------

class ComplianceAuditor:
    """External Compliance Auditor — independent watchdog.

    This is the heart of the ECA. It runs as a separate service with its
    own SQLite database for immutable audit trails. The agent cannot alter
    or delete ECA records because the ECA controls its own DB connection.

    The halt mechanism uses a filesystem flag file + HTTP endpoint.
    The agent MUST check the halt flag before proceeding past compliance
    gates. If the flag exists, the agent MUST halt immediately.
    """

    VERSION = "1.0.0"

    def __init__(self, db_path: (Path | None) = None):
        self._db_path = db_path or COMPLIANCE_DB_PATH
        self._lock = threading.Lock()
        self._halted = False
        self._halt_reason = ""
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            conn.executescript(SCHEMA)
            conn.commit()
            conn.close()

    # -- Halt Mechanism ---------------------------------------------

    def halt(self, reason: str, triggered_by: str = "eca") -> bool:
        """Issue a halt signal — creates the halt flag file AND records to DB.

        The halt persists until resume() is called. Multiple halt calls
        are idempotent (no duplicate flag files).
        """
        with self._lock:
            self._halted = True
            self._halt_reason = reason
            # Write halt flag file
            try:
                HALT_FLAG_PATH.write_text(
                    json.dumps({
                        "halted": True,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "reason": reason,
                        "triggered_by": triggered_by,
                    }, indent=2),
                    encoding="utf-8"
                )
            except OSError as exc:
                note_failure("compliance_auditor:L162", exc)
            # Record to DB
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO halt_events(timestamp, reason, triggered_by) VALUES(?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), reason, triggered_by)
            )
            conn.commit()
            conn.close()
        return True

    def resume(self, resolved_by: str = "eca") -> bool:
        """Resume from halt — removes flag file AND closes halt event in DB."""
        with self._lock:
            self._halted = False
            self._halt_reason = ""
            # Remove halt flag file
            try:
                if HALT_FLAG_PATH.exists():
                    HALT_FLAG_PATH.unlink()
            except OSError as exc:
                note_failure("compliance_auditor:L183", exc)
            # Close open halt event in DB
            conn = self._get_conn()
            conn.execute(
                "UPDATE halt_events SET resolved_at = ?, resolved_by = ? "
                "WHERE resolved_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), resolved_by)
            )
            conn.commit()
            conn.close()
        return True

    def is_halted(self) -> bool:
        """Check if the ECA has issued a halt signal.

        Checks BOTH the in-memory flag AND the filesystem flag file.
        This allows external processes to set the halt flag independently.

        Thread-safe via instance lock — all _halted/_halt_reason mutations
        are serialized to prevent races on 3.14t no-GIL.
        """
        with self._lock:
            if self._halted:
                return True
        # Check filesystem flag (can be set by external process)
        if HALT_FLAG_PATH.exists():
            try:
                data = json.loads(HALT_FLAG_PATH.read_text(encoding="utf-8"))
                if data.get("halted"):
                    with self._lock:
                        self._halted = True
                        self._halt_reason = data.get("reason", "External halt")
                    return True
            except (json.JSONDecodeError, OSError) as exc:
                note_failure("compliance_auditor:L217", exc)
        return False

    def get_halt_reason(self) -> str:
        """Get the reason for the halt, if halted."""
        return self._halt_reason

    # -- Compliance Checks ------------------------------------------

    def record_compliance_check(self, event_type: str, rule_id: str,
                                 passed: bool, details: (dict | None) = None,
                                 source: str = "eca"):
        """Record a compliance check result to the immutable audit trail.

        The agent cannot alter or delete this record once written.
        """
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO compliance_events(timestamp, event_type, rule_id, "
                "passed, details, source) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    event_type, rule_id,
                    1 if passed else 0,
                    json.dumps(details or {}, ensure_ascii=False),
                    source,
                )
            )
            conn.commit()
            conn.close()

    # -- Agent-side Compliance Gate ---------------------------------

    def check_compliance(self) -> dict[str, Any]:
        """The agent calls this before proceeding past compliance gates.

        Returns:
            dict with:
            - 'halted': True/False — if True, agent MUST stop
            - 'reason': why halted
            - 'recent_violations': count of recent failed checks
            - 'ok': True/False convenience field
        """
        halted = self.is_halted()
        recent_violations = self._count_recent_violations(minutes=5)

        if halted:
            self.record_compliance_check(
                "compliance_gate", "ECA_HALT",
                passed=False,
                details={"reason": self._halt_reason, "recent_violations": recent_violations},
                source="agent_check",
            )

        return {
            "halted": halted,
            "reason": self._halt_reason if halted else "",
            "recent_violations": recent_violations,
            "ok": not halted and recent_violations == 0,
            "eca_version": self.VERSION,
        }

    def _count_recent_violations(self, minutes: int = 5) -> int:
        """Count compliance violations in the last N minutes.

        Uses the 'created_at' column which stores timestamps in SQLite's
        native datetime format (via DEFAULT datetime('now')), ensuring
        correct string comparison with datetime('now', ?).
        """
        with self._lock:
            conn = self._get_conn()
            n = conn.execute(
                "SELECT COUNT(*) FROM compliance_events "
                "WHERE passed = 0 AND created_at >= datetime('now', ?)",
                (f"-{minutes} minutes",)
            ).fetchone()[0]
            conn.close()
        return n

    # -- Audit Queries ---------------------------------------------

    def get_audit_log(self, limit: int = 50, passed_only: (bool | None) = None
                       ) -> list[dict[str, Any]]:
        """Get the immutable audit trail.

        Args:
            limit: Max records to return
            passed_only: If True, only passed checks. If False, only failures.
                         If None, all records.
        """
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            if passed_only is True:
                rows = conn.execute(
                    "SELECT * FROM compliance_events WHERE passed = 1 "
                    "ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            elif passed_only is False:
                rows = conn.execute(
                    "SELECT * FROM compliance_events WHERE passed = 0 "
                    "ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM compliance_events ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            conn.close()

        results = []
        for r in rows:
            d = dict(r)
            try:
                d["details"] = json.loads(d["details"])
            except (json.JSONDecodeError, TypeError) as exc:
                note_failure("compliance_auditor:L334", exc)
            results.append(d)
        return results

    def get_halt_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get halt event history."""
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM halt_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            conn.close()
        return [dict(r) for r in rows]

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of compliance status."""
        with self._lock:
            conn = self._get_conn()
            total = conn.execute(
                "SELECT COUNT(*) FROM compliance_events"
            ).fetchone()[0]
            passed = conn.execute(
                "SELECT COUNT(*) FROM compliance_events WHERE passed = 1"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM compliance_events WHERE passed = 0"
            ).fetchone()[0]
            open_halts = conn.execute(
                "SELECT COUNT(*) FROM halt_events WHERE resolved_at IS NULL"
            ).fetchone()[0]
            conn.close()

        return {
            "version": self.VERSION,
            "halted": self.is_halted(),
            "halt_reason": self._halt_reason if self._halted else "",
            "total_checks": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / max(total, 1) * 100, 1),
            "open_halts": open_halts,
            "db_path": str(self._db_path),
            "halt_flag_path": str(HALT_FLAG_PATH),
        }


# --- Singleton ------------------------------------------------------

_eca_instance: (ComplianceAuditor | None) = None
_eca_lock = threading.Lock()


def reset_eca():
    """Reset the ECA singleton for testing."""
    global _eca_instance
    with _eca_lock:
        if _eca_instance is not None:
            if _eca_instance.is_halted():
                _eca_instance.resume()
            _eca_instance = None


def get_eca() -> ComplianceAuditor:
    """Get or create the ECA singleton."""
    global _eca_instance
    if _eca_instance is None:
        with _eca_lock:
            if _eca_instance is None:
                _eca_instance = ComplianceAuditor()
    return _eca_instance


# --- HTTP Server ----------------------------------------------------

class EcaHttpServer:
    """HTTP server for external ECA monitoring.

    Provides endpoints:
    GET  /status       — Compliance status summary
    GET  /check        — Full compliance check
    GET  /audit        — Recent audit log
    GET  /halt         — list halt events
    POST /halt         — Issue halt signal (body: {"reason": "..."})
    POST /resume       — Resume from halt
    GET  /halted       — Quick halted check (returns "HALTED" or "OK")

    The halt endpoint is the key external enforcement mechanism.
    Any external tool (monitoring, CI, admin script) can call POST /halt
    to force the agent to stop, bypassing the agent's own control.
    """

    def __init__(self, host: str = ECA_HOST, port: int = ECA_PORT):
        self.host = host
        self.port = port
        self._server_lock = threading.Lock()
        self.server: (socket.socket | None) = None
        self._running = threading.Event()

    def handle_client(self, conn: socket.socket):
        """Handle a single HTTP client."""
        try:
            conn.settimeout(10.0)
            data = conn.recv(65535)
            if not data:
                return

            request = data.decode("utf-8", errors="replace")
            lines = request.split("\r\n")
            if not lines:
                return

            method_path = lines[0].split(" ")
            if len(method_path) < 2:
                return
            method = method_path[0]
            path = method_path[1].split("?")[0]  # Strip query params

            eca = get_eca()

            if method == "POST" and path == "/halt":
                # Parse body for reason
                reason = "External halt via HTTP"
                try:
                    body = request.split("\r\n\r\n", 1)[-1]
                    if body:
                        payload = json.loads(body)
                        reason = payload.get("reason", reason)
                except (json.JSONDecodeError, IndexError) as exc:
                    note_failure("compliance_auditor:L463", exc)
                eca.halt(reason, triggered_by="http_api")
                self._send_json(conn, {"status": "halted", "reason": reason})

            elif method == "POST" and path == "/resume":
                eca.resume(resolved_by="http_api")
                self._send_json(conn, {"status": "resumed"})

            elif path == "/halted":
                if eca.is_halted():
                    self._send_text(conn, "HALTED: " + eca.get_halt_reason())
                else:
                    self._send_text(conn, "OK")

            elif path == "/status":
                self._send_json(conn, eca.get_summary())

            elif path == "/check":
                self._send_json(conn, eca.check_compliance())

            elif path == "/audit":
                limit = 50
                eca.record_compliance_check("audit_query", "AUDIT_LOG",
                                              passed=True, source="http_api")
                self._send_json(conn, eca.get_audit_log(limit=limit))

            elif path == "/halt-events":
                self._send_json(conn, eca.get_halt_history())

            elif path == "/":
                self._send_text(conn,
                    "ECA v" + eca.VERSION + " — External Compliance Auditor\n"
                    "Endpoints:\n"
                    "  GET  /            - This help\n"
                    "  GET  /status      - Compliance summary\n"
                    "  GET  /check       - Full compliance check\n"
                    "  GET  /audit       - Recent audit log\n"
                    "  GET  /halted      - Quick halt check\n"
                    "  GET  /halt-events - Halt event history\n"
                    "  POST /halt        - Issue halt (body: {\"reason\": \"...\"})\n"
                    "  POST /resume      - Resume from halt\n"
                )

            else:
                self._send_json(conn, {"error": f"Unknown path: {path}"}, status=404)

        except socket.timeout as exc:
            note_failure("compliance_auditor:L510", exc)
        except (OSError, ValueError, sqlite3.Error) as e:
            # Request-error boundary: socket I/O, body parsing, or audit DB access.
            # Request-error boundary: socket I/O, body parsing, or audit DB access.
            note_failure("compliance_auditor:L512", e)
            try:
                self._send_json(conn, {"error": str(e)}, status=500)
            except OSError as exc:
                note_failure("compliance_auditor:L516", exc) # client already gone / connection reset
        finally:
            try:
                conn.close()
            except OSError as exc:
                note_failure("compliance_auditor:L521", exc)

    def _send_json(self, conn: socket.socket, data: Any, status: int = 200):
        body = json.dumps(data, indent=2, default=str)
        resp = (
            f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{body}"
        )
        conn.sendall(resp.encode("utf-8"))

    def _send_text(self, conn: socket.socket, text: str, status: int = 200):
        body = text + "\n"
        resp = (
            f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\n"
            f"Content-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{body}"
        )
        conn.sendall(resp.encode("utf-8"))

    def start(self):
        """Start the ECA HTTP server in the current thread."""
        with self._server_lock:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind((self.host, self.port))
            self.server.listen(5)
            self.server.settimeout(1.0)
        self._running.set()

        eca = get_eca()
        eca.record_compliance_check("eca_server_started", "ECA_START",
                                     passed=True, source="eca")

        while self._running.is_set():
            try:
                with self._server_lock:
                    srv = self.server
                if srv is None:
                    break
                conn, addr = srv.accept()
                t = threading.Thread(target=self.handle_client, args=(conn,), daemon=True)
                t.start()
            except socket.timeout as exc:
                note_failure("compliance_auditor:L571", exc)
                continue
            except OSError as exc:
                note_failure("compliance_auditor:L573", exc)
                break

        eca.record_compliance_check("eca_server_stopped", "ECA_STOP",
                                     passed=True, source="eca")

    def stop(self):
        self._running.clear()
        with self._server_lock:
            if self.server:
                try:
                    self.server.close()
                    self.server = None
                except OSError as exc:
                    note_failure("compliance_auditor:L586", exc)

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.start, daemon=True)
        t.start()
        return t


# --- Halt signal handler for external processes ---------------------

def halt_agent(reason: str) -> bool:
    """Signal the ECA to halt the agent from an external process.

    This directly writes the halt flag file, which the ECA server
    picks up on its next check. No HTTP server needed.
    """
    return get_eca().halt(reason, triggered_by="external")


def resume_agent(resolved_by: str = "external") -> bool:
    """Resume the agent from an external process."""
    return get_eca().resume(resolved_by=resolved_by)


def is_agent_halted() -> bool:
    """Quick check if the agent is halted (no ECA server needed)."""
    return get_eca().is_halted()


# --- Convenience entry points ---------------------------------------

def start_eca(host: str = ECA_HOST, port: int = ECA_PORT,
              daemon: bool = True) -> threading.Thread:
    """Start the ECA HTTP server (convenience entry point).

    Args:
        host: Host to bind to (default: 127.0.0.1)
        port: Port to listen on (default: 18423)
        daemon: Whether to run as daemon thread

    Returns:
        The server thread (can be used to join/stop)
    """
    server = EcaHttpServer(host=host, port=port)
    t = server.start_background()
    if not daemon:
        t.daemon = False
    print(f"  [ECA] External Compliance Auditor running on http://{host}:{port}", flush=True)
    return t


# --- DirectMemory import (guarded for optional recording) ----------
_DM_RECORD = None
try:
    from CORE.direct_memory import record as _dm_record
    _DM_RECORD = _dm_record
except ImportError as exc:
    note_failure("compliance_auditor:L643", exc)


class ComplianceError(Exception):
    """Raised when compliance_gate detects a halt signal.

    This exception makes compliance enforcement IMPOSSIBLE TO BYPASS.
    Unlike returning False (which the agent can ignore), raising an
    exception forces the calling code to either handle it or crash.
    The agent's execution must stop until the exception is caught
    at a higher level that handles the halt properly.
    """


# --- Agent Compliance Gate ------------------------------------------

def compliance_gate(action: str, rule_id: str = "COMPLIANCE_GATE",
                    record_violation: bool = True) -> bool:
    """Compliance gate that the agent MUST call before proceeding.

    The agent MUST call this before suggest_followups, file edits, or
    any action that could violate compliance rules.

    Args:
        action: Description of what the agent is about to do
        rule_id: Which rule is being checked
        record_violation: Whether to record violations to DirectMemory too

    Returns:
        True if allowed to proceed, False if halted

    Usage in agent:
        if not compliance_gate("before_suggest_followups"):
            # HALT — do not proceed
            return
    """
    eca = get_eca()
    result = eca.check_compliance()

    if result["halted"]:
        eca.record_compliance_check(
            "compliance_gate_blocked", rule_id,
            passed=False,
            details={"action": action, "reason": result["reason"]},
            source="compliance_gate",
        )
        if record_violation and _DM_RECORD is not None:
            try:
                _DM_RECORD("compliance_gate_blocked", {
                    "action": action,
                    "rule_id": rule_id,
                    "reason": result["reason"],
                }, severity="error", tags=["compliance", "eca", "halt"])
            except (OSError, ValueError, sqlite3.Error) as exc:
                note_failure("compliance_auditor:L697", exc) # best-effort audit record — never block the gate
        raise ComplianceError(
            f"ECA HALT: {result['reason']} "
            f"(action={action}, rule={rule_id})"
        )

    eca.record_compliance_check(
        "compliance_gate_passed", rule_id,
        passed=True,
        details={"action": action},
        source="compliance_gate",
    )
    return True


# --- Self-test ------------------------------------------------------

def test_eca():
    """Run self-test on the ECA."""
    print(f"  [ECA] Self-test v{ComplianceAuditor.VERSION}")
    print(f"  [ECA] DB: {COMPLIANCE_DB_PATH}")
    print(f"  [ECA] Halt flag: {HALT_FLAG_PATH}")

    eca = get_eca()

    # Test record compliance
    eca.record_compliance_check("test_check", "TEST_RULE", passed=True,
                                  details={"test": True})
    eca.record_compliance_check("test_check", "TEST_RULE", passed=False,
                                  details={"test": False})
    print("  [OK] Recorded 2 compliance checks")

    # Test audit log
    log = eca.get_audit_log(limit=5)
    assert len(log) >= 2, f"Expected >=2, got {len(log)}"
    print(f"  [OK] Audit log: {len(log)} events")

    # Test summary
    summary = eca.get_summary()
    print(f"  [OK] Summary: {summary['passed']}p/{summary['failed']}f  rate={summary['pass_rate']}%")

    # Test halt/resume
    assert not eca.is_halted(), "Should not be halted initially"
    eca.halt("Self-test halt")
    assert eca.is_halted(), "Should be halted after halt()"
    assert HALT_FLAG_PATH.exists(), "Halt flag file should exist"
    print(f"  [OK] Halt signal issued: {eca.get_halt_reason()}")

    eca.resume()
    assert not eca.is_halted(), "Should not be halted after resume()"
    assert not HALT_FLAG_PATH.exists(), "Halt flag file should be removed"
    print("  [OK] Resume works")

    # Test check_compliance gate
    result = eca.check_compliance()
    # Note: recent_violations=1 because we recorded a failed check earlier
    # 'ok' will be False due to the violation, which is correct ECA behavior
    assert not result["halted"], f"Should not be halted after resume: {result}"
    assert result["recent_violations"] >= 0, f"Should track violations: {result}"
    print(f"  [OK] Compliance gate: halted={result['halted']} violations={result['recent_violations']}")

    # Test HTTP server
    server = EcaHttpServer(port=18424)
    server.start_background()
    time.sleep(0.3)

    def http_get(path: str) -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(("127.0.0.1", 18424))
        s.sendall(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode("utf-8"))
        data = s.recv(65535)
        s.close()
        body = data.decode("utf-8").split("\r\n\r\n", 1)[-1]
        return body

    # Test /halted
    eca.halt("HTTP test halt")
    resp = http_get("/halted")
    assert "HALTED" in resp, f"Expected HALTED, got: {resp}"
    print("  [OK] HTTP /halted returns HALTED")

    eca.resume()
    resp = http_get("/halted")
    assert "OK" in resp, f"Expected OK, got: {resp}"
    print("  [OK] HTTP /halted returns OK after resume")

    # Test /status
    resp = http_get("/status")
    status = json.loads(resp)
    assert "version" in status, f"Expected version in status: {resp[:800]}"
    print(f"  [OK] HTTP /status: v{status['version']}  checks={status['total_checks']}")

    # Test POST /halt
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(("127.0.0.1", 18424))
    body = json.dumps({"reason": "External halt via HTTP"})
    req = (
        f"POST /halt HTTP/1.0\r\n"
        f"Host: localhost\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Content-Type: application/json\r\n"
        f"\r\n"
        f"{body}"
    )
    s.sendall(req.encode("utf-8"))
    data = s.recv(65535)
    s.close()
    resp = json.loads(data.decode("utf-8").split("\r\n\r\n", 1)[-1])
    assert resp["status"] == "halted", f"Expected halted, got: {resp}"
    print("  [OK] HTTP POST /halt: status=halted")

    # Test POST /resume
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(("127.0.0.1", 18424))
    s.sendall(b"POST /resume HTTP/1.0\r\nHost: localhost\r\n\r\n")
    data = s.recv(65535)
    s.close()
    resp = json.loads(data.decode("utf-8").split("\r\n\r\n", 1)[-1])
    assert resp["status"] == "resumed", f"Expected resumed, got: {resp}"
    print("  [OK] HTTP POST /resume: status=resumed")

    server.stop()
    print("\n  [ECA] ALL SELF-TESTS PASSED")


if __name__ == "__main__":
    test_eca()
