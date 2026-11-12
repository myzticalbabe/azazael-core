# coding: utf-8
"""


__all__ = [
    "HealthServer",
    "PrometheusServer",
    "get_health_json",
    "get_prometheus_metrics",
    "main",
]

CORE/health_server.py — Simple TCP health and Prometheus metrics endpoints for Azazael.

Provides:
- TCP health endpoint on port 18420 (JSON report)
- Prometheus-format metrics endpoint on port 18421 (text/plain)

Usage:
    python -m CORE.health_server              # Start health server on default port 18420
    python -m CORE.health_server --port 18421 # Custom port
    python -m CORE.health_server --check      # One-shot health check (connect and exit)
    python -m CORE.health_server --prometheus-port 18421  # Also start Prometheus endpoint

Check health from any tool:
    python -c "import socket; s=socket.socket(); s.connect(('localhost',18420)); print(s.recv(8192).decode()); s.close()"
"""

import sys
import json
import socket
import threading
import argparse
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Optional  # removed — all names have native equivalents


def get_health_json() -> str:
    """Get full health report as JSON string.

    Delegates to dashboard's get_report() for the comprehensive report,
    then adds health-endpoint-specific fields (breakers stats).
    """
    report = {}

    try:
        from CORE.dashboard import get_report
        report = get_report()
    except Exception as e:
        note_failure("health_server:L48", e)
        report["_error"] = str(e)

    # Health-endpoint-specific additions
    try:
        from CORE.circuit_breaker import breaker_registry
        report["breakers"] = breaker_registry.all_stats()
    except Exception as exc:
        note_failure("health_server:L55", exc)

    return json.dumps(report, indent=2, default=str)


def get_prometheus_metrics() -> str:
    """Get system metrics in Prometheus exposition format (text/plain).

    Exposes:
    - azazael_health_ok  (gauge: 0 or 1 for overall health)
    - azazael_components_total{status="ok|degraded|down"}
    - azazael_metric_count{name="..."}
    - azazael_metric_avg{name="..."}
    - azazael_total_memories
    - azazael_worms_total
    - azazael_orchestration_runs_total
    - azazael_training_runs_total
    - azazael_training_reward_avg
    """
    lines = []
    lines.append("# HELP azazael_health_ok Overall system health (1=ok, 0=down)")
    lines.append("# TYPE azazael_health_ok gauge")

    try:
        from CORE.metrics import health_check
        report = health_check.report()
        overall = 1 if report["overall"] == "ok" else 0
        lines.append(f"azazael_health_ok {overall}")
        for status, count in report["counts"].items():
            lines.append(f'azazael_components_total{{status="{status}"}} {count}')
    except Exception as exc:
        note_failure("health_server:L86", exc)
        lines.append("azazael_health_ok 0")

    # Metrics
    lines.append("")
    lines.append("# HELP azazael_metric_count Number of data points per metric")
    lines.append("# TYPE azazael_metric_count gauge")
    lines.append("# HELP azazael_metric_avg Average value per metric")
    lines.append("# TYPE azazael_metric_avg gauge")
    try:
        from CORE.metrics import metrics_collector
        snap = metrics_collector.snapshot()
        for name, agg in snap.items():
            if agg["count"] > 0:
                safe_name = name.replace(".", "_").replace(" ", "_")
                lines.append(f'azazael_metric_count{{name="{safe_name}"}} {agg["count"]}')
                lines.append(f'azazael_metric_avg{{name="{safe_name}"}} {agg["avg"]}')
    except Exception as exc:
        note_failure("health_server:L103", exc)

    # Memory + Training (single store query)
    lines.append("")
    lines.append("# HELP azazael_total_memories Total events in LocalMemoryStore")
    lines.append("# TYPE azazael_total_memories gauge")
    lines.append("# HELP azazael_total_sessions Total sessions in LocalMemoryStore")
    lines.append("# TYPE azazael_total_sessions gauge")
    lines.append("# HELP azazael_training_runs_total Total training pipeline runs recorded")
    lines.append("# TYPE azazael_training_runs_total counter")
    lines.append("# HELP azazael_training_reward_avg Average reward from last training run")
    lines.append("# TYPE azazael_training_reward_avg gauge")
    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        stats = store.get_stats()
        lines.append(f"azazael_total_memories {stats.get('total_memories', 0)}")
        lines.append(f"azazael_total_sessions {stats.get('total_sessions', 0)}")

        results = store.search_memories("ScheduledTrainingWorm", limit=10)
        lines.append(f'azazael_training_runs_total {len(results)}')
        if results:
            last = results[-1].get("data", {})
            if isinstance(last, dict):
                lines.append(f'azazael_training_reward_avg {last.get("avg_reward", 0)}')
    except Exception as exc:
        note_failure("health_server:L129", exc)

    # MetaCluster
    lines.append("")
    lines.append("# HELP azazael_worms_total Total registered worms across all natures")
    lines.append("# TYPE azazael_worms_total gauge")
    lines.append("# HELP azazael_orchestration_runs_total Total orchestration runs executed")
    lines.append("# TYPE azazael_orchestration_runs_total counter")
    try:
        from CORE.WORMS.METACLUSTER import MetaCluster
        mc = MetaCluster()
        state = mc.get_status()
        ncm = state.get("ncm", {})
        lines.append(f'azazael_worms_total {ncm.get("total_worms", 0)}')
        lines.append(f'azazael_orchestration_runs_total {state.get("orchestration_history", 0)}')
    except Exception as exc:
        note_failure("health_server:L145", exc)

    lines.append("")
    return "\n".join(lines)


