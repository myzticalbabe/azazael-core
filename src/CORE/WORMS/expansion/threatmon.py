# coding: utf-8
"""
LIVE THREAT MONITOR — Category: MONITOR (1 worm)
Represents the threat-intel picture live: feed vitals, quorum findings,
urlscan corroboration, eBPF forests, ledger training flow.

House pattern (mirrors BerksMonitorWorm/DatabaseMonitorWorm): snapshot +
alert dataclasses, check_health(), PAMS + MemoryBus, start_monitor daemon.
Reads local model files only (no network in the worm; feeds pulled by jobs).

ThreatIntelMonitorWorm
"""

import json
import os
import threading
import time
from dataclasses import dataclass
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
    note_failure("threatmon:register-worm-import", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

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
                    _get_recorder = get_recorder  # singleton accessor (PamsRecorder.py:112-125)
                except ImportError as exc:
                    note_failure("threatmon:pams-import", exc)
                    return None
    try:
        return _get_recorder()  # one shared recorder, not a new one per call
    except Exception as exc:
        note_failure("threatmon:pams-construct", exc)
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
                    note_failure("threatmon:bus-import", exc)
                    return None
    return _mem_bus


@dataclass
class ThreatSnapshot:
    timestamp: str
    feed_rows: int = 0
    online_malicious: int = 0
    quorum_decided: int = 0
    ledger_triage_samples: int = 0
    urlscan_corroborated_hosts: int = 0
    ebpf_comms_baselined: int = 0
    healthy: bool = False
    # WHEN THE DATA IS FROM, not when it was read. `timestamp` above is the
    # read time, so without these the snapshot dated 14-hour-old feed files
    # as "now" and read as a live picture.
    feed_at: str = ""
    feed_age_s: float = -1.0


@dataclass
class ThreatAlert:
    alert_id: str
    severity: str
    message: str
    metric: str
    value: float
    threshold: float
    timestamp: str


MODELS = "G:/source-of-truth/azazael_models"

# The feed jobs are scheduled hourly (see scripts/threat_monitor.py), so six
# missed pulls is unambiguous staleness rather than jitter.
FEED_STALE_AFTER_S = 6 * 3600.0

# State files whose recency defines the picture's freshness.
FEED_FILES = ("threat_triage_v2.json", "threat_findings_v2.json",
              "urlhaus_findings.json", "urlscan_corrob.json",
              "bcc_baseline.json", "bcc_iforest_kali.json")


def _load(name: str, default):
    try:
        p = os.path.join(MODELS, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        note_failure("threatmon:feed-load", exc)
    return default


def feed_freshness(now=None) -> tuple:
    """(feed_at_iso, age_seconds) for the newest feed file, or ("", -1.0).

    A missing file is reported as unknown (-1.0), never as age zero: an
    unreadable timestamp is not a fresh one.
    """
    now = time.time() if now is None else now
    newest = None
    for name in FEED_FILES:
        try:
            m = os.path.getmtime(os.path.join(MODELS, name))
        except OSError as exc:
            note_failure("threatmon:feed-mtime", exc)
            continue
        if newest is None or m > newest:
            newest = m
    if newest is None:
        return "", -1.0
    return (datetime.fromtimestamp(newest, timezone.utc).isoformat(),
            max(0.0, now - newest))


@register_worm(category="monitor", priority=6, cooldown_seconds=900.0,
    description="Live threat-intel vitals: feeds, quorum, urlscan, ebpf, ledger")
class ThreatIntelMonitorWorm:
    """Live picture of the threat investigation. Local reads only."""

    VERSION = "1.0.0"

    def __init__(self):
        self._history: list = []
        self._lock = threading.Lock()

    def check_health(self) -> tuple:
        ts = datetime.now(timezone.utc).isoformat()
        feed_at, feed_age_s = feed_freshness()
        v2 = _load("threat_triage_v2.json", {})
        findings = _load("threat_findings_v2.json", [])
        v1 = _load("urlhaus_findings.json", {})
        v1_list = v1 if isinstance(v1, list) else v1.get("findings", [])
        corrob = _load("urlscan_corrob.json", {})
        bcc = _load("bcc_baseline.json", {}).get("baselines", {})
        kali = _load("bcc_iforest_kali.json", {})
        feed_rows = int(v2.get("n", 0))
        online = 0
        if isinstance(findings, list):
            online += sum(1 for f in findings if f.get("status") == "online")
        online += sum(1 for f in v1_list if f.get("status") == "online")
        # `healthy` used to be `feed_rows > 0` — a PRESENCE check. A feed job
        # that stopped left the row count at its last value, so the tier
        # reported healthy indefinitely on frozen data (measured 2026-09-14:
        # feed files 14.2 h old, hourly schedule, healthy=True). Presence is
        # not freshness, so the freshness condition is now part of the verdict.
        feed_stale = feed_age_s < 0 or feed_age_s > FEED_STALE_AFTER_S
        snap = ThreatSnapshot(
            timestamp=ts, feed_rows=feed_rows, online_malicious=online,
            quorum_decided=len(findings) if isinstance(findings, list) else 0,
            ledger_triage_samples=self._ledger_samples(),
            urlscan_corroborated_hosts=len(corrob.get("hosts", {})),
            ebpf_comms_baselined=len(bcc) + len(kali.get("features", [])) - (1 if "bio_per_s" in kali.get("features", []) else 0),
            healthy=feed_rows > 0 and not feed_stale,
            feed_at=feed_at, feed_age_s=feed_age_s,
        )
        alerts = []
        if not feed_rows:
            alerts.append(ThreatAlert("threat-nofeed", "warning", "no triage model present",
                                      "feed_rows", 0.0, 1.0, ts))
        if feed_rows and feed_stale:
            _age = "unknown" if feed_age_s < 0 else "%.1fh" % (feed_age_s / 3600.0)
            alerts.append(ThreatAlert(
                "threat-stalefeed", "warning",
                "feed data is %s old (limit %.0fh) -- the pull job has stalled"
                % (_age, FEED_STALE_AFTER_S / 3600.0),
                "feed_age_s", float(max(feed_age_s, 0.0)), FEED_STALE_AFTER_S, ts))
        if online > 0:
            alerts.append(ThreatAlert("threat-online", "info", f"{online} live-malicious URLs voted",
                                      "online_malicious", float(online), 1.0, ts))
        with self._lock:
            self._history.append(snap.__dict__)
            self._history = self._history[-200:]
        try:
            p = _pams()
            if p is not None and hasattr(p, "record_event"):
                p.record_event("threat_check", snap.__dict__)
        except Exception as exc:
            note_failure("threatmon:pams-record", exc)
        try:
            b = _bus()
            if b is not None and hasattr(b, "publish"):
                b.publish("monitor.threat", {"snapshot": snap.__dict__,
                                             "alerts": [a.__dict__ for a in alerts]})
        except Exception as exc:
            note_failure("threatmon:bus-publish", exc)
        return snap, alerts

    def _ledger_samples(self) -> int:
        try:
            from CORE import spine_registry as sp
            try:
                if hasattr(sp, "load_spine_state"):
                    sp.load_spine_state()
            except Exception as exc:
                note_failure("threatmon:spine-state-load", exc)
            view = sp.get_homeostat_view() if hasattr(sp, "get_homeostat_view") else {}
            return sum(v.get("successes", 0) + v.get("failures", 0)
                       for k, v in view.items() if k.startswith("triage:"))
        except Exception as exc:
            note_failure("threatmon:ledger-samples", exc)
            return 0

    def start_monitor(self, interval_seconds: float = 3600.0):
        stop = threading.Event()

        def loop():
            while not stop.wait(interval_seconds):
                try:
                    self.check_health()
                except Exception as exc:
                    note_failure("threatmon:monitor-loop", exc)
        t = threading.Thread(target=loop, name="threat-monitor", daemon=True)
        t.start()
        return stop

    def execute(self) -> dict[str, Any]:
        snap, alerts = self.check_health()
        return {"success": True, "method": "execute",
                "result": {"snapshot": snap.__dict__,
                           "alerts": [a.__dict__ for a in alerts]}}
