# coding: utf-8
"""
dead_letter_queue.py — Dead Letter Queue for Failed Worm Executions
====================================================================
Created from analysis by 5 inference APIs (Groq, OpenRouter, NVIDIA NIM,
Mistral, GitHub). All 5 independently flagged the need for a dead letter
queue to capture, persist, retry, and analyze failed worm executions.

Features:
- Persistent storage (JSON on disk) for failed worm executions
- Configurable retry with exponential backoff
- Integration with circuit breaker system
- Thread-safe enqueue/dequeue/replay for Python 3.14t
- Analytics: most-common failures, retry success rates, failure patterns

Usage:
    from CORE.WORMS.dead_letter_queue import DeadLetterQueue

    dlq = DeadLetterQueue()
    dlq.enqueue("SYSTEMZ", {"error": "Timeout"}, config={})
    dlq.replay_all()  # Retry all dead letters
    dlq.analytics()   # Get failure patterns
"""

import dataclasses
import json
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Data Types ------------------------------------------------------

@dataclass
class DeadLetter:
    """A single failed worm execution stored in the dead letter queue."""

    worm_name: str
    error_message: str
    error_type: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    cluster_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    retry_count: int = 0
    max_retries: int = 5
    last_retry: str = ""
    resolved: bool = False
    resolution: str = ""  # "retry_success", "permanent_failure", "circuit_open", "discarded"
    resolution_time: str = ""

    def can_retry(self) -> bool:
        """Check if this dead letter is eligible for another retry."""
        return not self.resolved and self.retry_count < self.max_retries

    def backoff_delay(self) -> float:
        """Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s... capped at 120s."""
        return min(2 ** self.retry_count, 120.0)

    def mark_resolved(self, resolution: str):
        """Mark this dead letter as resolved with a resolution reason."""
        self.resolved = True
        self.resolution = resolution
        self.resolution_time = datetime.now(timezone.utc).isoformat()


# -- Dead Letter Queue ----------------------------------------------

