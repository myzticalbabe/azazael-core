#!/usr/bin/env python3
# coding: utf-8
"""
feast_feature_store.py — Feast Feature Store integration for Azazael CORE.

Provides feature management for worm parameters, ML models, and agent configs.
Based on Feast 0.39+ with Redis online store and Parquet offline store.

WHY THIS EXISTS
---------------
The worm registry currently stores parameters as plain JSON. A feature store provides:
- Point-in-time correct feature retrieval (no data leakage)
- Consistent feature definitions across training/serving
- Low-latency online serving for real-time worm decisions
- Feature versioning and lineage

USAGE
-----
    from CORE.feast_feature_store import FeastFeatureStore
    
    store = FeastFeatureStore()
    store.apply()  # Register feature definitions
    
    # Materialize features for a worm
    store.materialize(worm_id="spine_registry", params={"learning_rate": 0.001})
    
    # Online retrieval for real-time decisions
    features = store.get_online_features(entity_rows=[{"worm_id": "spine_registry"}])
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta

# Feast imports (optional - gracefully handle missing).
#
# The names are bound for the type checker and rebound for the runtime only when
# the package is installed. `FeatureStore = Entity = ... = None` made every
# annotation that mentions them invalid and every construction a call on None;
# the entry point already raises when Feast is absent, so None was a lie about
# what the code does rather than a state it handles.
if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

    from feast import Entity, FeatureStore, FeatureView, Field, FileSource, RedisSource
    from feast.types import Bool, Float32, Int64, String
    from feast.value_type import ValueType

try:
    from feast import Entity, FeatureStore, FeatureView, Field, FileSource, RedisSource
    from feast.types import Bool, Float32, Int64, String
    from feast.value_type import ValueType
    FEAST_AVAILABLE = True
except ImportError:
    FEAST_AVAILABLE = False


@dataclass
class WormFeatureSpec:
    """Specification for a worm's feature set."""
    worm_id: str
    entity_name: str
    features: Dict[str, str]  # feature_name -> feast_type
    ttl_days: int = 30
    description: str = ""


