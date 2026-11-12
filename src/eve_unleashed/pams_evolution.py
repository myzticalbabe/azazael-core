"""
Eve Unleashed — PAMS Memory Evolution (Phase 1, V7.3).

Semantic memory search, importance scoring, auto-pruning retention windows,
and the memory-to-insight pipeline (PamsRecorder -> DistillZ recommendations).

Fail-open: every entry point degrades gracefully when PAMS/LocalMemoryStore is
absent; nothing here raises to the caller.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: Any = None) -> None:
        pass


_WORD_RE = re.compile(r"[a-z0-9_]{3,}")


@dataclass
class MemoryImportance:
    """Scored importance for a single memory/event."""
    event_id: str
    source: str
    event_type: str
    severity: str
    importance: float          # 0.0 (routine) -> 1.0 (critical)
    factors: Dict[str, float] = field(default_factory=dict)


class PamSEvolution:
    """PAMS memory evolution layer over PamsRecorder + DistillZ."""

    #: Severity -> base importance weight.
    SEVERITY_WEIGHT = {
        "critical": 1.0,
        "error": 0.8,
        "warning": 0.6,
        "info": 0.3,
        "debug": 0.1,
    }

    #: Event types that are always high-signal.
    SIGNAL_EVENT_TYPES = {
        "anomaly_detected", "worm_execution", "morph_event",
        "inference_bridge_intelligence", "autonomous_action",
        "model_swap", "service_restart",
    }

    def __init__(self, recorder=None, distillz=None):
        self.recorder = recorder
        self.distillz = distillz
        self._retention_days = 30  # configurable auto-prune window

    # -- Recorder access ------------------------------------------------------

    def _get_recorder(self):
        if self.recorder is not None:
            return self.recorder
        try:
            from CORE.PamsRecorder import get_recorder
            self.recorder = get_recorder()
            return self.recorder
        except Exception as exc:
            note_failure("pams_evolution:recorder", exc)
            return None

    # -- Semantic memory search ----------------------------------------------

    def _token_overlap(self, query: str, text: str) -> float:
        q = set(_WORD_RE.findall(query.lower()))
        t = set(_WORD_RE.findall(text.lower()))
        if not q or not t:
            return 0.0
        return len(q & t) / len(q | t)

    def semantic_search(self, query: str, limit: int = 50,
                        source: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Semantic (token-overlap) memory search over PAMS events.

        Builds on PamsRecorder.search_memories then re-ranks by lexical
        overlap against the query — an embedding-free semantic proxy that
        works on bare interpreters.
        """
        rec = self._get_recorder()
        if rec is None:
            return []
        try:
            candidates = rec.search_memories(query=query, source=source, limit=limit * 4)
        except Exception as exc:
            note_failure("pams_evolution:search_memories", exc)
            return []
        scored = []
        for evt in candidates:
            blob = " ".join(str(evt.get(k, "")) for k in
                            ("event_type", "source", "severity", "data"))
            score = self._token_overlap(query, blob)
            if score > 0:
                scored.append((score, evt))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [evt for _, evt in scored[:limit]]

    # -- Importance scoring ---------------------------------------------------

    def score_importance(self, evt: Dict[str, Any]) -> MemoryImportance:
        """Score a single event's importance (0.0->1.0) from severity/type."""
        sev = str(evt.get("severity", "info")).lower()
        etype = str(evt.get("event_type", "")).lower()
        base = self.SEVERITY_WEIGHT.get(sev, 0.3)
        boost = 0.25 if etype in {t.lower() for t in self.SIGNAL_EVENT_TYPES} else 0.0
        importance = min(1.0, base + boost)
        factors = {"severity_base": base, "signal_boost": boost}
        return MemoryImportance(
            event_id=str(evt.get("id", evt.get("timestamp", ""))),
            source=evt.get("source", "unknown"),
            event_type=etype,
            severity=sev,
            importance=importance,
            factors=factors,
        )

    def tag_importance(self, limit: int = 200) -> List[MemoryImportance]:
        """Score recent memories and return importance-ranked list."""
        rec = self._get_recorder()
        if rec is None:
            return []
        try:
            events = rec.query_memories(limit=limit)
        except Exception as exc:
            note_failure("pams_evolution:query_memories", exc)
            return []
        scored = [self.score_importance(e) for e in events]
        scored.sort(key=lambda m: m.importance, reverse=True)
        return scored

    # -- Auto-pruning with retention window ----------------------------------

    def set_retention_days(self, days: int):
        self._retention_days = max(1, int(days))

    def prune(self, older_than_days: Optional[int] = None) -> int:
        """
        Auto-prune memories older than the retention window.

        Delegates to LocalMemoryStore when available (real SQL DELETE);
        otherwise trims the JSON fallback buffer. Returns pruned count.
        """
        days = older_than_days if older_than_days is not None else self._retention_days
        rec = self._get_recorder()
        if rec is None:
            return 0
        store = None
        try:
            rec._ensure_local_store()
            store = rec._local_store
        except Exception as exc:
            store = None
            note_failure("pams_evolution:ensure_local_store", exc)
        cutoff = time.time() - days * 86400
        pruned = 0
        if store is not None and getattr(rec, "_local_ok", False):
            try:
                pruned = store.prune_before(cutoff)  # may not exist on store
            except AttributeError:
                pruned = 0
            except Exception as exc:
                note_failure("pams_evolution:prune_store", exc)
        else:
            # fallback buffer trim
            before = len(rec._fallback_buffer)
            rec._fallback_buffer = [
                e for e in rec._fallback_buffer
                if _parse_ts(e.get("timestamp", "")) >= cutoff
            ]
            rec._save_fallback()
            pruned = before - len(rec._fallback_buffer)
        return pruned


def _parse_ts(ts: str) -> float:
    try:
        return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception as exc:
        note_failure("pams_evolution:parse_ts", exc)
        return 0.0


# -- Memory-to-insight pipeline (PAMS -> DistillZ -> recommendations) -------

def distill_to_insights(use_llm: bool = False) -> Dict[str, Any]:
    """
    Run the full memory-to-insight pipeline.

    DistillZ over PAMS/LocalMemoryStore -> insights + recommendations.
    Returns a serializable dict (never raises).
    """
    try:
        from META.DistillZ import DistillZ
        dz = DistillZ()
        report = dz.distill(use_llm=use_llm)
        return {
            "version": report.version,
            "total_insights": report.total_insights,
            "total_sources": report.total_sources,
            "insights": [
                {"source": i.source, "insight": i.insight,
                 "importance": i.importance} for i in report.insights
            ],
            "recommendations": list(report.recommendations),
            "source_stats": dict(report.source_stats),
        }
    except Exception as exc:
        note_failure("pams_evolution:distill_to_insights", exc)
        return {"version": "", "total_insights": 0, "total_sources": 0,
                "insights": [], "recommendations": [],
                "source_stats": {}, "error": str(exc)}


__all__ = ["PamSEvolution", "MemoryImportance", "distill_to_insights"]
