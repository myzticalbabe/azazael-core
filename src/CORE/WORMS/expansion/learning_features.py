# coding: utf-8
"""Feature extraction worms -- SchemaAwareFeatureExtractor + FeatureEngineer + DATA_SHAPES (split from learning.py, 2026-09-07)."""

import json
import time
import random
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple
from collections import defaultdict
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("learning_features:L17", exc)
    _REGISTRY = False
def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("learning_features:L27", exc)
    _FREETHREAD = False
FreeThreadPool = None
ThreadSafeDict = dict
ThreadSafeList = list
def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:  # noqa: BLE001 — 314t detection is best-effort
            note_failure("learning_features:L36", exc)
            return False

# -- Schema-Aware Feature Extractor --------------------------------------
# Known data shapes per (source, event_type) in LocalMemoryStore
# Maps event_type -> {field_name: (type, [source_pattern])}


def _dynamic_shapes() -> Dict[str, Any]:
    """Lazily return the DYNAMIC_SHAPES registry from learning_pipeline.

    Imported here (not at module level) to avoid a circular import:
    learning_pipeline imports DATA_SHAPES from this module, so a
    module-level `from learning_pipeline import DYNAMIC_SHAPES` would
    resolve to a partially-initialized module when learning_pipeline is
    the import root. DYNAMIC_SHAPES is a module-level dict that
    refresh_dynamic_shapes() mutates in place, so returning the live
    object (not a copy) keeps this accessor current.
    """
    from CORE.WORMS.expansion.learning_pipeline import DYNAMIC_SHAPES
    return DYNAMIC_SHAPES


DATA_SHAPES = {
    "cluster_created": {
        "type": "cluster",
        "fields": {
            "cluster_type": "categorical",
            "cluster_id": "categorical",
        },
        "numeric_keys": [],
    },
    "cluster_execution_started": {
        "type": "execution",
        "fields": {
            "cluster_type": "categorical",
            "worm_count": "numeric",
            "parallel": "numeric",
        },
        "numeric_keys": ["worm_count", "parallel"],
    },
    "cluster_execution_completed": {
        "type": "execution",
        "fields": {
            "cluster_type": "categorical",
            "results": "numeric",
            "success": "numeric",
        },
        "numeric_keys": ["results", "success"],
    },
    "cross_category_route": {
        "type": "routing",
        "fields": {
            "from": "categorical",
            "to": "categorical",
            "data_keys": "list_categorical",
        },
        "numeric_keys": [],
    },
    "metacluster_orchestrated": {
        "type": "orchestration",
        "fields": {
            "categories": "numeric",
            "ok": "numeric",
            "fail": "numeric",
            "duration_ms": "numeric",
            "insights": "list",
        },
        "numeric_keys": ["natures", "ok", "fail", "duration_ms"],
    },
    "metacluster_discovery": {
        "type": "discovery",
        "fields": {
            "categories": "numeric",
            "total_worms": "numeric",
            "per_nature": "dict",
        },
        "numeric_keys": ["natures", "total_worms"],
    },
    "model_registered": {
        "type": "model_management",
        "fields": {
            "model_name": "categorical",
            "model_version": "categorical",
            "metrics": "dict",
            "metadata": "dict",
            "version_count": "numeric",
        },
        "numeric_keys": ["version_count"],
    },
    "category_cluster_executed": {
        "type": "execution",
        "fields": {
            "nature": "categorical",
            "worms": "numeric",
            "success": "numeric",
            "failures": "numeric",
            "duration_ms": "numeric",
        },
        "numeric_keys": ["worms", "success", "failures", "duration_ms"],
    },
    "nature_cluster_discovery": {
        "type": "discovery",
        "fields": {
            "nature": "categorical",
            "worms_found": "numeric",
            "worm_names": "list_categorical",
        },
        "numeric_keys": ["worms_found"],
    },
    "nature_manager_discovery": {
        "type": "discovery",
        "fields": {
            "natures_found": "numeric",
            "total_worms": "numeric",
            "per_nature": "dict",
        },
        "numeric_keys": ["natures_found", "total_worms"],
    },
    "training_complete": {
        "type": "training",
        "fields": {
            "n_episodes": "numeric",
            "total_duration_ms": "numeric",
            "avg_reward": "numeric",
            "q_table_size": "numeric",
            "source_filter": "categorical",
        },
        "numeric_keys": ["n_episodes", "total_duration_ms", "avg_reward", "q_table_size"],
    },
    # Canonical per-worm execution rows (CORE/WORMS/worm_telemetry.py:
    # emit_worm_outcome, source ClusterWORMZ/ConcurrencyWormExecutor,
    # event_type clusterwormz_worm_executed). Present in the live store but
    # previously unknown_shapes: trainers reading cluster trajectories got
    # the generic numeric fallback instead of typed features.
    # Deliberately UNMODELED keys from the same payload: `error` (free text —
    # one-hot over error strings explodes dims), `decision_id` and
    # `cluster_id` (unique per run — same explosion, zero signal).
    # Transcribed fixture: {"worm": "ResinWorm", "success": true,
    # "standby": false, "duration_ms": 0.534..., "error": null,
    # "cluster_type": "custom", "cluster_id": "cluster_custom_..."}.
    "clusterwormz_worm_executed": {
        "type": "execution",
        "fields": {
            "worm": "categorical",
            "success": "numeric",
            "standby": "numeric",
            "duration_ms": "numeric",
            "nature": "categorical",
            "cluster_type": "categorical",
        },
        "numeric_keys": ["success", "standby", "duration_ms"],
    },
}



