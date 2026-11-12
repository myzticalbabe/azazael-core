# coding: utf-8
"""
NEURAL MESH — Category: GLIA (4 worms)
Support cells: cleanup, packaging, pruning, G: GC. All envs.

AstrocyteWorm, OligodendrocyteWorm, MicrogliaWorm, EpendymalWorm
"""

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List
import os as _os

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
    note_failure("glia:L18", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@register_worm(category="glia", priority=6, cooldown_seconds=60.0,
    description="Metabolic cleanup: __pycache__, tmp, stale locks per env")
class AstrocyteWorm:
    """Per-env temp/cache sweeper (safe allowlist only)."""

    VERSION = "1.0.0"
    SAFE_SUFFIXES = (".pyc", ".tmp", ".log.1")

    def __init__(self):
        self._cleaned = 0
        self._lock = threading.Lock()

    def sweep(self, root: str, dry_run: bool = True) -> dict[str, Any]:
        p = Path(root)
        victims = []
        if p.exists():
            for f in p.rglob("*"):
                try:
                    if f.is_file() and (f.suffix in (".pyc", ".tmp") or f.name.endswith(".log.1")
                                        or f.name == "__pycache__"):
                        victims.append(str(f))
                except Exception as exc:
                    note_failure("glia:L44", exc)
                    continue
                if len(victims) >= 200:
                    break
        removed = 0
        if not dry_run:
            for v in victims[:200]:
                try:
                    Path(v).unlink(missing_ok=True)
                    removed += 1
                except Exception as exc:
                    note_failure("glia:L54", exc)
                    continue
        with self._lock:
            self._cleaned += removed
            total = self._cleaned
        return {"success": True, "root": root, "candidates": len(victims),
                "removed": removed, "dry_run": dry_run, "total_cleaned": total}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": {"total_cleaned": self._cleaned}}


@register_worm(category="glia", priority=5, cooldown_seconds=120.0,
    description="ONNX/GGUF packaging helper: picks quant per env RAM")
class OligodendrocyteWorm:
    """Insulation = compression choice per env capability."""

    VERSION = "1.0.0"

    def recommend(self, mem_gb: float = 16.0, has_gpu: bool = False) -> dict[str, Any]:
        # GPTQ-4bit and AWQ-4bit are GPU-only formats, so every branch that
        # selects one must require `has_gpu`. Gating AWQ on memory alone handed
        # a CPU-only host with 16-23 GB a format it cannot run.
        if has_gpu and mem_gb >= 24:
            fmt = "GPTQ-4bit"
        elif has_gpu and mem_gb >= 16:
            fmt = "AWQ-4bit"
        else:
            fmt = "GGUF-Q4_K_M"
        return {"success": True, "format": fmt, "mem_gb": mem_gb, "gpu": has_gpu}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": self.recommend()}


@register_worm(category="glia", priority=7, cooldown_seconds=60.0,
    description="Prunes dead worm procs + drains DeadLetterQueue backlog")
class MicrogliaWorm:
    """Phagocytosis: dead-proc + DLQ backlog accounting."""

    VERSION = "1.0.0"

    def __init__(self):
        self._pruned = 0
        self._lock = threading.Lock()

    _SIZE_DENSITY = None  # lazy-loaded task3 model

    @classmethod
    def _load_density(cls) -> dict:
        if cls._SIZE_DENSITY is not None:
            return cls._SIZE_DENSITY
        try:
            import json as _json
            cls._SIZE_DENSITY = _json.load(
                open("G:/source-of-truth/azazael_models/size_density.json", encoding="utf-8"))
        except Exception as exc:
            note_failure("glia:L109", exc)
            cls._SIZE_DENSITY = {}
        return cls._SIZE_DENSITY

    def check_path(self, path: str) -> dict[str, Any]:
        """3-sigma file-size anomaly check against task3 density model."""
        try:
            size = _os.stat(path).st_size
        except OSError as exc:
            note_failure("glia:L118", exc)
            return {"success": False, "path": path, "error": str(exc)[:800]}
        model = self._load_density()
        # match by topdir name fragment, else global fallback
        entry = None
        for k, v in model.items():
            if k in path.replace("\\", "/"):
                entry = v
                break
        if entry is None and model:
            ns = [v for v in model.values() if v.get("n", 0) >= 100] or list(model.values())
            mu = sum(v["mean"] for v in ns) / len(ns)
            sd = max(1.0, sum(v["std"] for v in ns) / len(ns))
            entry = {"mean": mu, "std": sd, "n": sum(v["n"] for v in ns)}
        if entry is None:
            return {"success": True, "path": path, "anomaly": False, "src": "no-model"}
        z = abs(size - entry["mean"]) / max(1.0, entry["std"])
        return {"success": True, "path": path, "size": size,
                "z": round(z, 2), "anomaly": z > 3.0, "src": "size_density.json"}

    def prune_report(self, dead_pids: list[int] | None = None, dlq_depth: int = 0) -> dict[str, Any]:
        dead_pids = dead_pids or []
        with self._lock:
            self._pruned += len(dead_pids)
            total = self._pruned
        action = "drain_dlq" if dlq_depth > 100 else ("prune" if dead_pids else "idle")
        return {"success": True, "action": action, "dead": len(dead_pids),
                "dlq_depth": dlq_depth, "total_pruned": total,
                "at": datetime.now().isoformat()}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": {"total_pruned": self._pruned}}


@register_worm(category="glia", priority=5, cooldown_seconds=180.0,
    description="G: archive GC: stale loom outputs, dedup by hash")
class EpendymalWorm:
    """Cerebrospinal flow = G: loom GC (reports only, deletes nothing by default)."""

    VERSION = "1.0.0"

    def __init__(self):
        self._scans = 0
        self._lock = threading.Lock()

    def scan(self) -> dict[str, Any]:
        import platform as _pl
        candidates = []
        for g in (r"G:\source-of-truth", "/mnt/g/source-of-truth"):
            if os.path.exists(g):
                candidates.append(g)
                break
        with self._lock:
            self._scans += 1
            n = self._scans
        return {"success": True, "g_roots": candidates, "scans": n,
                "mode": "report-only", "platform": _pl.system()}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.scan()}
