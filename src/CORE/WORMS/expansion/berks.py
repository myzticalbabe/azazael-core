# coding: utf-8
"""
FIRST-PARTY STATUS MONITOR — Category: MONITOR (1 worm)
Berks Counseling Center (Reading, PA) public-site watchdog.

Scope (explicit): the organization's own public websites only —
HTTP availability/latency, DNS resolution, TLS cert expiry. No client,
staff, or non-public data is touched. Targets are config, not hardcoded
secrets. Poll politely (30min cadence, single HEAD/GET, stdlib only).

BerksMonitorWorm
"""

import socket
import ssl
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("berks:register-worm-import", exc)
    def register_worm(
        category: str = "utility",
        priority: int = 5,
        cooldown_seconds: float = 30.0,
        description: str = "",
        tags=None,
    ):
        return lambda cls: cls

# -- PAMS + MemoryBus (lazy, double-checked locking for 3.14t) -------------
# Same house pattern as CLUSTERWORMS/METACLUSTER: audit trail + live pub/sub.
_get_recorder = None
_pams_lock = threading.Lock()
_MemoryBusCls = None
_bus_lock = threading.Lock()
_mem_bus = None


def _pams():
    global _get_recorder
    if _get_recorder is None:
        with _pams_lock:
            if _get_recorder is None:
                try:
                    from CORE.PamsRecorder import get_recorder
                    _get_recorder = get_recorder
                except ImportError as exc:
                    note_failure("berks:pams-import", exc)
                    return None
    try:
        return _get_recorder()
    except Exception as exc:
        note_failure("berks:pams-construct", exc)
        return None


def _bus():
    global _MemoryBusCls, _mem_bus
    if _mem_bus is None:
        with _bus_lock:
            if _mem_bus is None:
                try:
                    from CORE.MemoryBus import MemoryBus
                    _MemoryBusCls = MemoryBus
                    _mem_bus = MemoryBus()
                except ImportError as exc:
                    note_failure("berks:bus-import", exc)
                    return None
    return _mem_bus


from dataclasses import dataclass, field
from urllib.parse import urlparse



@dataclass
class BerksSiteSnapshot:
    """Per-site health snapshot (mirrors DbHealthSnapshot shape)."""
    timestamp: str
    url: str
    host: str
    up: bool
    http_status: int = 0
    http_ms: float = 0.0
    dns_ms: float = 0.0
    tls_days_left: float | None = None
    healthy: bool = False


@dataclass
class BerksAlert:
    """Severity alert (mirrors DbAlert: info/warning/critical)."""
    alert_id: str
    severity: str
    message: str
    metric: str
    value: float
    threshold: float
    timestamp: str


DEFAULT_TARGETS = ("https://www.berkscc.org/",
                   "https://www.berkscounselingcenter.org/")
SLOW_MS = 2000.0
TLS_WARN_DAYS = 30.0
TLS_CRIT_DAYS = 14.0


@register_worm(category="monitor", priority=6, cooldown_seconds=900.0,
    description="First-party watchdog: BCC public sites HTTP/DNS/TLS only")