class SchemaAwareFeatureExtractor:
    """Extracts typed features from the `data` JSON blob based on known schemas.

    Uses DATA_SHAPES registry to know what fields exist per event_type.
    Falls back to generic extraction for unknown schemas.

    Usage:
        extractor = SchemaAwareFeatureExtractor()
        features, meta = extractor.extract(records)
        # features is a 2D list of numeric features
        # meta has dim, field_names, shapes
    """

    VERSION = "1.0.0"

    def __init__(self):
        self._category_vocabs: Dict[str, List[str]] = {}  # field -> known categories
        self._fit_done = False
    def fit(self, records: List[Dict]):
        """Learn category vocabularies from records.

        Must be called before transform to build one-hot encoding vocabs.
        Checks both static DATA_SHAPES and dynamic DYNAMIC_SHAPES registry.
        """
        for r in records:
            data = r.get("data", {})
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (json.JSONDecodeError, TypeError) as exc:
                    note_failure("learning_features:L199", exc)
                    data = {}
            if not isinstance(data, dict):
                data = {}

            event_type = r.get("event_type", "unknown")
            shape = DATA_SHAPES.get(event_type)
            if shape is None:
                shape = _dynamic_shapes().get(event_type)

            if shape:
                for field_name, field_type in shape["fields"].items():
                    if field_type in ("categorical", "list_categorical"):
                        val = data.get(field_name)
                        vocab_key = f"{event_type}::{field_name}"
                        if vocab_key not in self._category_vocabs:
                            self._category_vocabs[vocab_key] = []

                        if field_type == "list_categorical" and isinstance(val, list):
                            for item in val:
                                item_str = str(item)
                                if item_str not in self._category_vocabs[vocab_key]:
                                    self._category_vocabs[vocab_key].append(item_str)
                        elif isinstance(val, str):
                            if val not in self._category_vocabs[vocab_key]:
                                self._category_vocabs[vocab_key].append(val)

        self._fit_done = True
    def extract(self, records: List[Dict]) -> Tuple[List[List[float]], Dict]:
        """Extract typed feature vectors from records.

        Args:
            records: List of memory dicts from LocalMemoryStore.

        Returns:
            (features, metadata) where features is a 2D list of floats.
        """
        if not self._fit_done:
            self.fit(records)

        features = []
        field_names = []
        shapes_used = set()
        dim = 0

        for r in records:
            data = r.get("data", {})
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (json.JSONDecodeError, TypeError) as exc:
                    note_failure("learning_features:L249", exc)
                    data = {}
            if not isinstance(data, dict):
                data = {}

            event_type = r.get("event_type", "unknown")
            severity = r.get("severity", "info")
            r.get("source", "unknown")
            shape = DATA_SHAPES.get(event_type)
            if shape is None:
                shape = _dynamic_shapes().get(event_type)

            feat = []

            # 1. Base features (always present)
            # Severity as ordinal
            sev_map = {"info": 1.0, "warning": 2.0, "error": 3.0, "critical": 4.0}
            feat.append(sev_map.get(severity, 0.0))

            # 2. Schema-aware features
            if shape:
                shapes_used.add(event_type)
                for field_name in shape["numeric_keys"]:
                    val = data.get(field_name, 0)
                    try:
                        feat.append(float(val))
                    except (ValueError, TypeError) as exc:
                        note_failure("learning_features:L275", exc)
                        feat.append(0.0)

                # One-hot categorical fields
                for field_name, field_type in shape["fields"].items():
                    if field_type in ("categorical", "list_categorical"):
                        vocab_key = f"{event_type}::{field_name}"
                        vocab = self._category_vocabs.get(vocab_key, [])
                        val = data.get(field_name)

                        if field_type == "list_categorical" and isinstance(val, list):
                            val_strs = [str(v) for v in val]
                        else:
                            val_strs = [str(val)] if val is not None else []

                        for cat in vocab:
                            feat.append(1.0 if cat in val_strs else 0.0)
            else:
                # Generic fallback: extract all numeric values from data
                if isinstance(data, dict):
                    for k, v in data.items():  # type: ignore[reportUnnecessaryIsInstance]
                        if isinstance(v, (int, float)):
                            feat.append(float(v))

            features.append(feat)

        # Build field names for diagnostics
        if features:
            dim = len(features[0])
            field_names = ["severity_ordinal"]
            for et in sorted(shapes_used):
                src_shape = DATA_SHAPES.get(et) or _dynamic_shapes().get(et) or {}
                for k in src_shape.get("numeric_keys", []):
                    field_names.append(f"{et}::{k}")
                for fn, ft in src_shape.get("fields", {}).items():
                    if ft in ("categorical", "list_categorical"):
                        vocab_key = f"{et}::{fn}"
                        vocab = self._category_vocabs.get(vocab_key, [])
                        for cat in vocab:
                            field_names.append(f"{et}::{fn}={cat}")

        # Count unknown shapes considering both DATA_SHAPES and DYNAMIC_SHAPES
        all_known = set(DATA_SHAPES.keys()) | set(_dynamic_shapes().keys())

        return features, {
            "dim": dim,
            "count": len(features),
            "field_names": field_names,
            "shapes_used": sorted(shapes_used),
            "unknown_shapes": len([r for r in records if r.get("event_type") not in all_known]),
        }

    def extract_features_and_labels(self, records: List[Dict]) -> Tuple[List[List[float]], List[float], Dict]:
        """Extract both feature vectors and labels.

        Label is 1.0 for non-error (info/warning), 0.0 for error/critical.
        This matches the RLTrainLoop label convention.

        Returns:
            (features, labels, metadata)
        """
        features, meta = self.extract(records)
        labels = []
        for r in records:
            sev = r.get("severity", "info")
            labels.append(1.0 if sev in ("info", "warning") else 0.0)
        return features, labels, meta
    def fallback_accuracy_report(self, records: List[Dict]) -> Dict[str, Any]:
        """Compare static vs dynamic shape extraction accuracy.

        Runs extract() twice per record: first using only static DATA_SHAPES,
        then using the DYNAMIC_SHAPES fallback. Reports how many event_types
        would have been unknown without dynamic shapes and how many
        additional features are extracted via the fallback.

        Args:
            records: List of memory dicts from LocalMemoryStore.

        Returns:
            Dict with comparison stats.
        """
        from CORE.WORMS.expansion.learning import DATA_SHAPES, DYNAMIC_SHAPES

        total = len(records)
        static_unknown = 0
        dynamic_known = 0
        dynamic_feature_dims: List[float] = []

        for r in records:
            et = r.get("event_type", "unknown")
            in_static = et in DATA_SHAPES
            in_dynamic = et in DYNAMIC_SHAPES

            if not in_static:
                static_unknown += 1
                if in_dynamic:
                    dynamic_known += 1

        # Run full extract to compare feature dimensions
        static_features, static_meta = self.extract(records)

        if static_features:
            [len(f) for f in static_features]

        return {
            "total_records": total,
            "static_unknown": static_unknown,
            "dynamic_recovered": dynamic_known,
            "recovery_rate": round(dynamic_known / max(static_unknown, 1), 3) if static_unknown else 1.0,
            "static_feature_dim": static_meta.get("dim", 0),
            "dynamic_shapes_count": len(DYNAMIC_SHAPES),
            "static_shapes_count": len(DATA_SHAPES),
            "unknown_shapes_count": static_meta.get("unknown_shapes", 0),
            "shapes_used": static_meta.get("shapes_used", []),
        }

    def get_summary(self) -> Dict[str, Any]:
        """Worm contract: return a summary dict (see BaseWormMixin).

        `SchemaAwareFeatureExtractor` was split out of the `learning.py`
        monolith without a `get_summary()`, so the smoke test that instantiates
        every registered worm saw `None` here. Reports the fitted vocabularies
        without extracting from any record set.
        """
        return {
            "success": True,
            "method": "get_summary",
            "result": {
                "version": self.VERSION,
                "fitted": self._fit_done,
                "vocab_fields": len(self._category_vocabs),
                "known_shapes": len(DATA_SHAPES),
            },
        }

