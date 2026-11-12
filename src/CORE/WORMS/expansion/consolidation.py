# coding: utf-8
"""
NEURAL MESH — Category: CONSOLIDATION (4 worms)
Sleep/wake memory consolidation: WSL2-Ubuntu + Bare train, G: persists.

SharpWaveReplayWorm, SleepConsolidatorWorm, EngramIndexerWorm, DreamCriticWorm
"""

import hashlib
import threading
from datetime import datetime
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
    note_failure("consolidation:L16", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


@register_worm(category="consolidation", priority=6, cooldown_seconds=60.0,
    description="Prioritized replay buffer sampler for sleep cycles")
class SharpWaveReplayWorm:
    """Samples high-TD-error memories first (pairs with PrioritizedReplayBuffer)."""

    VERSION = "1.0.0"

    def __init__(self):
        self._queue: list[dict] = []
        self._replayed = 0
        self._lock = threading.Lock()

    def push(self, mem_id: str, td_error: float) -> dict[str, Any]:
        with self._lock:
            self._queue.append({"mem": mem_id, "td": td_error})
            self._queue.sort(key=lambda x: -x["td"])
            self._queue = self._queue[:1000]
            return {"success": True, "queued": len(self._queue)}

    def replay_batch(self, n: int = 8) -> dict[str, Any]:
        with self._lock:
            batch = self._queue[:n]
            self._queue = self._queue[n:]
            self._replayed += len(batch)
            return {"success": True, "batch": [b["mem"] for b in batch],
                    "replayed": self._replayed, "remaining": len(self._queue)}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"queued": len(self._queue), "replayed": self._replayed}}


@register_worm(category="consolidation", priority=5, cooldown_seconds=180.0,
    description="Nightly sleep job: replay -> EWC anchor -> G: snapshot")
class SleepConsolidatorWorm:
    """Orchestrates one sleep cycle (report + counters; training via TrainingOrchestrator)."""

    VERSION = "1.0.0"

    def __init__(self):
        self._cycles = 0
        self._lock = threading.Lock()

    def sleep_cycle(self, replayed: int = 0, anchored_tasks: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            self._cycles += 1
            n = self._cycles
        return {"success": True, "cycle": n, "replayed": replayed,
                "anchored": anchored_tasks or [], "snapshot": "g_archive",
                "at": datetime.now().isoformat()}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": {"cycles": self._cycles}}


@register_worm(category="consolidation", priority=6, cooldown_seconds=60.0,
    description="Content-hash index over consolidated engrams")
class EngramIndexerWorm:
    """Hash -> env + task index (extends engram:VectorShardWorm)."""

    VERSION = "1.0.0"

    def __init__(self):
        self._index: dict[str, dict] = {}
        self._lock = threading.Lock()

    def index(self, text: str, env: str = "wsl2-ubuntu", task: str = "general") -> dict[str, Any]:
        key = _h(text)
        with self._lock:
            self._index[key] = {"env": env, "task": task,
                                "at": datetime.now().isoformat(), "len": len(text)}
            return {"success": True, "key": key, "indexed": len(self._index)}

    def lookup(self, key: str) -> dict[str, Any]:
        with self._lock:
            hit = self._index.get(key)
            return {"success": True, "key": key, "hit": hit is not None,
                    "entry": hit}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"indexed": len(self._index)}}


@register_worm(category="consolidation", priority=5, cooldown_seconds=120.0,
    description="Scores DreamingModule outputs; keeps only coherent dreams")
class DreamCriticWorm:
    """Coherence gate over synthetic dreams (pairs with DreamingModule/HypnagogicSynthesizer)."""

    VERSION = "1.0.0"

    def __init__(self, min_len: int = 40):
        self._min_len = min_len
        self._kept = 0
        self._rejected = 0
        self._lock = threading.Lock()

    def critique(self, dream: str) -> dict[str, Any]:
        ok = len(dream.strip()) >= self._min_len and " " in dream.strip()
        with self._lock:
            if ok:
                self._kept += 1
            else:
                self._rejected += 1
            return {"success": True, "kept": ok, "len": len(dream),
                    "kept_n": self._kept, "rejected_n": self._rejected}

    def execute(self) -> dict[str, Any]:
        with self._lock:
            return {"success": True, "method": "execute",
                    "result": {"kept": self._kept, "rejected": self._rejected}}