class BerksMonitorWorm:
    """Polite public-site status monitor. Stdlib only, no keys needed."""

    VERSION = "1.0.0"

    def __init__(self, targets: tuple = DEFAULT_TARGETS, timeout: float = 15.0):
        self._targets = tuple(targets)
        self._timeout = timeout
        self._history: list = []
        self._lock = threading.Lock()

    def _check_one(self, url: str) -> dict[str, Any]:
        host = urlparse(url).hostname or ''
        rec: dict[str, Any] = {"url": url, "host": host,
                               "at": datetime.now(timezone.utc).isoformat()}
        # DNS
        try:
            t0 = time.perf_counter()
            rec["dns_ip"] = socket.gethostbyname(host)
            rec["dns_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        except Exception as e:
            note_failure("berks:dns-lookup", e)
            rec["dns_error"] = str(e)[:800]
        # TLS cert expiry
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=self._timeout) as s:
                with ctx.wrap_socket(s, server_hostname=host) as ts:
                    der = ts.getpeercert()
            exp = str(der.get("notAfter", "")) if isinstance(der, dict) else ""
            rec["tls_not_after"] = exp
            try:
                import calendar
                import time as _t
                exp_ts = calendar.timegm(_t.strptime(exp, "%b %d %H:%M:%S %Y %Z"))
                rec["tls_days_left"] = round((exp_ts - time.time()) / 86400, 1)
            except Exception as exc:
                note_failure("berks:tls-expiry-parse", exc)
                rec["tls_days_left"] = None
        except Exception as e:
            note_failure("berks:tls-probe", e)
            rec["tls_error"] = str(e)[:800]
        # HTTP: HEAD first (cheap); some hosts (e.g. berkscc.org) 405
        # HEAD but serve GET fine — fall back instead of crying DOWN.
        try:
            req = urllib.request.Request(url, method="HEAD",
                                         headers={"User-Agent": "BCC-Monitor/1.0"})
            t0 = time.perf_counter()
            r = urllib.request.urlopen(req, timeout=self._timeout)
            rec["http_status"] = r.status
            rec["http_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        except Exception as e:
            note_failure("berks:http-head", e)
            if "405" in str(e):
                try:
                    req = urllib.request.Request(
                        url, method="GET",
                        headers={"User-Agent": "BCC-Monitor/1.0"})
                    t0 = time.perf_counter()
                    r = urllib.request.urlopen(req, timeout=self._timeout)
                    r.read(1)  # confirm body flows, don't download the page
                    rec["http_status"] = r.status
                    rec["http_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                    rec["http_via"] = "GET-fallback"
                except Exception as e2:
                    note_failure("berks:http-get-fallback", e2)
                    rec["http_error"] = str(e2)[:150]
                    rec["http_status"] = 0
            else:
                rec["http_error"] = str(e)[:150]
                rec["http_status"] = 0
        rec["up"] = rec.get("http_status") in (200, 301, 302, 303, 307, 308)
        return rec

    def _record(self, event_type: str, data: dict[str, Any]):
        """PAMS audit trail (best-effort; never breaks monitoring)."""
        try:
            p = _pams()
            if p is not None and hasattr(p, "record_event"):
                p.record_event(event_type, data)
        except Exception as exc:
            note_failure("berks:pams-record", exc)

    def _publish(self, results: list, alerts: list):
        """MemoryBus live pub/sub for inter-agent coordination."""
        try:
            b = _bus()
            if b is not None and hasattr(b, "publish"):
                b.publish("monitor.berks", {"results": results,
                                            "alerts": [a.__dict__ for a in alerts]})
        except Exception as exc:
            note_failure("berks:bus-publish", exc)

    def _to_alerts(self, results: list) -> list:
        """Severity alerts from thresholds (mirrors DbAlert tiers)."""
        alerts = []
        for r in results:
            ts = r.get("at", datetime.now(timezone.utc).isoformat())
            if not r.get("up"):
                alerts.append(BerksAlert(
                    alert_id=f"berks-down-{r['host']}", severity="critical",
                    message=f"{r['host']} DOWN (http={r.get('http_status')})",
                    metric="up", value=0.0, threshold=1.0, timestamp=ts))
            elif (r.get("http_ms") or 0) > SLOW_MS:
                alerts.append(BerksAlert(
                    alert_id=f"berks-slow-{r['host']}", severity="warning",
                    message=f"{r['host']} slow ({r['http_ms']}ms)",
                    metric="http_ms", value=float(r["http_ms"]),
                    threshold=SLOW_MS, timestamp=ts))
            days = r.get("tls_days_left")
            if isinstance(days, (int, float)):
                if days < TLS_CRIT_DAYS:
                    alerts.append(BerksAlert(
                        alert_id=f"berks-tls-crit-{r['host']}", severity="critical",
                        message=f"{r['host']} TLS expires in {days}d",
                        metric="tls_days_left", value=float(days),
                        threshold=TLS_CRIT_DAYS, timestamp=ts))
                elif days < TLS_WARN_DAYS:
                    alerts.append(BerksAlert(
                        alert_id=f"berks-tls-warn-{r['host']}", severity="warning",
                        message=f"{r['host']} TLS expires in {days}d",
                        metric="tls_days_left", value=float(days),
                        threshold=TLS_WARN_DAYS, timestamp=ts))
        return alerts

    def check_health(self) -> tuple[list, list]:
        """House-pattern health check: (snapshots, alerts)."""
        results = [self._check_one(u) for u in self._targets]
        snaps = [BerksSiteSnapshot(
            timestamp=r.get("at", ""), url=r["url"], host=r["host"],
            up=bool(r["up"]), http_status=r.get("http_status", 0),
            http_ms=r.get("http_ms", 0.0) or 0.0,
            dns_ms=r.get("dns_ms", 0.0) or 0.0,
            tls_days_left=r.get("tls_days_left"),
            healthy=bool(r["up"]) and (r.get("http_ms") or 0) <= SLOW_MS,
        ) for r in results]
        alerts = self._to_alerts(results)
        with self._lock:
            self._history.extend(results)
            self._history = self._history[-200:]
        self._record("berks_check", {"up": sum(s.up for s in snaps),
                                     "of": len(snaps),
                                     "alerts": [a.__dict__ for a in alerts]})
        self._publish(results, alerts)
        return snaps, alerts

    def check_all(self) -> dict[str, Any]:
        snaps, alerts = self.check_health()
        with self._lock:
            n = len(self._history)
        return {"success": True,
                "results": [s.__dict__ for s in snaps],
                "alerts": [a.__dict__ for a in alerts],
                "up": sum(s.up for s in snaps), "of": len(snaps),
                "history": n}

    def start_monitor(self, interval_seconds: float = 1800.0):
        """Continuous monitoring daemon (mirrors DatabaseMonitorWorm)."""
        stop = threading.Event()

        def loop():
            while not stop.wait(interval_seconds):
                try:
                    self.check_health()
                except Exception as exc:
                    note_failure("berks:monitor-loop", exc)
        t = threading.Thread(target=loop, name="berks-monitor",
                             daemon=True)
        t.start()
        return stop

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.check_all()}
