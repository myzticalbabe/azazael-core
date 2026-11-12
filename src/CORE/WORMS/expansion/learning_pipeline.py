# coding: utf-8
"""ETL/data pipeline worm -- DataPipelineWorm + dynamic shape registry (split from learning.py, 2026-09-07)."""

import json
import time
import random
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple
from collections import defaultdict

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm  # pyright: ignore[reportAssignmentType]
    _REGISTRY = True
except ImportError as exc:
    note_failure("learning_pipeline:L17", exc)
    _REGISTRY = False
def register_worm(**kw):
        return lambda cls: cls


# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("learning_pipeline:L27", exc)
    _FREETHREAD = False
FreeThreadPool = None
ThreadSafeDict = dict
ThreadSafeList = list
def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:  # noqa: BLE001 — 314t detection is best-effort
            note_failure("learning_pipeline:L36", exc)
            return False

from CORE.WORMS.expansion.learning_features import DATA_SHAPES

import sysconfig

@register_worm(category="learning", priority=5, cooldown_seconds=90.0,
    description="Data pipeline worm — ETL from LocalMemoryStore with caching, batching, and validation")
class DataPipelineWorm:
    """ETL data preprocessing pipeline connected to LocalMemoryStore.
    
    Production Features:
    - Extract: reads from LocalMemoryStore (FTS5 full-text search) or JSON files
    - Transform: filtering, aggregation, field extraction with caching
    - Load: writes to LocalMemoryStore, JSON, or in-memory DataFrames
    - Batch: configurable batch sizes with progress tracking
    - Cache: deduplication of recent extracts to avoid redundant I/O
    - Metrics: records pipeline execution metrics
    
    Usage:
        pipeline = DataPipelineWorm()
        
        # Extract from LocalMemoryStore
        data = pipeline.extract_from_memory(
            source="SystemZ", event_type="monitor", limit=500
        )
        
        # Transform
        transformed = pipeline.transform(data, [
            lambda rows: [r for r in rows if r.get("severity") == "error"],
            pipeline.extract_field("data"),
        ])
        
        # Load to target
        pipeline.load(transformed, "output/training_data.json")
        
        # Full pipeline in one call
        result = pipeline.run_pipeline(
            source="CLUSTERWORMS",
            limit=200,
            output="output/cluster_training_data.json",
        )
    """

    VERSION = "2.0.0"

    def __init__(self):
        self._cache: Dict[str, Any] = ThreadSafeDict() if ThreadSafeDict != dict else {}
        self._lock = threading.Lock()
        self._store: Any = None  # Lazy LocalMemoryStore (Any: tri-state None/store/False; narrows at use)
        self._execution_count = 0
        self._operations: List[Dict] = []

    def _get_store(self):
        """Lazy LocalMemoryStore connection — double-checked locking for 3.14t."""
        if self._store is None:
            with self._lock:
                if self._store is None:
                    try:
                        from CORE.local_memory import get_memory_store
                        self._store = get_memory_store()
                    except ImportError as exc:
                        note_failure("learning_pipeline:L96", exc)
                        self._store = False
        return self._store if self._store else None

    # -- Extract from LocalMemoryStore ---------------------------------

    def extract_from_memory(self, source: Optional[str] = None, event_type: Optional[str] = None,
                            severity: Optional[str] = None, limit: int = 200,
                            since: Optional[str] = None, max_age_days: Optional[int] = None,
                            session_id: Optional[str] = None,
                            exclude_sources: Optional[List[str]] = None) -> List[Dict]:
        """Extract data from LocalMemoryStore with filters.
        
        This is the primary data source for the ML training pipeline.
        11,633 memories are available for training (as of June 1, 2026).

        Args:
            exclude_sources: Skip events whose ``source`` is in this list.
                RLTrainLoop/ScheduledTrainingWorm pass the monitor sources
                (see EXCLUDED_TRAINING_SOURCES) so training never learns
                from the telemetry that observes training.
        """
        store = self._get_store()
        if not store:
            self._record_fallback("extract", {"error": "LocalMemoryStore unavailable"})
            return []

        cache_key = (f"memory:{source}:{event_type}:{severity}:{limit}:{since}:"
                     f"{','.join(sorted(exclude_sources or []))}")
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        results = store.query_memories(
            source=source, event_type=event_type,
            severity=severity, limit=limit,
            since=since, max_age_days=max_age_days,
            session_id=session_id,
            exclude_sources=exclude_sources,
        )

        with self._lock:
            self._cache[cache_key] = results
        return results
    def search_memory(self, query: str, source: Optional[str] = None,
                      limit: int = 50) -> List[Dict]:
        """Full-text search across all memories (uses FTS5)."""
        store = self._get_store()
        if not store:
            return []
        return store.search_memories(query, source=source, limit=limit)
    def extract_recent(self, limit: int = 50, source: Optional[str] = None) -> List[Dict]:
        """Get most recent memories, optionally by source."""
        store = self._get_store()
        if not store:
            return []
        return store.recall_recent(limit=limit, source=source)
    def get_memory_stats(self) -> Dict:
        """Get statistics about available training data."""
        store = self._get_store()
        if not store:
            return {"available": False, "error": "LocalMemoryStore unavailable"}
        return store.get_stats()

    # -- Extract from file --------------------------------------------

    def extract(self, source: str, fmt: str = "json") -> Any:
        """Extract data from a file source."""
        cache_key = f"extract:{source}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        path = Path(source)
        if not path.exists():
            return None
        if fmt == "json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = path.read_text(encoding="utf-8")
        self._cache[cache_key] = data
        return data

    # -- Transform -----------------------------------------------------

    def transform(self, data: Any, transformations: List[Callable]) -> Any:
        """Apply a chain of transformations with caching."""
        result = data
        for tf in transformations:
            try:
                result = tf(result)
            except Exception as e:  # noqa: BLE001 — record fallback then re-raise
                self._record_fallback("transform", {"error": str(e)})
                raise
        return result
    def extract_field(self, field_name: str) -> Callable:
        """Create a transformation that extracts a field from each record."""
        def _extract(records):
            results = []
            for r in records:
                if isinstance(r, dict) and field_name in r:
                    val = r[field_name]
                    if isinstance(val, str):
                        try:
                            val = json.loads(val)
                        except (json.JSONDecodeError, TypeError) as exc:
                            note_failure("learning_pipeline:L199", exc)
                    results.append(val)
            return results
        return _extract
    def filter_by_severity(self, severity: str) -> Callable:
        """Create a transformation that filters records by severity."""
        def _filter(records):
            return [r for r in records if isinstance(r, dict) and r.get("severity") == severity]
        return _filter
    def filter_by_source(self, source: str) -> Callable:
        """Create a transformation that filters records by source."""
        def _filter(records):
            return [r for r in records if isinstance(r, dict) and r.get("source") == source]
        return _filter
    def flatten_data(self, records: List[Dict]) -> List[Dict]:
        """Flatten nested 'data' field into top-level columns."""
        flattened = []
        for r in records:
            flat = {k: v for k, v in r.items() if k != "data"}
            data_field = r.get("data", {})
            if isinstance(data_field, dict):
                flat.update({f"data_{k}": v for k, v in data_field.items()})
            flattened.append(flat)
        return flattened
    def deduplicate(self, records: List[Dict], key: str = "id") -> List[Dict]:
        """Remove duplicate records by a key field."""
        seen = set()
        unique = []
        for r in records:
            k = r.get(key) if isinstance(r, dict) else r  # type: ignore[reportUnnecessaryIsInstance]
            if k not in seen:
                seen.add(k)
                unique.append(r)
        return unique

    # -- Load ----------------------------------------------------------

    def load(self, data: Any, target: str, fmt: str = "json"):
        """Load transformed data to file target."""
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        else:
            path.write_text(str(data), encoding="utf-8")
        self._record_fallback("load", {"target": target, "size": len(str(data))})
    def load_to_memory(self, source: str, event_type: str, data: Any,
                       severity: str = "info", tags: Optional[List[str]] = None):
        """Load data into LocalMemoryStore."""
        store = self._get_store()
        if store:
            store.record_event(
                source=source, event_type=event_type,
                data=data, severity=severity, tags=tags,
            )

    # -- Batch Processing ----------------------------------------------

    def batch(self, items: List[Any], batch_size: int = 100) -> List[List[Any]]:
        """Split items into batches."""
        return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

    def process_batches(self, items: List[Any], processor: Callable,
                        batch_size: int = 100) -> List[Any]:
        """Process items in batches with progress tracking."""
        batches = self.batch(items, batch_size)
        results = []
        for i, batch in enumerate(batches):
            try:
                result = processor(batch)
                results.append(result)
            except Exception as e:  # noqa: BLE001 — record failed batch, continue
                note_failure("learning_pipeline:L271", e)
                self._record_fallback("batch", {"batch": i, "error": str(e)})
        return results

    # -- Full Pipeline -------------------------------------------------

    def run_pipeline(self, source: Optional[str] = None, event_type: Optional[str] = None,
                     limit: int = 200, output: Optional[str] = None,
                     max_age_days: Optional[int] = None) -> Dict[str, Any]:
        """Run a complete ETL pipeline from LocalMemoryStore to output.
        
        Args:
            source: Filter by source component
            event_type: Filter by event type
            limit: Max records to extract
            output: Optional file path to save results
            max_age_days: Only include records newer than N days
        
        Returns:
            Dict with pipeline stats and extracted data preview
        """
        start = time.perf_counter()

        # Extract
        records = self.extract_from_memory(
            source=source, event_type=event_type,
            limit=limit, max_age_days=max_age_days,
        )

        # Transform — flatten nested data
        transformed = self.flatten_data(records)

        # Deduplicate
        unique = self.deduplicate(transformed, key="id")

        # Load if output path specified
        if output:
            self.load(unique, output)

        duration_ms = (time.perf_counter() - start) * 1000

        self._execution_count += 1

        return {
            "pipeline_version": self.VERSION,
            "execution": self._execution_count,
            "extracted": len(records),
            "transformed": len(transformed),
            "unique": len(unique),
            "duration_ms": round(duration_ms, 2),
            "filters": {"source": source, "event_type": event_type, "max_age_days": max_age_days},
            "preview": unique[:3] if unique else [],
            "output": output,
        }

    # -- Train/Validation/Test Split ----------------------------------

    def split_memories(self, source: Optional[str] = None, event_type: Optional[str] = None,
                        test_ratio: float = 0.2, val_ratio: float = 0.1,
                        by_session: bool = True,
                        max_age_days: Optional[int] = None) -> Dict[str, Any]:
        """Split memories into train/validation/test sets.

        When by_session=True, splits by session_id to prevent data leakage
        (all records from the same session stay in the same split).
        Uses temporal ordering: oldest sessions -> train, newest -> test.

        Args:
            source: Filter by source component.
            event_type: Filter by event type.
            test_ratio: Fraction of sessions for test set (0.0-1.0).
            val_ratio: Fraction of sessions for validation set (0.0-1.0).
            by_session: If True, split by session_id. If False, split by record.
            max_age_days: Only include records newer than N days.

        Returns:
            Dict with 'train', 'val', 'test' lists and 'stats'.
        """
        store = self._get_store()
        if not store:
            return {"train": [], "val": [], "test": [], "stats": {"error": "No store"}}

        # Get all matching records. fetch_all_memories, not
        # query_memories(limit=5000): the read API clamps to MAX_QUERY_LIMIT
        # (500), so a "get all records" call silently became "get the 500
        # newest" and the train/val/test split below was drawn from a
        # truncated, time-biased sample.
        records = store.fetch_all_memories(
            source=source, event_type=event_type,
            limit=5000, max_age_days=max_age_days,
        )

        if not records:
            return {"train": [], "val": [], "test": [], "stats": {"total": 0}}

        if by_session:
            # Group by session
            from collections import OrderedDict
            sessions: Dict[str, List] = OrderedDict()
            for r in records:
                sid = r.get("session_id", "unknown")
                if sid not in sessions:
                    sessions[sid] = []
                sessions[sid].append(r)

            # Sort sessions by timestamp for temporal split
            def _session_start_time(sid_records):
                timestamps = [r.get("timestamp", "") for r in sid_records[1]]
                valid = [t for t in timestamps if t]
                return valid[0] if valid else ""

            sorted_sessions = sorted(
                sessions.items(),
                key=_session_start_time,
            )

            total = len(sorted_sessions)
            n_test = max(1, int(total * test_ratio))
            n_val = max(1, int(total * val_ratio))
            n_train = total - n_test - n_val

            # Newest sessions -> test, middle -> val, oldest -> train
            train_items = sorted_sessions[:n_train]
            val_items = sorted_sessions[n_train:n_train + n_val]
            test_items = sorted_sessions[n_train + n_val:]

            train = [r for _, rr in train_items for r in rr]
            val = [r for _, rr in val_items for r in rr]
            test = [r for _, rr in test_items for r in rr]

            stats = {
                "total_sessions": total,
                "train_sessions": len(train_items),
                "val_sessions": len(val_items),
                "test_sessions": len(test_items),
                "total_records": len(records),
                "train_records": len(train),
                "val_records": len(val),
                "test_records": len(test),
                "split_method": "by_session_temporal",
            }
        else:
            # Simple record-level split
            import random
            shuffled = list(records)
            random.shuffle(shuffled)
            n = len(shuffled)
            n_test = max(1, int(n * test_ratio))
            n_val = max(1, int(n * val_ratio))

            test = shuffled[:n_test]
            val = shuffled[n_test:n_test + n_val]
            train = shuffled[n_test + n_val:]

            stats = {
                "total_records": n,
                "train_records": len(train),
                "val_records": len(val),
                "test_records": len(test),
                "split_method": "random_record",
            }

        return {
            "train": train,
            "val": val,
            "test": test,
            "stats": stats,
        }

    # -- Temporal Cross-Validation --------------------------------------

    def temporal_cross_validation(self, source: Optional[str] = None, event_type: Optional[str] = None,
                                   n_folds: int = 5, gap_days: int = 0,
                                   max_age_days: Optional[int] = None) -> Dict[str, Any]:
        """Temporal cross-validation with sliding window splits.

        Creates n_folds train/test splits where each fold's test set is
        temporally after its training set (no future leakage).
        An optional gap_days between train and test prevents seasonality bleed.

        Args:
            source: Filter by source component.
            event_type: Filter by event type.
            n_folds: Number of CV folds (default 5).
            gap_days: Days to skip between train and test (default 0).
            max_age_days: Only include records newer than N days.

        Returns:
            Dict with 'folds' list and 'stats' summary.
            Each fold: {'fold': N, 'train': [...], 'test': [...], 'stats': {...}}
        """
        store = self._get_store()
        if not store:
            return {"folds": [], "stats": {"error": "No store"}}

        records = store.fetch_all_memories(
            source=source, event_type=event_type,
            limit=5000, max_age_days=max_age_days,
        )

        if not records:
            return {"folds": [], "stats": {"total": 0, "gap_days": gap_days}}

        # Sort records by timestamp
        sorted_records = sorted(records, key=lambda r: r.get("timestamp", ""))
        n = len(sorted_records)

        # Compute fold boundaries (expanding window)
        fold_size = n // n_folds
        folds = []

        for fold_idx in range(1, n_folds + 1):
            # Expanding window: train = first fold_idx * fold_size records
            # Test = next (fold_size + remainder) records after gap
            train_end = fold_idx * fold_size

            # Apply gap between train and test
            if gap_days > 0 and train_end < n:
                gap_end = train_end
                gap_timestamps = [r.get("timestamp", "") for r in sorted_records[train_end:]]
                ref_ts = gap_timestamps[0] if gap_timestamps else ""
                if ref_ts:
                    try:
                        ref_dt = datetime.fromisoformat(ref_ts.replace("Z", "+00:00")[:19])
                        gap_end_dt = ref_dt + timedelta(days=gap_days)
                        # Count records within gap
                        for i, r in enumerate(sorted_records[train_end:], train_end):
                            ts = r.get("timestamp", "")
                            if ts:
                                try:
                                    r_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")[:19])
                                    if r_dt >= gap_end_dt:
                                        gap_end = i
                                        break
                                except Exception as exc:  # noqa: BLE001 — timestamp parse best-effort
                                    note_failure("learning_pipeline:L505", exc)
                        train_end = gap_end
                    except Exception as exc:  # noqa: BLE001 — gap-detection best-effort
                        note_failure("learning_pipeline:L508", exc)

            test_start = train_end
            test_end = min(test_start + fold_size + (n % n_folds), n)

            train = sorted_records[:train_end]
            test = sorted_records[test_start:test_end]

            if not test:
                continue

            folds.append({
                "fold": fold_idx,
                "train": train,
                "test": test,
                "stats": {
                    "train_records": len(train),
                    "test_records": len(test),
                    "train_ratio": round(len(train) / n, 3) if n else 0,
                    "test_ratio": round(len(test) / n, 3) if n else 0,
                },
            })

        return {
            "folds": folds,
            "stats": {
                "n_folds": len(folds),
                "total_records": n,
                "method": "temporal_expanding_window",
                "gap_days": gap_days,
            },
        }

    # -- Scheduled Dynamic Refresh -------------------------------------

    def schedule_dynamic_refresh(self, interval_hours: int = 24,
                                  sample_limit: int = 100,
                                  auto_merge: bool = True) -> Dict[str, Any]:
        """Schedule periodic DYNAMIC_SHAPES refresh with logging.

        Runs refresh_dynamic_shapes() now and records the operation.
        In a production daemon, call this periodically.

        Args:
            interval_hours: Recommended refresh interval.
            sample_limit: Max records to sample per event_type.
            auto_merge: If True, call refresh_dynamic_shapes() immediately.

        Returns:
            Dict with refresh results and schedule info.
        """
        from CORE.WORMS.expansion.learning import (
            refresh_dynamic_shapes, DYNAMIC_SHAPES, DATA_SHAPES
        )

        refresh_result = {}
        if auto_merge:
            refresh_result = refresh_dynamic_shapes(
                pipeline=self, sample_limit=sample_limit
            )

        # Record the refresh event
        store = self._get_store()
        if store:
            try:
                store.record_event(
                    source="DataPipelineWorm",
                    event_type="dynamic_shapes_refresh",
                    data={
                        "interval_hours": interval_hours,
                        "sample_limit": sample_limit,
                        "auto_merge": auto_merge,
                        "dynamic_count": len(DYNAMIC_SHAPES),
                        "static_count": len(DATA_SHAPES),
                        "refresh_added": refresh_result.get("added", 0),
                    },
                    severity="info",
                    tags=["shapes", "dynamic", "refresh"],
                )
            except Exception as exc:  # noqa: BLE001 — shape-refresh telemetry best-effort
                note_failure("learning_pipeline:L588", exc)

        return {
            "refresh": refresh_result,
            "schedule": {
                "interval_hours": interval_hours,
                "dynamic_shapes_count": len(DYNAMIC_SHAPES),
                "static_shapes_count": len(DATA_SHAPES),
            },
        }

    # -- Auto-Discover DATA_SHAPES --------------------------------------

    def auto_discover_shapes(self, sample_limit: int = 100) -> Dict[str, Any]:
        """Auto-discover DATA_SHAPES entries from LocalMemoryStore.

        Queries all event_types in the store, samples their data shapes,
        and generates DATA_SHAPES-compatible entries by inferring field types.
        Returns a dict that can be merged into or printed as DATA_SHAPES.

        Args:
            sample_limit: Max records to sample per event_type.

        Returns:
            Dict with discovered shapes keyed by event_type.
        """
        store = self._get_store()
        if not store:
            return {"error": "LocalMemoryStore unavailable"}

        stats = store.get_stats()
        event_types = stats.get("event_types", {})

        discovered = {}
        for et in sorted(event_types.keys()):
            records = store.query_memories(event_type=et, limit=sample_limit)
            if not records:
                continue

            # Collect all field names and infer their types
            all_fields = {}  # field_name -> {(type, sample_value)}
            for r in records:
                data = r.get("data", {})
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except (json.JSONDecodeError, TypeError) as exc:
                        note_failure("learning_pipeline:L635", exc)
                        data = {}
                if not isinstance(data, dict):
                    continue

                for k, v in data.items():
                    if k not in all_fields:
                        if isinstance(v, str):
                            all_fields[k] = "categorical"
                        elif isinstance(v, (int, float, bool)):
                            all_fields[k] = "numeric"
                        elif isinstance(v, list):
                            all_fields[k] = "list_categorical" if v and isinstance(v[0], str) else "list"
                        elif isinstance(v, dict):
                            all_fields[k] = "dict"
                        else:
                            all_fields[k] = str(type(v).__name__)

            if not all_fields:
                continue

            numeric_keys = [k for k, t in all_fields.items() if t == "numeric"]
            fields = {}
            for k, t in all_fields.items():
                if t in ("numeric", "categorical", "list_categorical", "list", "dict"):
                    fields[k] = t

            discovered[et] = {
                "type": "auto_discovered",
                "fields": fields,
                "numeric_keys": numeric_keys,
                "_sample_count": len(records),
            }

        return {
            "discovered": discovered,
            "stats": {
                "total_event_types": len(event_types),
                "discovered": len(discovered),
                "sample_limit": sample_limit,
            },
        }

    def ablate_sources(self, sources: list | None = None,
                       exclude_variants: list | None = None) -> Dict[str, Any]:
        """Ablation harness: measured composition over source variants (EP318).

        Runs extract_from_memory once per exclude-set variant and reports
        per-variant record counts. The DOWNSTREAM metric half (which variant
        trains better) stays deferred: training comparison needs the
        orchestrator loop, not the pipeline — the harness measures the
        composition axis the metric would read.
        """
        variants = list(exclude_variants or [[]])
        rows: Dict[str, Any] = {}
        for ex in variants:
            try:
                recs = self.extract_from_memory(
                    source=sources if isinstance(sources, str) else None,
                    limit=200,
                    exclude_sources=list(ex or []))
                rows["excluded:" + ",".join(ex or ["none"])] = len(recs or [])
            except Exception as exc:
                note_failure("expansion/learning_pipeline:713", exc)
                rows["excluded:" + ",".join(ex or ["none"])] = (
                    f"error:{type(exc).__name__}")
        return {"variants": rows, "n_variants": len(rows),
                "composition_axis": "exclude_sources parameterized"}

    def execute(self) -> Dict[str, Any]:
        """Standard worm execute() — runs pipeline with default params."""
        return self.run_pipeline(limit=100, max_age_days=7)

    def get_summary(self) -> Dict[str, Any]:
        """Worm contract: return a summary dict (see BaseWormMixin).

        `DataPipelineWorm` was split out of the `learning.py` monolith without
        a `get_summary()`, so the smoke test that instantiates every registered
        worm saw `None` here. `BaseWormMixin.__init_subclass__` would otherwise
        have picked the no-argument `execute()` and run a live 100-row pipeline
        just to produce a status dict; reporting instance state is cheaper and
        side-effect free.
        """
        with self._lock:
            execution_count = self._execution_count
            cached = len(self._cache)
        return {
            "success": True,
            "method": "get_summary",
            "result": {
                "version": self.VERSION,
                "execution_count": execution_count,
                "cached_extracts": cached,
                "operations": len(self._operations),
            },
        }

    # -- Internal ------------------------------------------------------

    def _record_fallback(self, operation: str, data: Dict):
        """Record operation metadata to instance state."""
        self._operations.append({
            "operation": operation,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        })