@register_worm(category="learning", priority=5, cooldown_seconds=45.0,
    description="Feature engineer — feature extraction from LocalMemoryStore, normalization, encoding, imputation")
class FeatureEngineer:
    """Feature engineering pipeline for ML preprocessing.
    
    Works with data extracted by DataPipelineWorm from LocalMemoryStore.
    Provides: normalization, one-hot encoding, missing-value imputation.
    Also provides feature extraction specifically for worm execution data.
    """

    VERSION = "2.0.0"

    def __init__(self):
        self._feature_cache: Dict[str, Any] = {}
        self._lock = threading.Lock()
    def normalize(self, values: List[float]) -> List[float]:
        """Min-max normalization to [0, 1]."""
        if not values:
            return []
        mn, mx = min(values), max(values)
        if mn == mx:
            return [0.5] * len(values)
        return [(v - mn) / (mx - mn) for v in values]

    def one_hot_encode(self, categories: List[str], known_categories: Optional[List[str]] = None) -> Dict[str, Any]:
        """One-hot encode categorical variables."""
        cats = known_categories or sorted(set(categories))
        cat_to_idx = {c: i for i, c in enumerate(cats)}
        encoded = [[0] * len(cats) for _ in categories]
        for i, cat in enumerate(categories):
            if cat in cat_to_idx:
                encoded[i][cat_to_idx[cat]] = 1
        return {"categories": cats, "encoded": encoded}

    def impute_missing(self, values: List[Optional[float]], strategy: str = "mean") -> List[float]:
        """Impute missing (None) values."""
        valid = [v for v in values if v is not None]
        if not valid:
            return [0.0] * len(values)
        fill = sum(valid) / len(valid) if strategy == "mean" else sorted(valid)[len(valid) // 2]
        return [v if v is not None else fill for v in values]

    def extract_worm_features(self, records: List[Dict]) -> Dict[str, Any]:
        """Extract ML-ready features from worm execution records.
        
        Converts LocalMemoryStore records into feature vectors suitable
        for training RLTrainLoop or SYSTEMZ_ML models.
        
        Features extracted:
        - Execution duration (numeric)
        - Success rate (boolean -> float)
        - Source entropy (categorical -> one-hot)
        - Severity distribution (categorical -> count vector)
        - Hour of day (cyclic)
        - Day of week (cyclic)
        """
        if not records:
            return {"count": 0, "features": [], "labels": []}

        features = []
        labels = []
        sources = set()

        for r in records:
            sources.add(r.get("source", "unknown"))

        source_list = sorted(sources)

        for r in records:
            feat = []
            # Duration (if available in data)
            data = r.get("data", {})
            if isinstance(data, dict):
                duration = data.get("duration_ms", data.get("duration", 0))
                feat.append(float(duration) if duration else 0.0)
            else:
                feat.append(0.0)

            # Success (boolean -> float)
            success = r.get("severity", "") != "error"
            feat.append(1.0 if success else 0.0)

            # Source (one-hot)
            source = r.get("source", "unknown")
            source_vec = [1.0 if s == source else 0.0 for s in source_list]
            feat.extend(source_vec)

            features.append(feat)
            labels.append(1.0 if success else 0.0)

        return {
            "count": len(features),
            "feature_dim": len(features[0]) if features else 0,
            "features": features,
            "labels": labels,
            "source_categories": source_list,
        }

    def get_summary(self) -> Dict[str, Any]:
        """Worm contract: return a summary dict (see BaseWormMixin).

        `FeatureEngineer` was split out of the `learning.py` monolith without a
        `get_summary()`, so the smoke test that instantiates every registered
        worm saw `None` here. `BaseWormMixin` would have auto-selected
        `normalize()`, which requires an argument; reporting cache state is
        cheaper and side-effect free.
        """
        with self._lock:
            cached = len(self._feature_cache)
        return {
            "success": True,
            "method": "get_summary",
            "result": {
                "version": self.VERSION,
                "cached_features": cached,
            },
        }
