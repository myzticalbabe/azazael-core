"""
Eve Unleashed Configuration
=============================
Inference engine settings: device, batch size, precision, caching.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Optional  # removed — all names have native equivalents


@dataclass
class EveConfig:
    """Configuration for the Eve Unleashed inference engine."""

    # Device settings
    device: str = "auto"  # auto, cpu, cuda, xpu, mps
    cuda_device: int = 0

    # Performance settings
    batch_size: int = 32
    max_batch_size: int = 256
    num_workers: int = 0  # 0 = single-threaded
    precision: str = "fp32"  # fp32, fp16, int8

    # Caching
    enable_cache: bool = True
    cache_size_mb: int = 512
    cache_ttl_seconds: int = 300

    # Logging
    log_level: str = "INFO"
    log_inference_times: bool = True

    # Model defaults
    default_framework: str = "torch"
    model_directory: str = "models/"

    # Warmup
    warmup_iterations: int = 3

    @classmethod
    def from_file(cls, path: str) -> "EveConfig":
        """Load configuration from a JSON file."""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {path}")

        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)

        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_file(self, path: str):
        """Save configuration to a JSON file."""
        config_path = Path(path)
        config_path.parent.mkdir(parents=True, exist_ok=True)

        data = {k: getattr(self, k) for k in self.__dataclass_fields__}
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def gpu_optimized(cls) -> "EveConfig":
        """Create a GPU-optimized configuration."""
        return cls(
            device="cuda",
            batch_size=64,
            max_batch_size=512,
            precision="fp16",
            num_workers=4,
        )

    @classmethod
    def cpu_optimized(cls) -> "EveConfig":
        """Create a CPU-optimized configuration."""
        return cls(
            device="cpu",
            batch_size=16,
            max_batch_size=128,
            precision="int8",
            num_workers=0,
        )
