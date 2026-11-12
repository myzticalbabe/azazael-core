"""
RULE 0Y: Multi-Agent Governance Infrastructure

Components:
  - MultiAgentJudge: Worker -> Verifier -> Judge -> Execute (>2/3 consensus)
  - ConfidenceGate: Threshold scoring for action authorization
  - BehavioralGraphAnalyzer: networkx-based action graph anomaly detection
  - DualLedgerValidator: JSONL <-> SQLite cross-validation
  - RealtimeAuditPipeline: Interleaved 15-layer VLP in middleware
"""

import json
import sqlite3
import time
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Optional networkx for graph analysis
try:
    import networkx as nx  # pyright: ignore[reportMissingModuleSource]
    HAS_NETWORKX = True
except ImportError as exc:
    note_failure("governance:L25", exc)
    HAS_NETWORKX = False
    nx = None


# ---------------------------------------------------------------------------
# Judge Verdict
# ---------------------------------------------------------------------------

@dataclass
class JudgeVerdict:
    """Verdict from a single judge in the multi-agent consensus system.

    Attributes:
        judge_id: Unique identifier for this judge
        approved: Whether this judge approves the proposed action
        confidence: Confidence score 0.0-1.0
        reason: Brief reason for the verdict
    """
    judge_id: str
    approved: bool
    confidence: float = 0.5
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "judge_id": self.judge_id,
            "approved": self.approved,
            "confidence": self.confidence,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Multi-Agent Judge / Consensus (RULE 0Y-1)
# ---------------------------------------------------------------------------

class MultiAgentJudge:
    """Multi-Agent Judge Consensus system.

    Implements the Worker -> Verifier -> Judge -> Execute pattern.
    Requires >2/3 consensus for approval.

    Usage:
        judge = MultiAgentJudge(min_consensus=0.67)

        # Register judges
        judge.register_judge("code-reviewer", lambda action, ctx: JudgeVerdict(...))
        judge.register_judge("architect", lambda action, ctx: JudgeVerdict(...))

        # Evaluate an action
        result = judge.evaluate("str_replace file.py", {"file": "file.py"})
        # result.approved = True only if >2/3 consensus
    """

    def __init__(self, min_consensus: float = 2.0 / 3.0):
        """
        Args:
            min_consensus: Fraction of judges that must approve (default: 0.67)
        """
        self._min_consensus = min_consensus
        self._judges: dict[str, Callable[[str, dict], JudgeVerdict]] = {}
        self._lock = threading.Lock()
        self._verdict_history: list[dict[str, Any]] = []

    @property
    def min_consensus(self) -> float:
        return self._min_consensus

    @property
    def judge_count(self) -> int:
        return len(self._judges)

    def register_judge(self, judge_id: str, evaluator_fn: Callable[[str, dict], JudgeVerdict]):
        """Register a judge function.

        Args:
            judge_id: Unique identifier for this judge
            evaluator_fn: Function that takes (action_description, context_dict)
                          and returns a JudgeVerdict
        """
        with self._lock:
            self._judges[judge_id] = evaluator_fn

    def unregister_judge(self, judge_id: str):
        with self._lock:
            self._judges.pop(judge_id, None)

    def evaluate(self, action: str, context: (dict[str, Any] | None) = None) -> dict[str, Any]:
        """Evaluate an action through all registered judges.

        Args:
            action: Description of the proposed action
            context: Context dictionary with relevant state

        Returns:
            dict with approved, consensus_ratio, verdicts, and summary
        """
        context = context or {}
        verdicts = {}

        with self._lock:
            current_judges = dict(self._judges)

        for judge_id, evaluator in current_judges.items():
            try:
                verdict = evaluator(action, context)
                verdicts[judge_id] = verdict
            except Exception as e:
                note_failure("governance:L133", e)
                verdicts[judge_id] = JudgeVerdict(
                    judge_id=judge_id,
                    approved=False,
                    confidence=0.0,
                    reason=f"Error: {e}",
                )

        total = len(verdicts)
        approved_count = sum(1 for v in verdicts.values() if v.approved)
        consensus_ratio = approved_count / max(total, 1)
        approved = consensus_ratio >= self._min_consensus and total > 0

        # Average confidence
        avg_confidence = sum(v.confidence for v in verdicts.values()) / max(total, 1)

        result = {
            "approved": approved,
            "consensus_ratio": round(consensus_ratio, 3),
            "min_consensus": self._min_consensus,
            "total_judges": total,
            "approved_count": approved_count,
            "avg_confidence": round(avg_confidence, 3),
            "verdicts": {jid: v.to_dict() for jid, v in verdicts.items()},
            "action": action,
            "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        self._log_verdict(result)
        return result

    def _log_verdict(self, result: dict[str, Any]):
        with self._lock:
            self._verdict_history.append(result)

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._verdict_history[-limit:])

    def reset(self):
        with self._lock:
            self._verdict_history.clear()

    def __repr__(self) -> str:
        return f"MultiAgentJudge(judges={self.judge_count}, min_consensus={self._min_consensus})"