# -- Dynamic Shape Registry ----------------------------------------------
# Auto-populated from LocalMemoryStore at runtime, merged with static DATA_SHAPES

DYNAMIC_SHAPES: Dict[str, Dict] = {}


def refresh_dynamic_shapes(pipeline=None, sample_limit: int = 100) -> Dict[str, Any]:
    """Refresh the DYNAMIC_SHAPES registry from LocalMemoryStore.

    Runs auto_discover_shapes() on a DataPipelineWorm instance and
    merges the discovered entries into the module-level DYNAMIC_SHAPES dict.
    Only adds event_types that are not already in the static DATA_SHAPES.

    Args:
        pipeline: Optional DataPipelineWorm instance (creates one if None).
        sample_limit: Max records to sample per event_type.

    Returns:
        Dict with "added" (count of new shapes), "total" (DYNAMIC_SHAPES size).
    """
    global DYNAMIC_SHAPES
    if pipeline is None:
        try:
            pipeline = DataPipelineWorm()
        except Exception as exc:  # noqa: BLE001 — pipeline creation failure is a signed error
            note_failure("learning_pipeline:L740", exc)
            return {"error": "Cannot create DataPipelineWorm"}

    result = pipeline.auto_discover_shapes(sample_limit=sample_limit)
    discovered = result.get("discovered", {})

    added = 0
    for et, shape in discovered.items():
        if et not in DATA_SHAPES and et not in DYNAMIC_SHAPES:
            # Remove internal metadata keys before storing
            clean_shape = {k: v for k, v in shape.items() if not k.startswith("_")}
            DYNAMIC_SHAPES[et] = clean_shape
            added += 1

    return {
        "added": added,
        "total": len(DYNAMIC_SHAPES),
        "discovered_total": len(discovered),
        "static_total": len(DATA_SHAPES),
    }