class FeastFeatureStore:
    """
    Feast Feature Store wrapper for Azazael CORE.
    
    Provides:
    - Worm parameter feature views
    - ML model feature management
    - Agent config feature registry
    - Point-in-time retrieval for training
    - Low-latency online serving
    """
    
    def __init__(
        self,
        repo_path: Optional[Path] = None,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        offline_store_path: Optional[Path] = None,
    ):
        if not FEAST_AVAILABLE:
            raise RuntimeError(
                "Feast not installed. Install with: pip install feast[redis]"
            )
        
        self.repo_path = repo_path or Path("C:/Azazael/azazael/src/CORE/feast_repo")
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.offline_store_path = offline_store_path or Path("C:/Azazael/azazael/feast_offline")
        
        self._fs: Optional[FeatureStore] = None
        self._initialized = False
    
    def _get_feature_store(self) -> FeatureStore:
        """Lazy-initialize FeatureStore."""
        if self._fs is None:
            self.repo_path.mkdir(parents=True, exist_ok=True)
            self.offline_store_path.mkdir(parents=True, exist_ok=True)
            
            # Write feature_store.yaml if not exists
            config_path = self.repo_path / "feature_store.yaml"
            if not config_path.exists():
                self._write_feature_store_yaml(config_path)
            
            self._fs = FeatureStore(repo_path=str(self.repo_path))
        
        return self._fs
    
    def _write_feature_store_yaml(self, path: Path):
        """Write Feast configuration."""
        config = f"""project: azazael_core
provider: local
registry:
  registry_type: file
  path: {self.repo_path / "registry.db"}
online_store:
  type: redis
  connection_string: redis://{self.redis_host}:{self.redis_port}
offline_store:
  type: file
  path: {self.offline_store_path}
entity_key_serialization_version: 2
"""
        path.write_text(config)
    
    def initialize(self) -> bool:
        """Initialize the feature store (create registry, apply definitions)."""
        try:
            fs = self._get_feature_store()
            fs.apply([], skip_apply_to_config=False)  # Initialize registry
            self._initialized = True
            return True
        except Exception as e:
            print(f"Feast initialization failed: {e}", file=sys.stderr)
            return False
    
    def apply_worm_features(self, specs: List[WormFeatureSpec]) -> bool:
        """Register worm feature definitions."""
        if not self._initialized:
            self.initialize()
        
        fs = self._get_feature_store()
        entities = []
        feature_views = []
        
        for spec in specs:
            # Entity
            entity = Entity(
                name=spec.entity_name,
                join_keys=["worm_id"],
                description=f"Entity for worm: {spec.worm_id}",
            )
            entities.append(entity)
            
            # Feature view fields
            fields = []
            for feat_name, feast_type in spec.features.items():
                field_type = self._feast_type(feast_type)
                fields.append(Field(name=feat_name, dtype=field_type))
            
            # Offline source (Parquet)
            offline_path = self.offline_store_path / f"{spec.entity_name}.parquet"
            offline_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Create empty parquet if not exists
            if not offline_path.exists():
                import pandas as pd
                df = pd.DataFrame(columns=["worm_id", "event_timestamp"] + list(spec.features.keys()))
                df.to_parquet(offline_path, index=False)
            
            file_source = FileSource(
                path=str(offline_path),
                timestamp_field="event_timestamp",
                created_timestamp_column="created_timestamp",
            )
            
            # Online source (Redis)
            redis_source = RedisSource(
                table_name=spec.entity_name,
                redis_connection_string=f"redis://{self.redis_host}:{self.redis_port}",
            )
            
            fv = FeatureView(
                name=spec.entity_name,
                entities=[spec.entity_name],
                ttl=timedelta(days=spec.ttl_days),
                schema=fields,
                online=True,
                source=file_source,
                tags={"worm_id": spec.worm_id, "description": spec.description},
            )
            feature_views.append(fv)
        
        try:
            fs.apply(entities + feature_views)
            return True
        except Exception as e:
            print(f"Failed to apply worm features: {e}", file=sys.stderr)
            return False
    
    def _feast_type(self, type_str: str):
        """Map string to Feast type."""
        type_map = {
            "float": Float32,
            "float32": Float32,
            "int": Int64,
            "int64": Int64,
            "string": String,
            "str": String,
            "bool": Bool,
            "boolean": Bool,
        }
        return type_map.get(type_str.lower(), String)
    
    def materialize_worm(self, worm_id: str, features: Dict[str, Any]) -> bool:
        """Materialize features for a worm (write to online store)."""
        if not self._initialized:
            self.initialize()
        
        fs = self._get_feature_store()
        
        # Find entity name for worm
        entity_name = f"worm_{worm_id.replace('-', '_')}"
        
        # Write to offline store first (append to parquet)
        offline_path = self.offline_store_path / f"{entity_name}.parquet"
        import pandas as pd
        
        row = {"worm_id": worm_id, "event_timestamp": datetime.utcnow()}
        row.update(features)
        df = pd.DataFrame([row])
        
        if offline_path.exists():
            existing = pd.read_parquet(offline_path)
            df = pd.concat([existing, df], ignore_index=True)
        
        df.to_parquet(offline_path, index=False)
        
        # Materialize to online store
        try:
            fs.materialize_incremental(end_date=datetime.utcnow() + timedelta(days=1))
            return True
        except Exception as e:
            print(f"Materialization failed: {e}", file=sys.stderr)
            return False
    
    def get_online_features(
        self,
        entity_rows: List[Dict[str, str]],
        features: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Retrieve features from online store (low latency)."""
        if not self._initialized:
            self.initialize()
        
        fs = self._get_feature_store()
        
        try:
            feature_vector = fs.get_online_features(
                entity_rows=entity_rows,
                features=features or [],
            )
            return feature_vector.to_dict()
        except Exception as e:
            print(f"Online feature retrieval failed: {e}", file=sys.stderr)
            return {}
    
    def get_historical_features(
        self,
        entity_df: "pd.DataFrame",
        features: List[str],
    ) -> "pd.DataFrame":
        """Get point-in-time correct features for training."""
        if not self._initialized:
            self.initialize()
        
        fs = self._get_feature_store()
        
        try:
            job = fs.get_historical_features(
                entity_df=entity_df,
                features=features,
            )
            return job.to_df()
        except Exception as e:
            print(f"Historical feature retrieval failed: {e}", file=sys.stderr)
            import pandas as pd
            return pd.DataFrame()
    
    def list_registered_features(self) -> Dict[str, List[str]]:
        """List all registered feature views and their features."""
        if not self._initialized:
            self.initialize()
        
        fs = self._get_feature_store()
        try:
            fvs = fs.list_feature_views()
            # The old comprehension iterated `fvs` but read `f.name` from a name
            # that was never bound (a NameError the moment Feast was installed),
            # behind a tautological `fv.name == fv.name` filter. A feature view's
            # features are its schema fields.
            return {fv.name: [field.name for field in fv.schema] for fv in fvs}
        except Exception as e:
            print(f"Failed to list features: {e}", file=sys.stderr)
            return {}


# Pre-defined worm feature specifications
DEFAULT_WORM_SPECS = [
    WormFeatureSpec(
        worm_id="spine_registry",
        entity_name="worm_spine_registry",
        features={
            "learning_rate": "float",
            "batch_size": "int",
            "max_depth": "int",
            "dropout": "float",
            "activation": "string",
            "optimizer": "string",
        },
        description="Spine registry worm parameters",
    ),
    WormFeatureSpec(
        worm_id="worm_registry",
        entity_name="worm_worm_registry",
        features={
            "max_worms": "int",
            "spawn_threshold": "float",
            "cooldown_seconds": "int",
            "priority_weight": "float",
        },
        description="Worm registry configuration",
    ),
    WormFeatureSpec(
        worm_id="expansion_spine",
        entity_name="worm_expansion_spine",
        features={
            "expansion_factor": "float",
            "max_branches": "int",
            "pruning_threshold": "float",
        },
        description="Expansion spine worm parameters",
    ),
    WormFeatureSpec(
        worm_id="exmachina",
        entity_name="worm_exmachina",
        features={
            "mutation_rate": "float",
            "population_size": "int",
            "generations": "int",
            "selection_pressure": "float",
        },
        description="Evolutionary machine worm parameters",
    ),
    WormFeatureSpec(
        worm_id="memory_bus",
        entity_name="worm_memory_bus",
        features={
            "max_queue_size": "int",
            "batch_size": "int",
            "flush_interval_ms": "int",
            "compression": "bool",
        },
        description="Memory bus worm parameters",
    ),
]


def get_feast_store() -> FeastFeatureStore:
    """Get singleton FeastFeatureStore instance."""
    global _FEAST_STORE
    if '_FEAST_STORE' not in globals():
        _FEAST_STORE = FeastFeatureStore()
    return _FEAST_STORE


def initialize_feast() -> bool:
    """Initialize Feast feature store with default worm specs."""
    store = get_feast_store()
    if not store.initialize():
        return False
    return store.apply_worm_features(DEFAULT_WORM_SPECS)


# Convenience functions for worm operations
def materialize_worm(worm_id: str, features: Dict[str, Any]) -> bool:
    """Quick materialize for a worm."""
    store = get_feast_store()
    return store.materialize_worm(worm_id, features)


def get_worm_features(worm_id: str, feature_names: List[str]) -> Dict[str, Any]:
    """Get online features for a worm."""
    store = get_feast_store()
    entity_name = f"worm_{worm_id.replace('-', '_')}"
    return store.get_online_features(
        entity_rows=[{"worm_id": worm_id}],
        features=[f"{entity_name}:{fn}" for fn in feature_names],
    )


if __name__ == "__main__":
    # Quick test
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        if initialize_feast():
            print("Feast feature store initialized successfully")
        else:
            print("Failed to initialize Feast")
            sys.exit(1)
    else:
        print("Usage: python feast_feature_store.py init")