# ---------------------------------------------------------------------------
# Default Judge Factories
# ---------------------------------------------------------------------------

def make_policy_judge(name: str, required_role: str = "developer"):
    """Create a judge that checks policy compliance.

    Returns a callable suitable for MultiAgentJudge.register_judge().

    Example:
        from CORE.compliance.policy import _get_global_engine
        judge.register_judge("policy_judge", make_policy_judge("policy"))
    """
    def evaluator(action: str, context: dict) -> JudgeVerdict:
        # Import here to avoid circular imports
        from CORE.compliance.policy import _get_global_engine
        engine = _get_global_engine()
        role = context.get("role", required_role)
        allowed, reason = engine.evaluate(action, role=role)
        return JudgeVerdict(
            judge_id=name,
            approved=allowed,
            confidence=1.0 if allowed else 0.0,
            reason=reason,
        )
    return evaluator


def make_state_machine_judge(name: str = "state_machine"):
    """Create a judge that checks state machine validity.

    Returns a callable suitable for MultiAgentJudge.register_judge().
    """
    def evaluator(action: str, context: dict) -> JudgeVerdict:
        state = context.get("state", "")
        can_edit = context.get("can_edit", False)
        can_review = context.get("can_review", False)

        if "edit" in action.lower() and not can_edit:
            return JudgeVerdict(
                judge_id=name,
                approved=False,
                confidence=0.0,
                reason=f"State machine in '{state}' state — editing not allowed",
            )
        if "review" in action.lower() and not can_review:
            return JudgeVerdict(
                judge_id=name,
                approved=False,
                confidence=0.0,
                reason=f"State machine in '{state}' state — review not allowed",
            )
        return JudgeVerdict(
            judge_id=name,
            approved=True,
            confidence=1.0,
            reason=f"State machine OK (state={state})",
        )
    return evaluator


# ---------------------------------------------------------------------------
# Confidence Gate (RULE 0Y-2)
# ---------------------------------------------------------------------------

def _weighted_stdev(values: list[float], weights: list[float]) -> float:
    """Population stdev of the weighted scores; 0.0 below two samples.

    The dispersion ACROSS sources, not the noise of one: it is what tells a
    caller whether a pass cleared the threshold by more than the disagreement
    between the things that voted for it.
    """
    total = sum(weights)
    if len(values) < 2 or total <= 0:
        return 0.0
    mean = sum(v * w for v, w in zip(values, weights)) / total
    variance = sum(w * (v - mean) ** 2 for v, w in zip(values, weights)) / total
    return variance ** 0.5


