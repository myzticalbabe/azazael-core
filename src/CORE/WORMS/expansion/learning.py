# coding: utf-8
"""LEARNING worms -- backward-compatible shim (split 2026-09-07).

RLTrainLoop, ModelVersioner, DataPipelineWorm, ABTestRunner, FeatureEngineer,
SchemaAwareFeatureExtractor and the shape registry now live in focused
per-worm modules (learning_rl.py, learning_versioner.py, learning_pipeline.py,
learning_features.py, learning_ab.py). This module re-exports the public API
so existing import paths keep working.
"""

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
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("learning:L24", exc)
    _REGISTRY = False
def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("learning:L34", exc)
    _FREETHREAD = False
FreeThreadPool = None
ThreadSafeDict = dict
ThreadSafeList = list
def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:  # noqa: BLE001 — 314t detection is best-effort
            note_failure("learning:L43", exc)
            return False

from CORE.WORMS.expansion.learning_rl import RLTrainLoop
from CORE.WORMS.expansion.learning_versioner import ModelVersioner
from CORE.WORMS.expansion.learning_pipeline import DataPipelineWorm, DYNAMIC_SHAPES, refresh_dynamic_shapes
from CORE.WORMS.expansion.learning_features import DATA_SHAPES, SchemaAwareFeatureExtractor, FeatureEngineer

import sysconfig

# ABTestRunner: prefer the v2.0.0 DPO-capable version from learning_grpo.py;
# fall back to the v1.0.0 basic stub in learning_ab.py only if that fails.
try:
    from CORE.WORMS.expansion.learning_grpo import ABTestRunner  # noqa: E402, F401
except ImportError as exc:
    note_failure("learning:L55", exc)
    from CORE.WORMS.expansion.learning_ab import ABTestRunner  # noqa: E402, F401

# ─────────────────────────────────────────────────────────────────────────
# Backward-compatibility re-exports
#
# GRPOTrainer was extracted into a focused module (learning_grpo.py) to
# avoid loading the 100K+ learning.py monolith. This re-export keeps the
# original import path working for existing callers and tests.
# (PrioritizedReplayBuffer retired 2026-10-11 with learning_replay.py:
# zero production callers.)
# ─────────────────────────────────────────────────────────────────────────
try:
    from CORE.WORMS.expansion.learning_grpo import GRPOTrainer  # noqa: E402, F401
except ImportError as exc:
    note_failure("learning:L72", exc)