class PrometheusServer:
    """Simple TCP server that responds with Prometheus-format metrics."""

    def __init__(self, host: str = "127.0.0.1", port: int = 18421):
        self.host = host
        self.port = port
        self.server: (socket.socket | None) = None
        self._running = threading.Event()

    def handle_client(self, conn: socket.socket):
        """Handle a single client connection — send Prometheus metrics."""
        try:
            metrics = get_prometheus_metrics()
            response = metrics.encode("utf-8")
            conn.sendall(response)
        except Exception as e:
            note_failure("health_server:L167", e)
            err = f"# ERROR: {e}"
            conn.sendall(err.encode("utf-8"))
        finally:
            conn.close()

    def start(self):
        """Start the Prometheus metrics server in the current thread."""
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((self.host, self.port))
        self.server.listen(5)
        self.server.settimeout(1.0)
        self._running.set()

        print(f"Prometheus metrics listening on {self.host}:{self.port}", flush=True)

        while self._running.is_set():
            try:
                conn, addr = self.server.accept()
                t = threading.Thread(target=self.handle_client, args=(conn,), daemon=True)
                t.start()
            except socket.timeout as exc:
                note_failure("health_server:L189", exc)
                continue
            except OSError as exc:
                note_failure("health_server:L191", exc)
                break

        print("Prometheus metrics stopped.", flush=True)

    def stop(self):
        self._running.clear()
        if self.server:
            try:
                self.server.close()
            except OSError as exc:
                note_failure("health_server:L201", exc)

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.start, daemon=True)
        t.start()
        return t


class HealthServer:
    """Simple TCP server that responds with JSON health report."""

    def __init__(self, host: str = "127.0.0.1", port: int = 18420):
        self.host = host
        self.port = port
        self.server: (socket.socket | None) = None
        self._running = threading.Event()

    def handle_client(self, conn: socket.socket):
        """Handle a single client connection."""
        try:
            health_json = get_health_json()
            conn.sendall(health_json.encode("utf-8"))
        except Exception as e:
            note_failure("health_server:L224", e)
            conn.sendall(json.dumps({"error": str(e)}).encode("utf-8"))
        finally:
            conn.close()

    def start(self):
        """Start the health server in the current thread."""
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((self.host, self.port))
        self.server.listen(5)
        self.server.settimeout(1.0)  # Allow periodic check of running flag
        self._running.set()
        
        print(f"Health server listening on {self.host}:{self.port}", flush=True)
        
        while self._running.is_set():
            try:
                conn, addr = self.server.accept()
                t = threading.Thread(target=self.handle_client, args=(conn,), daemon=True)
                t.start()
            except socket.timeout as exc:
                note_failure("health_server:L245", exc)
                continue
            except OSError as exc:
                note_failure("health_server:L247", exc)
                break
        
        print("Health server stopped.", flush=True)

    def stop(self):
        """Stop the health server."""
        self._running.clear()
        if self.server:
            try:
                self.server.close()
            except OSError as exc:
                note_failure("health_server:L258", exc)

    def start_background(self) -> threading.Thread:
        """Start the health server in a background daemon thread."""
        t = threading.Thread(target=self.start, daemon=True)
        t.start()
        return t


def main():
    parser = argparse.ArgumentParser(
        description="Azazael Health Endpoint — TCP health check + Prometheus metrics server"
    )
    parser.add_argument("--port", type=int, default=18420, help="Health JSON port")
    parser.add_argument("--check", action="store_true", help="One-shot check (connect and print)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--prometheus-port", type=int, default=None,
                        help="Also start Prometheus-format metrics endpoint on this port (e.g., 18421)")
    parser.add_argument("--prometheus-check", action="store_true",
                        help="One-shot query Prometheus endpoint and print")
    args = parser.parse_args()

    if args.prometheus_check:
        port = args.prometheus_port or 18421
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((args.host, port))
            data = s.recv(65535)
            print(data.decode("utf-8"))
            s.close()
        except ConnectionRefusedError:
            print(f"No Prometheus endpoint running on {args.host}:{port}")
            sys.exit(1)
        return

    if args.check:
        # Connect to existing server and print response
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((args.host, args.port))
            data = s.recv(65535)
            print(data.decode("utf-8"))
            s.close()
        except ConnectionRefusedError:
            print(json.dumps({"error": f"No health server running on {args.host}:{args.port}"}))
            sys.exit(1)
    else:
        # Start health server
        health_server = HealthServer(host=args.host, port=args.port)
        prom_server = None

        # Start Prometheus server if requested
        if args.prometheus_port:
            prom_server = PrometheusServer(host=args.host, port=args.prometheus_port)
            prom_server.start_background()
            print(f"Prometheus endpoint started on {args.host}:{args.prometheus_port}")

        try:
            health_server.start()
        except KeyboardInterrupt:
            print("\nShutting down...")
            health_server.stop()
            if prom_server:
                prom_server.stop()


if __name__ == "__main__":
    main()