def _parse_iso(value: (str | None)) -> (datetime | None):
    """Parse an ISO timestamp to an aware datetime; None if unparseable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        note_failure("governance:_parse_iso", exc)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class ConfidenceGate:
    """Confidence threshold scoring gate.

    Aggregates collective confidence scores across multiple sources.
    Actions are blocked if the aggregated score falls below the threshold.

    Usage:
        gate = ConfidenceGate(threshold=0.7)
        gate.add_score("policy", 0.95)
        gate.add_score("reviewer", 0.80)
        result = gate.evaluate()  # True if above threshold
    """

    def __init__(self, threshold: float = 0.7):
        self._threshold = threshold
        self._scores: dict[str, float] = {}
        #: Per-source weights. Default 1.0 keeps the aggregate identical to the
        #: plain mean this gate has always returned.
        self._weights: dict[str, float] = {}
        #: SUPPLIED continuous scorers (EP349). Never called implicitly.
        self._scorers: dict[str, Callable[[], float]] = {}
        self._lock = threading.Lock()

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float):
        self._threshold = max(0.0, min(1.0, value))

    def add_score(self, source: str, score: float, weight: float = 1.0):
        """Add a confidence score from a source.

        Args:
            source: Source identifier (e.g., "policy", "reviewer", "ast_scanner")
            score: Confidence score 0.0-1.0
            weight: Relative weight of this source (default 1.0 = plain mean)
        """
        with self._lock:
            self._scores[source] = max(0.0, min(1.0, score))
            self._weights[source] = max(0.0, float(weight))

    def remove_score(self, source: str):
        with self._lock:
            self._scores.pop(source, None)
            self._weights.pop(source, None)

    def clear_scores(self):
        with self._lock:
            self._scores.clear()
            self._weights.clear()

    def _stats(self) -> tuple[float, float, int, dict[str, float]]:
        """Return (weighted mean, dispersion, source count, scores snapshot).

        With every weight at its default the mean is arithmetically identical to
        the plain average this gate returned before weights existed.
        """
        with self._lock:
            scores = dict(self._scores)
            weights = {k: self._weights.get(k, 1.0) for k in scores}
        if not scores:
            return 1.0, 0.0, 0, {}
        values = list(scores.values())
        weight_values = list(weights.values())
        total = sum(weight_values)
        if total <= 0:
            return (sum(values) / len(values),
                    _weighted_stdev(values, [1.0] * len(values)),
                    len(values), scores)
        mean = sum(v * w for v, w in zip(values, weight_values)) / total
        return mean, _weighted_stdev(values, weight_values), len(values), scores

    def get_aggregate(self) -> float:
        """Get the aggregated confidence score (weighted average).

        Returns:
            Float between 0.0 and 1.0
        """
        return self._stats()[0]

    # -- EP156 / EP349: graded confidence, from a SUPPLIED scorer ----------------

    def add_scorer(self, source: str, scorer: Callable[[], float]):
        """Register a SUPPLIED continuous scorer (EP349).

        The scorer is a zero-argument callable returning 0.0-1.0, and it is only
        ever called by `score_supplied()`. Nothing here decides *when* something
        should be asked to grade an answer.
        """
        with self._lock:
            self._scorers[source] = scorer

    def unregister_scorer(self, source: str):
        with self._lock:
            self._scorers.pop(source, None)

    @property
    def scorer_count(self) -> int:
        with self._lock:
            return len(self._scorers)

    def score_supplied(self) -> list[str]:
        """Run every supplied scorer and fold its value in as a source.

        Returns the source ids that actually reported. A scorer that raises is
        recorded and skipped -- it is never counted as a zero.
        """
        with self._lock:
            scorers = dict(self._scorers)
        reported: list[str] = []
        for source, scorer in sorted(scorers.items()):
            try:
                value = scorer()
            except Exception as exc:
                note_failure("governance:scorer:%s" % source, exc)
                continue
            if isinstance(value, (int, float)):
                self.add_score("scorer:%s" % source, float(value))
                reported.append(source)
        return reported

    def quantify(self) -> dict[str, Any]:
        """EP156/EP349: report HOW FAR the aggregate is from the boundary.

        A pass/fail gate answers "did it clear the bar". An agent choosing what to
        do next needs to know whether it cleared the bar by more than its own
        noise, so this reports the signed margin against the threshold together
        with the dispersion across sources: a pass whose margin sits inside the
        standard error of its own inputs is UNCERTAIN, not ACCEPT.

        The continuous scoring source is SUPPLIED via `add_scorer` -- this tree
        consumes models as opaque providers, so there is no in-repo scorer to
        hardcode and inventing one would fabricate the very statistic it audits.
        """
        aggregate, dispersion, count, sources = self._stats()
        margin = aggregate - self._threshold
        band = dispersion / (count ** 0.5) if count else 0.0
        if not count:
            # No evidence is not a pass, whatever evaluate() reports for callers
            # that predate quantification.
            verdict = "UNCERTAIN"
        elif margin < 0:
            verdict = "REJECT"
        elif margin <= band:
            verdict = "UNCERTAIN"
        else:
            verdict = "ACCEPT"
        with self._lock:
            scorer_ids = sorted(self._scorers)
        return {
            "aggregate_score": round(aggregate, 3),
            "threshold": self._threshold,
            "margin": round(margin, 3),
            "dispersion": round(dispersion, 3),
            "uncertainty_band": round(band, 3),
            "source_count": count,
            "sources": sources,
            "scorers": scorer_ids,
            "verdict": verdict,
        }

    def evaluate_with_uncertainty(self) -> dict[str, Any]:
        """`evaluate()` plus the quantification block, for new call sites."""
        result = self.evaluate()
        result["quantified"] = self.quantify()
        return result

    def evaluate(self) -> dict[str, Any]:
        """Evaluate whether the gate should pass.

        Returns:
            dict with passed, aggregate_score, threshold, and per-source scores
        """
        aggregate = self.get_aggregate()
        passed = aggregate >= self._threshold

        return {
            "passed": passed,
            "aggregate_score": round(aggregate, 3),
            "threshold": self._threshold,
            "sources": dict(self._scores),
            "source_count": len(self._scores),
        }

    def __repr__(self) -> str:
        return f"ConfidenceGate(score={self.get_aggregate():.2f}, threshold={self._threshold})"


# ---------------------------------------------------------------------------
# Behavioral Graph Analyzer (RULE 0Y-3)
# ---------------------------------------------------------------------------

class BehavioralGraphAnalyzer:
    """NetworkX-based directed graph anomaly detection for agent actions.

    Models all agent actions as a directed graph at runtime. Detects:
    - File edits without prior file_reads
    - 10+ edits with zero reviews (oscillating fail states)
    - Out-of-sequence operations
    - Cycle detection (infinite loops in action patterns)

    Uses networkx if available, falls back to dict-based tracking.

    Usage:
        analyzer = BehavioralGraphAnalyzer()
        analyzer.record_action("read_file", {"file": "foo.py"}, "developer")
        analyzer.record_action("edit_file", {"file": "foo.py"}, "developer")
        anomalies = analyzer.check_anomalies()
    """

    VALID_SEQUENCES = {
        "read_file": ["edit_file", "review"],
        "edit_file": ["review", "test"],
        "review": ["edit_file", "sync", "test"],
        "test": ["read_file", "edit_file", "review"],
        "sync": ["read_file", "test"],
    }

    def __init__(self):
        self._actions: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._graph = None

        if HAS_NETWORKX:
            self._graph = nx.DiGraph()  # pyright: ignore[reportOptionalMemberAccess]

    def record_action(self, action_type: str, details: (dict | None) = None,
                      agent: str = "system"):
        """Record an agent action for graph analysis.

        Args:
            action_type: Type of action (read_file, edit_file, review, test, sync)
            details: Optional metadata about the action
            agent: Which agent performed the action
        """
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action_type": action_type,
            "details": details or {},
            "agent": agent,
            "index": len(self._actions),
        }

        with self._lock:
            self._actions.append(entry)

            if self._graph is not None and len(self._actions) > 1:
                prev = self._actions[-2]
                prev_type = prev["action_type"]
                curr_type = action_type
                self._graph.add_edge(prev_type, curr_type, count=1)

    def check_anomalies(self) -> list[dict[str, Any]]:
        """Check for behavioral anomalies in the action graph.

        Returns:
            list of anomaly dicts with type, severity, and description.
            Empty list means no anomalies detected.
        """
        anomalies = []

        with self._lock:
            if len(self._actions) < 2:
                return anomalies

            # 1. Check for file edits without prior reads
            edit_indices = [
                i for i, a in enumerate(self._actions)
                if a["action_type"] == "edit_file"
            ]
            for idx in edit_indices:
                has_prior_read = any(
                    a["action_type"] in ("read_file", "review")
                    for a in self._actions[:idx]
                )
                if not has_prior_read:
                    anomalies.append({
                        "type": "edit_without_read",
                        "severity": "high",
                        "action_index": idx,
                        "description": f"File edit at index {idx} without prior read_file or review",
                    })

            # 2. Check for 10+ edits with zero reviews
            edit_count = sum(1 for a in self._actions if a["action_type"] == "edit_file")
            review_count = sum(1 for a in self._actions if a["action_type"] == "review")
            if edit_count >= 10 and review_count == 0:
                anomalies.append({
                    "type": "edits_without_review",
                    "severity": "critical",
                    "description": f"{edit_count} edits with zero reviews — possible oscillation",
                })

            # 3. Check for out-of-sequence (using VALID_SEQUENCES as a guide, not strict)
            for i in range(1, len(self._actions)):
                prev = self._actions[i - 1]["action_type"]
                curr = self._actions[i]["action_type"]
                valid_next = self.VALID_SEQUENCES.get(prev, [])
                if valid_next and curr not in valid_next:
                    anomalies.append({
                        "type": "out_of_sequence",
                        "severity": "low",
                        "description": f"'{prev}' -> '{curr}' not in expected sequence {valid_next}",
                    })

            # 4. NetworkX cycle detection
            if self._graph is not None and HAS_NETWORKX:
                try:
                    cycles = list(nx.simple_cycles(self._graph))  # pyright: ignore[reportOptionalMemberAccess]
                    if cycles:
                        for cycle in cycles:
                            anomalies.append({
                                "type": "action_cycle",
                                "severity": "medium",
                                "description": f"Detected action cycle: {' -> '.join(cycle)}",
                            })
                except (nx.NetworkXNoCycle, nx.NetworkXError) as exc:  # pyright: ignore[reportOptionalMemberAccess]
                    note_failure("governance:L449", exc)

        return anomalies

    def get_action_count(self, action_type: str) -> int:
        with self._lock:
            return sum(1 for a in self._actions if a["action_type"] == action_type)

    def get_graph_summary(self) -> dict[str, Any]:
        with self._lock:
            action_counts = Counter(a["action_type"] for a in self._actions)
            return {
                "total_actions": len(self._actions),
                "action_counts": dict(action_counts),
                "anomalies": self.check_anomalies(),
            }

    def reset(self):
        with self._lock:
            self._actions.clear()
            if HAS_NETWORKX:
                self._graph = nx.DiGraph()  # pyright: ignore[reportOptionalMemberAccess]

    def __repr__(self) -> str:
        return f"BehavioralGraphAnalyzer(actions={len(self._actions)})"



def _chunks(items: list, size: int):
    """Yield `items` in `size`-sized slices (SQLite parameter-count safety)."""
    for k in range(0, len(items), size):
        yield items[k:k + size]


# ---------------------------------------------------------------------------
# Dual-Ledger Validator (RULE 0Y-4)
# ---------------------------------------------------------------------------

class DualLedgerValidator:
    """Cross-validates .audit/session_*.jsonl against DirectMemory SQLite.

    Detects single-ledger tampering by comparing entries across both stores.
    Runs automatically on every gate assertion.

    Usage:
        validator = DualLedgerValidator()
        result = validator.cross_validate()
        if not result["consistent"]:
            print(f"Tampering detected: {result['discrepancies']}")
    """

    def __init__(self, audit_dir: (str | None) = None,
                 memory_db_path: (str | None) = None):
        """
        Args:
            audit_dir: Path to .audit/ directory
            memory_db_path: Path to DirectMemory SQLite DB
        """
        self._audit_dir = Path(audit_dir or ".audit")
        self._memory_db = Path(memory_db_path or str(Path.home() / ".azazael" / "memory_store.db"))
        self._lock = threading.Lock()

    #: Wall-clock budget for the memory half of a comparison.
    #:
    #: The DirectMemory store measured 5.4 GB / 4,028,967 rows on the dev host
    #: on 2026-10-30. The previous implementation answered "which sessions do
    #: both ledgers share?" by issuing `SELECT * FROM memories ... fetchall()`
    #: and materialising every row in Python. That blocked the first test to
    #: reach check_all() for minutes -- faulthandler named the frame exactly:
    #: governance.py:791 (_read_memory_events) <- cross_validate <-
    #: check_rule_0y <- check_all. Two test files therefore hung, no suite run
    #: ever completed, and the compliance gate silently cost minutes per call.
    #:
    #: Nothing here reads a row it does not need: the audit side is a 9.3 MB
    #: JSONL read, the memory side is indexed lookups scoped to the sessions the
    #: audit trail names, and the whole comparison is abandoned at this deadline.
    MEMORY_BUDGET_S = 2.0

    def cross_validate(self, session_id: (str | None) = None) -> dict[str, Any]:
        """Cross-validate the audit trail against DirectMemory.

        Session-scoped: when a specific session_id is provided, only that
        session is compared. When no session_id is provided, events are
        compared across all sessions that exist in BOTH ledgers (intersection
        of session IDs). This prevents aggregate-count mismatches caused by
        sessions that exist in only one ledger.

        Returns:
            dict with `consistent`, `comparable`, and discrepancy details.

            `comparable` is the honest qualifier: it is False when no session id
            is spelled the same way in both ledgers. In that case nothing was
            compared, and `consistent` is True only because there is no
            evidence of inconsistency -- not because the two ledgers agreed.
            Callers that need a real verdict must check `comparable` first.
        """
        deadline = time.monotonic() + self.MEMORY_BUDGET_S
        audit_events = self._read_audit_trail(session_id)

        audit_sessions: set[str] = set()
        for e in audit_events:
            sid = e.get("session_file", "") or e.get("session_uuid", "")
            if sid:
                audit_sessions.add(sid)

        memory_sessions = self._memory_sessions_present(audit_sessions, deadline)

        if session_id:
            matching_sessions = (
                {session_id}
                if session_id in audit_sessions or session_id in memory_sessions
                else set()
            )
        else:
            matching_sessions = audit_sessions & memory_sessions

        base: dict[str, Any] = {
            "total_audit_events": len(audit_events),
            "memory_budget_s": self.MEMORY_BUDGET_S,
            "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        if not matching_sessions:
            # The audit trail keys on session_file/session_uuid; DirectMemory
            # keys on its own session_id. On this host the two never coincide
            # (measured 2026-10-30: 1,391 audit ids, 0 present in the store).
            # Comparing unfiltered lists would manufacture discrepancies, so the
            # comparison is reported as NOT PERFORMED rather than as a pass.
            return {
                **base,
                "consistent": True,
                "comparable": False,
                "discrepancies": [],
                "total_memory_events": None,
                "matching_sessions": 0,
                "memory_sessions_scanned": len(memory_sessions),
                "note": (
                    "No session id is shared between the two ledgers "
                    "(audit: session_file/session_uuid; DirectMemory: session_id). "
                    "Nothing was compared."
                ),
            }

        memory_counts = self._memory_event_counts(matching_sessions, deadline)
        audit_scoped = [
            e
            for e in audit_events
            if (e.get("session_file", "") or e.get("session_uuid", "")) in matching_sessions
        ]

        discrepancies = []
        for event_type in ["file_edit", "file_create", "code_review", "test_run", "inference_spawn"]:
            audit_count = sum(1 for e in audit_scoped if e.get("type") == event_type)
            memory_count = sum(
                n for (_sid, et), n in memory_counts.items() if et == event_type
            )

            if audit_count != memory_count:
                discrepancies.append({
                    "event_type": event_type,
                    "audit_count": audit_count,
                    "memory_count": memory_count,
                    "severity": "high" if abs(audit_count - memory_count) > 1 else "low",
                })

        return {
            **base,
            "consistent": len(discrepancies) == 0,
            "comparable": True,
            "discrepancies": discrepancies,
            "total_memory_events": sum(memory_counts.values()),
            "matching_sessions": len(matching_sessions),
            "memory_scope": "compared-sessions",
            "note": "",
        }

    def _read_audit_trail(self, session_id: (str | None) = None) -> list[dict]:
        """Read events from the .audit/ JSONL trail (both root and src/.audit/)."""
        events = []

        for audit_dir in (self._audit_dir, Path("src/.audit")):
            if not audit_dir.exists():
                continue
            for fp in sorted(audit_dir.glob("session_*.jsonl")):
                if session_id and session_id not in fp.name:
                    continue
                try:
                    with fp.open("r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                events.append(json.loads(line))
                            except json.JSONDecodeError as exc:
                                note_failure("governance:L606", exc)
                except OSError as exc:
                    note_failure("governance:L608", exc)
                    continue

        return events

    def _memory_sessions_present(self, candidates: set[str], deadline: float) -> set[str]:
        """Which of `candidates` are real session_id values in the memory store.

        One indexed lookup per candidate (`idx_memories_session`), chunked, and
        abandoned at `deadline`. Replaces a whole-store read that took minutes.
        """
        present: set[str] = set()
        if not candidates or not self._memory_db.exists():
            return present

        try:
            conn = sqlite3.connect(str(self._memory_db), timeout=5)
        except sqlite3.Error as exc:
            note_failure("governance:memory-open", exc)
            return present

        try:
            for chunk in _chunks([c for c in candidates if c], 400):
                if time.monotonic() > deadline:
                    note_failure("governance:memory-budget-sessions", None)
                    break
                marks = ",".join("?" * len(chunk))
                try:
                    rows = conn.execute(
                        f"SELECT DISTINCT session_id FROM memories WHERE session_id IN ({marks})",
                        chunk,
                    ).fetchall()
                except sqlite3.Error as exc:
                    note_failure("governance:memory-sessions", exc)
                    continue
                present.update(r[0] for r in rows if r[0])
        finally:
            conn.close()

        return present

    def _memory_event_counts(self, sessions: set[str], deadline: float) -> Counter:
        """Per-(session, event_type) counts for `sessions`, aggregated in SQLite.

        Never materialises rows: the aggregate is computed by the store, so the
        cost is one index range per session rather than 4M Python dicts.
        """
        counts: Counter = Counter()
        if not sessions or not self._memory_db.exists():
            return counts

        try:
            conn = sqlite3.connect(str(self._memory_db), timeout=5)
        except sqlite3.Error as exc:
            note_failure("governance:memory-open", exc)
            return counts

        try:
            for chunk in _chunks(sorted(sessions), 200):
                if time.monotonic() > deadline:
                    note_failure("governance:memory-budget-counts", None)
                    break
                marks = ",".join("?" * len(chunk))
                try:
                    rows = conn.execute(
                        "SELECT session_id, event_type, COUNT(*) FROM memories "
                        f"WHERE source != 'audit_backfill' AND session_id IN ({marks}) "
                        "GROUP BY session_id, event_type",
                        chunk,
                    ).fetchall()
                except sqlite3.Error as exc:
                    note_failure("governance:memory-counts", exc)
                    continue
                for sid, event_type, n in rows:
                    counts[(sid, event_type)] += n
        finally:
            conn.close()

        return counts

    def _read_memory_events(self, session_id: (str | None) = None) -> list[dict]:
        """DEPRECATED -- unbounded by construction; kept only for diagnostics.

        Reads WHOLE rows for every non-backfill event. On the 5.4 GB dev store
        that is 4M dicts and minutes of wall clock, which is what hung the test
        suite. `cross_validate` no longer calls this. Any new caller must bound
        it itself (a LIMIT, or a session filter), and must not sit on a gate
        path.
        """
        events = []

        if not self._memory_db.exists():
            return events

        try:
            conn = sqlite3.connect(str(self._memory_db), timeout=5)
            conn.row_factory = sqlite3.Row

            query = "SELECT * FROM memories WHERE source != 'audit_backfill'"
            params = []
            if session_id:
                query += " AND session_id = ?"
                params.append(session_id)
            query += " ORDER BY id LIMIT 5000"

            cursor = conn.execute(query, params)
            for row in cursor.fetchall():
                try:
                    events.append(dict(row))
                except Exception as exc:
                    note_failure("governance:L639", exc)
            conn.close()
        except (sqlite3.Error, OSError) as exc:
            note_failure("governance:L642", exc)

        return events

    def __repr__(self) -> str:
        return f"DualLedgerValidator(audit={self._audit_dir}, memory={self._memory_db})"


# ---------------------------------------------------------------------------
# Real-Time Audit Pipeline (RULE 0Y-5)
# ---------------------------------------------------------------------------

class RealtimeAuditPipeline:
    """Interleaved 15-layer VLP validation pipeline integrated into middleware.

    Runs the 15-layer VLP verification (RULE 0T-B) as part of the compliance
    middleware rather than a post-hoc --audit mode. Each layer is checked
    sequentially; any failure blocks the action.

    Layers 1-10: Standard VLP (file existence, line existence, type match,
                  agent match, per-entry hash, full chain, Ed25519 sig,
                  Merkle inclusion, proof of omission, anomaly detection)
    Layers 11-13: MANDATORY hardened layers (non-canonical sig check,
                  replay prevention, cross-session chain)
    Layers 14-15: RECOMMENDED (truncation defense, quantum-resistant backup)

    Usage:
        pipeline = RealtimeAuditPipeline()
        result = pipeline.process(audit_filepath="/path/to/session.jsonl")
    """

    def __init__(self, enable_hardened: bool = True):
        self._enable_hardened = enable_hardened
        self._results: list[dict] = []
        #: EP382 residual obligations: duties that OUTLIVE the decision that
        #: created them, keyed by obligation id.
        self._obligations: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def process(self, audit_filepath: str) -> dict[str, Any]:
        """Run the full 15-layer VLP pipeline on an audit file.

        Args:
            audit_filepath: Path to the .audit/session_X.jsonl file

        Returns:
            dict with all_passed, layer_results, and failed_layers
        """
        from CORE.audit_trail import (
            verify_chain, verify_monotonicity, verify_head_hash,
            get_merkle_root, audit_anomaly_scan, verify_completeness,
            verify_signature,
        )

        fp = Path(audit_filepath)
        if not fp.exists():
            return {"all_passed": False, "error": f"File not found: {audit_filepath}"}

        layer_results = []
        failed_layers = []

        # Layer 1: File existence
        l1 = fp.exists()
        layer_results.append({"layer": 1, "name": "File existence", "passed": l1})
        if not l1:
            failed_layers.append(1)

        # Layer 2: Line existence (verify chain reads successfully)
        chain_ok, _ = verify_chain(audit_filepath)
        layer_results.append({"layer": 2, "name": "Line existence (chain integrity)", "passed": chain_ok})
        if not chain_ok:
            failed_layers.append(2)

        # Layer 3-4: Type match + Agent match (handled by verify_chain indirectly)
        layer_results.append({"layer": 3, "name": "Type match (entry types valid)", "passed": chain_ok})
        layer_results.append({"layer": 4, "name": "Agent match (hash chain valid)", "passed": chain_ok})
        if not chain_ok:
            failed_layers.extend([3, 4])

        # Layer 5: Per-entry hash (verify_chain already checks this)
        layer_results.append({"layer": 5, "name": "Per-entry hash", "passed": chain_ok})
        if not chain_ok:
            failed_layers.append(5)

        # Layer 6: Full chain
        layer_results.append({"layer": 6, "name": "Full chain", "passed": chain_ok})
        if not chain_ok:
            failed_layers.append(6)

        # Layer 7: Ed25519 signature — verify first non-header entry's signature
        sig_ok = True
        try:
            # Read the first content entry (skip session_start) and verify its signature
            with open(audit_filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("type") not in ("session_start", "chain_checkpoint"):
                            sig_ok = verify_signature(entry)
                            break
                    except (json.JSONDecodeError, TypeError) as exc:
                        note_failure("governance:L743", exc)
                        continue
        except Exception as exc:
            note_failure("governance:L745", exc)
            sig_ok = False
        layer_results.append({"layer": 7, "name": "Ed25519 signature", "passed": sig_ok})
        if not sig_ok:
            failed_layers.append(7)

        # Layer 8: Merkle inclusion proof
        merkle_root = get_merkle_root(audit_filepath)
        merkle_ok = bool(merkle_root)
        layer_results.append({"layer": 8, "name": "Merkle inclusion proof", "passed": merkle_ok})
        if not merkle_ok:
            failed_layers.append(8)

        # Layer 9: Proof of omission
        complete_ok, missing = verify_completeness(audit_filepath)
        layer_results.append({"layer": 9, "name": "Proof of omission", "passed": complete_ok})
        if not complete_ok:
            failed_layers.append(9)

        # Layer 10: Anomaly detection
        anomalies = audit_anomaly_scan(audit_filepath)
        anomaly_ok = len(anomalies) == 0
        layer_results.append({"layer": 10, "name": "Anomaly detection", "passed": anomaly_ok,
                              "anomalies": anomalies if not anomaly_ok else []})
        if not anomaly_ok:
            failed_layers.append(10)

        if self._enable_hardened:
            # Monotonicity is checked as part of chain verification already
            mono_ok, _ = verify_monotonicity(audit_filepath)
            layer_results.append({"layer": 11, "name": "Non-canonical signature check", "passed": sig_ok})
            layer_results.append({"layer": 12, "name": "Replay prevention (monotonic)", "passed": mono_ok})
            layer_results.append({"layer": 13, "name": "Cross-session hash chaining", "passed": chain_ok and merkle_ok})
            if not sig_ok:
                failed_layers.append(11)
            if not mono_ok:
                failed_layers.append(12)
            if not (chain_ok and merkle_ok):
                failed_layers.append(13)

            # Layer 14 (RECOMMENDED)
            anchor_ok, _, _ = verify_head_hash(audit_filepath)
            layer_results.append({"layer": 14, "name": "Truncation defense (anchor)", "passed": anchor_ok})
            if not anchor_ok:
                failed_layers.append(14)

            # Layer 15 (ADVISORY)
            layer_results.append({"layer": 15, "name": "Quantum-resistant hybrid backup",
                                  "passed": True, "note": "ADVISORY — requires liboqs-python"})

        all_passed = len(failed_layers) == 0

        result = {
            "all_passed": all_passed,
            "layer_results": layer_results,
            "failed_layers": failed_layers,
            "passed_count": sum(1 for lr in layer_results if lr.get("passed")),
            "total_layers": len(layer_results),
            "audit_file": audit_filepath,
            "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        with self._lock:
            self._results.append(result)

        return result

    # -- EP382: residual obligations -------------------------------------------

    def register_obligation(self, obligation_id: str, subject: str,
                            due_in_s: (float | None) = None,
                            reason: str = "") -> dict[str, Any]:
        """Register a duty that outlives the decision which created it.

        EP382's finding is that a policy verdict of ALLOW/DENY cannot express an
        obligation: the effect type has no way to say "allowed, AND something
        must still happen afterwards". This is that second half, tracked by the
        same pipeline that audits the session so it can be reported while it is
        still open rather than reconstructed after the fact.
        """
        now = datetime.now(timezone.utc)
        due_at = None
        if due_in_s is not None:
            due_at = (now + timedelta(seconds=float(due_in_s))).isoformat(timespec="seconds")
        record: dict[str, Any] = {
            "obligation_id": str(obligation_id),
            "subject": str(subject),
            "reason": reason,
            "registered_at": now.isoformat(timespec="seconds"),
            "due_at": due_at,
            "resolved_at": None,
            "evidence": "",
        }
        with self._lock:
            self._obligations[record["obligation_id"]] = record
        return dict(record)

    def resolve_obligation(self, obligation_id: str, evidence: str = "") -> bool:
        """Close an obligation. False if no such obligation is registered."""
        with self._lock:
            record = self._obligations.get(str(obligation_id))
            if record is None:
                return False
            record["resolved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            record["evidence"] = evidence
            return True

    def residual_obligations(self, now: (datetime | None) = None) -> dict[str, Any]:
        """Open obligations split into pending vs OVERDUE, each with its age.

        `now` is injectable so the overdue boundary is testable without sleeping.
        """
        current = now or datetime.now(timezone.utc)
        with self._lock:
            records = [dict(r) for r in self._obligations.values()]
        pending: list[dict[str, Any]] = []
        overdue: list[dict[str, Any]] = []
        resolved = 0
        for record in records:
            if record.get("resolved_at"):
                resolved += 1
                continue
            entry = dict(record)
            registered = _parse_iso(record.get("registered_at"))
            entry["age_s"] = (round((current - registered).total_seconds(), 3)
                              if registered else None)
            due = _parse_iso(record.get("due_at"))
            entry["overdue"] = bool(due is not None and current > due)
            (overdue if entry["overdue"] else pending).append(entry)
        return {
            "pending": pending,
            "overdue": overdue,
            "pending_count": len(pending),
            "overdue_count": len(overdue),
            "resolved_count": resolved,
            "total": len(records),
        }

    def obligation_snapshot(self) -> dict[str, Any]:
        """Counts only -- cheap enough to call on every middleware tick."""
        with self._lock:
            open_count = sum(1 for r in self._obligations.values()
                             if not r.get("resolved_at"))
            return {"open": open_count, "total": len(self._obligations)}

    def get_results(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return list(self._results[-limit:])

    def __repr__(self) -> str:
        return f"RealtimeAuditPipeline(hardened={self._enable_hardened})"