class DeadLetterQueue:
    """Thread-safe dead letter queue for failed worm executions.

    Persists to disk as JSON for cross-session durability.
    Integrates with circuit breaker for intelligent retry decisions.
    Thread-safe for Python 3.14t free-threading (no GIL).

    Usage:
        dlq = DeadLetterQueue()
        dlq.enqueue("SYSTEMZ", {"error": "timeout"}, config={})
        results = dlq.replay_all(execute_fn)
        stats = dlq.analytics()
    """

    VERSION = "1.0.0"
    DEFAULT_STORAGE = Path.home() / ".azazael" / "dead_letters.json"
    MAX_QUEUE_SIZE = 1000

    def __init__(self, storage_path: (Path | None) = None):
        self._storage_path = storage_path or self.DEFAULT_STORAGE
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._letters: list[DeadLetter] = []
        self._total_enqueued: int = 0
        self._total_resolved: int = 0
        self._total_permanent: int = 0
        self._load()

    # -- Persistence ------------------------------------------------

    def _load(self):
        """Load dead letters from disk."""
        if not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = []
            known_fields = {f.name for f in dataclasses.fields(DeadLetter)}
            for item in data.get("letters", []):
                try:
                    filtered = {k: v for k, v in item.items() if k in known_fields}
                    dl = DeadLetter(**filtered)
                    loaded.append(dl)
                except (TypeError, ValueError) as exc:
                    note_failure("dead_letter_queue:L114", exc) # Skip malformed entries
            with self._lock:
                self._letters = loaded
                self._total_enqueued = data.get("total_enqueued", len(loaded))
                self._total_resolved = data.get("total_resolved", 0)
                self._total_permanent = data.get("total_permanent", 0)
        except (json.JSONDecodeError, OSError) as exc:
            note_failure("dead_letter_queue:L121", exc) # Corrupt file — start fresh

    def _save(self):
        """Persist dead letters to disk atomically."""
        with self._lock:
            data = {
                "version": self.VERSION,
                "updated": datetime.now(timezone.utc).isoformat(),
                "total_enqueued": self._total_enqueued,
                "total_resolved": self._total_resolved,
                "total_permanent": self._total_permanent,
                "active_count": len([l for l in self._letters if not l.resolved]),
                "letters": [asdict(l) for l in self._letters],
            }
        try:
            tmp = self._storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            tmp.replace(self._storage_path)
        except OSError as exc:
            note_failure("dead_letter_queue:L140", exc)

    # -- Core Operations --------------------------------------------

    def enqueue(self, worm_name: str, error: dict[str, Any],
                config: (dict[str, Any] | None) = None,
                cluster_id: str = "",
                max_retries: int = 5) -> DeadLetter:
        """Add a failed worm execution to the dead letter queue.

        Args:
            worm_name: Name of the worm that failed (e.g., 'SYSTEMZ')
            error: Error dict from worm execution result
            config: Worm configuration at time of failure
            cluster_id: Cluster ID if part of a cluster execution
            max_retries: Maximum retry attempts for this letter

        Returns:
            The created DeadLetter entry
        """
        error_msg = error.get("error", str(error))
        error_type = error.get("type", type(error_msg).__name__)

        letter = DeadLetter(
            worm_name=worm_name,
            error_message=str(error_msg)[:500],
            error_type=str(error_type),
            config=config or {},
            cluster_id=cluster_id,
            max_retries=max_retries,
        )

        with self._lock:
            self._letters.append(letter)
            self._total_enqueued += 1

            # Prune if over max
            if len(self._letters) > self.MAX_QUEUE_SIZE:
                # Remove oldest resolved letters first
                resolved = [l for l in self._letters if l.resolved]
                for old in resolved[: len(self._letters) - self.MAX_QUEUE_SIZE]:
                    self._letters.remove(old)

        self._save()
        return letter

    def enqueue_result(self, worm_name: str, result: dict[str, Any],
                       config: (dict[str, Any] | None) = None,
                       cluster_id: str = "") -> (DeadLetter | None):
        """Enqueue a failed worm execution result directly.

        Only enqueues if the result indicates failure (success=False).

        Args:
            worm_name: Worm name
            result: Execution result dict with 'success', 'error' keys
            config: Worm configuration
            cluster_id: Cluster ID

        Returns:
            DeadLetter if enqueued, None if result was successful
        """
        if result.get("success", False):
            return None
        return self.enqueue(worm_name, result, config=config, cluster_id=cluster_id)

    def replay(self, letter: DeadLetter,
               execute_fn: Callable[[str, dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """Attempt to replay a single dead letter.

        Args:
            letter: The DeadLetter to retry
            execute_fn: Function(worm_name, config) -> result_dict

        Returns:
            Execution result dict
        """
        if not letter.can_retry():
            return {"success": False, "error": "Max retries exhausted",
                    "retry_count": letter.retry_count}

        delay = letter.backoff_delay()
        time.sleep(delay)

        try:
            result = execute_fn(letter.worm_name, letter.config)
        except Exception as e:
            note_failure("dead_letter_queue:L227", e)
            result = {"success": False, "error": str(e)}

        with self._lock:
            letter.retry_count += 1
            letter.last_retry = datetime.now(timezone.utc).isoformat()

        if result.get("success", False):
            letter.mark_resolved("retry_success")
            with self._lock:
                self._total_resolved += 1
        elif not letter.can_retry():
            letter.mark_resolved("permanent_failure")
            with self._lock:
                self._total_permanent += 1

        self._save()
        return result

    def replay_all(self, execute_fn: Callable[[str, dict[str, Any]], dict[str, Any]],
                   max_concurrent: int = 4) -> list[dict[str, Any]]:
        """Replay all pending dead letters.

        Args:
            execute_fn: Function(worm_name, config) -> result_dict
            max_concurrent: Max concurrent replays (threads)

        Returns:
            list of replay results
        """
        pending = self.get_pending()
        if not pending:
            return []

        results: list[dict[str, Any]] = []
        semaphore = threading.Semaphore(max_concurrent)

        def _replay_one(letter: DeadLetter):
            semaphore.acquire()
            try:
                result = self.replay(letter, execute_fn)
                results.append({
                    "worm": letter.worm_name,
                    "success": result.get("success", False),
                    "error": result.get("error", ""),
                    "retry_count": letter.retry_count,
                    "resolution": letter.resolution,
                })
            finally:
                semaphore.release()

        threads = []
        for letter in pending:
            t = threading.Thread(target=_replay_one, args=(letter,),
                                name=f"dlq-replay-{letter.worm_name}")
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=30)

        return results

    def discard(self, letter: DeadLetter, reason: str = "discarded"):
        """Mark a dead letter as discarded (won't retry)."""
        letter.mark_resolved(reason)
        self._save()

    def discard_all(self, reason: str = "batch_discard"):
        """Discard all pending dead letters."""
        with self._lock:
            for letter in self._letters:
                if not letter.resolved:
                    letter.mark_resolved(reason)
        self._save()

    # -- Query ------------------------------------------------------

    def get_pending(self) -> list[DeadLetter]:
        """Get all dead letters eligible for retry."""
        with self._lock:
            return [l for l in self._letters if l.can_retry()]

    def get_by_worm(self, worm_name: str) -> list[DeadLetter]:
        """Get all dead letters for a specific worm."""
        with self._lock:
            return [l for l in self._letters if l.worm_name == worm_name]

    def get_all(self) -> list[DeadLetter]:
        """Get all dead letters (including resolved)."""
        with self._lock:
            return list(self._letters)

    def count_pending(self) -> int:
        """Count pending dead letters."""
        return len(self.get_pending())

    def count_total(self) -> int:
        """Total dead letters (including resolved)."""
        with self._lock:
            return len(self._letters)

    # -- Analytics --------------------------------------------------

    def analytics(self) -> dict[str, Any]:
        """Generate analytics on failure patterns.

        Returns:
            dict with failure counts by worm, error type, resolution stats,
            and retry success rates.
        """
        with self._lock:
            letters = list(self._letters)

        # Count by worm
        by_worm: dict[str, int] = {}
        # Count by error type
        by_error: dict[str, int] = {}
        # Resolution stats
        resolved_count = 0
        retry_success = 0
        permanent = 0

        for l in letters:
            by_worm[l.worm_name] = by_worm.get(l.worm_name, 0) + 1
            error_key = l.error_type or "unknown"
            by_error[error_key] = by_error.get(error_key, 0) + 1
            if l.resolved:
                resolved_count += 1
                if l.resolution == "retry_success":
                    retry_success += 1
                elif l.resolution == "permanent_failure":
                    permanent += 1

        # Most common failures
        top_worms = sorted(by_worm.items(), key=lambda x: x[1], reverse=True)[:5]
        top_errors = sorted(by_error.items(), key=lambda x: x[1], reverse=True)[:5]

        pending_count = len([l for l in letters if l.can_retry()])

        return {
            "total_letters": len(letters),
            "pending": pending_count,
            "resolved": resolved_count,
            "retry_success": retry_success,
            "permanent_failures": permanent,
            "retry_success_rate": (retry_success / max(resolved_count, 1)) * 100,
            "top_failing_worms": [{"worm": w, "count": c} for w, c in top_worms],
            "top_error_types": [{"type": e, "count": c} for e, c in top_errors],
            "total_enqueued": self._total_enqueued,
            "version": self.VERSION,
        }

    def summary(self) -> str:
        """Human-readable summary of the dead letter queue."""
        a = self.analytics()
        return (
            f"DLQ v{a['version']}: {a['pending']} pending, "
            f"{a['resolved']} resolved ({a['retry_success']} retried, "
            f"{a['permanent_failures']} permanent), "
            f"top worm: {a['top_failing_worms'][0]['worm'] if a['top_failing_worms'] else 'none'}"
        )

    # -- Cleanup ----------------------------------------------------

    def clear(self):
        """Clear all dead letters (use with caution)."""
        with self._lock:
            self._letters.clear()
        self._save()

    def clear_resolved(self):
        """Remove resolved dead letters, keeping only pending."""
        with self._lock:
            self._letters = [l for l in self._letters if not l.resolved]
        self._save()

    def __repr__(self) -> str:
        return f"DeadLetterQueue(v{self.VERSION}, {self.count_pending()}/{self.count_total()} pending)"

    def __len__(self) -> int:
        return self.count_total()


    def get_dead_letter_summary(self, lookback_seconds: float = 3600) -> dict[str, Any]:
        """Windowed failure summary. Absorbed from ClusterManager (U3).

        Same contract (total_failed, per_worm, recent_errors), flat dimension:
        breakdown is by `cluster_id` (registry category) instead of nature —
        the taxonomy is gone, the shape is not. Timestamps are ISO strings;
        unparseable stamps stay visible (counted, never silently dropped).
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        try:
            window = float(lookback_seconds)
        except (TypeError, ValueError) as exc:
            note_failure("dead_letter_queue:L423", exc)
            window = 3600.0
        summary: dict[str, Any] = {
            "total_failed": 0,
            "affected_categories": 0,
            "per_category": {},
            "per_worm": {},
            "recent_errors": [],
        }
        with self._lock:
            letters = list(self._letters)
        for letter in letters:
            try:
                stamp = datetime.fromisoformat(str(letter.timestamp))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                epoch = stamp.timestamp()
                age = (now - stamp).total_seconds()
            except (ValueError, TypeError) as exc:
                note_failure("dead_letter_queue:L441", exc)
                epoch = 0.0
                age = 0.0
            if age < 0 or age > window:
                continue
            summary["total_failed"] += 1
            cat = letter.cluster_id or "uncategorized"
            summary["per_category"][cat] = summary["per_category"].get(cat, 0) + 1
            summary["per_worm"][letter.worm_name] = (
                summary["per_worm"].get(letter.worm_name, 0) + 1)
            summary["recent_errors"].append({
                "category": cat,
                "worm": letter.worm_name,
                "error": str(letter.error_message)[:120],
                "method": "?",
                "resolved": letter.resolved,
                "timestamp": str(letter.timestamp),
                "timestamp_epoch": epoch,
            })
        summary["affected_categories"] = len(summary["per_category"])
        summary["recent_errors"].sort(key=lambda e: e["timestamp"], reverse=True)
        summary["recent_errors"] = summary["recent_errors"][:20]
        return summary

    def retry_all_failed_worms(self, max_retries: int = 2,
                               execute_fn=None,
                               category: str | None = None) -> dict[str, Any]:
        """Retry every retryable letter. Absorbed from ClusterManager (U3).

        Without `execute_fn` nothing can execute: reports eligibility per
        category instead of raising — a reporter must never become an
        executor to satisfy its contract. With one, delegates to replay_all.
        `category` scopes to one registry category (was per-nature retry).
        """
        results: dict[str, Any] = {
            "total_retried": 0,
            "total_succeeded": 0,
            "still_failed": 0,
            "per_category": {},
            "errors": [],
        }
        with self._lock:
            eligible = [l for l in self._letters
                        if l.can_retry() and l.retry_count < int(max_retries)
                        and (category is None or (l.cluster_id or "uncategorized") == category)]
        if execute_fn is None:
            for letter in eligible:
                cat = letter.cluster_id or "uncategorized"
                bucket = results["per_category"].setdefault(cat, {
                    "retried": 0, "succeeded": 0, "still_failed": 0})
                bucket["retried"] += 1
                results["total_retried"] += 1
            results["still_failed"] = results["total_retried"]
            return results
        if category is None:
            outcome = self.replay_all(execute_fn)
        else:
            outcome = []
            for letter in eligible:
                res = self.replay(letter, execute_fn)
                outcome.append({
                    "worm": letter.worm_name,
                    "success": res.get("success", False),
                    "error": res.get("error", ""),
                })
        for letter in eligible:
            cat = letter.cluster_id or "uncategorized"
            bucket = results["per_category"].setdefault(cat, {
                "retried": 0, "succeeded": 0, "still_failed": 0})
            bucket["retried"] += 1
            results["total_retried"] += 1
        results["total_succeeded"] = sum(
            1 for r in outcome if r.get("success"))
        results["still_failed"] = (results["total_retried"]
                                   - results["total_succeeded"])
        return results

    def get_summary(self):
        return {"status":"ok","worm":"DeadLetterQueue","method":"count_pending"}