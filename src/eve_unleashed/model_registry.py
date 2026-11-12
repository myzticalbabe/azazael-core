"""
Eve Unleashed Model Registry
=============================
Cross-project model tracking, deduplication, and persistent statistics.

Stores a JSON registry at ~/.azazael/model_registry.json tracking:
- All model files discovered across projects
- Canonical paths (deduplication)
- Per-model stats: inference count, avg latency, last used, framework
- Cross-project model discovery

Usage:
    from eve_unleashed.model_registry import ModelRegistry
    reg = ModelRegistry()
    reg.register("/models/resnet.pt", "torch")
    reg.record_inference("/models/resnet.pt", latency_ms=12.5)
    reg.scan()  # discover models across all project dirs

CLI:
    eve registry  — list all registered models
    eve registry scan  — scan and update registry
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


REGISTRY_PATH = Path.home() / ".azazael" / "model_registry.json"

# Common model file extensions to scan for
MODEL_EXTENSIONS = {".gguf", ".pt", ".pth", ".onnx", ".joblib", ".pkl", ".safetensors", ".bin"}

# Minimum file size to consider (bytes)
MIN_MODEL_SIZE = 1000

# Extension-to-framework mapping
_EXT_FRAMEWORK = {
    ".gguf": "gguf",
    ".pt": "torch",
    ".pth": "torch",
    ".onnx": "onnx",
    ".joblib": "sklearn",
    ".pkl": "sklearn",
    ".safetensors": "transformers",
    ".bin": "transformers",
}


def _guess_framework_ext(path: str) -> str:
    """Guess framework from file extension (module-level, reusable)."""
    return _EXT_FRAMEWORK.get(Path(path).suffix.lower(), "unknown")


@dataclass
class ModelRecord:
    """A single model entry in the registry."""
    path: str = ""
    canonical_path: str = ""  # resolved, deduplicated path
    filename: str = ""
    framework: str = "unknown"
    size_mb: float = 0.0
    first_seen: float = 0.0  # epoch timestamp
    last_used: float = 0.0
    inference_count: int = 0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    projects: list[str] = field(default_factory=list)  # which projects use this model

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "canonical_path": self.canonical_path,
            "filename": self.filename,
            "framework": self.framework,
            "size_mb": self.size_mb,
            "first_seen": self.first_seen,
            "last_used": self.last_used,
            "inference_count": self.inference_count,
            "total_latency_ms": self.total_latency_ms,
            "avg_latency_ms": self.avg_latency_ms,
            "projects": self.projects,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ModelRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ModelRegistry:
    """Cross-project model registry with deduplication and persistent stats."""

    def __init__(self, registry_path: (Path | None) = None):
        self._path = registry_path or REGISTRY_PATH
        self._models: dict[str, ModelRecord] = {}  # keyed by canonical_path
        self._load()

    # ── Persistence ─────────────────────────────────────────────

    def _load(self):
        """Load registry from JSON file."""
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for entry in data.get("models", []):
                    record = ModelRecord.from_dict(entry)
                    self._models[record.canonical_path] = record
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                note_failure("model_registry:L110", exc)

    def save(self):
        """Persist registry to JSON file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated": time.time(),
            "model_count": len(self._models),
            "models": [m.to_dict() for m in self._models.values()],
        }
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── Registration ────────────────────────────────────────────

    def register(self, path: str, framework: str = "unknown", project: str = "") -> (str | None):
        """Register a model file. Returns canonical_path if new, None if already known."""
        resolved = str(Path(path).resolve())
        if resolved in self._models:
            record = self._models[resolved]
            if project and project not in record.projects:
                record.projects.append(project)
                self.save()
            return None

        p = Path(resolved)
        size_mb = 0.0
        if p.exists():
            size_mb = p.stat().st_size / (1024 ** 2)

        now = time.time()
        record = ModelRecord(
            path=str(path),
            canonical_path=resolved,
            filename=p.name,
            framework=framework,
            size_mb=round(size_mb, 2),
            first_seen=now,
            last_used=now,
            projects=[project] if project else [],
        )
        self._models[resolved] = record
        self.save()
        return resolved

    def record_inference(self, path: str, latency_ms: float, framework: str = ""):
        """Record an inference event for a model."""
        resolved = str(Path(path).resolve())
        if resolved not in self._models:
            self.register(path, framework or _guess_framework_ext(path))

        record = self._models[resolved]
        record.inference_count += 1
        record.total_latency_ms += latency_ms
        record.avg_latency_ms = round(record.total_latency_ms / record.inference_count, 2)
        record.last_used = time.time()
        # Auto-save every 10 inferences to balance persistence vs I/O
        if record.inference_count % 10 == 0:
            self.save()

    # ── Query ───────────────────────────────────────────────────

    def get(self, path: str) -> (ModelRecord | None):
        """Get a model record by path."""
        resolved = str(Path(path).resolve())
        return self._models.get(resolved)

    def list_all(self) -> list[ModelRecord]:
        """list all registered models, sorted by last_used descending."""
        return sorted(self._models.values(), key=lambda r: r.last_used, reverse=True)

    def list_by_framework(self, framework: str) -> list[ModelRecord]:
        """list models for a specific framework."""
        return [m for m in self._models.values() if m.framework == framework]

    def list_by_project(self, project: str) -> list[ModelRecord]:
        """list models used by a specific project."""
        return [m for m in self._models.values() if project in m.projects]

    def total_models(self) -> int:
        return len(self._models)

    def total_inferences(self) -> int:
        return sum(m.inference_count for m in self._models.values())

    def total_size_gb(self) -> float:
        return sum(m.size_mb for m in self._models.values()) / 1024.0

    def stats(self) -> dict[str, Any]:
        """Return aggregate registry statistics."""
        models = list(self._models.values())
        if not models:
            return {"model_count": 0, "total_inferences": 0, "total_size_gb": 0.0,
                    "frameworks": {}, "most_used": None}

        frameworks: dict[str, int] = {}
        for m in models:
            frameworks[m.framework] = frameworks.get(m.framework, 0) + 1

        most_used = max(models, key=lambda m: m.inference_count)

        return {
            "model_count": len(models),
            "total_inferences": sum(m.inference_count for m in models),
            "total_size_gb": round(sum(m.size_mb for m in models) / 1024.0, 2),
            "frameworks": frameworks,
            "most_used": {
                "filename": most_used.filename,
                "framework": most_used.framework,
                "inference_count": most_used.inference_count,
                "avg_latency_ms": most_used.avg_latency_ms,
            } if most_used.inference_count > 0 else None,
        }

    def select_fastest(self, framework: (str | None) = None) -> (str | None):
        """Evolution: route to the lowest-latency model with observed data.

        Falls back to the smallest registered model (cheapest to try) when
        nothing has measurements yet. Returns canonical path or None.
        """
        cands = [m for m in self._models.values()
                 if m.inference_count > 0
                 and (framework is None or m.framework == framework)]
        if cands:
            return min(cands, key=lambda m: m.avg_latency_ms).canonical_path
        cold = [m for m in self._models.values()
                if framework is None or m.framework == framework]
        if cold:
            return min(cold, key=lambda m: m.size_mb).canonical_path
        return None

    # ── Scanning ────────────────────────────────────────────────

    def scan(self, directories: (list[str] | None) = None) -> int:
        """Scan directories for model files and register new ones.

        Args:
            directories: list of directories to scan. Defaults to common locations.

        Returns:
            Number of newly discovered models.
        """
        if directories is None:
            directories = [
                str(Path.home() / "models"),
                "/root/models",
                "/root/llama.cpp/models",
                str(Path("/mnt/c/Users/clint/.lmstudio/models")),
                str(Path.home() / ".lmstudio" / "models"),
            ]

        newly_found = 0
        for directory in directories:
            d = Path(directory)
            if not d.exists():
                continue
            for ext in MODEL_EXTENSIONS:
                for p in d.rglob(f"*{ext}"):
                    if not p.is_file() or p.stat().st_size < MIN_MODEL_SIZE:
                        continue
                    fw = _guess_framework_ext(str(p))
                    result = self.register(str(p), framework=fw)
                    if result is not None:
                        newly_found += 1

        return newly_found

    def prune_missing(self) -> list[str]:
        """Drop entries whose file no longer exists. Self-heal for stale
        rows (e.g. legacy entries from moved model dirs). Returns the pruned
        canonical paths and saves only when something was removed."""
        gone = [c for c, m in self._models.items()
                if not Path(m.canonical_path or c).is_file()]
        for c in gone:
            del self._models[c]
        if gone:
            self.save()
        return gone

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _guess_framework(path: str) -> str:
        """Guess framework from file extension (delegates to module-level helper)."""
        return _guess_framework_ext(path)


# ── Singleton ────────────────────────────────────────────────────

_registry: (ModelRegistry | None) = None


def get_registry() -> ModelRegistry:
    """Get the global model registry singleton."""
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry
