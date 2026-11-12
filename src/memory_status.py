# coding: utf-8
"""
Memory Status Reporter - Unified Memory System Initializer

Loads and reports status of all persistent memory systems on startup:
- SQL persistent memory (graph.db)
- Active debug session recordings
- Dynamic logger
- Telemetry & metrics state

Usage:
    >>> from memory_status import MemoryStatus
    >>> ms = MemoryStatus()
    >>> ms.print_banner()
    >>> systems = ms.get_all_status()
"""

from pathlib import Path
from datetime import datetime
from typing import Any, List
from dataclasses import dataclass, field

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class SystemStatus:
    name: str
    available: bool
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class MemoryStatus:
    """Unified memory system status reporter.

    Loads and reports on all persistent memory systems in the Azazael ecosystem.
    """
    VERSION = "1.0.0"

    def __init__(self, project_root: (Path | None) = None):
        self.root = project_root or Path(__file__).resolve().parent.parent
        self.systems: dict[str, SystemStatus] = {}
        self._load_all()

    def _load_all(self):
        """Load all memory systems."""
        self.systems["sql_memory"] = self._check_sql_memory()
        self.systems["debug_sessions"] = self._check_debug_sessions()
        self.systems["dynamic_logger"] = self._check_dynamic_logger()
        self.systems["telemetry"] = self._check_telemetry()
        self.systems["metrics"] = self._check_metrics()
        self.systems["session_memory"] = self._check_session_memory()
        self.systems["hydrameta"] = self._check_hydrameta()

    # ------------------------------------------------------------------
    # SQL Persistent Memory (graph.db)
    # ------------------------------------------------------------------

    def _find_file(self, *relative_paths: str) -> (Path | None):
        """Search for a file in multiple locations relative to root."""
        candidates = [
            self.root.joinpath(*relative_paths),
            self.root.parent.joinpath(*relative_paths),
            self.root / "azazael" / relative_paths[0] if len(relative_paths) == 1 and not relative_paths[0].startswith("workspace") else None,
        ]
        for candidate in candidates:
            if candidate and candidate.exists():
                return candidate
        return None

    def _check_sql_memory(self) -> SystemStatus:
        status = SystemStatus(name="SQL Persistent Memory", available=False)
        graph_db = self._find_file("workspace", "graph.db")
        if graph_db is None:
            graph_db = self.root / "workspace" / "graph.db"  # for error message

        if not graph_db.exists():
            status.errors.append(f"graph.db not found at {graph_db}")
            return status

        try:
            import sqlite3
            with sqlite3.connect(str(graph_db), timeout=30) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()

                # Metadata
                meta = dict(c.execute("SELECT key, value FROM metadata").fetchall())

                # Node counts
                node_total = c.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
                edge_total = c.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
                flow_count = c.execute("SELECT COUNT(*) FROM flows").fetchone()[0]
                community_count = c.execute("SELECT COUNT(*) FROM communities").fetchone()[0]
                risk_count = c.execute("SELECT COUNT(*) FROM risk_index").fetchone()[0]
                snapshot_count = c.execute("SELECT COUNT(*) FROM flow_snapshots").fetchone()[0]

                # Node kinds distribution
                node_kinds = dict(c.execute(
                    "SELECT kind, COUNT(*) as cnt FROM nodes GROUP BY kind ORDER BY cnt DESC"
                ).fetchall())

                # Edge kinds distribution
                edge_kinds = dict(c.execute(
                    "SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind ORDER BY cnt DESC"
                ).fetchall())

                # High risk count
                high_risk = c.execute(
                    "SELECT COUNT(*) FROM risk_index WHERE risk_score >= 0.7"
                ).fetchone()[0]

            status.available = True
            status.details = {
                "path": str(graph_db),
                "schema_version": int(meta.get("schema_version", 0)),
                "last_updated": meta.get("last_updated", "unknown"),
                "nodes": {"total": node_total, "by_kind": node_kinds},
                "edges": {"total": edge_total, "by_kind": edge_kinds},
                "flows": flow_count,
                "communities": community_count,
                "risk_entries": risk_count,
                "high_risk_nodes": high_risk,
                "flow_snapshots": snapshot_count,
            }
        except Exception as e:
            note_failure("memory_status:L125", e)
            status.errors.append(f"SQL memory error: {e}")

        return status

    # ------------------------------------------------------------------
    # Debug Sessions
    # ------------------------------------------------------------------

    def _check_debug_sessions(self) -> SystemStatus:
        status = SystemStatus(name="Debug Sessions", available=False)
        debug_dir = self._find_file(".debug")
        if debug_dir is None:
            debug_dir = self.root / ".debug"  # for error message

        if not debug_dir.exists():
            status.errors.append(f"No .debug directory at {debug_dir}")
            return status

        try:
            sessions = sorted(debug_dir.glob("*"), reverse=True)
            session_data = []
            for session_dir in sessions:
                if not session_dir.is_dir():
                    continue
                report = session_dir / "report.json"
                summary = session_dir / "summary.txt"
                sesh = {
                    "id": session_dir.name,
                    "has_report": report.exists(),
                    "has_summary": summary.exists(),
                }
                if report.exists():
                    import json
                    try:
                        data = json.loads(report.read_text(encoding="utf-8"))
                        sesh["total_tests"] = data.get("total", 0)
                        sesh["passed"] = data.get("passed", 0)
                        sesh["failed"] = data.get("failed", 0)
                        sesh["timestamp"] = data.get("timestamp", "")
                    except Exception as exc:
                        note_failure("memory_status:L165", exc)
                session_data.append(sesh)

            status.available = True
            status.details = {
                "total_sessions": len(session_data),
                "latest": session_data[0] if session_data else None,
                "sessions": session_data[:10],
            }
        except Exception as e:
            note_failure("memory_status:L175", e)
            status.errors.append(f"Debug sessions error: {e}")

        return status

    # ------------------------------------------------------------------
    # Dynamic Logger
    # ------------------------------------------------------------------

    def _check_dynamic_logger(self) -> SystemStatus:
        status = SystemStatus(name="Dynamic Logger", available=False)
        log_dir = self.root / "src" / "logs"
        log_file = log_dir / "azazael_dynamic.log"

        try:
            if log_file.exists():
                size = log_file.stat().st_size
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                with open(log_file, "r", encoding="utf-8") as f:
                    line_count = sum(1 for _ in f)
                status.available = True
                status.details = {
                    "log_file": str(log_file),
                    "size_bytes": size,
                    "lines": line_count,
                    "last_modified": mtime.isoformat(),
                }
            else:
                status.available = True
                status.details = {
                    "log_file": str(log_file),
                    "status": "initialized, no entries yet",
                    "ready": True,
                }

            # Verify singleton can be created
            try:
                from UTILITIES.Loggers import DynamicLogger
                logger = DynamicLogger()
                status.details["singleton"] = "active"
            except Exception as exc:
                note_failure("memory_status:L215", exc)
                status.details["singleton"] = "not available"
        except Exception as e:
            note_failure("memory_status:L217", e)
            status.errors.append(f"Dynamic logger error: {e}")

        return status

    # ------------------------------------------------------------------
    # TELEMETRYZ
    # ------------------------------------------------------------------

    def _check_telemetry(self) -> SystemStatus:
        status = SystemStatus(name="TELEMETRYZ (Performance Tracing)", available=False)
        try:
            from TELEMETRYZ import TELEMETRYZ
            tz = TELEMETRYZ()
            tz_status = tz.get_status()
            status.available = True
            status.details = {
                "version": tz_status.get("version"),
                "tracing_enabled": tz_status.get("tracing_enabled"),
                "events_captured": tz_status.get("events_captured"),
                "functions_wrapped": tz_status.get("functions_wrapped"),
                "max_events": tz_status.get("max_events"),
            }
            # Check for saved trace
            trace_file = Path(tz.config.TRACE_FILE)
            if trace_file.exists():
                status.details["saved_trace"] = str(trace_file)
                status.details["trace_size"] = trace_file.stat().st_size
        except Exception as e:
            note_failure("memory_status:L245", e)
            status.errors.append(f"TELEMETRYZ error: {e}")
        return status

    # ------------------------------------------------------------------
    # METRICZ
    # ------------------------------------------------------------------

    def _check_metrics(self) -> SystemStatus:
        status = SystemStatus(name="METRICZ (Execution Telemetry)", available=False)
        try:
            from METRICZ import METRICZ
            mz = METRICZ()
            summary = mz.get_summary()
            status.available = True
            status.details = {
                "version": summary.get("version"),
                "metrics_collected": summary.get("metrics_collected"),
                "executions_recorded": summary.get("executions_recorded"),
                "active_timers": summary.get("active_timers"),
                "alerts_count": summary.get("alerts_count"),
                "failure_rate": summary.get("failure_rate"),
                "components_monitored": summary.get("components_monitored"),
            }
        except Exception as e:
            note_failure("memory_status:L269", e)
            status.errors.append(f"METRICZ error: {e}")
        return status

    # ------------------------------------------------------------------
    # Session Memory (session_memory.db)
    # ------------------------------------------------------------------

    def _check_session_memory(self) -> SystemStatus:
        status = SystemStatus(name="Session Memory (Cross-Session Learning)", available=False)
        try:
            from session_memory import SessionMemory
            db_path = str(self.root / "workspace" / "session_memory.db")
            sm = SessionMemory(db_path)
            stats = sm.get_statistics()
            status.available = True
            status.details = {
                "db_path": str(sm.db_path),
                "total_sessions": stats["total_sessions"],
                "total_decisions": stats["total_decisions"],
                "total_changes": stats["total_file_changes"],
                "total_lessons": stats["total_lessons"],
                "high_confidence_lessons": stats["high_confidence_lessons"],
                "goals": f"{stats['completed_goals']}/{stats['total_goals']}",
                "categories": stats.get("lesson_categories", {}),
            }
        except Exception as e:
            note_failure("memory_status:L295", e)
            status.errors.append(f"Session memory error: {e}")
        return status

    # ------------------------------------------------------------------
    # HYDRAMETA Persistence
    # ------------------------------------------------------------------

    def _check_hydrameta(self) -> SystemStatus:
        status = SystemStatus(name="HYDRAMETA Persistence", available=False)
        try:
            from HYDRAMETA import HYDRAMETA
            hm = HYDRAMETA()
            hm_status = hm.get_status()
            status.available = True
            status.details = {
                "version": hm_status.get("version"),
                "running": hm_status.get("running"),
                "total_runs": hm_status.get("total_runs"),
                "last_run": hm_status.get("last_run"),
                "trial_count": hm_status.get("trial_count"),
                "best_params": hm_status.get("best_params"),
            }
        except Exception as e:
            note_failure("memory_status:L318", e)
            status.errors.append(f"HYDRAMETA error: {e}")
        return status

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_all_status(self) -> dict[str, Any]:
        """Get all system statuses as a dictionary."""
        return {
            name: {
                "available": s.available,
                "details": s.details,
                "errors": s.errors,
            }
            for name, s in self.systems.items()
        }

    def print_banner(self):
        """Print the startup memory systems banner."""

        GREEN = "\033[92m"
        RED = "\033[91m"
        DIM = "\033[2m"
        BOLD = "\033[1m"
        RST = "\033[0m"
        CHECK = GREEN + "✓" + RST
        CROSS = RED + "✗" + RST

        def dim(t): return DIM + str(t) + RST
        def bold(t): return BOLD + str(t) + RST
        def ok(): return CHECK
        def fail(): return CROSS

        print()
        print("=" * 70)
        print("  AZAZAEL MEMORY SYSTEMS")
        print("=" * 70)

        # ── SQL Memory ──
        sql = self.systems["sql_memory"]
        if sql.available:
            d = sql.details
            sv = d.get("schema_version", 0)
            lu = d.get("last_updated", "unknown")
            print()
            print("  {0} {1}  {2}".format(ok(), bold("SQL Persistent Memory"), dim("v{0}  |  {1}".format(sv, lu))))
            print("     Path:     {0}".format(d["path"]))
            nodes = d["nodes"]
            nk = ", ".join("{0}: {1}".format(k, v) for k, v in nodes["by_kind"].items())
            print("     Nodes:    {0:,}  ({1})".format(nodes["total"], nk))
            edges = d["edges"]
            ek = ", ".join("{0}: {1}".format(k, v) for k, v in edges["by_kind"].items())
            print("     Edges:    {0:,}  ({1})".format(edges["total"], ek))
            print("     Flows: {0}  |  Communities: {1}  |  Risk entries: {2}".format(
                d["flows"], d["communities"], d["risk_entries"]))
            print("     Snapshots: {0}  |  High-risk nodes: {1}".format(
                d["flow_snapshots"], d["high_risk_nodes"]))
        else:
            print()
            print("  {0} {1}  {2}".format(fail(), bold("SQL Persistent Memory"), dim("not available")))
            for err in sql.errors:
                print("     {0}".format(err))

        # ── Debug Sessions ──
        debug = self.systems["debug_sessions"]
        if debug.available:
            d = debug.details
            latest = d.get("latest", {})
            print()
            print("  {0} {1}  {2}".format(ok(), bold("Debug Sessions"), dim("{0} sessions".format(d["total_sessions"]))))
            if latest:
                p = latest.get("passed", 0)
                t = latest.get("total_tests", 0)
                print("     Latest:  {0}  ({1}/{2} passed)".format(latest["id"], p, t))
            if d.get("sessions"):
                for s in d["sessions"][:4]:
                    pp = s.get("passed", "?")
                    tt = s.get("total_tests", "?")
                    print("       {0}  ({1}/{2})".format(s["id"], pp, tt))
        else:
            print()
            print("  {0} {1}  {2}".format(fail(), bold("Debug Sessions"), dim("none found")))

        # ── Dynamic Logger ──
        dlog = self.systems["dynamic_logger"]
        if dlog.available:
            d = dlog.details
            lines = d.get("lines", 0)
            size = d.get("size_bytes", 0)
            if lines > 0:
                print()
                print("  {0} {1}  {2}".format(ok(), bold("Dynamic Logger"),
                    dim("{0} entries  |  {1:,} bytes".format(lines, size))))
                print("     File:  {0}".format(d["log_file"]))
                print("     Last:  {0}".format(d.get("last_modified", "unknown")))
            else:
                print()
                print("  {0} {1}  {2}".format(ok(), bold("Dynamic Logger"), dim("ready (no entries yet)")))
                print("     File:  {0}".format(d["log_file"]))
        else:
            print()
            print("  {0} {1}  {2}".format(fail(), bold("Dynamic Logger"), dim("not available")))

        # ── TELEMETRYZ ──
        tele = self.systems["telemetry"]
        if tele.available and not tele.errors:
            d = tele.details
            ver = d.get("version", "?")
            evt = d.get("events_captured", 0)
            print()
            print("  {0} {1}  {2}".format(ok(), bold("TELEMETRYZ"),
                dim("v{0}  |  {1} events captured".format(ver, evt))))
        elif tele.errors:
            print()
            print("  {0} {1}  {2}".format(fail(), bold("TELEMETRYZ"), dim("not available")))

        # ── METRICZ ──
        met = self.systems["metrics"]
        if met.available and not met.errors:
            d = met.details
            ver = d.get("version", "?")
            mc = d.get("metrics_collected", 0)
            er = d.get("executions_recorded", 0)
            print()
            print("  {0} {1}  {2}".format(ok(), bold("METRICZ"),
                dim("v{0}  |  {1} metrics  |  {2} executions".format(ver, mc, er))))
        elif met.errors:
            print()
            print("  {0} {1}  {2}".format(fail(), bold("METRICZ"), dim("not available")))

        # ── Session Memory ──
        sesmem = self.systems.get("session_memory")
        if sesmem and sesmem.available and not sesmem.errors:
            d = sesmem.details
            ses = d.get("total_sessions", 0)
            dec = d.get("total_decisions", 0)
            chg = d.get("total_changes", 0)
            les = d.get("total_lessons", 0)
            goals = d.get("goals", "0/0")
            cats = ", ".join(f"{k}: {v}" for k, v in d.get("categories", {}).items())
            print()
            print("  {0} {1}  {2}".format(ok(), bold("Session Memory"),
                dim("{0} session(s)  |  {1} decisions  |  {2} changes".format(ses, dec, chg))))
            print("     Lessons: {0} ({1} high-confidence)  |  Goals: {2}".format(les, d.get("high_confidence_lessons", 0), goals))
            if cats:
                print("     Categories: {0}".format(cats))
        elif sesmem and sesmem.errors:
            print()
            print("  {0} {1}  {2}".format(fail(), bold("Session Memory"), dim("not available")))

        # ── HYDRAMETA ──
        hyd = self.systems["hydrameta"]
        if hyd.available and not hyd.errors:
            d = hyd.details
            ver = d.get("version", "?")
            runs = d.get("total_runs", 0)
            trials = d.get("trial_count", 0)
            print()
            print("  {0} {1}  {2}".format(ok(), bold("HYDRAMETA"),
                dim("v{0}  |  {1} runs  |  {2} trials".format(ver, runs, trials))))
        elif hyd.errors:
            print()
            print("  {0} {1}  {2}".format(fail(), bold("HYDRAMETA"), dim("not available")))

        # ── Summary ──
        available = sum(1 for s in self.systems.values() if s.available)
        total = len(self.systems)
        print(f"\n  {'─' * 66}")
        print(f"  {available}/{total} memory systems loaded")
        print("=" * 70)
        print()


def print_memory_banner(root: (Path | None) = None):
    """Convenience function to print the memory status banner.

    Args:
        root: Project root directory. Auto-detected if None.
    """
    ms = MemoryStatus(root)
    ms.print_banner()


if __name__ == "__main__":
    print_memory_banner()
